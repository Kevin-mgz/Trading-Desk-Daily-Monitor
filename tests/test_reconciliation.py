import math

import pandas as pd

from generate_data import generate
from reconciliation import (
    DUPLICATE,
    LIMIT,
    MISMATCH,
    ORPHAN,
    OVERFILL,
    reconcile,
    summarize,
)


def make_orders(*rows):
    return pd.DataFrame(rows, columns=["order_id", "symbol", "side", "quantity", "limit_price"])


def make_executions(*rows):
    return pd.DataFrame(
        rows, columns=["execution_id", "order_id", "symbol", "side", "quantity", "price"]
    )


def checks(result):
    return sorted(result.exceptions["check"].tolist())


def order_row(result, order_id):
    return result.orders[result.orders["order_id"] == order_id].iloc[0]


def test_partial_fill_is_a_status_not_an_exception():
    orders = make_orders(("ORD-1", "AAPL", "BUY", 1000, 275.0))
    execs = make_executions(
        ("EX-1", "ORD-1", "AAPL", "BUY", 400, 274.80),
        ("EX-2", "ORD-1", "AAPL", "BUY", 350, 274.90),
    )
    result = reconcile(orders, execs)
    row = order_row(result, "ORD-1")
    assert row["executed_qty"] == 750
    assert row["remaining_qty"] == 250
    # (400 * 274.80 + 350 * 274.90) / 750 = 274.8467, i.e. 274.85 rounded
    assert math.isclose(row["avg_price"], 206135 / 750, abs_tol=1e-9)
    assert round(row["avg_price"], 2) == 274.85
    assert row["status"] == "Partial"
    assert result.exceptions.empty


def test_fully_filled_order_is_clean():
    orders = make_orders(("ORD-1", "NVDA", "SELL", 500, 190.0))
    execs = make_executions(("EX-1", "ORD-1", "NVDA", "SELL", 500, 190.20))
    result = reconcile(orders, execs)
    assert order_row(result, "ORD-1")["status"] == "Filled"
    assert result.exceptions.empty
    assert summarize(result)["match_rate"] == 1.0


def test_order_without_execution_is_no_fill_not_an_exception():
    orders = make_orders(("ORD-1", "SPY", "BUY", 200, 700.0))
    execs = make_executions()
    result = reconcile(orders, execs)
    row = order_row(result, "ORD-1")
    assert row["status"] == "No fill"
    assert row["remaining_qty"] == 200
    assert pd.isna(row["avg_price"])
    assert result.exceptions.empty


def test_overfill():
    orders = make_orders(("ORD-1", "MSFT", "BUY", 1000, 430.0))
    execs = make_executions(
        ("EX-1", "ORD-1", "MSFT", "BUY", 700, 429.0),
        ("EX-2", "ORD-1", "MSFT", "BUY", 500, 429.5),
    )
    result = reconcile(orders, execs)
    assert order_row(result, "ORD-1")["status"] == "Overfill"
    assert checks(result) == [OVERFILL]


def test_orphan_execution():
    orders = make_orders(("ORD-1", "AAPL", "BUY", 100, 275.0))
    execs = make_executions(
        ("EX-1", "ORD-1", "AAPL", "BUY", 100, 274.0),
        ("EX-2", "ORD-999", "AAPL", "BUY", 50, 274.0),
    )
    result = reconcile(orders, execs)
    assert checks(result) == [ORPHAN]
    assert result.exceptions.iloc[0]["order_id"] == "ORD-999"


def test_duplicate_execution_is_flagged_and_counted_once():
    orders = make_orders(("ORD-1", "AAPL", "BUY", 100, 275.0))
    execs = make_executions(
        ("EX-1", "ORD-1", "AAPL", "BUY", 100, 274.0),
        ("EX-1", "ORD-1", "AAPL", "BUY", 100, 274.0),
    )
    result = reconcile(orders, execs)
    row = order_row(result, "ORD-1")
    assert row["executed_qty"] == 100
    assert row["status"] == "Filled"
    assert checks(result) == [DUPLICATE]


def test_side_mismatch():
    orders = make_orders(("ORD-1", "AAPL", "SELL", 100, 270.0))
    execs = make_executions(("EX-1", "ORD-1", "AAPL", "BUY", 100, 271.0))
    result = reconcile(orders, execs)
    assert checks(result) == [MISMATCH]


def test_symbol_mismatch():
    orders = make_orders(("ORD-1", "AAPL", "BUY", 100, 275.0))
    execs = make_executions(("EX-1", "ORD-1", "NVDA", "BUY", 100, 190.0))
    result = reconcile(orders, execs)
    assert checks(result) == [MISMATCH]


def test_limit_breach_buy_above_limit():
    orders = make_orders(("ORD-1", "AAPL", "BUY", 100, 275.0))
    execs = make_executions(("EX-1", "ORD-1", "AAPL", "BUY", 100, 275.10))
    assert checks(reconcile(orders, execs)) == [LIMIT]


def test_limit_breach_sell_below_limit():
    orders = make_orders(("ORD-1", "AAPL", "SELL", 100, 275.0))
    execs = make_executions(("EX-1", "ORD-1", "AAPL", "SELL", 100, 274.90))
    assert checks(reconcile(orders, execs)) == [LIMIT]


def test_execution_exactly_at_limit_is_fine():
    orders = make_orders(
        ("ORD-1", "AAPL", "BUY", 100, 275.0),
        ("ORD-2", "AAPL", "SELL", 100, 275.0),
    )
    execs = make_executions(
        ("EX-1", "ORD-1", "AAPL", "BUY", 100, 275.0),
        ("EX-2", "ORD-2", "AAPL", "SELL", 100, 275.0),
    )
    assert reconcile(orders, execs).exceptions.empty


def test_market_order_without_limit_skips_limit_check():
    orders = make_orders(("ORD-1", "AAPL", "BUY", 100, None))
    execs = make_executions(("EX-1", "ORD-1", "AAPL", "BUY", 100, 999.0))
    assert reconcile(orders, execs).exceptions.empty


def test_missing_column_gives_a_clear_error():
    orders = pd.DataFrame({"order_id": ["ORD-1"], "symbol": ["AAPL"]})
    execs = make_executions()
    try:
        reconcile(orders, execs)
    except ValueError as err:
        assert "missing column" in str(err)
    else:
        raise AssertionError("expected a ValueError")


def test_every_injected_anomaly_is_found_and_nothing_else():
    orders, executions, injected = generate(seed=42)
    result = reconcile(orders, executions)
    found = set(zip(result.exceptions["check"], result.exceptions["order_id"]))
    expected = set(zip(injected["check"], injected["order_id"]))
    assert found == expected
    assert len(result.exceptions) == len(injected)


def test_generated_data_is_reproducible():
    a = generate(seed=7)
    b = generate(seed=7)
    for left, right in zip(a, b):
        assert left.equals(right)
