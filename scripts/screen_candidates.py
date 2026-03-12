#!/usr/bin/env python3
"""Pre-screen RS>70 candidates from AlpharvestPro databases.
Outputs ticker list for ai-hedge-fund pipeline input.

Usage:
    python scripts/screen_candidates.py --market us --min-rs 80 --top 10
    python scripts/screen_candidates.py --market tw --min-rs 70 --top 20 -o candidates.json
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Default DB paths (adjust for Jetson if synced to ~/data/)
DB_PATHS = {
    "us": [
        Path.home() / "data" / "us_rs.db",  # Jetson (synced from Pi 1)
        Path("/home/lohengrin168/us-stocks/data/rs_rankings.db"),  # Pi 1 direct
    ],
    "jp": [
        Path.home() / "data" / "jp_rs.db",
        Path("/home/lohengrin168/japan-stocks/data/rs_rankings.db"),
    ],
    "tw": [
        Path.home() / "data" / "tw_rs.db",
        Path("/home/alpharvestpro/taiwan-stocks/data/rs_rankings.db"),
    ],
    "hk": [
        Path.home() / "data" / "hk_rs.db",
        Path("/home/alpharvestpro/hk-stocks/data/rs_rankings.db"),
    ],
}

# yfinance suffixes by market
SUFFIXES = {
    "us": "",
    "jp": ".T",
    "tw": ".TW",  # TWSE; TPEX uses .TWO — needs exchange info from DB
    "hk": ".HK",
}


def find_db(market: str, db_path: str = None) -> Path | None:
    """Find the first available DB file for a market."""
    if db_path:
        p = Path(db_path)
        return p if p.exists() else None

    for p in DB_PATHS.get(market, []):
        if p.exists():
            return p
    return None


def screen_rs(db_path: Path, market: str, min_rs: int = 70, top: int = None) -> list[str]:
    """Query RS rankings DB for tickers with RS >= min_rs."""
    conn = sqlite3.connect(str(db_path))

    # US uses 'ticker', TW/HK/JP use 'code'
    col = "ticker" if market == "us" else "code"
    query = f"SELECT {col} FROM rs_rankings WHERE rs_rating >= ? ORDER BY rs_rating DESC"

    if top:
        query += f" LIMIT {top}"

    rows = conn.execute(query, (min_rs,)).fetchall()
    conn.close()

    suffix = SUFFIXES.get(market, "")
    tickers = []
    for (code,) in rows:
        code = str(code).strip()
        if market == "hk":
            # HK: zero-pad to 4 digits + .HK
            tickers.append(f"{code.zfill(4)}{suffix}")
        elif market == "tw":
            # TW: append .TW (could also be .TWO for TPEX — simplified for v1)
            tickers.append(f"{code}{suffix}")
        else:
            tickers.append(f"{code}{suffix}")

    return tickers


def main():
    parser = argparse.ArgumentParser(description="Screen RS>70 candidates for AI hedge fund")
    parser.add_argument("--market", type=str, required=True, choices=["us", "jp", "tw", "hk"])
    parser.add_argument("--min-rs", type=int, default=80, help="Minimum RS rating (default: 80)")
    parser.add_argument("--top", type=int, default=10, help="Max tickers to return (default: 10)")
    parser.add_argument("--db", type=str, default=None, help="Override DB path")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output JSON file (default: stdout)")
    args = parser.parse_args()

    db = find_db(args.market, args.db)
    if not db:
        print(f"Error: No RS rankings DB found for market '{args.market}'", file=sys.stderr)
        print(f"Checked paths: {[str(p) for p in DB_PATHS.get(args.market, [])]}", file=sys.stderr)
        sys.exit(1)

    print(f"Using DB: {db}", file=sys.stderr)
    tickers = screen_rs(db, args.market, args.min_rs, args.top)
    print(f"Found {len(tickers)} tickers with RS >= {args.min_rs}", file=sys.stderr)

    result = {args.market: tickers}

    if args.output:
        # Merge with existing file if present
        out_path = Path(args.output)
        if out_path.exists():
            existing = json.loads(out_path.read_text())
            existing.update(result)
            result = existing
        out_path.write_text(json.dumps(result, indent=2))
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
