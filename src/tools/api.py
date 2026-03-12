"""Public data API — routes through free providers (yfinance, SEC EDGAR, FMP).

Function signatures and return types are unchanged from the original
financialdatasets.ai implementation. The `api_key` parameter is kept for
backward compatibility but ignored (free providers don't need it).
"""

import datetime
import pandas as pd

from src.data.cache import get_cache
from src.data.models import (
    CompanyFacts,
    CompanyFactsResponse,
    CompanyNews,
    FinancialMetrics,
    InsiderTrade,
    LineItem,
    Price,
)
from src.data.providers import yfinance_provider, edgar_provider, fmp_provider

# Global in-memory cache instance (unchanged — sits on top of disk cache)
_cache = get_cache()


def get_prices(ticker: str, start_date: str, end_date: str, api_key: str = None) -> list[Price]:
    """Fetch price data. Primary: yfinance."""
    cache_key = f"{ticker}_{start_date}_{end_date}"
    if cached_data := _cache.get_prices(cache_key):
        return [Price(**price) for price in cached_data]

    prices = yfinance_provider.get_prices(ticker, start_date, end_date)

    if prices:
        _cache.set_prices(cache_key, [p.model_dump() for p in prices])
    return prices


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[FinancialMetrics]:
    """Fetch financial metrics. Primary: FMP. Fallback: yfinance."""
    cache_key = f"{ticker}_{period}_{end_date}_{limit}"
    if cached_data := _cache.get_financial_metrics(cache_key):
        return [FinancialMetrics(**metric) for metric in cached_data]

    # Try FMP first (richer data)
    metrics = []
    try:
        metrics = fmp_provider.get_financial_metrics(ticker, end_date, period, limit)
    except Exception:
        pass

    # Fallback to yfinance
    if not metrics:
        try:
            metrics = yfinance_provider.get_financial_metrics(ticker, end_date, period, limit)
        except Exception:
            pass

    if metrics:
        _cache.set_financial_metrics(cache_key, [m.model_dump() for m in metrics])
    return metrics


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
    api_key: str = None,
) -> list[LineItem]:
    """Fetch line items. Primary: SEC EDGAR XBRL. Fallback: yfinance."""
    # Try EDGAR first (exact 10-K/10-Q data)
    try:
        results = edgar_provider.search_line_items(ticker, line_items, end_date, period, limit)
        if results:
            return results
    except Exception:
        pass

    # Fallback to yfinance
    try:
        return yfinance_provider.search_line_items(ticker, line_items, end_date, period, limit)
    except Exception:
        return []


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[InsiderTrade]:
    """Fetch insider trades. Primary: SEC EDGAR Form 4. Fallback: yfinance."""
    cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}"
    if cached_data := _cache.get_insider_trades(cache_key):
        return [InsiderTrade(**trade) for trade in cached_data]

    # Try EDGAR first
    trades = []
    try:
        trades = edgar_provider.get_insider_trades(ticker, end_date, start_date, limit)
    except Exception:
        pass

    # Fallback to yfinance
    if not trades:
        try:
            trades = yfinance_provider.get_insider_trades(ticker, end_date, start_date, limit)
        except Exception:
            pass

    if trades:
        _cache.set_insider_trades(cache_key, [t.model_dump() for t in trades])
    return trades


def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
    api_key: str = None,
) -> list[CompanyNews]:
    """Fetch company news. Primary: yfinance. Fallback: FMP."""
    cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}"
    if cached_data := _cache.get_company_news(cache_key):
        return [CompanyNews(**news) for news in cached_data]

    # Try yfinance first (no API key needed)
    news = []
    try:
        news = yfinance_provider.get_company_news(ticker, end_date, start_date, limit)
    except Exception:
        pass

    # Fallback to FMP
    if not news:
        try:
            news = fmp_provider.get_company_news(ticker, end_date, start_date, limit)
        except Exception:
            pass

    if news:
        _cache.set_company_news(cache_key, [n.model_dump() for n in news])
    return news


def get_market_cap(
    ticker: str,
    end_date: str,
    api_key: str = None,
) -> float | None:
    """Fetch market cap. Primary: yfinance."""
    # Try yfinance .info for current market cap
    if end_date == datetime.datetime.now().strftime("%Y-%m-%d"):
        mc = yfinance_provider.get_market_cap(ticker, end_date)
        if mc:
            return mc

    # Fallback: get from financial metrics
    financial_metrics = get_financial_metrics(ticker, end_date, api_key=api_key)
    if not financial_metrics:
        return None
    return financial_metrics[0].market_cap


def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    """Convert prices to a DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in prices])
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    numeric_cols = ["open", "close", "high", "low", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df


def get_price_data(ticker: str, start_date: str, end_date: str, api_key: str = None) -> pd.DataFrame:
    prices = get_prices(ticker, start_date, end_date, api_key=api_key)
    return prices_to_df(prices)
