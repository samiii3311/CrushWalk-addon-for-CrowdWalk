require 'RubyAgentBase.rb'

class SpaceForceAgent < RubyAgentBase
  TriggerFilter = ["update"]

  attr_accessor :personal_space_radius, :firmness

  def initialize(agent, config, fallback)
    super(agent, config, fallback)
    @personal_space_radius = 1.2
    @firmness = 0.8
    @original_empty_speed = getEmptySpeed()
  end

  def update
    my_pos = @javaAgent.getPosition()
    all_agents = getSimulator().getAllAgentCollection()
    
    total_repulsion = 0.0
    active_interference = false

    all_agents.each do |other|
      next if other == @javaAgent

      # 🔥 ADD THIS
      next if other.hasTag("CRUSHED_BODY")

      dist = my_pos.distance(other.getPosition())
      
      if dist < @personal_space_radius && dist > 0.05
        total_repulsion += (@personal_space_radius - dist) * @firmness
        active_interference = true
      end
    end

    if active_interference
      apply_movement_penalty(total_repulsion)
    else
      setEmptySpeed(@original_empty_speed)
    end
    
    return super()
  end

  def apply_movement_penalty(repulsion)
    if repulsion > 0.1
      setEmptySpeed(@original_empty_speed * 0.4)
    end
  end
end