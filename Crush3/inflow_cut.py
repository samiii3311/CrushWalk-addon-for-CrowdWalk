"""E20 inflow cut: does closing the entrances that feed an alarm link stop the crush?

The model is trained on the training runs and tested on new runs it never saw
(force_model.py --holdout-map runs:seed_5/,...). For each test run the first alarm (prediction
>= 1,062 N on any link) gives a time and a link. The entrances whose crowds walk through that
link (shortest path by length on Map.xml, as CrowdWalk routes) are closed for --hold s from
several start times (CrushAgent.rb entranceGates), and the run is simulated again with the same
seed. The model only uses the past and the gate is unused until it closes, so replaying a
finished run gives the same alarm a live loop would.

    python3 Crush3/inflow_cut.py prepare PRED.csv TEST.duckdb --runs ~/maps_e20 ~/maps_e20_bridge --out ~/e20_reruns
    python3 Crush3/inflow_cut.py run ~/e20_reruns --jobs 8
    python3 Crush3/inflow_cut.py compare ~/e20_reruns
    python3 Crush3/inflow_cut.py selftest

prepare writes OUT/plan.csv (one row per run and gate start) and one rerun folder per row.
Settings: alarm, alarm+30, alarm+60, alarm+90 (s after the alarm second) and crossing (the
second the alarm link first reaches 1,062 N, if it does). A gate starts the second after its
time, since the model sees second t only once it has been simulated. --check N also reruns
the first N alarmed runs with no gate (must match the original exactly).
compare writes OUT/compare.csv, judged on deaths (CRUSHED lines in sim.log):
  prevented      alarm link had deaths, now no deaths anywhere
  reduced        fewer deaths on the alarm link, none on a new link
  not prevented  as many deaths on the alarm link, none on a new link
  moved          deaths on a link that had none before (a new crush = failure)
  false alarm    alarm link had no deaths before; "no harm" if no new deaths, else "moved"
  can't act      no entrance feeds the alarm link (no rerun);  quiet: no alarm (no rerun)
"""
import argparse
import heapq
import json
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from link_context import TICK, read_json_c  # noqa: E402
from make_maps import read_gen  # noqa: E402

PRESSURE = "max_compression_pressure"
OFFSETS = {"alarm": 0, "alarm+30": 30, "alarm+60": 60, "alarm+90": 90}
CRUSHED = re.compile(r"CRUSHED \| id=(\w+) .*link=(\w+)")


def read_map(path):
    """Map.xml -> ({node: tags}, {link: (from, to, length)}). Tags are <tag> children."""
    nodes, links = {}, {}
    for el in ET.parse(path).iter():
        kind = el.tag.split("}")[-1]
        tags = [t.text for t in el if t.tag.split("}")[-1] == "tag"]
        if kind == "Node":
            nodes[el.get("id")] = tags
        elif kind == "Link":
            links[el.get("id")] = (el.get("from"), el.get("to"), float(el.get("length")))
    return nodes, links


def feeders(nodes, links, groups, alarm_link):
    """Entrance links (the one link at a group's start node) whose shortest route to the group's
    goal passes alarm_link. groups = [(start_tag, goal_tag)].
    ponytail: one shortest path per start node; ties or CrowdWalk's mental lengths could differ."""
    adj = {n: [] for n in nodes}
    for lid, (a, b, length) in links.items():
        adj[a].append((lid, b, length))
        adj[b].append((lid, a, length))
    gates = set()
    for start_tag, goal_tag in groups:
        goals = [n for n, t in nodes.items() if goal_tag in t]
        assert goals, f"no node tagged {goal_tag}"
        dist = {n: 0.0 for n in goals}
        heap = [(0.0, n) for n in goals]
        while heap:  # distance to the goal from every node
            d, n = heapq.heappop(heap)
            if d > dist[n]:
                continue
            for _, m, length in adj[n]:
                if d + length < dist.get(m, float("inf")):
                    dist[m] = d + length
                    heapq.heappush(heap, (d + length, m))
        for start in (n for n, t in nodes.items() if start_tag in t):
            assert len(adj[start]) == 1, f"start node {start} has {len(adj[start])} links"
            assert start in dist, f"start node {start} has no route to {goal_tag}"
            entrance, n, route = adj[start][0][0], start, set()
            while n not in goals:
                lid, n, _ = min(adj[n], key=lambda e: e[2] + dist.get(e[1], float("inf")))
                route.add(lid)
            if alarm_link in route:
                gates.add(entrance)
    return sorted(gates)


def deaths(sim_log):
    return CRUSHED.findall(Path(sim_log).read_text(errors="ignore")) if Path(sim_log).exists() else []


def outcome(before, after, alarm_link):
    """before/after = [(agent, link)] deaths of the original run and the gated rerun."""
    links_before = {l for _, l in before}
    on = lambda d: sum(l == alarm_link for _, l in d)
    if any(l not in links_before for _, l in after):
        return "moved"
    if on(before) == 0:
        return "false alarm, no harm"
    if not after:
        return "prevented"
    return "reduced" if on(after) < on(before) else "not prevented"


def prepare(args):
    import duckdb
    col = f"pred_{args.model}"
    p = pd.read_csv(args.predictions, usecols=["scenario_id", "link_id", TICK, col])
    hit = p[p[col] >= args.threshold].sort_values([TICK, col], ascending=[True, False])
    first = hit.groupby("scenario_id").first()
    with duckdb.connect(str(args.db), read_only=True) as con:
        cross = con.execute(f"select scenario_id, link_id, min({TICK}) t from link_ticks where {PRESSURE} >= ? "
                            "group by 1, 2", [args.threshold]).df().set_index(["scenario_id", "link_id"]).t
        info = con.execute(f"select scenario_id, max({TICK}) end_t from link_ticks group by 1").df().set_index("scenario_id")
    rows = [{"scenario_id": s, "setting": "quiet"} for s in sorted(set(p.scenario_id) - set(first.index))]
    for i, (sid, a) in enumerate(first.iterrows()):
        run = next((r / sid for r in args.runs if (r / sid / "prop.json").exists()), None)
        assert run, f"{sid}: no run folder under {args.runs}"
        prop = read_json_c(run / "prop.json")
        map_xml = (run / prop["map_file"]).resolve()
        gen = read_gen(run / prop["generation_file"])
        gates = feeders(*read_map(map_xml), [(g["startPlace"], g["goal"]) for g in gen], a.link_id)
        before = deaths(run / "sim.log")
        base = {"scenario_id": sid, "alarm_t": int(a[TICK]), "alarm_link": a.link_id, "alarm_pred": round(a[col], 1),
                "crossing_t": cross.get((sid, a.link_id)), "gates": ";".join(gates), "orig_end_t": info.end_t.get(sid),
                "orig_deaths": len(before), "orig_deaths_alarm_link": sum(l == a.link_id for _, l in before)}
        if not gates:
            rows.append({**base, "setting": "can't act"})
            continue
        starts = {k: base["alarm_t"] + v for k, v in OFFSETS.items()}
        if pd.notna(base["crossing_t"]):
            starts["crossing"] = int(base["crossing_t"])
        if i < args.check:
            starts["nogate"] = None
        for name, t in starts.items():
            folder = args.out / sid / name
            folder.mkdir(parents=True, exist_ok=False)
            for f in (prop["generation_file"], prop["scenario_file"]):
                (folder / f).write_bytes((run / f).read_bytes())
            new = {**prop, "map_file": str(map_xml)}
            new["link_logging"] = {**prop["link_logging"], "file": str(folder / "linkMetrics.csv")}
            if t is not None:
                new["entranceGates"] = ",".join(f"{g}:{t + 1}:{t + 1 + args.hold}" for g in gates)
            (folder / "prop.json").write_text(json.dumps(new, indent=2))
            rows.append({**base, "setting": name, "close_t": None if t is None else t + 1, "folder": str(folder),
                         "orig_run": str(run)})
    plan = pd.DataFrame(rows)
    plan.to_csv(args.out / "plan.csv", index=False)
    print(plan.drop_duplicates("scenario_id").setting.replace({k: "gated" for k in [*OFFSETS, "crossing", "nogate"]})
          .value_counts().to_string(), f"\n{plan.folder.notna().sum()} reruns -> {args.out / 'plan.csv'}")


def run(args):
    import os
    from run_batch import quickstart_cmd, simulate
    os.environ.setdefault("JAVA_OPTS", "-Dgroovy.source.encoding=UTF-8 -Dfile.encoding=UTF-8 -Xmx2g")
    plan = pd.read_csv(args.out / "plan.csv")
    todo = [Path(f) for f in plan.folder.dropna() if not (Path(f) / "sim.exit").exists()]
    cmd = quickstart_cmd(HERE.parent)
    print(f"{len(todo)} reruns to do, {args.jobs} at a time", flush=True)

    def one(folder):
        ok, msg = simulate(cmd(folder / "prop.json"), folder / "prop.json", folder, args.timeout)
        (folder / "sim.exit").write_text(msg + "\n")
        print(f"{folder}: {msg}", flush=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(one, todo))


def compare(args):
    from run_batch import last_tick
    plan = pd.read_csv(args.out / "plan.csv")
    out = []
    for r in plan.itertuples():
        row = r._asdict()
        if isinstance(r.folder, str):
            before, after = deaths(Path(r.orig_run) / "sim.log"), deaths(Path(r.folder) / "sim.log")
            lm = pd.read_csv(Path(r.folder) / "linkMetrics.csv", usecols=["link_id", PRESSURE])
            row.update(deaths=len(after), deaths_alarm_link=sum(l == r.alarm_link for _, l in after),
                       end_t=last_tick(Path(r.folder) / "linkMetrics.csv"),
                       alarm_link_peak=lm[lm.link_id == r.alarm_link][PRESSURE].max(),
                       outcome=("same as original" if before == after else "DIFFERENT") if r.setting == "nogate"
                       else outcome(before, after, r.alarm_link))
        else:
            row["outcome"] = r.setting
        out.append(row)
    res = pd.DataFrame(out).drop(columns="Index")
    res.to_csv(args.out / "compare.csv", index=False)
    gated = res[res.folder.notna() & (res.setting != "nogate")]
    print(pd.crosstab(gated.setting, gated.outcome, margins=True).to_string())
    print("no rerun:", res[res.folder.isna()].outcome.value_counts().to_dict())
    print("repeat check:", res[res.setting == "nogate"].outcome.value_counts().to_dict())
    print(f"-> {args.out / 'compare.csv'}")


def selftest():
    # entrance A -> j -> bottleneck L -> goal G; entrance B -> j2 -> other route to G; C leaves at H
    nodes = {"a": ["S_in"], "b": ["S_in"], "c": ["T_in"], "j": [], "j2": [], "k": [], "g": ["G"], "h": ["H"]}
    links = {"ea": ("a", "j", 5), "eb": ("b", "j2", 5), "ec": ("c", "k", 5), "L": ("j", "k", 10),
             "kg": ("k", "g", 10), "j2g": ("j2", "g", 30), "j2j": ("j2", "j", 50), "kh": ("k", "h", 5)}
    assert feeders(nodes, links, [("S_in", "G")], "L") == ["ea"]
    assert feeders(nodes, links, [("S_in", "G"), ("T_in", "H")], "kg") == ["ea"]
    assert feeders(nodes, links, [("T_in", "G")], "L") == []
    d = [("p1", "L"), ("p2", "L")]
    assert outcome(d, [], "L") == "prevented" and outcome(d, [("p1", "L")], "L") == "reduced"
    assert outcome(d, d, "L") == "not prevented" and outcome(d, [("p3", "kg")], "L") == "moved"
    assert outcome([], [], "L") == "false alarm, no harm" and outcome([], [("p1", "kg")], "L") == "moved"
    print("selftest ok")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prepare")
    a.add_argument("predictions", type=Path)
    a.add_argument("db", type=Path, help="the test runs' DuckDB (for crossings and end times)")
    a.add_argument("--runs", type=Path, nargs="+", required=True, help="folders holding the test runs (scenario_id below)")
    a.add_argument("--out", type=Path, required=True)
    a.add_argument("--model", default="XGB_history")
    a.add_argument("--threshold", type=float, default=1062.0)
    a.add_argument("--hold", type=int, default=300, help="seconds each gate stays closed (default 300)")
    a.add_argument("--check", type=int, default=10, help="alarmed runs also rerun with no gate (default 10)")
    b = sub.add_parser("run")
    b.add_argument("out", type=Path)
    b.add_argument("--jobs", type=int, default=8)
    b.add_argument("--timeout", type=int, default=14400)
    c = sub.add_parser("compare")
    c.add_argument("out", type=Path)
    sub.add_parser("selftest")
    args = p.parse_args()
    if args.cmd == "prepare":
        args.out.mkdir(parents=True, exist_ok=True)
    {"prepare": prepare, "run": run, "compare": compare, "selftest": lambda _: selftest()}[args.cmd](args)


if __name__ == "__main__":
    main()
