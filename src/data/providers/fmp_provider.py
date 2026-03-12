"""Financial Modeling Prep provider — financial metrics. Free tier: 250 req/day."""

import os
import requests

from src.data.models import CompanyNews, FinancialMetrics
from src.data.providers.cache import TTL_4H, TTL_7D, cache_get, cache_set

PROVIDER = "fmp"
BASE_URL = "https://financialmodelingprep.com/api/v3"


def _api_key() -> str | None:
    return os.environ.get("FMP_API_KEY")


def get_financial_metrics(ticker: str, end_date: str, period: str = "ttm", limit: int = 10) -> list[FinancialMetrics]:
    """Fetch financial metrics from FMP /ratios and /key-metrics endpoints."""
    key = _api_key()
    if not key:
        return []

    cached = cache_get(PROVIDER, "get_financial_metrics", ticker, end_date=end_date, period=period, limit=limit)
    if cached is not None:
        return [FinancialMetrics(**m) for m in cached]

    try:
        fmp_period = "quarter" if period in ("quarterly", "q") else "annual"

        # Fetch ratios
        ratios_url = f"{BASE_URL}/ratios/{ticker}?period={fmp_period}&limit={limit}&apikey={key}"
        ratios_resp = requests.get(ratios_url, timeout=15)
        ratios_data = ratios_resp.json() if ratios_resp.status_code == 200 else []

        # Fetch key metrics
        metrics_url = f"{BASE_URL}/key-metrics/{ticker}?period={fmp_period}&limit={limit}&apikey={key}"
        metrics_resp = requests.get(metrics_url, timeout=15)
        metrics_data = metrics_resp.json() if metrics_resp.status_code == 200 else []

        # Fetch growth
        growth_url = f"{BASE_URL}/financial-growth/{ticker}?period={fmp_period}&limit={limit}&apikey={key}"
        growth_resp = requests.get(growth_url, timeout=15)
        growth_data = growth_resp.json() if growth_resp.status_code == 200 else []

        # Merge by date
        all_dates = {}
        for entry in ratios_data:
            d = entry.get("date", "")[:10]
            if d <= end_date:
                all_dates.setdefault(d, {}).update(entry)
        for entry in metrics_data:
            d = entry.get("date", "")[:10]
            if d <= end_date:
                all_dates.setdefault(d, {}).update(entry)
        for entry in growth_data:
            d = entry.get("date", "")[:10]
            if d <= end_date:
                all_dates.setdefault(d, {}).update(entry)

        sorted_dates = sorted(all_dates.keys(), reverse=True)[:limit]

        results = []
        for date_str in sorted_dates:
            d = all_dates[date_str]
            results.append(FinancialMetrics(
                ticker=ticker,
                report_period=date_str,
                period=period,
                currency="USD",
                market_cap=d.get("marketCap"),
                enterprise_value=d.get("enterpriseValue"),
                price_to_earnings_ratio=d.get("peRatio", d.get("priceEarningsRatio")),
                price_to_book_ratio=d.get("priceToBookRatio", d.get("pbRatio")),
                price_to_sales_ratio=d.get("priceToSalesRatio"),
                enterprise_value_to_ebitda_ratio=d.get("enterpriseValueOverEBITDA"),
                enterprise_value_to_revenue_ratio=d.get("evToSales"),
                free_cash_flow_yield=d.get("freeCashFlowYield"),
                peg_ratio=d.get("pegRatio"),
                gross_margin=d.get("grossProfitMargin"),
                operating_margin=d.get("operatingProfitMargin"),
                net_margin=d.get("netProfitMargin"),
                return_on_equity=d.get("returnOnEquity"),
                return_on_assets=d.get("returnOnAssets"),
                return_on_invested_capital=d.get("roic", d.get("returnOnCapitalEmployed")),
                asset_turnover=d.get("assetTurnover"),
                inventory_turnover=d.get("inventoryTurnover"),
                receivables_turnover=d.get("receivablesTurnover"),
                days_sales_outstanding=d.get("daysOfSalesOutstanding", d.get("daysSalesOutstanding")),
                operating_cycle=d.get("operatingCycle"),
                working_capital_turnover=None,
                current_ratio=d.get("currentRatio"),
                quick_ratio=d.get("quickRatio"),
                cash_ratio=d.get("cashRatio"),
                operating_cash_flow_ratio=d.get("operatingCashFlowPerShare"),
                debt_to_equity=d.get("debtEquityRatio", d.get("debtToEquity")),
                debt_to_assets=d.get("debtToAssets"),
                interest_coverage=d.get("interestCoverage"),
                revenue_growth=d.get("revenueGrowth"),
                earnings_growth=d.get("epsgrowth", d.get("epsDilutedGrowth")),
                book_value_growth=d.get("bookValueperShareGrowth"),
                earnings_per_share_growth=d.get("epsgrowth", d.get("epsDilutedGrowth")),
                free_cash_flow_growth=d.get("freeCashFlowGrowth"),
                operating_income_growth=d.get("operatingIncomeGrowth"),
                ebitda_growth=d.get("ebitdagrowth"),
                payout_ratio=d.get("payoutRatio", d.get("dividendPayoutRatio")),
                earnings_per_share=d.get("netIncomePerShare"),
                book_value_per_share=d.get("bookValuePerShare"),
                free_cash_flow_per_share=d.get("freeCashFlowPerShare"),
            ))

        if results:
            cache_set(PROVIDER, "get_financial_metrics", ticker, [m.model_dump() for m in results], TTL_7D, end_date=end_date, period=period, limit=limit)
        return results
    except Exception as e:
        print(f"[FMP] Error fetching metrics for {ticker}: {e}")
        return []


def get_company_news(ticker: str, end_date: str, start_date: str = None, limit: int = 100) -> list[CompanyNews]:
    """Fetch news from FMP /stock_news endpoint."""
    key = _api_key()
    if not key:
        return []

    cached = cache_get(PROVIDER, "get_company_news", ticker, end_date=end_date, start_date=start_date, limit=limit)
    if cached is not None:
        return [CompanyNews(**n) for n in cached]

    try:
        url = f"{BASE_URL}/stock_news?tickers={ticker}&limit={min(limit, 50)}&apikey={key}"
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return []

        articles = []
        for item in resp.json():
            pub_date = item.get("publishedDate", "")[:19]
            date_only = pub_date[:10]
            if end_date and date_only > end_date:
                continue
            if start_date and date_only < start_date:
                continue

            articles.append(CompanyNews(
                ticker=ticker,
                title=item.get("title", ""),
                author=item.get("site", ""),
                source=item.get("site", ""),
                date=pub_date,
                url=item.get("url", ""),
                sentiment=item.get("sentiment"),
            ))

        if articles:
            cache_set(PROVIDER, "get_company_news", ticker, [a.model_dump() for a in articles], TTL_4H, end_date=end_date, start_date=start_date, limit=limit)
        return articles
    except Exception as e:
        print(f"[FMP] Error fetching news for {ticker}: {e}")
        return []
