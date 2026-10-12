# working passing model

require 'SpaceForceAgent.rb'
require 'GhostAgentManager.rb'

class PhysicalAgent < SpaceForceAgent
  TriggerFilter = ["update"]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)
    @is_crushed = false
    @body_radius = 0.5
    @pressure_accum = 0.0
    @pressure_threshold = 8.0   # tune this (higher = harder to crush)
  end

  # ============================================================
  # 🔥 Enable ghost mode safely
  # ============================================================
  def enable_ghost_mode
    return unless @javaAgent

    # Only call if method exists (avoids crash if Java not rebuilt)
    if @javaAgent.respond_to?(:setGhost)
      @javaAgent.setGhost(true)
    end

    # Stop movement (optional but recommended for "dead body")
    if @javaAgent.respond_to?(:setSpeed)
      @javaAgent.setSpeed(0.0)
    end

    $stdout.puts "GHOST ENABLED id=#{@javaAgent.getID()}"
    $stdout.flush
  end

  # ============================================================
  def update
    #$stdout.puts "UPDATE #{@javaAgent.getID()}"
    #$stdout.flush

    return true if @is_crushed

    my_pos = @javaAgent.getPosition()
    all_agents = getSimulator().getAllAgentCollection()

    crowd_pressure = 0

    all_agents.each do |other|
      next if other == @javaAgent
      next if other.nil?

      dist = my_pos.distance(other.getPosition())

      if dist < 0.5
        crowd_pressure += 1
      end
    end

    # # 🔥 BREAK DEADLOCKS
    # if crowd_pressure > 2 && rand < 0.2
    #   @javaAgent.exposed(0.5)
    # end

    # 🔥 (currently unused loop kept safe)
    GhostAgentManager.get_bodies.each do |body|
      next if body[:x].nil? || body[:y].nil?

      dx = my_pos.getX() - body[:x]
      dy = my_pos.getY() - body[:y]
      dist = Math.sqrt(dx * dx + dy * dy)
    end

    #$stdout.puts "pressure=#{crowd_pressure} id=#{@javaAgent.getID()}"

    # 🔥 CRUSH CONDITION
    # 🔥 IMPROVED CRUSH MODEL (time-based)
    if crowd_pressure > 3
      @pressure_accum += crowd_pressure * 0.05
    else
      # recover if pressure drops
      @pressure_accum *= 0.8
    end

    if @pressure_accum > @pressure_threshold
      crush_agent!
      return true
    end

    apply_body_slowdown

    return super()
  end

  # ============================================================
  def crush_agent!
    return true if @is_crushed   # prevent double logging

    @is_crushed = true

    pos = @javaAgent.getPosition()

    # 🔥 ADD TAG (this is the key addition)
    @javaAgent.addTag("crushed")

    enable_ghost_mode()

    # 🔥 LOG
    $stdout.puts "CRUSHED | id=#{@javaAgent.getID()} | x=#{pos.getX()} | y=#{pos.getY()}"
    $stdout.flush

    # store for physics
    GhostAgentManager.add_body(pos.getX(), pos.getY())

    return true
    end

  # ============================================================
  def apply_body_slowdown
    my_pos = @javaAgent.getPosition()

    GhostAgentManager.get_bodies.each do |body|
      next if body[:x].nil? || body[:y].nil?

      dx = my_pos.getX() - body[:x]
      dy = my_pos.getY() - body[:y]
      dist = Math.sqrt(dx * dx + dy * dy)

      next if dist == 0.0

      if dist < 1.2
        factor = 1.0 + (1.2 - dist) * 0.5

        # 🔥 ANTI-CLOG
        if rand < 0.3
          factor *= 0.5
        end

        #@javaAgent.exposed(factor)
      end
    end
  end
end