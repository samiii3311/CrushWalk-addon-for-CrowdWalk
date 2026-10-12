require 'RubyAgentBase.rb'

class GhostAgent < RubyAgentBase
  TriggerFilter = []

  def initialize(agent, config, fallback)
    super(agent, config, fallback)
    @javaAgent.getTags().add("CRUSHED_BODY")
    setEmptySpeed(0.0)
  end

  def update
    setEmptySpeed(0.0)
    return true
  end
end