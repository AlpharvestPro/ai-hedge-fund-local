"""SQLite disk cache with TTL for API responses."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

# Register numpy type adapters
sqlite3.register_adapter(np.int64, int)
sqlite3.register_adapter(np.float64, float)
sqlite3.register_adapter(np.bool_, bool)

_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "cache.db"

# TTL constants (seconds)
TTL_INFINITE = 0  # never expires
TTL_4H = 4 * 3600
TTL_24H = 24 * 3600
TTL_7D = 7 * 86400
TTL_30D = 30 * 86400


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_cache (
            cache_key   TEXT PRIMARY KEY,
            response    TEXT NOT NULL,
            fetched_at  REAL NOT NULL,
            ttl_seconds INTEGER NOT NULL,
            provider    TEXT NOT NULL,
            ticker      TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_provider ON api_cache(provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_ticker ON api_cache(ticker)")
    conn.commit()
    return conn


def _make_key(provider: str, function: str, ticker: str, **kwargs) -> str:
    raw = json.dumps({"provider": provider, "function": function, "ticker": ticker, **kwargs}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def cache_get(provider: str, function: str, ticker: str, **kwargs) -> list | None:
    """Returns cached response if fresh, None if expired/missing."""
    key = _make_key(provider, function, ticker, **kwargs)
    conn = _get_conn()
    try:
        row = conn.execute("SELECT response, fetched_at, ttl_seconds FROM api_cache WHERE cache_key = ?", (key,)).fetchone()
        if row is None:
            return None
        response_json, fetched_at, ttl = row
        if ttl > 0 and (time.time() - fetched_at) > ttl:
            conn.execute("DELETE FROM api_cache WHERE cache_key = ?", (key,))
            conn.commit()
            return None
        return json.loads(response_json)
    finally:
        conn.close()


def cache_set(provider: str, function: str, ticker: str, response: list, ttl: int, **kwargs):
    """Stores response with TTL."""
    key = _make_key(provider, function, ticker, **kwargs)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO api_cache (cache_key, response, fetched_at, ttl_seconds, provider, ticker) VALUES (?, ?, ?, ?, ?, ?)",
            (key, json.dumps(response, default=str), time.time(), ttl, provider, ticker),
        )
        conn.commit()
    finally:
        conn.close()


def cache_clear(provider: str = None, ticker: str = None):
    """Clear cache entries. Optional filter by provider or ticker."""
    conn = _get_conn()
    try:
        if provider and ticker:
            conn.execute("DELETE FROM api_cache WHERE provider = ? AND ticker = ?", (provider, ticker))
        elif provider:
            conn.execute("DELETE FROM api_cache WHERE provider = ?", (provider,))
        elif ticker:
            conn.execute("DELETE FROM api_cache WHERE ticker = ?", (ticker,))
        else:
            conn.execute("DELETE FROM api_cache")
        conn.commit()
    finally:
        conn.close()
