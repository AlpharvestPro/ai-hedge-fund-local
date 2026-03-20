#!/usr/bin/env python3
"""Generate selection summary report from AI hedge fund pipeline output.

Reads the analyst_signals from the pipeline state and formats a
Terminal Pro Dark themed summary report with industry group enrichment.

Usage:
    python scripts/generate_summary.py --date 2026-03-12 --market us
    python scripts/generate_summary.py --date 2026-03-12 --market us --input output/pipeline_2026-03-12.json
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATHS = {
    "us": [
        Path.home() / "data" / "us_rs.db",
        Path("/home/lohengrin168/us-stocks/data/rs_rankings.db"),
    ],
}

# Short display names for agents
AGENT_SHORT = {
    "cathie_wood_agent": "Cathie",
    "peter_lynch_agent": "Lynch",
    "technical_analyst_agent": "Tech",
    "fundamentals_analyst_agent": "Funda",
    "valuation_analyst_agent": "Value",
    "sentiment_analyst_agent": "Sent",
    "news_sentiment_agent": "News",
    "growth_analyst_agent": "Growth",
    "ben_graham_agent": "Graham",
    "bill_ackman_agent": "Ackman",
    "charlie_munger_agent": "Munger",
    "michael_burry_agent": "Burry",
    "warren_buffett_agent": "Buffett",
    "stanley_druckenmiller_agent": "Druck",
    "phil_fisher_agent": "Fisher",
    "mohnish_pabrai_agent": "Pabrai",
    "rakesh_jhunjhunwala_agent": "Rakesh",
    "aswath_damodaran_agent": "Damod",
}

# Agents excluded from display (position-sizing only, no signal)
EXCLUDE_FROM_DISPLAY = {"risk_management_agent"}


def _summarize_reasoning(reasoning) -> str:
    """Convert reasoning (str or dict) to readable text."""
    if isinstance(reasoning, str):
        return reasoning
    if isinstance(reasoning, dict):
        parts = []
        for k, v in reasoning.items():
            label = k.replace("_", " ").title()
            if isinstance(v, str):
                parts.append(f"{label}: {v}")
            elif isinstance(v, (int, float)):
                parts.append(f"{label}: {v}")
            elif isinstance(v, dict):
                sub = "; ".join(f"{sk}: {sv}" for sk, sv in v.items())
                parts.append(f"{label}: {sub}")
            elif isinstance(v, list):
                parts.append(f"{label}: {', '.join(str(i) for i in v)}")
        return " | ".join(parts)
    return str(reasoning) if reasoning else ""


def _load_stock_info(tickers: list[str], market: str) -> dict:
    """Load company name, sector, industry group, RS rating from DB."""
    info = {}
    db_path = None
    for p in DB_PATHS.get(market, []):
        if p.exists():
            db_path = p
            break
    if not db_path:
        return info

    conn = sqlite3.connect(str(db_path))
    for t in tickers:
        row = conn.execute("""
            SELECT s.company_name, s.sector, m.ibd_group_name, r.rs_rating, s.market_cap
            FROM stocks s
            LEFT JOIN ibd_stock_mapping m ON s.ticker = m.ticker
            LEFT JOIN rs_ratings r ON s.ticker = r.ticker
                AND r.date = (SELECT MAX(date) FROM rs_ratings)
            WHERE s.ticker = ?
        """, (t,)).fetchone()
        if row:
            name = row[0] or ""
            for suffix in [" Common Stock", " Common Shares", " - Common Stock",
                           " - Common Shares", " Ordinary Shares", " - Class A",
                           " - Class C Capital Stock"]:
                name = name.replace(suffix, "")
            info[t] = {
                "company": name.strip().rstrip(" -,"),
                "sector": row[1] or "",
                "industry_group": row[2] or "",
                "rs_rating": row[3] or 0,
                "market_cap_b": (row[4] or 0) / 1e9,
            }
    conn.close()
    return info


def _signal_score(signal: str, confidence: float) -> float:
    """Convert signal+confidence to a -100 to +100 score.
    bullish 80% -> +80, bearish 90% -> -90, neutral -> 0."""
    if signal in ("bullish", "positive"):
        return confidence
    elif signal in ("bearish", "negative"):
        return -confidence
    return 0


def _overall_score(analyst_signals: dict, ticker: str) -> float:
    """Average score across all analysts for a ticker (-100 to +100)."""
    scores = []
    for agent_id, agent_data in analyst_signals.items():
        if agent_id in EXCLUDE_FROM_DISPLAY:
            continue
        if not isinstance(agent_data, dict) or ticker not in agent_data:
            continue
        ts = agent_data[ticker]
        if isinstance(ts, dict):
            scores.append(_signal_score(ts.get("signal", "neutral"), ts.get("confidence", 0)))
    return sum(scores) / len(scores) if scores else 0


def _signal_counts(analyst_signals: dict, ticker: str) -> tuple[int, int, int]:
    """Count bullish/bearish/neutral signals for a ticker."""
    bull = bear = neut = 0
    for agent_id, agent_data in analyst_signals.items():
        if agent_id in EXCLUDE_FROM_DISPLAY:
            continue
        if not isinstance(agent_data, dict) or ticker not in agent_data:
            continue
        ts = agent_data[ticker]
        if isinstance(ts, dict):
            sig = ts.get("signal", "neutral")
            if sig in ("bullish", "positive"):
                bull += 1
            elif sig in ("bearish", "negative"):
                bear += 1
            else:
                neut += 1
    return bull, bear, neut


def _signal_char(signal, confidence: float) -> str:
    """Short display: +78 for bullish 78%, -90 for bearish 90%, . for no data."""
    if signal is None:
        return "."
    if signal in ("bullish", "positive"):
        return f"+{confidence:.0f}"
    elif signal in ("bearish", "negative"):
        return f"-{confidence:.0f}"
    return f"~{confidence:.0f}"


def generate_text_summary(analyst_signals: dict, date: str,
                          market: str, stock_info: dict) -> str:
    """Generate a text summary report with matrix table + per-ticker details."""
    lines = []
    lines.append("=" * 80)
    lines.append(f"  AI HEDGE FUND — Selection Summary ({date})")
    lines.append(f"  Market: {market.upper()}")
    lines.append("=" * 80)
    lines.append("")

    if not analyst_signals:
        lines.append("No analyst signals available.")
        return "\n".join(lines)

    all_tickers = set()
    for agent_signals in analyst_signals.values():
        if isinstance(agent_signals, dict):
            all_tickers.update(agent_signals.keys())

    # Get ordered agent list (excluding non-signal agents)
    agent_ids = sorted(a for a in analyst_signals.keys() if a not in EXCLUDE_FROM_DISPLAY)
    agent_shorts = [AGENT_SHORT.get(a, a.replace("_agent", "")[:6].title()) for a in agent_ids]

    # Pre-compute scores (used in sorting, matrix rows, and detail cards)
    ticker_scores = {t: _overall_score(analyst_signals, t) for t in all_tickers}
    sorted_tickers = sorted(all_tickers, key=lambda t: ticker_scores[t], reverse=True)

    # ── Summary Matrix Table ──
    lines.append("  SUMMARY MATRIX  (+ = bullish, - = bearish, ~ = neutral, . = no data)")
    lines.append("")

    # Header
    hdr = f"  {'Ticker':<7} {'Industry Group':<24} {'RS':>3} {'Scr':>5}"
    for short in agent_shorts:
        hdr += f" {short:>6}"
    lines.append(hdr)
    lines.append("  " + "─" * (len(hdr) - 2))

    for ticker in sorted_tickers:
        info = stock_info.get(ticker, {})
        score = ticker_scores[ticker]

        row = f"  {ticker:<7} {info.get('industry_group', '')[:23]:<24} {info.get('rs_rating', 0):3.0f} {score:+5.0f}"
        for agent_id in agent_ids:
            agent_data = analyst_signals.get(agent_id, {})
            if isinstance(agent_data, dict) and ticker in agent_data:
                ts = agent_data[ticker]
                sc = _signal_char(ts.get("signal", "neutral"), ts.get("confidence", 0))
            else:
                sc = "."
            row += f" {sc:>6}"
        lines.append(row)

    lines.append("  " + "─" * (len(hdr) - 2))
    lines.append(f"  Total: {len(all_tickers)} tickers")
    lines.append("")
    lines.append("")

    # ── Per-Ticker Detail ──
    lines.append("  DETAIL")
    lines.append("")

    for ticker in sorted_tickers:
        info = stock_info.get(ticker, {})
        score = ticker_scores[ticker]

        lines.append(f"▶  {ticker}  —  {info.get('company', '')}")
        lines.append(f"  {info.get('sector', '')} / {info.get('industry_group', '')}  |  RS: {info.get('rs_rating', 0):.0f}  |  Score: {score:+.0f}")
        lines.append("  " + "─" * 60)

        for agent_id, agent_data in sorted(analyst_signals.items()):
            if agent_id in EXCLUDE_FROM_DISPLAY:
                continue
            if not isinstance(agent_data, dict) or ticker not in agent_data:
                continue
            ticker_signal = agent_data[ticker]
            if isinstance(ticker_signal, dict):
                signal = ticker_signal.get("signal", "neutral").upper()
                if signal == "POSITIVE":
                    signal = "BULLISH"
                elif signal == "NEGATIVE":
                    signal = "BEARISH"
                conf = ticker_signal.get("confidence", 0)
                reasoning = ticker_signal.get("reasoning", "")
                reason_text = _summarize_reasoning(reasoning)
                short_reason = f"— {reason_text[:70]}" if reason_text else ""
                agent_name = agent_id.replace("_agent", "").replace("_", " ").title()
                lines.append(f"  {agent_name:25s} {signal:8s} ({conf:3.0f}%)  {short_reason}")

        lines.append("  " + "─" * 60)
        lines.append("")

    return "\n".join(lines)


def generate_html_summary(analyst_signals: dict, date: str,
                          market: str, stock_info: dict) -> str:
    """Generate a Terminal Pro Dark themed HTML summary with matrix table."""
    all_tickers = set()
    for agent_signals in analyst_signals.values():
        if isinstance(agent_signals, dict):
            all_tickers.update(agent_signals.keys())

    agent_ids = sorted(a for a in analyst_signals.keys() if a not in EXCLUDE_FROM_DISPLAY)
    agent_shorts = [AGENT_SHORT.get(a, a.replace("_agent", "")[:6].title()) for a in agent_ids]

    # Pre-compute scores (used in sorting, matrix rows, and detail cards)
    ticker_scores = {t: _overall_score(analyst_signals, t) for t in all_tickers}
    sorted_tickers = sorted(all_tickers, key=lambda t: ticker_scores[t], reverse=True)

    # ── Summary Matrix Header ──
    agent_th = "".join(f'<th class="agent-col" title="{aid.replace("_agent","").replace("_"," ").title()}">{short}</th>' for aid, short in zip(agent_ids, agent_shorts))

    # ── Summary Matrix Rows ──
    summary_rows = ""
    for ticker in sorted_tickers:
        info = stock_info.get(ticker, {})
        score = ticker_scores[ticker]
        score_class = "pos-score" if score > 0 else "neg-score" if score < 0 else ""

        agent_tds = ""
        for agent_id in agent_ids:
            agent_data = analyst_signals.get(agent_id, {})
            if isinstance(agent_data, dict) and ticker in agent_data:
                ts = agent_data[ticker]
                sig = ts.get("signal")
                sig_conf = ts.get("confidence", 0)
                if sig is None:
                    display = "."
                    sig_class = "nodata"
                elif sig in ("bullish", "positive"):
                    display = f"+{sig_conf:.0f}"
                    sig_class = "bull"
                elif sig in ("bearish", "negative"):
                    display = f"-{sig_conf:.0f}"
                    sig_class = "bear"
                else:
                    display = f"~{sig_conf:.0f}"
                    sig_class = "neut"
            else:
                display = "."
                sig_class = "nodata"
            agent_tds += f'<td class="sig {sig_class}">{display}</td>'

        summary_rows += f"""<tr>
<td class="ticker-link" onclick="document.getElementById('{ticker}').scrollIntoView({{behavior:'smooth'}})"><a href="#{ticker}">{ticker}</a></td>
<td class="company-col">{info.get('company', '')}</td>
<td class="ig-col">{info.get('industry_group', '')}</td>
<td class="num">{info.get('rs_rating', 0):.0f}</td>
<td class="num {score_class}">{score:+.0f}</td>
{agent_tds}
</tr>
"""

    # ── Per-Ticker Cards ──
    ticker_cards = ""
    for ticker in sorted_tickers:
        info = stock_info.get(ticker, {})
        score = ticker_scores[ticker]

        agent_rows = ""
        for agent_id, agent_data in sorted(analyst_signals.items()):
            if agent_id in EXCLUDE_FROM_DISPLAY:
                continue
            if not isinstance(agent_data, dict) or ticker not in agent_data:
                continue
            ts = agent_data[ticker]
            if isinstance(ts, dict):
                signal = ts.get("signal", "neutral")
                sig_conf = ts.get("confidence", 0)
                reasoning = ts.get("reasoning", "")
                reason_text = _summarize_reasoning(reasoning)
                signal_class = "bullish" if signal in ("bullish", "positive") else "bearish" if signal in ("bearish", "negative") else "neutral"
                agent_name = agent_id.replace("_agent", "").replace("_", " ").title()
                agent_rows += f'<tr><td class="agent-name">{agent_name}</td><td class="signal {signal_class}">{signal.upper()}</td><td class="num">{sig_conf:.0f}%</td><td class="reasoning">{reason_text}</td></tr>\n'

        ticker_cards += f"""
        <div class="ticker-card" id="{ticker}">
            <div class="ticker-header">
                <span class="ticker-symbol">{ticker}</span>
                <span class="company-name">{info.get('company', '')}</span>
            </div>
            <div class="ticker-meta">
                <span>{info.get('sector', '')} / {info.get('industry_group', '')}</span>
                <span>RS: {info.get('rs_rating', 0):.0f}  |  Score: {score:+.0f}</span>
            </div>
            <table class="signals-table">
                <thead><tr><th>Agent</th><th>Signal</th><th>Conf</th><th>Reasoning</th></tr></thead>
                <tbody>{agent_rows}</tbody>
            </table>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Hedge Fund — {date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0a0e14; color: #e0e0e0; font-family: 'JetBrains Mono', 'Fira Code', monospace; padding: 1.5rem; max-width: 1600px; margin: 0 auto; }}
h1 {{ color: #e6b450; font-size: 1.5rem; margin-bottom: 0.3rem; }}
h2 {{ color: #39bae6; font-size: 1.1rem; margin: 1.5rem 0 0.8rem 0; }}
.meta {{ color: #666; margin-bottom: 0.5rem; font-size: 0.85rem; }}
.stats {{ color: #888; font-size: 0.85rem; margin-bottom: 1.5rem; }}
.stats .buy-count {{ color: #4ade80; }}
.stats .sell-count {{ color: #f87171; }}
.stats .hold-count {{ color: #facc15; }}

/* Matrix Summary Table */
.matrix-table {{ width: 100%; border-collapse: collapse; font-size: 0.75rem; margin-bottom: 2rem; white-space: nowrap; }}
.matrix-table th {{ color: #e6b450; padding: 0.4rem 0.3rem; border-bottom: 2px solid #1a1f2a; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.03em; }}
.matrix-table td {{ padding: 0.35rem 0.3rem; border-bottom: 1px solid #12161c; }}
.matrix-table tr:hover {{ background: #151a22; }}
.matrix-table .ticker-link a {{ color: #39bae6; font-weight: bold; text-decoration: none; }}
.matrix-table .ticker-link a:hover {{ text-decoration: underline; }}
.matrix-table .company-col {{ color: #aaa; max-width: 160px; overflow: hidden; text-overflow: ellipsis; }}
.matrix-table .ig-col {{ color: #777; font-size: 0.7rem; max-width: 180px; overflow: hidden; text-overflow: ellipsis; }}
.matrix-table .agent-col {{ text-align: center; min-width: 42px; }}
.num {{ text-align: right; }}
.decision-cell {{ font-weight: bold; text-align: center; }}
.decision-cell.buy {{ color: #4ade80; }}
.decision-cell.sell {{ color: #f87171; }}
.decision-cell.hold {{ color: #facc15; }}
.pos-score {{ color: #4ade80; }}
.neg-score {{ color: #f87171; }}

/* Signal cells in matrix */
.sig {{ text-align: center; font-size: 0.72rem; }}
.sig.bull {{ color: #4ade80; }}
.sig.bear {{ color: #f87171; }}
.sig.neut {{ color: #facc15; }}
.sig.nodata {{ color: #333; }}

/* Ticker Cards */
.ticker-card {{ background: #12161c; border: 1px solid #1a1f2a; border-radius: 8px; padding: 1.2rem; margin-bottom: 1rem; }}
.ticker-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.3rem; }}
.ticker-symbol {{ color: #39bae6; font-size: 1.2rem; font-weight: bold; margin-right: 0.8rem; }}
.company-name {{ color: #aaa; font-size: 0.9rem; }}
.ticker-meta {{ color: #666; font-size: 0.8rem; margin-bottom: 0.3rem; display: flex; justify-content: space-between; }}
.decision {{ padding: 0.2rem 0.8rem; border-radius: 4px; font-weight: bold; font-size: 0.85rem; }}
.decision.buy {{ background: #1a3a2a; color: #4ade80; }}
.decision.sell {{ background: #3a1a1a; color: #f87171; }}
.decision.hold {{ background: #2a2a1a; color: #facc15; }}
.confidence {{ color: #888; font-size: 0.85rem; margin-bottom: 0.8rem; }}
.signals-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
.signals-table th {{ text-align: left; color: #666; padding: 0.3rem 0; border-bottom: 1px solid #1a1f2a; }}
.signals-table td {{ padding: 0.3rem 0; border-bottom: 1px solid #0d1117; }}
.agent-name {{ color: #aaa; }}
.signal.bullish {{ color: #4ade80; }}
.signal.bearish {{ color: #f87171; }}
.signal.neutral {{ color: #facc15; }}
.reasoning {{ color: #666; font-size: 0.72rem; white-space: normal; word-wrap: break-word; }}
.watermark {{ text-align: right; color: rgba(255,255,255,0.18); font-size: 0.75rem; margin-top: 2rem; }}
.back-top {{ text-align: right; margin: 0.5rem 0; }}
.back-top a {{ color: #39bae6; font-size: 0.75rem; text-decoration: none; }}
</style>
</head>
<body>
<h1>AI Hedge Fund — Selection Summary</h1>
<div class="meta">{date} | Market: {market.upper()} | RS 80-89 + Minervini + Large Cap</div>
<div class="stats">{len(all_tickers)} tickers</div>

<h2 id="top">Overview — All Analysts Matrix</h2>
<div style="overflow-x:auto;">
<table class="matrix-table">
<thead>
<tr><th>Ticker</th><th>Company</th><th>Industry Group</th><th>RS</th><th>Score</th>{agent_th}</tr>
</thead>
<tbody>
{summary_rows}
</tbody>
</table>
</div>

<h2>Detail</h2>
{ticker_cards}
<div class="watermark">AlpharvestPro</div>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate AI hedge fund summary report")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--market", type=str, default="us")
    parser.add_argument("--input", type=str, default=None, help="Path to pipeline output JSON")
    args = parser.parse_args()

    analyst_signals = {}

    input_path = args.input or f"output/pipeline_{args.date}.json"
    if Path(input_path).exists():
        data = json.loads(Path(input_path).read_text())
        analyst_signals = data.get("analyst_signals", {})
    else:
        print(f"Warning: No pipeline output at {input_path}", file=__import__("sys").stderr)

    all_tickers = set()
    for agent_data in analyst_signals.values():
        if isinstance(agent_data, dict):
            all_tickers.update(agent_data.keys())

    stock_info = _load_stock_info(list(all_tickers), args.market)

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    text = generate_text_summary(analyst_signals, args.date, args.market, stock_info)
    text_path = output_dir / f"summary_{args.date}.txt"
    text_path.write_text(text)
    print(text)
    print(f"\nSaved to {text_path}")

    html = generate_html_summary(analyst_signals, args.date, args.market, stock_info)
    html_path = output_dir / f"summary_{args.date}.html"
    html_path.write_text(html)
    print(f"HTML saved to {html_path}")


if __name__ == "__main__":
    main()
