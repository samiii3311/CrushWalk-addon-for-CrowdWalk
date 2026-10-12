# TelemetryHandler.rb
module TelemetryHandler
  # This is the ONLY place you need to add or change fields
  def self.update_telemetry(agent, physics_data)
    # 1. Access the Java config Term directly
    config = agent.config

    # 2. Map your physics variables to the CSV keys defined in properties.json
    # Format: config.setArg("CSV_KEY", VALUE)
    config.setArg("p_acc", physics_data[:pressure])
    config.setArg("compression", physics_data[:compression])
    
    # 3. Dynamic logic: Check for specific thresholds on the fly
    # Change '9.0' to '8.0' here and restart the sim to see results
    config.setArg("is_near_limit", physics_data[:pressure] > 9.0 ? 1 : 0)
    
    # 4. Optional: Add a "state" column
    config.setArg("agent_status", agent.hasTag("crushed") ? "CRITICAL" : "STABLE")

    config.setArg("current_speed",physics_data[:speed])
  end
end