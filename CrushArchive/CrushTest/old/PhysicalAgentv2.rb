require 'SpaceForceAgent.rb'
require 'GhostAgentManager.rb'

class PhysicalAgent < SpaceForceAgent
  TriggerFilter = ["update"]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)
    @is_crushed = false
    @body_radius = 0.5
  end

  def update
    $stdout.puts "UPDATE #{@javaAgent.getID()}"
    $stdout.flush

  return true if @is_crushed

    my_pos = @javaAgent.getPosition()
    all_agents = getSimulator().getAllAgentCollection()

    crowd_pressure = 0

    all_agents.each do |other|
      next if other == @javaAgent
      dist = my_pos.distance(other.getPosition())

      if dist < 0.5
        crowd_pressure += 1
      end
    end

    # 🔥 BREAK DEADLOCKS
    if crowd_pressure > 2 && rand < 0.2
      @javaAgent.exposed(0.5)   # temporary speed boost to escape
    end

    GhostAgentManager.get_bodies.each do |body|
      dx = my_pos.getX() - body[:x]
      dy = my_pos.getY() - body[:y]
      dist = Math.sqrt(dx*dx + dy*dy)


     
    end

    $stdout.puts "pressure=#{crowd_pressure} id=#{@javaAgent.getID()}"

    # 🔥 SIMPLE CRUSH CONDITION
    if crowd_pressure > 3
      crush_agent!
      return true
    end

    apply_body_slowdown

    return super()
  end

  def crush_agent!
    return true if @is_crushed   # prevent double logging

    @is_crushed = true

    pos = @javaAgent.getPosition()

    # 🔥 LOG HERE
    $stdout.puts "CRUSHED | id=#{@javaAgent.getID()} | x=#{pos.getX()} | y=#{pos.getY()}"
    $stdout.flush

    # store for physics
    GhostAgentManager.add_body(pos.getX(), pos.getY())

    # spawn visual ghost agent
    simulator = getSimulator()
    #simulator.generateAgent("GhostAgent", pos)

    # remove original agent
    #simulator.removeAgent(@javaAgent)

    return true
  end

 def apply_body_slowdown
  my_pos = @javaAgent.getPosition()

  GhostAgentManager.get_bodies.each do |body|
    next if body[:x].nil? || body[:y].nil?

    dx = my_pos.getX() - body[:x]
    dy = my_pos.getY() - body[:y]
    dist = Math.sqrt(dx*dx + dy*dy)

    next if dist == 0.0

    if dist < 1.2
      # base slowdown
      factor = 1.0 + (1.2 - dist) * 0.5

      # 🔥 ANTI-CLOG: reduce slowdown randomly
      if rand < 0.3
        factor *= 0.5   # sometimes "slip through"
      end

      @javaAgent.exposed(factor)
    end
  end
end
end