# Trading Desk Daily Monitor

A small Python tool that reconciles client orders with their executions and lists the ones a trader should look at.

It runs on synthetic data. Nothing is connected to a bank system, and the setup is a simplified picture of an execution desk, not a description of a real one.

## Why

On a desk, client instructions (OMS) and market fills (EMS) sit in separate files. Checking an order means finding all its executions, adding them up and comparing with what was asked. I wanted to automate that part and only show what needs a human.

## What it does

For each order, the script finds its executions, sums the quantity, computes the remaining quantity and the volume-weighted average price, and sets a status: Filled, Partial, No fill or Overfill.

Partial and unfilled orders are normal follow-up states, so they are not treated as errors. Exceptions are limited to five checks:

- orphan execution: no matching client order
- duplicate execution ID
- overfill: executed quantity above ordered quantity
- data mismatch: execution side or symbol differs from the order
- limit breach: a BUY executed above its limit, or a SELL below it

Some choices worth knowing: a duplicated execution ID is counted once, an execution with the wrong side or symbol is still counted in the order totals but flagged, and market orders (no limit) skip the limit check.

## Data

`generate_data.py` creates 150 orders across equities, ETFs, futures, options, crypto ETPs and one structured product, with 232 executions. It injects 10 known anomalies (two per check) and saves them in `data/injected_anomalies.csv`.

## Usage

```bash
pip install -r requirements.txt
python generate_data.py
python reconciliation.py --out output
pytest
```

Output on the default dataset:

```
Orders processed : 150
Filled           : 118
Partial          : 18
No fill          : 12
Overfill         : 2
Orders to review : 8
Match rate       : 74.7%
```

Match rate is the share of orders that are fully filled with no exception attached.

## Tests

Each check has its own test on a small hand-made case. One more test generates the full dataset and verifies that the engine flags exactly the 10 injected anomalies, no more and no fewer.

## Status

The reconciliation engine and tests are done. A Streamlit dashboard (CSV upload, filters, exceptions page) is next.
