# AI Hedge Fund — Jetson Orin NX

Multi-agent AI stock analysis pipeline running locally on Jetson with Qwen 3.5:9B via Ollama.

## Tech Stack
- Python 3.10+, Poetry, LangGraph/LangChain
- LLM: Qwen 3.5:9B via Ollama (localhost:11434), ~10.4 tok/s
- Data: yfinance (primary), SEC EDGAR, ~~FMP~~ (key expired 2026-03-15)
- Database: SQLite `~/data/us_rs.db` (synced from Pi1 `rs_rankings.db`)

## Commands
```bash
# Daily pipeline (screen → analyze → report → deploy)
./scripts/daily_run.sh us

# Individual steps
python scripts/screen_candidates.py --market us --min-rs 80 --max-rs 90 --minervini --large-cap
poetry run python src/main.py --tickers AAPL,MSFT --analysts cathie_wood,technical_analyst,fundamentals_analyst,news_sentiment_analyst,peter_lynch,sentiment_analyst,growth_analyst,valuation_analyst --model qwen3.5:9b --ollama --show-reasoning
poetry run python scripts/generate_summary.py --date 2026-03-15 --market us

# Sync DB from Pi1
./scripts/sync_db.sh

# Generate X cross-signal stories (fetches from Pi2, generates, pushes back)
./scripts/generate_x_stories.sh
# Or manually:
poetry run python scripts/generate_x_stories.py --signals-dir /tmp/x_data --output output/x_stories_DATE.json [--pipeline-json output/pipeline_DATE.json] [--dry-run]

# Backtester
poetry run python src/backtester.py --ticker AAPL,MSFT
```

## Key Architecture
```
src/
├── agents/           # 17 analyst agents + portfolio_manager + risk_manager
├── data/providers/   # yfinance_provider.py (sole working provider), edgar_provider.py
├── graph/            # LangGraph state machine (AgentState)
├── llm/models.py     # Ollama model config
├── tools/api.py      # Data routing layer (FMP → yfinance fallback)
└── main.py           # Entry point
scripts/
├── daily_run.sh      # Full pipeline orchestrator
├── screen_candidates.py  # RS + Minervini + large-cap pre-screener
├── generate_summary.py   # HTML + TXT report generator (matrix table)
├── generate_x_stories.py # Cross-signal X story generator (influencer+trends × candidates → Qwen)
├── generate_x_stories.sh # Wrapper: SCP fetch from Pi2 → generate → push back
└── sync_db.sh        # rsync DB from Pi1 via Tailscale
```

## Database
- **File on Pi1**: `rs_rankings.db` — **Table inside**: `rs_ratings` (names differ!)
- Key tables: `rs_ratings`, `daily_analysis` (minervini_pass), `stocks` (market_cap), `ibd_stock_mapping` (industry group)
- Sync: cron CST 07:00 Tue-Sat via Tailscale SSH to Pi1 (100.72.251.33)

## Screening Criteria
- RS 80-89 + Minervini trend template pass + market_cap >= 10B
- Auto-deduplicates dual-class shares (normalized company name, keep highest cap)
- ~33 tickers qualify currently

## Deployment
Reports upload to Pi2 VIP repo via SCP + git push, triggering two GitHub Actions workflows:
1. **GitHub Pages**: builds static site from `docs/`
2. **Linode deploy**: SSH + rsync to production (172.105.237.37)

```
Jetson → SCP → Pi2:~/alpharvestpro-vip/docs/us/YYYY-MM/ai_hedge_fund_DATE.html
                  → git commit + push → GitHub Actions → GH Pages + Linode
```

- Pi2 SSH: `alpharvestpro@100.64.141.38`
- VIP site: alpharvestpro.vip (Static + FastAPI, Lemon Squeezy payments)
- Also serves .com (WordPress, JP/US) and .win (Ghost, TW/HK) — all on one Linode 4GB Tokyo

## Gotchas
- NEVER use "IBD" in user-facing text — use "Industry Group" / "業種グループ"
- FMP API key is dead — all data comes from yfinance; `api.py` silently falls back
- yfinance `get_financial_metrics()` returns 4-5 annual periods with computed growth; growth agent needs >= 4
- Use Tailscale IPs directly (100.x.x.x), NOT hostnames — DNS unreliable on Jetson
- Ollama serves one request at a time — 17 analysts run sequentially per ticker (~10 min/ticker)
- Pipeline output JSON at `output/pipeline_YYYY-MM-DD.json`; reports at `output/summary_YYYY-MM-DD.{html,txt}`
- X stories output at `output/x_stories_YYYY-MM-DD.json`; pushed to Pi2 `data/x-stories/`
- X stories cron: `30 8 * * 2-6` (after DB sync at 07:00, before first X post slot)
