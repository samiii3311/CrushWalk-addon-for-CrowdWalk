#!/usr/bin/env python3
"""
random_net.py

The "randnet" map family: a random street network, like SUMO's `netgenerate --rand`. Not in
make_maps' default families (D4 keeps the five); for R3, the unseen-map test. Use it with
`make_maps.py --families randnet` or `run_batch.py --families randnet`.

  - Nodes on an nx x ny grid (3-5 each, 40-100 m apart), each moved by up to
    (1 - grid) x 0.35 x spacing. grid (0-1, per map) plays the part of SUMO's rand.grid:
    1 = city blocks, 0 = irregular. Low grid also adds some diagonal streets (one per cell at most).
  - Some streets are dropped (0-30% per map), never so many that the network falls apart.
  - Widths 2-8 m; about 15% of streets narrow (1.5-3 m).
  - 2-4 spawn groups enter on short stubs at the edge (at least 2 m wide, like crossroads) and
    walk to one of 1-2 exits, also stubs at the edge.
  - Bottleneck for --bottleneck-walls: the street that the most groups' shortest routes share,
    the narrowest one if several tie (bottleneck_link). Assumes people take the shortest route,
    which is CrowdWalk's default routing.

ponytail: all ranges are hand-picked, like the other families'; change them here.
Check: python3 Crush3/random_net.py --selftest
"""
import heapq
import math
import sys


def build(r, m):
    """r: random.Random for this map, m: an empty make_maps.MapBuilder. Returns (m, groups, params)."""
    nx, ny, sp, grid = r.randint(3, 5), r.randint(3, 5), r.uniform(40, 100), r.random()
    jit = (1 - grid) * 0.35 * sp
    pts = {(i, j): m.node(i * sp + r.uniform(-jit, jit), j * sp + r.uniform(-jit, jit))
           for i in range(nx) for j in range(ny)}
    edges = [((i, j), (i + 1, j)) for i in range(nx - 1) for j in range(ny)]
    edges += [((i, j), (i, j + 1)) for i in range(nx) for j in range(ny - 1)]
    edges += [((i, j), (i + 1, j + 1)) if r.random() < 0.5 else ((i + 1, j), (i, j + 1))
              for i in range(nx - 1) for j in range(ny - 1) if r.random() < 0.3 * (1 - grid)]
    drop = r.uniform(0, 0.3)
    r.shuffle(edges)
    keep = list(edges)
    for e in edges:
        if r.random() < drop and connected(pts, [k for k in keep if k != e]):
            keep.remove(e)
    for a, b in keep:
        m.link(pts[a], pts[b], r.uniform(1.5, 3) if r.random() < 0.15 else r.uniform(2, 8))

    cx, cy = (nx - 1) * sp / 2, (ny - 1) * sp / 2
    edge_nodes = [pts[k] for k in sorted(pts) if k[0] in (0, nx - 1) or k[1] in (0, ny - 1)]
    k_spawn, k_goal = r.randint(2, 4), r.randint(1, 2)
    picks = r.sample(edge_nodes, k_spawn + k_goal)

    def stub(n):
        dx, dy = n["x"] - cx, n["y"] - cy
        d, L = math.hypot(dx, dy) or 1.0, r.uniform(20, 40)
        return n["x"] + dx / d * L, n["y"] + dy / d * L

    groups = []
    for g, n in enumerate(picks[k_spawn:]):
        m.link(n, m.node(*stub(n), f"Goal{g}"), r.uniform(2, 6))
    for s, n in enumerate(picks[:k_spawn]):
        m.link(m.node(*stub(n)), n, r.uniform(3, 8), f"Spawn{s}")
        groups.append((f"Spawn{s}", f"Goal{r.randrange(k_goal)}"))

    shared = {}
    for spawn, goal in groups:
        for lid in route(m, spawn, goal):
            shared[lid] = shared.get(lid, 0) + 1
    links = {l["id"]: l for l in m.links}
    bl = max(shared, key=lambda i: (shared[i], -links[i]["width"]))
    return m, groups, {"grid": round(grid, 3), "nx": nx, "ny": ny, "spacing": round(sp, 1),
                       "dropped_share": round(drop, 3), "spawns": k_spawn, "exits": k_goal,
                       "bottleneck_link": bl, "bottleneck_width": links[bl]["width"],
                       "bottleneck_shared_by": shared[bl]}


def connected(pts, edges):
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen, todo = set(), [next(iter(pts))]
    while todo:
        k = todo.pop()
        if k not in seen:
            seen.add(k)
            todo += adj.get(k, [])
    return len(seen) == len(pts)


def route(m, spawn_tag, goal_tag):
    """Link IDs on the shortest walk from the spawn link to the goal node (links are two-way)."""
    adj = {}
    for l in m.links:
        adj.setdefault(l["from"], []).append((l["to"], l))
        adj.setdefault(l["to"], []).append((l["from"], l))
    sl = next(l for l in m.links if spawn_tag in l["tags"])
    goal = next(n["id"] for n in m.nodes if goal_tag in n["tags"])
    dist, prev, heap = {sl["from"]: 0.0}, {}, [(0.0, sl["from"])]
    while heap:
        d, u = heapq.heappop(heap)
        if u == goal:
            break
        if d > dist[u]:
            continue
        for v, l in adj[u]:
            if d + l["length"] < dist.get(v, math.inf):
                dist[v], prev[v] = d + l["length"], (u, l["id"])
                heapq.heappush(heap, (dist[v], v))
    path, u = [], goal
    while u in prev:
        u, lid = prev[u]
        path.append(lid)
    return path


def selftest():
    import random
    import tempfile
    from pathlib import Path
    import make_maps
    from link_context import parse_links
    out = Path(tempfile.mkdtemp())
    rows = make_maps.generate(out, 20, 7, make_maps.HERE, None, families=["randnet"], runs_per_map=1,
                              wall_share=0.3, bottleneck_walls=1.0)
    assert len(rows) == 20 and {r["wall_kind"] for r in rows} == {"bottleneck"}
    assert len({(r["nx"], r["ny"], r["spawns"]) for r in rows}) > 3  # maps differ
    for row in rows:
        m, groups, p = build(random.Random(f"7-{row['map']}"), make_maps.MapBuilder())
        assert p["bottleneck_link"] == row["bottleneck_link"]  # same seed -> same map
        assert all(route(m, s, g) for s, g in groups)  # every exit reachable
        counts = [sum(lid in route(m, s, g) for s, g in groups) for lid in [l["id"] for l in m.links]]
        assert p["bottleneck_shared_by"] == max(counts)
        assert all(l["width"] >= 2 for l in m.links if any(t.startswith("Spawn") for t in l["tags"]))
        links = parse_links(out / row["map"] / "Map.xml").set_index("link_id")
        assert "WALL2" in links.loc[row["bottleneck_link"], "link_tags"].split(";"), row["map"]
    print(f"selftest ok: {len(rows)} random networks in {out}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        selftest()
    else:
        sys.exit(__doc__)
