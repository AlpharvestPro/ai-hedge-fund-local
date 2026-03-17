"""yfinance provider — base data layer, no API key required."""

import datetime
import yfinance as yf

from src.data.models import (
    CompanyFacts,
    CompanyNews,
    FinancialMetrics,
    InsiderTrade,
    LineItem,
    Price,
)
from src.data.providers.cache import (
    TTL_4H,
    TTL_7D,
    TTL_24H,
    TTL_30D,
    TTL_INFINITE,
    cache_get,
    cache_set,
)

PROVIDER = "yfinance"


def get_prices(ticker: str, start_date: str, end_date: str) -> list[Price]:
    """Fetch OHLCV price data from yfinance."""
    cached = cache_get(PROVIDER, "get_prices", ticker, start_date=start_date, end_date=end_date)
    if cached is not None:
        return [Price(**p) for p in cached]

    try:
        df = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=False)
        if df.empty:
            return []

        # Handle multi-level columns from yfinance
        if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
            df.columns = df.columns.droplevel(1)

        prices = []
        for idx, row in df.iterrows():
            prices.append(Price(
                open=float(row["Open"]),
                close=float(row["Close"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                volume=int(row["Volume"]),
                time=idx.strftime("%Y-%m-%d"),
            ))

        # Historical prices are immutable; today's prices refresh in 4h
        today = datetime.date.today().isoformat()
        ttl = TTL_4H if end_date >= today else TTL_INFINITE
        cache_set(PROVIDER, "get_prices", ticker, [p.model_dump() for p in prices], ttl, start_date=start_date, end_date=end_date)
        return prices
    except Exception as e:
        print(f"[yfinance] Error fetching prices for {ticker}: {e}")
        return []


def _yf_safe_float(val) -> float | None:
    """Convert yfinance value to float, handling NaN."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if f != f else f  # NaN check
    except (ValueError, TypeError):
        return None


def _pct_growth(current, previous) -> float | None:
    """Calculate percentage growth between two values."""
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous)


def get_financial_metrics(ticker: str, end_date: str, period: str = "ttm", limit: int = 10) -> list[FinancialMetrics]:
    """Fetch financial metrics from yfinance .info + .financials/.cashflow.

    Returns multiple periods (up to `limit`) with computed growth fields,
    so agents that need historical series (e.g. growth_agent) get enough data.
    """
    cached = cache_get(PROVIDER, "get_financial_metrics", ticker, end_date=end_date, period=period, limit=limit)
    if cached is not None:
        return [FinancialMetrics(**m) for m in cached]

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        # Fetch annual financials + cashflow for multi-period data
        financials = t.financials  # annual income statement
        cashflow = t.cashflow      # annual cashflow

        # Build per-period metrics from historical data
        results = []
        if financials is not None and not financials.empty:
            dates = sorted(financials.columns, reverse=True)
            dates = [d for d in dates if d.strftime("%Y-%m-%d") <= end_date][:limit]

            for i, date_col in enumerate(dates):
                date_str = date_col.strftime("%Y-%m-%d")

                # Extract values for this period
                revenue = _yf_safe_float(financials.loc["Total Revenue", date_col]) if "Total Revenue" in financials.index else None
                net_income = _yf_safe_float(financials.loc["Net Income", date_col]) if "Net Income" in financials.index else None
                gross_profit = _yf_safe_float(financials.loc["Gross Profit", date_col]) if "Gross Profit" in financials.index else None
                operating_income = _yf_safe_float(financials.loc["Operating Income", date_col]) if "Operating Income" in financials.index else None
                diluted_eps = _yf_safe_float(financials.loc["Diluted EPS", date_col]) if "Diluted EPS" in financials.index else None
                basic_eps = _yf_safe_float(financials.loc["Basic EPS", date_col]) if "Basic EPS" in financials.index else None
                eps = diluted_eps or basic_eps

                fcf = None
                if cashflow is not None and not cashflow.empty and date_col in cashflow.columns:
                    fcf = _yf_safe_float(cashflow.loc["Free Cash Flow", date_col]) if "Free Cash Flow" in cashflow.index else None

                # Calculate growth vs previous period
                prev_col = dates[i + 1] if i + 1 < len(dates) else None
                rev_growth = None
                eps_growth = None
                fcf_growth = None
                oi_growth = None
                earnings_growth = None

                if prev_col is not None:
                    prev_revenue = _yf_safe_float(financials.loc["Total Revenue", prev_col]) if "Total Revenue" in financials.index else None
                    rev_growth = _pct_growth(revenue, prev_revenue)
                    prev_ni = _yf_safe_float(financials.loc["Net Income", prev_col]) if "Net Income" in financials.index else None
                    earnings_growth = _pct_growth(net_income, prev_ni)
                    prev_eps = _yf_safe_float(financials.loc["Diluted EPS", prev_col]) if "Diluted EPS" in financials.index else _yf_safe_float(financials.loc["Basic EPS", prev_col]) if "Basic EPS" in financials.index else None
                    eps_growth = _pct_growth(eps, prev_eps)
                    prev_oi = _yf_safe_float(financials.loc["Operating Income", prev_col]) if "Operating Income" in financials.index else None
                    oi_growth = _pct_growth(operating_income, prev_oi)
                    if cashflow is not None and not cashflow.empty and prev_col in cashflow.columns:
                        prev_fcf = _yf_safe_float(cashflow.loc["Free Cash Flow", prev_col]) if "Free Cash Flow" in cashflow.index else None
                        fcf_growth = _pct_growth(fcf, prev_fcf)

                # Margins
                gross_margin = _safe_div(gross_profit, revenue)
                operating_margin = _safe_div(operating_income, revenue)
                net_margin = _safe_div(net_income, revenue)

                # For the most recent period, enrich with .info data (live ratios)
                is_latest = (i == 0)

                m = FinancialMetrics(
                    ticker=ticker,
                    report_period=date_str,
                    period=period,
                    currency=info.get("currency", "USD"),
                    market_cap=info.get("marketCap") if is_latest else None,
                    enterprise_value=info.get("enterpriseValue") if is_latest else None,
                    price_to_earnings_ratio=info.get("trailingPE") if is_latest else None,
                    price_to_book_ratio=info.get("priceToBook") if is_latest else None,
                    price_to_sales_ratio=info.get("priceToSalesTrailing12Months") if is_latest else None,
                    enterprise_value_to_ebitda_ratio=info.get("enterpriseToEbitda") if is_latest else None,
                    enterprise_value_to_revenue_ratio=info.get("enterpriseToRevenue") if is_latest else None,
                    free_cash_flow_yield=_safe_div(info.get("freeCashflow"), info.get("marketCap")) if is_latest else None,
                    peg_ratio=info.get("pegRatio") if is_latest else None,
                    gross_margin=gross_margin if gross_margin is not None else (info.get("grossMargins") if is_latest else None),
                    operating_margin=operating_margin if operating_margin is not None else (info.get("operatingMargins") if is_latest else None),
                    net_margin=net_margin if net_margin is not None else (info.get("profitMargins") if is_latest else None),
                    return_on_equity=info.get("returnOnEquity") if is_latest else None,
                    return_on_assets=info.get("returnOnAssets") if is_latest else None,
                    return_on_invested_capital=None,
                    asset_turnover=None,
                    inventory_turnover=None,
                    receivables_turnover=None,
                    days_sales_outstanding=None,
                    operating_cycle=None,
                    working_capital_turnover=None,
                    current_ratio=info.get("currentRatio") if is_latest else None,
                    quick_ratio=info.get("quickRatio") if is_latest else None,
                    cash_ratio=None,
                    operating_cash_flow_ratio=None,
                    debt_to_equity=info.get("debtToEquity") if is_latest else None,
                    debt_to_assets=None,
                    interest_coverage=None,
                    revenue_growth=rev_growth if rev_growth is not None else (info.get("revenueGrowth") if is_latest else None),
                    earnings_growth=earnings_growth if earnings_growth is not None else (info.get("earningsGrowth") if is_latest else None),
                    book_value_growth=None,
                    earnings_per_share_growth=eps_growth,
                    free_cash_flow_growth=fcf_growth,
                    operating_income_growth=oi_growth,
                    ebitda_growth=None,
                    payout_ratio=info.get("payoutRatio") if is_latest else None,
                    earnings_per_share=eps,
                    book_value_per_share=info.get("bookValue") if is_latest else None,
                    free_cash_flow_per_share=None,
                )
                results.append(m)

        # Fallback: if no financials available, return single metric from .info
        if not results:
            results = [FinancialMetrics(
                ticker=ticker,
                report_period=end_date,
                period=period,
                currency=info.get("currency", "USD"),
                market_cap=info.get("marketCap"),
                enterprise_value=info.get("enterpriseValue"),
                price_to_earnings_ratio=info.get("trailingPE"),
                price_to_book_ratio=info.get("priceToBook"),
                price_to_sales_ratio=info.get("priceToSalesTrailing12Months"),
                enterprise_value_to_ebitda_ratio=info.get("enterpriseToEbitda"),
                enterprise_value_to_revenue_ratio=info.get("enterpriseToRevenue"),
                free_cash_flow_yield=_safe_div(info.get("freeCashflow"), info.get("marketCap")),
                peg_ratio=info.get("pegRatio"),
                gross_margin=info.get("grossMargins"),
                operating_margin=info.get("operatingMargins"),
                net_margin=info.get("profitMargins"),
                return_on_equity=info.get("returnOnEquity"),
                return_on_assets=info.get("returnOnAssets"),
                return_on_invested_capital=None, asset_turnover=None, inventory_turnover=None,
                receivables_turnover=None, days_sales_outstanding=None, operating_cycle=None,
                working_capital_turnover=None,
                current_ratio=info.get("currentRatio"),
                quick_ratio=info.get("quickRatio"),
                cash_ratio=None, operating_cash_flow_ratio=None,
                debt_to_equity=info.get("debtToEquity"),
                debt_to_assets=None, interest_coverage=None,
                revenue_growth=info.get("revenueGrowth"),
                earnings_growth=info.get("earningsGrowth"),
                book_value_growth=None, earnings_per_share_growth=None,
                free_cash_flow_growth=None, operating_income_growth=None, ebitda_growth=None,
                payout_ratio=info.get("payoutRatio"),
                earnings_per_share=info.get("trailingEps"),
                book_value_per_share=info.get("bookValue"),
                free_cash_flow_per_share=None,
            )]

        cache_set(PROVIDER, "get_financial_metrics", ticker, [m.model_dump() for m in results], TTL_7D, end_date=end_date, period=period, limit=limit)
        return results
    except Exception as e:
        print(f"[yfinance] Error fetching metrics for {ticker}: {e}")
        return []


def search_line_items(ticker: str, line_items: list[str], end_date: str, period: str = "ttm", limit: int = 10) -> list[LineItem]:
    """Fetch financial line items from yfinance .financials/.balance_sheet/.cashflow."""
    cached = cache_get(PROVIDER, "search_line_items", ticker, line_items=line_items, end_date=end_date, period=period, limit=limit)
    if cached is not None:
        return [LineItem(**li) for li in cached]

    try:
        t = yf.Ticker(ticker)
        freq = "quarterly" if period in ("quarterly", "q") else "annual"

        income = getattr(t, "quarterly_financials" if freq == "quarterly" else "financials", None)
        balance = getattr(t, "quarterly_balance_sheet" if freq == "quarterly" else "balance_sheet", None)
        cash = getattr(t, "quarterly_cashflow" if freq == "quarterly" else "cashflow", None)

        # Merge all statements into one lookup
        all_data = {}
        for stmt in [income, balance, cash]:
            if stmt is not None and not stmt.empty:
                for col in stmt.columns:
                    date_str = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
                    if date_str not in all_data:
                        all_data[date_str] = {}
                    for idx_name in stmt.index:
                        val = stmt.loc[idx_name, col]
                        if val is not None and str(val) != "nan":
                            all_data[date_str][_normalize_item_name(idx_name)] = float(val)

        # Filter by end_date and limit
        sorted_dates = sorted([d for d in all_data.keys() if d <= end_date], reverse=True)[:limit]

        results = []
        for date_str in sorted_dates:
            item_data = all_data[date_str]
            # Build LineItem with requested fields
            extra = {}
            for requested in line_items:
                normalized = _normalize_item_name(requested)
                # Try exact match, then fuzzy
                val = item_data.get(normalized)
                if val is None:
                    val = _fuzzy_match(normalized, item_data)
                if val is not None:
                    extra[requested] = val
                else:
                    extra[requested] = None

            results.append(LineItem(
                ticker=ticker,
                report_period=date_str,
                period="quarterly" if freq == "quarterly" else "annual",
                currency="USD",
                **extra,
            ))

        cache_set(PROVIDER, "search_line_items", ticker, [li.model_dump() for li in results], TTL_30D, line_items=line_items, end_date=end_date, period=period, limit=limit)
        return results
    except Exception as e:
        print(f"[yfinance] Error fetching line items for {ticker}: {e}")
        return []


def get_insider_trades(ticker: str, end_date: str, start_date: str = None, limit: int = 50) -> list[InsiderTrade]:
    """Fetch insider transactions from yfinance."""
    cached = cache_get(PROVIDER, "get_insider_trades", ticker, end_date=end_date, start_date=start_date, limit=limit)
    if cached is not None:
        return [InsiderTrade(**t) for t in cached]

    try:
        t = yf.Ticker(ticker)
        txns = t.insider_transactions
        if txns is None or txns.empty:
            return []

        trades = []
        for _, row in txns.iterrows():
            filing_date = str(row.get("Start Date", row.get("startDate", "")))
            if hasattr(row.get("Start Date"), "strftime"):
                filing_date = row["Start Date"].strftime("%Y-%m-%d")

            if end_date and filing_date > end_date:
                continue
            if start_date and filing_date < start_date:
                continue

            shares = row.get("Shares", row.get("shares", 0))
            value = row.get("Value", row.get("value", 0))

            trades.append(InsiderTrade(
                ticker=ticker,
                issuer=None,
                name=str(row.get("Insider", row.get("insider", ""))),
                title=str(row.get("Position", row.get("position", ""))),
                is_board_director=None,
                transaction_date=filing_date,
                transaction_shares=float(shares) if shares else None,
                transaction_price_per_share=float(value / shares) if shares and value else None,
                transaction_value=float(value) if value else None,
                shares_owned_before_transaction=None,
                shares_owned_after_transaction=None,
                security_title=str(row.get("Text", row.get("text", ""))),
                filing_date=filing_date,
            ))

        trades = trades[:limit]
        cache_set(PROVIDER, "get_insider_trades", ticker, [t.model_dump() for t in trades], TTL_24H, end_date=end_date, start_date=start_date, limit=limit)
        return trades
    except Exception as e:
        print(f"[yfinance] Error fetching insider trades for {ticker}: {e}")
        return []


def get_company_news(ticker: str, end_date: str, start_date: str = None, limit: int = 100) -> list[CompanyNews]:
    """Fetch news from yfinance."""
    cached = cache_get(PROVIDER, "get_company_news", ticker, end_date=end_date, start_date=start_date, limit=limit)
    if cached is not None:
        return [CompanyNews(**n) for n in cached]

    try:
        t = yf.Ticker(ticker)
        raw_news = t.news or []

        articles = []
        for item in raw_news[:limit]:
            pub_date = ""
            if "providerPublishTime" in item:
                pub_date = datetime.datetime.fromtimestamp(item["providerPublishTime"]).strftime("%Y-%m-%dT%H:%M:%S")
            elif "publishedAt" in item:
                pub_date = str(item["publishedAt"])

            date_only = pub_date[:10] if pub_date else ""
            if end_date and date_only and date_only > end_date:
                continue
            if start_date and date_only and date_only < start_date:
                continue

            articles.append(CompanyNews(
                ticker=ticker,
                title=item.get("title", ""),
                author=item.get("publisher", ""),
                source=item.get("publisher", ""),
                date=pub_date,
                url=item.get("link", ""),
                sentiment=None,
            ))

        cache_set(PROVIDER, "get_company_news", ticker, [a.model_dump() for a in articles], TTL_4H, end_date=end_date, start_date=start_date, limit=limit)
        return articles
    except Exception as e:
        print(f"[yfinance] Error fetching news for {ticker}: {e}")
        return []


def get_market_cap(ticker: str, end_date: str) -> float | None:
    """Fetch market cap from yfinance."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return info.get("marketCap")
    except Exception:
        return None


def get_company_facts(ticker: str) -> CompanyFacts | None:
    """Fetch company facts from yfinance .info."""
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return CompanyFacts(
            ticker=ticker,
            name=info.get("longName", info.get("shortName", ticker)),
            cik=None,
            industry=info.get("industry"),
            sector=info.get("sector"),
            category=None,
            exchange=info.get("exchange"),
            is_active=True,
            listing_date=None,
            location=info.get("city"),
            market_cap=info.get("marketCap"),
            number_of_employees=info.get("fullTimeEmployees"),
            sec_filings_url=None,
            sic_code=None,
            sic_industry=None,
            sic_sector=None,
            website_url=info.get("website"),
            weighted_average_shares=info.get("sharesOutstanding"),
        )
    except Exception:
        return None


# --- Helpers ---

def _safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _normalize_item_name(name: str) -> str:
    """Normalize financial item names for matching."""
    return name.lower().replace(" ", "_").replace("-", "_")


def _fuzzy_match(key: str, data: dict) -> float | None:
    """Try to find a fuzzy match in data dict keys."""
    # Common aliases
    aliases = {
        "revenue": ["total_revenue", "totalrevenue", "total_sales"],
        "net_income": ["netincome", "net_income_common_stockholders"],
        "operating_income": ["operatingincome", "ebit"],
        "gross_profit": ["grossprofit"],
        "total_assets": ["totalassets"],
        "total_liabilities": ["totalliabilities", "total_liab"],
        "total_debt": ["totaldebt", "long_term_debt"],
        "free_cash_flow": ["freecashflow", "free_cash_flow"],
        "operating_cash_flow": ["operatingcashflow", "total_cash_from_operating_activities"],
        "capital_expenditure": ["capitalexpenditure", "capex", "capital_expenditures"],
        "research_and_development": ["researchdevelopment", "research_development", "r_and_d"],
        "dividends_and_other_cash_distributions": ["dividendspaid", "cash_dividends_paid"],
        "outstanding_shares": ["sharesoutstanding", "shares_outstanding", "ordinary_shares_number"],
        "depreciation_and_amortization": ["depreciationandamortization", "depreciation_amortization_depletion"],
        "earnings_per_share": ["basiceps", "basic_eps"],
        "gross_margin": ["grossmargin"],
        "operating_margin": ["operatingmargin"],
        "operating_expense": ["operatingexpense", "total_operating_expenses"],
        "debt_to_equity": ["debttoequity"],
        "book_value_per_share": ["bookvalue"],
        "return_on_equity": ["returnonequity"],
        "return_on_assets": ["returnonassets"],
        "current_ratio": ["currentratio"],
    }
    for canonical, alts in aliases.items():
        if key == canonical or key in alts:
            for check in [canonical] + alts:
                if check in data:
                    return data[check]
    # Substring match as last resort
    for data_key, val in data.items():
        if key in data_key or data_key in key:
            return val
    return None
