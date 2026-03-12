"""SEC EDGAR provider — insider trades, line items, company facts. No API key required."""

import os
import time
import requests

from src.data.models import CompanyFacts, InsiderTrade, LineItem
from src.data.providers.cache import TTL_24H, TTL_30D, cache_get, cache_set

PROVIDER = "edgar"
BASE_URL = "https://data.sec.gov"
EFTS_URL = "https://efts.sec.gov"

_last_request_time = 0.0


def _headers() -> dict:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "alpharvestpro taitientzu@gmail.com")
    return {"User-Agent": ua, "Accept": "application/json"}


def _rate_limit():
    """SEC EDGAR requires max 10 requests/second."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 0.12:
        time.sleep(0.12 - elapsed)
    _last_request_time = time.time()


def _get_cik(ticker: str) -> str | None:
    """Look up CIK from ticker via SEC EDGAR company tickers JSON."""
    cached = cache_get(PROVIDER, "cik_lookup", ticker)
    if cached is not None:
        return cached[0] if cached else None

    _rate_limit()
    try:
        resp = requests.get(f"{EFTS_URL}/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K", headers=_headers(), timeout=10)
        if resp.status_code != 200:
            # Fallback: try the company tickers file
            resp2 = requests.get(f"{BASE_URL}/files/company_tickers.json", headers=_headers(), timeout=10)
            if resp2.status_code == 200:
                data = resp2.json()
                for entry in data.values():
                    if entry.get("ticker", "").upper() == ticker.upper():
                        cik = str(entry["cik_str"]).zfill(10)
                        cache_set(PROVIDER, "cik_lookup", ticker, [cik], TTL_30D)
                        return cik
            return None

        # Try full-text search
        resp3 = requests.get(f"{BASE_URL}/files/company_tickers.json", headers=_headers(), timeout=10)
        if resp3.status_code == 200:
            data = resp3.json()
            for entry in data.values():
                if entry.get("ticker", "").upper() == ticker.upper():
                    cik = str(entry["cik_str"]).zfill(10)
                    cache_set(PROVIDER, "cik_lookup", ticker, [cik], TTL_30D)
                    return cik
    except Exception as e:
        print(f"[EDGAR] CIK lookup error for {ticker}: {e}")
    return None


def get_insider_trades(ticker: str, end_date: str, start_date: str = None, limit: int = 50) -> list[InsiderTrade]:
    """Fetch insider trades from SEC EDGAR Form 4 filings."""
    cached = cache_get(PROVIDER, "get_insider_trades", ticker, end_date=end_date, start_date=start_date, limit=limit)
    if cached is not None:
        return [InsiderTrade(**t) for t in cached]

    cik = _get_cik(ticker)
    if not cik:
        return []

    _rate_limit()
    try:
        url = f"{BASE_URL}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb={end_date.replace('-', '')}&owner=include&count={limit}&output=atom"
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            return []

        # Parse the Atom feed for Form 4 entries
        # SEC EDGAR Atom format is XML — use a simpler approach via submissions API
        _rate_limit()
        sub_url = f"{BASE_URL}/submissions/CIK{cik}.json"
        sub_resp = requests.get(sub_url, headers=_headers(), timeout=15)
        if sub_resp.status_code != 200:
            return []

        sub_data = sub_resp.json()
        recent = sub_data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        names = recent.get("name", []) if "name" in recent else [""] * len(forms)

        trades = []
        for i, form in enumerate(forms):
            if form != "4":
                continue
            filing_date = dates[i] if i < len(dates) else ""
            if end_date and filing_date > end_date:
                continue
            if start_date and filing_date < start_date:
                continue

            trades.append(InsiderTrade(
                ticker=ticker,
                issuer=sub_data.get("name", ""),
                name=names[i] if i < len(names) else None,
                title=None,
                is_board_director=None,
                transaction_date=filing_date,
                transaction_shares=None,
                transaction_price_per_share=None,
                transaction_value=None,
                shares_owned_before_transaction=None,
                shares_owned_after_transaction=None,
                security_title=None,
                filing_date=filing_date,
            ))

            if len(trades) >= limit:
                break

        cache_set(PROVIDER, "get_insider_trades", ticker, [t.model_dump() for t in trades], TTL_24H, end_date=end_date, start_date=start_date, limit=limit)
        return trades
    except Exception as e:
        print(f"[EDGAR] Error fetching insider trades for {ticker}: {e}")
        return []


def search_line_items(ticker: str, line_items: list[str], end_date: str, period: str = "ttm", limit: int = 10) -> list[LineItem]:
    """Fetch financial line items from SEC EDGAR XBRL company-concept API."""
    cached = cache_get(PROVIDER, "search_line_items", ticker, line_items=line_items, end_date=end_date, period=period, limit=limit)
    if cached is not None:
        return [LineItem(**li) for li in cached]

    cik = _get_cik(ticker)
    if not cik:
        return []

    # Map common line item names to XBRL tags
    xbrl_map = {
        "revenue": "Revenues",
        "total_revenue": "Revenues",
        "net_income": "NetIncomeLoss",
        "gross_profit": "GrossProfit",
        "operating_income": "OperatingIncomeLoss",
        "total_assets": "Assets",
        "total_liabilities": "Liabilities",
        "total_debt": "LongTermDebt",
        "free_cash_flow": "NetCashProvidedByOperatingActivities",
        "operating_cash_flow": "NetCashProvidedByOperatingActivities",
        "capital_expenditure": "PaymentsToAcquirePropertyPlantAndEquipment",
        "research_and_development": "ResearchAndDevelopmentExpense",
        "outstanding_shares": "CommonStockSharesOutstanding",
        "depreciation_and_amortization": "DepreciationDepletionAndAmortization",
        "earnings_per_share": "EarningsPerShareBasic",
        "dividends_and_other_cash_distributions": "PaymentsOfDividends",
        "operating_expense": "OperatingExpenses",
        "debt_to_equity": "DebtToEquityRatio",
        "gross_margin": "GrossProfit",
        "operating_margin": "OperatingIncomeLoss",
    }

    # Collect data per report period
    period_data: dict[str, dict[str, float]] = {}

    for item_name in line_items:
        normalized = item_name.lower().replace(" ", "_").replace("-", "_")
        xbrl_tag = xbrl_map.get(normalized)
        if not xbrl_tag:
            continue

        _rate_limit()
        try:
            url = f"{BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
            resp = requests.get(url, headers=_headers(), timeout=15)
            if resp.status_code != 200:
                continue

            data = resp.json()
            facts = data.get("facts", {}).get("us-gaap", {}).get(xbrl_tag, {})
            units = facts.get("units", {})

            # Try USD first, then shares
            values = units.get("USD", units.get("shares", units.get("USD/shares", [])))

            for entry in values:
                filing_end = entry.get("end", "")
                if filing_end > end_date:
                    continue
                form = entry.get("form", "")
                if period in ("quarterly", "q") and form not in ("10-Q",):
                    continue
                if period in ("annual", "a", "ttm") and form not in ("10-K",):
                    continue

                if filing_end not in period_data:
                    period_data[filing_end] = {}
                period_data[filing_end][item_name] = entry.get("val")
            break  # Only need to fetch company facts once (contains all tags)
        except Exception as e:
            print(f"[EDGAR] Error fetching {xbrl_tag} for {ticker}: {e}")

    # If we already fetched company facts, process remaining items
    if period_data or True:
        _rate_limit()
        try:
            url = f"{BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
            resp = requests.get(url, headers=_headers(), timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                us_gaap = data.get("facts", {}).get("us-gaap", {})

                for item_name in line_items:
                    normalized = item_name.lower().replace(" ", "_").replace("-", "_")
                    xbrl_tag = xbrl_map.get(normalized)
                    if not xbrl_tag or xbrl_tag not in us_gaap:
                        continue

                    facts = us_gaap[xbrl_tag]
                    units = facts.get("units", {})
                    values = units.get("USD", units.get("shares", units.get("USD/shares", [])))

                    for entry in values:
                        filing_end = entry.get("end", "")
                        if filing_end > end_date:
                            continue
                        form = entry.get("form", "")
                        if period in ("quarterly", "q") and form not in ("10-Q",):
                            continue
                        if period in ("annual", "a", "ttm") and form not in ("10-K",):
                            continue

                        if filing_end not in period_data:
                            period_data[filing_end] = {}
                        period_data[filing_end][item_name] = entry.get("val")
        except Exception:
            pass

    # Build results sorted by date, limited
    sorted_dates = sorted([d for d in period_data.keys()], reverse=True)[:limit]
    results = []
    for date_str in sorted_dates:
        extra = {item: period_data[date_str].get(item) for item in line_items}
        results.append(LineItem(
            ticker=ticker,
            report_period=date_str,
            period="quarterly" if period in ("quarterly", "q") else "annual",
            currency="USD",
            **extra,
        ))

    if results:
        cache_set(PROVIDER, "search_line_items", ticker, [li.model_dump() for li in results], TTL_30D, line_items=line_items, end_date=end_date, period=period, limit=limit)
    return results


def get_company_facts(ticker: str) -> CompanyFacts | None:
    """Fetch company facts from SEC EDGAR /submissions endpoint."""
    cik = _get_cik(ticker)
    if not cik:
        return None

    cached = cache_get(PROVIDER, "get_company_facts", ticker)
    if cached is not None:
        return CompanyFacts(**cached[0]) if cached else None

    _rate_limit()
    try:
        url = f"{BASE_URL}/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        facts = CompanyFacts(
            ticker=ticker,
            name=data.get("name", ticker),
            cik=cik,
            industry=data.get("sic", None),
            sector=None,
            category=data.get("category"),
            exchange=data.get("exchanges", [None])[0] if data.get("exchanges") else None,
            is_active=True,
            listing_date=None,
            location=f"{data.get('addresses', {}).get('business', {}).get('stateOrCountry', '')}",
            market_cap=None,
            number_of_employees=None,
            sec_filings_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=&dateb=&owner=include&count=40",
            sic_code=data.get("sic"),
            sic_industry=data.get("sicDescription"),
            sic_sector=None,
            website_url=None,
            weighted_average_shares=None,
        )
        cache_set(PROVIDER, "get_company_facts", ticker, [facts.model_dump()], TTL_30D)
        return facts
    except Exception as e:
        print(f"[EDGAR] Error fetching company facts for {ticker}: {e}")
        return None
