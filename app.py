"""Streamlit front end for the order / execution reconciliation.

Run with:  streamlit run app.py
"""
from pathlib import Path

import pandas as pd
import streamlit as st

from reconciliation import reconcile, summarize

DATA_DIR = Path(__file__).resolve().parent / "data"
STATUSES = ["Filled", "Partial", "No fill", "Overfill"]
ORDER_COLUMNS = [
    "order_id", "timestamp", "client_id", "symbol", "instrument_type", "side",
    "quantity", "limit_price", "executed_qty", "remaining_qty", "fill_pct",
    "avg_price", "n_executions", "status", "exception_count",
]

st.set_page_config(page_title="Trading Desk Daily Monitor", layout="wide")
st.title("Trading Desk Daily Monitor")
st.caption("Order and execution reconciliation. Synthetic data, no real system connected.")


def read_inputs():
    """Return (orders, executions, source label)."""
    with st.sidebar:
        st.header("Data")
        orders_file = st.file_uploader("Orders CSV", type="csv")
        executions_file = st.file_uploader("Executions CSV", type="csv")
        if bool(orders_file) != bool(executions_file):
            st.warning("Upload both files to use your own data.")
        st.caption("Without an upload, the demo files in data/ are used.")

    if orders_file and executions_file:
        return pd.read_csv(orders_file), pd.read_csv(executions_file), "uploaded files"

    orders_path = DATA_DIR / "orders.csv"
    executions_path = DATA_DIR / "executions.csv"
    if not (orders_path.exists() and executions_path.exists()):
        st.error("Demo data not found. Run `python generate_data.py` first.")
        st.stop()
    return pd.read_csv(orders_path), pd.read_csv(executions_path), "demo data"


try:
    orders_raw, executions_raw, source = read_inputs()
    result = reconcile(orders_raw, executions_raw)
except (ValueError, KeyError, pd.errors.ParserError, pd.errors.EmptyDataError) as err:
    st.error(f"Could not process the files: {err}")
    st.stop()

orders, exceptions = result.orders, result.exceptions
summary = summarize(result)
st.caption(f"Source: {source}")

# Indicators
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Client orders", summary["orders"])
k2.metric("Fully filled", summary["filled"])
k3.metric("Partial / no fill", summary["partial"] + summary["no_fill"])
k4.metric("Orders to review", summary["orders_to_review"])
k5.metric("Match rate", f"{summary['match_rate']:.1%}")

tab_orders, tab_exceptions = st.tabs(["Orders", f"Exceptions ({summary['exceptions']})"])

# Orders tab
with tab_orders:
    f1, f2, f3, f4 = st.columns(4)
    status_sel = f1.multiselect("Status", STATUSES, default=STATUSES)
    if "instrument_type" in orders.columns:
        types = sorted(orders["instrument_type"].dropna().unique())
        type_sel = f2.multiselect("Instrument type", types, default=types)
    else:
        type_sel = None
    side_sel = f3.multiselect("Side", ["BUY", "SELL"], default=["BUY", "SELL"])
    search = f4.text_input("Symbol or order ID contains")
    review_only = st.checkbox("Only orders with an exception")

    view = orders[orders["status"].isin(status_sel) & orders["side"].isin(side_sel)]
    if type_sel is not None:
        view = view[view["instrument_type"].isin(type_sel)]
    if search:
        hit = view["symbol"].astype(str).str.contains(search, case=False, regex=False)
        hit |= view["order_id"].astype(str).str.contains(search, case=False, regex=False)
        view = view[hit]
    if review_only:
        view = view[view["needs_review"]]

    columns = [c for c in ORDER_COLUMNS if c in view.columns]
    table = view[columns].round({"avg_price": 4})
    st.write(f"{len(table)} order(s) shown")
    st.dataframe(table, hide_index=True)
    st.download_button(
        "Download these orders (CSV)",
        table.to_csv(index=False).encode("utf-8"),
        file_name="orders_filtered.csv",
        mime="text/csv",
    )

# Exceptions tab
with tab_exceptions:
    if exceptions.empty:
        st.success("No exception detected.")
    else:
        st.bar_chart(exceptions["check"].value_counts())
        checks = sorted(exceptions["check"].unique())
        check_sel = st.multiselect("Check", checks, default=checks)
        exc_view = exceptions[exceptions["check"].isin(check_sel)]
        st.dataframe(exc_view, hide_index=True)
        st.download_button(
            "Download these exceptions (CSV)",
            exc_view.to_csv(index=False).encode("utf-8"),
            file_name="exceptions.csv",
            mime="text/csv",
        )

        if not exc_view.empty:
            st.subheader("Order detail")
            order_id = st.selectbox("Order", sorted(exc_view["order_id"].unique()))
            match = orders[orders["order_id"] == order_id]
            if match.empty:
                st.info(f"{order_id} does not exist in the orders file.")
            else:
                st.dataframe(match[[c for c in ORDER_COLUMNS if c in match.columns]], hide_index=True)
            st.write("Executions on file for this order")
            st.dataframe(
                executions_raw[executions_raw["order_id"] == order_id], hide_index=True
            )
