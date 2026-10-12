require 'RubyColorBase.rb'
require 'java'
java_import 'java.awt.Color'

class CrushColor < RubyColorBase

  def initialize()
    super

    # SpeedModel parameters
    @coefficientOfHue = 0.35
    @exponent = 5.0
    @saturation = 0.8588
    @brightness = 0.698
  end
  
  def getAgentColorRGB(agent)
    # 🔵 crushed override
    if agent.hasTag("crushed")
      return [0, 0, 0]
    end

    # 🌈 replicate SpeedModel
    speed = agent.getSpeed()
    hue = (speed ** @exponent) * @coefficientOfHue
    hue = [[hue, 0.0].max, 1.0].min

    rgb_int = Color.HSBtoRGB(hue.to_f, @saturation.to_f, @brightness.to_f)

    r = (rgb_int >> 16) & 0xFF
    g = (rgb_int >> 8) & 0xFF
    b = rgb_int & 0xFF

    return [r, g, b]
  end
end