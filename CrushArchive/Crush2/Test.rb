require 'RubyAgentBase.rb'
require 'PhysicalAgent.rb'

class Test < RubyAgentBase

  def initialize(agent, config, fallback)
      super(agent, config, fallback)
  end

  def calcSpeed(previousSpeed)
    currentTime = getCurrentTime()
    
    _speed = calcSpeedBody(previousSpeed,currentTime)

    _speed = @javaAgent.currentPlace.getLink().calcRestrictedSpeed(_speed,@javaAgent,currentTime)
  
    deltaDistance = _speed * currentTime.getTickUnit()

    if @javaAgent.currentPlace.isBeyondLinkWithAdvance(deltaDistance)
      _speed = @javaAgent.currentPlace.getHeadingNode().calcRestrictedSpeed(_speed, @javaAgent, currentTime)
    end

    _speed = @javaAgent.obstructer.calcAffectedSpeed(_speed)

    return _speed
  end

  def calcSpeedBody(previousSpeed,currentTime)
    baseSpeed = @javaAgent.currentPlace.getLink().calcEmptySpeedForAgent(getEmptySpeed(), @javaAgent, currentTime)

    accel = calcAccel(baseSpeed, previousSpeed)

    deltaSpeed = accel * currentTime.getTickUnit()
    _speed = previousSpeed + deltaSpeed

    if _speed > baseSpeed
      _speed = baseSpeed
    elsif _speed < 0
      distanceFromStart = @javaAgent.currentPlace.getAdvancingDistance()
      
      wantedBackDist = _speed * currentTime.getTickUnit()

      if (distanceFromStart + wantedBackwardDistance) < 0
        _speed = -distanceFromStart / currentTime.getTickUnit()
        agentID = getAgentID()
        puts "back TO start" + agentID
      else
        puts agentID + "minus speed:" + _speed
      end
    end

    width = @javaAgent.currentPlace.getLaneWidth()
    indexInLane = @javaAgent.currentPlace.getIndexFromHeadingInLane(@javaAgent)
    
    if indexInLane < width && @javaAgent.currentPlace.getHeadingNode().hasTag(getGoal())
      _speed = baseSpeed
    end

    return _speed
  end
end