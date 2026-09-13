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

    config.setArg("crush_pressure", pressure.round(2).to_s)
    config.setArg("current_speed", speed.round(3).to_s)
    config.setArg("empty_speed", empty_speed.round(3).to_s)
    config.setArg("agent_status", agent.hasTag("crushed") ? "1" : "0")

    if current_time
      config.setArg("last_telemetry_tick", current_time.getRelativeTime().to_i.to_s)
    end
  end
end