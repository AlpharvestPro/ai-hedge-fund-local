#!/usr/bin/env python3
"""
Generate X (Twitter) stories by cross-referencing influencer signals / X trends
with AI hedge fund candidates and pipeline signals.

Reads:
  - Influencer signals markdown (from Pi2)
  - X trends markdown (from Pi2)
  - candidates.json (local)
  - pipeline_YYYY-MM-DD.json (local)
  - SQLite: us_rs.db (daily_analysis, vcp_patterns, entry_signals, stocks)

Produces:
  - output/x_stories_YYYY-MM-DD.json

Usage:
  poetry run python scripts/generate_x_stories.py \
      --signals-dir /tmp/x_data \
      --pipeline-json output/pipeline_2026-03-15.json \
      --output output/x_stories_2026-03-15.json \
      [--dry-run]
"""

import argparse
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ============================================================
# CONSTANTS
# ============================================================

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DB_PATH = Path.home() / "data" / "us_rs.db"
CANDIDATES_PATH = PROJECT_ROOT / "candidates.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:9b"
MAX_STORIES = 4

# Ticker extraction: $TICKER or (TICKER) patterns
RE_DOLLAR_TICKER = re.compile(r"\$([A-Z]{1,5})\b")
RE_PAREN_TICKER = re.compile(r"\(([A-Z]{1,5})\)")

# Sector keyword mapping for theme matching
SECTOR_KEYWORDS = {
    "energy": ["energy", "oil", "crude", "petroleum", "原油", "エネルギー", "石油"],
    "technology": ["tech", "ai ", "semiconductor", "chip", "テック", "半導体", "AI"],
    "healthcare": ["health", "pharma", "biotech", "hospital", "医療", "ヘルスケア"],
    "materials": ["mining", "silver", "gold", "copper", "aluminum", "素材", "鉱業"],
    "defense": ["defense", "defence", "military", "aerospace", "防衛", "軍事"],
    "financials": ["bank", "financial", "insurance", "金融", "銀行"],
}


# ============================================================
# DATA LOADING
# ============================================================


def load_candidates() -> list[str]:
    """Load candidate tickers from candidates.json."""
    if not CANDIDATES_PATH.exists():
        print(f"  WARNING: {CANDIDATES_PATH} not found")
        return []
    data = json.loads(CANDIDATES_PATH.read_text())
    return data.get("us", [])


def load_pipeline(pipeline_path: str | None) -> dict:
    """Load pipeline JSON (analyst_signals + decisions)."""
    if not pipeline_path:
        return {}
    p = Path(pipeline_path)
    if not p.exists():
        print(f"  WARNING: Pipeline JSON not found: {p}")
        return {}
    return json.loads(p.read_text())


def load_company_names(db_path: Path) -> dict[str, str]:
    """Load ticker -> company_name mapping from stocks table."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT ticker, company_name FROM stocks WHERE is_active = 1")
    mapping = {row[0]: row[1] for row in cur.fetchall()}
    conn.close()
    return mapping


def build_reverse_lookup(company_names: dict[str, str]) -> dict[str, str]:
    """Build company name -> ticker reverse lookup for text matching."""
    lookup = {}
    for ticker, name in company_names.items():
        if not name:
            continue
        # Full name
        lookup[name.lower()] = ticker
        # Strip common suffixes progressively
        cleaned = name
        for suffix in [" - Common Stock", " Common Stock", " - Class A",
                       " - Class B", " - Class C",
                       ", Inc.", ", Inc", " Inc.", " Inc",
                       " Corporation", " Corp.", " Corp",
                       " Ltd.", " Ltd", " Co., Ltd.", " Co.",
                       " Holdings", " Group", " plc", " PLC",
                       " SE", " NV", " SA", " AG", " N.V."]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
        if len(cleaned) > 3 and cleaned.lower() != name.lower():
            lookup[cleaned.lower()] = ticker
            # Also try with comma removed: "Pan American Silver Corp" -> "Pan American Silver"
            if "," in cleaned:
                lookup[cleaned.split(",")[0].strip().lower()] = ticker
    return lookup


def get_db_data(db_path: Path, tickers: list[str], days: int = 7) -> dict:
    """Fetch daily_analysis, vcp_patterns, entry_signals for given tickers."""
    if not db_path.exists() or not tickers:
        return {"daily_analysis": {}, "vcp_patterns": {}, "entry_signals": {}, "stocks": {}}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    placeholders = ",".join("?" * len(tickers))

    # daily_analysis — latest per ticker
    daily = {}
    cur.execute(
        f"SELECT * FROM daily_analysis WHERE ticker IN ({placeholders}) "
        f"ORDER BY date DESC",
        tickers,
    )
    for row in cur.fetchall():
        t = row["ticker"]
        if t not in daily:
            daily[t] = dict(row)

    # vcp_patterns — latest per ticker
    vcp = {}
    cur.execute(
        f"SELECT * FROM vcp_patterns WHERE ticker IN ({placeholders}) "
        f"AND date >= ? ORDER BY date DESC",
        tickers + [cutoff],
    )
    for row in cur.fetchall():
        t = row["ticker"]
        if t not in vcp:
            vcp[t] = dict(row)

    # entry_signals — all in window
    signals = {}
    cur.execute(
        f"SELECT * FROM entry_signals WHERE ticker IN ({placeholders}) "
        f"AND date >= ? ORDER BY date DESC",
        tickers + [cutoff],
    )
    for row in cur.fetchall():
        t = row["ticker"]
        signals.setdefault(t, []).append(dict(row))

    # stocks metadata
    stocks = {}
    cur.execute(
        f"SELECT ticker, company_name, sector, industry, market_cap "
        f"FROM stocks WHERE ticker IN ({placeholders})",
        tickers,
    )
    for row in cur.fetchall():
        stocks[row["ticker"]] = dict(row)

    conn.close()
    return {
        "daily_analysis": daily,
        "vcp_patterns": vcp,
        "entry_signals": signals,
        "stocks": stocks,
    }


# ============================================================
# MARKDOWN PARSING
# ============================================================


def parse_influencer_signals(signals_dir: Path) -> list[dict]:
    """Parse influencer signal markdown files."""
    signals = []
    for md_file in sorted(signals_dir.glob("*.md")):
        if "xtrends" in md_file.name or "x-trends" in md_file.name:
            continue
        text = md_file.read_text(encoding="utf-8")
        if "Influencer" not in text and "influencer" not in text:
            continue

        file_date = md_file.stem  # e.g. "2026-03-14"

        # Split on ## N. blocks
        blocks = re.split(r"(?=^## \d+\. )", text, flags=re.MULTILINE)
        for block in blocks:
            header = re.match(
                r"## (\d+)\.\s+(.+?)(?:\s+--\s+(.+))?\s*$",
                block.split("\n")[0],
            )
            if not header:
                continue

            num = int(header.group(1))
            source = header.group(2).strip()
            headline = (header.group(3) or "").strip()

            # Extract fields
            tags_match = re.search(r"\*\*Tags\*\*:\s*(.+)", block)
            tags = [t.strip() for t in tags_match.group(1).split(",")] if tags_match else []

            relevance_match = re.search(r"\*\*Relevance\*\*:\s*(\w+)", block)
            relevance = relevance_match.group(1) if relevance_match else "MEDIUM"

            # Extract JA summary
            ja_match = re.search(r"\*\*JA\*\*:\s*(.+?)(?:\n\n|\n\*\*ZH)", block, re.DOTALL)
            ja_text = ja_match.group(1).strip() if ja_match else ""

            # Extract EN summary
            en_match = re.search(r"\*\*EN\*\*:\s*(.+?)(?:\n\n|\n\*\*JA)", block, re.DOTALL)
            en_text = en_match.group(1).strip() if en_match else ""

            signals.append({
                "num": num,
                "source": source,
                "headline": headline,
                "tags": tags,
                "relevance": relevance,
                "ja": ja_text,
                "en": en_text,
                "date": file_date,
                "type": "influencer",
            })

    return signals


def parse_x_trends(signals_dir: Path) -> list[dict]:
    """Parse x-trends markdown files."""
    trends = []
    for md_file in sorted(signals_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        if "Momentum Trading Trends" not in text and "xtrends" not in md_file.name and "x-trends" not in md_file.name:
            continue

        file_date = md_file.stem

        blocks = re.split(r"(?=^## \d+\. )", text, flags=re.MULTILINE)
        for block in blocks:
            header = re.match(r"## (\d+)\.\s+(.+)", block.split("\n")[0])
            if not header:
                continue

            num = int(header.group(1))
            title = header.group(2).strip()

            category_match = re.search(r"\*\*Category\*\*:\s*(.+?)(?:\s*\|)", block)
            category = category_match.group(1).strip() if category_match else ""

            hashtags_match = re.search(r"\*\*Hashtags\*\*:\s*(.+)", block)
            hashtags = hashtags_match.group(1).strip().split() if hashtags_match else []

            engagement_match = re.search(r"\*\*Engagement\*\*:\s*(\w+)", block)
            engagement = engagement_match.group(1) if engagement_match else "MEDIUM"

            ja_match = re.search(r"\*\*JA\*\*:\s*(.+?)(?:\n\n|\n\*\*ZH)", block, re.DOTALL)
            ja_text = ja_match.group(1).strip() if ja_match else ""

            en_match = re.search(r"\*\*EN\*\*:\s*(.+?)(?:\n\n|\n\*\*JA)", block, re.DOTALL)
            en_text = en_match.group(1).strip() if en_match else ""

            # Title is trilingual "EN | JA | ZH" — extract just English part
            title_en = title.split("|")[0].strip() if "|" in title else title

            trends.append({
                "num": num,
                "title": title_en,
                "category": category,
                "hashtags": hashtags,
                "engagement": engagement,
                "ja": ja_text,
                "en": en_text,
                "date": file_date,
                "type": "trend",
            })

    return trends


def extract_tickers_from_text(
    text: str,
    reverse_lookup: dict[str, str],
    known_tickers: set[str] | None = None,
) -> list[str]:
    """Extract stock tickers from text using multiple strategies."""
    found = set()

    # Strategy 1: $TICKER pattern
    for m in RE_DOLLAR_TICKER.finditer(text):
        found.add(m.group(1))

    # Strategy 2: (TICKER) pattern — e.g. "HCA Healthcare (HCA)"
    for m in RE_PAREN_TICKER.finditer(text):
        t = m.group(1)
        if len(t) >= 2:
            found.add(t)

    # Strategy 3: company name reverse lookup
    text_lower = text.lower()
    for name, ticker in reverse_lookup.items():
        if name in text_lower:
            found.add(ticker)

    # Strategy 4: known ticker word boundary match (e.g. "HCA stock", "HCA Healthcare")
    # Only for tickers with 3+ chars to avoid false positives
    if known_tickers:
        for ticker in known_tickers:
            if len(ticker) >= 3 and re.search(rf"\b{re.escape(ticker)}\b", text):
                found.add(ticker)

    return list(found)


# ============================================================
# CROSS-REFERENCING
# ============================================================


def cross_reference(
    signals: list[dict],
    trends: list[dict],
    candidates: list[str],
    pipeline: dict,
    db_data: dict,
    reverse_lookup: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """Cross-reference signals/trends with candidates. Returns (ticker_stories, theme_stories)."""

    candidates_set = set(candidates)

    # Build pipeline decisions lookup
    decisions = {}
    for d in pipeline.get("decisions", []):
        decisions[d["ticker"]] = d

    # Build set of all known tickers (candidates + DB) for word-boundary matching
    all_known = candidates_set | set(db_data.get("daily_analysis", {}).keys())

    # Extract tickers from all signals/trends
    signal_tickers = {}  # ticker -> list of source dicts
    for item in signals + trends:
        full_text = f"{item.get('en', '')} {item.get('ja', '')} {item.get('headline', '')} {item.get('title', '')}"
        tickers = extract_tickers_from_text(full_text, reverse_lookup, all_known)
        for t in tickers:
            signal_tickers.setdefault(t, []).append(item)

    # Tier 1: Golden — in signals AND candidates AND pipeline BUY
    tier1 = []
    # Tier 2: Strong — in signals AND has minervini_pass=1 in DB
    tier2 = []

    daily = db_data.get("daily_analysis", {})
    vcp = db_data.get("vcp_patterns", {})
    entry = db_data.get("entry_signals", {})
    stocks_meta = db_data.get("stocks", {})

    for ticker, sources in signal_tickers.items():
        in_candidates = ticker in candidates_set
        pipeline_decision = decisions.get(ticker)
        da = daily.get(ticker, {})
        has_minervini = da.get("minervini_pass") == 1

        if in_candidates and pipeline_decision and pipeline_decision.get("action") == "buy":
            tier = 1
        elif in_candidates:
            tier = 1  # In candidates even without BUY is still golden (RS 80-89 + Minervini)
        elif has_minervini:
            tier = 2
        else:
            continue

        stock = stocks_meta.get(ticker, {})
        vcp_data = vcp.get(ticker)
        entry_data = entry.get(ticker, [])

        # Clean company name (strip " Common Stock", " - Class A", etc.)
        company_name = stock.get("company_name", "")
        for sfx in [" - Common Stock", " Common Stock", " - Class A",
                     " - Class B", " - Class C"]:
            company_name = company_name.replace(sfx, "")

        # Priority scoring
        score = 50
        if tier == 1:
            score += 30
        if pipeline_decision and pipeline_decision.get("action") == "buy":
            score += 10
            score += min(pipeline_decision.get("confidence", 0) // 10, 5)
        if vcp_data:
            score += 10
        if entry_data:
            score += 5
        if any(s.get("relevance") == "HIGH" or s.get("engagement") == "HIGH" for s in sources):
            score += 5

        # Pick best source
        best_source = max(sources, key=lambda s: (
            1 if s.get("relevance") == "HIGH" or s.get("engagement") == "HIGH" else 0
        ))

        story = {
            "tier": tier,
            "ticker": ticker,
            "company": company_name,
            "priority_score": min(score, 100),
            "source_type": best_source["type"],
            "source_name": best_source.get("source", best_source.get("title", "")),
            "source_headline": best_source.get("headline", best_source.get("title", "")),
            "source_ja": best_source.get("ja", ""),
            "source_tags": best_source.get("tags", best_source.get("hashtags", [])),
            "ai_decision": pipeline_decision.get("action", "") if pipeline_decision else "",
            "ai_confidence": pipeline_decision.get("confidence", 0) if pipeline_decision else 0,
            "ai_top_reasoning": pipeline_decision.get("reasoning", "") if pipeline_decision else "",
            "ai_bullish_count": 0,
            "ai_total_count": 0,
            "rs_rating": da.get("rs_rating", 0),
            "minervini_score": da.get("minervini_score", 0),
            "industry_group": stock.get("industry", ""),
            "market_cap_b": round(stock.get("market_cap", 0) / 1e9, 1) if stock.get("market_cap") else 0,
            "vcp": {
                "contractions": vcp_data["contraction_count"],
                "depth_pct": vcp_data["depth_pct"],
                "pivot": vcp_data["pivot_price"],
                "quality": vcp_data["pattern_quality"],
            } if vcp_data else None,
            "entry_signals": [
                {"type": s["signal_type"], "strength": s["strength"], "date": s["date"]}
                for s in entry_data[:3]
            ],
            "generated_post": "",  # filled by Qwen
            "hashtags": [],
        }

        # Count bullish analysts from pipeline
        if pipeline.get("analyst_signals"):
            bullish = 0
            total = 0
            for agent_signals in pipeline["analyst_signals"].values():
                if ticker in agent_signals:
                    total += 1
                    if agent_signals[ticker].get("signal") == "bullish":
                        bullish += 1
            story["ai_bullish_count"] = bullish
            story["ai_total_count"] = total

        if tier == 1:
            tier1.append(story)
        else:
            tier2.append(story)

    # Sort by priority
    tier1.sort(key=lambda s: s["priority_score"], reverse=True)
    tier2.sort(key=lambda s: s["priority_score"], reverse=True)

    # Combine ticker stories (max 3)
    ticker_stories = (tier1 + tier2)[:3]

    # Tier 3: Theme matching
    theme_stories = _find_theme_matches(signals, trends, candidates, daily, stocks_meta)

    return ticker_stories, theme_stories[:1]  # max 1 theme story


def _find_theme_matches(
    signals: list[dict],
    trends: list[dict],
    candidates: list[str],
    daily: dict,
    stocks_meta: dict,
) -> list[dict]:
    """Find sector/theme matches between signals/trends and candidate sectors."""
    themes = []

    # Collect all text from signals+trends
    all_text = " ".join(
        f"{s.get('en', '')} {s.get('ja', '')} {s.get('headline', '')} {s.get('title', '')}"
        for s in signals + trends
    ).lower()

    for sector_key, keywords in SECTOR_KEYWORDS.items():
        keyword_hits = sum(1 for kw in keywords if kw in all_text)
        if keyword_hits < 2:
            continue

        # Find candidates in this sector
        matching = []
        for ticker in candidates:
            stock = stocks_meta.get(ticker, {})
            da = daily.get(ticker, {})
            stock_sector = (stock.get("sector", "") + " " + stock.get("industry", "")).lower()
            if any(kw in stock_sector for kw in keywords):
                matching.append({
                    "ticker": ticker,
                    "rs": da.get("rs_rating", 0),
                    "minervini": da.get("minervini_score", 0),
                })

        if len(matching) < 2:
            continue

        avg_rs = round(sum(m["rs"] for m in matching) / len(matching), 1) if matching else 0
        theme_name = sector_key.replace("_", " ").title() + " Momentum"

        themes.append({
            "tier": 3,
            "theme": theme_name,
            "matching_candidates": [m["ticker"] for m in matching],
            "avg_rs": avg_rs,
            "candidate_details": matching,
            "keyword_hits": keyword_hits,
            "generated_post": "",
            "hashtags": [],
        })

    themes.sort(key=lambda t: (t["keyword_hits"], len(t["matching_candidates"])), reverse=True)
    return themes


# ============================================================
# QWEN STORY GENERATION
# ============================================================

STORY_PROMPT_TEMPLATE = """\
あなたはAlpharvestProの金融アナリストです。
以下のクロスシグナルデータから、X（Twitter）投稿用の日本語ストーリーを1つ作成してください。

【インフルエンサーシグナル】
ソース: {source_name}
内容: {source_ja}

【AI分析結果】
判定: {decision} | 確信度: {confidence}%
ブル: {bullish}/{total}アナリスト
理由: {reasoning}

【テクニカルデータ】
ティッカー: ${ticker} ({company})
RS: {rs} | Minervini: {minervini}/10
{vcp_line}
{entry_signal_line}

ルール:
- 240文字以内（ハッシュタグ含む）
- 具体的な数値を含める
- 最後に質問を付ける
- ハッシュタグ2-3個
- "IBD"という文字列は絶対に使わない。代わりに"Investor's Business Daily"を使用
- 絵文字1-2個のみ
- /nothink
"""

THEME_PROMPT_TEMPLATE = """\
あなたはAlpharvestProの金融アナリストです。
以下のセクターテーマデータから、X（Twitter）投稿用の日本語ストーリーを1つ作成してください。

【テーマ】
{theme}

【該当銘柄】
{candidate_list}

【平均RS Rating】
{avg_rs}

ルール:
- 240文字以内（ハッシュタグ含む）
- セクター全体のモメンタムに焦点
- 具体的な銘柄名と数値を含める
- 最後に質問を付ける
- ハッシュタグ2-3個
- 絵文字1-2個のみ
- /nothink
"""

FALLBACK_TEMPLATE = (
    "📊 {source_name}注目の${ticker}（{company}）— "
    "RS {rs}、Minervini {minervini}/10"
    "{vcp_text}"
    "。AI分析は{decision}判定（確信度{confidence}%）。"
    "あなたはどう見ますか？"
    " #{ticker} #クロスシグナル"
)


def generate_story_qwen(story: dict) -> str:
    """Generate Japanese X post text via Qwen."""
    vcp = story.get("vcp")
    vcp_line = ""
    if vcp:
        vcp_line = f"VCP: {vcp['contractions']}回収縮、深さ{vcp['depth_pct']:.1f}%、ピボット${vcp['pivot']:.2f}、品質{vcp['quality']}"

    entry_sigs = story.get("entry_signals", [])
    entry_line = ""
    if entry_sigs:
        sig_strs = [f"{s['type']}(強度{s['strength']})" for s in entry_sigs[:2]]
        entry_line = f"エントリーシグナル: {', '.join(sig_strs)}"

    prompt = STORY_PROMPT_TEMPLATE.format(
        source_name=story.get("source_name", ""),
        source_ja=story.get("source_ja", "")[:200],
        decision=story.get("ai_decision", "N/A").upper(),
        confidence=story.get("ai_confidence", 0),
        bullish=story.get("ai_bullish_count", 0),
        total=story.get("ai_total_count", 0),
        reasoning=story.get("ai_top_reasoning", "")[:200],
        ticker=story.get("ticker", ""),
        company=story.get("company", ""),
        rs=story.get("rs_rating", 0),
        minervini=story.get("minervini_score", 0),
        vcp_line=vcp_line,
        entry_signal_line=entry_line,
    )

    text = _call_ollama(prompt)
    if not text:
        # Fallback template
        vcp_text = f"、VCP{vcp['contractions']}回収縮" if vcp else ""
        text = FALLBACK_TEMPLATE.format(
            source_name=story.get("source_name", "分析"),
            ticker=story.get("ticker", ""),
            company=story.get("company", ""),
            rs=story.get("rs_rating", 0),
            minervini=story.get("minervini_score", 0),
            vcp_text=vcp_text,
            decision=story.get("ai_decision", "HOLD"),
            confidence=story.get("ai_confidence", 0),
        )
    return text


def generate_theme_story_qwen(theme: dict) -> str:
    """Generate Japanese X post for a theme match."""
    tickers = theme.get("matching_candidates", [])
    candidate_list = ", ".join(f"${t}" for t in tickers[:5])
    if len(tickers) > 5:
        candidate_list += f" 他{len(tickers) - 5}銘柄"

    prompt = THEME_PROMPT_TEMPLATE.format(
        theme=theme.get("theme", ""),
        candidate_list=candidate_list,
        avg_rs=theme.get("avg_rs", 0),
    )

    text = _call_ollama(prompt)
    if not text:
        text = (
            f"📊 {theme.get('theme', 'セクター')}に注目！"
            f"候補銘柄{candidate_list}の平均RS {theme.get('avg_rs', 0)}。"
            f"セクター全体のモメンタムは続くか？"
            f" #モメンタム #セクターローテーション"
        )
    return text


def _call_ollama(prompt: str, timeout: int = 120) -> str:
    """Call Ollama API and return generated text. Returns empty string on failure."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            # Clean up: remove thinking tags if present
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            # Truncate to 280 chars (X limit)
            if len(text) > 280:
                # Try to cut at last sentence before 280
                cut = text[:280].rfind("。")
                if cut > 100:
                    text = text[: cut + 1]
                else:
                    text = text[:277] + "..."
            return text
    except requests.RequestException as e:
        print(f"  WARNING: Ollama call failed: {e}")
    return ""


# ============================================================
# MAIN
# ============================================================


def main():
    parser = argparse.ArgumentParser(description="Generate X stories from cross-signal data")
    parser.add_argument("--signals-dir", required=True, help="Directory with influencer/trend markdown files")
    parser.add_argument("--pipeline-json", help="Path to pipeline JSON (optional)")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Skip Qwen generation")
    args = parser.parse_args()

    signals_dir = Path(args.signals_dir)
    today = datetime.now().strftime("%Y-%m-%d")
    t0 = time.time()

    print("=" * 60)
    print("  X Story Generator — Cross-Signal Analysis")
    print("=" * 60)
    print(f"  Date: {today}")
    print(f"  Signals dir: {signals_dir}")

    # 1. Load data
    print("\n[1/5] Loading data...")
    candidates = load_candidates()
    print(f"  Candidates: {len(candidates)} tickers")

    pipeline = load_pipeline(args.pipeline_json)
    print(f"  Pipeline: {'loaded' if pipeline else 'not available (DB-only mode)'}")

    company_names = load_company_names(DB_PATH)
    reverse_lookup = build_reverse_lookup(company_names)
    print(f"  Company names: {len(company_names)} loaded")

    # 2. Parse markdown
    print("\n[2/5] Parsing signals & trends...")
    influencer_signals = parse_influencer_signals(signals_dir)
    print(f"  Influencer signals: {len(influencer_signals)}")

    x_trends = parse_x_trends(signals_dir)
    print(f"  X trends: {len(x_trends)}")

    if not influencer_signals and not x_trends:
        print("  No signals or trends found — exiting")
        _write_empty_output(args.output, today, t0)
        return

    # 3. Extract tickers and cross-reference
    print("\n[3/5] Cross-referencing...")
    # Collect all mentioned tickers for DB query
    all_text = " ".join(
        f"{s.get('en', '')} {s.get('ja', '')} {s.get('headline', '')} {s.get('title', '')}"
        for s in influencer_signals + x_trends
    )
    all_mentioned = extract_tickers_from_text(all_text, reverse_lookup, set(candidates))
    # Also include candidates for DB lookup
    db_tickers = list(set(all_mentioned + candidates))
    print(f"  Tickers extracted from signals: {len(all_mentioned)}")

    db_data = get_db_data(DB_PATH, db_tickers)
    print(f"  DB data loaded: {len(db_data['daily_analysis'])} daily, {len(db_data['vcp_patterns'])} VCP, {len(db_data['entry_signals'])} entry signal tickers")

    ticker_stories, theme_stories = cross_reference(
        influencer_signals, x_trends, candidates, pipeline, db_data, reverse_lookup,
    )
    print(f"  Tier 1+2 matches: {len(ticker_stories)}")
    print(f"  Tier 3 themes: {len(theme_stories)}")

    if not ticker_stories and not theme_stories:
        print("  No cross-signal matches — exiting")
        _write_empty_output(args.output, today, t0)
        return

    # 4. Generate stories via Qwen
    print("\n[4/5] Generating stories via Qwen...")
    qwen_t0 = time.time()

    if args.dry_run:
        print("  [DRY RUN] Skipping Qwen generation")
        for s in ticker_stories:
            s["generated_post"] = f"[DRY RUN] Story for ${s['ticker']}"
            s["hashtags"] = [f"#{s['ticker']}", "#クロスシグナル"]
        for t in theme_stories:
            t["generated_post"] = f"[DRY RUN] Theme: {t['theme']}"
            t["hashtags"] = ["#テーマ", "#モメンタム"]
    else:
        for i, story in enumerate(ticker_stories):
            print(f"  Generating story {i + 1}/{len(ticker_stories)}: ${story['ticker']}...")
            story["generated_post"] = generate_story_qwen(story)
            # Extract hashtags from generated text
            story["hashtags"] = re.findall(r"#\w+", story["generated_post"])
            print(f"    -> {len(story['generated_post'])} chars")

        for i, theme in enumerate(theme_stories):
            print(f"  Generating theme {i + 1}/{len(theme_stories)}: {theme['theme']}...")
            theme["generated_post"] = generate_theme_story_qwen(theme)
            theme["hashtags"] = re.findall(r"#\w+", theme["generated_post"])
            print(f"    -> {len(theme['generated_post'])} chars")

    qwen_time = round(time.time() - qwen_t0, 1)

    # 5. Write output
    print("\n[5/5] Writing output...")
    output = {
        "date": today,
        "stories": ticker_stories,
        "theme_matches": theme_stories,
        "metadata": {
            "signals_scanned": len(influencer_signals),
            "trends_scanned": len(x_trends),
            "tickers_extracted": len(all_mentioned),
            "tier1_matches": sum(1 for s in ticker_stories if s["tier"] == 1),
            "tier2_matches": sum(1 for s in ticker_stories if s["tier"] == 2),
            "tier3_matches": len(theme_stories),
            "qwen_generation_time_s": qwen_time,
            "total_time_s": round(time.time() - t0, 1),
        },
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Written to {out_path}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  Stories: {len(ticker_stories)} ticker + {len(theme_stories)} theme")
    print(f"  Qwen time: {qwen_time}s | Total: {round(time.time() - t0, 1)}s")
    for s in ticker_stories:
        print(f"    Tier {s['tier']}: ${s['ticker']} (score {s['priority_score']})")
    for t in theme_stories:
        print(f"    Tier 3: {t['theme']} ({len(t['matching_candidates'])} candidates)")
    print("=" * 60)


def _write_empty_output(output_path: str, date: str, t0: float):
    """Write an empty output JSON when no matches are found."""
    output = {
        "date": date,
        "stories": [],
        "theme_matches": [],
        "metadata": {
            "signals_scanned": 0,
            "trends_scanned": 0,
            "tickers_extracted": 0,
            "tier1_matches": 0,
            "tier2_matches": 0,
            "tier3_matches": 0,
            "qwen_generation_time_s": 0,
            "total_time_s": round(time.time() - t0, 1),
        },
    }
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Empty output written to {out}")


if __name__ == "__main__":
    main()
