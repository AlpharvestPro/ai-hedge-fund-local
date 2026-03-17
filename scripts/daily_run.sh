#!/bin/bash
# Daily AI Hedge Fund run — designed for Jetson Orin NX cron
# Usage: ./scripts/daily_run.sh [us|tw|hk|jp]

set -euo pipefail
MARKET=${1:-us}
DATE=$(date +%Y-%m-%d)
MONTH=$(date +%Y-%m)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PI2="alpharvestpro@100.64.141.38"
VIP_REPO="alpharvestpro-vip"

cd "$REPO_DIR"

echo "[$DATE] Starting AI hedge fund analysis for $MARKET"

# 1. Screen RS 80-89 + Minervini + large-cap (S&P 500 class), dedup dual-class shares
python scripts/screen_candidates.py --market "$MARKET" --min-rs 80 --max-rs 90 --minervini --large-cap -o candidates.json

# 2. Extract tickers
TICKERS=$(python -c "import json; print(','.join(json.load(open('candidates.json'))['$MARKET']))")
if [ -z "$TICKERS" ]; then
    echo "[$DATE] No tickers found for $MARKET with RS >= 80"
    exit 0
fi

echo "[$DATE] Analyzing tickers: $TICKERS"

# 3. Run AI pipeline (8 selected analysts in 2 logical batches, not --analysts-all)
ANALYSTS="technical_analyst,fundamentals_analyst,news_sentiment_analyst,peter_lynch,sentiment_analyst,growth_analyst,valuation_analyst"
poetry run python src/main.py \
    --tickers "$TICKERS" \
    --analysts "$ANALYSTS" \
    --model qwen2.5:7b \
    --ollama \
    --show-reasoning

# 4. Generate summary report
mkdir -p output
poetry run python scripts/generate_summary.py --date "$DATE" --market "$MARKET"

# 5. Upload to VIP repo on Pi2 → triggers GitHub Pages + Linode deploy
REPORT="output/summary_${DATE}.html"
if [ -f "$REPORT" ]; then
    echo "[$DATE] Uploading report to VIP repo..."
    ssh "$PI2" "mkdir -p ~/$VIP_REPO/docs/us/$MONTH"
    scp "$REPORT" "$PI2:~/$VIP_REPO/docs/us/$MONTH/ai_hedge_fund_${DATE}.html"
    ssh "$PI2" "cd ~/$VIP_REPO && \
        git add docs/us/$MONTH/ai_hedge_fund_${DATE}.html && \
        git commit -m 'AI hedge fund report ${DATE} (${MARKET})' && \
        git pull --rebase origin main && \
        git push origin main" 2>&1 || echo "[$DATE] WARNING: git push failed, report saved locally"
    echo "[$DATE] Report deployed: docs/us/$MONTH/ai_hedge_fund_${DATE}.html"
fi

echo "[$DATE] AI hedge fund analysis complete for $MARKET — $(echo $TICKERS | tr ',' '\n' | wc -l) tickers"
