#!/usr/bin/env python3
"""
baselines.py

What every model is compared with. None of these learns from the feature table.

  baseline: train mean   always predicts the average target of the training runs
  baseline: now          "no change": the link's value right now
  Holt                   a time-series baseline: Holt's damped-trend method (Gardner & McKenzie
                         1985) on the link's own past pressure, nothing else. It keeps a level
                         and a trend per link, projects them over the window and predicts the
                         highest point. The three smoothing settings are picked from HOLT_GRID
                         on the training runs by least squared error against the target.

The first two can never raise an alarm on a calm link; Holt can, so it is scored like a model
(crossings.csv, results.csv). If Holt raises alarms as well as the main models, their gain is
only "pressure is rising, so it keeps rising".

force_model.py calls add_holt() once per table and predictions() once per fold.

    python3 baselines.py --selftest
"""

import sys

import numpy as np
import pandas as pd

from link_context import FORCE, TICK

MEAN, NOW, HOLT = "baseline: train mean", "baseline: now", "Holt"
HOLT_PREFIX = "holt_"  # add_holt's columns; never model features
# (alpha: weight of the newest value in the level, beta: same for the trend, phi: share of the trend
# kept per tick ahead; 1 = a straight line, 0.9 = the trend has almost died out after 20 ticks)
HOLT_GRID = [(a, b, phi) for a in (0.2, 0.5, 0.8) for b in (0.05, 0.2) for phi in (0.9, 0.98, 1.0)]


def holt_states(y, alpha, beta, phi):
    """Level and trend after each value of y: Holt's damped-trend recursion. Uses values up to t only."""
    level, trend = np.empty(len(y)), np.empty(len(y))
    l, b = y[0], 0.0
    for i, v in enumerate(y.tolist()):
        prev = l
        l = alpha * v + (1 - alpha) * (prev + phi * b)
        b = beta * (l - prev) + (1 - beta) * phi * b
        level[i], trend[i] = l, b
    return level, trend


def add_holt(df):
    """holt_l<j>, holt_b<j> = level and trend of the link's own pressure under HOLT_GRID[j], per (run, link).
    A link with no row at a tick was empty -> pressure 0 (same rule as the target).
    ponytail: a plain Python loop over every tick, 18 times, and 36 extra columns; slow and heavy on a
    much bigger table. numba for the loop, or a smaller grid, would fix it."""
    keys = [k for k in ("scenario_id", "link_id") if k in df]
    df = df.reset_index(drop=True)
    out = np.empty((len(df), 2 * len(HOLT_GRID)), dtype=np.float32)
    for _, g in df.groupby(keys):
        at = g[TICK].to_numpy().astype(int)
        at -= at.min()
        y = np.zeros(at.max() + 1)
        y[at] = g[FORCE].fillna(0.0).to_numpy()
        for j, params in enumerate(HOLT_GRID):
            level, trend = holt_states(y, *params)
            out[g.index, 2 * j], out[g.index, 2 * j + 1] = level[at], trend[at]
    cols = [f"{HOLT_PREFIX}{s}{j}" for j in range(len(HOLT_GRID)) for s in "lb"]
    return pd.concat([df, pd.DataFrame(out, columns=cols)], axis=1)


class Holt:
    """Highest point of the Holt forecast over the next `horizon` ticks, from add_holt's columns."""

    def __init__(self, horizon):
        self.horizon = horizon

    def forecast(self, X, j):
        phi = HOLT_GRID[j][2]
        level = X[f"{HOLT_PREFIX}l{j}"].to_numpy(float)
        trend = X[f"{HOLT_PREFIX}b{j}"].to_numpy(float)
        # the forecast k ticks ahead is level + (phi + phi^2 + .. + phi^k) * trend: rising -> highest at
        # the end of the window, falling -> highest at the first tick
        steps = self.horizon if phi == 1 else phi * (1 - phi ** self.horizon) / (1 - phi)
        return np.clip(level + np.where(trend > 0, steps, phi) * trend, 0, None)

    def fit(self, X, y):
        y = np.asarray(y)
        self.j = min(range(len(HOLT_GRID)), key=lambda j: ((self.forecast(X, j) - y) ** 2).sum())
        print("   Holt settings picked on the training runs: alpha %g, beta %g, phi %g" % HOLT_GRID[self.j])
        return self

    def predict(self, X):
        return self.forecast(X, self.j)


def predictions(train, te, target, horizon):
    """name -> prediction for the test rows. Holt only for the pressure target, and only if add_holt() was run."""
    preds = {MEAN: np.full(len(te), train.target.mean()), NOW: te[target].to_numpy()}
    if target == FORCE and f"{HOLT_PREFIX}l0" in te:
        preds[HOLT] = Holt(horizon).fit(train, train.target).predict(te)
    return preds


def selftest():
    level, trend = holt_states(np.arange(0.0, 20, 2), 1.0, 1.0, 1.0)
    assert level[-1] == 18 and trend[-1] == 2  # a straight line: level = the value, trend = its slope
    two = pd.DataFrame({"holt_l0": [600.0, 600.0], "holt_b0": [5.0, -5.0]})  # both at 600 N, one rising, one falling
    up, down = Holt(5).forecast(two, 0)
    assert up > 600 and abs(down - 595.5) < 1e-9  # falling: the highest point is the next tick

    gap = pd.DataFrame({"scenario_id": "g", "link_id": "b", TICK: [0, 1, 2, 12], FORCE: [1.0, 3.0, 4.0, 9.0]})
    h = add_holt(gap)
    assert len(h) == 4 and h.shape[1] == 4 + 2 * len(HOLT_GRID)
    assert h.holt_l0.iloc[3] < 3  # ticks 3-11 count as 0 N, so the level has fallen before the 9 arrives

    # a link that rises 10 N per tick: Holt should predict close to the true highest value 5 ticks on
    ramp = add_holt(pd.DataFrame({"scenario_id": "r", "link_id": "b", TICK: range(60), FORCE: np.arange(60) * 10.0}))
    ramp = ramp.assign(target=ramp[FORCE] + 50)
    p = predictions(ramp, ramp, FORCE, 5)
    assert set(p) == {MEAN, NOW, HOLT} and (p[NOW] == ramp[FORCE]).all()
    assert np.abs(p[HOLT][30:] - ramp.target[30:]).max() < 10 < np.abs(p[NOW] - ramp.target).min()
    held = ramp.assign(max_time_over_threshold=1.0)
    assert set(predictions(held, held, "max_time_over_threshold", 5)) == {MEAN, NOW}  # no Holt for held time
    print("selftest ok")


if __name__ == "__main__":
    selftest() if sys.argv[1:] == ["--selftest"] else sys.exit(__doc__)
