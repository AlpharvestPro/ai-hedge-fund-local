#!/bin/bash
# Daily AI Hedge Fund run — designed for Jetson Orin NX cron
# Usage: ./scripts/daily_run.sh [us|tw|hk|jp]

set -euo pipefail
MARKET=${1:-us}
DATE=$(date +%Y-%m-%d)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

cd "$REPO_DIR"

echo "[$DATE] Starting AI hedge fund analysis for $MARKET"

# 1. Screen RS>80 candidates
python scripts/screen_candidates.py --market "$MARKET" --min-rs 80 --top 10 -o candidates.json

# 2. Extract tickers
TICKERS=$(python -c "import json; print(','.join(json.load(open('candidates.json'))['$MARKET']))")
if [ -z "$TICKERS" ]; then
    echo "[$DATE] No tickers found for $MARKET with RS >= 80"
    exit 0
fi

echo "[$DATE] Analyzing tickers: $TICKERS"

# 3. Run AI pipeline
poetry run python src/main.py \
    --tickers "$TICKERS" \
    --analysts-all \
    --model qwen3.5:9b \
    --ollama \
    --show-reasoning

# 4. Generate summary report
mkdir -p output
poetry run python scripts/generate_summary.py --date "$DATE" --market "$MARKET"

# 5. Sync to VIP portal (optional — uncomment when ready)
# scp output/summary_${DATE}.html linode:/var/www/vip/reports/ai-hedge-fund/
# ssh linode "ln -sf /var/www/vip/reports/ai-hedge-fund/summary_${DATE}.html /var/www/vip/reports/ai-hedge-fund/latest.html"

echo "[$DATE] AI hedge fund analysis complete for $MARKET — $(echo $TICKERS | tr ',' '\n' | wc -l) tickers"
