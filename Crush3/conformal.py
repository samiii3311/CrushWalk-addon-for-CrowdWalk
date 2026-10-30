#!/usr/bin/env python3
"""
conformal.py

A confidence range for every prediction, from a model's saved predictions (no training).
For E13 (joint meeting request: "how sure is each prediction?").

Each prediction p gets the range [p - q, p + q] (lower end cut at 0): "the highest pressure in the
next window will be in this range, at 90% (or 80 / 95%) confidence". q is split-conformal:
the (1 - a) quantile of |true - predicted| on calibration rows, with the usual (n + 1) correction.

  - Calibration is leave-one-fold-out over whole runs: the rows of fold k get their q from the
    held-out errors of the other folds, so no run calibrates its own range.
  - q is computed per band of the PREDICTED value (bands below), because errors grow with pressure;
    one q for all rows would be too wide on calm links and too narrow on busy ones.
  - Coverage (share of rows whose true value fell inside its range) is checked on the held-out
    fold, overall, per band and per run. The guarantee is on average over rows of runs like the
    training ones; it is expected to fail on a new kind of map, so check it again in R3.

ponytail: the other folds' errors come from models trained on slightly different runs; the usual
cross-conformal approximation. Proper split conformal (separate calibration runs) if coverage misses.

Usage (on the Linux machine):
    python3 Crush3/conformal.py <results>/<date>_fm_r2a/h120_runsall/predictions.csv --out fm_conformal/results.csv
    python3 Crush3/conformal.py --selftest
"""
import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

BANDS = (0, 250, 500, 1062, np.inf)  # predicted N; 1062 = the event line
LEVELS = (0.8, 0.9, 0.95)


def conformal_q(err, level):
    """Split-conformal width: the ceil((n + 1) level) / n quantile of the absolute errors."""
    n = len(err)
    if n == 0:
        return np.nan
    return float(np.quantile(err, min(1.0, np.ceil((n + 1) * level) / n), method="higher"))


def ranges(p, model, levels=LEVELS, bands=BANDS):
    """Rows: model, level, fold, band, q, rows, covered share (on that fold's held-out rows)."""
    pred = p[f"pred_{model}"].to_numpy()
    err = np.abs(p.true.to_numpy() - pred)
    names = [f"{lo:g}-{hi:g} N" for lo, hi in zip(bands[:-1], bands[1:])]
    band = pd.cut(np.clip(pred, bands[0], None), bands, right=False, labels=False)  # band number; below 0 N joins the lowest
    out, cover = [], {}
    for level in levels:
        inside = np.zeros(len(p), dtype=bool)
        for fold in p.fold.unique():
            test = (p.fold == fold).to_numpy()
            for b in np.unique(band):
                cal, te = ~test & (band == b), test & (band == b)
                if not te.any():
                    continue
                q = conformal_q(err[cal], level)
                ok = err[te] <= q
                inside[te] = ok
                out.append({"model": model, "level": level, "fold": fold, "band_i": b, "band": names[b], "q": q,
                            "rows": int(te.sum()), "covered": ok.mean()})
        cover[level] = inside
    return pd.DataFrame(out), cover


def summary(p, model, levels=LEVELS):
    per_fold, cover = ranges(p, model, levels)
    rows = []
    for level in levels:
        f = per_fold[per_fold.level == level]
        by_run = pd.Series(cover[level]).groupby(p.scenario_id.to_numpy()).mean()
        for (_, b), g in f.groupby(["band_i", "band"]):
            rows.append({"model": model, "level": level, "band": b, "q_mean": np.average(g.q, weights=g.rows),
                         "q_min": g.q.min(), "q_max": g.q.max(), "rows": g.rows.sum(),
                         "covered": np.average(g.covered, weights=g.rows)})
        rows.append({"model": model, "level": level, "band": "ALL", "rows": len(p), "covered": cover[level].mean(),
                     "runs": len(by_run), "runs_covered_median": by_run.median(),
                     "runs_below_level_minus_10pts": int((by_run < level - 0.10).sum())})
    return pd.DataFrame(rows)


def selftest():
    rng = np.random.default_rng(0)
    n_runs, T = 40, 200
    pred = rng.uniform(0, 2000, n_runs * T)
    true = np.clip(pred + rng.normal(0, 20 + 0.1 * pred), 0, None)  # errors grow with pressure
    pred[:5] = -1.0  # models can predict slightly below 0 (XGBoost does)
    p = pd.DataFrame({"scenario_id": np.repeat([f"r{i}" for i in range(n_runs)], T),
                      "fold": np.repeat(np.arange(n_runs) % 5, T), "true": true, "pred_M": pred})
    s = summary(p, "M")
    alls = s[s.band == "ALL"].set_index("level").covered
    for level in LEVELS:  # coverage near the asked level
        assert abs(alls[level] - level) < 0.02, (level, alls[level])
    q90 = s[(s.level == 0.9) & (s.band != "ALL")].q_mean.to_numpy()
    assert (np.diff(q90) > 0).all(), q90  # wider ranges for higher predicted pressure
    assert conformal_q(np.arange(1, 10.0), 0.9) == 9.0  # ceil(10 x 0.9) / 9 -> the top value
    out = Path(tempfile.mkdtemp()) / "results.csv"
    s.to_csv(out, index=False)
    print(s.round(3).to_string(index=False))
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    ap = argparse.ArgumentParser(description="Conformal confidence range per prediction, from predictions.csv.")
    ap.add_argument("predictions", type=Path, help="predictions.csv with fold, scenario_id, true, pred_<model>")
    ap.add_argument("--models", default="RF_history,XGB_history", help="comma-separated (default %(default)s)")
    ap.add_argument("--out", type=Path, default=Path("fm_conformal/results.csv"))
    a = ap.parse_args()
    p = pd.read_csv(a.predictions)
    res = pd.concat([summary(p, m) for m in a.models.split(",")], ignore_index=True)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(a.out, index=False)
    print(res.round(3).to_string(index=False))
    print(f"\nwrote {a.out}. Each prediction's range = prediction +- q_mean of its band; "
          "covered = share of held-out rows inside their range (target = level).")


if __name__ == "__main__":
    main()
