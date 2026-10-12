require 'RubyAgentBase.rb'
require 'GhostAgentManager.rb'
require 'TelemetryHandler.rb'

# Inherits from base directly. We let Java handle living-agent collision avoidance.
class PhysicalAgent < RubyAgentBase
  TriggerFilter = ["update"]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)

    @is_crushed = false
    @is_on_first_link = true

    # DYNAMIC PARAMETERS
    props = getSimulator().getProperties()

    # PHYSICAL PARAMETERS
    @body_radius = props.getDouble("body_radius", 0.4)
    @pressure_accum = 0.0
    @pressure_threshold = props.getDouble("pressure_threshold", 10.0)

    # BEHAVIOR TUNING
    @recovery_rate = props.getDouble("recovery_rate", 0.8)
    @compression_gain = props.getDouble("compression_gain", 0.1)

    # RESISTANCE PARAMETERS (The Tripping Mechanic)
    @strength = props.getDouble("strength", 0.5) 
    @sharpness = props.getDouble("sharpness", 2.0)
    @influence_dist = props.getDouble("influence_dist", 1.2)
  end

  # ============================================================
  # Safe ghost activation (Allows living to walk over the dead)
  # ============================================================
  def enable_ghost_mode
    return unless @javaAgent

    if @javaAgent.respond_to?(:setGhost)
      @javaAgent.setGhost(true)
    end

    if @javaAgent.respond_to?(:setSpeed)
      @javaAgent.setEmptySpeed(0.0)
    end

    $stdout.flush
  end

  # ============================================================
  def update
    return true if @is_crushed

    walked_dist = @javaAgent.getAdvancingDistance()
    dist_to_exit = @javaAgent.getRemainingDistance()
    is_at_exit = @javaAgent.getGoal()

    pos = @javaAgent.getPosition()
    currentLink = @javaAgent.getCurrentLink()
    all_agents = getSimulator().getAllAgentCollection()

    if @is_on_first_link && currentLink != @javaAgent.getCurrentLink()
      @is_on_first_link = false 
    end

    is_spawning = @is_on_first_link && walked_dist < 3.0
    is_exiting = dist_to_exit < 1.0 && is_at_exit

    compression = 0.0 # Scope outside the unless block for telemetry

    unless is_spawning || is_exiting
      # ========================================================
      # COMPRESSION CALCULATION (Physical Overlap Only)
      # ========================================================
      all_agents.each do |other|
        next if other == @javaAgent || other.nil? || other.isGhost()

        dist = pos.distance(other.getPosition())
        overlap = (@body_radius * 2.0) - dist

        if overlap > 0
          compression += overlap
        end
      end

      # ========================================================
      # PRESSURE ACCUMULATION
      # ========================================================
      if compression > 0
        @pressure_accum += compression * @compression_gain
      else
        @pressure_accum *= @recovery_rate
      end
    end

    physics_results = {
      pressure: @pressure_accum,
      compression: compression,
      speed: @javaAgent.getSpeed()
    }
    TelemetryHandler.update_telemetry(@javaAgent, physics_results)

    # ========================================================
    # CRUSH CONDITION
    # ========================================================
    if @pressure_accum > @pressure_threshold
      crush_agent!
      check_simulation_end()
      return true
    end

    # Apply the stumbling/friction override
    apply_body_slowdown

    return super()
  end

  # ============================================================
  def crush_agent!
    return true if @is_crushed

    @is_crushed = true
    pos = @javaAgent.getPosition()
    
    @javaAgent.addTag("crushed")
    enable_ghost_mode
    GhostAgentManager.add_body(pos.getX(), pos.getY())

    return true
  end

  # ============================================================
  # The "Tripping" Mechanic: Overriding Java to force stumbling
  # ============================================================
  def apply_body_slowdown
    # Start with whatever speed the Java engine naturally calculated for this tick
    current_speed = @javaAgent.getSpeed() 
    total_resistance = 0.0
    pos = @javaAgent.getPosition()
    
    GhostAgentManager.get_bodies.each do |body|
      dx = pos.getX() - body[:x]
      dy = pos.getY() - body[:y]
      dist = Math.sqrt(dx * dx + dy * dy)

      next if dist == 0.0 || dist > 1.5 

      # Exponential friction
      resistance = @strength * Math.exp(@sharpness * (@influence_dist - dist))
      total_resistance += resistance
    end

    # Hijack the physics only if the agent is actively walking over a body
    if total_resistance > 0
      adjusted_speed = current_speed / (1.0 + total_resistance)

      if total_resistance > 10.0
        adjusted_speed *= 0.1 # Severe stumble
      end
      
      # Apply random jitter to simulate unstable footing on crushed agents
      adjusted_speed *= (0.8 + rand * 0.4) 
      
      # Forcefully override the Java simulation
      @javaAgent.setSpeed(adjusted_speed)
    end
    # Note: If total_resistance is 0, we do NOTHING. 
    # This allows the Java WalkAgent to regain normal control and accelerate cleanly.
  end

  def check_simulation_end
    all_agents = getSimulator().getAllAgentCollection()
    remaining_living = all_agents.any? { |a| a && !a.hasTag("crushed") && !a.isGhost() }

    unless remaining_living
      $stdout.puts "===================================="
      $stdout.puts " ALL AGENTS CRUSHED - END SIMULATION"
      $stdout.puts "===================================="
      $stdout.flush
      getSimulator().finish()
    end

    return true
  end
end