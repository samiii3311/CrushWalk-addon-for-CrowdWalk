require 'SpaceForceAgent.rb'
require 'GhostAgentManager.rb'
require 'TelemetryHandler.rb'

class PhysicalAgent < SpaceForceAgent
  TriggerFilter = ["update"]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)

    @is_crushed = false

    @is_on_first_link = true

    # DYNAMIC PARAMETERS (Pulls from prop.json via Java)
    props = getSimulator().getProperties()

    # PHYSICAL PARAMETERS
    @body_radius = props.getDouble("body_radius",0.4)
    @pressure_accum = 0.0
    @pressure_threshold = props.getDouble("pressure_threshold",10.0)

    # BEHAVIOR TUNING
    @recovery_rate = props.getDouble("recovery_rate",0.8)
    @compression_gain = props.getDouble("compression_gain",0.1)

    #Resistance PARAMETERS
    @strength = props.getDouble("strength",0.5) #strength of resistance(higher = harder to move)
    @sharpness = props.getDouble("sharpness",2.0)#sudden slowdown
    @influence_dist = props.getDouble("influence_dist",1.2)#resistance start dist

    #If agents:
      # die too fast → increase @pressure_threshold
      # never die → increase @compression_gain
      # feel too soft → increase @body_radius
  end

  # ============================================================
  # Safe ghost activation
  # ============================================================
  def enable_ghost_mode
    return unless @javaAgent

    if @javaAgent.respond_to?(:setGhost)
      @javaAgent.setGhost(true)
    end

    if @javaAgent.respond_to?(:setSpeed)
      @javaAgent.setSpeed(0.0)
    end

    #$stdout.puts "GHOST ENABLED id=#{@javaAgent.getID()}"
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

    if @is_on_first_link
      if currentLink != @javaAgent.getCurrentLink()
        @is_on_first_link = false 
      end
    end

    is_spawning = @is_on_first_link && walked_dist < 3.0
    is_exiting = dist_to_exit < 1.0 && is_at_exit

    unless is_spawning || is_exiting
      # ========================================================
      # COMPRESSION CALCULATION (physics-based)
      # ========================================================
      compression = 0.0

      all_agents.each do |other|
        next if other == @javaAgent
        next if other.nil?

        dist = pos.distance(other.getPosition())

        # overlap = how much bodies intersect
        overlap = (@body_radius * 2.0) - dist

        if overlap > 0
          compression += overlap
        end
      end

      # ========================================================
      # PRESSURE ACCUMULATION (time-based crushing)
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
      speed:@javaAgent.getSpeed()
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

    # optional slowdown from bodies
    apply_body_slowdown

    physics_results = {
      pressure: @pressure_accum,
      compression: compression,
      speed:@javaAgent.getSpeed()

    }
    TelemetryHandler.update_telemetry(@javaAgent, physics_results)

    return super()
  end

  # ============================================================
  def crush_agent!
    return true if @is_crushed

    @is_crushed = true

    pos = @javaAgent.getPosition()
    # mark agent
    @javaAgent.addTag("crushed")
    # correct ghost activation
    enable_ghost_mode

    # log
    #$stdout.puts "CRUSHED | id=#{@javaAgent.getID()} | x=#{pos.getX()} | y=#{pos.getY()}"
    $stdout.flush

    # store body
    GhostAgentManager.add_body(pos.getX(), pos.getY())

    return true
  end

  # ============================================================
  # Bodies slow down nearby agents
  # ============================================================
  def apply_body_slowdown
    base_speed = @javaAgent.getEmptySpeed()
    #emptySpeed =1.02265769054586 unless set
    space_mod = calcSpaceForce()
    total_resistance = 0.0
    
    pos = @javaAgent.getPosition()
    # 2. Accumulate "Resistance" from dead bodies 
    GhostAgentManager.get_bodies.each do |body|
      dx = pos.getX() - body[:x]
      dy = pos.getY() - body[:y]
      dist = Math.sqrt(dx * dx + dy * dy)

      next if dist == 0.0 || dist > 1.5 # Only care about immediate vicinity

      # Intensity of resistance increases exponentially as you get closer
      # This formula is inspired by calcSocialForce in WalkAgent.java
      # Resistance = A * exp(B * (Size - dist))
      resistance = @strength * Math.exp(@sharpness *(@influence_dist - dist))
      total_resistance += resistance
    end

    adjusted_speed = base_speed * space_mod
    
    # 3. Calculate the New Speed
    if total_resistance > 0
      # If resistance is high, speed approaches 0
      adjusted_speed /=  (1.0 + total_resistance)

      if total_resistance > 10.0
        adjusted_speed *= 0.1 # Force a near-stop
      end
      
      # Apply a small random jitter to prevent perfect grid-locking
      adjusted_speed *= (0.8 + rand * 0.4) 
      
      @javaAgent.setSpeed(adjusted_speed)
    else
      # If no bodies nearby, ensure we return to base speed
      @javaAgent.setSpeed(base_speed)
    end
  end

  def check_simulation_end
    all_agents = getSimulator().getAllAgentCollection()

    remaining_living = all_agents.any? do |a|
      a && !a.hasTag("crushed") && !a.isGhost()
    end

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
