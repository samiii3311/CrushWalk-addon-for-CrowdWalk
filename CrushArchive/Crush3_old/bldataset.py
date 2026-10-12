#!/usr/bin/env python3
"""
bldataset.py

a link-tick ML dataset with density,density_trend, queue_count, bottleneck_ratio,
and a forward-looking crush_within_horizon label) but built to scale to large runs 
(10k+ agents,multi-million-row raw telemetry).

All grouping and window-function work happens INSIDE DuckDB, directly
against the raw telemetry file on disk (CSV or Parquet) - pandas/Python
memory only ever holds the small link-geometry table. The full link-tick
output is streamed straight to disk via DuckDB's COPY, never fully
materialized in Python.

Usage:
    python3 bldataset.py telemetry.parquet Map.xml --output ml_dataset.parquet
    python3 bldataset.py telemetry.csv Map.xml --output ml_dataset.csv --horizon 30

Recommended: convert raw run CSVs to Parquet first (much faster repeated
querying), e.g.:
    duckdb -c "COPY (SELECT * FROM read_csv_auto('run.csv')) TO 'run.parquet' (FORMAT PARQUET)"
"""

import argparse
import sys
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

import duckdb
import pandas as pd


STATIONARY_EPS = 0.02  # m/s - below this, treat an agent as queued/stopped


def parse_args():
    p = argparse.ArgumentParser(description="Build ML-ready link-tick dataset (DuckDB-backed, scales to large runs).")
    p.add_argument("telemetry_path", type=Path, help="Raw per-agent-tick telemetry file (.csv or .parquet)")
    p.add_argument("map_xml", type=Path, help="CrowdWalk Map.xml")
    p.add_argument("--output", type=Path, default=Path("ml_dataset.parquet"),
                   help="Output path (.csv or .parquet), default: ml_dataset.parquet")
    p.add_argument("--horizon", type=int, default=30,
                   help="Forecast horizon in ticks for crush_within_horizon label (default: 30)")
    p.add_argument("--trend-window", type=int, default=5,
                   help="Lookback window (ticks) for the density_trend feature (default: 5)")
    p.add_argument("--width-unit", type=float, default=0.9,
                   help="widthUnit_SameLane used to derive lane_count from link width (default: 0.9)")
    p.add_argument("--scenario-id", type=str, default=None,
                   help="Optional scenario/run identifier stamped onto every output row, "
                        "useful when concatenating many scenario runs later")
    return p.parse_args()


def parse_map(map_xml_path: Path, width_unit: float) -> pd.DataFrame:
    """Extract static per-link geometry, including bottleneck_ratio, from the map XML.

    bottleneck_ratio for link L (from node A to node B) = the widest link that
    feeds INTO A, divided by L's own width. A link with no upstream feeder
    (e.g. a spawn link) gets ratio = 1.0.
    """
    tree = ET.parse(map_xml_path)
    root = tree.getroot()

    links = []
    for link_el in root.iter("Link"):
        links.append({
            "link_id": link_el.get("id"),
            "from_node": link_el.get("from"),
            "to_node": link_el.get("to"),
            "length": float(link_el.get("length", 0.0)),
            "width": float(link_el.get("width", 1.0)),
        })
    link_df = pd.DataFrame(links)
    if link_df.empty:
        print("[!] Warning: no <Link> elements found in map XML.")
        return link_df

    link_df["area_m2"] = link_df["length"] * link_df["width"]
    link_df["lane_count"] = (link_df["width"] / width_unit).round().clip(lower=1)

    widest_incoming = link_df.groupby("to_node")["width"].max()

    def bottleneck_ratio(row):
        upstream_width = widest_incoming.get(row["from_node"])
        if upstream_width is None or row["width"] <= 0:
            return 1.0
        return upstream_width / row["width"]

    link_df["bottleneck_ratio"] = link_df.apply(bottleneck_ratio, axis=1)

    return link_df[["link_id", "length", "width", "area_m2", "lane_count", "bottleneck_ratio"]]


def build_query(telemetry_source: str, horizon: int, trend_window: int, scenario_id: Optional[str]) -> str:
    scenario_col = f"'{scenario_id}' AS scenario_id," if scenario_id else ""

    return f"""
    WITH agg AS (
        SELECT
            link_id,
            current_traveling_period,
            COUNT(*) AS agent_count,
            AVG(current_speed) AS mean_speed,
            COALESCE(STDDEV(current_speed), 0.0) AS std_speed,
            AVG(push_force) AS mean_push_force,
            MAX(push_force) AS max_push_force,
            MAX(compression_pressure) AS max_compression_pressure,
            COALESCE(MAX(agent_status), 0) AS crush_now,
            SUM(CASE WHEN ABS(current_speed) < {STATIONARY_EPS} THEN 1 ELSE 0 END) AS queue_count
        FROM {telemetry_source}
        WHERE link_id IS NOT NULL AND link_id != ''
        GROUP BY link_id, current_traveling_period
    ),
    joined AS (
        SELECT
            {scenario_col}
            agg.*,
            g.length, g.width, g.area_m2, g.lane_count, g.bottleneck_ratio,
            agg.agent_count / NULLIF(g.area_m2, 0) AS density,
            agg.queue_count::DOUBLE / NULLIF(agg.agent_count, 0) AS queue_ratio
        FROM agg
        LEFT JOIN link_geo g USING (link_id)
    ),
    windowed AS (
        SELECT
            *,
            density - LAG(density, {trend_window}) OVER (
                PARTITION BY link_id ORDER BY current_traveling_period
            ) AS density_trend,
            COALESCE(MAX(crush_now) OVER (
                PARTITION BY link_id ORDER BY current_traveling_period
                ROWS BETWEEN 1 FOLLOWING AND {horizon} FOLLOWING
            ), 0) AS crush_within_horizon
        FROM joined
    )
    SELECT * FROM windowed ORDER BY link_id, current_traveling_period
    """


def main():
    args = parse_args()

    if not args.telemetry_path.exists():
        print(f"[!] Telemetry file not found: {args.telemetry_path}")
        sys.exit(1)
    if not args.map_xml.exists():
        print(f"[!] Map XML not found: {args.map_xml}")
        sys.exit(1)

    print(f">>> Parsing map geometry from {args.map_xml}")
    link_geo = parse_map(args.map_xml, args.width_unit)
    print(f"    Found {len(link_geo)} links. Bottleneck ratio range: "
          f"{link_geo['bottleneck_ratio'].min():.2f} - {link_geo['bottleneck_ratio'].max():.2f}")

    con = duckdb.connect()
    con.register("link_geo", link_geo)

    is_parquet = args.telemetry_path.suffix.lower() == ".parquet"
    reader = "read_parquet" if is_parquet else "read_csv_auto"
    telemetry_source = f"{reader}('{args.telemetry_path.as_posix()}')"
    print(f">>> Querying telemetry via DuckDB ({'parquet' if is_parquet else 'csv'} source, "
          f"streamed - not loaded into pandas)")

    query = build_query(telemetry_source, args.horizon, args.trend_window, args.scenario_id)

    print(">>> Running aggregation + window functions in DuckDB and writing output directly to disk")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.suffix == ".parquet":
        con.execute(f"COPY ({query}) TO '{args.output.as_posix()}' (FORMAT PARQUET)")
    else:
        con.execute(f"COPY ({query}) TO '{args.output.as_posix()}' (HEADER, DELIMITER ',')")
    print(f">>> Wrote {args.output}")

    print(">>> Computing diagnostics (small aggregate query, not the full dataset)")
    n_rows, n_pos = con.execute(f"""
        SELECT COUNT(*), SUM(CASE WHEN crush_within_horizon = 1 THEN 1 ELSE 0 END)
        FROM ({query})
    """).fetchone()
    positive_rate = (n_pos / n_rows) if n_rows else 0.0
    print(f"    {n_rows:,} link-tick rows, {n_pos:,} positive (crush_within_horizon=1), "
          f"rate = {positive_rate:.4%}")
    if n_pos == 0:
        print("    [!] No positive examples in this run. See earlier discussion on widening the")
        print("        pre-bottleneck approach or lowering crushResistance/crushThreshold to")
        print("        generate training data, then rerun this script per scenario.")


if __name__ == "__main__":
    main()
