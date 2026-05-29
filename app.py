"""
Interactive stock screener site.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import yfinance as yf

import screener


ROOT = Path(__file__).parent
DB_FILE = ROOT / "screener_data.db"
CACHE_FILE = ROOT / "_fetch_cache_v5_edgar.parquet"
SCREENER_FILE = ROOT / "screener.py"

DISPLAY_COLUMNS = [
    "symbol", "shortName", "sector", "industry", "currentPrice", "marketCap",
    "overall_score", "projection_score", "growth_score", "quality_score",
    "valuation_score", "balance_score", "momentum_score",
    "data_quality", "quick_read",
    "revenueGrowth", "rev_cagr_3y", "earningsGrowth",
    "grossMargins", "profitMargins", "fcf_margin", "fcf_conversion",
    "roic", "roic_wacc_spread", "op_margin_latest", "op_margin_trend",
    "accrual_ratio", "debtToEquity", "net_debt_to_fcf", "interest_coverage",
    "forwardPE", "ev_to_fcf", "ev_to_ebitda", "shortPercentOfFloat",
    "heldPercentInsiders", "pct_below_52w_high", "pct_above_52w_low",
    "sec_backlog_to_rev", "longBusinessSummary",
]

SECTOR_CONFIG = {
    "Technology": {
        "metrics": ["revenue_growth_min", "gross_margin_min", "rule_of_40_min", "sbc_pct_revenue_max"],
        "primary_sort": "revenueGrowth",
    },
    "Financial Services": {
        "metrics": ["return_on_equity_min", "forward_pe_max", "accrual_ratio_max"],
        "primary_sort": "returnOnEquity",
    },
    "Healthcare": {
        "metrics": ["revenue_growth_min", "ev_to_ebitda_max", "fcf_margin_min"],
        "primary_sort": "fcf_margin",
    },
    "Consumer Cyclical": {
        "metrics": ["op_margin_min", "forward_pe_max", "rev_cagr_3y_min"],
        "primary_sort": "rev_cagr_3y",
    },
    "Consumer Defensive": {
        "metrics": ["profit_margin_min", "roic_min", "forward_pe_max"],
        "primary_sort": "roic",
    },
    "Industrials": {
        "metrics": ["roic_min", "sec_backlog_to_rev_min", "ev_to_ebitda_max", "op_margin_trend_min"],
        "primary_sort": "roic",
    },
    "Energy": {
        "metrics": ["ev_to_ebitda_max", "fcf_margin_min", "debt_to_equity_max", "net_debt_to_fcf_max"],
        "primary_sort": "fcf_margin",
    },
    "Real Estate": {
        "metrics": ["forward_pe_max", "debt_to_equity_max", "interest_coverage_min"],
        "primary_sort": "forwardPE",
    },
    "Basic Materials": {
        "metrics": ["ev_to_ebitda_max", "gross_margin_min", "debt_to_equity_max"],
        "primary_sort": "grossMargins",
    },
    "Communication Services": {
        "metrics": ["ev_to_fcf_max", "fcf_margin_min", "revenue_growth_min"],
        "primary_sort": "ev_to_fcf",
    },
    "Utilities": {
        "metrics": ["debt_to_equity_max", "forward_pe_max", "interest_coverage_min"],
        "primary_sort": "interest_coverage",
    },
}

# How each filter key maps to the underlying display column in the dataframe
METRIC_TO_COLUMN = {
    "revenue_growth_min": "revenueGrowth",
    "rev_cagr_3y_min": "rev_cagr_3y",
    "earnings_growth_min": "earningsGrowth",
    "rule_of_40_min": "rule_of_40",
    "gross_margin_min": "grossMargins",
    "profit_margin_min": "profitMargins",
    "op_margin_min": "op_margin_latest",
    "op_margin_trend_min": "op_margin_trend",
    "fcf_margin_min": "fcf_margin",
    "fcf_conversion_min": "fcf_conversion",
    "roic_min": "roic",
    "roic_wacc_spread_min": "roic_wacc_spread",
    "return_on_equity_min": "returnOnEquity",
    "forward_pe_max": "forwardPE",
    "ev_to_fcf_max": "ev_to_fcf",
    "ev_to_ebitda_max": "ev_to_ebitda",
    "debt_to_equity_max": "debtToEquity",
    "interest_coverage_min": "interest_coverage",
    "net_debt_to_fcf_max": "net_debt_to_fcf",
    "accrual_ratio_max": "accrual_ratio",
    "sbc_pct_revenue_max": "sbc_pct_revenue",
    "sec_backlog_to_rev_min": "sec_backlog_to_rev",
}

# Single source-of-truth slider config for sector focus mode
METRIC_DEFS: dict[str, dict] = {
    "revenue_growth_min": dict(kind="pct", label="Min revenue growth (YoY)",
        lo=-20, hi=80, step=1,
        help="Latest quarter rev vs. same quarter last year. 15%+ = strong."),
    "rev_cagr_3y_min": dict(kind="pct", label="Min 3-yr revenue CAGR",
        lo=-20, hi=60, step=1,
        help="3-year compounded revenue growth — durability."),
    "earnings_growth_min": dict(kind="pct", label="Min EPS growth (YoY)",
        lo=-50, hi=200, step=5,
        help="Earnings per share YoY change."),
    "rule_of_40_min": dict(kind="pct", label="Min Rule of 40",
        lo=0, hi=100, step=5,
        help="Rev growth + FCF margin. SaaS quality bar. ≥40% = elite."),
    "gross_margin_min": dict(kind="pct", label="Min gross margin",
        lo=-20, hi=90, step=1,
        help="Pricing power. Software 70%+, commodity <15%."),
    "profit_margin_min": dict(kind="pct", label="Min profit margin",
        lo=-50, hi=50, step=1,
        help="Net income ÷ revenue. >10% = healthy."),
    "op_margin_min": dict(kind="pct", label="Min operating margin",
        lo=-30, hi=50, step=1,
        help="Operating income ÷ revenue. >15% = strong operator."),
    "op_margin_trend_min": dict(kind="pct", label="Min operating-margin trend (3-yr)",
        lo=-20, hi=20, step=1,
        help="Has operating margin been improving? 0%+ = stable/expanding."),
    "fcf_margin_min": dict(kind="pct", label="Min FCF margin",
        lo=-20, hi=50, step=1,
        help="Free cash flow ÷ revenue. The truest profitability metric."),
    "fcf_conversion_min": dict(kind="pct", label="Min FCF / Net Income",
        lo=0, hi=200, step=5,
        help="≥100% = reported profits are real cash."),
    "roic_min": dict(kind="pct", label="Min ROIC",
        lo=-10, hi=50, step=1,
        help="Return on Invested Capital. 15%+ = elite compounder."),
    "roic_wacc_spread_min": dict(kind="pct", label="Min ROIC − WACC spread",
        lo=-10, hi=30, step=1,
        help="How much ROIC beats cost of capital."),
    "return_on_equity_min": dict(kind="pct", label="Min ROE",
        lo=-20, hi=60, step=1,
        help="Return on Equity. >15% is good (watch for debt inflation)."),
    "forward_pe_max": dict(kind="num", label="Max Forward P/E",
        lo=5, hi=120, step=1, fmt="%g×",
        help="Price ÷ next-year earnings. <25 fair, >40 expensive."),
    "ev_to_fcf_max": dict(kind="num", label="Max EV / FCF",
        lo=5, hi=100, step=1, fmt="%g×",
        help="Enterprise value vs. free cash flow. <20 cheap, >50 priced for perfection."),
    "ev_to_ebitda_max": dict(kind="num", label="Max EV / EBITDA",
        lo=2, hi=50, step=0.5, fmt="%g×",
        help="Cap-intensive valuation. <10 cheap, >20 expensive."),
    "debt_to_equity_max": dict(kind="num", label="Max Debt / Equity (%)",
        lo=0, hi=500, step=10, fmt="%g",
        help="<100 conservative, >300 risky."),
    "interest_coverage_min": dict(kind="num", label="Min Interest Coverage",
        lo=0, hi=20, step=0.5, fmt="%g×",
        help="Op income ÷ interest expense. >5× safe."),
    "net_debt_to_fcf_max": dict(kind="num", label="Max Net Debt / FCF",
        lo=0, hi=20, step=0.5, fmt="%g× FCF",
        help="Years of FCF to clear debt. <3 safe, >7 leveraged."),
    "accrual_ratio_max": dict(kind="pct", label="Max accrual ratio",
        lo=-30, hi=30, step=1,
        help="Earnings quality. >10% = aggressive accounting."),
    "sbc_pct_revenue_max": dict(kind="pct", label="Max SBC / Revenue",
        lo=0, hi=50, step=1,
        help="Stock-based comp dilution. >25% = dilution problem."),
    "sec_backlog_to_rev_min": dict(kind="pct", label="Min Backlog / Revenue",
        lo=0, hi=300, step=10,
        help="Signed-contract revenue visibility. 100% = full year covered."),
}


def render_metric_by_key(key: str, filters: dict) -> None:
    """Render a single filter slider by its key, using METRIC_DEFS."""
    spec = METRIC_DEFS.get(key)
    if spec is None:
        st.sidebar.warning(f"Unknown metric: {key}")
        return
    # Sector-focus filters are required (not optional), so seed None → permissive lo.
    if filters.get(key) is None:
        filters[key] = (spec["lo"] / 100.0) if spec["kind"] == "pct" else float(spec["lo"])
    kind = spec["kind"]
    if kind == "pct":
        pct_slider(spec["label"], key, filters,
                   lo=spec["lo"], hi=spec["hi"], step=spec.get("step", 1),
                   help=spec.get("help", ""))
    else:
        num_slider(spec["label"], key, filters,
                   lo=spec["lo"], hi=spec["hi"], step=spec.get("step", 1),
                   fmt=spec.get("fmt", "%g"), help=spec.get("help", ""))


KEY_DATA_FIELDS = [
    "currentPrice", "marketCap", "revenueGrowth", "forwardPE", "beta",
    "grossMargins", "profitMargins", "fcf_margin", "fcf_conversion",
    "roic", "roic_wacc_spread", "accrual_ratio", "debtToEquity",
    "net_debt_to_fcf", "interest_coverage", "ev_to_fcf",
    "pct_below_52w_high", "pct_above_52w_low",
]

# Every threshold pushed to its most permissive value — nothing gets rejected.
# Used as the default so first-time users see the full universe.
SHOW_ALL_FILTERS = {
    "revenue_growth_min": -10.0,
    "rev_cagr_3y_min": None,
    "earnings_growth_min": None,
    "rule_of_40_min": None,
    "gross_margin_min": -10.0,
    "profit_margin_min": None,
    "fcf_margin_min": None,
    "fcf_conversion_min": None,
    "roic_min": None,
    "roic_wacc_spread_min": None,
    "return_on_equity_min": None,
    "op_margin_min": None,
    "op_margin_trend_min": None,
    "gross_margin_trend_min": None,
    "forward_pe_max": 1e9,
    "ev_to_fcf_max": None,
    "ev_to_ebitda_max": None,
    "beta_min": -10.0,
    "beta_max": 100.0,
    "debt_to_equity_max": 1e9,
    "interest_coverage_min": None,
    "current_ratio_min": None,
    "net_debt_to_fcf_max": None,
    "goodwill_pct_assets_max": None,
    "market_cap_min": 0,
    "min_pct_below_52w_high": -10.0,
    "max_pct_above_52w_low": 1e9,
    "short_float_max": None,
    "insider_ownership_min": None,
    "accrual_ratio_max": 1e9,
    "sbc_pct_revenue_max": None,
    "capex_to_revenue_max": None,
    "sec_backlog_to_rev_min": None,
}


PRESETS = {
    # ===== STARTER PRESETS =====
    "🔓 Show all (no filters)": dict(SHOW_ALL_FILTERS),
    "🎯 Balanced winners": {
        "revenue_growth_min": 0.15,
        "forward_pe_max": 50.0,
        "gross_margin_min": 0.20,
        "accrual_ratio_max": 0.15,
    },
    "📈 Quality compounders": {
        "revenue_growth_min": 0.08,
        "forward_pe_max": 45.0,
        "beta_min": 0.6,
        "beta_max": 2.0,
        "gross_margin_min": 0.30,
        "profit_margin_min": 0.06,
        "fcf_margin_min": 0.05,
        "fcf_conversion_min": 0.70,
        "roic_min": 0.08,
        "roic_wacc_spread_min": 0.01,
        "net_debt_to_fcf_max": 4.0,
        "sbc_pct_revenue_max": 0.15,
        "ev_to_fcf_max": 45.0,
    },
    "🚀 High-growth operators": {
        "revenue_growth_min": 0.25,
        "forward_pe_max": 80.0,
        "beta_min": 0.8,
        "beta_max": 3.2,
        "gross_margin_min": 0.25,
        "rev_cagr_3y_min": 0.15,
        "rule_of_40_min": 0.35,
        "short_float_max": 0.18,
        "sbc_pct_revenue_max": 0.25,
    },
    "📉 Pullback candidates": {
        "revenue_growth_min": 0.10,
        "forward_pe_max": 55.0,
        "beta_min": 0.8,
        "beta_max": 2.8,
        "min_pct_below_52w_high": 0.15,
        "max_pct_above_52w_low": 1.25,
        "fcf_margin_min": 0.00,
        "accrual_ratio_max": 0.12,
    },

    # ===== INSTITUTIONAL / ADVANCED PRESETS =====
    "🏆 Elite compounders (15%+ ROIC)": {
        # The rare gems: high ROIC every year, growing margins, clean accounting
        "revenue_growth_min": 0.08,
        "gross_margin_min": 0.40,
        "profit_margin_min": 0.10,
        "fcf_margin_min": 0.12,
        "fcf_conversion_min": 0.85,
        "roic_min": 0.15,
        "roic_wacc_spread_min": 0.05,
        "accrual_ratio_max": 0.05,
        "sbc_pct_revenue_max": 0.08,
        "debt_to_equity_max": 100.0,
        "interest_coverage_min": 8.0,
        "op_margin_trend_min": 0.00,
        "forward_pe_max": 45.0,
        "beta_min": 0.5,
        "beta_max": 1.8,
    },
    "🧹 Earnings quality (no accounting tricks)": {
        # Reported profits backed by real cash, low dilution, low M&A risk
        "accrual_ratio_max": 0.05,
        "fcf_conversion_min": 0.90,
        "sbc_pct_revenue_max": 0.10,
        "goodwill_pct_assets_max": 0.30,
        "revenue_growth_min": 0.08,
        "gross_margin_min": 0.30,
        "profit_margin_min": 0.05,
        "forward_pe_max": 40.0,
        "current_ratio_min": 1.2,
    },
    "🛡️ Capital allocators (Outsider-style)": {
        # Run by smart capital deployers — buybacks, high ROIC, low dilution
        "roic_min": 0.12,
        "roic_wacc_spread_min": 0.04,
        "sbc_pct_revenue_max": 0.08,
        "debt_to_equity_max": 150.0,
        "fcf_margin_min": 0.10,
        "fcf_conversion_min": 0.85,
        "forward_pe_max": 30.0,
        "ev_to_fcf_max": 25.0,
        "beta_max": 2.0,
    },
    "📦 Industrial backlog plays": {
        # Visible future revenue from signed contracts (defense, infra, large-cap SaaS)
        "sec_backlog_to_rev_min": 0.8,
        "revenue_growth_min": 0.08,
        "gross_margin_min": 0.20,
        "ev_to_ebitda_max": 18.0,
        "interest_coverage_min": 4.0,
        "debt_to_equity_max": 200.0,
        "fcf_margin_min": 0.05,
    },
    "☁️ SaaS compounders (Rule of 40+)": {
        # Software with elite gross margins and FCF discipline
        "rule_of_40_min": 0.40,
        "gross_margin_min": 0.65,
        "fcf_margin_min": 0.15,
        "fcf_conversion_min": 0.80,
        "revenue_growth_min": 0.20,
        "rev_cagr_3y_min": 0.20,
        "sbc_pct_revenue_max": 0.25,
        "forward_pe_max": 80.0,
        "beta_min": 0.8,
        "beta_max": 3.0,
    },
    "🏗️ Picks & shovels (AI / data center infra)": {
        # Build the rails others depend on — hardware, semis, electricals
        "gross_margin_min": 0.30,
        "revenue_growth_min": 0.20,
        "rev_cagr_3y_min": 0.10,
        "roic_min": 0.10,
        "fcf_margin_min": 0.08,
        "capex_to_revenue_max": 0.25,
        "debt_to_equity_max": 150.0,
        "forward_pe_max": 50.0,
        "beta_min": 0.8,
        "beta_max": 2.5,
    },
    "💰 Deep value (quality on sale)": {
        # Cheap on cash flow + still creating value vs. cost of capital
        "ev_to_fcf_max": 18.0,
        "roic_wacc_spread_min": 0.02,
        "fcf_margin_min": 0.06,
        "fcf_conversion_min": 0.75,
        "accrual_ratio_max": 0.10,
        "min_pct_below_52w_high": 0.20,
        "debt_to_equity_max": 150.0,
        "interest_coverage_min": 4.0,
        "forward_pe_max": 25.0,
        "revenue_growth_min": 0.03,
    },
    "💎 Hidden gems (small-cap quality)": {
        # Smaller names with elite economics
        "market_cap_min": 300_000_000,
        "roic_min": 0.12,
        "gross_margin_min": 0.35,
        "fcf_margin_min": 0.08,
        "revenue_growth_min": 0.15,
        "debt_to_equity_max": 100.0,
        "accrual_ratio_max": 0.08,
        "insider_ownership_min": 0.05,
        "beta_min": 0.5,
        "beta_max": 2.5,
        "forward_pe_max": 35.0,
    },
}


def data_version() -> tuple[float | None, float | None]:
    db_mtime = DB_FILE.stat().st_mtime if DB_FILE.exists() else None
    cache_mtime = CACHE_FILE.stat().st_mtime if CACHE_FILE.exists() else None
    return db_mtime, cache_mtime


def data_age_hours() -> float | None:
    if not DB_FILE.exists():
        return None
    age_seconds = datetime.now().timestamp() - DB_FILE.stat().st_mtime
    return max(age_seconds / 3600, 0)


@st.cache_data(show_spinner=False)
def load_latest_data(_version: tuple[float | None, float | None]) -> pd.DataFrame:
    if DB_FILE.exists():
        with sqlite3.connect(DB_FILE) as conn:
            try:
                df = pd.read_sql_query(
                    """
                    SELECT f.*
                    FROM fundamentals f
                    JOIN (
                        SELECT symbol, MAX(fetched_at) AS fetched_at
                        FROM fundamentals
                        WHERE (error IS NULL OR error = '')
                          AND currentPrice IS NOT NULL
                        GROUP BY symbol
                    ) latest
                      ON f.symbol = latest.symbol
                     AND f.fetched_at = latest.fetched_at
                    """,
                    conn,
                )
            except Exception:
                df = pd.read_sql_query("SELECT * FROM fundamentals", conn)
        return df.drop_duplicates("symbol", keep="last")

    if CACHE_FILE.exists():
        return pd.read_parquet(CACHE_FILE).drop_duplicates("symbol", keep="last")

    return pd.DataFrame()


def money_short(value) -> str:
    try:
        value = float(value)
    except Exception:
        return "-"
    if pd.isna(value):
        return "-"
    for suffix, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(value) >= scale:
            return f"${value / scale:,.1f}{suffix}"
    return f"${value:,.0f}"


def pct(value) -> str:
    try:
        value = float(value)
    except Exception:
        return "-"
    return "-" if pd.isna(value) else f"{value:.1%}"


def number(value, digits: int = 1) -> str:
    try:
        value = float(value)
    except Exception:
        return "-"
    return "-" if pd.isna(value) else f"{value:.{digits}f}"


def volume_short(value) -> str:
    try:
        value = float(value)
    except Exception:
        return "-"
    if pd.isna(value):
        return "-"
    for suffix, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= scale:
            return f"{value / scale:,.1f}{suffix}"
    return f"{value:,.0f}"


def metric_value(row: pd.Series, col: str):
    try:
        value = row.get(col)
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def clamp(value: float | None, low: float, high: float, default: float = 0.0) -> float:
    if value is None or pd.isna(value):
        return default
    return max(low, min(high, float(value)))


def score_high(value: float | None, low: float, high: float, default: float = 50.0) -> float:
    if value is None or pd.isna(value):
        return default
    if high == low:
        return default
    return 100 * (clamp(float(value), low, high) - low) / (high - low)


def score_low(value: float | None, low: float, high: float, default: float = 50.0) -> float:
    if value is None or pd.isna(value):
        return default
    if high == low:
        return default
    return 100 * (high - clamp(float(value), low, high)) / (high - low)


def avg_score(values: list[float]) -> float:
    return sum(values) / len(values) if values else 50.0


def score_breakdown(row: pd.Series) -> dict[str, float]:
    # ---- Growth: top-line, bottom-line, durability, SaaS-style efficiency ----
    growth = avg_score([
        score_high(metric_value(row, "revenueGrowth"), -0.05, 0.50),
        score_high(metric_value(row, "rev_cagr_3y"), -0.05, 0.30),
        score_high(metric_value(row, "earningsGrowth"), -0.20, 0.75),
        score_high(metric_value(row, "rule_of_40"), 0.10, 0.60),
        score_high(metric_value(row, "gross_margin_trend"), -0.05, 0.10),
        score_high(metric_value(row, "op_margin_trend"), -0.05, 0.10),
    ])
    # ---- Quality: margins, capital efficiency, earnings reality, dilution ----
    quality = avg_score([
        score_high(metric_value(row, "grossMargins"), 0.10, 0.75),
        score_high(metric_value(row, "profitMargins"), -0.05, 0.30),
        score_high(metric_value(row, "op_margin_latest"), -0.05, 0.35),
        score_high(metric_value(row, "fcf_margin"), -0.05, 0.30),
        score_high(metric_value(row, "fcf_conversion"), 0.00, 1.25),
        score_high(metric_value(row, "roic"), -0.05, 0.30),
        score_high(metric_value(row, "roic_wacc_spread"), -0.05, 0.15),
        score_high(metric_value(row, "returnOnEquity"), -0.05, 0.40),
        score_low(abs(metric_value(row, "accrual_ratio") or 0), 0.00, 0.20),
        score_low(metric_value(row, "sbc_pct_revenue"), 0.00, 0.25),
    ])
    # ---- Valuation: P/E, EV multiples, P/S, P/B, PEG ----
    valuation = avg_score([
        score_low(metric_value(row, "forwardPE"), 5.0, 60.0),
        score_low(metric_value(row, "trailingPE"), 5.0, 80.0),
        score_low(metric_value(row, "ev_to_fcf"), 5.0, 60.0),
        score_low(metric_value(row, "ev_to_ebitda"), 5.0, 35.0),
        score_low(metric_value(row, "priceToSalesTrailing12Months"), 1.0, 20.0),
        score_low(metric_value(row, "priceToBook"), 1.0, 15.0),
        score_low(metric_value(row, "pegRatio"), 0.5, 3.5),
    ])
    # ---- Balance sheet ----
    balance = avg_score([
        score_low(metric_value(row, "debtToEquity"), 0.0, 250.0),
        score_low(metric_value(row, "net_debt_to_fcf"), -2.0, 8.0),
        score_high(metric_value(row, "interest_coverage"), 0.0, 15.0),
        score_high(metric_value(row, "current_ratio"), 0.5, 3.0),
        score_high(metric_value(row, "quickRatio"), 0.3, 2.5),
        score_low(metric_value(row, "goodwill_pct_assets"), 0.0, 0.60),
    ])
    # ---- Momentum / market positioning ----
    off_high = metric_value(row, "pct_below_52w_high")
    above_low = metric_value(row, "pct_above_52w_low")
    beta = metric_value(row, "beta")
    momentum = avg_score([
        score_high(off_high, 0.00, 0.35),
        score_low(above_low, 0.00, 1.50),
        score_low(abs((beta or 1.5) - 1.2), 0.0, 1.5),
        score_low(metric_value(row, "shortPercentOfFloat"), 0.00, 0.20),
        score_high(metric_value(row, "heldPercentInsiders"), 0.00, 0.20),
    ])

    # ---- Forward projection: expected 5yr compounding power ----
    # Heuristic blend: durable growth + reinvestment ROIC + margin-of-safety on price.
    proj_growth = (
        metric_value(row, "rev_cagr_3y")
        or metric_value(row, "revenueGrowth")
        or 0.0
    )
    roic = metric_value(row, "roic") or 0.0
    spread = metric_value(row, "roic_wacc_spread") or 0.0
    fcf_yield = None
    ev_fcf = metric_value(row, "ev_to_fcf")
    if ev_fcf and ev_fcf > 0:
        fcf_yield = 1.0 / ev_fcf
    rule40 = metric_value(row, "rule_of_40") or 0.0
    projection = avg_score([
        # Expected 5yr revenue compounding (cap at 30% CAGR for sanity)
        score_high(min(proj_growth, 0.30), -0.05, 0.25),
        # Capital reinvested at a high ROIC compounds value
        score_high(roic, 0.05, 0.30),
        # Value creation vs cost of capital
        score_high(spread, -0.02, 0.15),
        # FCF yield = inverse of expensiveness (entry margin of safety)
        score_high(fcf_yield, 0.02, 0.10) if fcf_yield is not None else 50.0,
        # Earnings durability proxy
        score_high(metric_value(row, "fcf_conversion"), 0.50, 1.20),
        # SaaS-style efficiency bonus
        score_high(rule40, 0.20, 0.50),
        # Margin trajectory — improving margins compound returns
        score_high(metric_value(row, "op_margin_trend"), -0.05, 0.10),
    ])

    data = data_quality(row) * 100
    overall = (
        growth * 0.18
        + quality * 0.26
        + valuation * 0.16
        + balance * 0.10
        + momentum * 0.08
        + projection * 0.18
        + data * 0.04
    )
    return {
        "overall_score": round(overall, 0),
        "growth_score": round(growth, 0),
        "quality_score": round(quality, 0),
        "valuation_score": round(valuation, 0),
        "balance_score": round(balance, 0),
        "momentum_score": round(momentum, 0),
        "projection_score": round(projection, 0),
    }


def data_quality(row: pd.Series) -> float:
    available = 0
    for col in KEY_DATA_FIELDS:
        value = row.get(col)
        if value is not None and not pd.isna(value):
            available += 1
    return available / len(KEY_DATA_FIELDS)


def stock_strengths(row: pd.Series) -> list[str]:
    checks = []
    rg = metric_value(row, "revenueGrowth")
    cagr = metric_value(row, "rev_cagr_3y")
    fcf = metric_value(row, "fcf_margin")
    conv = metric_value(row, "fcf_conversion")
    roic = metric_value(row, "roic")
    spread = metric_value(row, "roic_wacc_spread")
    accrual = metric_value(row, "accrual_ratio")
    fpe = metric_value(row, "forwardPE")
    ev_fcf = metric_value(row, "ev_to_fcf")
    off_high = metric_value(row, "pct_below_52w_high")
    insider = metric_value(row, "heldPercentInsiders")

    if rg is not None and rg >= 0.15:
        checks.append(f"revenue growth {rg:.0%}")
    if cagr is not None and cagr >= 0.10:
        checks.append(f"3Y revenue CAGR {cagr:.0%}")
    if fcf is not None and fcf >= 0.05:
        checks.append(f"FCF margin {fcf:.0%}")
    if conv is not None and conv >= 0.80:
        checks.append(f"cash conversion {conv:.0%}")
    if roic is not None and roic >= 0.10:
        checks.append(f"ROIC {roic:.0%}")
    if spread is not None and spread >= 0.02:
        checks.append(f"ROIC-WACC spread {spread:.0%}")
    if accrual is not None and accrual <= 0.10:
        checks.append(f"low accruals {accrual:.1%}")
    if fpe is not None and 0 < fpe <= 25:
        checks.append(f"forward P/E {fpe:.1f}")
    if ev_fcf is not None and 0 < ev_fcf <= 25:
        checks.append(f"EV/FCF {ev_fcf:.1f}")
    if off_high is not None and off_high >= 0.10:
        checks.append(f"{off_high:.0%} below 52w high")
    if insider is not None and insider >= 0.03:
        checks.append(f"insider ownership {insider:.0%}")
    return checks


def stock_cautions(row: pd.Series) -> list[str]:
    warnings = []
    fcf = metric_value(row, "fcf_margin")
    accrual = metric_value(row, "accrual_ratio")
    sbc = metric_value(row, "sbc_pct_revenue")
    debt_fcf = metric_value(row, "net_debt_to_fcf")
    beta = metric_value(row, "beta")
    fpe = metric_value(row, "forwardPE")
    short_float = metric_value(row, "shortPercentOfFloat")

    if fcf is not None and fcf < 0:
        warnings.append(f"negative FCF margin {fcf:.0%}")
    if accrual is not None and accrual > 0.15:
        warnings.append(f"high accruals {accrual:.1%}")
    if sbc is not None and sbc > 0.15:
        warnings.append(f"SBC/revenue {sbc:.0%}")
    if debt_fcf is not None and debt_fcf > 5:
        warnings.append(f"net debt/FCF {debt_fcf:.1f}")
    if beta is not None and beta > 2.5:
        warnings.append(f"high beta {beta:.1f}")
    if fpe is not None and fpe > 50:
        warnings.append(f"forward P/E {fpe:.1f}")
    if short_float is not None and short_float > 0.15:
        warnings.append(f"short float {short_float:.0%}")
    return warnings


def quick_read(row: pd.Series) -> str:
    strengths = stock_strengths(row)
    if strengths:
        return "; ".join(strengths[:3])
    return "passes selected filters"


def enrich_for_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    enriched = df.copy()
    scores = enriched.apply(score_breakdown, axis=1, result_type="expand")
    for col in scores.columns:
        enriched[col] = scores[col]
    enriched["data_quality"] = enriched.apply(lambda row: f"{data_quality(row):.0%}", axis=1)
    enriched["quick_read"] = enriched.apply(quick_read, axis=1)
    return enriched


def rank_candidates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    ranked = enrich_for_display(df)
    return ranked.sort_values(["overall_score", "projection_score"], ascending=[False, False])


def latest_timestamp(df: pd.DataFrame) -> str:
    if "fetched_at" not in df.columns or df["fetched_at"].dropna().empty:
        return "unknown"
    return str(df["fetched_at"].dropna().max())


def add_momentum_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ("currentPrice", "fiftyTwoWeekLow", "fiftyTwoWeekHigh"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if {"currentPrice", "fiftyTwoWeekLow"}.issubset(df.columns):
        df["pct_above_52w_low"] = (
            (df["currentPrice"] - df["fiftyTwoWeekLow"]) / df["fiftyTwoWeekLow"]
        )
    if {"currentPrice", "fiftyTwoWeekHigh"}.issubset(df.columns):
        df["pct_below_52w_high"] = (
            (df["fiftyTwoWeekHigh"] - df["currentPrice"]) / df["fiftyTwoWeekHigh"]
        )
    return df


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


@st.cache_data(ttl=300, show_spinner=False)
def fetch_delayed_prices(symbols: tuple[str, ...]) -> pd.DataFrame:
    rows: list[dict] = []
    clean_symbols = tuple(dict.fromkeys(s for s in symbols if isinstance(s, str) and s.strip()))
    for chunk in _chunks(list(clean_symbols), 75):
        try:
            data = yf.download(
                tickers=chunk,
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )
        except Exception:
            continue

        if data is None or data.empty:
            continue

        if isinstance(data.columns, pd.MultiIndex):
            for sym in chunk:
                if sym not in data.columns.get_level_values(0):
                    continue
                sub = data[sym]
                if "Close" not in sub.columns:
                    continue
                close = pd.to_numeric(sub["Close"], errors="coerce").dropna()
                if close.empty:
                    continue
                rows.append({
                    "symbol": sym,
                    "live_price": float(close.iloc[-1]),
                    "live_price_asof": str(close.index[-1]),
                })
        else:
            if len(chunk) != 1 or "Close" not in data.columns:
                continue
            close = pd.to_numeric(data["Close"], errors="coerce").dropna()
            if close.empty:
                continue
            rows.append({
                "symbol": chunk[0],
                "live_price": float(close.iloc[-1]),
                "live_price_asof": str(close.index[-1]),
            })

    return pd.DataFrame(rows)


def apply_live_prices(df: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if df.empty or prices is None or prices.empty:
        return df
    out = df.copy()
    prices = prices.drop_duplicates("symbol", keep="last")
    out = out.merge(prices, on="symbol", how="left")
    live_price = pd.to_numeric(out.get("live_price"), errors="coerce")
    out["currentPrice"] = live_price.combine_first(pd.to_numeric(out["currentPrice"], errors="coerce"))
    return add_momentum_columns(out)


def run_full_screener(scope: str, workers: int, clear_cache: bool) -> subprocess.CompletedProcess:
    if clear_cache and CACHE_FILE.exists():
        CACHE_FILE.unlink()

    env = {
        **dict(__import__("os").environ),
        "SCREENER_SCOPE": scope,
        "SCREENER_MAX_WORKERS": str(workers),
        "SCREENER_NO_OPEN": "1",
    }
    return subprocess.run(
        [sys.executable, str(SCREENER_FILE)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )


def render_full_refresh_controls() -> None:
    age = data_age_hours()
    age_text = "never" if age is None else f"{age:.1f} hours old"
    st.caption(f"Fundamentals database age: {age_text}")

    with st.expander("Full data refresh", expanded=False):
        st.caption(
            "Runs `python screener.py` from the site. This refreshes fundamentals, "
            "statement KPIs, Excel output, and the database. It can take a few minutes."
        )
        c1, c2, c3 = st.columns([2, 1, 1])
        scope = c1.selectbox(
            "Universe",
            ["sp1500", "russell1000", "sp500"],
            index=0,
            help="sp1500 is the best balance. us_listed is intentionally omitted because Yahoo is too slow/noisy for it.",
        )
        workers = c2.slider("Workers", 1, 10, 5)
        clear_cache = c3.checkbox("Clear cache", value=True)

        max_age = st.slider("Auto-run if data older than hours", 1, 48, 12)
        auto_run = st.toggle("Auto-run full screener when stale", value=False)
        should_auto_run = auto_run and (age is None or age >= max_age)
        run_now = st.button("Run full screener now")

        if run_now or should_auto_run:
            if st.session_state.get("_screener_running"):
                st.info("A screener run is already in progress.")
                return
            st.session_state["_screener_running"] = True
            with st.spinner("Running full screener. Leave this tab open..."):
                try:
                    result = run_full_screener(scope, workers, clear_cache)
                except subprocess.TimeoutExpired:
                    st.session_state["_screener_running"] = False
                    st.error("The screener timed out after 30 minutes.")
                    return
                finally:
                    st.session_state["_screener_running"] = False

            if result.returncode == 0:
                load_latest_data.clear()
                fetch_delayed_prices.clear()
                st.success("Full screener refresh finished. Reloading data.")
                st.code(result.stdout[-4000:] if result.stdout else "(no output)")
                st.rerun()
            else:
                st.error("Full screener refresh failed. Existing database was left in place when possible.")
                st.code((result.stdout + "\n" + result.stderr)[-6000:])


def clean_for_filters(df: pd.DataFrame) -> pd.DataFrame:
    if "error" in df.columns:
        df = df[df["error"].isna() | (df["error"].astype(str).str.len() == 0)]
    return df.copy()


# =============================================================================
# Friendlier filter UI
# =============================================================================

# Each preset can suggest which Yahoo sectors it's geared toward.
# Empty list = no sector bias (apply to whole universe).
PRESET_SECTORS = {
    "🔓 Show all (no filters)": [],
    "🎯 Balanced winners": [],
    "📈 Quality compounders": [],
    "🚀 High-growth operators": ["Technology", "Communication Services", "Healthcare"],
    "📉 Pullback candidates": [],
    "🏆 Elite compounders (15%+ ROIC)": [],
    "🧹 Earnings quality (no accounting tricks)": [],
    "🛡️ Capital allocators (Outsider-style)": [],
    "📦 Industrial backlog plays": ["Industrials", "Technology"],
    "☁️ SaaS compounders (Rule of 40+)": ["Technology", "Communication Services"],
    "🏗️ Picks & shovels (AI / data center infra)": ["Technology", "Industrials", "Utilities"],
    "💰 Deep value (quality on sale)": [],
    "💎 Hidden gems (small-cap quality)": [],
}


PRESET_DESCRIPTIONS = {
    # Starter
    "🔓 Show all (no filters)":
        "No filters applied — every stock in the loaded universe is shown. "
        "Use this as a starting point, then turn on filters in the sidebar.",
    "🎯 Balanced winners":
        "Solid growth + fair valuation. The default starting point.",
    "📈 Quality compounders":
        "Boring-but-elite cash machines. Buffett/Munger style.",
    "🚀 High-growth operators":
        "Hypergrowth with profitability. SaaS/AI tilt.",
    "📉 Pullback candidates":
        "Quality stocks that have sold off — buying the dip.",
    # Institutional
    "🏆 Elite compounders (15%+ ROIC)":
        "The rare gems: 15%+ ROIC every year, clean accounting, growing margins. "
        "Usually only 5–15 stocks survive this screen.",
    "🧹 Earnings quality (no accounting tricks)":
        "Reported profits backed by real cash. Low accruals, low SBC, low goodwill. "
        "Filters out 'creative accounting' names.",
    "🛡️ Capital allocators (Outsider-style)":
        "Run by smart capital deployers: high ROIC, ROIC > WACC, low dilution, "
        "buyback-friendly. Based on Thorndike's 'The Outsiders'.",
    "📦 Industrial backlog plays":
        "Visible future revenue from signed contracts (defense, infrastructure, "
        "large SaaS). Pulls SEC XBRL backlog data.",
    "☁️ SaaS compounders (Rule of 40+)":
        "Software with growth + FCF margin ≥ 40%. Elite gross margins required.",
    "🏗️ Picks & shovels (AI / data center infra)":
        "The infrastructure layer powering AI: semis, electricals, cooling, power. "
        "Disciplined CapEx, growing.",
    "💰 Deep value (quality on sale)":
        "Cheap on EV/FCF AND still creating value vs. cost of capital. "
        "Combines value + quality — the rare double.",
    "💎 Hidden gems (small-cap quality)":
        "Sub-$300M+ market cap, high ROIC, high gross margin, insider ownership. "
        "Where alpha lives.",
}


def pct_slider(label: str, key: str, filters: dict, *,
               lo: float = -50.0, hi: float = 100.0, step: float = 1.0,
               help: str = "", optional: bool = False) -> None:
    """Percent-formatted slider that writes back into filters[key]. 0-1 scale internally."""
    current = filters.get(key)
    if optional:
        enabled = st.checkbox(f"Use {label}", value=current is not None,
                              key=f"chk_{key}", help=help)
        if not enabled:
            filters[key] = None
            return
        if current is None:
            current = max(lo / 100, 0.0)
    display = max(float(lo), min(float(hi), float(current) * 100))
    val = st.slider(label, float(lo), float(hi), display, float(step),
                    format="%g%%", key=f"sld_{key}", help=help)
    filters[key] = val / 100


def num_slider(label: str, key: str, filters: dict, *,
               lo: float, hi: float, step: float = 1.0, fmt: str = "%g",
               help: str = "", optional: bool = False) -> None:
    """Plain-number slider that writes back into filters[key]."""
    current = filters.get(key)
    if optional:
        enabled = st.checkbox(f"Use {label}", value=current is not None,
                              key=f"chk_{key}", help=help)
        if not enabled:
            filters[key] = None
            return
        if current is None:
            current = lo
    display = max(float(lo), min(float(hi), float(current)))
    val = st.slider(label, float(lo), float(hi), display, float(step),
                    format=fmt, key=f"sld_{key}", help=help)
    filters[key] = val


STARTER_PRESETS = [
    "🔓 Show all (no filters)",
    "🎯 Balanced winners",
    "📈 Quality compounders",
    "🚀 High-growth operators",
    "📉 Pullback candidates",
]
INSTITUTIONAL_PRESETS = [
    "🏆 Elite compounders (15%+ ROIC)",
    "🧹 Earnings quality (no accounting tricks)",
    "🛡️ Capital allocators (Outsider-style)",
    "📦 Industrial backlog plays",
    "☁️ SaaS compounders (Rule of 40+)",
    "🏗️ Picks & shovels (AI / data center infra)",
    "💰 Deep value (quality on sale)",
    "💎 Hidden gems (small-cap quality)",
]


def render_preset_picker() -> dict:
    """Big friendly preset chooser. Returns the chosen filter dict (preset overlay)."""
    st.sidebar.markdown("### 🎯 Strategy")
    tier = st.sidebar.radio(
        "Preset tier",
        ["Starter", "Institutional"],
        horizontal=True,
        help="Starter = simple growth/value screens. "
             "Institutional = uses ROIC, FCF conversion, accruals, backlog, etc.",
    )
    options = STARTER_PRESETS if tier == "Starter" else INSTITUTIONAL_PRESETS
    preset_name = st.sidebar.radio(
        "Pick the kind of stocks you want",
        options,
        help="Each preset is a starting point. Tweak the sliders below to refine.",
        label_visibility="collapsed",
    )
    st.sidebar.info(PRESET_DESCRIPTIONS.get(preset_name, ""))

    # Strictness toggle — institutional presets default to strict
    is_institutional = preset_name in INSTITUTIONAL_PRESETS
    strict = st.sidebar.toggle(
        "🔒 Strict mode (require data, reject blanks)",
        value=is_institutional,
        help="OFF: stocks missing a KPI still pass that filter (looser, more hits). "
             "ON: missing data = REJECT. Use for elite screens where you want only "
             "stocks that PROVE quality.",
    )
    st.session_state["_strict_optional"] = strict

    filters = screener.FILTERS.copy()
    filters.update(PRESETS[preset_name])
    st.session_state["_show_all"] = preset_name == "🔓 Show all (no filters)"
    st.session_state["_preset_name"] = preset_name
    return filters


def build_filter_controls(selected_sectors: list[str] | None = None) -> dict:
    selected_sectors = selected_sectors or []

    # ---------- FOCUS MODE: exactly one sector selected ----------
    if len(selected_sectors) == 1:
        sector = selected_sectors[0]
        cfg = SECTOR_CONFIG.get(sector)
        if cfg:
            # Skip presets entirely — sector mode is its own preset.
            filters = screener.FILTERS.copy()
            filters.update(SHOW_ALL_FILTERS)  # start permissive
            st.session_state["_show_all"] = False
            st.session_state["_strict_optional"] = False
            st.session_state["_preset_name"] = f"🎯 Focus: {sector}"
            st.session_state["_focus_sector"] = sector

            st.sidebar.markdown("### 🎯 Sector focus")
            st.sidebar.info(
                f"**{sector}** mode — only the KPIs that matter for this sector "
                "are shown. Switch back to ≥2 or 0 sectors for global filters."
            )
            st.sidebar.markdown("---")
            st.sidebar.markdown(f"#### {sector} KPIs")
            for key in cfg["metrics"]:
                render_metric_by_key(key, filters)
            return filters

    # ---------- GLOBAL MODE: preset + expanders ----------
    st.session_state["_focus_sector"] = None
    filters = render_preset_picker()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### Tune the screen")
    st.sidebar.caption(
        "Each filter has a help icon (?) explaining what it measures and "
        "what a good value looks like."
    )

    # ---------- GROWTH ----------
    with st.sidebar.expander("🌱 Growth — is the business expanding?", expanded=True):
        pct_slider(
            "Min revenue growth (YoY)", "revenue_growth_min", filters,
            lo=-20, hi=80, step=1,
            help="Latest quarter revenue vs. same quarter last year. "
                 "S&P median ≈ 5–7%. 15%+ = strong. 30%+ = hypergrowth.",
        )
        pct_slider(
            "Min 3-year revenue CAGR", "rev_cagr_3y_min", filters,
            lo=-20, hi=60, step=1, optional=True,
            help="Sustained growth over 3 years — filters out one-quarter wonders.",
        )
        pct_slider(
            "Min EPS growth (YoY)", "earnings_growth_min", filters,
            lo=-50, hi=200, step=5, optional=True,
            help="Earnings per share growth. Confirms that revenue growth is "
                 "translating to actual profits.",
        )
        pct_slider(
            "Min Rule of 40 (SaaS)", "rule_of_40_min", filters,
            lo=0, hi=100, step=5, optional=True,
            help="Rev growth + FCF margin. Used to screen SaaS/software. "
                 "≥40% = elite, ≥30% = solid, <20% = weak.",
        )

    # ---------- PROFITABILITY ----------
    with st.sidebar.expander("💰 Profitability — does it make real money?", expanded=True):
        pct_slider(
            "Min gross margin", "gross_margin_min", filters,
            lo=-20, hi=90, step=1,
            help="Revenue minus cost of goods, as % of revenue. "
                 "Pricing power indicator. Software 70%+, retail 20–30%, commodity <15%.",
        )
        pct_slider(
            "Min profit margin", "profit_margin_min", filters,
            lo=-50, hi=50, step=1, optional=True,
            help="Net income as % of revenue. >10% = healthy, <0% = losing money.",
        )
        pct_slider(
            "Min FCF margin", "fcf_margin_min", filters,
            lo=-20, hi=50, step=1, optional=True,
            help="Free cash flow as % of revenue. The truest profitability metric "
                 "(can't be faked with accounting). >15% = elite.",
        )
        pct_slider(
            "Min FCF/Net Income conversion", "fcf_conversion_min", filters,
            lo=0, hi=200, step=5, optional=True,
            help="FCF ÷ Net Income. Should be ≥100% for high-quality companies — "
                 "means reported profits are real cash.",
        )
        pct_slider(
            "Min ROIC", "roic_min", filters,
            lo=-10, hi=50, step=1, optional=True,
            help="Return on Invested Capital — the single best quality metric. "
                 "<8% = capital-destroyer, 15%+ = elite compounder.",
        )
        pct_slider(
            "Min ROIC − WACC spread", "roic_wacc_spread_min", filters,
            lo=-10, hi=30, step=1, optional=True,
            help="How much ROIC beats cost of capital. Positive = creating value, "
                 "negative = destroying value.",
        )
        pct_slider(
            "Min ROE", "return_on_equity_min", filters,
            lo=-20, hi=60, step=1, optional=True,
            help="Return on Equity. >15% is good. Beware: can be inflated by debt.",
        )

    # ---------- FINANCIAL STRENGTH ----------
    with st.sidebar.expander("🏦 Financial Strength — won't go bankrupt", expanded=False):
        num_slider(
            "Max Debt / Equity", "debt_to_equity_max", filters,
            lo=0, hi=500, step=10, fmt="%g",
            help="Total debt vs. shareholder equity (Yahoo reports as %). "
                 "<100 = conservative, 100–200 = moderate, >300 = risky.",
        )
        num_slider(
            "Min Interest Coverage", "interest_coverage_min", filters,
            lo=0, hi=20, step=0.5, fmt="%g×",
            optional=True,
            help="Operating income ÷ interest expense. <2× = at risk in a downturn, "
                 ">5× = very safe.",
        )
        num_slider(
            "Min Current Ratio", "current_ratio_min", filters,
            lo=0, hi=5, step=0.1, fmt="%g",
            optional=True,
            help="Current assets ÷ current liabilities. ≥1 = can cover short-term "
                 "bills. <1 = liquidity squeeze risk.",
        )
        num_slider(
            "Max Net Debt / FCF", "net_debt_to_fcf_max", filters,
            lo=0, hi=20, step=0.5, fmt="%g× FCF",
            optional=True,
            help="Years of free cash flow needed to pay off all debt. "
                 "<3 = safe, >7 = leveraged.",
        )
        pct_slider(
            "Max Goodwill / Assets", "goodwill_pct_assets_max", filters,
            lo=0, hi=80, step=5, optional=True,
            help="High goodwill means the company grew by acquisition. "
                 "Risk: future write-downs hit earnings.",
        )

    # ---------- VALUATION ----------
    with st.sidebar.expander("📊 Valuation — am I overpaying?", expanded=True):
        num_slider(
            "Max Forward P/E", "forward_pe_max", filters,
            lo=5, hi=120, step=1, fmt="%g×",
            help="Price vs. next year's expected earnings. <15 = cheap, "
                 "15–25 = fair, 25–40 = pricey but OK for growth, >40 = expensive.",
        )
        num_slider(
            "Max EV / FCF", "ev_to_fcf_max", filters,
            lo=5, hi=100, step=1, fmt="%g×",
            optional=True,
            help="Enterprise value vs. free cash flow. Cleaner than P/E. "
                 "<20 = cheap, 20–35 = fair, >50 = priced for perfection.",
        )
        num_slider(
            "Max EV / EBITDA", "ev_to_ebitda_max", filters,
            lo=2, hi=50, step=0.5, fmt="%g×",
            optional=True,
            help="Used for capital-intensive businesses. <10 = cheap, "
                 "10–15 = fair, >20 = expensive.",
        )

    # ---------- RISK & MOMENTUM ----------
    with st.sidebar.expander("📉 Risk & Momentum", expanded=True):
        bmin_disp = max(0.0, min(4.0, float(filters["beta_min"])))
        bmax_disp = max(0.0, min(4.0, float(filters["beta_max"])))
        if bmin_disp > bmax_disp:
            bmin_disp, bmax_disp = bmax_disp, bmin_disp
        beta_min, beta_max = st.slider(
            "Beta range (volatility vs. market)",
            0.0, 4.0,
            (bmin_disp, bmax_disp),
            0.1,
            help="1.0 = moves with market. <1 = defensive. "
                 ">1.5 = aggressive. Pick range based on your risk tolerance.",
        )
        filters["beta_min"] = beta_min
        filters["beta_max"] = beta_max

        mcap_b = st.slider(
            "Min market cap",
            0.0, 50.0,
            float(filters.get("market_cap_min") or 0) / 1e9,
            0.5,
            format="$%gB",
            help="<$2B = small cap (volatile), $2–10B = mid cap, "
                 ">$10B = large cap (safer).",
            key="sld_market_cap_min",
        )
        filters["market_cap_min"] = mcap_b * 1e9

        pct_slider(
            "Min % below 52-week high", "min_pct_below_52w_high", filters,
            lo=0, hi=80, step=1,
            help="Don't chase the top. Require stocks be at least X% off their "
                 "52w high. Higher = more pullback bias.",
        )
        pct_slider(
            "Max % above 52-week low", "max_pct_above_52w_low", filters,
            lo=0, hi=300, step=5,
            help="Don't chase rallies. Reject stocks already up X% from their low. "
                 "100% = doubled from low.",
        )
        pct_slider(
            "Max short float %", "short_float_max", filters,
            lo=0, hi=50, step=1, optional=True,
            help="Sentiment indicator. High short interest = bears are betting "
                 "against it. >20% = heavily shorted.",
        )
        pct_slider(
            "Min insider ownership", "insider_ownership_min", filters,
            lo=0, hi=50, step=1, optional=True,
            help="Skin in the game. Higher = management aligned with shareholders.",
        )

    # ---------- ADVANCED ----------
    with st.sidebar.expander("🎓 Advanced (institutional metrics)", expanded=False):
        pct_slider(
            "Max accrual ratio (earnings quality)", "accrual_ratio_max", filters,
            lo=-30, hi=30, step=1, optional=True,
            help="(Net income − Operating cash flow) ÷ Assets. >10% = aggressive "
                 "accounting / possible earnings manipulation.",
        )
        pct_slider(
            "Max SBC / Revenue (dilution)", "sbc_pct_revenue_max", filters,
            lo=0, hi=50, step=1, optional=True,
            help="Stock-based compensation as % of revenue. "
                 "Tech often 10–20%, anything >25% = dilution problem.",
        )
        pct_slider(
            "Min operating margin", "op_margin_min", filters,
            lo=-30, hi=50, step=1, optional=True,
            help="Operating income ÷ revenue. Core profitability before "
                 "interest/taxes.",
        )
        pct_slider(
            "Min operating margin trend (5Y)", "op_margin_trend_min", filters,
            lo=-20, hi=20, step=1, optional=True,
            help="Is operating margin getting better or worse over 5 years?",
        )
        pct_slider(
            "Min gross margin trend (5Y)", "gross_margin_trend_min", filters,
            lo=-20, hi=20, step=1, optional=True,
            help="Is pricing power improving? Positive = yes.",
        )
        pct_slider(
            "Max CapEx / Revenue", "capex_to_revenue_max", filters,
            lo=0, hi=50, step=1, optional=True,
            help="Capital intensity. Software <5%, manufacturing 5–10%, "
                 "telecom/utilities 15%+.",
        )
        num_slider(
            "Min Backlog / Revenue (SEC)", "sec_backlog_to_rev_min", filters,
            lo=0, hi=10, step=0.1, fmt="%g×",
            optional=True,
            help="Years of revenue locked in via signed contracts. "
                 "Strong for industrials, defense, SaaS. >1× = great visibility.",
        )

    return filters


def render_filter_funnel(scoped: pd.DataFrame, filters: dict) -> None:
    """Show how each filter narrows the universe — the 'why am I getting 0 hits' answer."""
    if scoped.empty:
        return

    # Run each filter standalone to show its individual impact
    df = screener.add_momentum_columns(scoped) if hasattr(screener, "add_momentum_columns") else scoped
    # Compute momentum cols in case screener doesn't expose helper
    for col in ("currentPrice", "fiftyTwoWeekLow", "fiftyTwoWeekHigh"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if {"currentPrice", "fiftyTwoWeekLow"}.issubset(df.columns):
        df["pct_above_52w_low"] = (df["currentPrice"] - df["fiftyTwoWeekLow"]) / df["fiftyTwoWeekLow"]
    if {"currentPrice", "fiftyTwoWeekHigh"}.issubset(df.columns):
        df["pct_below_52w_high"] = (df["fiftyTwoWeekHigh"] - df["currentPrice"]) / df["fiftyTwoWeekHigh"]

    checks = [
        ("Rev growth ≥",        "revenueGrowth",      "ge", "revenue_growth_min", "%"),
        ("Fwd P/E ≤",           "forwardPE",          "le", "forward_pe_max",     "×"),
        ("Beta range",          "beta",               "range", ("beta_min","beta_max"), ""),
        ("D/E ≤",               "debtToEquity",       "le", "debt_to_equity_max", ""),
        ("Gross margin ≥",      "grossMargins",       "ge", "gross_margin_min",   "%"),
        ("Market cap ≥",        "marketCap",          "ge", "market_cap_min",     "$"),
        ("Below 52w hi ≥",      "pct_below_52w_high", "ge", "min_pct_below_52w_high", "%"),
        ("Above 52w lo ≤",      "pct_above_52w_low",  "le", "max_pct_above_52w_low",  "%"),
        ("FCF margin ≥",        "fcf_margin",         "ge", "fcf_margin_min",     "%"),
        ("ROIC ≥",              "roic",               "ge", "roic_min",           "%"),
        ("Accruals ≤",          "accrual_ratio",      "le", "accrual_ratio_max",  "%"),
        ("Rule of 40 ≥",        "rule_of_40",         "ge", "rule_of_40_min",     "%"),
        ("EV/FCF ≤",            "ev_to_fcf",          "le", "ev_to_fcf_max",      "×"),
        ("Interest cov ≥",      "interest_coverage",  "ge", "interest_coverage_min", "×"),
    ]

    rows = []
    universe = len(df)
    for label, col, op, key, fmt in checks:
        if col not in df.columns:
            continue
        if op == "range":
            kmin, kmax = key
            lo_v, hi_v = filters.get(kmin), filters.get(kmax)
            if lo_v is None or hi_v is None:
                continue
            s = pd.to_numeric(df[col], errors="coerce")
            survivors = int(((s >= lo_v) & (s <= hi_v)).sum())
            value_str = f"{lo_v:.1f}–{hi_v:.1f}"
        else:
            thresh = filters.get(key)
            if thresh is None:
                continue
            s = pd.to_numeric(df[col], errors="coerce")
            cond = (s >= thresh) if op == "ge" else (s <= thresh)
            survivors = int((cond | s.isna()).sum())
            if fmt == "%":
                value_str = f"{thresh:.1%}"
            elif fmt == "$":
                value_str = f"${thresh/1e9:.1f}B"
            elif fmt == "×":
                value_str = f"{thresh:.1f}×"
            else:
                value_str = f"{thresh:.2f}"
        kill_rate = 1 - (survivors / universe) if universe else 0
        rows.append({
            "Filter": label,
            "Threshold": value_str,
            "Passes alone": survivors,
            "Kill rate alone": f"{kill_rate:.0%}",
        })

    if not rows:
        return
    funnel_df = pd.DataFrame(rows).sort_values("Passes alone", ascending=True)
    st.markdown("##### 🔎 Which filters are killing your candidates?")
    st.caption(
        "Each row shows how many stocks would pass if ONLY this filter were applied. "
        "The lowest 'Passes alone' is your binding constraint — loosen that one first."
    )
    st.dataframe(funnel_df, hide_index=True, use_container_width=True)


def pick_sectors_sidebar(df: pd.DataFrame) -> list[str]:
    """Render the sector multiselect FIRST in the sidebar; returns selected list."""
    st.sidebar.header("Universe")
    sectors = sorted(df.get("sector", pd.Series(dtype=str)).dropna().astype(str).unique())

    preset_name = st.session_state.get("_preset_name", "")
    suggested = [s for s in PRESET_SECTORS.get(preset_name, []) if s in sectors]
    sector_key = f"sector_select::{preset_name}"
    if sector_key not in st.session_state and suggested:
        st.session_state[sector_key] = suggested
    selected = st.sidebar.multiselect(
        "Sectors",
        sectors,
        default=st.session_state.get(sector_key, suggested),
        key=sector_key,
        help="Pick exactly ONE sector → Focus Mode (sector-tuned KPIs only). "
             "0 or 2+ sectors → global filters.",
    )
    if len(selected) == 1:
        st.sidebar.success(f"🎯 Focus mode: **{selected[0]}**")
    if suggested and selected != suggested:
        if st.sidebar.button("↺ Reset to preset default", key=f"reset_{sector_key}"):
            st.session_state[sector_key] = suggested
            st.rerun()
    if suggested:
        st.sidebar.caption(f"Preset targets: {', '.join(suggested)}")
    return selected


def filter_by_sector_and_search(df: pd.DataFrame, selected: list[str]) -> pd.DataFrame:
    """Apply sector + theme + ticker/company search filters."""
    themes = st.sidebar.text_input("Theme keywords", help="Examples: ai, data center, solar, defense")
    query = st.sidebar.text_input("Ticker, company, or industry")

    out = df.copy()
    if selected:
        out = out[out["sector"].isin(selected)]
    if themes:
        keywords = screener.expand_themes(themes)
        out = screener.apply_theme_filter(out, keywords)
    if query:
        q = query.strip().lower()
        haystack = (
            out.get("symbol", "").astype(str) + " "
            + out.get("shortName", "").astype(str) + " "
            + out.get("industry", "").astype(str)
        ).str.lower()
        out = out[haystack.str.contains(q, na=False)]
    return out


def format_table(df: pd.DataFrame) -> pd.DataFrame:
    enriched = enrich_for_display(df)
    cols = [c for c in DISPLAY_COLUMNS if c in enriched.columns]
    out = enriched[cols].copy()
    return out


def render_metric_row(
    raw: pd.DataFrame,
    screenable: pd.DataFrame,
    scoped: pd.DataFrame,
    hits: pd.DataFrame,
) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Raw symbols", f"{len(raw):,}")
    c2.metric("Screenable", f"{len(screenable):,}")
    c3.metric("Selected", f"{len(scoped):,}")
    c4.metric("Passing", f"{len(hits):,}")
    pass_rate = len(hits) / len(scoped) if len(scoped) else 0
    c5.metric("Pass rate", f"{pass_rate:.1%}")


@st.cache_data(ttl=300, show_spinner=False)
def fetch_price_history(symbol: str, period: str) -> pd.DataFrame:
    interval = "5m" if period == "1d" else "1d"
    try:
        data = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
    except Exception:
        return pd.DataFrame()

    if data is None or data.empty:
        return pd.DataFrame()

    close = None
    volume = None
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            close_block = data["Close"]
            close = close_block.iloc[:, 0] if isinstance(close_block, pd.DataFrame) else close_block
        if "Volume" in data.columns.get_level_values(0):
            volume_block = data["Volume"]
            volume = volume_block.iloc[:, 0] if isinstance(volume_block, pd.DataFrame) else volume_block
    else:
        if "Close" in data.columns:
            close = data["Close"]
        if "Volume" in data.columns:
            volume = data["Volume"]

    if close is None:
        return pd.DataFrame()

    out = pd.DataFrame({"Close": pd.to_numeric(close, errors="coerce")})
    if volume is not None:
        out["Volume"] = pd.to_numeric(volume, errors="coerce")
    out = out.dropna(subset=["Close"])
    out.index = pd.to_datetime(out.index)
    return out


@st.cache_data(ttl=60, show_spinner=False)
def fetch_live_quote(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(symbol)
        fast = ticker.fast_info
    except Exception:
        return {}

    def fast_get(name: str):
        try:
            return fast.get(name)
        except Exception:
            try:
                return getattr(fast, name)
            except Exception:
                return None

    last_price = fast_get("last_price") or fast_get("lastPrice")
    previous_close = fast_get("previous_close") or fast_get("previousClose")
    volume = fast_get("last_volume") or fast_get("lastVolume")

    try:
        last_price = float(last_price) if last_price is not None else None
    except Exception:
        last_price = None
    try:
        previous_close = float(previous_close) if previous_close is not None else None
    except Exception:
        previous_close = None

    change = None
    change_pct = None
    if last_price is not None and previous_close:
        change = last_price - previous_close
        change_pct = change / previous_close

    return {
        "price": last_price,
        "previous_close": previous_close,
        "change": change,
        "change_pct": change_pct,
        "volume": volume,
        "loaded_at": datetime.now().strftime("%H:%M:%S"),
    }


def render_price_chart(row: pd.Series, key_prefix: str, quote: dict | None = None) -> None:
    symbol = str(row.get("symbol", "")).strip()
    if not symbol:
        st.info("No ticker available for charting.")
        return

    periods = ["1d", "1mo", "3mo", "6mo", "1y", "2y", "5y"]
    period = st.radio(
        "Chart range",
        periods,
        index=0,
        horizontal=True,
        key=f"{key_prefix}_chart_period_{symbol}",
    )
    hist = fetch_price_history(symbol, period)
    if hist.empty:
        st.warning("No chart data came back for this ticker.")
        return

    last_close = float(hist["Close"].iloc[-1])
    first_close = float(hist["Close"].iloc[0])
    change = last_close - first_close
    change_pct = change / first_close if first_close else 0
    period_high = float(hist["Close"].max())
    period_low = float(hist["Close"].min())
    quote = quote or fetch_live_quote(symbol)
    row_price = quote.get("price") or metric_value(row, "currentPrice") or last_close
    quote_change_pct = quote.get("change_pct")

    has_volume = "Volume" in hist.columns and hist["Volume"].notna().any()
    avg_volume = float(hist["Volume"].mean()) if has_volume else None
    last_volume = float(hist["Volume"].iloc[-1]) if has_volume else None

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(
        "Current quote",
        f"${row_price:,.2f}",
        f"{quote_change_pct:.1%}" if quote_change_pct is not None else None,
    )
    m2.metric(f"{period} change", f"{change_pct:.1%}", f"${change:,.2f}")
    m3.metric(f"{period} high", f"${period_high:,.2f}",
              f"{(last_close/period_high - 1):+.1%} vs last" if period_high else None,
              delta_color="off")
    m4.metric(f"{period} low", f"${period_low:,.2f}",
              f"{(last_close/period_low - 1):+.1%} vs last" if period_low else None,
              delta_color="off")
    if has_volume:
        m5.metric(
            "Volume (last / avg)",
            volume_short(last_volume),
            f"vs {volume_short(avg_volume)} avg",
            delta_color="off",
        )

    import altair as alt

    price_df = hist.reset_index().rename(columns={hist.index.name or "index": "Date"})

    n = len(price_df)
    if n >= 20:
        price_df["SMA20"] = price_df["Close"].rolling(20, min_periods=5).mean()
    if n >= 50:
        price_df["SMA50"] = price_df["Close"].rolling(50, min_periods=20).mean()

    price_min = float(price_df["Close"].min())
    price_max = float(price_df["Close"].max())
    pad = max((price_max - price_min) * 0.08, price_max * 0.01, 0.01)
    y_low = max(0.0, price_min - pad)
    y_high = price_max + pad

    nearest = alt.selection_point(
        nearest=True, on="mouseover", fields=["Date"], empty=False, clear="mouseout"
    )

    x_enc = alt.X("Date:T", axis=alt.Axis(title=None, labelAngle=0))
    price_scale = alt.Scale(domain=[y_low, y_high], clamp=True, nice=False)

    layers = []

    if has_volume:
        vol_max = float(price_df["Volume"].max() or 0)
        vol_domain_top = vol_max * 5 if vol_max else 1
        volume_bars = (
            alt.Chart(price_df)
            .mark_bar(opacity=0.35, color="#6c8ebf")
            .encode(
                x=x_enc,
                y=alt.Y(
                    "Volume:Q",
                    scale=alt.Scale(domain=[0, vol_domain_top], nice=False),
                    axis=alt.Axis(title="Volume", orient="right", format="~s",
                                  labelColor="#6c8ebf", titleColor="#6c8ebf"),
                ),
            )
        )
        layers.append(volume_bars)

    price_area = (
        alt.Chart(price_df)
        .mark_area(opacity=0.12, color="#1f77b4")
        .encode(
            x=x_enc,
            y=alt.Y("Close:Q", scale=price_scale, axis=None),
        )
    )
    layers.append(price_area)

    price_line = (
        alt.Chart(price_df)
        .mark_line(color="#1f77b4", strokeWidth=2)
        .encode(
            x=x_enc,
            y=alt.Y(
                "Close:Q",
                scale=price_scale,
                axis=alt.Axis(title="Price (USD)", orient="left", format="$,.2f",
                              labelColor="#1f77b4", titleColor="#1f77b4"),
            ),
        )
    )
    layers.append(price_line)

    if "SMA20" in price_df.columns:
        layers.append(
            alt.Chart(price_df)
            .mark_line(color="#ff7f0e", strokeWidth=1.5, strokeDash=[4, 2])
            .encode(x=x_enc, y=alt.Y("SMA20:Q", scale=price_scale, axis=None))
        )
    if "SMA50" in price_df.columns:
        layers.append(
            alt.Chart(price_df)
            .mark_line(color="#2ca02c", strokeWidth=1.5, strokeDash=[6, 3])
            .encode(x=x_enc, y=alt.Y("SMA50:Q", scale=price_scale, axis=None))
        )

    rule = (
        alt.Chart(price_df)
        .mark_rule(color="gray")
        .encode(
            x=x_enc,
            opacity=alt.condition(nearest, alt.value(0.6), alt.value(0)),
            tooltip=[
                alt.Tooltip("Date:T", title="Date"),
                alt.Tooltip("Close:Q", title="Close", format="$,.2f"),
            ]
            + ([alt.Tooltip("SMA20:Q", title="SMA 20", format="$,.2f")]
               if "SMA20" in price_df.columns else [])
            + ([alt.Tooltip("SMA50:Q", title="SMA 50", format="$,.2f")]
               if "SMA50" in price_df.columns else [])
            + ([alt.Tooltip("Volume:Q", title="Volume", format=",.0f")]
               if has_volume else []),
        )
        .add_params(nearest)
    )
    layers.append(rule)

    hover_point = (
        alt.Chart(price_df)
        .mark_point(size=80, color="#1f77b4", filled=True)
        .encode(
            x=x_enc,
            y=alt.Y("Close:Q", scale=price_scale, axis=None),
            opacity=alt.condition(nearest, alt.value(1), alt.value(0)),
        )
    )
    layers.append(hover_point)

    chart = (
        alt.layer(*layers)
        .resolve_scale(y="independent")
        .properties(height=420)
        .configure_view(strokeOpacity=0)
        .configure_axis(grid=True, gridOpacity=0.15)
    )
    st.altair_chart(chart, use_container_width=True)

    legend_items = [
        '<span style="color:#1f77b4;font-weight:600;">━ Close price</span> '
        '<span style="opacity:.7;">(daily closing price, left axis)</span>'
    ]
    if "SMA20" in price_df.columns:
        legend_items.append(
            '<span style="color:#ff7f0e;font-weight:600;">┄ SMA 20</span> '
            '<span style="opacity:.7;">(20-day simple moving average &mdash; short-term trend)</span>'
        )
    if "SMA50" in price_df.columns:
        legend_items.append(
            '<span style="color:#2ca02c;font-weight:600;">┄ SMA 50</span> '
            '<span style="opacity:.7;">(50-day simple moving average &mdash; medium-term trend)</span>'
        )
    if has_volume:
        legend_items.append(
            '<span style="color:#6c8ebf;font-weight:600;">▮ Volume</span> '
            '<span style="opacity:.7;">(shares traded per day, right axis)</span>'
        )
    st.markdown(
        "<div style='font-size:0.85rem;line-height:1.6;'>"
        + "<br>".join(legend_items)
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "**How to read it:** when price crosses above SMA 20 / SMA 50 it suggests upward momentum; "
        "when SMA 20 crosses above SMA 50 (a *golden cross*) it's a classic bullish signal. "
        "Volume spikes confirm whether a price move has real conviction behind it."
    )
    loaded_at = quote.get("loaded_at")
    quote_note = f" Fast quote loaded at {loaded_at}." if loaded_at else ""
    st.caption(
        "Quotes and charts use Yahoo delayed data; Google can be faster during market hours."
        f"{quote_note} The full screener refresh updates fundamentals."
    )


def render_quick_read(row: pd.Series) -> None:
    strengths = stock_strengths(row)
    cautions = stock_cautions(row)
    coverage = data_quality(row)

    q1, q2, q3 = st.columns([2, 2, 1])
    with q1:
        st.markdown("**Strengths**")
        if strengths:
            for item in strengths[:6]:
                st.write(f"- {item}")
        else:
            st.write("No standout positive KPI above the default thresholds.")
    with q2:
        st.markdown("**Watch Items**")
        if cautions:
            for item in cautions[:6]:
                st.write(f"- {item}")
        else:
            st.write("No major red flags from the tracked KPIs.")
    with q3:
        st.markdown("**Data Coverage**")
        st.progress(coverage)
        st.write(f"{coverage:.0%} of key fields")


def render_kpi_groups(row: pd.Series) -> None:
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        st.markdown("**Growth**")
        st.write(f"Revenue growth: {pct(row.get('revenueGrowth'))}")
        st.write(f"3Y revenue CAGR: {pct(row.get('rev_cagr_3y'))}")
        st.write(f"EPS growth: {pct(row.get('earningsGrowth'))}")
        st.write(f"Rule of 40: {pct(row.get('rule_of_40'))}")
    with g2:
        st.markdown("**Quality**")
        st.write(f"Gross margin: {pct(row.get('grossMargins'))}")
        st.write(f"FCF margin: {pct(row.get('fcf_margin'))}")
        st.write(f"FCF/NI: {pct(row.get('fcf_conversion'))}")
        st.write(f"Accrual ratio: {pct(row.get('accrual_ratio'))}")
    with g3:
        st.markdown("**Capital**")
        st.write(f"ROIC: {pct(row.get('roic'))}")
        st.write(f"WACC: {pct(row.get('wacc'))}")
        st.write(f"ROIC-WACC: {pct(row.get('roic_wacc_spread'))}")
        st.write(f"Net debt/FCF: {number(row.get('net_debt_to_fcf'))}")
    with g4:
        st.markdown("**Valuation/Risk**")
        st.write(f"Forward P/E: {number(row.get('forwardPE'))}")
        st.write(f"EV/FCF: {number(row.get('ev_to_fcf'))}")
        st.write(f"Beta: {number(row.get('beta'))}")
        st.write(f"Short float: {pct(row.get('shortPercentOfFloat'))}")


def render_analysis_depth(row: pd.Series) -> None:
    st.markdown("#### Valuation Beyond P/E")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Forward P/E", number(row.get("forwardPE")))
    v2.metric("EV/EBITDA", number(row.get("ev_to_ebitda")))
    v3.metric("EV/FCF", number(row.get("ev_to_fcf")))
    v4.metric("PEG on FCF", number(row.get("peg_on_fcf")))
    st.caption(
        "Forward P/E can understate reinvestment-heavy companies. EV/EBITDA and EV/FCF "
        "help compare the business against operating cash flow and enterprise value."
    )

    st.markdown("#### Operating Leverage")
    o1, o2, o3, o4 = st.columns(4)
    o1.metric("Operating margin", pct(row.get("op_margin_latest")))
    o2.metric("Margin trend", pct(row.get("op_margin_trend")))
    o3.metric("Incremental margin", pct(row.get("incremental_margin")))
    o4.metric("R&D / revenue", pct(row.get("rnd_pct_revenue")))
    st.caption(
        "Operating leverage asks whether revenue growth is turning into better margins. "
        "R&D/revenue is especially useful for software, AI, medtech, and other innovation-heavy names."
    )

    st.markdown("#### Reinvestment And Dilution")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("SBC / revenue", pct(row.get("sbc_pct_revenue")))
    r2.metric("CapEx / revenue", pct(row.get("capex_to_revenue")))
    r3.metric("Reinvestment rate", pct(row.get("reinvestment_rate")))
    r4.metric("Buyback yield", pct(row.get("buyback_yield")))

    st.markdown("#### Business-Specific KPIs")
    summary_text = " ".join(
        str(row.get(col, "") or "").lower()
        for col in ("sector", "industry", "longBusinessSummary")
    )
    kpi_sets = [
        {
            "match": ("app", "subscription", "subscriber", "users", "marketplace", "platform", "software"),
            "label": "User / Subscription / Platform",
            "items": [
                ("Active users", "DAUs, MAUs, or customers show whether growth is real usage, not only pricing."),
                ("Paid conversion", "Free-to-paid conversion tells you if the product can monetize demand."),
                ("Retention / churn", "High retention makes growth compound; high churn means constant replacement spending."),
                ("ARPU", "Average revenue per user shows pricing power and upsell potential."),
            ],
        },
        {
            "match": ("medical", "device", "surgery", "orthopedic", "procedure", "hospital", "health care", "healthcare"),
            "label": "Medtech / Healthcare",
            "items": [
                ("Procedure volume", "Unit growth matters more than revenue if pricing or mix is moving around."),
                ("Installed base", "A growing installed base can create recurring instrument, implant, or service revenue."),
                ("Regulatory pipeline", "FDA approvals and trial milestones can change the growth runway quickly."),
                ("Gross margin by segment", "Mix shift can reveal whether new products are improving economics."),
            ],
        },
        {
            "match": ("industrial", "defense", "aerospace", "construction", "equipment", "machinery", "manufacturing"),
            "label": "Industrial / Defense / Equipment",
            "items": [
                ("Backlog", "Backlog shows future revenue already contracted or ordered."),
                ("Book-to-bill", "Orders above shipments suggest demand is building."),
                ("Capacity utilization", "Useful for spotting operating leverage before it appears in earnings."),
                ("Working capital", "Inventory and receivables can warn that growth is lower quality."),
            ],
        },
        {
            "match": ("retail", "restaurant", "store", "consumer", "ecommerce", "apparel"),
            "label": "Retail / Consumer",
            "items": [
                ("Same-store sales", "Separates true demand from new-store growth."),
                ("Traffic vs. ticket", "Shows whether growth is more customers or only higher prices."),
                ("Inventory turns", "Weak turns can signal markdown risk."),
                ("Customer acquisition cost", "Important when growth is driven by paid marketing."),
            ],
        },
        {
            "match": ("energy", "oil", "gas", "uranium", "mining", "materials", "chemical"),
            "label": "Energy / Materials",
            "items": [
                ("Production volume", "Separates commodity price tailwinds from actual output growth."),
                ("Realized pricing", "Shows what the company actually earns after hedges and contracts."),
                ("Reserve life", "Long-life assets can support durable cash flow."),
                ("Maintenance capex", "High sustaining capex can make reported FCF less durable."),
            ],
        },
        {
            "match": ("bank", "insurance", "financial", "lending", "credit"),
            "label": "Financials",
            "items": [
                ("Credit quality", "Charge-offs and delinquencies can move before earnings do."),
                ("Net interest margin", "Shows spread profitability for banks and lenders."),
                ("Loss ratio", "Core underwriting quality for insurers."),
                ("Capital ratio", "Balance-sheet strength matters more than standard industrial leverage metrics."),
            ],
        },
    ]

    selected_set = None
    for kpi_set in kpi_sets:
        if any(token in summary_text for token in kpi_set["match"]):
            selected_set = kpi_set
            break
    if selected_set is None:
        selected_set = {
            "label": "General Business Quality",
            "items": [
                ("Unit economics", "Look for the non-financial driver that creates revenue per customer, unit, or location."),
                ("Customer concentration", "A few large customers can make growth fragile."),
                ("Pricing power", "Check whether revenue growth comes from volume, price, or acquisitions."),
                ("Management targets", "Compare actual KPI progress against the company's own long-term targets."),
            ],
        }

    st.info(
        f"Detected category: {selected_set['label']}. These KPIs are not reliably available from Yahoo, "
        "so treat them as the next diligence layer after the financial screen."
    )
    checklist = pd.DataFrame(
        [{"KPI to verify": name, "Why it matters": reason} for name, reason in selected_set["items"]]
    )
    st.dataframe(checklist, use_container_width=True, hide_index=True)
    st.caption("Best sources: latest 10-Q/10-K, earnings presentation, shareholder letter, or earnings-call transcript.")


def render_stock_reader(row: pd.Series, key_prefix: str) -> None:
    symbol = row.get("symbol", "")
    name = row.get("shortName", "")
    quote = fetch_live_quote(str(symbol)) if symbol else {}
    display_price = quote.get("price") or metric_value(row, "currentPrice")
    st.subheader(f"{symbol} - {name}")
    st.caption(f"{row.get('sector', '-') or '-'} / {row.get('industry', '-') or '-'}")

    scores = score_breakdown(row)
    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Quote",
        f"${display_price:,.2f}" if display_price is not None else "-",
        f"{quote.get('change_pct'):.1%}" if quote.get("change_pct") is not None else None,
    )
    c2.metric("Score", f"{scores['overall_score']:.0f}")
    c3.metric("Market cap", money_short(row.get("marketCap")))
    c4, c5, c6 = st.columns(3)
    c4.metric("Rev growth", pct(row.get("revenueGrowth")))
    c5.metric("Fwd P/E", number(row.get("forwardPE")))
    c6.metric("Data", f"{data_quality(row):.0%}")

    overview_tab, chart_tab, kpi_tab, depth_tab, profile_tab = st.tabs(
        ["Overview", "Chart", "KPIs", "Analysis Depth", "Profile"]
    )
    with overview_tab:
        st.write(f"**Why it passed:** {quick_read(row)}")
        cautions = stock_cautions(row)
        if cautions:
            st.write(f"**Watch:** {'; '.join(cautions[:4])}")
        render_score_breakdown(row)
        render_quick_read(row)
    with chart_tab:
        render_price_chart(row, key_prefix, quote)
    with kpi_tab:
        render_kpi_groups(row)
    with depth_tab:
        render_analysis_depth(row)
    with profile_tab:
        summary = row.get("longBusinessSummary")
        if isinstance(summary, str) and summary.strip():
            st.write(summary)
        else:
            st.write("No business summary available for this ticker.")


def stock_label(row) -> str:
    name = row.shortName if isinstance(row.shortName, str) and row.shortName else ""
    return f"{row.symbol} - {name}" if name else str(row.symbol)


def render_idea_board(ranked: pd.DataFrame) -> str:
    if ranked.empty:
        return ""

    symbols = ranked["symbol"].dropna().astype(str).tolist()
    selected = st.session_state.get("top_idea_symbol")
    if selected not in symbols:
        selected = symbols[0]
        st.session_state["top_idea_symbol"] = selected

    st.caption("Click a ticker to open its stock reader.")
    for start in range(0, len(ranked), 3):
        cols = st.columns(3)
        for col, (_, row) in zip(cols, ranked.iloc[start:start + 3].iterrows()):
            symbol = str(row.get("symbol", ""))
            scores = score_breakdown(row)
            label = (
                f"{symbol}\n"
                f"{row.get('shortName', '')}\n"
                f"Score {scores['overall_score']:.0f} | "
                f"Rev {pct(row.get('revenueGrowth'))} | "
                f"P/E {number(row.get('forwardPE'))}"
            )
            button_type = "primary" if symbol == selected else "secondary"
            if col.button(label, key=f"top_idea_tile_{symbol}", use_container_width=True, type=button_type):
                st.session_state["top_idea_symbol"] = symbol
                selected = symbol

    return selected


def render_company_detail(hits: pd.DataFrame) -> None:
    if hits.empty:
        st.info("No passing stocks for the current filters.")
        return
    labels = [stock_label(row) for row in hits[["symbol", "shortName"]].itertuples(index=False)]
    selected_label = st.selectbox("Company", labels, key="company_reader_select")
    symbol = selected_label.split(" - ", 1)[0]
    row = hits[hits["symbol"].astype(str) == symbol].iloc[0]
    render_stock_reader(row, "company")


def render_score_breakdown(row: pd.Series) -> None:
    scores = score_breakdown(row)
    st.markdown("#### 0-100 Score")
    s1, s2, s3, s4, s5, s6, s7 = st.columns(7)
    s1.metric("Overall", f"{scores['overall_score']:.0f}",
              help="Weighted blend of all sub-scores below. 0–100, higher is better.")
    s2.metric("Projection", f"{scores['projection_score']:.0f}",
              help="Forward-looking: durable growth × ROIC × FCF yield × margin trajectory. "
                   "Estimates 5-year compounding power.")
    s3.metric("Growth", f"{scores['growth_score']:.0f}",
              help="Revenue growth, EPS growth, 3Y CAGR, Rule of 40, margin trends.")
    s4.metric("Quality", f"{scores['quality_score']:.0f}",
              help="Gross/op/net/FCF margins, ROIC, ROIC vs WACC, ROE, accruals, SBC.")
    s5.metric("Value", f"{scores['valuation_score']:.0f}",
              help="Forward P/E, trailing P/E, EV/FCF, EV/EBITDA, P/S, P/B, PEG.")
    s6.metric("Balance", f"{scores['balance_score']:.0f}",
              help="Debt/equity, net debt/FCF, coverage, current & quick ratios, goodwill.")
    s7.metric("Momentum", f"{scores['momentum_score']:.0f}",
              help="Position vs 52w range, beta sweet spot, short float, insider ownership.")
    st.caption(
        "**Overall** = 18% Growth · 26% Quality · 16% Valuation · 10% Balance · "
        "8% Momentum · 18% Projection · 4% Data coverage. Built for ranking ideas, not as a price target."
    )


def render_top_ideas(hits: pd.DataFrame) -> None:
    if hits.empty:
        st.info("No passing stocks for the current filters.")
        return

    st.markdown("#### Top Ideas")
    ranked = hits.head(min(25, len(hits))).copy()
    symbol = render_idea_board(ranked)
    row = ranked[ranked["symbol"].astype(str) == symbol].iloc[0]

    st.divider()
    render_stock_reader(row, "top_ideas")

    with st.expander("Ranked idea list", expanded=False):
        st.dataframe(
            format_table(ranked),
            use_container_width=True,
            hide_index=True,
        )


def main() -> None:
    st.set_page_config(page_title="Stock Screener", page_icon="$", layout="wide")
    st.title("Stock Screener")

    version = data_version()
    full = load_latest_data(version)
    if full.empty:
        st.error("No screener data found. Run `python screener.py` once to fetch data.")
        return

    raw = full.copy()
    full = add_momentum_columns(clean_for_filters(raw))
    failed = len(raw) - len(full)
    st.caption(f"Latest data: {latest_timestamp(raw)}")
    if failed:
        st.caption(
            f"{failed:,} loaded symbols were skipped because Yahoo returned no usable fundamentals or price data."
        )
    if st.button("Refresh data"):
        load_latest_data.clear()
        st.rerun()

    render_full_refresh_controls()

    price_col, note_col = st.columns([1, 3])
    with price_col:
        refresh_prices = st.button("Refresh delayed prices")
    with note_col:
        st.caption(
            "Fundamentals update when you run `python screener.py`. "
            "Delayed prices can be refreshed here and may be delayed by Yahoo."
        )

    if refresh_prices:
        symbols = tuple(full["symbol"].dropna().astype(str).unique().tolist())
        with st.spinner(f"Fetching delayed prices for {len(symbols):,} screenable symbols..."):
            live_prices = fetch_delayed_prices(symbols)
        st.session_state["live_prices"] = live_prices
        st.session_state["live_price_loaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.toast(f"Updated delayed prices for {len(live_prices):,} symbols.")

    if "live_prices" in st.session_state:
        live_prices = st.session_state["live_prices"]
        full = apply_live_prices(full, live_prices)
        if not live_prices.empty:
            asof = live_prices["live_price_asof"].dropna().max()
            loaded = st.session_state.get("live_price_loaded_at", "unknown")
            st.caption(f"Using refreshed delayed prices for {len(live_prices):,} symbols. Price date: {asof}. Loaded: {loaded}.")

    # Sidebar order: Universe (sectors) → Filters (sector-aware) → Theme/search
    selected_sectors = pick_sectors_sidebar(full)
    filters = build_filter_controls(selected_sectors)
    scoped = filter_by_sector_and_search(full, selected_sectors)
    if st.session_state.get("_show_all", False):
        hits = scoped.copy()
    else:
        hits = screener.apply_filters(
            scoped,
            verbose=False,
            filters=filters,
            strict_optional=st.session_state.get("_strict_optional", False),
        )
    hits = rank_candidates(hits)

    render_metric_row(raw, full, scoped, hits)

    # Show filter funnel when hits are low — helps the user understand WHY
    if len(hits) < 20 or st.session_state.get("show_funnel", False):
        with st.expander("🔎 Filter funnel — see which filter is rejecting the most stocks",
                         expanded=len(hits) == 0):
            render_filter_funnel(scoped, filters)

    tab_ideas, tab_screen, tab_sector, tab_detail, tab_data = st.tabs(
        ["Top Ideas", "Screen", "Sectors", "Company", "All Selected"]
    )

    with tab_ideas:
        render_top_ideas(hits)

    with tab_screen:
        focus = st.session_state.get("_focus_sector")

        # Diversification tracker: only meaningful when not in single-sector focus
        if not focus and not hits.empty and "sector" in hits.columns:
            sector_dist = (
                hits["sector"].fillna("(unknown)").value_counts()
            )
            if len(sector_dist) > 1:
                st.markdown("**Sector distribution of passing stocks** — concentration check")
                st.bar_chart(sector_dist, height=160)

        if focus and focus in SECTOR_CONFIG and not hits.empty:
            cfg = SECTOR_CONFIG[focus]
            # Identifier columns + active sector metrics
            id_cols = ["symbol", "shortName", "sector", "marketCap", "currentPrice",
                       "overall_score", "projection_score"]
            metric_cols = [METRIC_TO_COLUMN.get(k) for k in cfg["metrics"]]
            metric_cols = [c for c in metric_cols if c]
            want = id_cols + metric_cols
            enriched = enrich_for_display(hits)
            cols = [c for c in want if c in enriched.columns]
            table = enriched[cols].copy()
            sort_key = cfg["primary_sort"]
            if sort_key in table.columns:
                table = table.sort_values(sort_key, ascending=False, na_position="last")
            top = table.head(50)
            st.caption(
                f"Showing top **{len(top)}** of {len(table)} passing {focus} stocks, "
                f"sorted by **{sort_key}** ↓. Columns trimmed to sector-relevant KPIs."
            )
            st.dataframe(top, use_container_width=True, hide_index=True)
            csv = table.to_csv(index=False).encode("utf-8")
        else:
            full_table = format_table(hits)
            top = full_table.head(50)
            if len(full_table) > 50:
                st.caption(f"Showing top **50** of {len(full_table)} passing stocks (by overall_score).")
            st.dataframe(top, use_container_width=True, hide_index=True)
            csv = full_table.to_csv(index=False).encode("utf-8")

        st.download_button(
            "Download passing stocks (full list, CSV)",
            data=csv,
            file_name=f"screened_stocks_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )

    with tab_sector:
        if hits.empty:
            st.info("No passing stocks to chart.")
        else:
            import altair as alt

            sector_counts = (
                hits["sector"]
                .fillna("(unknown)")
                .value_counts()
                .rename_axis("sector")
                .reset_index(name="count")
            )
            st.markdown("**Passing stocks by sector**")
            sector_chart = (
                alt.Chart(sector_counts)
                .mark_bar(color="#1f77b4")
                .encode(
                    y=alt.Y("sector:N", sort="-x", title=None),
                    x=alt.X("count:Q", title="Number of stocks",
                            axis=alt.Axis(tickMinStep=1)),
                    tooltip=[
                        alt.Tooltip("sector:N", title="Sector"),
                        alt.Tooltip("count:Q", title="Stocks"),
                    ],
                )
                .properties(height=max(180, 28 * len(sector_counts)))
            )
            st.altair_chart(sector_chart, use_container_width=True)

            st.markdown("**Growth vs. valuation** — bubble size = market cap")
            scatter_cols = ["revenueGrowth", "forwardPE", "valuation_score", "marketCap", "symbol", "sector"]
            chart_data = hits[[c for c in scatter_cols if c in hits.columns]].copy()
            for col in ("revenueGrowth", "forwardPE", "marketCap"):
                if col in chart_data.columns:
                    chart_data[col] = pd.to_numeric(chart_data[col], errors="coerce")
            chart_data = chart_data.replace([float("inf"), float("-inf")], pd.NA)
            chart_data = chart_data.dropna(subset=["revenueGrowth", "forwardPE", "marketCap"])
            chart_data = chart_data[(chart_data["forwardPE"] > 0) & (chart_data["forwardPE"] < 200)]
            if chart_data.empty:
                st.info("Not enough complete valuation and growth data for the scatter chart.")
            else:
                scatter = (
                    alt.Chart(chart_data)
                    .mark_circle(opacity=0.65)
                    .encode(
                        x=alt.X("forwardPE:Q", title="Forward P/E",
                                scale=alt.Scale(domainMin=0, nice=True)),
                        y=alt.Y("revenueGrowth:Q", title="Revenue growth (YoY)",
                                axis=alt.Axis(format=".0%")),
                        size=alt.Size("marketCap:Q", title="Market cap",
                                      scale=alt.Scale(range=[40, 900]),
                                      legend=alt.Legend(format="~s")),
                        color=alt.Color("sector:N", title="Sector"),
                        tooltip=[
                            alt.Tooltip("symbol:N", title="Symbol"),
                            alt.Tooltip("sector:N", title="Sector"),
                            alt.Tooltip("forwardPE:Q", title="Fwd P/E", format=".1f"),
                            alt.Tooltip("revenueGrowth:Q", title="Rev growth", format=".1%"),
                            alt.Tooltip("marketCap:Q", title="Market cap", format="$~s"),
                        ],
                    )
                    .properties(height=460)
                    .interactive()
                )
                st.altair_chart(scatter, use_container_width=True)

    with tab_detail:
        render_company_detail(hits)

    with tab_data:
        st.dataframe(format_table(scoped), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
