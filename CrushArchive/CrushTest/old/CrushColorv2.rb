require 'RubyColorBase.rb'

class CrushColor < RubyColorBase

  def initialize()
    super
  end

  def getAgentColorRGB(agent)
    triage = agent.getTriageName()

    case triage
    when "BLACK"
      return [0, 0, 255]   # dead (blue)
    when "RED"
      return [255, 0, 0]
    when "YELLOW"
      return [255, 255, 0]
    else
      return [0, 255, 0]   # alive
    end
  end
end