#!/usr/bin/env python3
"""
patchCrowd.py -- the full CrushWalk patcher: vanilla CrowdWalk -> CrushWalk, then builds.

    python3 CrushTools/crush/patchCrowd.py              # patch + build (from crowdwalk/)
    python3 CrushTools/crush/patchCrowd.py --no-build   # patch only

Steps:
  1. ../logger/patchLogger.py: the agent and link loggers (Java). That file alone is enough for logging only.
  2. Crush Java: ghost agents (AgentBase, WalkAgent), crushed-agent log and count (AgentHandler),
     crushed numbers in the status line (EvacuationSimulator, BasicSimulationLauncher).
  3. Crush Ruby fixes 9 and 10 in Crush3/*.rb (stale second push, squeeze = bigger push), if missing.
Safe to run twice: each change is skipped when its marker is already in the file.
"""

from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "logger"))  # CrushTools/logger/patchLogger.py
from patchLogger import (REPO_DIR, find_file, patch_file_exact, build_with_gradle,
                         write_logger_classes, apply_loggers)


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

    # In-line hook patches with unique markers (the logger hooks come from patchLogger.apply_loggers)
    patch_file_exact(
        "AgentHandler.java",
        search="linkLogger.accumulate(agent, currentTime);",
        replace="""linkLogger.accumulate(agent, currentTime);
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
        replace="initEvacuatedAgentsLogger() ;\n        initCrushedAgentsLogger();",
        marker="initCrushedAgentsLogger();",
    )

    patch_file_exact(
        "AgentHandler.java",
        search="closeEvacuatedAgentsLogger();",
        replace="closeEvacuatedAgentsLogger();\n        closeCrushedAgentsLogger();",
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
    # The Stuck-branch format string above gains a 6th %d (Crushed: %d / %d),
    # but its String.format call args end differently from the non-stuck
    # branch (numOfStuckAgents() instead of numOfEvacuatedAgents()), so the
    # patch at line 582 never matches it. Without this, getEvacuatedCountStatus()
    # throws MissingFormatArgumentException the first time an agent is stuck.
    patch_file_exact(
        "EvacuationSimulator.java",
        search='                    agentHandler.numOfStuckAgents(),\n'
               '                    agentHandler.getMaxAgentCount());',
        replace='                    agentHandler.numOfStuckAgents(),\n'
                '                    agentHandler.getMaxAgentCount(), agentHandler.numOfCrushed(), agentHandler.getMaxAgentCount());',
        marker='numOfStuckAgents(),\n                    agentHandler.getMaxAgentCount(), agentHandler.numOfCrushed()',
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


# CrushWalk Ruby agent fixes 9 and 10 (2026-10-19/20, Crush3/PHYSICS_NOTES.md), first kept in
# patchSingleLane.py. (file under crowdwalk/, text found once, replacement with marker SINGLELANE:<name>).
# The Crush3 .rb files in this repo already contain them; this updates an older copy.
RUBY_FIXES = {
    # A push from the front is kept for one tick (update order), so when someone stepped into the
    # gap ahead, the agent behind felt both the newcomer and the old pusher: 2 pushes, and the doubled
    # value travelled down the whole queue (1,080 -> 3,000 -> 6,600 N on a 1.7 m arm). Now a pusher
    # counts only for the agent it pushed last.
    "onevictim": [
        ("Crush3/PhysicsBlackboard.rb",
         "    @hits = {}\n",
         "    @hits = {}\n"
         "    @victim_of = {}  # SINGLELANE:onevictim -- aggressor_id => the agent it pushed last\n"),
        ("Crush3/PhysicsBlackboard.rb",
         "    @hits[victim_id][aggressor_id] = { accel: accel, time: tick_time }\n",
         "    @hits[victim_id][aggressor_id] = { accel: accel, time: tick_time }\n"
         "    @victim_of[aggressor_id.to_s] = victim_id.to_s  # SINGLELANE:onevictim\n"),
        ("Crush3/PhysicsBlackboard.rb",
         "    hit_data = @hits.dig(victim_id, aggressor_id)\n",
         "    return false unless @victim_of[aggressor_id.to_s] == victim_id.to_s  # SINGLELANE:onevictim\n"
         "    hit_data = @hits.dig(victim_id, aggressor_id)\n"),
    ],
    # Squeeze = the bigger of the push from behind and the push from the front, not their sum (user,
    # 2026-10-19: a body between two hands feels the force passing through it once). The push from
    # the front is weakened by the room the agent has to step back into: full when touching someone
    # behind (or the link start, which the step-back code also treats as a wall), half with a full
    # step of room or more (a free body pushed on one side is squeezed from F at the front to 0 at
    # the back, ~F/2 on average). Body resistance is still subtracted after.
    # The push from behind is weakened the same way by the room ahead: the smaller of the gap to the
    # next agent ahead and how far the agent walked last tick, so an agent held still (by a crowd, a
    # full link or the crossing rule at a node) gets the full push (user: nodes are open junctions).
    "squeeze": [
        ("Crush3/CrushAgent.rb",
         '    @pressure_transfer = props.getDouble("pressureTransfer", 1.0)\n',
         '    @pressure_transfer = props.getDouble("pressureTransfer", 1.0)\n'
         '    # SINGLELANE:squeeze -- room (m) at which a push is down to half: the distance a person moves back\n'
         '    # to regain balance after a push, 37.8 +- 12.1 cm (Muthukumar et al. 2025, Physiotherapy Quarterly\n'
         '    # 33(2), Table 1, push-and-release test, 60 young adults). A crowd push is stronger, so maybe longer.\n'
         '    @back_step = props.getDouble("backStepLength", 0.38)\n'),
        ("Crush3/CrushAgent.rb",
         "    startPos = virtualPlace.getAdvancingDistance()\n",
         "    startPos = virtualPlace.getAdvancingDistance()\n"
         "    @back_room = startPos  # SINGLELANE:squeeze -- free room behind, same rule as the step-back code\n"
         "    @front_gap = Float::INFINITY  # SINGLELANE:squeeze -- free room to the next agent ahead\n"),
        ("Crush3/CrushAgent.rb",
         "        dy = @widthUnit_SameLane * ((laneWidth - (count % laneWidth)) % laneWidth)\n",
         "        dy = @widthUnit_SameLane * ((laneWidth - (count % laneWidth)) % laneWidth)\n"
         "        @front_gap = [@front_gap, dx - @physicalSpace].min  # SINGLELANE:squeeze\n"),
        ("Crush3/CrushAgent.rb",
         "    _accel = @a0 * (baseSpeed - previousSpeed)\n",
         "    _accel = @a0 * (baseSpeed - previousSpeed)\n"
         "    @prev_speed = previousSpeed  # SINGLELANE:squeeze\n"),
        ("Crush3/CrushAgent.rb",
         "        if agentPos < startPos                         # Behind us\n",
         "        if agentPos < startPos                         # Behind us\n"
         "          if distanceSoFar == 0.0 && (!agent.isGhost() || agent.hasTag(\"crushed\"))  # SINGLELANE:squeeze\n"
         "            @back_room = [@back_room, startPos - agentPos - @physicalSpace].min\n"
         "          end\n"),
        ("Crush3/CrushAgent.rb",
         "    raw_crush_pressure = 0.0\n",
         "    raw_crush_pressure = 0.0\n"
         "    pressure_from_front = 0.0  # SINGLELANE:squeeze\n"),
        ("Crush3/CrushAgent.rb",
         "        raw_crush_pressure += incoming_force_mag\n",
         "        pressure_from_front += incoming_force_mag unless data[:behind]  # SINGLELANE:squeeze\n"),
        ("Crush3/CrushAgent.rb",
         "    @@dbg_max_raw_pressure = raw_crush_pressure if raw_crush_pressure > @@dbg_max_raw_pressure\n",
         "    room = [[(@back_room || 0.0), 0.0].max / @back_step, 1.0].min  # SINGLELANE:squeeze\n"
         "    walked = [(@prev_speed || 0.0) * currentTime.getTickUnit(), (@front_gap || 0.0)].min\n"
         "    room_ahead = [[walked, 0.0].max / @back_step, 1.0].min\n"
         "    raw_crush_pressure = [pressure_from_behind * (1.0 - 0.5 * room_ahead),\n"
         "                          pressure_from_front * (1.0 - 0.5 * room)].max\n"
         "    @@dbg_max_raw_pressure = raw_crush_pressure if raw_crush_pressure > @@dbg_max_raw_pressure\n"),
    ],
    # Fix 11 (2026-10-20): crush_agent! set speed 0 once, but calcSpeed kept running, so a crushed
    # agent walked off as a ghost and left by the exit. The body is meant to stay as a roadblock
    # (drag in calcPhysical) and count toward the end check (crushed + evacuated = all agents).
    "stillbody": [
        ("Crush3/CrushAgent.rb",
         "    _speed = @javaAgent.obstructer.calcAffectedSpeed(_speed)\n",
         "    _speed = @javaAgent.obstructer.calcAffectedSpeed(_speed)\n"
         "    _speed = 0.0 if @is_crushed  # SINGLELANE:stillbody -- a crushed body stays where it fell\n"),
    ],
    # Fix 12 (2026-10-20): search() listed a crushed body ahead like a standing person, so the
    # social force braked everyone behind it to a stop and one body blocked the whole queue (with
    # fix 11: 130-910 crushed per jam). User: people step over a body; it slows them, never stops
    # them. A body is no longer a neighbour ahead; an agent within physicalSpace of one walks at
    # most bodyStepFactor x its own free speed.
    "stepover": [
        ("Crush3/CrushAgent.rb",
         '    @body_drag_coefficient = props.getDouble("bodyDrag", 0.5 * @my_mass * 9.8)\n',
         '    @body_drag_coefficient = props.getDouble("bodyDrag", 0.5 * @my_mass * 9.8)\n'
         '    # SINGLELANE:stepover -- speed cap (share of own free speed) while stepping over a body.\n'
         '    # Placeholder, no source yet: test its effect before relying on it.\n'
         '    @body_step_factor = props.getDouble("bodyStepFactor", 0.5)\n'),
        ("Crush3/CrushAgent.rb",
         "    return [[], [], 0.0] if @javaAgent.isGhost()\n",
         "    @on_body = false  # SINGLELANE:stepover\n"
         "    return [[], [], 0.0] if @javaAgent.isGhost()\n"),
        ("Crush3/CrushAgent.rb",
         "        next if agentPos <= insensitivePos                 # Too close in counterflow\n",
         "        next if agentPos <= insensitivePos                 # Too close in counterflow\n"
         "        if agent.hasTag(\"crushed\")  # SINGLELANE:stepover -- a body is stepped over, not an obstacle\n"
         "          @on_body = true if distanceSoFar + (agentPos - startPos) <= @physicalSpace\n"
         "          next\n"
         "        end\n"),
        ("Crush3/CrushAgent.rb",
         "        next if agentPos == startPos && myTurnIsOver\n",
         "        next if agentPos == startPos && myTurnIsOver\n"
         "        if agent.hasTag(\"crushed\")  # SINGLELANE:stepover -- a body is stepped over, not an obstacle\n"
         "          @on_body = true if distanceSoFar + (agentPos - startPos) <= @physicalSpace\n"
         "          next\n"
         "        end\n"),
        ("Crush3/CrushAgent.rb",
         "    _speed = 0.0 if @is_crushed  # SINGLELANE:stillbody -- a crushed body stays where it fell\n",
         "    _speed = 0.0 if @is_crushed  # SINGLELANE:stillbody -- a crushed body stays where it fell\n"
         "    _speed = [_speed, @desired_empty_speed * @body_step_factor].min if @on_body && !@is_crushed  # SINGLELANE:stepover\n"),
    ],
}


def apply_ruby_fixes():
    """Adds fixes 9 to 11 to Crush3/*.rb. Skips an edit that is already there; stops if an anchor is missing."""
    for name, edits in RUBY_FIXES.items():
        for rel, find, repl in edits:
            path = REPO_DIR / rel
            if not path.exists():
                print(f"[!] Warning: {rel} not found, Ruby fix '{name}' skipped.")
                break
            text = path.read_text(encoding="utf-8")
            if repl in text:
                continue
            if text.count(find) != 1:
                sys.exit(f"[!] Error: {rel}: anchor for Ruby fix '{name}' found {text.count(find)} times "
                         "(expected 1). Nothing more changed.")
            path.write_text(text.replace(find, repl), encoding="utf-8", newline="")
            print(f"[+] Ruby fix '{name}': {rel}")


def main():
    print(f">>> Target repository root: {REPO_DIR}")

    # 1. Loggers (patchLogger.py)
    write_logger_classes()
    apply_loggers()

    # 2. Crush Java patches
    apply_agent_base()
    apply_walk_agent()
    apply_agent_handler()
    apply_simulator_and_launcher()

    # 3. Crush Ruby fixes
    apply_ruby_fixes()

    # 4. Build JAR
    if "--no-build" not in sys.argv:
        build_with_gradle(skip_tests=True)


if __name__ == "__main__":
    main()