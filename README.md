# CrushWalk: physical crowd pressure and crush dynamics addon for CrowdWalk

CrushWalk is an experimental addon for the [CrowdWalk](https://github.com/crest-cassia/CrowdWalk) pedestrian simulator. It patches the core Java engine to add social-force pressure accumulation, ghost-agent collision handling, and per-tick telemetry, then layers Ruby-side agent scripts on top for the physics model itself.

## What it adds

CrowdWalk agents normally block each other on a lane. CrushWalk adds two boolean flags to `AgentBase`, `allowOverlap` and `ghostMode`. When `ghostMode` is set, `WalkAgent.calcSocialForce`, `calcSocialForceToHeading`, and `accumulateSocialForces` all short-circuit to `0.0`, and `AgentHandler`'s same-lane predecessor search skips the agent entirely. That is the mechanism a ghost agent uses to pass through a jam without contributing to it or blocking anyone else. The Ruby side handles the actual physics: `GhostAgentManager.rb` (in `Crush2/`, `Crush3/`, `CrushTesting/`, and `Test/`) decides when an agent should become a ghost and tracks a shared blackboard of pressure state.

`AgentHandler` also gains two loggers, generated and wired in by the patcher:

- `DynamicAgentLogger` writes per-tick CSV rows (position, speed, force, crushed state) for whatever fields a scenario's `properties.json` lists under `dynamic_logging.fields`.
- `LinkAggregateLogger` writes link-level aggregates over the same run.

`EvacuationSimulator` and `BasicSimulationLauncher` are patched so the crushed/evacuated counts show up in the exit condition and the run's console summary.

## Repository structure

```
crowdwalk/
├── Crush2/              Intermediate Ruby agent prototype and scenarios
├── Crush3/              Blackboard-architecture prototype and physics tests
├── CrushTest/           Integration test setup and plotting utilities
├── CrushTesting/        Runnable benchmark scenarios: 8 map topologies
│                        (1OneWay.xml … 8RandomMovement.xml, covering one-way
│                        corridors, turns, funnels, crossroads, and random
│                        motion) plus the Ruby agent scripts, gen/prop/scenario
│                        JSON, and a code.py utility needed to actually run them
├── Test/                Real-world map (Higashiv2.xml) and its gen/prop/
│                        scenario configs
├── patchCrowd.py        Patcher: string-anchored search/replace, generates
│                        DynamicAgentLogger.java and LinkAggregateLogger.java,
│                        builds with Gradle
├── run.py               Batch parameter-sweep runner and plotter (hardcodes
│                        a Linux path and output directory; edit both before
│                        running on this machine)
└── gpxToCsv.py          Converts a GPX trace to CrowdWalk-compatible CSV
```

## Setup

### Requirements

- JDK 17 (the build is pinned to it via `org.gradle.java.home` in `gradle.properties`, regardless of what else is installed)
- Python 3.8+
- CrowdWalk cloned locally, with `build.gradle` reachable from the current or parent directory (the patcher uses that to find the repo root)

### Environment variables

CrowdWalk needs these set before `quickstart.sh` or a Gradle run will behave correctly:

```
LANG=ja_JP.UTF-8
JAVA_OPTS='-Dgroovy.source.encoding=UTF-8 -Dfile.encoding=UTF-8'
```

### Patching and building

Run the patcher from the `crowdwalk/` directory:

```
python patchCrowd.py
```

It is idempotent: each patch is guarded by a marker string, so re-running it after it has already applied a change is a no-op. It finishes by running `./gradlew compileJava jar -x test -x check`, the same fast-rebuild command to use directly after any further hand-edit to the patched files.

If you hand-edit a file under a `// === Custom: ... ===` block, you have desynced it from the patcher's anchors. Update the corresponding `search=`/`replace=` string in `patchCrowd.py` instead of leaving the `.java` file as the only copy of the change.

## Adding a custom telemetry field

`DynamicAgentLogger` reads whatever field names a scenario asks for, so a new per-agent metric does not need a Java rebuild:

1. Compute the value in the agent script, `PhysicalAgent.rb`.
2. Pass it into `TelemetryHandler.rb` during the per-tick update.
3. Map it to an output key inside `TelemetryHandler.rb`.
4. Add that key to the `dynamic_logging.fields` array in the scenario's `properties.json`.

CrowdWalk picks up the new column in `dynamic_metrics.csv` automatically on the next run.

## Running simulations

A single scenario, through CrowdWalk's own launcher:

```
sh quickstart.sh ./CrushTesting/prop.json -g2
```

A parameter sweep across multiple runs (edit the hardcoded paths in `run.py` first):

```
python run.py
```

A GPX trace converted to model coordinates:

```
python gpxToCsv.py input_track.gpx output_coordinates.csv
```

## Output

- `log_crushed_agents.csv`: timestamp, agent ID, link, and node coordinates for each crush-threshold breach.
- `dynamic_metrics.csv`: per-tick position, speed, and force/compression for every agent, plus any custom fields declared in `properties.json`.

## Notes

- `crowdwalk/.gitignore` denies everything by default and only allowlists `*[Cc]rush*` paths plus root-level `.json`/`.rb`/`.py`/`.xml` files. A new file added under `src/`, `test/`, `sample/`, or `tools/` needs `git add -f` or it will silently stay untracked.
- This addon is under active development as of September 2026. Parts of the code and this document were written with AI assistance.
