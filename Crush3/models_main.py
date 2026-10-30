#!/usr/bin/env python3
"""
models_main.py

The two main models: Random Forest and XGBoost on the link's current state plus its history.

  RF_history, XGB_history

History = for each column in LAG_COLS, its change over the last 10, 30 and 60 ticks
(<column>_d10, _d30, _d60), built per run and link from ticks <= t only. Two links both at 600 N
look the same without it; with it, the one that rose from 200 N and the one that fell from
900 N are told apart.

How far back the history should go is what history_sweep.py tests; LAGS below is the setting in use.

force_model.py trains these by default (--models main). The same two models without the
history columns are in models_other.py.

    python3 models_main.py --selftest
"""

import os
import sys

import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from xgboost import XGBRegressor

from link_context import FORCE, TICK

LAGS = (10, 30, 60)  # ticks back
# only columns where "no row at that tick" really means 0 (the logger skips empty links)
LAG_COLS = ("density", "agent_count", FORCE, "prev_inflow", "prev_agent_count", "next_density",
            "prev_max_compression_pressure", "next_max_compression_pressure")


def is_history(column):
    """A column add_lags made: <a LAG_COLS column>_d<ticks back>, for any number of ticks."""
    base, _, lag = column.rpartition("_d")
    return base in LAG_COLS and lag.isdigit()


def random_forest():
    # CRUSH_RF_DEVICE=cuda: XGBoost's random-forest mode on the GPU, set to match the sklearn forest below
    # (300 trees, >= 5 rows per leaf, no depth limit, all inputs per split, ~63% of rows per tree like a
    # bootstrap). Not the same algorithm (binned splits, no replacement), so never mix the two in one comparison.
    if os.environ.get("CRUSH_RF_DEVICE") == "cuda":
        from xgboost import XGBRFRegressor
        return XGBRFRegressor(n_estimators=300, min_child_weight=5, max_depth=0, grow_policy="lossguide",
                              subsample=0.632, colsample_bynode=1.0, random_state=0, device="cuda")
    return RandomForestRegressor(n_estimators=300, min_samples_leaf=5, n_jobs=-1, random_state=0)


def xgboost():
    # CRUSH_XGB_DEVICE=cuda trains on the GPU (~65x faster on the Linux box, 2026-10-20). GPU and CPU
    # results differ slightly, so keep one device for every result that gets compared.
    return XGBRegressor(n_estimators=400, max_depth=6, learning_rate=0.05, subsample=0.8, random_state=0,
                        device=os.environ.get("CRUSH_XGB_DEVICE", "cpu"))


def new_models(columns=None):
    """name -> (untrained model, does it get the history columns)."""
    return {"RF_history": (random_forest(), True), "XGB_history": (xgboost(), True)}


def add_lags(df, lags=LAGS):
    """<col>_d<L> = col now minus col L ticks ago, per (run, link). A link with no row L ticks ago was empty -> 0."""
    keys = [k for k in ("scenario_id", "link_id") if k in df]
    cols = [c for c in LAG_COLS if c in df]
    n = len(df)
    for lag in lags:
        names = {c: f"{c}_d{lag}" for c in cols}
        past = df[keys + [TICK] + cols].assign(**{TICK: df[TICK] + lag}).rename(columns=names)
        df = df.merge(past, on=keys + [TICK], how="left")
        for c, name in names.items():
            df[name] = df[c] - df[name].fillna(0.0)
    assert len(df) == n, "more than one row per (run, link, tick)"
    return df


def selftest():
    lag = pd.DataFrame({"scenario_id": "g", "link_id": "b", TICK: [0, 1, 2, 12], "density": [1.0, 3.0, 4.0, 9.0]})
    assert list(add_lags(lag, (1, 10)).density_d1) == [1.0, 2.0, 1.0, 9.0]   # tick 11 had no row: empty link
    assert list(add_lags(lag, (1, 10)).density_d10) == [1.0, 3.0, 4.0, 5.0]  # tick 12 minus tick 2
    assert all(is_history(c) for c in set(add_lags(lag, (1, 300)).columns) - set(lag.columns))
    assert not any(is_history(c) for c in ("density", "density_d", "speed_d10", "prev_density"))
    assert all(history for _, history in new_models().values())
    print("selftest ok")


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["--selftest"] else sys.exit(__doc__)
