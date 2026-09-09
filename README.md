
# CrushWalk: Physical Crowd Pressure & Crush Dynamics Addon for CrowdWalk

**CrushWalk** is an experimental physics and telemetry addon for the [CrowdWalk](https://github.com/crest-cassia/CrowdWalk) pedestrian simulation framework. It extends default navigation behavior with realistic physical interaction, side-force pressure accumulation, dynamic bottleneck resistance, and crowd crush/stuck telemetry.

---

## Key Features

* **Physical Force & Side Pressure Modeling:** Extends agent interaction to evaluate physical constraints, bottleneck densities, and force thresholds beyond standard 1D lane progression.
* **Ghost Agent Mechanics:** Manages overlapping and non-colliding agent states (`GhostAgent`, `GhostAgentManager`) to realistically resolve complex bottleneck jams and casualty dynamics.
* **Automated Source Patcher (`patchCrowd.py`):** Automatically injects Java bytecode hooks into CrowdWalk (`AgentBase`, `WalkAgent`, `AgentHandler`, `BasicSimulationLauncher`), adds `DynamicAgentLogger.java`, and recompiles the core engine.
* **Comprehensive Telemetry & Logging:** Logs granular agent data (speed, force, link ID, nodes, timestamps, and crushed states) via `TelemetryHandler.rb` and `DynamicAgentLogger`.
* **GPS Data Integration (`gpxToCsv.py`):** Converts real-world GPX trace logs into CrowdWalk-compatible CSV coordinate datasets for empirical trajectory validation.
* **Extensive Benchmark Suite (`CrushTesting/`):** Pre-configured test scenarios for one-way corridors, turns, narrow entrances, wide exits, busy paths, 2-way crossroads, 4-way crossroads, and random motion.

---

## Repository Structure

CrushWalk/
├── Crush2/              # Intermediate Ruby-agent prototype and scenarios
├── Crush3/              # Advanced blackboard architecture & physics tests
├── CrushTest/           # Standard integration test setup and plotting utilities
├── CrushTesting/        # Comprehensive benchmark maps & XML topologies:
│   ├── 1OneWay.xml      # Single-direction bottleneck corridor
│   ├── 2Turn.xml        # Sharp turn bottleneck
│   ├── 3SmallEnter.xml  # Funnel/narrow entrance topology
│   ├── 4BigExit.xml     # Constrained entry with wide dissipation exit
│   ├── 5BusyPath.xml    # Bidirectional high-density pathway
│   ├── 6Crossroad2way.xml # 2-way intersecting corridor
│   └── 7Crossroad4way.xml # 4-way intersection gridlock test
├── Test/                # Real-world test maps (e.g., HigashiKU) & generation configs
├── patchCrowd.py        # Automated Java hook injector and Gradle builder
├── run.py               # Batch execution runner
└── gpxToCsv.py          # GPX trace parser to CrowdWalk CSV format

---

## Getting Started

### Prerequisites

* **Java JDK:** OpenJDK / Oracle JDK 17+ (or 21+)
* **Python:** 3.8+
* **JRuby / Ruby:** Embedded with CrowdWalk
* **CrowdWalk:** Cloned and accessible locally

### Installation & Patching

1. Place the `CrushWalk` files in your workspace or inside your CrowdWalk repository.
2. Run the patcher script to inject custom hooks into CrowdWalk's Java source code and rebuild the project:

python patchCrowd.py

This automatically locates CrowdWalk files, generates `DynamicAgentLogger.java`, modifies agent collision rules, adjusts the simulation exit condition, fixes launcher scripts, and executes `./gradlew build -x test`.

---

## Dynamic Telemetry & Custom Logging Pipeline

CrushWalk uses a runtime-decoupled logging architecture. The compiled Java engine acts as an agnostic data pipeline, meaning **you can track new physical properties or custom metrics without recompiling Java or rebuilding the project with Gradle.**

### How to Log Your Own Variables

To record custom agent variables (such as custom density models, stamina, stress, or lane deviations), follow this 4-step workflow:

1. **Calculate the Metric in Your Agent Script (`PhysicalAgent.rb`):**
Define or calculate your custom mathematical variable during the agent's per-tick update routine based on the current simulation state.
2. **Send the Value to the Telemetry Bridge (`TelemetryHandler.rb`):**
Pass your calculated metric dictionary to the telemetry updater during each step.
3. **Map the Variable to an Output Key (`TelemetryHandler.rb`):**
Inside the telemetry handler, assign your variable to the agent's configuration object under a specific key name (e.g., `"my_custom_metric"`).
4. **Declare the Key in Your Scenario Properties (`properties.json`):**
Add the exact string name of your new key to the `fields` array inside the `dynamic_logging` block in your simulation properties file.

When CrowdWalk starts, the logger automatically creates a matching column header in `dynamic_metrics.csv`, queries each agent for that key on every tick, and records the live data to disk.

---

## Running Simulations

### Running a Single Experiment

Execute any benchmark configuration using CrowdWalk's launcher:

sh quickstart.sh ./CrushTesting/prop.json -g2

### Running Batch Automation

Use the batch controller script to run multiple parameter sweeps:

python run.py


### Processing GPX Field Data

Convert empirical GPS tracker traces to model coordinates:

python gpxToCsv.py input_track.gpx output_coordinates.csv


---

## Telemetry Output

The addon logs agent metrics to CSV for analysis:

* `log_crushed_agents.csv`: Timestamps, agent IDs, links, and node coordinates recorded when physical crush thresholds are breached.
* `dynamic_metrics.csv`: Per-tick continuous time-series recording agent coordinates, speeds, physical force/compression values, and any custom fields declared in `properties.json`.

---

# Notes

* **Work in Progress:** This repository is actively under development as of September 2026.
* **AI Assistance:** Portions of the code and documentation in this repository were developed with AI assistance.