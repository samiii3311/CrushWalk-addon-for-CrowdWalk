#!/usr/bin/env python3
"""
patchCrowd.py -- the full CrushWalk patcher: vanilla CrowdWalk -> CrushWalk, then builds.

    python3 CrushTools/crush/patchCrowd.py              # patch + build (from crowdwalk/)
    python3 CrushTools/crush/patchCrowd.py --no-build   # patch only

Steps:
  1. ../logger/patchLogger.py: the agent and link loggers (Java). That file alone is enough for logging only.
  2. Crush Java: ghost agents (AgentBase, WalkAgent), crushed-agent log and count (AgentHandler),
     crushed numbers in the status line (EvacuationSimulator, BasicSimulationLauncher).
  3. Crush Ruby fixes 9-27 in Crush3/*.rb (RUBY_FIXES), if missing.
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
         '    # SINGLELANE:bodybreak -- fix 17: within bodyAwareDistance behind a body, push and pass on only bodyPassShare\n'
         '    @body_aware = props.getDouble("bodyAwareDistance", 0.0)\n'
         '    @body_pass = props.getDouble("bodyPassShare", 1.0)\n'
         '    # SINGLELANE:stepover -- speed cap (share of own free speed) while stepping over a body.\n'
         '    # Placeholder, no source yet: test its effect before relying on it.\n'
         '    @body_step_factor = props.getDouble("bodyStepFactor", 0.5)\n'),
        ("Crush3/CrushAgent.rb",
         "    return [[], [], 0.0] if @javaAgent.isGhost()\n",
         "    @on_body = false  # SINGLELANE:stepover\n"
         "    @body_near = false  # SINGLELANE:bodybreak\n"
         "    return [[], [], 0.0] if @javaAgent.isGhost()\n"),
        ("Crush3/CrushAgent.rb",
         "        next if agentPos <= insensitivePos                 # Too close in counterflow\n",
         "        next if agentPos <= insensitivePos                 # Too close in counterflow\n"
         "        if agent.hasTag(\"crushed\")  # SINGLELANE:stepover -- a body is stepped over, not an obstacle\n"
         "          @on_body = true if distanceSoFar + (agentPos - startPos) <= @physicalSpace\n"
         "          @body_near = true if distanceSoFar + (agentPos - startPos) <= @body_aware  # SINGLELANE:bodybreak\n"
         "          next\n"
         "        end\n"),
        ("Crush3/CrushAgent.rb",
         "        next if agentPos == startPos && myTurnIsOver\n",
         "        next if agentPos == startPos && myTurnIsOver\n"
         "        if agent.hasTag(\"crushed\")  # SINGLELANE:stepover -- a body is stepped over, not an obstacle\n"
         "          @on_body = true if distanceSoFar + (agentPos - startPos) <= @physicalSpace\n"
         "          @body_near = true if distanceSoFar + (agentPos - startPos) <= @body_aware  # SINGLELANE:bodybreak\n"
         "          next\n"
         "        end\n"),
        ("Crush3/CrushAgent.rb",
         "    _speed = 0.0 if @is_crushed  # SINGLELANE:stillbody -- a crushed body stays where it fell\n",
         "    _speed = 0.0 if @is_crushed  # SINGLELANE:stillbody -- a crushed body stays where it fell\n"
         "    _speed = [_speed, @desired_empty_speed * @body_step_factor].min if @on_body && !@is_crushed  # SINGLELANE:stepover\n"),
    ],
    # Fix 13 (2026-10-20): every agent stuck in a jam pushed at full drive forever, so a whole queue
    # sat above the crush line and a crush killed 130-900 people (PHYSICS_NOTES limit 9). The push
    # (drive) that feeds the pressure chain is now scaled; the agent's own walking is unchanged.
    # B: people push hard only near the bottleneck (Usten et al. 2023, Collective Dynamics), with
    # the spatial-expectancy curve of Usten, Sieben, Chraibi, Seyfried 2026 (arXiv 2604.26858):
    # 1 at the link end, falling to pushFarShare at pushNearDistance and beyond (their w = 10 m,
    # floor 0.1, chosen by hand there too). A (user): the longer someone stands touching the person
    # ahead, the weaker they push (nobody pushes full force to hurt), down to pushResidual.
    # No measured values exist for any of these four numbers: test a range before relying on them.
    # Fix 15 (2026-10-21, marker SINGLELANE:recover, inside this patch because it edits the same
    # line): the waiting time used to reset to 0 on the first gap ahead. A crushed body is stepped
    # over, so it opens a gap: everyone behind got their full push back at once and the next person
    # was crushed (the cascade). Now it runs back down at pushRecoverRate x the rate it built up.
    "pushfade": [
        ("Crush3/CrushAgent.rb",
         '    @body_step_factor = props.getDouble("bodyStepFactor", 0.5)\n',
         '    @body_step_factor = props.getDouble("bodyStepFactor", 0.5)\n'
         '    # SINGLELANE:pushfade -- placeholders, see patchCrowd.py fix 13\n'
         '    @push_near = props.getDouble("pushNearDistance", 10.0)\n'
         '    @push_far = props.getDouble("pushFarShare", 0.1)\n'
         '    @push_fade_time = props.getDouble("pushFadeTime", 60.0)\n'
         '    @push_residual = props.getDouble("pushResidual", 0.2)\n'
         '    @push_recover = props.getDouble("pushRecoverRate", 1.0)  # SINGLELANE:recover -- fix 15, placeholder\n'),
        ("Crush3/CrushAgent.rb",
         "    PhysicsBlackboard.instance.log_drive(getAgentId(), @my_mass * [_accel, 0.0].max)\n",
         "    # SINGLELANE:pushfade -- B: near the end of the link (ponytail: link end stands in for the\n"
         "    # bottleneck; use the queue front if jams form mid-link). A: fades while touching the one ahead.\n"
         "    to_end = @javaAgent.currentPlace.getLinkLength() - @javaAgent.currentPlace.getAdvancingDistance()\n"
         "    near = to_end < @push_near ? Math::E * Math.exp(1.0 / ((to_end / @push_near)**2 - 1.0)) : 0.0\n"
         "    # SINGLELANE:recover -- the wait runs back down instead of resetting on the first gap (fix 15)\n"
         "    dt = currentTime.getTickUnit()\n"
         "    @wait_time = (@front_gap || Float::INFINITY) <= 0.0 ? (@wait_time || 0.0) + dt : [(@wait_time || 0.0) - @push_recover * dt, 0.0].max\n"
         "    fade = @push_residual + (1.0 - @push_residual) * Math.exp(-@wait_time / @push_fade_time)\n"
         "    push_share = (@push_far + (1.0 - @push_far) * near) * fade\n"
         "    push_share *= @body_pass if @body_near  # SINGLELANE:bodybreak\n"
         "    PhysicsBlackboard.instance.log_drive(getAgentId(), push_share * @my_mass * [_accel, 0.0].max)\n"),
    ],
    # Fix 14 (2026-10-20, user): nobody is crushed while they can still move forward. The crush
    # timer only runs while the agent is pinned: touching the person ahead, or stopped (speed at
    # most 0.02 m/s, the queue_count line in the link logger).
    "pinned": [
        ("Crush3/CrushAgent.rb",
         "    if final_crush_pressure > active_threshold\n",
         "    pinned = (@front_gap || Float::INFINITY) <= 0.0 || (@prev_speed || 0.0) <= 0.02  # SINGLELANE:pinned\n"
         "    if final_crush_pressure > active_threshold && pinned\n"),
    ],
    # Fix 17 (2026-10-21, user rule R2: not everyone behind a body dies). Someone lying on the ground
    # is not part of the standing chain that passes a push forward, but the chain skipped bodies and
    # carried the full pressure across them. Within bodyAwareDistance behind a body, an agent pushes
    # and passes on only bodyPassShare (the init and search parts sit in the stepover patch, the push
    # part in the pushfade patch, because they edit the same lines). Defaults 0 m / 1.0 = off.
    "bodybreak": [
        ("Crush3/CrushAgent.rb",
         "    PhysicsBlackboard.instance.log_pressure(getAgentId(), pressure_from_behind)\n",
         "    # SINGLELANE:bodybreak -- right behind a body, only bodyPassShare of the push from behind goes on\n"
         "    PhysicsBlackboard.instance.log_pressure(getAgentId(), pressure_from_behind * (@body_near ? @body_pass : 1.0))\n"),
    ],
    # Fix 16 (2026-10-21): everyone had the same limit (1112 N for exactly 240 s), so a whole
    # stretch of queue died in the same minute (test: 196 of 196 deaths in sim minute 9). Kroll et
    # al. 2017 give 4-6 min, so each agent draws its own limit from crushDuration..crushDurationMax
    # (seeded by randseed + agent id: same run, same draw on any machine). Unset max = no spread.
    "tolerance": [
        ("Crush3/CrushAgent.rb",
         '    @crush_duration = props.getDouble("crushDuration", 240.0)\n',
         '    @crush_duration = props.getDouble("crushDuration", 240.0)\n'
         '    # SINGLELANE:tolerance -- own limit up to crushDurationMax, drawn on first use (no agent ID yet here)\n'
         '    @crush_spread = props.getDouble("crushDurationMax", @crush_duration) - @crush_duration\n'
         '    @crush_seed = props.getDouble("randseed", 0.0)\n'),
        ("Crush3/CrushAgent.rb",
         "    if @time_over_threshold >= @crush_duration\n",
         "    if @crush_spread  # SINGLELANE:tolerance -- the ID is set by now (in initialize it was not: all agents drew the same)\n"
         '      @crush_duration += @crush_spread * Random.new(Digest::MD5.hexdigest("#{@crush_seed}-#{getAgentId()}").to_i(16)).rand\n'
         "      @crush_spread = nil\n"
         "    end\n"
         "    if @time_over_threshold >= @crush_duration\n"),
    ],
    # Walls (draft, 2026-10-21): an open crowd leaks a push sideways, a wall stops the leak. A link
    # tagged WALL1 (river/cliff on one side) or WALL2 (bridge, alley) passes on a bigger share of the
    # push: pressureTransfer with no wall, wallPressureTransfer with two, halfway with one. The tags
    # come from make_maps.py --wall-share. Unset wallPressureTransfer = no change. No source for it.
    "walls": [
        ("Crush3/CrushAgent.rb",
         '    @back_step = props.getDouble("backStepLength", 0.38)\n',
         '    @back_step = props.getDouble("backStepLength", 0.38)\n'
         '    @wall_transfer = props.getDouble("wallPressureTransfer", @pressure_transfer)  # SINGLELANE:walls\n'),
        ("Crush3/CrushAgent.rb",
         "  def calcPhysical(physicalAgent, currentTime)\n",
         "  # SINGLELANE:walls -- share of a push passed on here: walls on 0/1/2 sides of the current link\n"
         "  def link_transfer\n"
         "    link = @javaAgent.currentPlace.getLink()\n"
         '    sides = link.hasTag("WALL2") ? 2 : (link.hasTag("WALL1") ? 1 : 0)\n'
         "    @pressure_transfer + sides * 0.5 * (@wall_transfer - @pressure_transfer)\n"
         "  end\n"
         "\n"
         "  def calcPhysical(physicalAgent, currentTime)\n"),
        ("Crush3/CrushAgent.rb",
         "    total_body_drag = 0.0\n",
         "    total_body_drag = 0.0\n"
         "    transfer = link_transfer  # SINGLELANE:walls\n"),
        ("Crush3/CrushAgent.rb",
         "               @pressure_transfer * PhysicsBlackboard.instance.get_pressure(other_agent.getID())\n",
         "               transfer * PhysicsBlackboard.instance.get_pressure(other_agent.getID())  # SINGLELANE:walls\n"),
        ("Crush3/CrushAgent.rb",
         "          raw_net_force_x += @pressure_transfer * incoming_force_mag * dir_x\n",
         "          raw_net_force_x += transfer * incoming_force_mag * dir_x  # SINGLELANE:walls\n"),
        # the link a body fell on, so deaths can be counted on walled vs open links
        ("Crush3/CrushAgent.rb",
         '    $stdout.puts "CRUSHED | id=#{@javaAgent.getID()} | x=#{pos.getX()} | y=#{pos.getY()}"\n',
         '    $stdout.puts "CRUSHED | id=#{@javaAgent.getID()} | x=#{pos.getX()} | y=#{pos.getY()} | link=#{getCurrentLinkId()}"  # SINGLELANE:walls\n'),
    ],
    # Fix 18 (2026-10-21): calcSocial counted each person's repulsion in full, also from people
    # beside you (large dy, small dx). Vanilla (WalkAgent.calcSocialForceToHeading) scales it by
    # dx / dist, so only the part along the walking direction brakes. Without it people stood
    # still at ~1 person/m^2 (braking -1 to -3 m/s^2 vs ~1 m/s^2 drive), every link passed only
    # ~1-1.5 people/s per 7 m and queues backed up to the entrance (PHYSICS_TUNING_LOG.md).
    "heading": [
        ("Crush3/CrushAgent.rb",
         "      totalCrossingForce += -empty_speed * @a1 * Math.exp(@a2 * (@personalSpace - dist))\n",
         "      totalCrossingForce += -empty_speed * @a1 * Math.exp(@a2 * (@personalSpace - dist)) * (dx / dist)"
         "  # SINGLELANE:heading -- only the part along the walking direction brakes, as vanilla calcSocialForceToHeading\n"),
    ],
    # Fix 19 (2026-10-21): with a fixed personalSpace (1.044 m) people stop at ~1.02 m spacing
    # (d_stop = pS + ln(A1/A0)/A2), i.e. ~1 person/m^2 in 1 m lanes. Real walkers need space that
    # grows with speed, d = 0.36 m + 1.06 s x v (Seyfried et al. 2005, J. Stat. Mech. P10002), so
    # slow people close up. Settings personalSpaceMin / personalSpaceSlope; slope 0 (default) = old.
    "speedspace": [
        ("Crush3/CrushAgent.rb",
         '    @a2 = props.getDouble("a2", 4.682)\n',
         '    @a2 = props.getDouble("a2", 4.682)\n'
         "    # SINGLELANE:speedspace -- fix 19: personal space grows with speed, d = personalSpaceMin + personalSpaceSlope x v\n"
         "    # (Seyfried et al. 2005, J. Stat. Mech. P10002: 0.36 m + 1.06 s x v). Slope 0 = fixed personalSpace (old).\n"
         '    @ps_min = props.getDouble("personalSpaceMin", @personalSpace)\n'
         '    @ps_slope = props.getDouble("personalSpaceSlope", 0.0)\n'
         "    @ps_now = @personalSpace\n"),
        ("Crush3/CrushAgent.rb",
         "    @prev_speed = previousSpeed  # SINGLELANE:squeeze\n",
         "    @prev_speed = previousSpeed  # SINGLELANE:squeeze\n"
         "    @ps_now = @ps_slope > 0.0 ? @ps_min + @ps_slope * [previousSpeed, 0.0].max : @personalSpace  # SINGLELANE:speedspace\n"),
        # applied through the speed factor so fix 18's line stays as it is (re-running stays a no-op)
        ("Crush3/CrushAgent.rb",
         "    empty_speed = @desired_empty_speed\n",
         "    empty_speed = @desired_empty_speed\n"
         "    # SINGLELANE:speedspace -- x exp(a2 (ps_now - pS)) turns exp(a2 (pS - dist)) below into exp(a2 (ps_now - dist))\n"
         "    empty_speed *= Math.exp(@a2 * (@ps_now - @personalSpace))\n"),
    ],
    # Fix 20 (2026-10-21): people placed on a spawn node (position 0) have no contacts and stepped
    # onto the link without checking for room: 22 people/m^2 in the first 2 m, all the pressure over
    # the crush line and every recent entrance death. Entry gate, setting entryGateLength (0 = off).
    "entrygate": [
        ("Crush3/CrushAgent.rb",
         "    @ps_now = @personalSpace\n",
         "    @ps_now = @personalSpace\n"
         '    @entry_gate = props.getDouble("entryGateLength", 0.0)  # SINGLELANE:entrygate -- fix 20, 0 = off\n'),
        ("Crush3/CrushAgent.rb",
         "    _speed = [_speed, @desired_empty_speed * @body_step_factor].min if @on_body && !@is_crushed  # SINGLELANE:stepover\n",
         "    _speed = [_speed, @desired_empty_speed * @body_step_factor].min if @on_body && !@is_crushed  # SINGLELANE:stepover\n"
         "    _speed = 0.0 if @entry_gate > 0.0 && entry_blocked?(currentTime)  # SINGLELANE:entrygate\n"),
        ("Crush3/CrushAgent.rb",
         "  def calcSpeedBody(previousSpeed, currentTime, speed_factor)\n",
         "  # SINGLELANE:entrygate -- fix 20: people placed on a spawn node (position 0) have no contacts and\n"
         "  # stepped onto the link without room: 22 people/m² in its first 2 m, all the over-threshold\n"
         "  # pressure and every recent entrance death (2026-10-21). Now someone waiting at 0 enters only\n"
         "  # while the first entryGateLength m hold fewer than lanes x entryGateLength / personalSpaceMin\n"
         "  # people (one per lane every personalSpaceMin m); the rest wait off the map. Slots are counted\n"
         "  # once per link per tick, so a free gap is not filled by everyone waiting at once.\n"
         "  @@gate = {}\n"
         "  def entry_blocked?(currentTime)\n"
         "    place = @javaAgent.currentPlace\n"
         "    return false if place.getAdvancingDistance() > 0.0\n"
         "    link = place.getLink()\n"
         '    return false unless link.hasSubTag("Spawn")\n'
         "    tick = currentTime.getRelativeTime().to_i\n"
         "    key = link.ID\n"
         "    slot = @@gate[key]\n"
         "    if slot.nil? || slot[0] != tick\n"
         "      inside = 0\n"
         "      place.getLane().each do |agent|\n"
         "        pos = agent.currentPlace.getAdvancingDistance()\n"
         "        break if pos >= @entry_gate\n"
         "        inside += 1 if pos > 0.0\n"
         "      end\n"
         "      slot = @@gate[key] = [tick, place.getLaneWidth() * @entry_gate / @ps_min - inside]\n"
         "    end\n"
         "    return true if slot[1] < 1.0\n"
         "    slot[1] -= 1.0\n"
         "    false\n"
         "  end\n"
         "\n"
         "  def calcSpeedBody(previousSpeed, currentTime, speed_factor)\n"),
    ],
    # Fixes 21 and 22 (2026-10-21). 21: personal space stops growing at personalSpaceMax (Seyfried's
    # line was fitted up to 1 m/s; fast walkers kept 1.47 m and walked slower than with a fixed 0.6 m).
    # 22: CrowdWalk's pseudo-lane width MapLink.dWidth (hard-coded 1.0 m) from setting laneWidth, so
    # people fit side by side (Fruin-type body width ~0.5-0.6 m; 0.36 m x 0.55 m ~ 5 /m^2, Weidmann 5.4).
    # Both off by default.
    "pscap_lanewidth": [
        ("Crush3/CrushAgent.rb",
         "  @@dbg_last_tick = -1\n",
         "  @@dbg_last_tick = -1\n"
         "  @@lane_width_set = false  # SINGLELANE:lanewidth\n"),
        ("Crush3/CrushAgent.rb",
         '    @entry_gate = props.getDouble("entryGateLength", 0.0)  # SINGLELANE:entrygate -- fix 20, 0 = off\n',
         '    @entry_gate = props.getDouble("entryGateLength", 0.0)  # SINGLELANE:entrygate -- fix 20, 0 = off\n'
         "    # SINGLELANE:pscap -- fix 21: personal space stops growing at personalSpaceMax (Seyfried's line was fitted up to 1 m/s)\n"
         '    @ps_max = props.getDouble("personalSpaceMax", Float::INFINITY)\n'
         "    # SINGLELANE:lanewidth -- fix 22: CrowdWalk's pseudo-lane width (MapLink.dWidth, hard-coded 1.0 m, no\n"
         "    # setting) from laneWidth; set once per run, before anyone walks. 0 = leave CrowdWalk's 1.0 m.\n"
         "    # ponytail: set from Ruby because it is a public static field; a Java patch in patchCrowd.py would be cleaner.\n"
         '    lane_w = props.getDouble("laneWidth", 0.0)\n'
         "    if lane_w > 0.0 && !@@lane_width_set\n"
         "      Java::NodagumiAnanPJNetworkMapLink::MapLink.dWidth = lane_w\n"
         "      @@lane_width_set = true\n"
         '      $stdout.puts "LANEWIDTH dWidth=#{Java::NodagumiAnanPJNetworkMapLink::MapLink.dWidth}"\n'
         "    end\n"),
        ("Crush3/CrushAgent.rb",
         "    @ps_now = @ps_slope > 0.0 ? @ps_min + @ps_slope * [previousSpeed, 0.0].max : @personalSpace  # SINGLELANE:speedspace\n",
         "    @ps_now = @ps_slope > 0.0 ? @ps_min + @ps_slope * [previousSpeed, 0.0].max : @personalSpace  # SINGLELANE:speedspace\n"
         "    @ps_now = [@ps_now, @ps_max].min  # SINGLELANE:pscap\n"),
    ],
    # Fix 23 (2026-10-22): sticky lane pusher (setting stickyPusher, 0 = old "laneWidth nearest behind"
    # rule). See the comment block it adds to CrushAgent.rb.
    "stickypusher": [
        ("Crush3/CrushAgent.rb",
         '    mass_term = ItkTerm.getArg(@fallback, "mass")\n',
         '    @sticky_pusher = props.getDouble("stickyPusher", 0.0) > 0.0  # SINGLELANE:stickypusher -- fix 23, 0 = old rule\n'
         '    @pusher_id = nil\n'
         '    mass_term = ItkTerm.getArg(@fallback, "mass")\n'),
        ("Crush3/CrushAgent.rb",
         '  def search(currentTime)\n',
         '  # SINGLELANE:stickypusher -- fix 23: with narrow lanes the laneWidth nearest people behind are mostly\n'
         '  # people beside you in your own row (within ~0.3 m < one row, 0.36 m), so the push spread sideways and\n'
         '  # never built along the queue (peaks ~650 N on any push setting, 2026-10-22). The pusher is now the\n'
         "  # person directly behind in my pseudo-lane, list index - laneWidth (the lane model's index + laneWidth\n"
         '  # finds the person ahead), full share; kept while still within physicalSpace behind, because the list\n'
         '  # re-sorts every tick and a fresh pick each tick made pressure flicker between people (held <= 3 s).\n'
         '  # ponytail: a pusher can be kept by one person and newly picked by another, counting twice; claim\n'
         '  # pushers per tick if pressure looks doubled.\n'
         '  def sticky_pusher!(physicalAgent, behind, place, laneWidth)\n'
         '    cand = @pusher_id && behind.find { |e| e[:agent].getID() == @pusher_id }\n'
         '    unless cand\n'
         '      j = place.getIndexInLane(@javaAgent) - [laneWidth, 1].max\n'
         '      if j >= 0\n'
         '        back_id = place.getLane().get(j).getID()\n'
         '        cand = behind.find { |e| e[:agent].getID() == back_id }\n'
         '      end\n'
         '    end\n'
         '    @pusher_id = cand ? cand[:agent].getID() : nil\n'
         '    physicalAgent << cand.merge(share: 1.0) if cand\n'
         '  end\n'
         '\n'
         '  def search(currentTime)\n'),
        ("Crush3/CrushAgent.rb",
         '      physicalAgent.concat(behind.max_by([laneWidth, 1].max) { |b| b[:dx] })\n',
         '      if @sticky_pusher  # SINGLELANE:stickypusher\n'
         '        sticky_pusher!(physicalAgent, behind, virtualPlace, laneWidth) if distanceSoFar == 0.0\n'
         '      else\n'
         '        physicalAgent.concat(behind.max_by([laneWidth, 1].max) { |b| b[:dx] })\n'
         '      end\n'),
    ],
    # Fixes 24-26 (2026-10-22/23, PHYSICS_TUNING_LOG.md), one entry because they share band_table.
    # 24 bands: pressure from position along the link instead of who-pushes-whom (bandPressure).
    # 25 counter: oncoming people push back like the braking they cause (counterPushShare).
    # 26 lanesplit: walking lanes split by speed, so a standing queue no longer blocks the other
    #    direction's way off a link (laneSpeedWeight, laneHold). All off by default.
    # Every edit adds after the end of an earlier fix's text (or before its start), never inside it,
    # so re-running stays a no-op.
    "bands": [
        ("Crush3/CrushAgent.rb",
         "    @my_mass = mass_term ? mass_term.getDouble() : 60.0\n",
         "    @my_mass = mass_term ? mass_term.getDouble() : 60.0\n"
         "    # SINGLELANE:bands -- fix 24, see band_table. 0 = old per-person chain\n"
         '    @band_on = props.getDouble("bandPressure", 0.0) > 0.0\n'
         '    @band_len = props.getDouble("bandLength", 0.36)  # one row of people at jam spacing\n'
         '    @front_share = props.getDouble("frontPushShare", 0.0)  # share of a push passed backwards\n'
         '    @squeeze_sum = props.getDouble("squeezeSum", 0.0) > 0.0  # squeeze = push from behind + front, not the larger\n'
         '    @band_gap = props.getDouble("bandGap", -1.0)  # widest gap the chain bridges; < 0 = physicalSpace\n'
         '    @counter_share = props.getDouble("counterPushShare", 0.0)  # SINGLELANE:counter -- fix 25, 0 = off\n'
         '    @lane_speed = props.getDouble("laneSpeedWeight", 0.0) > 0.0  # SINGLELANE:lanesplit -- fix 26, 0 = CrowdWalk split\n'
         '    @lane_hold = props.getDouble("laneHold", 0.0)  # SINGLELANE:lanesplit -- s before a lane count may rise again\n'),
        ("Crush3/CrushAgent.rb",
         "  def calcSpeedBody(previousSpeed, currentTime, speed_factor)\n",
         "  def calcSpeedBody(previousSpeed, currentTime, speed_factor)\n"
         "    @@lane_tick = currentTime.getRelativeTime().to_i  # SINGLELANE:lanesplit\n"),
        ("Crush3/CrushAgent.rb",
         "    return [[], [], 0.0] if @javaAgent.isGhost()\n",
         "    return [[], [], 0.0] if @javaAgent.isGhost()\n"
         "    @counter_push = 0.0  # SINGLELANE:counter\n"),
        ("Crush3/CrushAgent.rb",
         "      laneWidthOther = virtualPlace.getOtherLaneWidth()\n",
         "      laneWidthOther = lane_widths(virtualPlace)[1]  # SINGLELANE:lanesplit\n"),
        ("Crush3/CrushAgent.rb",
         "        add_neighbor(physicalAgent, socialAgent, { agent: agent, dx: dx, dy: dy })\n"
         "\n"
         "        insensitivePos = agentPos + @insDist if countOther % laneWidthOther == 0\n",
         "        add_neighbor(physicalAgent, socialAgent, { agent: agent, dx: dx, dy: dy })\n"
         "        if @counter_share > 0.0 && dx > 0.0  # SINGLELANE:counter -- their push, weighted like the braking\n"
         "          dist = Math.sqrt(dx**2 + dy**2)\n"
         "          bb = PhysicsBlackboard.instance\n"
         "          w = [(dx / dist) * Math.exp(@a2 * (@ps_min - dist)), 1.0].min\n"
         "          @counter_push += @counter_share * (bb.get_drive(agent.getID()) + bb.get_pressure(agent.getID())) * w\n"
         "        end\n"
         "\n"
         "        insensitivePos = agentPos + @insDist if countOther % laneWidthOther == 0\n"),
        ("Crush3/CrushAgent.rb",
         "      laneWidth = virtualPlace.getLaneWidth()\n",
         "      laneWidth = lane_widths(virtualPlace)[0]  # SINGLELANE:lanesplit\n"),
        ("Crush3/CrushAgent.rb",
         "    room = [[(@back_room || 0.0), 0.0].max / @back_step, 1.0].min  # SINGLELANE:squeeze\n",
         "    if @band_on  # SINGLELANE:bands -- replaces the per-person chain for the squeeze\n"
         "      pressure_from_behind, pressure_from_front = band_pressure(currentTime)\n"
         "    end\n"
         "    room = [[(@back_room || 0.0), 0.0].max / @back_step, 1.0].min  # SINGLELANE:squeeze\n"),
        ("Crush3/CrushAgent.rb",
         "    @@dbg_max_raw_pressure = raw_crush_pressure if raw_crush_pressure > @@dbg_max_raw_pressure\n",
         "    @@dbg_max_raw_pressure = raw_crush_pressure if raw_crush_pressure > @@dbg_max_raw_pressure\n"
         "    if @band_on && @squeeze_sum  # SINGLELANE:bands -- squeezed from both sides at once\n"
         "      raw_crush_pressure = pressure_from_behind * (1.0 - 0.5 * room_ahead) + pressure_from_front * (1.0 - 0.5 * room)\n"
         "    end\n"),
        ("Crush3/CrushAgent.rb",
         "  def calcSocial(socialAgent, lowerBound, totalCrossingForce)\n",
         '''  # SINGLELANE:lanesplit -- fix 26 (2026-10-23): CrowdWalk splits a link's width between the two
  # directions by headcount, people standing still included, so a waiting queue took the lanes the
  # other side needed to get off (head-on gridlock up to 3 h on bridge maps). With laneSpeedWeight the
  # WALKING split counts each person by speed / own free speed (a stopped queue claims ~no lanes, all
  # stopped -> even); the PRESSURE split (band_table) keeps headcount, since a standing queue still
  # fills the width. laneHold (user): a lane count drops at once but rises only laneHold s after its
  # last change, so lanes don't flicker 2-3-2. Off = CrowdWalk's MapLink.getLaneWidth.
  # ponytail: CrowdWalk's laneShareDiffuser sight cap is ignored (runs use 1.0).
  @@lanes = {}
  @@held = {}
  @@lane_tick = -1
  def lane_widths(place, walk = true)
    return [place.getLaneWidth(), place.getOtherLaneWidth()] unless @lane_speed
    link = place.getLink()
    key = "#{link.ID}:#{walk}"
    c = @@lanes[key]
    c = @@lanes[key] = [@@lane_tick, held_split(key, lane_split(link, walk))] if c.nil? || c[0] != @@lane_tick
    place.isForwardDirection() ? c[1] : c[1].reverse
  end

  def held_split(key, s)
    return s unless @lane_hold > 0.0
    h = @@held[key] ||= [s.dup, [-1.0e9, -1.0e9]]
    [0, 1].each do |d|
      next if s[d] == h[0][d]
      next if s[d] > h[0][d] && @@lane_tick - h[1][d] < @lane_hold
      h[0][d] = s[d]
      h[1][d] = @@lane_tick
    end
    h[0].dup
  end

  # [forward lanes, backward lanes]: share of width / dWidth by (speed-weighted) headcount, at least 1
  def lane_split(link, walk)
    n = [0, 0]
    w = [0.0, 0.0]
    link.getAgents().each do |a|
      d = a.isForwardDirection() ? 0 : 1
      n[d] += 1
      w[d] += walk ? walk_weight(a) : 1.0
    end
    w = n.map { |x| x > 0 ? 1.0 : 0.0 } if w.sum <= 0.0  # all stopped -> even
    return [1, 1] if w.sum <= 0.0
    total = link.getWidth() / Java::NodagumiAnanPJNetworkMapLink::MapLink.dWidth
    w.map { |x| [(x * total / w.sum).to_i, 1].max }
  end

  def walk_weight(a)  # share of own free walking speed, 0..1
    e = a.getEmptySpeed()
    e > 0.0 ? [[a.getSpeed() / e, 0.0].max, 1.0].min : 1.0
  end

  # SINGLELANE:bands -- fix 24 (2026-10-22): once per link, direction and tick, cut the lane into
  # bandLength strips along the link (one row of people at jam spacing). Back walk: each strip passes
  # transfer x what it got + its people's push to the strip ahead; front walk: the same backwards with
  # frontPushShare x push. A gap wider than bandGap breaks the chain; a strip holding a body passes only
  # bodyPassShare. Each person gets their strip's force / number of lanes (the old 1/laneWidth share).
  # SINGLELANE:counter -- fix 25 (2026-10-22, user): oncoming people push back like the braking they
  # cause (search). That push is carried backwards along the chain as the largest one carried, not a
  # sum: a sum built 7,400-15,078 N peaks and made pressure flicker to 0 and back (tuning log).
  # ponytail: chain stops at link ends (the old rule did too); join links if jams span them.
  @@bands = {}
  @@counter = {}
  def band_pressure(currentTime)
    place = @javaAgent.currentPlace
    tick = currentTime.getRelativeTime().to_i
    key = "#{place.getLink().ID}:#{@javaAgent.isForwardDirection()}"
    @@counter[getAgentId()] = @counter_push || 0.0
    c = @@bands[key]
    c = @@bands[key] = [tick, band_table(place)] if c.nil? || c[0] != tick
    v = c[1][getAgentId()] || [0.0, 0.0]
    [v[0], v[1] + (@counter_push || 0.0)]  # my own counter push on top
  end

  def band_table(place)
    bb = PhysicsBlackboard.instance
    lanes = [lane_widths(place, false)[0], 1].max.to_f
    transfer = link_transfer
    bands = []  # [strip index, first pos, last pos, ids, push sum, body?, counter push sum], back to front
    place.getLane().each do |a|
      pos = a.currentPlace.getAdvancingDistance()
      next if pos <= 0.0  # people waiting at the entry gate are off the map
      body = a.hasTag("crushed")
      next if a.isGhost() && !body
      k = (pos / @band_len).floor
      b = bands.last
      bands << (b = [k, pos, pos, [], 0.0, false, 0.0]) if b.nil? || b[0] != k
      b[2] = pos
      if body
        b[5] = true
      else
        b[3] << a.getID()
        b[4] += bb.get_drive(a.getID())
        b[6] += @@counter[a.getID()] || 0.0
      end
    end
    gap = @band_gap < 0.0 ? @physicalSpace : @band_gap
    n = bands.size
    fb = Array.new(n, 0.0)
    ff = Array.new(n, 0.0)
    fc = Array.new(n, 0.0)
    (1...n).each do |i|
      prev = bands[i - 1]
      next if bands[i][1] - prev[2] > gap
      fb[i] = (transfer * fb[i - 1] + prev[4]) * (prev[5] ? @body_pass : 1.0)
    end
    (n - 2).downto(0) do |i|
      nxt = bands[i + 1]
      next if nxt[1] - bands[i][2] > gap
      body = nxt[5] ? @body_pass : 1.0
      ff[i] = (transfer * ff[i + 1] + @front_share * nxt[4]) * body
      fc[i] = [transfer * fc[i + 1], transfer * nxt[6]].max * body
    end
    t = {}
    bands.each_with_index { |b, i| b[3].each { |id| t[id] = [fb[i] / lanes, (ff[i] + fc[i]) / lanes] } }
    t
  end

  def calcSocial(socialAgent, lowerBound, totalCrossingForce)
'''),
    ],
    # Fix 27 (2026-10-23, user): the crush clock reset to 0 on any short dip, so someone squeezed for
    # 200 s got a full 240 s again after 1 s of relief. It now winds down crushRecover s per s of
    # relief (0 = old reset). Breathing muscles tire under sustained load (Motomura et al. 2019, J Nippon
    # Med Sch 86(6)); no measured recovery rate exists, k = 2 was chosen by the crush rate (11/36 runs).
    "crushclock": [
        ("Crush3/CrushAgent.rb",
         '    @crush_margin = props.getDouble("crushMargin", 50.0)\n',
         '    @crush_margin = props.getDouble("crushMargin", 50.0)\n'
         '    @crush_recover = props.getDouble("crushRecover", 0.0)  # SINGLELANE:crushclock -- s off per s of relief, 0 = reset\n'),
        ("Crush3/CrushAgent.rb",
         "      @time_over_threshold = 0.0  # no contact -> pressure released\n",
         "      @time_over_threshold = @crush_recover > 0.0 ? [@time_over_threshold - @crush_recover * currentTime.getTickUnit(), 0.0].max : 0.0"
         "  # SINGLELANE:crushclock -- no contact -> pressure released\n"),
        ("Crush3/CrushAgent.rb",
         "    else\n      @time_over_threshold = 0.0\n    end\n",
         "    else\n"
         "      @time_over_threshold = @crush_recover > 0.0 ? [@time_over_threshold - @crush_recover * currentTime.getTickUnit(), 0.0].max : 0.0"
         "  # SINGLELANE:crushclock\n"
         "    end\n"),
    ],
}


def apply_ruby_fixes():
    """Adds RUBY_FIXES to Crush3/*.rb. Skips an edit that is already there; stops if an anchor is missing."""
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