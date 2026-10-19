#!/usr/bin/env python3
"""
patchLogger.py -- adds the CrushWalk loggers to a vanilla CrowdWalk checkout (Java side only), then builds.

    python3 CrushTools/logger/patchLogger.py              # patch + build (from crowdwalk/)
    python3 CrushTools/logger/patchLogger.py --no-build   # patch only

What it adds:
  DynamicAgentLogger   one row per agent per tick   (properties.json: "dynamic_logging")
  LinkAggregateLogger  one row per link per tick    (properties.json: "link_logging"):
                       agent_count, mean/std speed, mean_<field>/max_<field> for each listed field,
                       crush_now (an agent on the link has the "crushed" tag), queue_count (speed < 0.02)
Both write only the fields listed in "fields", read from each agent's config. Those values are set from
Ruby: call TelemetryHandler.update_telemetry(agent, data, time) every tick (TelemetryHandler.rb next
to this file; README.md here explains it in Japanese); an agent whose config has no "last_telemetry_tick" for this tick is skipped by the agent logger.
The link logger always reads "link_id" and "current_speed" (TelemetryHandler sets both).

    "dynamic_logging": {"file": "/path/agents.csv", "fields": ["link_id", "position", "current_speed"]},
    "link_logging":    {"file": "/path/linkMetrics.csv", "fields": ["speed_fwd", "speed_bwd"]}

Safe to run twice: each change is skipped when its marker is already in the file.
CrushTools/crush/patchCrowd.py (the full CrushWalk patcher) runs this first, then adds the crush physics.
"""

from pathlib import Path
import subprocess
import sys


def find_repo_root() -> Path:
    """Detects CrowdWalk root directory containing build.gradle."""
    cwd = Path.cwd().resolve()
    candidates = [cwd, cwd.parent, cwd / "crowdwalk", cwd / ".." / "crowdwalk"]
    candidates += list(Path(__file__).resolve().parents)  # also works when run from inside CrushTools/
    for p in candidates:
        if (p / "build.gradle").exists():
            return p.resolve()
    print("[!] Error: Could not locate build.gradle in current or parent directories.")
    sys.exit(1)


REPO_DIR = find_repo_root()


def find_file(filename: str) -> Path:
    """Recursively finds a target file inside the repository."""
    matches = list(REPO_DIR.rglob(filename))
    return matches[0] if matches else None


def patch_file_exact(filename: str, search: str, replace: str, marker: str) -> bool:
    """Safely replaces a specific block only if the marker does not already exist."""
    target_path = find_file(filename)
    if not target_path:
        print(f"[!] Error: File '{filename}' not found.")
        return False

    content = target_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    search_norm = search.replace("\r\n", "\n")
    replace_norm = replace.replace("\r\n", "\n")

    # 1. Idempotency check: Skip if already present
    if marker in content:
        print(f"[*] Already patched: {filename} -> ({marker[:30]}...)")
        return True

    # 2. Check if anchor is present
    if search_norm not in content:
        print(f"[!] Warning: Anchor missing in {filename}:\n    '{search_norm[:45]}...'")
        return False

    target_path.write_text(content.replace(search_norm, replace_norm, 1), encoding="utf-8")
    print(f"[+] Successfully patched: {filename}")
    return True


DYNAMIC_AGENT_LOGGER_SRC = r'''package nodagumi.ananPJ.Simulator;

import java.util.*;
import java.io.PrintWriter;
import java.io.FileOutputStream;
import java.io.File;

import nodagumi.ananPJ.Agents.AgentBase;
import nodagumi.ananPJ.misc.SimTime;
import nodagumi.Itk.Itk;
import nodagumi.Itk.Term;

public class DynamicAgentLogger {
    private PrintWriter writer = null;
    private List<String> fields = new ArrayList<>();

    public DynamicAgentLogger(AgentHandler handler) {}

    public void init(Term config) {
        if (config != null) {
            Term fieldListTerm = config.getArgTerm("fields");
            if (fieldListTerm != null && fieldListTerm.isArray()) {
                for (int i = 0; i < fieldListTerm.getArraySize(); i++) {
                    fields.add(fieldListTerm.getNthTerm(i).getString());
                }
            }

            String filename = config.getArgString("file");
            if (filename == null) filename = "dynamic_metrics.csv";

            try {
                File file = new File(filename);
                File dir = file.getParentFile();
                if (dir != null && !dir.exists()) dir.mkdirs();

                this.writer = new PrintWriter(new FileOutputStream(file), true);
                
                StringBuilder header = new StringBuilder("current_traveling_period,generated_time,agent_id");
                for (String field : fields) {
                    header.append(",").append(field);
                }
                writer.println(header.toString());
                Itk.logInfo("Dynamic Logger initialized", filename);
            } catch (Exception e) {
                Itk.logError("Dynamic Logger Init Error", e.getMessage());
            }
        }
    }

    public void log(AgentBase agent, SimTime time) {
        if (writer != null && agent != null) {

            // --- Skip agents whose config was never written, or wasn't
            //     refreshed this tick (stale / recycled object guard) ---
            if (agent.config == null) {
                return;
            }
            Object lastTickObj = agent.config.getArg("last_telemetry_tick");
            long currentTick = (long) time.getRelativeTime();
            if (lastTickObj == null) {
                return;
            }
            try {
                if (Long.parseLong(lastTickObj.toString()) != currentTick) {
                    return;
                }
            } catch (NumberFormatException e) {
                return;
            }
            // -----------------------------------------------------------

            StringBuilder row = new StringBuilder();
            row.append((int)time.getRelativeTime()).append(",");
            row.append((int)agent.generatedTime.getRelativeTime()).append(",");
            row.append(agent.ID).append(",");
            
            for (int i = 0; i < fields.size(); i++) {
                String key = fields.get(i);
                Object val = null;

                if (agent.hasTag(key)) {
                    val = "1";
                } else if (agent.config != null) {
                    val = agent.config.getArg(key);
                }
                
                row.append(val != null ? val.toString().replaceAll(",", ";") : "0");
                if (i < fields.size() - 1) row.append(",");
            }
            writer.println(row.toString());
        }
    }

    public void close() {
        if (writer != null) {
            writer.flush();
            writer.close();
            writer = null;
        }
        Itk.logInfo("Logger","Logger Finished");
    }
}
'''

LINK_AGGREGATE_LOGGER_SRC = r'''package nodagumi.ananPJ.Simulator;

import java.util.*;
import java.io.PrintWriter;
import java.io.FileOutputStream;
import java.io.File;

import nodagumi.ananPJ.Agents.AgentBase;
import nodagumi.ananPJ.misc.SimTime;
import nodagumi.Itk.Itk;
import nodagumi.Itk.Term;

public class LinkAggregateLogger {
    private PrintWriter writer = null;
    private static final double STATIONARY_EPS = 0.02;
    // Which agent.config fields to aggregate, driven by link_logging.fields
    // in properties.json -- add/rename/remove a field there (and set it from
    // Ruby via config.setArg) with no Java rebuild needed.
    private List<String> fields = new ArrayList<>();

    private static class LinkAccumulator {
        int count = 0;
        double speedSum = 0.0, speedSumSq = 0.0;
        int crushNow = 0;
        int queueCount = 0;
        Map<String, Double> fieldSum = new HashMap<>();
        Map<String, Double> fieldMax = new HashMap<>();
    }

    private Map<String, LinkAccumulator> tickAccum = new HashMap<>();
    private int currentPeriod = Integer.MIN_VALUE;

    public LinkAggregateLogger(AgentHandler handler) {}

    public void init(Term config) {
        if (config == null) return;

        Term fieldListTerm = config.getArgTerm("fields");
        if (fieldListTerm != null && fieldListTerm.isArray()) {
            for (int i = 0; i < fieldListTerm.getArraySize(); i++) {
                fields.add(fieldListTerm.getNthTerm(i).getString());
            }
        }

        String filename = config.getArgString("file");
        if (filename == null) filename = "link_metrics.csv";

        try {
            File file = new File(filename);
            File dir = file.getParentFile();
            if (dir != null && !dir.exists()) dir.mkdirs();

            this.writer = new PrintWriter(new FileOutputStream(file), true);
            StringBuilder header = new StringBuilder(
                "current_traveling_period,link_id,agent_count,mean_speed,std_speed");
            for (String field : fields) {
                header.append(",mean_").append(field).append(",max_").append(field);
            }
            header.append(",crush_now,queue_count");
            writer.println(header.toString());
            Itk.logInfo("Link Aggregate Logger initialized", filename);
        } catch (Exception e) {
            Itk.logError("Link Aggregate Logger Init Error", e.getMessage());
        }
    }

    // Called once PER AGENT, at the SAME call site as DynamicAgentLogger.log().
    // Detects tick boundaries itself (by watching current_traveling_period change),
    // so no separate per-tick flush call is needed anywhere else in AgentHandler.
    public void accumulate(AgentBase agent, SimTime time) {
        if (writer == null || agent == null || agent.config == null) return;

        int period = (int) time.getRelativeTime();
        if (currentPeriod != Integer.MIN_VALUE && period != currentPeriod) {
            flushPeriod(currentPeriod);
        }
        currentPeriod = period;

        Object linkIdObj = agent.config.getArg("link_id");
        String linkId = linkIdObj != null ? linkIdObj.toString() : null;
        if (linkId == null || linkId.isEmpty()) return;

        double speed = parseDouble(agent.config.getArg("current_speed"));
        boolean crushed = agent.hasTag("crushed");

        LinkAccumulator acc = tickAccum.computeIfAbsent(linkId, k -> new LinkAccumulator());
        acc.count++;
        acc.speedSum += speed;
        acc.speedSumSq += speed * speed;

        for (String field : fields) {
            double val = parseDouble(agent.config.getArg(field));
            acc.fieldSum.merge(field, val, Double::sum);
            acc.fieldMax.merge(field, Math.abs(val), Math::max);
        }

        if (crushed) acc.crushNow = 1;
        if (Math.abs(speed) < STATIONARY_EPS) acc.queueCount++;
    }

    private void flushPeriod(int period) {
        if (writer == null) return;
        for (Map.Entry<String, LinkAccumulator> entry : tickAccum.entrySet()) {
            String linkId = entry.getKey();
            LinkAccumulator acc = entry.getValue();
            if (acc.count == 0) continue;

            double meanSpeed = acc.speedSum / acc.count;
            double variance = (acc.speedSumSq / acc.count) - (meanSpeed * meanSpeed);
            double stdSpeed = variance > 0 ? Math.sqrt(variance) : 0.0;

            StringBuilder row = new StringBuilder();
            row.append(period).append(",").append(linkId).append(",").append(acc.count).append(",")
               .append(round(meanSpeed)).append(",").append(round(stdSpeed));

            for (String field : fields) {
                double mean = acc.fieldSum.getOrDefault(field, 0.0) / acc.count;
                double max = acc.fieldMax.getOrDefault(field, 0.0);
                row.append(",").append(round(mean)).append(",").append(round(max));
            }

            row.append(",").append(acc.crushNow).append(",").append(acc.queueCount);
            writer.println(row.toString());
        }
        tickAccum.clear();
    }

    private double parseDouble(Object val) {
        if (val == null) return 0.0;
        try { return Double.parseDouble(val.toString()); }
        catch (NumberFormatException e) { return 0.0; }
    }

    private double round(double v) {
        return Math.round(v * 1000.0) / 1000.0;
    }

    public void close() {
        if (currentPeriod != Integer.MIN_VALUE) {
            flushPeriod(currentPeriod);
        }
        if (writer != null) {
            writer.flush();
            writer.close();
            writer = null;
        }
        Itk.logInfo("Link Logger", "Logger Finished");
    }
}
'''


def build_with_gradle(skip_tests: bool = True):
    print("\n>>> Building CrowdWalk with Gradle...")
    is_windows = sys.platform == "win32"
    gradle_exec = REPO_DIR / ("gradlew.bat" if is_windows else "gradlew")

    if not gradle_exec.exists():
        print(f"[!] Error: Could not find Gradle wrapper at {gradle_exec.resolve()}")
        return False

    if not is_windows:
        gradle_exec.chmod(0o755)

    build_cmd = [str(gradle_exec.resolve()), "compileJava", "jar"]
    if skip_tests:
        build_cmd.extend(["-x", "test", "-x", "check"])

    result = subprocess.run(build_cmd, cwd=REPO_DIR, check=False)
    if result.returncode == 0:
        print("\n[✓] CrowdWalk patched and compiled successfully!")
        return True
    else:
        print(f"\n[!] Gradle build exited with status: {result.returncode}")
        return False


def apply_loggers():
    """Hooks in AgentHandler.java: create, feed (after every agent update), init and close both loggers."""
    patch_file_exact(
        "AgentHandler.java",
        search="private EvacuationSimulator simulator;",
        replace="private EvacuationSimulator simulator;\n    private DynamicAgentLogger dynamicLogger = new DynamicAgentLogger(this);",
        marker="private DynamicAgentLogger dynamicLogger",
    )
    patch_file_exact(
        "AgentHandler.java",
        search="private DynamicAgentLogger dynamicLogger = new DynamicAgentLogger(this);",
        replace="private DynamicAgentLogger dynamicLogger = new DynamicAgentLogger(this);\n"
                "    private LinkAggregateLogger linkLogger = new LinkAggregateLogger(this);",
        marker="private LinkAggregateLogger linkLogger",
    )
    patch_file_exact(
        "AgentHandler.java",
        search="agent.update(currentTime);",
        replace="agent.update(currentTime);\n                dynamicLogger.log(agent, currentTime);",
        marker="dynamicLogger.log(agent, currentTime);",
    )
    patch_file_exact(
        "AgentHandler.java",
        search="dynamicLogger.log(agent, currentTime);",
        replace="dynamicLogger.log(agent, currentTime);\n                linkLogger.accumulate(agent, currentTime);",
        marker="linkLogger.accumulate(agent, currentTime)",
    )
    patch_file_exact(
        "AgentHandler.java",
        search="initEvacuatedAgentsLogger() ;",
        replace='initEvacuatedAgentsLogger() ;\n        dynamicLogger.init(simulator.getProperties().getTerm("dynamic_logging"));',
        marker="dynamicLogger.init(",
    )
    patch_file_exact(
        "AgentHandler.java",
        search='dynamicLogger.init(simulator.getProperties().getTerm("dynamic_logging"));',
        replace='dynamicLogger.init(simulator.getProperties().getTerm("dynamic_logging"));\n'
                '        linkLogger.init(simulator.getProperties().getTerm("link_logging"));',
        marker="linkLogger.init(",
    )
    patch_file_exact(
        "AgentHandler.java",
        search="closeEvacuatedAgentsLogger();",
        replace="closeEvacuatedAgentsLogger();\n        dynamicLogger.close();",
        marker="dynamicLogger.close();",
    )
    patch_file_exact(
        "AgentHandler.java",
        search="dynamicLogger.close();",
        replace="dynamicLogger.close();\n        linkLogger.close();",
        marker="linkLogger.close();",
    )


def write_logger_classes():
    """Writes both logger classes next to AgentHandler.java (always overwritten from the templates above)."""
    handler = find_file("AgentHandler.java")
    if not handler:
        print("[!] Error: AgentHandler.java not found.")
        sys.exit(1)
    for name, text in (("DynamicAgentLogger.java", DYNAMIC_AGENT_LOGGER_SRC),
                       ("LinkAggregateLogger.java", LINK_AGGREGATE_LOGGER_SRC)):
        target = handler.parent / name
        target.write_text(text.strip() + "\n", encoding="utf-8")
        print(f"[+] Created/Updated: {target.relative_to(REPO_DIR)}")


def main():
    print(f">>> Target repository root: {REPO_DIR}")
    write_logger_classes()
    apply_loggers()
    if "--no-build" not in sys.argv:
        build_with_gradle(skip_tests=True)


if __name__ == "__main__":
    main()

