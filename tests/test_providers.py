"""Tests for free data providers (yfinance, EDGAR, FMP) and the api.py routing layer.

Run: poetry run pytest tests/test_providers.py -v
"""

import os
import pytest
from datetime import datetime, timedelta

# Test against AAPL — reliable, liquid, US stock
TICKER = "AAPL"
END_DATE = datetime.now().strftime("%Y-%m-%d")
START_DATE = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")


class TestYfinanceProvider:
    """Test yfinance provider functions."""

    def test_get_prices(self):
        from src.data.providers.yfinance_provider import get_prices
        prices = get_prices(TICKER, START_DATE, END_DATE)
        assert len(prices) > 0
        assert prices[0].open > 0
        assert prices[0].close > 0
        assert prices[0].volume > 0
        assert prices[0].time  # has date string

    def test_get_financial_metrics(self):
        from src.data.providers.yfinance_provider import get_financial_metrics
        metrics = get_financial_metrics(TICKER, END_DATE)
        assert len(metrics) > 0
        m = metrics[0]
        assert m.ticker == TICKER
        assert m.market_cap is not None and m.market_cap > 0

    def test_search_line_items(self):
        from src.data.providers.yfinance_provider import search_line_items
        items = search_line_items(TICKER, ["revenue", "net_income"], END_DATE, period="annual", limit=3)
        assert len(items) > 0
        # At least one item should have revenue data
        has_revenue = any(getattr(li, "revenue", None) is not None for li in items)
        assert has_revenue, "Expected at least one LineItem with revenue"

    def test_get_insider_trades(self):
        from src.data.providers.yfinance_provider import get_insider_trades
        trades = get_insider_trades(TICKER, END_DATE, limit=10)
        # May be empty for some tickers, but should not raise
        assert isinstance(trades, list)

    def test_get_company_news(self):
        from src.data.providers.yfinance_provider import get_company_news
        news = get_company_news(TICKER, END_DATE, limit=10)
        # yfinance news may be empty in some environments
        assert isinstance(news, list)

    def test_get_market_cap(self):
        from src.data.providers.yfinance_provider import get_market_cap
        mc = get_market_cap(TICKER, END_DATE)
        assert mc is not None and mc > 0

    def test_get_company_facts(self):
        from src.data.providers.yfinance_provider import get_company_facts
        facts = get_company_facts(TICKER)
        assert facts is not None
        assert facts.ticker == TICKER
        assert facts.name  # should have a name


class TestEdgarProvider:
    """Test SEC EDGAR provider functions."""

    def test_get_insider_trades(self):
        from src.data.providers.edgar_provider import get_insider_trades
        trades = get_insider_trades(TICKER, END_DATE, limit=5)
        assert isinstance(trades, list)

    def test_search_line_items(self):
        from src.data.providers.edgar_provider import search_line_items
        items = search_line_items(TICKER, ["revenue", "net_income"], END_DATE, period="annual", limit=3)
        assert isinstance(items, list)

    def test_get_company_facts(self):
        from src.data.providers.edgar_provider import get_company_facts
        facts = get_company_facts(TICKER)
        # May fail in CI without proper User-Agent, but should not crash
        assert facts is None or facts.ticker == TICKER


class TestFmpProvider:
    """Test FMP provider (only runs if FMP_API_KEY is set)."""

    @pytest.mark.skipif(not os.environ.get("FMP_API_KEY"), reason="FMP_API_KEY not set")
    def test_get_financial_metrics(self):
        from src.data.providers.fmp_provider import get_financial_metrics
        metrics = get_financial_metrics(TICKER, END_DATE, limit=3)
        assert len(metrics) > 0
        assert metrics[0].ticker == TICKER

    @pytest.mark.skipif(not os.environ.get("FMP_API_KEY"), reason="FMP_API_KEY not set")
    def test_get_company_news(self):
        from src.data.providers.fmp_provider import get_company_news
        news = get_company_news(TICKER, END_DATE, limit=5)
        assert isinstance(news, list)


class TestApiRouting:
    """Test the main api.py routing layer."""

    def test_get_prices(self):
        from src.tools.api import get_prices
        prices = get_prices(TICKER, START_DATE, END_DATE)
        assert len(prices) > 0

    def test_get_financial_metrics(self):
        from src.tools.api import get_financial_metrics
        metrics = get_financial_metrics(TICKER, END_DATE)
        assert len(metrics) > 0

    def test_search_line_items(self):
        from src.tools.api import search_line_items
        items = search_line_items(TICKER, ["revenue"], END_DATE, period="annual", limit=3)
        assert isinstance(items, list)

    def test_get_insider_trades(self):
        from src.tools.api import get_insider_trades
        trades = get_insider_trades(TICKER, END_DATE, limit=10)
        assert isinstance(trades, list)

    def test_get_company_news(self):
        from src.tools.api import get_company_news
        news = get_company_news(TICKER, END_DATE, limit=10)
        assert isinstance(news, list)

    def test_get_market_cap(self):
        from src.tools.api import get_market_cap
        mc = get_market_cap(TICKER, END_DATE)
        assert mc is not None and mc > 0

    def test_prices_to_df(self):
        from src.tools.api import get_prices, prices_to_df
        prices = get_prices(TICKER, START_DATE, END_DATE)
        df = prices_to_df(prices)
        assert len(df) > 0
        assert "close" in df.columns


class TestDiskCache:
    """Test the SQLite disk cache."""

    def test_cache_set_get(self):
        from src.data.providers.cache import cache_get, cache_set, cache_clear
        # Clean test entry
        cache_clear(provider="test")
        cache_set("test", "func", "AAPL", [{"a": 1}], ttl=3600)
        result = cache_get("test", "func", "AAPL")
        assert result == [{"a": 1}]
        cache_clear(provider="test")

    def test_cache_ttl_expiry(self):
        import time
        from src.data.providers.cache import cache_get, cache_set, cache_clear
        cache_clear(provider="test")
        cache_set("test", "func", "AAPL", [{"b": 2}], ttl=1)
        time.sleep(1.5)
        result = cache_get("test", "func", "AAPL")
        assert result is None
        cache_clear(provider="test")

    def test_cache_infinite_ttl(self):
        from src.data.providers.cache import cache_get, cache_set, cache_clear, TTL_INFINITE
        cache_clear(provider="test")
        cache_set("test", "func", "AAPL", [{"c": 3}], ttl=TTL_INFINITE)
        result = cache_get("test", "func", "AAPL")
        assert result == [{"c": 3}]
        cache_clear(provider="test")
