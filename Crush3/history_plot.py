#!/usr/bin/env python3
"""
history_plot.py

Graph of a history_sweep.py --single sweep: share of real alarms by look-back, one line per model,
shaded +-1 standard error, hollow points where events were missed, rings on the look-back the
one-standard-error rule picks, and Holt as a dashed line. A sweep run in parts (fm_single_a, _b, _c)
is joined first and the rule is applied to all rows together; the per-part 1se marks are not used.

Writes, into --out-dir: combined.csv (every row, with the rule's marks redone), lookback_real_en.png
and lookback_real_ja.png (the second only if a Japanese font is installed). Holt's share of real
alarms is read from back0/crossings.csv of the first part (Holt is the same at every look-back).

Usage (on the Linux machine):
    python3 history_plot.py fm_single_a/results.csv fm_single_b/results.csv fm_single_c/results.csv
    python3 history_plot.py --selftest
"""

import argparse
import sys
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd

import results
from history_sweep import one_se, show

COLOURS = {"RF_history": "#08306b", "XGB_history": "#a66a00"}
JA_FONTS = ("Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic", "TakaoGothic", "Yu Gothic")
TXT = {
    "en": dict(x="Look-back: how far back the one change reaches (s)", y="Alarms that were real (%)",
               model={"RF_history": "Random Forest + history", "XGB_history": "XGBoost + history"},
               holt="Holt ({:.0f}%)", pick="picked (one-SE rule)", miss="hollow: missed an event",
               note="{} events. Shaded: ±1 standard error over re-drawn runs."),
    "ja": dict(x="さかのぼる長さ(秒)", y="警報の的中率(%)",
               model={"RF_history": "Random Forest+履歴", "XGB_history": "XGBoost+履歴"},
               holt="Holtの方法({:.0f}%)", pick="一標準誤差ルールで選んだ長さ", miss="白抜き:イベントを見逃した長さ",
               note=""),
}


def combine(paths):
    res = pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)
    res = res.drop(columns=[c for c in res if c.startswith("d_")])  # differences to the row above are per part
    dup = res[res.duplicated(["model", "back_s"], keep=False)]
    assert dup.empty, f"look-backs in more than one part:\n{dup[['model', 'back_s']]}"
    return one_se(res.sort_values(["model", "back_s"], ignore_index=True))


def picks(res):
    ok = res[res.within_warning.astype(bool) & res.within_real.astype(bool)]
    return ok.groupby("model").back_s.min().to_dict()


def draw(res, holt, lang, path):
    t = TXT[lang]
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=200)
    chosen = picks(res)
    for model, g in res.groupby("model"):
        col = COLOURS.get(model, "#555555")
        x, y, se, miss = g.back_s, g.alarms_real_share * 100, g.real_se * 100, g.events_missed > 0
        ax.fill_between(x, y - se, y + se, color=col, alpha=0.15, lw=0)
        ax.plot(x, y, color=col, lw=1.6, label=t["model"].get(model, model))
        ax.scatter(x[~miss], y[~miss], s=16, color=col, zorder=3)
        ax.scatter(x[miss], y[miss], s=16, facecolor="white", edgecolor=col, lw=1.2, zorder=3)
        p = x == chosen.get(model)
        ax.scatter(x[p], y[p], s=170, facecolor="none", edgecolor="#D55E00", lw=2, zorder=4)
    if holt is not None:
        ax.axhline(holt * 100, color="#009E73", ls="--", lw=1.4, label=t["holt"].format(holt * 100))
    ax.scatter([], [], s=170, facecolor="none", edgecolor="#D55E00", lw=2, label=t["pick"])
    ax.scatter([], [], s=16, facecolor="white", edgecolor="#555555", lw=1.2, label=t["miss"])
    ax.set_xlim(res.back_s.min() - 5, res.back_s.max() + 5)
    ax.set_ylim(0, 100)
    ax.set_xlabel(t["x"], fontsize=11)
    ax.set_ylabel(t["y"], fontsize=11)
    ax.grid(axis="y", color="#e4e4e2", lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    if t["note"]:
        fig.text(0.09, 0.01, t["note"].format(int(res.events.iloc[0])), fontsize=8.5)
    fig.tight_layout(rect=(0, 0.04 if t["note"] else 0, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


def holt_share(first_csv):
    f = Path(first_csv).parent / "back0" / "crossings.csv"
    if not f.exists():
        print("no", f, "- drawing without the Holt line")
        return None
    return pd.read_csv(f, index_col=0).loc["Holt", "alarms_real_share"]


SETS_TXT = {
    "en": dict(x="Number of look-backs in the set", y="Alarms that were real (%)",
               model={"RF_history": "Random Forest + history", "XGB_history": "XGBoost + history"},
               fwd="forward search (adds one at a time)", bwd="backward search (drops one at a time)",
               ref="{} s alone (picked earlier): {:.0f}%", holt="Holt ({:.0f}%)",
               note="{} events. Shaded: ±1 standard error. Labels: look-back (s) added (+) or dropped (−)"),
    "ja": dict(x="組み合わせたさかのぼる長さの数", y="警報の的中率(%)",
               model={"RF_history": "Random Forest+履歴", "XGB_history": "XGBoost+履歴"},
               fwd="前向き探索(一つずつ加える)", bwd="後ろ向き探索(一つずつ外す)",
               ref="{}秒のみ(以前の選択): {:.0f}%", holt="Holtの方法({:.0f}%)", note=""),
}


def draw_sets(res, holt, ref, lang, path):
    """One panel per model: real alarms of each set on the forward and backward paths, against set size."""
    t = SETS_TXT[lang]
    models = sorted(res.model.unique())
    fig, axes = plt.subplots(1, len(models), figsize=(10, 5.8), dpi=200, sharey=True, squeeze=False)
    for ax, model in zip(axes[0], models):
        g, col = res[res.model == model], COLOURS.get(model, "#555555")
        for direction, ls, dy, va in (("forward", "-", 3, "bottom"), ("backward", "--", -3, "top")):
            p = g[g.path.str.contains(direction)].sort_values("n_back")
            x, y, se = p.n_back, p.alarms_real_share * 100, p.real_se * 100
            miss = p.events_missed > 0
            if direction == "forward":
                ax.fill_between(x, y - se, y + se, color=col, alpha=0.15, lw=0)
            ax.plot(x, y, color=col, ls=ls, lw=1.8, label=t["fwd" if direction == "forward" else "bwd"])
            ax.scatter(x[~miss], y[~miss], s=28, color=col, zorder=3)
            ax.scatter(x[miss], y[miss], s=28, facecolor="white", edgecolor=col, lw=1.4, zorder=3)
            # label each step with the look-back it added (forward, above) or dropped (backward, below);
            # backward labels only where the backward path left the forward one
            sets = [set(str(b).split("+")) for b in p.backs]
            for i, (_, r) in enumerate(p.iterrows()):
                if direction == "forward":
                    lab = "+" + "".join(sets[i] - sets[i - 1]) if i else str(r.backs)
                elif "forward" not in r.path and i + 1 < len(sets):
                    lab = "−" + "".join(sets[i + 1] - sets[i])
                else:
                    continue
                ax.annotate(lab, (r.n_back, r.alarms_real_share * 100 + dy), ha="center", va=va, fontsize=13, color=col)
        single = g[g.backs.astype(str) == str(ref.get(model))]
        if len(single):
            v = single.alarms_real_share.iloc[0] * 100
            ax.axhline(v, color="#555555", ls=":", lw=1.6, label=t["ref"].format(ref[model], v))
        if holt is not None:
            ax.axhline(holt * 100, color="#009E73", ls="--", lw=1.4, label=t["holt"].format(holt * 100))
        ax.set_title(t["model"].get(model, model), fontsize=15, color=col)
        ax.set_xticks(range(1, int(res.n_back.max()) + 1))
        ax.set_xlim(0.6, res.n_back.max() + 0.4)
        ax.set_ylim(40, 100)
        ax.set_xlabel(t["x"], fontsize=14)
        ax.tick_params(labelsize=13)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.legend(loc="lower center", frameon=False, fontsize=12)
    axes[0][0].set_ylabel(t["y"], fontsize=14)
    if t["note"]:
        fig.text(0.07, 0.01, t["note"].format(int(res.events.iloc[0])), fontsize=12)
    fig.tight_layout(rect=(0, 0.04 if t["note"] else 0, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


def plot_sets(path, out_dir, ref):
    res = pd.read_csv(path)
    res["path"] = res.path.fillna("")
    out_dir.mkdir(parents=True, exist_ok=True)
    crossings = sorted(Path(path).parent.glob("*/crossings.csv"))  # Holt is the same in every training
    holt = pd.read_csv(crossings[0], index_col=0).loc["Holt", "alarms_real_share"] if crossings else None
    draw_sets(res, holt, ref, "en", out_dir / "lookback_sets_en.png")
    have = {f.name for f in font_manager.fontManager.ttflist}
    ja = next((f for f in JA_FONTS if f in have), None)
    if ja:
        plt.rcParams["font.family"] = ja
        draw_sets(res, holt, ref, "ja", out_dir / "lookback_sets_ja.png")
    return res


def plot(paths, out_dir):
    res = combine(paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    res.to_csv(out_dir / "combined.csv", index=False)
    show(res)
    print("\none-SE pick over all rows:", picks(res))
    holt = holt_share(paths[0])
    draw(res, holt, "en", out_dir / "lookback_real_en.png")
    have = {f.name for f in font_manager.fontManager.ttflist}
    ja = next((f for f in JA_FONTS if f in have), None)
    if ja:
        plt.rcParams["font.family"] = ja
        draw(res, holt, "ja", out_dir / "lookback_real_ja.png")
    else:
        print("no Japanese font found (tried", ", ".join(JA_FONTS) + ") - Japanese graph skipped")
    return res


def selftest():
    def part(backs, missed=0):
        return pd.DataFrame([{"back_s": b, "model": m, "MAE": 10.0, "events": 39, "events_missed": missed,
                              "median_warning_s": 70.0, "alarms_real_share": 0.5 + (0.2 if b == 20 else 0),
                              "warning_se": 5.0, "real_se": 0.05, "within_warning": True, "within_real": True,
                              "d_warning_s": 0.0} for b in backs for m in COLOURS])
    tmp = Path(tempfile.mkdtemp())
    (tmp / "a").mkdir(), (tmp / "b").mkdir()
    part([0, 10]).to_csv(tmp / "a" / "results.csv", index=False)
    part([20, 30], missed=1).to_csv(tmp / "b" / "results.csv", index=False)
    res = plot([tmp / "a" / "results.csv", tmp / "b" / "results.csv"], tmp / "out")
    # part b's 20 s is the best real share, but it misses an event, so only part a's rows can be picked;
    # the per-part marks (all True) must be redone over the joined rows
    assert len(res) == 8 and "d_warning_s" not in res
    assert picks(res) == {"RF_history": 0, "XGB_history": 0}
    assert not res[res.back_s >= 20].within_real.astype(bool).any()
    assert (tmp / "out" / "lookback_real_en.png").exists()
    # sets mode: a --search results.csv (has a backs column) gets the sets graph
    (tmp / "s").mkdir()
    pd.DataFrame([{"model": m, "backs": b, "n_back": b.count("+") + 1, "path": p, "events": 39, "events_missed": 0,
                   "alarms_real_share": 0.7, "real_se": 0.05} for m in COLOURS
                  for b, p in (("4", ""), ("10", "forward backward"), ("4+10", "forward"), ("10+30", "backward"),
                               ("4+10+30", "forward backward"))]).to_csv(tmp / "s" / "results.csv", index=False)
    plot_sets(tmp / "s" / "results.csv", tmp / "out_s", {"RF_history": 30, "XGB_history": 4})
    assert (tmp / "out_s" / "lookback_sets_en.png").exists()
    print("\nselftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Join history_sweep.py --single parts, redo the one-SE rule, draw the graph.")
    p.add_argument("results", nargs="+", help="results.csv of each part, shortest look-backs first")
    p.add_argument("--out-dir", type=Path, default=Path("fm_single_plot"))
    p.add_argument("--ref", default="RF_history=30,XGB_history=4",
                   help="sets graph only: the single look-back picked earlier, drawn as a reference line (model=s,...)")
    args = p.parse_args()
    paths = [results.find(r) for r in args.results]
    out = results.new_dir(args.out_dir)
    if "backs" in pd.read_csv(paths[0], nrows=0).columns:  # a --search results.csv
        plot_sets(paths[0], out, {m: int(s) for m, s in (x.split("=") for x in args.ref.split(","))})
    else:
        plot(paths, out)
    results.zip_up(out)


if __name__ == "__main__":
    main()
