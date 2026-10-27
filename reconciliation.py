"""Order / execution reconciliation engine.

Takes client orders (OMS side) and executions (EMS side) and returns:
  - one status line per order: executed quantity, remaining quantity,
    volume-weighted average price, status
  - a list of exceptions a trader should look at

Order statuses (Filled, Partial, No fill) are follow-up states, not errors.
Only the five checks below are reported as exceptions.

Conventions
  - Duplicated execution IDs are counted once (first occurrence kept).
  - Executions whose order does not exist are not attached to any order.
  - Executions with a side/symbol inconsistent with their order are still
    counted in the order's totals, and flagged as DATA_MISMATCH.
  - Orders without a limit price (market orders) skip the limit check.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ORPHAN = "ORPHAN_EXECUTION"
DUPLICATE = "DUPLICATE_EXECUTION"
OVERFILL = "OVERFILL"
MISMATCH = "DATA_MISMATCH"
LIMIT = "LIMIT_BREACH"

ORDER_COLUMNS = ["order_id", "symbol", "side", "quantity", "limit_price"]
EXECUTION_COLUMNS = ["execution_id", "order_id", "quantity", "price"]
EXCEPTION_COLUMNS = ["check", "order_id", "execution_id", "detail"]

TOLERANCE = 1e-9


@dataclass
class ReconciliationResult:
    orders: pd.DataFrame      # one row per order, with status
    exceptions: pd.DataFrame  # one row per exception


def load_data(orders_path, executions_path):
    return pd.read_csv(orders_path), pd.read_csv(executions_path)


def _check_columns(df, required, name):
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: missing column(s): {', '.join(missing)}")


def _prepare(orders, executions):
    _check_columns(orders, ORDER_COLUMNS, "orders")
    _check_columns(executions, EXECUTION_COLUMNS, "executions")
    orders = orders.copy()
    executions = executions.copy()

    if orders["order_id"].duplicated().any():
        raise ValueError("orders: order_id must be unique")

    for df in (orders, executions):
        for col in ("side", "symbol"):
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip().str.upper()

    orders["quantity"] = pd.to_numeric(orders["quantity"])
    orders["limit_price"] = pd.to_numeric(orders["limit_price"], errors="coerce")
    executions["quantity"] = pd.to_numeric(executions["quantity"])
    executions["price"] = pd.to_numeric(executions["price"])
    return orders, executions


def _to_exceptions(check, df, detail_fn):
    if df.empty:
        return pd.DataFrame(columns=EXCEPTION_COLUMNS)
    if "execution_id" in df.columns:
        execution_ids = df["execution_id"].to_numpy()
    else:
        execution_ids = [None] * len(df)
    return pd.DataFrame(
        {
            "check": check,
            "order_id": df["order_id"].to_numpy(),
            "execution_id": execution_ids,
            "detail": [detail_fn(r) for r in df.to_dict("records")],
        }
    )


def _whole_numbers(series):
    """Show 750 instead of 750.0 when every value is a whole number."""
    if (series % 1 == 0).all():
        return series.astype("int64")
    return series


def reconcile(orders: pd.DataFrame, executions: pd.DataFrame) -> ReconciliationResult:
    orders, executions = _prepare(orders, executions)
    exception_frames = []

    # 1. Technical duplicates: same execution_id more than once.
    dup_rows = executions[executions.duplicated("execution_id", keep=False)]
    dups = (
        dup_rows.groupby("execution_id")
        .agg(order_id=("order_id", "first"), n=("quantity", "size"))
        .reset_index()
    )
    exception_frames.append(
        _to_exceptions(DUPLICATE, dups, lambda r: f"Execution ID appears {r['n']} times")
    )
    clean = executions.drop_duplicates("execution_id", keep="first")

    # 2. Orphan executions: no matching client order.
    orphans = clean[~clean["order_id"].isin(orders["order_id"])]
    exception_frames.append(
        _to_exceptions(
            ORPHAN,
            orphans,
            lambda r: f"No client order {r['order_id']} for {r['quantity']} @ {r['price']}",
        )
    )

    # Attach each execution to its order.
    order_ref = orders[["order_id", "symbol", "side", "limit_price"]].rename(
        columns={"symbol": "order_symbol", "side": "order_side"}
    )
    matched = clean.merge(order_ref, on="order_id", how="inner")

    # 3. Data mismatch: execution side or symbol differs from its order.
    mismatch_mask = pd.Series(False, index=matched.index)
    if "side" in matched.columns:
        mismatch_mask |= matched["side"] != matched["order_side"]
    if "symbol" in matched.columns:
        mismatch_mask |= matched["symbol"] != matched["order_symbol"]

    def mismatch_detail(r):
        parts = []
        if "side" in r and r["side"] != r["order_side"]:
            parts.append(f"side {r['side']} vs order {r['order_side']}")
        if "symbol" in r and r["symbol"] != r["order_symbol"]:
            parts.append(f"symbol {r['symbol']} vs order {r['order_symbol']}")
        return "; ".join(parts)

    exception_frames.append(_to_exceptions(MISMATCH, matched[mismatch_mask], mismatch_detail))

    # 4. Limit price: a BUY above its limit, or a SELL below its limit.
    buy_breach = (matched["order_side"] == "BUY") & (
        matched["price"] > matched["limit_price"] + TOLERANCE
    )
    sell_breach = (matched["order_side"] == "SELL") & (
        matched["price"] < matched["limit_price"] - TOLERANCE
    )
    breach = matched[matched["limit_price"].notna() & (buy_breach | sell_breach)]
    exception_frames.append(
        _to_exceptions(
            LIMIT,
            breach,
            lambda r: f"{r['order_side']} executed at {r['price']} vs limit {r['limit_price']}",
        )
    )

    # Aggregate executions per order.
    matched = matched.assign(notional=matched["quantity"] * matched["price"])
    agg = (
        matched.groupby("order_id")
        .agg(
            executed_qty=("quantity", "sum"),
            n_executions=("execution_id", "count"),
            notional=("notional", "sum"),
        )
        .reset_index()
    )
    status = orders.merge(agg, on="order_id", how="left")
    for col in ("executed_qty", "n_executions", "notional"):
        status[col] = status[col].fillna(0)

    status["remaining_qty"] = (status["quantity"] - status["executed_qty"]).clip(lower=0)
    status["fill_pct"] = (status["executed_qty"] / status["quantity"] * 100).round(1)
    status["avg_price"] = (status["notional"] / status["executed_qty"]).where(
        status["executed_qty"] > 0
    )
    status["status"] = np.select(
        [
            status["executed_qty"] == 0,
            status["executed_qty"] > status["quantity"],
            status["executed_qty"] == status["quantity"],
        ],
        ["No fill", "Overfill", "Filled"],
        default="Partial",
    )
    status = status.drop(columns="notional")
    status["n_executions"] = status["n_executions"].astype("int64")
    status["executed_qty"] = _whole_numbers(status["executed_qty"])
    status["remaining_qty"] = _whole_numbers(status["remaining_qty"])

    # 5. Overfill: executed more than ordered.
    overfills = status[status["status"] == "Overfill"]
    exception_frames.append(
        _to_exceptions(
            OVERFILL,
            overfills,
            lambda r: f"Executed {r['executed_qty']} for an order of {r['quantity']} ({r['fill_pct']}%)",
        )
    )

    exceptions = pd.concat(
        [f for f in exception_frames if not f.empty], ignore_index=True
    ) if any(not f.empty for f in exception_frames) else pd.DataFrame(columns=EXCEPTION_COLUMNS)

    counts = exceptions.groupby("order_id").size()
    status["exception_count"] = status["order_id"].map(counts).fillna(0).astype("int64")
    status["needs_review"] = status["exception_count"] > 0

    return ReconciliationResult(orders=status, exceptions=exceptions)


def summarize(result: ReconciliationResult) -> dict:
    o, e = result.orders, result.exceptions
    total = len(o)
    counts = o["status"].value_counts()
    clean_filled = int(((o["status"] == "Filled") & ~o["needs_review"]).sum())
    return {
        "orders": total,
        "filled": int(counts.get("Filled", 0)),
        "partial": int(counts.get("Partial", 0)),
        "no_fill": int(counts.get("No fill", 0)),
        "overfill": int(counts.get("Overfill", 0)),
        "orders_to_review": int(o["needs_review"].sum()),
        "exceptions": len(e),
        "exceptions_by_check": e["check"].value_counts().to_dict(),
        # share of orders that are fully filled with no exception attached
        "match_rate": clean_filled / total if total else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(description="Reconcile client orders with executions.")
    parser.add_argument("--orders", default="data/orders.csv")
    parser.add_argument("--executions", default="data/executions.csv")
    parser.add_argument("--out", help="folder where order_status.csv and exceptions.csv are written")
    args = parser.parse_args()

    start = time.perf_counter()
    orders, executions = load_data(args.orders, args.executions)
    result = reconcile(orders, executions)
    elapsed = time.perf_counter() - start

    s = summarize(result)
    print(f"Orders processed : {s['orders']}")
    print(f"Filled           : {s['filled']}")
    print(f"Partial          : {s['partial']}")
    print(f"No fill          : {s['no_fill']}")
    print(f"Overfill         : {s['overfill']}")
    print(f"Orders to review : {s['orders_to_review']}")
    print(f"Match rate       : {s['match_rate']:.1%}")
    print(f"Run time         : {elapsed * 1000:.0f} ms")
    if s["exceptions"]:
        print(f"\n{s['exceptions']} exception(s):")
        print(result.exceptions.to_string(index=False))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        result.orders.to_csv(out / "order_status.csv", index=False)
        result.exceptions.to_csv(out / "exceptions.csv", index=False)


if __name__ == "__main__":
    main()
