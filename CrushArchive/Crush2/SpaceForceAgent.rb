require 'RubyAgentBase.rb'

class SpaceForceAgent < RubyAgentBase
  TriggerFilter = ["update"]

  attr_accessor :personal_space_radius, :firmness

  def initialize(agent, config, fallback)
    super(agent, config, fallback)
    # Access the Properties Handler from the Java Simulator
    props = getSimulator().getProperties()

    # DYNAMIC PARAMETERS: Pull from prop.json with default fallbacks
    # 'personal_space_radius' defines the distance agents try to maintain.
    @personal_space_radius = props.getDouble("personal_space_radius", 1.2)
    
    # 'firmness' defines how strongly they push away when inside that radius.
    @firmness = props.getDouble("firmness", 0.8)
  end

  def calcSpaceForce
    my_pos = @javaAgent.getPosition()
    all_agents = getSimulator().getAllAgentCollection()
    closestAgent = @personal_space_radius

    all_agents.each do |other|
      next if other == @javaAgent
      next if other.nil?
      next if other.isGhost()

      dist = my_pos.distance(other.getPosition())

      if dist < closestAgent && dist > 0.1
          closestAgent = dist
      end
    end

    if closestAgent < @personal_space_radius
      penetration = @personal_space_radius - closestAgent

      modifier = 1.0 - (penetration/@personal_space_radius)*@firmness

      return [[modifier,0.0].max,1.0].min
    end

    return 1.0 #No intrusion
  end

  def update
    return super()
  end
end