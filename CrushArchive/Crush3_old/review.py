#!/usr/bin/env python3
"""
review_csv.py
Chunked, memory-efficient reviewer for large CrowdWalk telemetry CSVs.
Doesn't load the whole file into memory - processes it in streaming chunks,
so it works on files far too big for Excel or a naive pandas.read_csv().

Usage:
    python3 review_csv.py path/to/file.csv
    python3 review_csv.py path/to/file.csv --chunksize 500000 --sample-frozen 5
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np


def parse_args():
    p = argparse.ArgumentParser(description="Review a large CrowdWalk telemetry CSV.")
    p.add_argument("csv_path", type=Path, help="Path to the CSV file")
    p.add_argument("--chunksize", type=int, default=250_000,
                    help="Rows per chunk (default: 250000)")
    p.add_argument("--sample-frozen", type=int, default=5,
                    help="How many example 'frozen row' agent_ids to print (default: 5)")
    return p.parse_args()


def main():
    args = parse_args()
    csv_path = args.csv_path

    if not csv_path.exists():
        print(f"[!] File not found: {csv_path}")
        sys.exit(1)

    file_size_mb = csv_path.stat().st_size / (1024 * 1024)
    print(f">>> Reviewing: {csv_path}")
    print(f">>> File size: {file_size_mb:,.1f} MB")
    print(f">>> Chunk size: {args.chunksize:,} rows\n")

    total_rows = 0
    columns = None
    numeric_stats = {}          # col -> running min/max/sum/count/nan_count
    non_numeric_examples = defaultdict(set)  # col -> set of a few bad values seen
    duplicate_full_rows = 0
    negative_speed_rows = 0

    # Track, per agent_id, the last row's values for the "frozen/stale repeat" check.
    # Only keep a lightweight signature (not the whole row) to bound memory.
    last_signature = {}          # agent_id -> (speed, other_numeric_fields_tuple)
    frozen_run_length = defaultdict(int)   # agent_id -> consecutive identical-row count
    frozen_examples = []         # list of (agent_id, run_length) worth flagging
    max_frozen_run = defaultdict(int)      # agent_id -> longest run seen

    # For duplicate full-row detection without holding everything in memory,
    # compare each row to the immediately preceding row per agent_id only
    # (catches the "same agent repeated identically tick after tick" bug pattern).
    prev_row_per_agent = {}

    first_chunk = True
    reader = pd.read_csv(csv_path, chunksize=args.chunksize, dtype=str, keep_default_na=False)

    for chunk in reader:
        if first_chunk:
            columns = list(chunk.columns)
            print(f">>> Columns detected: {columns}\n")
            for col in columns:
                numeric_stats[col] = {"min": None, "max": None, "sum": 0.0,
                                       "count": 0, "nan_count": 0}
            first_chunk = False

        total_rows += len(chunk)

        # --- Column-wise numeric profiling ---
        for col in columns:
            if col == "agent_id":
                continue
            series = pd.to_numeric(chunk[col], errors="coerce")
            nan_mask = series.isna()
            n_nan = int(nan_mask.sum())
            numeric_stats[col]["nan_count"] += n_nan

            bad_vals = chunk.loc[nan_mask, col].unique()
            for v in bad_vals[:3]:
                if len(non_numeric_examples[col]) < 5:
                    non_numeric_examples[col].add(v)

            valid = series.dropna()
            if not valid.empty:
                cmin, cmax = valid.min(), valid.max()
                numeric_stats[col]["min"] = cmin if numeric_stats[col]["min"] is None else min(numeric_stats[col]["min"], cmin)
                numeric_stats[col]["max"] = cmax if numeric_stats[col]["max"] is None else max(numeric_stats[col]["max"], cmax)
                numeric_stats[col]["sum"] += valid.sum()
                numeric_stats[col]["count"] += valid.count()

        # --- Negative speed check ---
        if "current_speed" in chunk.columns:
            speed_numeric = pd.to_numeric(chunk["current_speed"], errors="coerce")
            negative_speed_rows += int((speed_numeric < -1e-9).sum())

        # --- Frozen/stale-repeat detection per agent_id ---
        if "agent_id" in chunk.columns:
            compare_cols = [c for c in columns if c not in ("current_traveling_period", "generated_time")]
            for row in chunk.itertuples(index=False):
                row_dict = row._asdict() if hasattr(row, "_asdict") else dict(zip(columns, row))
                agent = row_dict["agent_id"]
                signature = tuple(row_dict[c] for c in compare_cols)

                prev = prev_row_per_agent.get(agent)
                if prev == signature:
                    frozen_run_length[agent] += 1
                    duplicate_full_rows += 1
                    if frozen_run_length[agent] > max_frozen_run[agent]:
                        max_frozen_run[agent] = frozen_run_length[agent]
                else:
                    frozen_run_length[agent] = 0

                prev_row_per_agent[agent] = signature

        print(f"    ...processed {total_rows:,} rows so far", end="\r")

    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Total rows: {total_rows:,}")
    print(f"Unique agent_ids seen: {len(prev_row_per_agent):,}")
    print(f"Rows that exactly repeat the previous row for the same agent_id: {duplicate_full_rows:,}")
    if "current_speed" in (columns or []):
        print(f"Rows with negative current_speed: {negative_speed_rows:,}")

    print(f"\n{'-'*60}")
    print("NUMERIC COLUMN STATS")
    print(f"{'-'*60}")
    for col, stats in numeric_stats.items():
        if col == "agent_id":
            continue
        count = stats["count"]
        mean = stats["sum"] / count if count else float("nan")
        print(f"{col:>20}: min={stats['min']!s:>12}  max={stats['max']!s:>12}  "
              f"mean={mean:>10.4f}  non-numeric/blank={stats['nan_count']:,}")
        if non_numeric_examples[col]:
            print(f"{'':>20}  example non-numeric values: {sorted(non_numeric_examples[col])[:5]}")

    print(f"\n{'-'*60}")
    print(f"TOP AGENTS BY LONGEST FROZEN/STALE ROW RUN (top {args.sample_frozen})")
    print(f"{'-'*60}")
    if max_frozen_run:
        top_frozen = sorted(max_frozen_run.items(), key=lambda kv: kv[1], reverse=True)[:args.sample_frozen]
        for agent, run_len in top_frozen:
            if run_len > 0:
                print(f"  agent_id={agent}: {run_len} consecutive identical rows")
        if all(v == 0 for _, v in top_frozen):
            print("  (none found - no agent had back-to-back identical rows)")
    else:
        print("  (no agent_id column found, or file empty)")

    print(f"\n{'='*60}")
    print("Done.")


if __name__ == "__main__":
    main()
