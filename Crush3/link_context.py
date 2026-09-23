#!/usr/bin/env python3
"""
link_context.py

Loads one run's LinkAggregateLogger output into a DuckDB table (link_ticks),
adding to every record: previous-link (upstream) and next-link (downstream)
context and that run's map details for the link. The map goes on the records
here, not in the logger, so runs on different maps can share one table.
Sim settings from prop.json are NOT added -- real-world data wouldn't have them;
prop.json is only read to find which map the run used. Pure post-processing, so no
Java rebuild / re-patch is needed.

Per-record run info:
  scenario_id        run id (--run-id, default <csv folder>/<csv name>); re-loading a run replaces it
  map_file           readable label of the map this run used: <folder>/<file>, e.g. corridor_003/Map.xml
  map_id             hash of the map's contents: same map -> same id, even from different folders;
                     different maps -> different ids, even if both are named Map.xml.
                     force_model.py --holdout-map groups runs by this.
  length, width, area_m2, link_tags   this link's details from the map
  prev_width_ratio   widest upstream width / own width (>1 = narrowing into this link)

Direction: each link is an arc from->to. If the CSV has mean_link_direction
(link_logging.fields contains "link_direction", logged by Test.rb as +1 for
from->to / -1 for to->from) and a link ever carries backward agents, it is
treated as two-way and also gets a to->from arc. Without that column every
link is one-way (from->to), so old CSVs still work.

For each arc of a link L:
  previous links = arcs that END where L's arc starts   (they feed into L)
  next links     = arcs that START where L's arc ends   (L feeds into them)
(U-turns onto L itself or onto a reverse-parallel twin link are excluded.)

Columns added per (tick, link) row:
  density            agents/m^2 on this link
  flow               agents/s leaving this link ~ agent_count * mean_speed / length
  fwd_count, bwd_count            agents walking from->to / to->from on this link
  fwd_mean_speed, bwd_mean_speed  mean speed of each direction's agents (0 if none)
                                  (from speed_fwd/speed_bwd in link_logging.fields)
  counterflow_share  share of agents going the minority direction (0 = one-way, 0.5 = even split)
  prev_link_count    number of upstream links (static)
  prev_inflow        agents/s arriving at L: upstream flow heading INTO L's entry node(s)
  prev_agent_count   agents on upstream links heading toward L
  prev_mean_speed    agent-weighted mean speed on upstream links
  prev_max_compression_pressure
  next_link_count    number of downstream links (static)
  next_density       agents/m^2 across all downstream links (both directions)
  next_mean_speed    agent-weighted mean speed on downstream links
  next_max_compression_pressure
  next_width_ratio   total downstream width / own width (<1 = narrowing ahead)

Usage (on the Linux machine):
    python3 link_context.py /mnt/ssd_2tb/log-sep14/linkMetrics.csv prop.json --db crush.duckdb
    python3 link_context.py --selftest
"""

import argparse
import hashlib
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

TICK = "current_traveling_period"
FORCE = "max_compression_pressure"
DIR = "mean_link_direction"
TABLE = "link_ticks"  # DuckDB table holding every run's records


def parse_links(map_xml):
    rows = [{"link_id": l.get("id"), "from_node": l.get("from"), "to_node": l.get("to"),
             "length": float(l.get("length", 0.0)), "width": float(l.get("width", 1.0)),
             "link_tags": ";".join(t.text or "" for t in l.iter("tag"))}
            for l in ET.parse(map_xml).getroot().iter("Link")]
    return pd.DataFrame(rows)


def parse_json_c(text):
    """CrowdWalk JSON allows // comments; strip them (outside strings) before parsing."""
    return json.loads(re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*', lambda m: m.group(1) or "", text))


def read_json_c(path):
    return parse_json_c(Path(path).read_text(encoding="utf-8"))


def build_run(df, map_xml, run_id):
    """One run's logger rows + link context + that run's map details, per record.
    Sim settings (prop.json) are deliberately NOT added: real-world data wouldn't have them."""
    out = add_context(df, parse_links(map_xml))
    out.insert(0, "scenario_id", run_id)
    map_xml = Path(map_xml).resolve()
    out.insert(1, "map_file", f"{map_xml.parent.name}/{map_xml.name}")
    out.insert(2, "map_id", map_id(map_xml))
    return out


def map_id(map_xml):
    return hashlib.sha1(Path(map_xml).read_bytes()).hexdigest()[:10]


def quote_ident(name):
    return '"' + str(name).replace('"', '""') + '"'


def write_db(df, db_path, run_id):
    """Append one run to the DuckDB table (replacing it if that run_id was loaded before).
    Columns new to the table (e.g. a new logger field) are added; older runs get NULL.
    Only the fixed TABLE name and quoted column identifiers go into SQL text; values are parameters."""
    import duckdb
    con = duckdb.connect(str(db_path))
    con.register("new_rows", df)
    if TABLE not in {r[0] for r in con.execute("SHOW TABLES").fetchall()}:
        con.execute(f"CREATE TABLE {TABLE} AS SELECT * FROM new_rows")
    else:
        have = {r[0] for r in con.execute(f"DESCRIBE {TABLE}").fetchall()}
        for name, dtype, *_ in con.execute("DESCRIBE new_rows").fetchall():
            if name not in have:
                con.execute(f"ALTER TABLE {TABLE} ADD COLUMN {quote_ident(name)} {dtype}")
        con.execute(f"DELETE FROM {TABLE} WHERE scenario_id = ?", [run_id])
        con.execute(f"INSERT INTO {TABLE} BY NAME SELECT * FROM new_rows")
    n = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    con.close()
    return n


def make_arcs(links, two_way):
    fwd = links.assign(tail=links.from_node, head=links.to_node, dir=1)
    rev = links[links.link_id.isin(two_way)]
    bwd = rev.assign(tail=rev.to_node, head=rev.from_node, dir=-1)
    return pd.concat([fwd, bwd])[["link_id", "tail", "head", "dir"]]


def neighbor_pairs(arcs):
    """prev: (link_id, link_id_n, dir_n) arcs feeding L.  next: (link_id, link_id_n) links L feeds."""
    prev = arcs.merge(arcs, left_on="tail", right_on="head", suffixes=("", "_n"))
    prev = prev[(prev.link_id_n != prev.link_id) & (prev.tail_n != prev.head)]
    nxt = arcs.merge(arcs, left_on="head", right_on="tail", suffixes=("", "_n"))
    nxt = nxt[(nxt.link_id_n != nxt.link_id) & (nxt.head_n != nxt.tail)]
    return (prev[["link_id", "link_id_n", "dir_n"]].drop_duplicates(),
            nxt[["link_id", "link_id_n"]].drop_duplicates())


def add_context(df, links):
    links = links.assign(link_id=links.link_id.astype(str))
    df = df.assign(link_id=df.link_id.astype(str))
    unknown = set(df.link_id) - set(links.link_id)
    if unknown:
        print(f"[!] {len(unknown)} link_ids in the CSV are not in Map.xml, e.g. {sorted(unknown)[:3]}")

    df = df.merge(links[["link_id", "length", "width", "link_tags"]], on="link_id", how="left")
    df["area_m2"] = df.length * df.width
    n = df.agent_count
    # mean of +/-1 -> agent counts per direction (the logger rounds the mean to 3 decimals)
    df["fwd_count"] = (n * (1 + df[DIR]) / 2).round() if DIR in df else n
    df["bwd_count"] = n - df.fwd_count
    two_way = set(df.link_id[df.bwd_count > 0])
    print(f"two-way links detected: {len(two_way)} of {len(links)}")

    for d in ("fwd", "bwd"):
        count, raw = df[f"{d}_count"], f"mean_speed_{d}"
        # The logger's mean_speed_fwd/bwd averages over ALL agents on the link (0 for the other
        # direction), so rescale to the mean over just this direction's agents.
        speed = df[raw] * n / count if raw in df else df.mean_speed  # old CSVs: one shared speed
        df[f"{d}_mean_speed"] = speed.where(count > 0, 0.0)
        df[f"{d}_flow"] = count * df[f"{d}_mean_speed"].abs() / df.length

    df["density"] = n / (df.length * df.width)
    df["flow"] = df.fwd_flow + df.bwd_flow
    df["counterflow_share"] = df[["fwd_count", "bwd_count"]].min(axis=1) / n

    prev, nxt = neighbor_pairs(make_arcs(links, two_way))
    df = df.merge(prev.groupby("link_id").link_id_n.nunique().rename("prev_link_count"), on="link_id", how="left")
    df = df.merge(nxt.groupby("link_id").size().rename("next_link_count"), on="link_id", how="left")
    geo = links.assign(next_width=links.width, next_area=links.length * links.width)
    next_geo = (nxt.merge(geo[["link_id", "next_width", "next_area"]].rename(columns={"link_id": "link_id_n"}),
                          on="link_id_n")
                .groupby("link_id")[["next_width", "next_area"]].sum())
    df = df.merge(next_geo, on="link_id", how="left")
    df["next_width_ratio"] = df.next_width / df.width
    widest_prev = (prev.merge(links[["link_id", "width"]].rename(columns={"link_id": "link_id_n"}), on="link_id_n")
                   .groupby("link_id").width.max().rename("widest_prev"))
    df = df.merge(widest_prev, on="link_id", how="left")
    df["prev_width_ratio"] = (df.pop("widest_prev") / df.width).fillna(1.0)  # >1 = narrowing into L

    # upstream: only the neighbour's agents walking TOWARD L count (its fwd or bwd columns)
    cols = [TICK, "link_id", FORCE] + [f"{d}_{c}" for d in ("fwd", "bwd") for c in ("count", "flow", "mean_speed")]
    up = prev.merge(df[cols].rename(columns={"link_id": "link_id_n"}), on="link_id_n")
    f = up.dir_n == 1
    up = up.assign(n=up.fwd_count.where(f, up.bwd_count), flow=up.fwd_flow.where(f, up.bwd_flow),
                   speed=up.fwd_mean_speed.where(f, up.bwd_mean_speed))
    up = up[up.n > 0].assign(speed_x_count=lambda u: u.n * u.speed)
    up = up.groupby([TICK, "link_id"]).agg(
        prev_inflow=("flow", "sum"), prev_agent_count=("n", "sum"),
        speed_x_count=("speed_x_count", "sum"), prev_max_compression_pressure=(FORCE, "max"))
    up["prev_mean_speed"] = up.pop("speed_x_count") / up.prev_agent_count

    whole = df[[TICK, "link_id", "agent_count", "mean_speed", FORCE]].rename(columns={"link_id": "link_id_n"})
    down = nxt.merge(whole, on="link_id_n").assign(speed_x_count=lambda d: d.agent_count * d.mean_speed)
    down = down.groupby([TICK, "link_id"]).agg(
        next_agent_count=("agent_count", "sum"), speed_x_count=("speed_x_count", "sum"),
        next_max_compression_pressure=(FORCE, "max"))
    down["next_mean_speed"] = down.pop("speed_x_count") / down.next_agent_count

    # Neighbor rows only exist at ticks where that neighbor had agents; missing = empty (0).
    df = df.merge(up.reset_index(), on=[TICK, "link_id"], how="left")
    df = df.merge(down.reset_index(), on=[TICK, "link_id"], how="left")
    df["next_density"] = df.next_agent_count / df.next_area
    fill = [c for c in df.columns if c.startswith(("prev_", "next_"))]
    df[fill] = df[fill].fillna(0)
    drop = ["next_width", "next_area", "next_agent_count", "fwd_flow", "bwd_flow", DIR, "max_link_direction",
            "mean_speed_fwd", "max_speed_fwd", "mean_speed_bwd", "max_speed_bwd"]
    return df.drop(columns=[c for c in drop if c in df])


def selftest():
    # one-way chain 1 -> 2 -> 3, link "b" is the middle one and is half as wide downstream
    links = pd.DataFrame({"link_id": ["a", "b", "c"], "from_node": ["1", "2", "3"],
                          "to_node": ["2", "3", "4"], "length": [10.0, 10.0, 10.0],
                          "width": [4.0, 4.0, 2.0], "link_tags": ""})
    df = pd.DataFrame({TICK: [0, 0, 0, 1], "link_id": ["a", "b", "c", "b"],
                       "agent_count": [20, 10, 5, 8], "mean_speed": [1.0, 0.5, 0.2, 0.4],
                       FORCE: [100.0, 300.0, 900.0, 250.0]})
    out = add_context(df, links).set_index([TICK, "link_id"])
    b0 = out.loc[(0, "b")]
    assert b0.prev_inflow == 20 * 1.0 / 10          # a's flow
    assert b0.prev_agent_count == 20 and b0.prev_mean_speed == 1.0
    assert b0.next_density == 5 / 20 and b0.next_max_compression_pressure == 900
    assert b0.next_width_ratio == 0.5
    b1 = out.loc[(1, "b")]                          # neighbors empty at tick 1
    assert b1.prev_inflow == 0 and b1.next_density == 0
    assert out.loc[(0, "a")].prev_link_count == 0   # spawn link has no upstream

    # two-way: a: 1->2, d: 5->3, c: 3->4, and x: 2->3 with 5 agents forward at 0.4 m/s
    # and 15 backward (3->2) at 0.8 m/s -- logged as the link logger would average them
    links = pd.DataFrame({"link_id": ["a", "x", "d", "c"], "from_node": ["1", "2", "5", "3"],
                          "to_node": ["2", "3", "3", "4"], "length": [10.0] * 4, "width": [2.0] * 4, "link_tags": ""})
    df = pd.DataFrame({TICK: [0] * 4, "link_id": ["a", "x", "d", "c"], "agent_count": [10, 20, 4, 2],
                       "mean_speed": [1.0, 0.7, 1.0, 1.0], DIR: [1.0, -0.5, 1.0, 1.0],
                       "mean_speed_fwd": [1.0, 5 * 0.4 / 20, 1.0, 1.0],
                       "mean_speed_bwd": [0.0, 15 * 0.8 / 20, 0.0, 0.0],
                       FORCE: [0.0, 500.0, 0.0, 0.0]})
    out = add_context(df, links).set_index("link_id")
    x = out.loc["x"]
    assert (x.fwd_count, x.bwd_count) == (5, 15) and x.counterflow_share == 0.25
    assert abs(x.fwd_mean_speed - 0.4) < 1e-9 and abs(x.bwd_mean_speed - 0.8) < 1e-9
    assert x.prev_link_count == 2 and x.prev_inflow == 1.0 + 0.4  # fed from both ends: a and d
    assert x.next_link_count == 1                                  # c (a can't be walked into from node 2)
    assert out.loc["d"].next_link_count == 2                       # c, and x backward toward node 2
    assert out.loc["d"].next_max_compression_pressure == 500
    c = out.loc["c"]                                               # fed by d + x's 5 forward agents only
    assert abs(c.prev_inflow - (0.4 + 5 * 0.4 / 10)) < 1e-9 and c.prev_agent_count == 4 + 5
    assert abs(c.prev_mean_speed - (4 * 1.0 + 5 * 0.4) / 9) < 1e-9
    assert "mean_speed_fwd" not in out and "mean_link_direction" not in out  # raw logger columns replaced
    assert out.loc["a"].prev_width_ratio == 1.0 and out.loc["a"].area_m2 == 20.0

    # DB: two runs on different maps, a logger field only in run 2, and re-loading
    # run 1 must replace it, not duplicate it
    import duckdb
    assert read_json_c(Path(__file__).parent / "prop.json")["map_file"] == "Map.xml"  # // comments ok
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, l in (("one.xml", links.iloc[:2]), ("two.xml", links)):
            ET.ElementTree(ET.fromstring("<Map>" + "".join(
                f'<Link id="{r.link_id}" from="{r.from_node}" to="{r.to_node}" length="{r.length}" '
                f'width="{r.width}"><tag>T{r.link_id}</tag></Link>' for r in l.itertuples()) + "</Map>")
            ).write(tmp / name)
        db = tmp / "t.duckdb"
        write_db(build_run(df[df.link_id.isin(["a", "x"])], tmp / "one.xml", "run1"), db, "run1")
        write_db(build_run(df.assign(max_social_force=7.0), tmp / "two.xml", "run2"), db, "run2")
        n = write_db(build_run(df[df.link_id.isin(["a", "x"])], tmp / "one.xml", "run1"), db, "run1")
        con = duckdb.connect(str(db))
        rows = con.execute(f"SELECT scenario_id, map_file, COUNT(*), MAX(max_social_force), "
                           f"MAX(link_tags) FROM {TABLE} "
                           f"GROUP BY ALL ORDER BY 1").fetchall()
        con.close()
        assert n == 2 + 4
        assert not any(c.startswith("prop_") for c in build_run(df, tmp / "one.xml", "r"))
        label = tmp.name
        assert rows == [("run1", f"{label}/one.xml", 2, None, "Tx"), ("run2", f"{label}/two.xml", 4, 7.0, "Tx")], rows
        # map_id: same contents in another folder -> same id; different map -> different id
        (tmp / "copy").mkdir()
        (tmp / "copy" / "Map.xml").write_bytes((tmp / "one.xml").read_bytes())
        assert map_id(tmp / "copy" / "Map.xml") == map_id(tmp / "one.xml") != map_id(tmp / "two.xml")
    print("selftest ok")


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    p = argparse.ArgumentParser(description="Add link context + map details to one run, store in DuckDB.")
    p.add_argument("link_csv", type=Path, help="LinkAggregateLogger output (link_logging.file)")
    p.add_argument("prop_json", type=Path, help="the properties.json the run used (only map_file is read)")
    p.add_argument("--map", type=Path, default=None, help="default: map_file from prop_json")
    p.add_argument("--db", type=Path, default=Path("crush.duckdb"))
    p.add_argument("--run-id", default=None, help="default: <csv folder>/<csv name>")
    args = p.parse_args()

    run_id = args.run_id or f"{args.link_csv.parent.name}/{args.link_csv.stem}"
    map_xml = args.map or args.prop_json.parent / read_json_c(args.prop_json)["map_file"]
    out = build_run(pd.read_csv(args.link_csv), map_xml, run_id)
    n = write_db(out, args.db, run_id)
    print(f"{args.db}: stored run {run_id!r} ({len(out):,} rows, {len(out.columns)} columns); table now {n:,} rows")


if __name__ == "__main__":
    main()
