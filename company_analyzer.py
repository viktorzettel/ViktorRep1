#!/usr/bin/env python3
"""
Company Analyzer - Free Financial Data Gatherer for Investment Analysis
========================================================================
Two-tier analysis system using yfinance (Yahoo Finance) - 100% FREE

Usage:
    python company_analyzer.py AAPL              # Level 1 (Base)
    python company_analyzer.py AAPL --extensive  # Level 2 (Extensive)
    python company_analyzer.py AAPL -e           # Level 2 (short flag)
    python company_analyzer.py --test            # Run connectivity test

Requirements:
    pip install yfinance
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Optional

try:
    import yfinance as yf
except ImportError:
    print("❌ yfinance not installed. Run: pip install yfinance")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# FORMATTING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def fmt_num(value, prefix: str = "", suffix: str = "", decimals: int = 2) -> str:
    """Format number with prefix/suffix and human-readable scaling."""
    if value is None:
        return "N/A"
    try:
        val = float(value)
        if abs(val) >= 1e12:
            return f"{prefix}{val/1e12:.{decimals}f}T{suffix}"
        elif abs(val) >= 1e9:
            return f"{prefix}{val/1e9:.{decimals}f}B{suffix}"
        elif abs(val) >= 1e6:
            return f"{prefix}{val/1e6:.{decimals}f}M{suffix}"
        elif abs(val) >= 1e3:
            return f"{prefix}{val/1e3:.{decimals}f}K{suffix}"
        else:
            return f"{prefix}{val:.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_pct(value) -> str:
    """Format as percentage."""
    if value is None:
        return "N/A"
    try:
        val = float(value)
        # If already in percentage form (e.g., 25.5 for 25.5%)
        if abs(val) > 1:
            return f"{val:.1f}%"
        # If in decimal form (e.g., 0.255 for 25.5%)
        return f"{val * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def fmt_ratio(value) -> str:
    """Format ratio."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "N/A"


def color(text: str, code: str) -> str:
    """Apply ANSI color code."""
    colors = {
        "green": "\033[92m",
        "red": "\033[91m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "cyan": "\033[96m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }
    return f"{colors.get(code, '')}{text}{colors['reset']}"


def safe_get(data: dict, *keys, default=None):
    """Safely get nested dictionary values."""
    for key in keys:
        if isinstance(data, dict):
            data = data.get(key, default)
        else:
            return default
    return data if data is not None else default


# ══════════════════════════════════════════════════════════════════════════════
# COMPANY ANALYZER
# ══════════════════════════════════════════════════════════════════════════════

class CompanyAnalyzer:
    """Main analyzer class using yfinance for free data."""

    def __init__(self, ticker: str):
        self.ticker_symbol = ticker.upper()
        self.ticker = yf.Ticker(self.ticker_symbol)
        self.data = {}
        self.info = {}

    def fetch_level1(self) -> dict:
        """
        Level 1 (Base Analysis) - Essential data for investment decisions.
        Includes: Prices, Valuation, Profitability, Financials, Analyst Estimates, Insider Activity
        """
        print(f"\n🔍 Fetching Level 1 (Base) data for {color(self.ticker_symbol, 'bold')}...")

        try:
            # Core company info (contains most metrics)
            print("  ├─ Company info & metrics...")
            self.info = self.ticker.info or {}
            self.data["info"] = self.info

            # Historical prices (1 year)
            print("  ├─ Price history (1 year)...")
            self.data["history"] = self.ticker.history(period="1y")

            # Income statement (quarterly - last 4)
            print("  ├─ Income statements...")
            self.data["income_quarterly"] = self.ticker.quarterly_financials
            self.data["income_annual"] = self.ticker.financials

            # Analyst recommendations
            print("  ├─ Analyst recommendations...")
            self.data["recommendations"] = self.ticker.recommendations
            self.data["price_targets"] = self.ticker.analyst_price_targets

            # Insider transactions
            print("  ├─ Insider transactions...")
            self.data["insider"] = self.ticker.insider_transactions

            # Earnings estimates
            print("  └─ Earnings estimates...")
            self.data["earnings_estimate"] = self.ticker.earnings_estimate
            self.data["revenue_estimate"] = self.ticker.revenue_estimate

        except Exception as e:
            print(f"  ⚠️  Error fetching data: {e}")

        return self.data

    def fetch_level2(self) -> dict:
        """
        Level 2 (Extensive Analysis) - Deep dive for due diligence.
        Includes everything from Level 1 plus: Full financials, Cash flow, Balance sheet,
        Institutional holders, Major holders, News, Earnings calendar
        """
        # First get all Level 1 data
        self.fetch_level1()

        print(f"🔬 Fetching Level 2 (Extensive) additional data...")

        try:
            # Balance sheet
            print("  ├─ Balance sheets...")
            self.data["balance_quarterly"] = self.ticker.quarterly_balance_sheet
            self.data["balance_annual"] = self.ticker.balance_sheet

            # Cash flow
            print("  ├─ Cash flow statements...")
            self.data["cashflow_quarterly"] = self.ticker.quarterly_cashflow
            self.data["cashflow_annual"] = self.ticker.cashflow

            # Holders
            print("  ├─ Institutional & major holders...")
            self.data["institutional_holders"] = self.ticker.institutional_holders
            self.data["major_holders"] = self.ticker.major_holders
            self.data["mutualfund_holders"] = self.ticker.mutualfund_holders

            # News
            print("  ├─ Recent news...")
            self.data["news"] = self.ticker.news

            # Earnings dates
            print("  ├─ Earnings calendar...")
            self.data["earnings_dates"] = self.ticker.earnings_dates

            # Actions (dividends, splits)
            print("  └─ Corporate actions...")
            self.data["actions"] = self.ticker.actions
            self.data["dividends"] = self.ticker.dividends
            self.data["splits"] = self.ticker.splits

        except Exception as e:
            print(f"  ⚠️  Error fetching extended data: {e}")

        return self.data

    def generate_report(self, level: int = 1) -> str:
        """Generate formatted analysis report."""
        lines = []
        sep = "═" * 65
        info = self.info

        # ══════════════════════════════════════════════════════════════════════
        # HEADER
        # ══════════════════════════════════════════════════════════════════════
        company_name = info.get("longName", info.get("shortName", self.ticker_symbol))
        lines.append(f"\n{sep}")
        lines.append(f"📊 {color('COMPANY ANALYSIS', 'bold')}: {color(company_name, 'cyan')}")
        lines.append(f"   Ticker: {self.ticker_symbol} | Sector: {info.get('sector', 'N/A')} | Industry: {info.get('industry', 'N/A')}")
        lines.append(f"   Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Level: {'Base (L1)' if level == 1 else 'Extensive (L2)'}")
        lines.append(sep)

        # ──────────────────────────────────────────────────────────────────────
        # PRICE & PERFORMANCE
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('💰 PRICE & PERFORMANCE', 'bold')}")
        
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        prev_close = info.get("previousClose")
        high_52w = info.get("fiftyTwoWeekHigh")
        low_52w = info.get("fiftyTwoWeekLow")
        market_cap = info.get("marketCap")
        
        # Calculate YTD from history
        history = self.data.get("history")
        ytd_return = None
        if history is not None and len(history) > 0:
            try:
                start_price = history["Close"].iloc[0]
                end_price = history["Close"].iloc[-1]
                ytd_return = (end_price - start_price) / start_price
            except Exception:
                pass

        lines.append(f"├─ Current Price:   {color(fmt_num(current_price, '$'), 'cyan')}")
        lines.append(f"├─ Previous Close:  {fmt_num(prev_close, '$')}")
        lines.append(f"├─ 52-Week High:    {fmt_num(high_52w, '$')}")
        lines.append(f"├─ 52-Week Low:     {fmt_num(low_52w, '$')}")
        lines.append(f"├─ Market Cap:      {fmt_num(market_cap, '$')}")
        
        ytd_color = "green" if ytd_return and ytd_return > 0 else "red"
        lines.append(f"└─ 1-Year Return:   {color(fmt_pct(ytd_return), ytd_color)}")

        # ──────────────────────────────────────────────────────────────────────
        # VALUATION
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('📈 VALUATION', 'bold')}")
        
        pe_trailing = info.get("trailingPE")
        pe_forward = info.get("forwardPE")
        pb = info.get("priceToBook")
        ps = info.get("priceToSalesTrailing12Months")
        peg = info.get("pegRatio")
        ev_ebitda = info.get("enterpriseToEbitda")
        ev_revenue = info.get("enterpriseToRevenue")

        lines.append(f"├─ P/E (Trailing):  {fmt_ratio(pe_trailing)}")
        lines.append(f"├─ P/E (Forward):   {fmt_ratio(pe_forward)}")
        lines.append(f"├─ P/B Ratio:       {fmt_ratio(pb)}")
        lines.append(f"├─ P/S Ratio:       {fmt_ratio(ps)}")
        lines.append(f"├─ PEG Ratio:       {fmt_ratio(peg)}")
        lines.append(f"├─ EV/EBITDA:       {fmt_ratio(ev_ebitda)}")
        lines.append(f"└─ EV/Revenue:      {fmt_ratio(ev_revenue)}")

        # ──────────────────────────────────────────────────────────────────────
        # PROFITABILITY
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('💪 PROFITABILITY', 'bold')}")
        
        gross_margin = info.get("grossMargins")
        operating_margin = info.get("operatingMargins")
        profit_margin = info.get("profitMargins")
        roe = info.get("returnOnEquity")
        roa = info.get("returnOnAssets")

        lines.append(f"├─ Gross Margin:      {fmt_pct(gross_margin)}")
        lines.append(f"├─ Operating Margin:  {fmt_pct(operating_margin)}")
        lines.append(f"├─ Profit Margin:     {fmt_pct(profit_margin)}")
        lines.append(f"├─ ROE:               {fmt_pct(roe)}")
        lines.append(f"└─ ROA:               {fmt_pct(roa)}")

        # ──────────────────────────────────────────────────────────────────────
        # FINANCIAL HEALTH
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('🏦 FINANCIAL HEALTH', 'bold')}")
        
        total_debt = info.get("totalDebt")
        total_cash = info.get("totalCash")
        debt_to_equity = info.get("debtToEquity")
        current_ratio = info.get("currentRatio")
        quick_ratio = info.get("quickRatio")
        free_cashflow = info.get("freeCashflow")

        lines.append(f"├─ Total Cash:      {fmt_num(total_cash, '$')}")
        lines.append(f"├─ Total Debt:      {fmt_num(total_debt, '$')}")
        lines.append(f"├─ Debt/Equity:     {fmt_ratio(debt_to_equity)}")
        lines.append(f"├─ Current Ratio:   {fmt_ratio(current_ratio)}")
        lines.append(f"├─ Quick Ratio:     {fmt_ratio(quick_ratio)}")
        lines.append(f"└─ Free Cash Flow:  {fmt_num(free_cashflow, '$')}")

        # ──────────────────────────────────────────────────────────────────────
        # GROWTH
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('📊 GROWTH', 'bold')}")
        
        revenue_growth = info.get("revenueGrowth")
        earnings_growth = info.get("earningsGrowth")
        earnings_quarterly_growth = info.get("earningsQuarterlyGrowth")

        lines.append(f"├─ Revenue Growth (YoY):    {fmt_pct(revenue_growth)}")
        lines.append(f"├─ Earnings Growth (YoY):   {fmt_pct(earnings_growth)}")
        lines.append(f"└─ Earnings Growth (QoQ):   {fmt_pct(earnings_quarterly_growth)}")

        # ──────────────────────────────────────────────────────────────────────
        # ANALYST ESTIMATES & TARGETS
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('🎯 ANALYST ESTIMATES & TARGETS', 'bold')}")
        
        target_high = info.get("targetHighPrice")
        target_low = info.get("targetLowPrice")
        target_mean = info.get("targetMeanPrice")
        target_median = info.get("targetMedianPrice")
        recommendation = info.get("recommendationKey", "N/A").upper()
        num_analysts = info.get("numberOfAnalystOpinions")

        rec_color = "green" if recommendation in ["BUY", "STRONG_BUY"] else "red" if recommendation in ["SELL", "STRONG_SELL"] else "yellow"
        lines.append(f"├─ Recommendation:    {color(recommendation, rec_color)} ({num_analysts} analysts)")
        lines.append(f"├─ Target Mean:       {fmt_num(target_mean, '$')}")
        lines.append(f"├─ Target Median:     {fmt_num(target_median, '$')}")
        lines.append(f"├─ Target High:       {fmt_num(target_high, '$')}")
        lines.append(f"└─ Target Low:        {fmt_num(target_low, '$')}")

        # Earnings estimates
        earnings_est = self.data.get("earnings_estimate")
        if earnings_est is not None and not earnings_est.empty:
            lines.append(f"\n    {color('EPS Estimates:', 'bold')}")
            try:
                for col in earnings_est.columns[:4]:
                    avg = earnings_est.loc["avg", col] if "avg" in earnings_est.index else None
                    lines.append(f"    ├─ {col}: {fmt_num(avg, '$')}")
            except Exception:
                pass

        # ──────────────────────────────────────────────────────────────────────
        # INSIDER ACTIVITY
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('👔 INSIDER ACTIVITY', 'bold')}")
        
        insider_df = self.data.get("insider")
        if insider_df is not None and not insider_df.empty:
            # Count buys vs sells
            try:
                recent = insider_df.head(20)
                buys = len(recent[recent["Shares"].apply(lambda x: "+" in str(x) if x else False)]) if "Shares" in recent.columns else 0
                sells = len(recent) - buys
                
                lines.append(f"├─ Recent Transactions: {len(recent)}")
                lines.append(f"├─ Notable Trades:")
                
                for idx, row in recent.head(5).iterrows():
                    insider_name = str(row.get("Insider", "Unknown"))[:25]
                    shares = row.get("Shares", "N/A")
                    value = row.get("Value", 0)
                    date = str(row.get("Start Date", ""))[:10]
                    trans_type = "BUY" if "+" in str(shares) else "SELL"
                    trans_color = "green" if trans_type == "BUY" else "red"
                    lines.append(f"│  └─ {date}: {insider_name} - {color(trans_type, trans_color)} {fmt_num(value, '$')}")
            except Exception as e:
                lines.append(f"├─ Could not parse insider data: {e}")
        else:
            lines.append("└─ No recent insider transactions")

        # ──────────────────────────────────────────────────────────────────────
        # RECENT QUARTERLY INCOME
        # ──────────────────────────────────────────────────────────────────────
        lines.append(f"\n{color('📋 RECENT QUARTERLY INCOME', 'bold')}")
        
        income_q = self.data.get("income_quarterly")
        if income_q is not None and not income_q.empty:
            lines.append(f"{'Quarter':<12} {'Revenue':>14} {'Net Income':>14} {'EPS':>10}")
            lines.append("─" * 52)
            
            for col in income_q.columns[:4]:
                period = str(col.date()) if hasattr(col, 'date') else str(col)[:10]
                revenue = income_q.loc["Total Revenue", col] if "Total Revenue" in income_q.index else None
                net_income = income_q.loc["Net Income", col] if "Net Income" in income_q.index else None
                # EPS from info
                eps = info.get("trailingEps")
                lines.append(f"{period:<12} {fmt_num(revenue, '$'):>14} {fmt_num(net_income, '$'):>14} {fmt_num(eps, '$') if col == income_q.columns[0] else 'N/A':>10}")
        else:
            lines.append("└─ No quarterly income data available")

        # ══════════════════════════════════════════════════════════════════════
        # LEVEL 2 EXTENSIVE ADDITIONS
        # ══════════════════════════════════════════════════════════════════════
        if level == 2:
            lines.append(f"\n{sep}")
            lines.append(f"{color('🔬 EXTENSIVE ANALYSIS (Level 2)', 'bold')}")
            lines.append(sep)

            # ──────────────────────────────────────────────────────────────────
            # ANNUAL INCOME TREND
            # ──────────────────────────────────────────────────────────────────
            lines.append(f"\n{color('📈 ANNUAL INCOME TREND', 'bold')}")
            
            income_a = self.data.get("income_annual")
            if income_a is not None and not income_a.empty:
                lines.append(f"{'Year':<12} {'Revenue':>14} {'Net Income':>14} {'Gross Profit':>14}")
                lines.append("─" * 56)
                
                for col in income_a.columns[:5]:
                    period = str(col.year) if hasattr(col, 'year') else str(col)[:4]
                    revenue = income_a.loc["Total Revenue", col] if "Total Revenue" in income_a.index else None
                    net_income = income_a.loc["Net Income", col] if "Net Income" in income_a.index else None
                    gross = income_a.loc["Gross Profit", col] if "Gross Profit" in income_a.index else None
                    lines.append(f"{period:<12} {fmt_num(revenue, '$'):>14} {fmt_num(net_income, '$'):>14} {fmt_num(gross, '$'):>14}")
            else:
                lines.append("└─ No annual income data")

            # ──────────────────────────────────────────────────────────────────
            # BALANCE SHEET SUMMARY
            # ──────────────────────────────────────────────────────────────────
            lines.append(f"\n{color('🏛️ BALANCE SHEET SUMMARY', 'bold')}")
            
            balance = self.data.get("balance_annual")
            if balance is not None and not balance.empty:
                latest = balance.columns[0]
                
                total_assets = balance.loc["Total Assets", latest] if "Total Assets" in balance.index else None
                total_liab = balance.loc["Total Liabilities Net Minority Interest", latest] if "Total Liabilities Net Minority Interest" in balance.index else None
                equity = balance.loc["Stockholders Equity", latest] if "Stockholders Equity" in balance.index else None
                cash = balance.loc["Cash And Cash Equivalents", latest] if "Cash And Cash Equivalents" in balance.index else None
                
                lines.append(f"├─ Total Assets:        {fmt_num(total_assets, '$')}")
                lines.append(f"├─ Total Liabilities:   {fmt_num(total_liab, '$')}")
                lines.append(f"├─ Stockholders Equity: {fmt_num(equity, '$')}")
                lines.append(f"└─ Cash & Equivalents:  {fmt_num(cash, '$')}")
            else:
                lines.append("└─ No balance sheet data")

            # ──────────────────────────────────────────────────────────────────
            # CASH FLOW SUMMARY
            # ──────────────────────────────────────────────────────────────────
            lines.append(f"\n{color('💵 CASH FLOW SUMMARY', 'bold')}")
            
            cashflow = self.data.get("cashflow_annual")
            if cashflow is not None and not cashflow.empty:
                latest = cashflow.columns[0]
                
                operating = cashflow.loc["Operating Cash Flow", latest] if "Operating Cash Flow" in cashflow.index else None
                investing = cashflow.loc["Investing Cash Flow", latest] if "Investing Cash Flow" in cashflow.index else None
                financing = cashflow.loc["Financing Cash Flow", latest] if "Financing Cash Flow" in cashflow.index else None
                capex = cashflow.loc["Capital Expenditure", latest] if "Capital Expenditure" in cashflow.index else None
                fcf = cashflow.loc["Free Cash Flow", latest] if "Free Cash Flow" in cashflow.index else None
                
                lines.append(f"├─ Operating Cash Flow: {fmt_num(operating, '$')}")
                lines.append(f"├─ Investing Cash Flow: {fmt_num(investing, '$')}")
                lines.append(f"├─ Financing Cash Flow: {fmt_num(financing, '$')}")
                lines.append(f"├─ Capital Expenditure: {fmt_num(capex, '$')}")
                lines.append(f"└─ Free Cash Flow:      {fmt_num(fcf, '$')}")
            else:
                lines.append("└─ No cash flow data")

            # ──────────────────────────────────────────────────────────────────
            # MAJOR HOLDERS
            # ──────────────────────────────────────────────────────────────────
            lines.append(f"\n{color('🏢 MAJOR HOLDERS', 'bold')}")
            
            major = self.data.get("major_holders")
            if major is not None and not major.empty:
                try:
                    # Handle different DataFrame structures
                    for idx, row in major.iterrows():
                        if len(row) >= 2:
                            lines.append(f"├─ {row.iloc[1]}: {row.iloc[0]}")
                        elif len(row) == 1:
                            lines.append(f"├─ {idx}: {row.iloc[0]}")
                        else:
                            lines.append(f"├─ {row}")
                except Exception as e:
                    lines.append(f"└─ Could not parse holders data: {e}")
            else:
                lines.append("└─ No major holders data")

            # Institutional holders
            inst = self.data.get("institutional_holders")
            if inst is not None and not inst.empty:
                lines.append(f"\n    {color('Top Institutional Holders:', 'bold')}")
                try:
                    for idx, row in inst.head(5).iterrows():
                        holder = str(row.get("Holder", row.get("holder", "Unknown")))[:30]
                        shares = row.get("Shares", row.get("shares", 0))
                        pct = row.get("pctHeld", row.get("% Out", row.get("pct", 0)))
                        lines.append(f"    ├─ {holder}: {fmt_num(shares)} shares ({fmt_pct(pct)})")
                except Exception:
                    pass

            # ──────────────────────────────────────────────────────────────────
            # RECENT NEWS
            # ──────────────────────────────────────────────────────────────────
            lines.append(f"\n{color('📰 RECENT NEWS', 'bold')}")
            
            news = self.data.get("news")
            if news:
                for article in news[:8]:
                    title = article.get("title", "No title")[:65]
                    publisher = article.get("publisher", "Unknown")
                    lines.append(f"├─ {title}...")
                    lines.append(f"│    Source: {publisher}")
            else:
                lines.append("└─ No recent news")

            # ──────────────────────────────────────────────────────────────────
            # DIVIDENDS & SPLITS
            # ──────────────────────────────────────────────────────────────────
            lines.append(f"\n{color('💎 DIVIDENDS & SPLITS', 'bold')}")
            
            div_yield = info.get("dividendYield")
            div_rate = info.get("dividendRate")
            payout_ratio = info.get("payoutRatio")
            ex_div_date = info.get("exDividendDate")
            
            lines.append(f"├─ Dividend Yield:  {fmt_pct(div_yield)}")
            lines.append(f"├─ Dividend Rate:   {fmt_num(div_rate, '$')}")
            lines.append(f"├─ Payout Ratio:    {fmt_pct(payout_ratio)}")
            
            # Recent splits
            splits = self.data.get("splits")
            if splits is not None and len(splits) > 0:
                lines.append(f"├─ Recent Stock Splits:")
                for date, ratio in splits.tail(3).items():
                    lines.append(f"│  └─ {str(date)[:10]}: {ratio}:1")
            else:
                lines.append("└─ No recent stock splits")

            # ──────────────────────────────────────────────────────────────────
            # BUSINESS SUMMARY
            # ──────────────────────────────────────────────────────────────────
            lines.append(f"\n{color('📝 BUSINESS SUMMARY', 'bold')}")
            summary = info.get("longBusinessSummary", "No summary available.")
            # Truncate to 500 chars
            if len(summary) > 500:
                summary = summary[:500] + "..."
            lines.append(summary)

        # ══════════════════════════════════════════════════════════════════════
        # FOOTER
        # ══════════════════════════════════════════════════════════════════════
        lines.append(f"\n{sep}")
        lines.append(f"✅ Analysis complete. Data source: Yahoo Finance (yfinance)")
        lines.append(f"   💡 This data is FREE with no API limits!")
        lines.append(sep)

        return "\n".join(lines)

    def export_json(self, filepath: str = None) -> str:
        """Export data to JSON (converts DataFrames to dict)."""
        if not filepath:
            filepath = f"{self.ticker_symbol}_analysis.json"
        
        export_data = {}
        for key, value in self.data.items():
            try:
                if hasattr(value, 'to_dict'):
                    export_data[key] = value.to_dict()
                elif isinstance(value, dict):
                    export_data[key] = value
                else:
                    export_data[key] = str(value)
            except Exception:
                export_data[key] = str(value)
        
        with open(filepath, "w") as f:
            json.dump(export_data, f, indent=2, default=str)
        
        return filepath


# ══════════════════════════════════════════════════════════════════════════════
# TESTING
# ══════════════════════════════════════════════════════════════════════════════

def run_tests():
    """Run connectivity tests."""
    print("\n" + "=" * 60)
    print("🧪 RUNNING YFINANCE CONNECTIVITY TESTS")
    print("=" * 60)

    test_ticker = "AAPL"
    tests_passed = 0
    tests_total = 0

    tests = [
        ("Company Info", lambda t: t.info),
        ("Price History", lambda t: t.history(period="5d")),
        ("Financials", lambda t: t.financials),
        ("Balance Sheet", lambda t: t.balance_sheet),
        ("Cash Flow", lambda t: t.cashflow),
        ("Recommendations", lambda t: t.recommendations),
        ("Insider Transactions", lambda t: t.insider_transactions),
        ("News", lambda t: t.news),
    ]

    ticker = yf.Ticker(test_ticker)
    
    for name, func in tests:
        tests_total += 1
        print(f"\n  Testing {name}...", end=" ")
        try:
            result = func(ticker)
            if result is not None and (not hasattr(result, 'empty') or not result.empty):
                print(color("✓ PASS", "green"))
                tests_passed += 1
            else:
                print(color("✗ EMPTY", "yellow"))
        except Exception as e:
            print(color(f"✗ FAIL ({e})", "red"))

    print(f"\n{'=' * 60}")
    print(f"Tests passed: {tests_passed}/{tests_total}")
    print(f"Note: Some empty results are normal (e.g., no recent splits)")
    print("=" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Company Analyzer - FREE Financial Data via Yahoo Finance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python company_analyzer.py AAPL              # Base analysis (Level 1)
  python company_analyzer.py NVDA --extensive  # Full analysis (Level 2)
  python company_analyzer.py MSFT -e --json    # Level 2 + export JSON
  python company_analyzer.py --test            # Test connectivity
        """
    )
    
    parser.add_argument("ticker", nargs="?", help="Stock ticker symbol (e.g., AAPL, NVDA)")
    parser.add_argument("-e", "--extensive", action="store_true", help="Run extensive Level 2 analysis")
    parser.add_argument("--json", action="store_true", help="Export data to JSON file")
    parser.add_argument("--test", action="store_true", help="Run connectivity tests")

    args = parser.parse_args()

    # Run tests if requested
    if args.test:
        run_tests()
        return

    # Require ticker for analysis
    if not args.ticker:
        parser.print_help()
        sys.exit(1)

    # Initialize analyzer
    analyzer = CompanyAnalyzer(args.ticker)

    # Determine level and fetch data
    if args.extensive:
        analyzer.fetch_level2()
        report = analyzer.generate_report(level=2)
    else:
        analyzer.fetch_level1()
        report = analyzer.generate_report(level=1)

    # Print report
    print(report)

    # Export JSON if requested
    if args.json:
        filepath = analyzer.export_json()
        print(f"\n📁 Data exported to: {filepath}")


if __name__ == "__main__":
    main()
