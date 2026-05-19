"""
Interactive stock screener site.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import screener


ROOT = Path(__file__).parent
DB_FILE = ROOT / "screener_data.db"
CACHE_FILE = ROOT / "_fetch_cache_v5_edgar.parquet"

DISPLAY_COLUMNS = [
    "symbol", "shortName", "sector", "industry", "currentPrice", "marketCap",
    "value_score", "revenueGrowth", "rev_cagr_3y", "earningsGrowth",
    "grossMargins", "profitMargins", "fcf_margin", "fcf_conversion",
    "roic", "roic_wacc_spread", "op_margin_latest", "op_margin_trend",
    "accrual_ratio", "debtToEquity", "net_debt_to_fcf", "interest_coverage",
    "forwardPE", "ev_to_fcf", "ev_to_ebitda", "shortPercentOfFloat",
    "heldPercentInsiders", "pct_below_52w_high", "pct_above_52w_low",
    "sec_backlog_to_rev", "longBusinessSummary",
]

PRESETS = {
    "Balanced winners": {
        "revenue_growth_min": 0.15,
        "forward_pe_max": 50.0,
        "gross_margin_min": 0.20,
        "accrual_ratio_max": 0.15,
    },
    "Quality compounders": {
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
    "High-growth operators": {
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
    "Pullback candidates": {
        "revenue_growth_min": 0.10,
        "forward_pe_max": 55.0,
        "beta_min": 0.8,
        "beta_max": 2.8,
        "min_pct_below_52w_high": 0.15,
        "max_pct_above_52w_low": 1.25,
        "fcf_margin_min": 0.00,
        "accrual_ratio_max": 0.12,
    },
}


@st.cache_data(show_spinner=False)
def load_latest_data() -> pd.DataFrame:
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


def clean_for_filters(df: pd.DataFrame) -> pd.DataFrame:
    if "error" in df.columns:
        df = df[df["error"].isna() | (df["error"].astype(str).str.len() == 0)]
    return df.copy()


def number_filter(label: str, value, step: float, help_text: str | None = None):
    if value is None:
        enabled = st.checkbox(label, value=False, help=help_text)
        if not enabled:
            return None
        value = 0.0
    return st.number_input(label, value=float(value), step=step, help=help_text)


def build_filter_controls() -> dict:
    st.sidebar.header("KPI Filters")
    preset_name = st.sidebar.selectbox("Preset", list(PRESETS), index=0)
    filters = screener.FILTERS.copy()
    filters.update(PRESETS[preset_name])

    with st.sidebar.expander("Core", expanded=True):
        filters["revenue_growth_min"] = st.slider(
            "Revenue growth min", -0.20, 1.00, float(filters["revenue_growth_min"]), 0.01
        )
        filters["forward_pe_max"] = st.slider(
            "Forward P/E max", 5.0, 120.0, float(filters["forward_pe_max"]), 1.0
        )
        filters["gross_margin_min"] = st.slider(
            "Gross margin min", -0.20, 0.90, float(filters["gross_margin_min"]), 0.01
        )
        filters["market_cap_min"] = st.number_input(
            "Market cap min", value=float(filters["market_cap_min"]), step=100_000_000.0
        )
        filters["profit_margin_min"] = number_filter("Profit margin min", filters["profit_margin_min"], 0.01)
        filters["earnings_growth_min"] = number_filter("EPS growth min", filters["earnings_growth_min"], 0.01)
        filters["return_on_equity_min"] = number_filter("ROE min", filters["return_on_equity_min"], 0.01)

    with st.sidebar.expander("Risk and Momentum", expanded=True):
        beta_min, beta_max = st.slider(
            "Beta range",
            0.0,
            4.0,
            (float(filters["beta_min"]), float(filters["beta_max"])),
            0.1,
        )
        filters["beta_min"] = beta_min
        filters["beta_max"] = beta_max
        filters["debt_to_equity_max"] = st.slider(
            "Debt/equity max", 0.0, 500.0, float(filters["debt_to_equity_max"]), 5.0
        )
        filters["max_pct_above_52w_low"] = st.slider(
            "Max above 52w low", 0.0, 3.0, float(filters["max_pct_above_52w_low"]), 0.05
        )
        filters["min_pct_below_52w_high"] = st.slider(
            "Min below 52w high", 0.0, 0.80, float(filters["min_pct_below_52w_high"]), 0.01
        )
        filters["short_float_max"] = number_filter("Short float max", filters["short_float_max"], 0.01)
        filters["insider_ownership_min"] = number_filter(
            "Insider ownership min", filters["insider_ownership_min"], 0.01
        )

    with st.sidebar.expander("Quality", expanded=False):
        filters["fcf_margin_min"] = number_filter("FCF margin min", filters["fcf_margin_min"], 0.01)
        filters["fcf_conversion_min"] = number_filter("FCF conversion min", filters["fcf_conversion_min"], 0.05)
        filters["accrual_ratio_max"] = number_filter("Accrual ratio max", filters["accrual_ratio_max"], 0.01)
        filters["roic_min"] = number_filter("ROIC min", filters["roic_min"], 0.01)
        filters["roic_wacc_spread_min"] = number_filter(
            "ROIC-WACC spread min", filters["roic_wacc_spread_min"], 0.01
        )
        filters["gross_margin_trend_min"] = number_filter(
            "Gross margin trend min", filters["gross_margin_trend_min"], 0.01
        )
        filters["op_margin_min"] = number_filter("Operating margin min", filters["op_margin_min"], 0.01)
        filters["op_margin_trend_min"] = number_filter(
            "Operating margin trend min", filters["op_margin_trend_min"], 0.01
        )
        filters["interest_coverage_min"] = number_filter(
            "Interest coverage min", filters["interest_coverage_min"], 0.5
        )
        filters["current_ratio_min"] = number_filter("Current ratio min", filters["current_ratio_min"], 0.1)
        filters["net_debt_to_fcf_max"] = number_filter("Net debt / FCF max", filters["net_debt_to_fcf_max"], 0.5)
        filters["goodwill_pct_assets_max"] = number_filter(
            "Goodwill/assets max", filters["goodwill_pct_assets_max"], 0.05
        )
        filters["sbc_pct_revenue_max"] = number_filter("SBC/revenue max", filters["sbc_pct_revenue_max"], 0.01)

    with st.sidebar.expander("Growth Durability and Valuation", expanded=False):
        filters["rule_of_40_min"] = number_filter("Rule of 40 min", filters["rule_of_40_min"], 0.05)
        filters["rev_cagr_3y_min"] = number_filter("3Y revenue CAGR min", filters["rev_cagr_3y_min"], 0.01)
        filters["ev_to_fcf_max"] = number_filter("EV/FCF max", filters["ev_to_fcf_max"], 1.0)
        filters["ev_to_ebitda_max"] = number_filter("EV/EBITDA max", filters["ev_to_ebitda_max"], 1.0)
        filters["capex_to_revenue_max"] = number_filter(
            "CapEx/revenue max", filters["capex_to_revenue_max"], 0.01
        )
        filters["sec_backlog_to_rev_min"] = number_filter(
            "Backlog/revenue min", filters["sec_backlog_to_rev_min"], 0.1
        )

    return filters


def filter_by_sector_and_search(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Universe")
    sectors = sorted(df.get("sector", pd.Series(dtype=str)).dropna().astype(str).unique())
    selected = st.sidebar.multiselect("Sectors", sectors, default=[])
    query = st.sidebar.text_input("Ticker, company, or industry")

    out = df.copy()
    if selected:
        out = out[out["sector"].isin(selected)]
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
    cols = [c for c in DISPLAY_COLUMNS if c in df.columns]
    out = df[cols].copy()
    return out


def render_metric_row(full: pd.DataFrame, scoped: pd.DataFrame, hits: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Universe", f"{len(full):,}")
    c2.metric("Selected", f"{len(scoped):,}")
    c3.metric("Passing", f"{len(hits):,}")
    pass_rate = len(hits) / len(scoped) if len(scoped) else 0
    c4.metric("Pass rate", f"{pass_rate:.1%}")


def render_company_detail(hits: pd.DataFrame) -> None:
    if hits.empty:
        st.info("No passing stocks for the current filters.")
        return
    symbols = hits["symbol"].astype(str).tolist()
    symbol = st.selectbox("Company", symbols)
    row = hits[hits["symbol"].astype(str) == symbol].iloc[0]

    st.subheader(f"{row.get('symbol', '')} - {row.get('shortName', '')}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Market cap", money_short(row.get("marketCap")))
    c2.metric("Revenue growth", pct(row.get("revenueGrowth")))
    c3.metric("ROIC", pct(row.get("roic")))
    c4.metric("Forward P/E", number(row.get("forwardPE")))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("FCF margin", pct(row.get("fcf_margin")))
    c6.metric("ROIC-WACC", pct(row.get("roic_wacc_spread")))
    c7.metric("EV/FCF", number(row.get("ev_to_fcf")))
    c8.metric("Off 52w high", pct(row.get("pct_below_52w_high")))

    summary = row.get("longBusinessSummary")
    if isinstance(summary, str) and summary.strip():
        st.write(summary)


def main() -> None:
    st.set_page_config(page_title="Stock Screener", page_icon="$", layout="wide")
    st.title("Stock Screener")

    full = load_latest_data()
    if full.empty:
        st.error("No screener data found. Run `python screener.py` once to fetch data.")
        return

    full = add_momentum_columns(clean_for_filters(full))
    st.caption(f"Latest data: {latest_timestamp(full)}")

    filters = build_filter_controls()
    scoped = filter_by_sector_and_search(full)
    hits = screener.apply_filters(scoped, verbose=False, filters=filters)

    render_metric_row(full, scoped, hits)

    tab_screen, tab_sector, tab_detail, tab_data = st.tabs(
        ["Screen", "Sectors", "Company", "All Selected"]
    )

    with tab_screen:
        st.dataframe(
            format_table(hits),
            use_container_width=True,
            hide_index=True,
        )
        csv = format_table(hits).to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download passing stocks",
            data=csv,
            file_name=f"screened_stocks_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv",
        )

    with tab_sector:
        if hits.empty:
            st.info("No passing stocks to chart.")
        else:
            sector_counts = (
                hits["sector"]
                .fillna("(unknown)")
                .value_counts()
                .rename_axis("sector")
                .reset_index(name="count")
            )
            st.bar_chart(sector_counts)
            scatter_cols = ["revenueGrowth", "forwardPE", "value_score", "marketCap", "symbol", "sector"]
            chart_data = hits[[c for c in scatter_cols if c in hits.columns]].copy()
            for col in ("revenueGrowth", "forwardPE", "marketCap"):
                if col in chart_data.columns:
                    chart_data[col] = pd.to_numeric(chart_data[col], errors="coerce")
            chart_data = chart_data.replace([float("inf"), float("-inf")], pd.NA)
            chart_data = chart_data.dropna(subset=["revenueGrowth", "forwardPE", "marketCap"])
            if chart_data.empty:
                st.info("Not enough complete valuation and growth data for the scatter chart.")
            else:
                st.scatter_chart(
                    chart_data,
                    x="forwardPE",
                    y="revenueGrowth",
                    size="marketCap",
                    color="sector",
                )

    with tab_detail:
        render_company_detail(hits)

    with tab_data:
        st.dataframe(format_table(scoped), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
