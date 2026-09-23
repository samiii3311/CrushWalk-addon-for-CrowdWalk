module TelemetryHandler
  def self.update_telemetry(agent, physics_data = {}, current_time = nil)
    return unless agent

    agent_id = agent.getID().to_s
    bb = PhysicsBlackboard.instance

    if agent.config.nil?
  agent.config = ItkTerm.newTerm()
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