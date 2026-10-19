# CrushWalk physics notes

A running record of what was wrong in the pressure model, how it was fixed, what is still a
known limit, and what is left to do. Last updated 2026-10-19.

The code these notes describe is `CrushAgent.rb` (the agent's forces), `prop.json` (the physics
settings) and `make_maps.py` (the generated maps).

## Which data uses which physics

Runs made before 2026-10-09 used the physics as it was before fixes 7 and 8 and before the
`pressureTransfer` change. They must not be mixed with later runs.

| Database | Runs | Physics |
|---|---|---|
| `crush_w4.duckdb`, `crush_w4_nox.duckdb` | seed 0 | before fix 7 |
| `crush_s1.duckdb` | seed 1 | before fix 8 (crossroads rows: fix 7 only) |
| `crush_xr_test.duckdb` | seed 1 crossroads only | fixes 7 and 8, `pressureTransfer` 0.95 |
| `crush_s1b.duckdb` | seeds 1 and 2, 120 runs | current (fixes 1 to 8, `pressureTransfer` 0.97, spawn arms at least 2 m) |
| `crush_s1b_1way.duckdb` | the same minus the 6 two-way crossroads runs of seed 2, 114 runs | before fixes 9 and 10. **This is the one used for the ML results so far.** |

Fixes 9 and 10 (2026-10-19) change every run with a jam, one-way included. Every database made
before them is old physics; the full rerun on Linux replaces them.

## Fixes so far

Fixes 1 to 6 were found from database checks between 2026-09-24 and 2026-10-01. Fixes 7 and 8
were found on 2026-10-08 and 2026-10-09 from the seed 1 batch.

| # | What was wrong | What was changed |
|---|---|---|
| 1 | Peak pressure was 0: nothing pushed from behind. | The nearest row behind now pushes with its own drive plus a share of the pressure it receives. |
| 2 | People walked about 1 m/s at 3 to 4 people/m². | `contactBraking`: people in contact ahead also slow the agent. |
| 3 | Still about 1 m/s, because pushes from behind accelerated the agent forward. | Pushes from behind count as pressure only, never as forward force. |
| 4 | 24,000 N at 1.8 people/m², because anyone within 0.9 m was in the chain. | The chain needs body contact (`physicalSpace`, 0.5 m). |
| 5 | Pressure added up along a queue with no loss. | `pressureTransfer` below 1: each person passes on only a share. |
| 6 | Crossroads peaked at 10⁷ to 10¹³ N. The distance to the crossing node was passed with the wrong sign. | Sign corrected (`distToNode` in `search`). |
| 7 | Crossroads still 8,000 to 56,000 N. The crossing force (slowing for people crossing ahead) was only capped inside the loop over people ahead, so the agent nearest the node kept the full value, and it was passed to the person behind as a physical push. | `calcSocial` returns the braking cap straight away when the crossing force alone is past it. |
| 8 | A single-file spawn arm (1.7 m) reached 6×10⁵ to 7×10⁷ N with 187 people on it. A push from the front was passed back at full strength, and was then also sent forward again in the pressure each agent reports. | A push from the front loses the `pressureTransfer` share per person, and an agent reports forward only the pressure it received from behind. |
| 9 | Single file and two-way links still reached 4,800 to 23,000 N. A push from the front is kept for one tick (update order). When someone stepped into the gap ahead, the agent behind felt the newcomer's push and the old pusher's stale push: two pushes, and the doubled value travelled down the queue (traced: 1,080 to 3,000 to 6,600 N). | The blackboard remembers whom each agent pushed last; a push counts only for that agent (`PhysicsBlackboard.rb`). |
| 10 | Pressure was the push from behind plus the push from the front. A body between two pushes feels the force passing through it once, not twice (user, 2026-10-19). In single file neither push is weakened by a sideways offset, so the sum reached about 2,900 N. | Pressure = the bigger of the two. The push from the front is weakened by the room behind (full when touching someone or at the link start, down to half with one step, `backStepLength` 0.7 m, of room: a free body pushed on one side is squeezed about F/2 on average); the push from behind likewise by the room ahead, counted as the smaller of the gap ahead and how far the agent walked last tick, so an agent held still at a node gets the full push. Body resistance is subtracted after, as before. |

What each fix was checked against:

- Fix 7: on the seed 1 rerun, the three crossroads maps without a single-file arm dropped to at
  most 2,355 N, the same ceiling as the other families at that time.
- Fix 8: on the seed 1 crossroads rerun, the single-file map dropped to 3,520 to 7,014 N.
  The mechanism was read from the code and fits the numbers, but it was not confirmed from
  per-agent logs.
- Fixes 9 and 10 (2026-10-19, small test set run on Windows with the fix kept in the separate
  `patchSingleLane.py`, sets `onevictim` and `squeeze`; per-agent logs from its `diag` set):

  | Runs | Before | After 9 and 10 |
  |---|---|---|
  | narrow 1.7 m spawn arm, one-way, 3 runs | 7,269 to 23,377 N | 1,723 to 1,901 N |
  | two-way crossroads (seed 2 `crossroads_002`, `003`), 6 runs | 417 to 4,870 N | 552 to 2,006 N |
  | one-way crossroads (seed 2 `crossroads_000`, `001`), 6 runs | 503 to 1,935 N | 366 to 1,832 N |

  One-way peaks fall 5 to 9 %, and which runs have a crush is unchanged in the one-way runs; one
  two-way run (`003_run_2`) lost its crush. Fix 9 alone (6 two-way runs): 4,793 to 2,649 N and
  4,870 to 2,744 N. Reruns are byte-identical, so the comparison is exact. The `backStepLength`
  of 0.7 m is a typical walking step, from memory. A backward step after a push is shorter:
  first step 24.8 ± 7.2 cm, total distance moved back 37.8 ± 12.1 cm (push-and-release test, 60
  young adults; Muthukumar et al. 2025, Physiotherapy Quarterly 33(2), Table 1). Whether to change
  it is open (To-check list, section 7).
  Changed to 0.38 m (user, 2026-10-20) and checked on 9 of the 15 test runs: peaks, ticks over
  1,062 N and crushes are all unchanged. At most 15 link-ticks per run differ, by up to -55 N,
  never at a peak. In a jam people touch (no room), so the weakening hardly ever applies, and the
  squeeze does not feed back into movement. So results do not depend on this uncertain number
  between 0.38 and 0.7 m.

## Calibration choices (no source behind them)

**`pressureTransfer` = 0.97.** After fix 8, the highest pressure a standing queue can reach is
about (push one person adds) / (1 − `pressureTransfer`). Measured on the seed 1 crossroads
test at 0.95: a ceiling of 1,043 to 1,091 N after the agent's own resistance, which is just
under the 1,112 N crush threshold, so no run crushed. Estimated ceilings for other values
(estimates from that one measurement, not yet run):

| `pressureTransfer` | Ceiling (N) | Crushes |
|---|---|---|
| 0.95 | about 1,090 | none |
| 0.96 | about 1,440 | only in long, sustained queues |
| 0.97 | about 2,010 | similar to the runs before fix 8 |
| 0.975 | about 2,470 | slightly more |

0.97 was chosen on 2026-10-09 because it puts the ceiling near the roughly 2,300 N seen in all
earlier runs. Part of that earlier ceiling came from the double counting that fix 8 removed.

Checked on the full seed 1 rerun (`crush_s1b.duckdb`, 60 runs, 2026-10-10): the highest peak
in any run is 1,968 N, against the estimate of about 2,010 N. 16 of 60 runs end in a crush
(corridor 5, crossroads 8, funnel 2, tree 1, T-junction 0), and 18 reach 1,062 N. The former
single-file map `crossroads_000` (west arm now 2.0 m) peaks at 1,095 to 1,968 N, in line with
the other maps.

With seed 2 added and the six two-way runs left out (`crush_s1b_1way.duckdb`, 114 runs): the
highest peak is still 1,968 N; 27 runs end in a crush (corridor 9 of 24, crossroads 12 of 18,
funnel 2 of 24, T-junction 3 of 24, tree 1 of 24) and 32 reach 1,062 N.

**Minimum spawn arm width 2 m in crossroads maps.** See the single-file limit below.

**Crush rule.** 1,112 N (Kroll et al. 2017) held for 240 s, with a 50 N margin once the timer
has started. The threshold and the 4 to 6 minute duration are from the paper; the 50 N margin
is ours.

## Known limits of the research

1. **Whether a crush happens depends on `pressureTransfer`.** It is a calibration setting with
   no measured value behind it. At 0.95 the simulator produced no crush (measured); the
   estimate is that it needs to be above about 0.951 for any crush, and 0.97 was chosen. Any statement about how often crushes occur is conditional on this value.
2. **Single-file queues are outside the model.** The simulator turns link width into whole
   lanes (`int(width)`), so a link under 2 m is one lane, but it still fills the link by area.
   About 6 people then stand in each metre of one line, and the 0.5 m contact logic does not
   hold. Fix 8 bounds the pressure there, but it stays 3 to 7 times higher than on wider links.
   Since fixes 9 and 10 the narrow test arm stays under the one-way ceiling (1,723 to 1,901 N),
   so the pressure is no longer inflated there; the 6 people per metre is still unchanged.
   Generated maps avoid single-file spawn arms; narrow bottleneck and exit links (down to
   1.5 m) still exist, and were fine in the runs so far because the queue forms upstream of them.
3. **The model's truth is the simulator's truth.** The ML model learns "CrushWalk says this is
   a crush". Its real-world validity is bounded by how faithful the contact-force model is.
4. **Link averages hide local queues.** `density` is the average over a whole link, so a queue
   packed into part of a long link looks moderate.
5. **Pushing does not cross nodes.** A queue on an approach link does not push people who are
   already in the bottleneck link, and agents behind on the previous link are not seen.
6. **One-tick lag in the push chain.** Depending on update order, pressure moves one person
   per tick (1 s). Fine at 1 s ticks.
7. **Map and crowd ranges are hand-picked.** Widths, lengths, crowd sizes and timings in
   `make_maps.py` are plausible values, not taken from a design standard or real venues.
8. **Two-way flow is outside the model, so everything is tested on one-way flow only.**
   Seed 2 has two two-way crossroads maps (`crossroads_002`, `crossroads_003`). Three of their
   six runs peak at 2,591, 4,793 and 4,870 N, above the 1,968 N ceiling that every one-way run
   stays under. The cause is limit 2 again: a two-way link shares its lanes between the
   directions by head count (`MapLink.getLaneWidth`, vanilla CrowdWalk: `int(own count x width /
   both counts)`, at least 1), so on a 4 m link the smaller direction is single file whenever it
   has under half the people (21:19 already gives 2 + 1 lanes). All six runs on those two maps
   are left out of the analysis (`crush_s1b_1way.duckdb`, 114 runs), chosen by map type and not
   by result. Counterflow features have therefore never had usable data.
   - Checked in the data (2026-10-19, `crush_s1b.duckdb`, the six two-way runs): link-ticks with
     both directions and the smaller one on 1 lane: 53,148 ticks, peak 4,870 N, mean 316 N; with
     2 or 3 lanes: 488 ticks, peak 16 N; one direction only: 39,062 ticks, peak 2,363 N. That one
     one-direction tick above 1,968 N came within 30 s after the same link was two-way, so it is
     most likely pressure left over from the squeeze. Every tick above the one-way ceiling is
     during or just after a single-lane squeeze. Not proof of cause: an even split (2+ lanes)
     mostly happens when few people are on the link, so lanes and crowding are mixed together.
     Queries:
     ```
     SELECT (fwd_count>0 AND bwd_count>0) AS both_ways, greatest(1, floor(least(fwd_count,bwd_count)*width/greatest(fwd_count+bwd_count,1))) AS smaller_dir_lanes, count(*), max(max_compression_pressure), avg(max_compression_pressure) FROM link_ticks WHERE regexp_matches(scenario_id,'^seed_2/crossroads_00[23]/') GROUP BY ALL
     SELECT count(*), sum(CASE WHEN recent_both THEN 1 ELSE 0 END) FROM (SELECT *, coalesce(max(CASE WHEN fwd_count>0 AND bwd_count>0 THEN 1 ELSE 0 END) OVER (PARTITION BY scenario_id, link_id ORDER BY current_traveling_period RANGE BETWEEN 30 PRECEDING AND 1 PRECEDING),0)=1 AS recent_both FROM link_ticks WHERE regexp_matches(scenario_id,'^seed_2/crossroads_00[23]/')) WHERE NOT (fwd_count>0 AND bwd_count>0) AND max_compression_pressure>1968
     ```
   - The rounding down also wastes a lane (21:19 on 4 m uses 3 of 4). A fix that never
     overflows: smaller direction = max(1, floor(share)), bigger = the rest; one-way unchanged.
     This is a cleanup only: the smaller direction is still single file at 30:10, so the
     single-file handling below is the real fix (user's decision, 2026-10-19).
   - Fixes 9 and 10 bring the two-way test runs to 552 to 2,006 N, at the one-way ceiling. The
     two-way maps can come back after the Linux rerun confirms this on all runs.
   - Not checked yet: whether walkers going opposite ways touch at all in the contact logic
     (`MapLink` keeps `forwardLane` and `backwardLane` as separate lists).

## To work on eventually

- Find a measured basis for `pressureTransfer`, or show how results change across a range of
  values (a sensitivity check), so limit 1 becomes a reported result.
- Handle single-file links properly: either limit how many people can stand in a metre of one
  lane, or make the contact logic aware of lane width. Then the 2 m minimum can go.
- ~~Confirm the fix 8 mechanism from per-agent logs.~~ Done 2026-10-19: the remaining blow-up
  was the stale second push (fix 9).
- `backStepLength` set to 0.38 m (Muthukumar et al. 2025); results barely change between 0.38 and 0.7 m.
- Make two-way flow usable (it needs the single-file handling above), then bring the two-way
  maps and the counterflow features back in.
- Let pushing cross nodes (limit 5).
- Rerun seed 0 with the current physics if those maps are wanted again.
- Remove the temporary DEBUG counters in `CrushAgent.rb` once the physics has settled.
- Test on a realistic network (a station or stadium exit), which has not been started.
