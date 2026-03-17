#!/usr/bin/env bash
# generate_x_stories.sh — Fetch signals from Pi2, generate cross-signal X stories, push back
# Crontab: 30 8 * * 2-6 (CST 08:30 Tue-Sat)
set -euo pipefail

DATE=$(date +%Y-%m-%d)
PI2="alpharvestpro@100.64.141.38"
LOG="$HOME/log/x_stories.log"
SIGNALS_DIR="/tmp/x_data"
PROJECT_DIR="$HOME/ai-hedge-fund-local"

mkdir -p "$HOME/log"
echo "[$DATE $(date +%H:%M:%S)] Starting X story generation" >> "$LOG"

# 1. Fetch last 3 days of signals/trends from Pi2
echo "[$DATE] Fetching signals from Pi2..." >> "$LOG"
rm -rf "$SIGNALS_DIR"
mkdir -p "$SIGNALS_DIR"

for i in 0 1 2; do
    d=$(date -d "-${i} days" +%Y-%m-%d)
    scp -q "$PI2:~/alpharvestpro-vip/data/influencer-signals/${d}.md" "$SIGNALS_DIR/" 2>/dev/null || true
    # Rename x-trends files so parser can distinguish them
    scp -q "$PI2:~/alpharvestpro-vip/data/x-trends/${d}.md" "$SIGNALS_DIR/xtrends_${d}.md" 2>/dev/null || true
done

fetched=$(ls "$SIGNALS_DIR"/*.md 2>/dev/null | wc -l)
echo "[$DATE] Fetched $fetched markdown files" >> "$LOG"

if [ "$fetched" -eq 0 ]; then
    echo "[$DATE] No signal files found — exiting" >> "$LOG"
    exit 0
fi

# 2. Find pipeline JSON (today or yesterday)
PIPELINE_JSON=""
if [ -f "$PROJECT_DIR/output/pipeline_${DATE}.json" ]; then
    PIPELINE_JSON="$PROJECT_DIR/output/pipeline_${DATE}.json"
else
    YESTERDAY=$(date -d "-1 days" +%Y-%m-%d)
    if [ -f "$PROJECT_DIR/output/pipeline_${YESTERDAY}.json" ]; then
        PIPELINE_JSON="$PROJECT_DIR/output/pipeline_${YESTERDAY}.json"
        echo "[$DATE] Using yesterday's pipeline: $PIPELINE_JSON" >> "$LOG"
    else
        echo "[$DATE] No pipeline JSON found — DB-only mode" >> "$LOG"
    fi
fi

# 3. Run generator
cd "$PROJECT_DIR"
PIPELINE_ARG=""
if [ -n "$PIPELINE_JSON" ]; then
    PIPELINE_ARG="--pipeline-json $PIPELINE_JSON"
fi

poetry run python scripts/generate_x_stories.py \
    --signals-dir "$SIGNALS_DIR" \
    $PIPELINE_ARG \
    --output "output/x_stories_${DATE}.json" \
    >> "$LOG" 2>&1

OUTPUT_FILE="$PROJECT_DIR/output/x_stories_${DATE}.json"
if [ ! -f "$OUTPUT_FILE" ]; then
    echo "[$DATE] ERROR: Output file not created" >> "$LOG"
    exit 1
fi

# Check if there are any stories (skip push if empty)
STORY_COUNT=$(python3 -c "
import json
d = json.load(open('$OUTPUT_FILE'))
print(len(d.get('stories', [])) + len(d.get('theme_matches', [])))
")

if [ "$STORY_COUNT" -eq 0 ]; then
    echo "[$DATE] No stories generated — skipping push" >> "$LOG"
    exit 0
fi

# 4. Push to Pi2
echo "[$DATE] Pushing $STORY_COUNT stories to Pi2..." >> "$LOG"
ssh "$PI2" "mkdir -p ~/alpharvestpro-vip/data/x-stories"
scp "$OUTPUT_FILE" "$PI2:~/alpharvestpro-vip/data/x-stories/" >> "$LOG" 2>&1

# 5. Commit on Pi2
ssh "$PI2" "cd ~/alpharvestpro-vip && git add data/x-stories/ && \
    git diff --cached --quiet || git commit -m 'x-stories: ${DATE}' && \
    git pull --rebase && git push" >> "$LOG" 2>&1

echo "[$DATE $(date +%H:%M:%S)] Done — $STORY_COUNT stories pushed" >> "$LOG"
