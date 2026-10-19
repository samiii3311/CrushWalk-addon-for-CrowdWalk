#!/usr/bin/env python3
"""
repare.py
Direct method-anchored patcher that places fields and methods
safely inside class bodies without string-slicing errors.
"""

from pathlib import Path
import re
import subprocess
import sys


def find_repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd, cwd.parent, cwd / "crowdwalk"]:
        if (p / "build.gradle").exists():
            return p.resolve()
        if (p / "crowdwalk" / "build.gradle").exists():
            return (p / "crowdwalk").resolve()
    print("[!] Could not detect CrowdWalk root directory.")
    sys.exit(1)


REPO_DIR = find_repo_root()


def find_file(filename: str) -> Path:
    matches = list(REPO_DIR.rglob(filename))
    return matches[0] if matches else None


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


def repair_agent_base():
    p = find_file("AgentBase.java")
    if not p:
        return
    txt = p.read_text(encoding="utf-8").replace("\r\n", "\n")

    # 1. Clean out all previous attempts
    txt = re.sub(r"\s*// === Custom: Ghost & Overlap ===[\s\S]*", "", txt)
    txt = re.sub(r"\s*protected boolean allowOverlap = false;[\s\S]*", "", txt)
    txt = re.sub(r"\n// ;;; Local Variables:[\s\S]*", "", txt)
    txt = re.sub(r"\s*\}\s*$", "", txt)  # remove trailing class brace if present

    # 2. Inject cleanly inside the class before closing it
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
    print("[+] Repaired & Patched AgentBase.java")


def repair_agent_handler():
    p = find_file("AgentHandler.java")
    if not p:
        return
    txt = p.read_text(encoding="utf-8").replace("\r\n", "\n")

    # 1. Clean previous hooks and methods
    txt = re.sub(r"\s*// === Custom: Crushed & Log Helpers ===[\s\S]*", "", txt)
    txt = re.sub(r"\s*public int numOfCrushed\(\)[\s\S]*", "", txt)
    txt = re.sub(
        r"(\s*private DynamicAgentLogger dynamicLogger = new DynamicAgentLogger\(this\);)+",
        "",
        txt,
    )
    txt = re.sub(
        r"agent\.update\(currentTime\);[\s\S]*?agent\.addTag\(\"crushed_logged\"\);\s*}",
        "agent.update(currentTime);",
        txt,
    )
    txt = re.sub(r"\s*setupCrushedAgentsLogger\(\);", "", txt)
    txt = re.sub(
        r"\s*initCrushedAgentsLogger\(\);\s*dynamicLogger\.init\(simulator\.getProperties\(\)\.getTerm\(\"dynamic_logging\"\)\);",
        "",
        txt,
    )
    txt = re.sub(r"\s*closeCrushedAgentsLogger\(\);\s*dynamicLogger\.close\(\);", "", txt)
    txt = re.sub(r"\n// ;;; Local Variables:[\s\S]*", "", txt)
    txt = re.sub(r"\s*\}\s*$", "", txt)  # remove trailing class brace if present

    # 2. Add dynamicLogger field
    txt = txt.replace(
        "private EvacuationSimulator simulator;",
        "private EvacuationSimulator simulator;\n    private DynamicAgentLogger dynamicLogger = new DynamicAgentLogger(this);",
        1,
    )

    # 3. Add update logger hook
    update_hook = """agent.update(currentTime);
                dynamicLogger.log(agent, currentTime);
                if (agent.hasTag("crushed") && !agent.hasTag("crushed_logged")) {
                    logCrushedAgent(agent, currentTime);
                    agent.addTag("crushed_logged");
                }"""
    txt = txt.replace("agent.update(currentTime);", update_hook, 1)

    # 4. Add lifecycle hooks
    txt = txt.replace(
        "setupEvacuatedAgentsLogger() ;",
        "setupEvacuatedAgentsLogger() ;\n        setupCrushedAgentsLogger();",
        1,
    )
    txt = txt.replace(
        "initEvacuatedAgentsLogger() ;",
        """initEvacuatedAgentsLogger() ;
        initCrushedAgentsLogger();
        dynamicLogger.init(simulator.getProperties().getTerm("dynamic_logging"));""",
        1,
    )
    txt = txt.replace(
        "closeEvacuatedAgentsLogger();",
        "closeEvacuatedAgentsLogger();\n        closeCrushedAgentsLogger();\n        dynamicLogger.close();",
        1,
    )

    # 5. Append crushed handler methods inside class and close properly
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
    print("[+] Repaired & Patched AgentHandler.java")


def repair_launcher():
    p = find_file("BasicSimulationLauncher.java")
    if not p:
        return
    txt = p.read_text(encoding="utf-8").replace("\r\n", "\n")

    orig_step = """    protected void simulateOneStepBare() {
        finished = simulator.updateEveryTick();
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
        }
        int relativeTime = (int)simulator.clock.getRelativeTime() ;
        counter++ ;
        if ((counter % 100) == 0)
            Itk.logDebug("Cycle",
                         "at:", simulator.currentTime.getAbsoluteTimeString(),
                         "("+counter+")",
                         "time:", Itk.getCurrentTimeStrShort(),
                         "walking:",
                         simulator.getAgentHandler().numOfWalkingAgents()) ;
    }"""

    txt = re.sub(
        r"protected void simulateOneStepBare\(\)\s*\{[\s\S]*?simulator\.getAgentHandler\(\)\.numOfWalkingAgents\(\)\)\s*;\s*\}",
        orig_step,
        txt,
    )
    p.write_text(txt, encoding="utf-8")
    print("[+] Repaired & Patched BasicSimulationLauncher.java")


def repair_walk_agent():
    p = find_file("WalkAgent.java")
    if not p:
        return
    txt = p.read_text(encoding="utf-8").replace("\r\n", "\n")

    txt = re.sub(r"(\s*if\s*\(this\.isGhost\(\)\)\s*return\s*0\.0;)+", "", txt)
    txt = re.sub(r"(\s*if\s*\(agent\.isGhost\(\)\)\s*continue;)+", "", txt)

    txt = txt.replace(
        "protected double calcSocialForce(double dist) {",
        "protected double calcSocialForce(double dist) {\n        if (this.isGhost()) return 0.0;",
        1,
    )
    txt = txt.replace(
        "protected double calcSocialForceToHeading(double dx, double dy) {",
        "protected double calcSocialForceToHeading(double dx, double dy) {\n        if (this.isGhost()) return 0.0;",
        1,
    )
    txt = txt.replace(
        "private double accumulateSocialForces(SimTime currentTime, double lowerBound) {",
        "private double accumulateSocialForces(SimTime currentTime, double lowerBound) {\n        if (this.isGhost()) return 0.0;",
        1,
    )
    txt = txt.replace(
        "AgentBase agent = otherLane.get(otherLane.size() - i - 1);",
        "AgentBase agent = otherLane.get(otherLane.size() - i - 1);\n                if (agent.isGhost()) continue;",
        1,
    )
    txt = txt.replace(
        "for(AgentBase agent : sameLane) {",
        "for(AgentBase agent : sameLane) {\n                if (agent.isGhost()) continue;",
        1,
    )

    p.write_text(txt, encoding="utf-8")
    print("[+] Repaired & Patched WalkAgent.java")


def main():
    # 1. Write DynamicAgentLogger
    p_ah = find_file("AgentHandler.java")
    (p_ah.parent / "DynamicAgentLogger.java").write_text(
        DYNAMIC_AGENT_LOGGER_SRC.strip() + "\n", encoding="utf-8"
    )

    # 2. Repair each file
    repair_agent_base()
    repair_agent_handler()
    repair_launcher()
    repair_walk_agent()

    # 3. Build with Gradle Wrapper
    print("\n>>> Building CrowdWalk with Gradle Wrapper...")
    is_windows = sys.platform == "win32"
    gradle_exec = REPO_DIR / ("gradlew.bat" if is_windows else "gradlew")

    cmd = [
        str(gradle_exec.resolve()),
        "compileJava",
        "jar",
        "-x",
        "test",
        "-x",
        "check",
    ]
    res = subprocess.run(cmd, cwd=REPO_DIR, check=False)
    if res.returncode == 0:
        print("\n[✓] Build Succeeded! CrowdWalk is compiled and ready.")
    else:
        print(f"\n[!] Build failed with status: {res.returncode}")


if __name__ == "__main__":
    main()