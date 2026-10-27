# Trading Desk Daily Monitor

Reconciliation of client orders and executions, with automatic detection of operational exceptions.

Personal project built on synthetic data. It is not connected to any bank system and does not describe how any real desk works.

## Problem

On an execution desk, order instructions (OMS), market executions (EMS) and reporting files live in different places and formats. Checking that an order was executed correctly means comparing several files by hand.

## What it does

- Matches every execution to its client order.
- Computes executed quantity, remaining quantity and volume-weighted average price per order.
- Gives each order a follow-up status: `Filled`, `Partial`, `No fill`, `Overfill`.
- Reports only the real exceptions, so the trader looks at those and nothing else.

Partial fills and unfilled orders are statuses, not errors. Only the checks below raise exceptions.

| Check | Meaning |
|---|---|
| `ORPHAN_EXECUTION` | An execution has no matching client order |
| `DUPLICATE_EXECUTION` | The same execution ID appears more than once |
| `OVERFILL` | Executed quantity is above the ordered quantity |
| `DATA_MISMATCH` | Execution side or symbol differs from its order |
| `LIMIT_BREACH` | A BUY executed above its limit, or a SELL below its limit |

Conventions: duplicated execution IDs are counted once; executions with a mismatching side or symbol still count in the order totals and are flagged; market orders (no limit) skip the limit check.

## Data

`generate_data.py` produces 150 orders across equities, ETFs, futures, options, crypto ETPs and a structured product, and 232 executions. It injects 10 known anomalies (2 of each check) and writes them to `data/injected_anomalies.csv`. A test checks that the engine finds exactly those and nothing else.

## Run

```bash
pip install -r requirements.txt
python generate_data.py
python reconciliation.py --out output
pytest
```

Example output:

```
Orders processed : 150
Filled           : 118
Partial          : 18
No fill          : 12
Overfill         : 2
Orders to review : 8
Match rate       : 74.7%
```

Match rate = orders fully filled with no exception attached, divided by all orders.

## Structure

```
data/                 orders.csv, executions.csv, injected_anomalies.csv
generate_data.py      synthetic data with injected anomalies
reconciliation.py     matching logic and checks
tests/                pytest tests, one per check
```

## Next

- Streamlit dashboard: CSV upload, filters, indicators, exceptions page.
- CSV export of exceptions.
