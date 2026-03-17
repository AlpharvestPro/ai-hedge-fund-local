#!/usr/bin/env python3
"""Pre-screen candidates from AlpharvestPro databases.
Outputs ticker list for ai-hedge-fund pipeline input.

Usage:
    python scripts/screen_candidates.py --market us --min-rs 80 --top 10
    python scripts/screen_candidates.py --market us --min-rs 80 --max-rs 90 --minervini --large-cap
    python scripts/screen_candidates.py --market tw --min-rs 70 --top 20 -o candidates.json
"""

import argparse
import json
import re
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

# Minimum market cap for large-cap filter (USD)
LARGE_CAP_MIN = 10_000_000_000  # 10B

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


def _normalize_company(name: str) -> str:
    """Strip share class suffixes for deduplication.
    e.g. 'Alphabet Inc. - Class A Common Stock' -> 'Alphabet Inc.'
    """
    if not name:
        return ""
    n = re.sub(
        r"\s*-?\s*(Class\s+[A-C]|Common\s+(Stock|Shares?)|Capital\s+Stock|Ordinary\s+Shares?).*",
        "", name, flags=re.IGNORECASE,
    )
    return n.strip().rstrip(" -,")


def _dedup_tickers(rows: list[tuple]) -> list[tuple]:
    """Remove dual-class duplicates, keeping highest market_cap.
    Input rows: (ticker, company_name, rs_rating, market_cap)
    """
    by_company = {}
    for ticker, name, rs, cap in rows:
        base = _normalize_company(name)
        if base not in by_company or (cap or 0) > (by_company[base][3] or 0):
            by_company[base] = (ticker, name, rs, cap)
    return list(by_company.values())


def screen_rs(db_path: Path, market: str, min_rs: int = 70, max_rs: int = None,
              top: int = None, minervini: bool = False, large_cap: bool = False,
              dedup: bool = True) -> list[str]:
    """Query RS rankings DB for tickers matching criteria."""
    conn = sqlite3.connect(str(db_path))

    col = "ticker" if market == "us" else "code"

    if minervini and market == "us":
        # Join with daily_analysis for Minervini filter
        query = f"""
            SELECT r.{col}, s.company_name, r.rs_rating, s.market_cap
            FROM rs_ratings r
            JOIN daily_analysis d ON r.{col} = d.{col} AND r.date = d.date
            JOIN stocks s ON r.{col} = s.{col}
            WHERE r.date = (SELECT MAX(date) FROM rs_ratings)
            AND d.minervini_pass = 1
            AND r.rs_rating >= ?
        """
        params = [min_rs]

        if max_rs is not None:
            query += " AND r.rs_rating < ?"
            params.append(max_rs)

        if large_cap:
            query += " AND s.market_cap >= ?"
            params.append(LARGE_CAP_MIN)

        query += " ORDER BY r.rs_rating DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()

        if dedup:
            rows = _dedup_tickers(rows)
            rows.sort(key=lambda x: x[2], reverse=True)  # re-sort by RS

        n_before = len(rows)
        if top:
            rows = rows[:top]

        suffix = SUFFIXES.get(market, "")
        tickers = [str(r[0]).strip() + suffix for r in rows]

        dedup_note = f" (deduped from {n_before})" if dedup and n_before != len(rows) else ""
        print(f"Minervini pass: {len(tickers)} tickers{dedup_note}", file=sys.stderr)
        return tickers

    # Simple RS-only query (original behavior, works for all markets)
    query = f"SELECT {col} FROM rs_ratings WHERE rs_rating >= ? ORDER BY rs_rating DESC"
    params = [min_rs]

    if max_rs is not None:
        query = f"SELECT {col} FROM rs_ratings WHERE rs_rating >= ? AND rs_rating < ? ORDER BY rs_rating DESC"
        params.append(max_rs)

    if top:
        query += f" LIMIT {top}"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    suffix = SUFFIXES.get(market, "")
    tickers = []
    for (code,) in rows:
        code = str(code).strip()
        if market == "hk":
            tickers.append(f"{code.zfill(4)}{suffix}")
        else:
            tickers.append(f"{code}{suffix}")

    return tickers


def main():
    parser = argparse.ArgumentParser(description="Screen candidates for AI hedge fund")
    parser.add_argument("--market", type=str, required=True, choices=["us", "jp", "tw", "hk"])
    parser.add_argument("--min-rs", type=int, default=80, help="Minimum RS rating (default: 80)")
    parser.add_argument("--max-rs", type=int, default=None, help="Maximum RS rating exclusive (e.g. 90 = RS 80-89)")
    parser.add_argument("--minervini", action="store_true", help="Require Minervini trend template pass")
    parser.add_argument("--large-cap", action="store_true", help="Large-cap only (market_cap >= 10B)")
    parser.add_argument("--no-dedup", action="store_true", help="Skip dual-class deduplication")
    parser.add_argument("--top", type=int, default=None, help="Max tickers to return")
    parser.add_argument("--db", type=str, default=None, help="Override DB path")
    parser.add_argument("-o", "--output", type=str, default=None, help="Output JSON file (default: stdout)")
    args = parser.parse_args()

    db = find_db(args.market, args.db)
    if not db:
        print(f"Error: No RS rankings DB found for market '{args.market}'", file=sys.stderr)
        print(f"Checked paths: {[str(p) for p in DB_PATHS.get(args.market, [])]}", file=sys.stderr)
        sys.exit(1)

    print(f"Using DB: {db}", file=sys.stderr)
    tickers = screen_rs(db, args.market, args.min_rs, args.max_rs, args.top,
                        args.minervini, args.large_cap, dedup=not args.no_dedup)
    print(f"Found {len(tickers)} tickers with RS >= {args.min_rs}"
          + (f" and < {args.max_rs}" if args.max_rs else ""), file=sys.stderr)

    result = {args.market: tickers}

    if args.output:
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
