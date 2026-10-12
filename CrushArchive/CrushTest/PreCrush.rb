require 'GhostAgentManager.rb'
require 'TelemetryHandler.rb'
require 'PhysicalAgent.rb'

class PreCrush < PhysicalAgent
  TriggerFilter = ["update"]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)

    @target_x = 530.0 + (rand - 0.5) * 2.0
    @target_y = 482.0 + (rand - 0.5) * 2.0
    @trigger_distance = 1.5
  end

  # ============================================================
  def update
    return true if @is_crushed

    # 1. Check current position
    pos = @javaAgent.getPosition()
    
    # 2. Calculate distance to the "Test Spot"
    dx = pos.getX() - @target_x
    dy = pos.getY() - @target_y
    dist_to_target = Math.sqrt(dx * dx + dy * dy)

    # 3. Trigger immediate crush if within range
    if dist_to_target < @trigger_distance
      $stdout.puts "TEST: PreCrushedAgent reached spot. Triggering crush."
      $stdout.flush
      
      # We skip the pressure logic and go straight to the crush
      crush_agent!
      return true
    end

    # Otherwise, keep walking normally toward the destination
    return super()
  end
end
