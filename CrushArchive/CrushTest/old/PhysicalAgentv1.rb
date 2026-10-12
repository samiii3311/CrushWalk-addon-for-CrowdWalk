require 'SpaceForceAgent.rb'
require 'GhostAgentManager.rb'

class PhysicalAgent < SpaceForceAgent
  TriggerFilter = ["update"]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)
    @is_crushed = false
    @body_radius = 0.5
    @crush_threshold = 2.0
  end

  def update
    if @is_crushed
      setEmptySpeed(0.0)
      check_simulation_end 
      return true # Documentation: methods must return boolean
    end
    
    my_pos = @javaAgent.getPosition()
    all_agents = getSimulator().getAllAgentCollection()

    body_slowdown = 1.0

    GhostAgentManager.get_bodies.each do |body|
      dx = my_pos.getX() - body[:x]
      dy = my_pos.getY() - body[:y]
      dist = Math.sqrt(dx*dx + dy*dy)

      if dist < @body_radius
        body_slowdown *= 0.5
      end
    end
    setEmptySpeed(@original_empty_speed * body_slowdown)
    return super()
  end

def crush_agent!
  @is_crushed = true

  pos = @javaAgent.getPosition()

  $stdout.puts "AGENT CRUSHED at (#{pos.getX()}, #{pos.getY()})"
  $stdout.flushs
  
  @javaAgent.addTag("CRUSHED_BODY")

  # store for physics
  GhostAgentManager.add_body(pos.getX(), pos.getY())

  # 🔥 spawn visual ghost agent
  simulator = getSimulator()
  simulator.generateAgent("GhostAgent", pos)

  # remove original agent
  simulator.removeAgent(@javaAgent)

  return true
end

  def check_simulation_end
    all_agents = getSimulator().getAllAgentCollection()
    # Check if any "living" agents remain
    remaining_living = all_agents.any? { |a| !a.hasTag("CRUSHED_BODY") }
    
    unless remaining_living
      $stdout.puts "ALL ACTIVE AGENTS CLEARED. FINISHING..."
      $stdout.flush
      getSimulator().finish() 
    end
    return true
  end
end