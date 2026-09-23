#!/usr/bin/env python3
"""
make_maps.py

Generates random CrowdWalk scenarios from five map families. Each map gets a folder
with Map.xml, and --runs-per-map run_<j>/ subfolders that share that map but each
have their own gen.json (different crowd), scenario.json and prop.json:

  generated_maps/corridor_000/Map.xml
  generated_maps/corridor_000/run_0/{prop,gen,scenario}.json
  generated_maps/corridor_000/run_1/...

Several runs per map are what let force_model.py --cv test a map it trained on
with new crowd data, while --holdout-map tests maps it never saw. The five families:

  corridor    spawn -> wide approach -> narrow bottleneck -> exit
  funnel      2-4 feeder links merge into one trunk, then a narrow exit (like Crush3/Map.xml)
  tjunction   two opposing arms merge head-on into a narrower stem
  crossroads  4 arms; two-way (every arm spawns, walks to the opposite arm -> counterflow)
              or one-way (W and S spawn, walk to E and N)
  tree        branching egress: 2-3 levels of branches merging toward one narrow exit

Random per map: link lengths, widths, bottleneck width, feeder count/angles,
tree shape. Random per run, in gen.json: the total agent count (inflow surge), each spawn
group's start-time offset (departure timing skew) and release duration.

Links in one-way families point from->to in the walking direction, which is
what link_context.py assumes for one-way links. Coordinates are metres.

Physics/logging settings come from the template folder's prop.json (default:
this folder, Crush3/). Each run's prop.json points at "../Map.xml" and its own
gen/scenario, its logs at <log-root>/<map>/run_<j>/, and ruby_load_path at the
template folder (absolute, resolved on the machine running this script).
manifest.csv has one row per run: map, family and every random parameter.

Usage (on the Linux machine, from crowdwalk/):
    python3 Crush3/make_maps.py --n 10 --runs-per-map 3 --seed 1 --out generated_maps --log-root /mnt/ssd_2tb/gen_logs
    for p in generated_maps/*/run_*/prop.json; do
        sh quickstart.sh "$p" -c -lError
        r=${p#generated_maps/}; r=${r%/prop.json}          # e.g. corridor_000/run_1
        python3 Crush3/link_context.py /mnt/ssd_2tb/gen_logs/$r/linkMetrics.csv "$p" --db crush.duckdb --run-id "$r"
    done
    python3 Crush3/make_maps.py --selftest
"""

import argparse
import csv
import json
import math
import random
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from link_context import parse_json_c, parse_links, read_json_c  # noqa: E402

FAMILIES = ["corridor", "funnel", "tjunction", "crossroads", "tree"]


class MapBuilder:
    def __init__(self):
        self.nodes, self.links, self.next_id = [], [], 2  # _p00000/_p00001 are the two Groups

    def _id(self):
        i = f"_p{self.next_id:05d}"
        self.next_id += 1
        return i

    def node(self, x, y, tag=None):
        n = {"id": self._id(), "x": round(x, 3), "y": round(y, 3), "tags": [tag] if tag else [], "links": []}
        self.nodes.append(n)
        return n

    def link(self, a, b, width, tag=None):
        l = {"id": self._id(), "from": a["id"], "to": b["id"], "width": round(width, 3),
             "length": math.hypot(b["x"] - a["x"], b["y"] - a["y"]), "tags": [tag] if tag else []}
        a["links"].append(l["id"])
        b["links"].append(l["id"])
        self.links.append(l)
        return l

    def xml(self):
        xs, ys = [n["x"] for n in self.nodes], [n["y"] for n in self.nodes]
        box = {"pNorthWestX": min(xs) - 20, "pNorthWestY": min(ys) - 20,
               "pSouthEastX": max(xs) + 20, "pSouthEastY": max(ys) + 20}
        attrs = lambda i: {"defaultHeight": "0.0", "id": i, "imageFileName": "", "maxHeight": "5.0",
                           "minHeight": "-5.0", **{k: str(v) for k, v in box.items()}, "pTheta": "0.0",
                           "r": "0.0", "scale": "1.0", "sx": "1.0", "sy": "1.0", "tx": "0.0", "ty": "0.0",
                           "zone": "0"}
        root = ET.Element("Group", attrs("_p00000"))
        ET.SubElement(root, "tag").text = "root"
        ground = ET.SubElement(root, "Group", attrs("_p00001"))
        ET.SubElement(ground, "tag").text = "Ground"
        for n in self.nodes:
            e = ET.SubElement(ground, "Node", {"height": "0.0", "id": n["id"], "x": str(n["x"]), "y": str(n["y"])})
            for t in n["tags"]:
                ET.SubElement(e, "tag").text = t
            for lid in n["links"]:
                ET.SubElement(e, "link", {"id": lid})
        for l in self.links:
            e = ET.SubElement(ground, "Link", {"from": l["from"], "id": l["id"], "length": str(l["length"]),
                                               "to": l["to"], "width": str(l["width"])})
            for t in l["tags"]:
                ET.SubElement(e, "tag").text = t
        ET.indent(root)
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# Each family returns (MapBuilder, groups, params); groups = [(spawn_tag, goal_tag), ...].
# ponytail: width/length ranges are hand-picked plausible values, not from a design standard;
# widen them in one place here if runs never reach crush-level force.

def corridor(r):
    m = MapBuilder()
    W, wb = r.uniform(6, 12), r.uniform(1.5, 4)
    x = [0, r.uniform(50, 150)]
    x += [x[-1] + r.uniform(30, 100), 0, 0]
    x[3] = x[2] + r.uniform(10, 60)
    x[4] = x[3] + r.uniform(20, 60)
    n = [m.node(xi, 0, "GOAL" if i == 4 else None) for i, xi in enumerate(x)]
    m.link(n[0], n[1], W, "Spawn")
    m.link(n[1], n[2], W)
    m.link(n[2], n[3], wb)
    m.link(n[3], n[4], W)
    return m, [("Spawn", "GOAL")], {"approach_width": W, "bottleneck_width": wb}


def funnel(r):
    m = MapBuilder()
    k, W, wt, wb = r.randint(2, 4), r.uniform(4, 10), r.uniform(6, 12), r.uniform(1.5, 4)
    merge = m.node(0, 0)
    for i in range(k):
        ang = math.radians(r.uniform(-60, 60) if k == 1 else -60 + 120 * i / (k - 1) + r.uniform(-10, 10))
        L = r.uniform(60, 180)
        start = m.node(-L * math.cos(ang), L * math.sin(ang))
        m.link(start, merge, W, "Spawn")
    mid = m.node(r.uniform(40, 120), 0)
    goal = m.node(mid["x"] + r.uniform(30, 150), 0, "GOAL")
    m.link(merge, mid, wt)
    m.link(mid, goal, wb)
    return m, [("Spawn", "GOAL")], {"feeders": k, "feeder_width": W, "trunk_width": wt, "bottleneck_width": wb}


def tjunction(r):
    m = MapBuilder()
    W, ws = r.uniform(4, 10), r.uniform(2, 6)
    j = m.node(0, 0)
    for side in (-1, 1):
        m.link(m.node(side * r.uniform(60, 180), 0), j, W, "Spawn")
    m.link(j, m.node(0, r.uniform(40, 160), "GOAL"), ws)
    return m, [("Spawn", "GOAL")], {"arm_width": W, "stem_width": ws, "bottleneck_width": ws}


def crossroads(r):
    m = MapBuilder()
    two_way = r.random() < 0.5
    c = m.node(0, 0)
    dirs = {"W": (-1, 0), "E": (1, 0), "N": (0, -1), "S": (0, 1)}
    widths = {d: r.uniform(3, 8) for d in dirs}
    widths[r.choice(list(dirs))] = r.uniform(1.5, 3)  # one narrow arm
    for d, (dx, dy) in dirs.items():
        L, stub = r.uniform(50, 150), r.uniform(20, 40)
        inner = m.node(dx * L, dy * L)
        outer = m.node(dx * (L + stub), dy * (L + stub), f"Goal{d}")
        spawning = two_way or d in ("W", "S")
        if spawning:  # arm + stub point toward the center (walking direction for one-way)
            m.link(outer, inner, widths[d], f"Spawn{d}")
            m.link(inner, c, widths[d])
        else:
            m.link(c, inner, widths[d])
            m.link(inner, outer, widths[d])
    groups = ([("SpawnW", "GoalE"), ("SpawnE", "GoalW"), ("SpawnN", "GoalS"), ("SpawnS", "GoalN")] if two_way
              else [("SpawnW", "GoalE"), ("SpawnS", "GoalN")])
    return m, groups, {"two_way": two_way, "bottleneck_width": min(widths.values()),
                       **{f"width_{d}": w for d, w in widths.items()}}


def tree(r):
    m = MapBuilder()
    depth, branching = r.randint(2, 3), r.randint(2, 3)
    we = r.uniform(2, 5)
    exit_node = m.node(0, 0)
    m.link(exit_node, m.node(r.uniform(30, 80), 0, "GOAL"), we)
    step = r.uniform(50, 100)

    def grow(parent, level, y_lo, y_hi):
        for b in range(branching):
            y = y_lo + (b + 0.5) * (y_hi - y_lo) / branching
            child = m.node(parent["x"] - step * r.uniform(0.8, 1.2), y)
            leaf = level == depth
            m.link(child, parent, r.uniform(3, 6) if leaf else r.uniform(4, 10), "Spawn" if leaf else None)
            if not leaf:
                grow(child, level + 1, y_lo + b * (y_hi - y_lo) / branching, y_lo + (b + 1) * (y_hi - y_lo) / branching)

    span = 60 * branching ** depth
    grow(exit_node, 1, -span / 2, span / 2)
    return m, [("Spawn", "GOAL")], {"depth": depth, "branching": branching, "exit_width": we,
                                    "bottleneck_width": we}


def make_gen(r, groups, agent_template):
    """One gen entry per spawn group. total = inflow surge, start offset = departure skew."""
    total = r.randint(300, 3000)
    shares = [r.random() + 0.2 for _ in groups]
    counts = [max(1, round(total * s / sum(shares))) for s in shares]
    entries, params = [], {"total_agents": sum(counts)}
    for (spawn, goal), n in zip(groups, counts):
        skew, dur = r.randint(0, 600), round(r.uniform(5, 300), 1)
        start = 18 * 3600 + 60 + skew  # scenario Initiates at 18:00:00
        entries.append({**agent_template, "total": n, "startPlace": spawn, "goal": goal,
                        "startTime": f"{start // 3600:02d}:{start % 3600 // 60:02d}:{start % 60:02d}",
                        "duration": dur})
        params[f"{spawn}_agents"], params[f"{spawn}_start_skew_s"], params[f"{spawn}_duration_s"] = n, skew, dur
    return '#{ "version" : 2}\n' + json.dumps(entries, indent=2) + "\n", params


def generate(out, n, seed, template, log_root, families=FAMILIES, runs_per_map=1, sim_minutes=60):
    out.mkdir(parents=True, exist_ok=True)
    gen_entry = read_gen(template / "gen.json")[0]
    agent_template = {k: v for k, v in gen_entry.items() if k not in ("total", "startPlace", "goal", "startTime", "duration")}
    base_prop = read_json_c(template / "prop.json")
    if "link_logging" not in base_prop:
        sys.exit(f"{template / 'prop.json'} has no link_logging block -- the pipeline needs the link logger")
    rows = []
    for fam in families:
        for i in range(n):
            name = f"{fam}_{i:03d}"
            r = random.Random(f"{seed}-{name}")
            m, groups, mparams = globals()[fam](r)
            (out / name).mkdir(exist_ok=True)
            (out / name / "Map.xml").write_text(m.xml(), encoding="utf-8")
            for j in range(runs_per_map):  # same map, different crowd (gen.json) per run
                run = f"{name}/run_{j}"
                gen_text, gparams = make_gen(random.Random(f"{seed}-{run}"), groups, agent_template)
                d = out / run
                d.mkdir(exist_ok=True)
                (d / "gen.json").write_text(gen_text, encoding="utf-8")
                (d / "scenario.json").write_text(make_scenario(sim_minutes), encoding="utf-8")
                logs = (Path(log_root) / run) if log_root else d
                prop = json.loads(json.dumps(base_prop))
                # randseed: CrowdWalk's own RNG (agent placement etc.) defaults to 0 -> identical
                # randomness every run unless set; give every run its own, reproducible from --seed
                prop.update(map_file="../Map.xml", generation_file="gen.json", scenario_file="scenario.json",
                            ruby_load_path=str(template),
                            randseed=random.Random(f"{seed}-{run}-crowdwalk").randrange(1, 2**31))
                prop.pop("agent_appearance_file", None)
                if "dynamic_logging" in prop:  # per-agent logger is optional
                    prop["dynamic_logging"]["file"] = str(logs / "agents.csv")
                prop["link_logging"]["file"] = str(logs / "linkMetrics.csv")
                (d / "prop.json").write_text(json.dumps(prop, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
                rows.append({"run": run, "map": name, "family": fam, "links": len(m.links), **mparams, **gparams,
                             "randseed": prop["randseed"]})
    cols = list(dict.fromkeys(k for row in rows for k in row))
    with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)
    return rows


def make_scenario(sim_minutes):
    """Initiate at 18:00:00 (gen.json start times assume this) and a Finish event, so a run
    ends at a fixed simulated time even if crushed agents never reach the goal."""
    end = 18 * 3600 + sim_minutes * 60
    return json.dumps([{"atTime": "18:00:00", "type": "Initiate"},
                       {"atTime": f"{end // 3600:02d}:{end % 3600 // 60:02d}:{end % 60:02d}", "type": "Finish"}],
                      indent=2) + "\n"


def read_gen(path):
    """gen.json starts with a '#{ "version" : 2}' header line and may have // comments."""
    text = Path(path).read_text(encoding="utf-8")
    return parse_json_c(text.split("\n", 1)[1] if text.startswith("#") else text)


def selftest():
    out = Path(tempfile.mkdtemp())
    rows = generate(out, n=3, seed=7, template=HERE, log_root=None, runs_per_map=2)
    assert len(rows) == 30 and (out / "manifest.csv").exists()
    for row in rows:
        d, map_xml = out / row["run"], out / row["map"] / "Map.xml"
        root = ET.parse(map_xml).getroot()
        nodes = {n.get("id"): n for n in root.iter("Node")}
        links = parse_links(map_xml)
        # geometry: length = node distance, both ends exist and list the link
        for l in links.itertuples():
            a, b = nodes[l.from_node], nodes[l.to_node]
            assert abs(math.hypot(float(a.get("x")) - float(b.get("x")),
                                  float(a.get("y")) - float(b.get("y"))) - l.length) < 1e-6
            for n in (a, b):
                assert l.link_id in [e.get("id") for e in n.iter("link")]
        # every gen entry's spawn tag is on a link, its goal tag on a node, and the goal is reachable
        gen = read_gen(d / "gen.json")
        node_tags = {t.text: n.get("id") for n in nodes.values() for t in n.iter("tag")}
        adj = {}
        for l in links.itertuples():  # undirected: two-way crossroads walk links backwards
            adj.setdefault(l.from_node, set()).add(l.to_node)
            adj.setdefault(l.to_node, set()).add(l.from_node)
        for g in gen:
            spawn = links[links.link_tags.str.split(";").apply(lambda ts: g["startPlace"] in ts)]
            assert len(spawn) and g["goal"] in node_tags, (row["run"], g)
            seen, todo = set(), [spawn.iloc[0].to_node]
            while todo:
                x = todo.pop()
                if x not in seen:
                    seen.add(x)
                    todo += adj[x]
            assert node_tags[g["goal"]] in seen, (row["run"], g["goal"])
        assert sum(g["total"] for g in gen) == row["total_agents"]
        prop = read_json_c(d / "prop.json")
        assert prop["link_logging"]["file"] == str(d / "linkMetrics.csv") and prop["ruby_load_path"] == str(HERE)
        assert (d / prop["map_file"]).resolve() == map_xml.resolve()  # CrowdWalk resolves it from the prop's folder
    # one-way families: links point toward the goal (every non-goal node has an outgoing link)
    for row in rows:
        if row["family"] == "crossroads" and row["two_way"]:
            continue
        links = parse_links(out / row["map"] / "Map.xml")
        goals = {n.get("id") for n in ET.parse(out / row["map"] / "Map.xml").getroot().iter("Node")
                 if any(t.text.startswith(("GOAL", "Goal")) for t in n.iter("tag"))}
        assert set(links.from_node) | goals == set(links.from_node) | set(links.to_node), row["map"]
    # runs on one map share its Map.xml but get different crowds
    r0, r1 = rows[0], rows[1]
    assert r0["map"] == r1["map"] and r0["total_agents"] != r1["total_agents"]
    assert len({row["randseed"] for row in rows}) == len(rows)  # every run gets its own CrowdWalk seed
    sc = json.loads((out / rows[0]["run"] / "scenario.json").read_text())
    assert sc == [{"atTime": "18:00:00", "type": "Initiate"}, {"atTime": "19:00:00", "type": "Finish"}]
    assert json.loads(make_scenario(90))[1]["atTime"] == "19:30:00"
    # every crowd is released well before Finish
    assert all(row[k] + row[k.replace("start_skew_s", "duration_s")] + 60 < 3600
               for row in rows for k in row if k.endswith("start_skew_s"))
    assert generate(Path(tempfile.mkdtemp()), 3, 7, HERE, None, runs_per_map=2)[4] == rows[4]  # same seed -> same
    # link logger only: a template prop.json without dynamic_logging still generates
    tpl = Path(tempfile.mkdtemp())
    (tpl / "gen.json").write_text((HERE / "gen.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tpl / "prop.json").write_text(json.dumps({k: v for k, v in read_json_c(HERE / "prop.json").items()
                                               if k != "dynamic_logging"}), encoding="utf-8")
    rows2 = generate(Path(tempfile.mkdtemp()) / "o", 1, 7, tpl, None, runs_per_map=1)
    assert len(rows2) == 5
    print(f"selftest ok: {len(rows)} scenarios in {out}")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Generate random CrowdWalk scenarios from five map families.")
    p.add_argument("--n", type=int, default=10, help="maps per family (default 10)")
    p.add_argument("--sim-minutes", type=int, default=60, help="simulated minutes before Finish (default 60)")
    p.add_argument("--runs-per-map", type=int, default=3,
                   help="crowd variations (gen.json) per map (default 3) -- lets --cv test a known map with new data")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--families", nargs="+", choices=FAMILIES, default=FAMILIES)
    p.add_argument("--out", type=Path, default=Path("generated_maps"))
    p.add_argument("--template", type=Path, default=HERE, help="folder with prop/gen/scenario.json + Ruby agents")
    p.add_argument("--log-root", default=None, help="where each run writes its CSVs (default: its own folder)")
    args = p.parse_args()
    rows = generate(args.out, args.n, args.seed, args.template.resolve(), args.log_root, args.families,
                    args.runs_per_map, args.sim_minutes)
    print(f"wrote {len(rows)} runs ({args.n} maps x {len(args.families)} families x {args.runs_per_map}) to {args.out} (see {args.out / 'manifest.csv'})")


if __name__ == "__main__":
    main()
