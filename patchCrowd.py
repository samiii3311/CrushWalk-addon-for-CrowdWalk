#!/usr/bin/env python3
"""
patch_crowdwalk.py
Idempotent, robust CrowdWalk patcher and compiler.
Safely injects custom research hooks, dynamic logger, and ghost mechanics.
Safe to run multiple times without duplicating code.
"""

from pathlib import Path
import re
import subprocess
import sys


def find_repo_root() -> Path:
    """Detects CrowdWalk root directory containing build.gradle."""
    cwd = Path.cwd().resolve()
    candidates = [cwd, cwd.parent, cwd / "crowdwalk", cwd / ".." / "crowdwalk"]
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


def apply_agent_base():
    p = find_file("AgentBase.java")
    if not p:
        return
    txt = p.read_text(encoding="utf-8").replace("\r\n", "\n")

    marker = "protected boolean allowOverlap = false;"
    if marker in txt:
        print("[*] Already patched: AgentBase.java")
        return

    # Clean previous/malformed attempts
    txt = re.sub(r"\s*// === Custom: Ghost & Overlap ===[\s\S]*", "", txt)
    txt = re.sub(r"\n// ;;; Local Variables:[\s\S]*", "", txt)
    txt = re.sub(r"\s*\}\s*$", "", txt)

    new_tail = """
    // === Custom: Ghost & Overlap ===
    protected boolean allowOverlap = false;
    public void setAllowOverlap(boolean flag) { this.allowOverlap = flag; }
    public boolean isAllowOverlap() { return allowOverlap; }

    protected boolean ghostMode = false;
    public boolean isGhost() { return ghostMode; }
    public void setGhost(boolean flag) { ghostMode = flag; }
}
// ;;; Local Variables:
// ;;; mode: java
// ;;; End:
"""
    p.write_text(txt + new_tail, encoding="utf-8")
    print("[+] Successfully patched: AgentBase.java")


def apply_walk_agent():
    patch_file_exact(
        "WalkAgent.java",
        search="protected double calcSocialForce(double dist) {",
        replace="protected double calcSocialForce(double dist) {\n        if (this.isGhost()) return 0.0;",
        marker="if (this.isGhost()) return 0.0;",
    )
    patch_file_exact(
        "WalkAgent.java",
        search="protected double calcSocialForceToHeading(double dx, double dy) {",
        replace="protected double calcSocialForceToHeading(double dx, double dy) {\n        if (this.isGhost()) return 0.0;",
        marker="protected double calcSocialForceToHeading(double dx, double dy) {\n        if (this.isGhost()) return 0.0;",
    )
    patch_file_exact(
        "WalkAgent.java",
        search="private double accumulateSocialForces(SimTime currentTime, double lowerBound) {",
        replace="private double accumulateSocialForces(SimTime currentTime, double lowerBound) {\n        if (this.isGhost()) return 0.0;",
        marker="private double accumulateSocialForces(SimTime currentTime, double lowerBound) {\n        if (this.isGhost()) return 0.0;",
    )
    patch_file_exact(
        "WalkAgent.java",
        search="AgentBase agent = otherLane.get(otherLane.size() - i - 1);",
        replace="AgentBase agent = otherLane.get(otherLane.size() - i - 1);\n                if (agent.isGhost()) continue;",
        marker="if (agent.isGhost()) continue;",
    )
    patch_file_exact(
        "WalkAgent.java",
        search="for(AgentBase agent : sameLane) {",
        replace="for(AgentBase agent : sameLane) {\n                if (agent.isGhost()) continue;",
        marker="for(AgentBase agent : sameLane) {\n                if (agent.isGhost()) continue;",
    )

    orig_pred = """            if(agents.size() > 0 && predecessorIndex < agents.size()) {
                // 現在のworkingPlace に前の人がいる場合
                // indexが負の場合は、最後尾の人が直前の人
                if(predecessorIndex < 0) predecessorIndex = 0 ;
                distToPredecessor +=
                    agents.get(predecessorIndex).currentPlace.getAdvancingDistance() ;
                break ;
            }"""

    custom_pred = """            if (agents.size() > 0 && predecessorIndex < agents.size()) {
                AgentBase predecessor = null;
                for (int i = predecessorIndex; i < agents.size(); i++) {
                    AgentBase a = agents.get(i);
                    if (!a.isGhost()) {
                        predecessor = a;
                        break;
                    }
                }
                if (predecessor != null) {
                    distToPredecessor += predecessor.currentPlace.getAdvancingDistance();
                    break;
                }
            }"""
    patch_file_exact(
        "WalkAgent.java",
        search=orig_pred,
        replace=custom_pred,
        marker="AgentBase predecessor = null;",
    )


def apply_agent_handler():
    p = find_file("AgentHandler.java")
    if not p:
        return
    txt = p.read_text(encoding="utf-8").replace("\r\n", "\n")

    # In-line hook patches with unique markers
    patch_file_exact(
        "AgentHandler.java",
        search="private EvacuationSimulator simulator;",
        replace="private EvacuationSimulator simulator;\n    private DynamicAgentLogger dynamicLogger = new DynamicAgentLogger(this);",
        marker="private DynamicAgentLogger dynamicLogger",
    )

    patch_file_exact(
        "AgentHandler.java",
        search="agent.update(currentTime);",
        replace="""agent.update(currentTime);
                dynamicLogger.log(agent, currentTime);
                if (agent.hasTag("crushed") && !agent.hasTag("crushed_logged")) {
                    logCrushedAgent(agent, currentTime);
                    agent.addTag("crushed_logged");
                }""",
        marker="logCrushedAgent(agent, currentTime);",
    )

    patch_file_exact(
        "AgentHandler.java",
        search="setupEvacuatedAgentsLogger() ;",
        replace="setupEvacuatedAgentsLogger() ;\n        setupCrushedAgentsLogger();",
        marker="setupCrushedAgentsLogger();",
    )

    patch_file_exact(
        "AgentHandler.java",
        search="initEvacuatedAgentsLogger() ;",
        replace="""initEvacuatedAgentsLogger() ;
        initCrushedAgentsLogger();
        dynamicLogger.init(simulator.getProperties().getTerm("dynamic_logging"));""",
        marker="initCrushedAgentsLogger();",
    )

    patch_file_exact(
        "AgentHandler.java",
        search="closeEvacuatedAgentsLogger();",
        replace="closeEvacuatedAgentsLogger();\n        closeCrushedAgentsLogger();\n        dynamicLogger.close();",
        marker="closeCrushedAgentsLogger();",
    )

    # Class-level methods insertion
    marker_methods = "public int numOfCrushed()"
    txt = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    if marker_methods in txt:
        print("[*] Already patched: AgentHandler.java (crush methods)")
        return

    txt = re.sub(r"\s*// === Custom: Crushed & Log Helpers ===[\s\S]*", "", txt)
    txt = re.sub(r"\n// ;;; Local Variables:[\s\S]*", "", txt)
    txt = re.sub(r"\s*\}\s*$", "", txt)

    crushed_methods = """
    // === Custom: Crushed & Log Helpers ===
    public int numOfCrushed() {
        int crushed = 0;
        for (AgentBase agent : getAllAgentCollection()) {
            if (agent != null && agent.hasTag("crushed")) crushed++;
        }
        return crushed;
    }

    public Logger crushedAgentsLogger = null;
    public static CsvFormatter<AgentBase> crushedAgentsLoggerFormatter = new CsvFormatter<AgentBase>();
    static {
        CsvFormatter<AgentBase> formatter = crushedAgentsLoggerFormatter;
        formatter
            .addColumn(formatter.new Column("generated_time") {
                public String value(AgentBase agent, Object timeObj, Object agentHandlerObj) {
                    return "" + (int)agent.generatedTime.getRelativeTime();
                }})
            .addColumn(formatter.new Column("current_traveling_period") {
                public String value(AgentBase agent, Object timeObj, Object agentHandlerObj) {
                    return "" + (int)((SimTime)timeObj).getRelativeTime();
                }})
            .addColumn(formatter.new Column("pedestrianID") {
                public String value(AgentBase agent, Object timeObj, Object agentHandlerObj) {
                    return agent.getID();
                }})
            .addColumn(formatter.new Column("event_type") {
                public String value(AgentBase agent, Object timeObj, Object agentHandlerObj) {
                    return "CRUSHED";
                }})
            .addColumn(formatter.new Column("current_linkID") {
                public String value(AgentBase agent, Object timeObj, Object agentHandlerObj) {
                    return (agent.getCurrentLink() != null) ? agent.getCurrentLink().getID() : "";
                }})
            .addColumn(formatter.new Column("forward_node") {
                public String value(AgentBase agent, Object timeObj, Object agentHandlerObj) {
                    return (agent.getNextNode() != null) ? agent.getNextNode().getID() : "";
                }})
            .addColumn(formatter.new Column("backward_node") {
                public String value(AgentBase agent, Object timeObj, Object agentHandlerObj) {
                    return (agent.getPrevNode() != null) ? agent.getPrevNode().getID() : "";
                }});
    }

    public void logCrushedAgent(AgentBase agent, SimTime currentTime) {
        if (crushedAgentsLogger != null && agent != null) {
            crushedAgentsLoggerFormatter.outputValueToLoggerInfo(crushedAgentsLogger, agent, currentTime, this);
        }
    }
    private void closeCrushedAgentsLogger() { closeLogger(crushedAgentsLogger); }
    private void setupCrushedAgentsLogger() {}
    private void initCrushedAgentsLogger() {
        try {
            String crushedLogDir = simulator.getProperties().getDirectoryPath("crushed_agents_log_dir", null);
            if (crushedLogDir != null) {
                crushedLogDir = crushedLogDir.replaceFirst("[/\\\\\\\\]+$", "");
                openCrushedAgentsLogger("crushed_agents_log", crushedLogDir);
            }
        } catch(Exception e) {
            Itk.logError("can not setup Crushed Logger", e.getMessage());
            Itk.quitWithStackTrace(e);
        }
    }
    private void openCrushedAgentsLogger(String name, String dirPath) {
        crushedAgentsLogger = openLogger(name, Level.INFO, dirPath + "/log_crushed_agents.csv");
        crushedAgentsLoggerFormatter.outputHeaderToLoggerInfo(crushedAgentsLogger);
    }
}
// ;;; Local Variables:
// ;;; mode: java
// ;;; End:
"""
    p.write_text(txt + crushed_methods, encoding="utf-8")
    print("[+] Successfully patched: AgentHandler.java (crush methods)")


def apply_simulator_and_launcher():
    # EvacuationSimulator.java
    patch_file_exact(
        "EvacuationSimulator.java",
        search='agentHandler.numOfEvacuatedAgents(), agentHandler.getMaxAgentCount());',
        replace='agentHandler.numOfEvacuatedAgents(), agentHandler.getMaxAgentCount(), agentHandler.numOfCrushed(), agentHandler.getMaxAgentCount());',
        marker="agentHandler.numOfCrushed()",
    )
    patch_file_exact(
        "EvacuationSimulator.java",
        search='"Walking: %d  Generated: %d  Evacuated: %d / %d"',
        replace='"Walking: %d  Generated: %d  Evacuated: %d / %d Crushed: %d / %d"',
        marker="Crushed: %d / %d",
    )
    patch_file_exact(
        "EvacuationSimulator.java",
        search='"Walking: %d  Generated: %d  Evacuated(Stuck): %d(%d) / %d"',
        replace='"Walking: %d  Generated: %d  Evacuated(Stuck): %d(%d) / %d Crushed: %d / %d"',
        marker="Evacuated(Stuck): %d(%d) / %d Crushed: %d / %d",
    )

    # BasicSimulationLauncher.java
    launcher_check = """        finished = simulator.updateEveryTick();
        nodagumi.ananPJ.Simulator.AgentHandler agentHandler = simulator.getAgentHandler();
        boolean aliveFound = false;

        for (AgentBase agent : simulator.getAgentHandler().getAllAgentCollection()){
            if (agent == null) continue;
            if (!agent.hasTag("crushed") && !agent.isEvacuated()){
                aliveFound = true;
                break;
            }
        }

        if (!aliveFound && (agentHandler.numOfCrushed() + agentHandler.numOfEvacuatedAgents() >= agentHandler.getMaxAgentCount())){
            finished = true;
        }"""
    patch_file_exact(
        "BasicSimulationLauncher.java",
        search="finished = simulator.updateEveryTick();",
        replace=launcher_check,
        marker="agentHandler.numOfCrushed() + agentHandler.numOfEvacuatedAgents()",
    )

    # quickstart.sh (Duplicate command fix)
    double_launch = """echo "$JAVA $JAVAOPT -Djdk.gtk.version=2 -jar $JAR $*"
$JAVA $JAVAOPT -Djdk.gtk.version=2 -jar $JAR $*

echo "$JAVA $JAVAOPT -Djdk.gtk.version=2 -jar $JAR $*"
$JAVA $JAVAOPT -Djdk.gtk.version=2 -jar $JAR $*"""

    single_launch = """echo "$JAVA $JAVAOPT -Djdk.gtk.version=2 -jar $JAR $*"
$JAVA $JAVAOPT -Djdk.gtk.version=2 -jar $JAR $*"""

    patch_file_exact(
        "quickstart.sh",
        search=double_launch,
        replace=single_launch,
        marker=single_launch,
    )


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


def main():
    print(f">>> Target repository root: {REPO_DIR}")

    # 1. Ensure DynamicAgentLogger exists
    handler_path = find_file("AgentHandler.java")
    if handler_path:
        target_logger = handler_path.parent / "DynamicAgentLogger.java"
        target_logger.write_text(DYNAMIC_AGENT_LOGGER_SRC.strip() + "\n", encoding="utf-8")
        print(f"[+] Created/Updated: {target_logger.relative_to(REPO_DIR)}")

    # 2. Apply all patches with strict guards
    apply_agent_base()
    apply_walk_agent()
    apply_agent_handler()
    apply_simulator_and_launcher()

    # 3. Build JAR
    build_with_gradle(skip_tests=True)


if __name__ == "__main__":
    main()