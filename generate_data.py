"""Generate synthetic orders and executions for the Trading Desk Daily Monitor.

Everything is fictional. Anomalies are injected on purpose, on distinct orders,
so the checks in reconciliation.py have something to find. The list of what was
injected is written to data/injected_anomalies.csv and is used by the tests.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from reconciliation import DUPLICATE, LIMIT, MISMATCH, ORPHAN, OVERFILL

# symbol, instrument type, reference price (fictional)
INSTRUMENTS = [
    ("AAPL", "Equity", 275.0),
    ("NVDA", "Equity", 190.0),
    ("MSFT", "Equity", 430.0),
    ("NESN", "Equity", 90.0),
    ("NOVN", "Equity", 105.0),
    ("UBSG", "Equity", 30.0),
    ("SPY", "ETF", 700.0),
    ("CSPX", "ETF", 650.0),
    ("VWRL", "ETF", 130.0),
    ("ESZ6", "Future", 7000.0),
    ("FESXZ6", "Future", 5500.0),
    ("AAPL DEC26 C300", "Option", 8.5),
    ("SPY DEC26 P650", "Option", 12.0),
    ("BTC-ETP", "Crypto ETP", 55.0),
    ("ETH-ETP", "Crypto ETP", 28.0),
    ("TRK-TECH", "Structured Product", 101.5),
]

# min quantity, max quantity, lot step
QUANTITY_RULES = {
    "Equity": (50, 2000, 50),
    "ETF": (100, 1000, 100),
    "Future": (1, 20, 1),
    "Option": (5, 100, 5),
    "Crypto ETP": (50, 500, 50),
    "Structured Product": (100, 1000, 100),
}


def _execution_price(rng, ref, side, limit):
    price = round(ref * (1 + rng.uniform(-0.001, 0.001)), 2)
    return min(price, limit) if side == "BUY" else max(price, limit)


def _split(rng, units, parts):
    parts = min(parts, units)
    if parts <= 1:
        return [units]
    cuts = np.sort(rng.choice(np.arange(1, units), size=parts - 1, replace=False))
    return np.diff(np.concatenate(([0], cuts, [units]))).tolist()


def generate(n_orders=150, seed=42, trading_date="2026-10-26"):
    """Return (orders, executions, injected_anomalies) as DataFrames."""
    rng = np.random.default_rng(seed)
    day_start = pd.Timestamp(trading_date) + pd.Timedelta(hours=9)
    orders, executions, meta = [], [], {}

    for i in range(n_orders):
        symbol, itype, ref = INSTRUMENTS[int(rng.integers(len(INSTRUMENTS)))]
        side = "BUY" if rng.random() < 0.55 else "SELL"
        lo, hi, step = QUANTITY_RULES[itype]
        units = int(rng.integers(max(lo // step, 1), hi // step + 1))
        sign = 1 if side == "BUY" else -1
        limit = round(ref * (1 + sign * rng.uniform(0.0015, 0.004)), 2)
        ts = day_start + pd.Timedelta(minutes=int(rng.integers(0, 400)))
        order_id = f"ORD-{101 + i}"

        orders.append(
            {
                "order_id": order_id,
                "timestamp": ts,
                "client_id": f"CLI-{int(rng.integers(1, 21)):03d}",
                "symbol": symbol,
                "instrument_type": itype,
                "side": side,
                "quantity": units * step,
                "limit_price": limit,
            }
        )
        meta[order_id] = {
            "units": units, "step": step, "side": side, "symbol": symbol,
            "ref": ref, "limit": limit, "full": False,
        }

        # ~80% fully filled, ~13% partially filled, the rest not filled
        r = rng.random()
        if r < 0.80:
            filled_units = units
            n_parts = int(rng.choice([1, 2, 3], p=[0.5, 0.35, 0.15]))
        elif r < 0.93 and units >= 2:
            filled_units = int(rng.integers(1, units))
            n_parts = int(rng.choice([1, 2]))
        else:
            continue

        exec_ts = ts
        for part in _split(rng, filled_units, n_parts):
            exec_ts = exec_ts + pd.Timedelta(minutes=int(rng.integers(1, 30)))
            executions.append(
                {
                    "order_id": order_id,
                    "timestamp": exec_ts,
                    "symbol": symbol,
                    "side": side,
                    "quantity": part * step,
                    "price": _execution_price(rng, ref, side, limit),
                }
            )
        meta[order_id]["full"] = filled_units == units

    ex = pd.DataFrame(executions).sort_values("timestamp", kind="stable").reset_index(drop=True)
    ex.insert(0, "execution_id", [f"EX-{i + 1:04d}" for i in range(len(ex))])
    next_id = len(ex) + 1

    # Inject anomalies on 8 distinct, fully filled orders (+ 2 orphan executions).
    full_ids = [oid for oid, m in meta.items() if m["full"]]
    picked = [str(x) for x in rng.choice(full_ids, size=8, replace=False)]
    overfill_ids, limit_ids, side_ids, dup_ids = picked[0:2], picked[2:4], picked[4:6], picked[6:8]
    injected, extra = [], []

    for oid in overfill_ids:
        m = meta[oid]
        last = ex[ex["order_id"] == oid].iloc[-1]
        extra.append(
            {
                "execution_id": f"EX-{next_id:04d}",
                "order_id": oid,
                "timestamp": last["timestamp"] + pd.Timedelta(minutes=5),
                "symbol": m["symbol"],
                "side": m["side"],
                "quantity": max(1, m["units"] // 5) * m["step"],
                "price": _execution_price(rng, m["ref"], m["side"], m["limit"]),
            }
        )
        injected.append((OVERFILL, oid, None))
        next_id += 1

    for oid in limit_ids:
        m = meta[oid]
        idx = ex.index[ex["order_id"] == oid][0]
        sign = 1 if m["side"] == "BUY" else -1
        ex.loc[idx, "price"] = round(m["limit"] * (1 + 0.003 * sign), 2)
        injected.append((LIMIT, oid, ex.loc[idx, "execution_id"]))

    for oid in side_ids:
        m = meta[oid]
        idx = ex.index[ex["order_id"] == oid][0]
        ex.loc[idx, "side"] = "SELL" if m["side"] == "BUY" else "BUY"
        injected.append((MISMATCH, oid, ex.loc[idx, "execution_id"]))

    for oid in dup_ids:
        idx = ex.index[ex["order_id"] == oid][-1]
        extra.append(ex.loc[idx].to_dict())
        injected.append((DUPLICATE, oid, ex.loc[idx, "execution_id"]))

    for oid in ("ORD-901", "ORD-902"):
        symbol, itype, ref = INSTRUMENTS[int(rng.integers(len(INSTRUMENTS)))]
        lo, hi, step = QUANTITY_RULES[itype]
        extra.append(
            {
                "execution_id": f"EX-{next_id:04d}",
                "order_id": oid,
                "timestamp": day_start + pd.Timedelta(minutes=int(rng.integers(0, 400))),
                "symbol": symbol,
                "side": "BUY" if rng.random() < 0.5 else "SELL",
                "quantity": int(rng.integers(max(lo // step, 1), hi // step + 1)) * step,
                "price": round(ref, 2),
            }
        )
        injected.append((ORPHAN, oid, f"EX-{next_id:04d}"))
        next_id += 1

    ex = pd.concat([ex, pd.DataFrame(extra)], ignore_index=True)
    ex = ex.sort_values("timestamp", kind="stable").reset_index(drop=True)

    return (
        pd.DataFrame(orders),
        ex,
        pd.DataFrame(injected, columns=["check", "order_id", "execution_id"]),
    )


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic trading data.")
    parser.add_argument("--orders", type=int, default=150, help="number of client orders")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default="data")
    args = parser.parse_args()

    orders, executions, injected = generate(args.orders, args.seed)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    orders.to_csv(out / "orders.csv", index=False)
    executions.to_csv(out / "executions.csv", index=False)
    injected.to_csv(out / "injected_anomalies.csv", index=False)
    print(f"{len(orders)} orders, {len(executions)} executions, {len(injected)} injected anomalies -> {out}/")


if __name__ == "__main__":
    main()
