#!/bin/bash
# Sync RS rankings database from Pi1 to Jetson via Tailscale SSH
# Pi1 daily pipeline runs at CDT 16:00 (CST 05:00), typically finishes in ~30 min
# Schedule this at CST 06:00 (CDT 17:00) to ensure fresh data
#
# Usage: ./scripts/sync_db.sh [--all]
#   --all: sync all markets (us, jp), default: us only

set -euo pipefail
DATE=$(date +%Y-%m-%d)
LOG_DIR="$HOME/log"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sync_db.log"

PI1_HOST="lohengrin168@100.72.251.33"
LOCAL_DATA="$HOME/data"
mkdir -p "$LOCAL_DATA"

sync_db() {
    local remote_path="$1"
    local local_file="$2"
    local label="$3"

    echo "[$DATE $(date +%H:%M:%S)] Syncing $label ..."
    if rsync -az --timeout=120 \
        "$PI1_HOST:$remote_path" \
        "$LOCAL_DATA/$local_file"; then
        local size
        size=$(du -h "$LOCAL_DATA/$local_file" | cut -f1)
        echo "[$DATE $(date +%H:%M:%S)] $label synced OK ($size)"
    else
        echo "[$DATE $(date +%H:%M:%S)] ERROR: $label sync failed (exit $?)" >&2
        return 1
    fi
}

{
    echo ""
    echo "========== Sync started: $DATE $(date +%H:%M:%S) =========="

    # Always sync US
    sync_db "/home/lohengrin168/us-stocks/data/rs_rankings.db" "us_rs.db" "US RS DB"

    # Sync JP if --all
    if [[ "${1:-}" == "--all" ]]; then
        sync_db "/home/lohengrin168/japan-stocks/data/rs_rankings.db" "jp_rs.db" "JP RS DB"
    fi

    echo "========== Sync complete: $DATE $(date +%H:%M:%S) =========="
} >> "$LOG" 2>&1

# Print summary to stdout (useful for manual runs)
tail -5 "$LOG"
