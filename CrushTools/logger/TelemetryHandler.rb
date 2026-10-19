# Writes per-agent values into agent.config for the loggers added by patchLogger.py (see README.md here).
# Two copies on purpose (user, 2026-10-20): this one for the logger, Crush3/TelemetryHandler.rb for the
# CrushWalk runs. Keep the field list the same in both. Only this copy gives each agent its own config
# (below); in CrushWalk, CrushAgent.rb does that itself (ensure_fresh_config).
module TelemetryHandler
  def self.update_telemetry(agent, physics_data = {}, current_time = nil)
    return unless agent

    # Agents made by the same generation rule share one config object. Give each agent its own the
    # first time, or every agent overwrites the others' values and the logs mix them up.
    agent_id = agent.getID().to_s
    owner = agent.config.respond_to?(:getArgString) ? agent.config.getArgString("telemetry_owner") : nil
    if owner != agent_id
      agent.config = ItkTerm.newTerm()
      agent.config.setArg("telemetry_owner", agent_id)
    end

    config = agent.config

    pressure     = physics_data[:pressure]     || 9999
    speed        = physics_data[:speed]        || 9999
    empty_speed  = physics_data[:empty_speed]  || 9999
    net_force    = physics_data[:net_force]    || 0.0
    social_force = physics_data[:social_force] || 0.0
    link_id      = physics_data[:link_id]      || ""
    position     = physics_data[:position]     || 0.0        
    blocked_by   = physics_data[:blocked_by]   || ""

    config.setArg("link_id", link_id)
    config.setArg("position", position)
    direction = physics_data[:link_direction] || 1
    config.setArg("link_direction", direction.to_s)
    # Speed split by walking direction (0 in the other one), so the link logger's
    # mean_speed_fwd / mean_speed_bwd give per-direction speeds on one link row.
    config.setArg("speed_fwd", (direction > 0 ? speed : 0.0).round(3).to_s)
    config.setArg("speed_bwd", (direction < 0 ? speed : 0.0).round(3).to_s)
    config.setArg("compression_pressure", pressure.round(2).to_s)
    # before the body's resistance is subtracted -- nonzero even when compression_pressure is 0
    config.setArg("raw_pressure", (physics_data[:raw_pressure] || 0.0).round(2).to_s)
    # seconds this agent's pressure has stayed over crushThreshold in a row (crushed at crushDuration)
    config.setArg("time_over_threshold", (physics_data[:time_over_threshold] || 0.0).round(1).to_s)
    config.setArg("current_speed", speed.round(3).to_s)
    config.setArg("empty_speed", empty_speed.round(3).to_s)
    config.setArg("push_force", net_force.round(3).to_s)
    config.setArg("social_force", social_force.round(3).to_s)
    config.setArg("blocked_by", blocked_by)
    config.setArg("agent_status", agent.hasTag("crushed") ? "1" : "0")

    if current_time
      config.setArg("last_telemetry_tick", current_time.getRelativeTime().to_i.to_s)
    end
  end
end