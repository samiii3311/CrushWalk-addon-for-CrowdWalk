require 'RubyAgentBase.rb'
require 'PhysicsBlackboard.rb'
require 'GhostAgentManager.rb'
require 'TelemetryHandler.rb'

class Test < RubyAgentBase

  TriggerFilter = [
    "calcSpeed"
  ]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)

    props = getSimulator().getProperties()
    @personalSpace = props.getDouble("personalSpace",2.0 * 0.522)
    @physicalSpace = props.getDouble("physicalSpace",0.5)
    @widthUnit_OtherLane = props.getDouble("widthUnit_OtherLane",0.9)
    @widthUnit_SameLane = props.getDouble("widthUnit_SameLane",0.9)
    @physicalThreshold = props.getDouble("physicalThreshold",0.9)
    @insDist = props.getDouble("insensitiveDistanceInCounterFlow",@personalSpace*0.5)
    @a0 = props.getDouble("a0",0.962)
    @a1 = props.getDouble("a1",0.8497467021796484659)
    @a2 = props.getDouble("a2",4.682)
    @body_drag_coefficient = props.getDouble("bodyDrag", 300.0)
    @crush_threshold = props.getDouble("crushThreshold", 3000.0)



    mass_term = ItkTerm.getArg(@fallback, "mass")
    @my_mass = mass_term ? mass_term.getDouble() : 60.0 # Default to 60kg
    PhysicsBlackboard.instance.log_mass(getAgentId(),@my_mass)
    space_term = ItkTerm.getArg(@fallback, "physicalSpace")
    @physicalSpace = space_term ? space_term.getDouble() : 0.5 
    @my_resistance = @my_mass * 9.8 * 0.5
    @last_crush_pressure = 0.0
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
    
    telemetry_data = {
      pressure: @last_crush_pressure,
      speed: _speed
    }
    TelemetryHandler.update_telemetry(@javaAgent, telemetry_data)

    return _speed
  end

  def calcSpeedBody(previousSpeed,currentTime)
    baseSpeed = @javaAgent.currentPlace.getLink().calcEmptySpeedForAgent(getEmptySpeed(), @javaAgent, currentTime)
    agentID = getAgentId()

    accel = calcAccel(baseSpeed, previousSpeed, currentTime)
    PhysicsBlackboard.instance.log_accel(agentID, accel)

    deltaSpeed = accel * currentTime.getTickUnit()
    _speed = previousSpeed + deltaSpeed

    if _speed > baseSpeed
      _speed = baseSpeed
    elsif _speed < 0
      distanceFromStart = @javaAgent.currentPlace.getAdvancingDistance()
      linkID = getCurrentLinkId()
      wantedBackDist = _speed * currentTime.getTickUnit()

      # 1. Find the closest agent directly behind us
      sameLane = @javaAgent.currentPlace.getLane()
      closest_agent_behind = nil
      min_dist_behind = Float::INFINITY

      sameLane.each do |other_agent|
        # Ignore normal ghosts, but acknowledge crushed bodies
        if other_agent.isGhost()
          next unless other_agent.hasTag("crushed")
        end
        # Don't check against ourselves
        next if other_agent.getID() == agentID

        other_pos = other_agent.currentPlace.getAdvancingDistance()
        
        # Check if they are strictly behind us
        if other_pos < distanceFromStart
          dist_behind = distanceFromStart - other_pos
          if dist_behind < min_dist_behind
            min_dist_behind = dist_behind
            closest_agent_behind = other_agent
          end
        end
      end

      # 2. Calculate our maximum allowed backward travel distance
      availableBackDist = -distanceFromStart # Default limit: start of the link
      limit_from_agent = -Float::INFINITY

      if closest_agent_behind
        # Calculate the exact gap minus our physical space requirement
        gap = min_dist_behind - @physicalSpace
        gap = 0.0 if gap < 0.0 # Clamp to 0 if we are already dangerously overlapping
        
        # Available distance is negative because we are moving backward
        limit_from_agent = -gap
        
        # Take the most restrictive (closest to 0) boundary
        availableBackDist = [availableBackDist, limit_from_agent].max
      end

      # 3. Apply Boundary and Log Collision
      if wantedBackDist < availableBackDist
        # We hit a physical boundary! Clamp speed exactly to the collision point.
        _speed = availableBackDist / currentTime.getTickUnit()
        
        # 4. Blackboard Registration (Did we hit a person, or just the wall?)
        if closest_agent_behind && availableBackDist == limit_from_agent
          # Register the physical shockwave!
          PhysicsBlackboard.instance.register_push(
            agentID, 
            closest_agent_behind.getID(), 
            accel, 
            currentTime
          )
          
          # Optional: log the impact to the console for debugging
          puts "[CRUSH IMPACT] #{agentID} shoved backward into #{closest_agent_behind.getID()}!"
        else
          # Pinned against the start of the link
          puts "back TO start #{agentID} from linkID #{linkID}:#{distanceFromStart}\n\n"
        end
      end
    end

    width = @javaAgent.currentPlace.getLaneWidth()
    indexInLane = @javaAgent.currentPlace.getIndexFromHeadingInLane(@javaAgent)
    
    if indexInLane < width && @javaAgent.currentPlace.getHeadingNode().hasTag(getGoal())
      _speed = baseSpeed
    end

    return _speed
  end
    
  def calcAccel(baseSpeed, previousSpeed, currentTime)
    _accel = @a0 * (baseSpeed - previousSpeed)

    speed_model = @javaAgent.getSpeedModel().to_s

    case speed_model
    when /LaneModel/
      distToPredecessor = @javaAgent.send(:calcDistanceToPredecessor, currentTime)
      _accel += @javaAgent.send(:calcSocialForce, distToPredecessor)

    when /PlainModel/, /CrossingModel/
      lowerBound = -((baseSpeed / currentTime.getTickUnit()) + _accel)
      physicalAgent, socialAgent,totalCrossingForce = search(currentTime)

      physicalForce = calcPhysical(physicalAgent,currentTime)

      socialForce = calcSocial(socialAgent,lowerBound,totalCrossingForce)

      _accel += (physicalForce + socialForce)

      props = getSimulator().getProperties()
      recovering_accel = props.getDouble("accelerationOfRecoveringHeadAgent", 0.0)
      if recovering_accel > 0.0
        if _accel <= 0 && previousSpeed <= 0 && @javaAgent.currentPlace.getIndexFromHeadingInLane(@javaAgent) == 0
          _accel = recovering_accel
        end
      end

    else
      logWithLevel(:error, "SpeedModel", "Unknown Speed Model: #{speed_model}")
    end

    return _accel
  end

  #maybe should use a copy instead of the if-文
  #->using the copy and moving it causes added calculations to the sim. To reduce this only use it to move to the next link
  def search(currentTime)
    if @javaAgent.isGhost()
      return    [[], [], 0.0]
    end
    
    #first instance
    physicalAgent = []
    socialAgent = []
    totalCrossingForce = 0.0

    emptySpeed = getEmptySpeed()
    tickUnit = currentTime.getTickUnit()

    maxSearch = (@personalSpace + emptySpeed) * (tickUnit + 1.0)
    remainingDist = maxSearch
    
    virtualPlace = @javaAgent.currentPlace.duplicate()
    virtualRoute = @javaAgent.routePlan.duplicate()

    startPos = virtualPlace.getAdvancingDistance()

    count = 0
    countOther = 0

    while remainingDist > 0
      currentLink = virtualPlace.getLink()
      linkLength = virtualPlace.getLinkLength()

      availableDistance = linkLength - startPos

      searchDist = startPos + [remainingDist, availableDistance].min
      distanceSoFar = maxSearch - remainingDist

      # --- COUNTER FLOW (Other Lane) ---
      otherLane = virtualPlace.getOtherLane()
      laneWidthOther = virtualPlace.getOtherLaneWidth()
      insensitivePos = 0.0

      (0...otherLane.size()).each do |i|
        agent = otherLane.get(otherLane.size() - i - 1)
        
        # Counterflow agents are moving the opposite direction, so their coordinate is inverted
        agentPos = linkLength - agent.currentPlace.getAdvancingDistance()

        if agentPos > searchDist
          break # Past our search boundary
        elsif agentPos <= startPos - @physicalSpace
          next  # Behind our search start
        elsif agentPos <= insensitivePos
          next  # Too close in counterflow
        else
          countOther += 1
          
          # Exact distance from our actual agent to this target agent
          dx = distanceSoFar + (agentPos - startPos)
          dy = @widthUnit_OtherLane * (((laneWidthOther - (countOther % laneWidthOther)) % laneWidthOther) + 1)

          agent_data = { agent: agent, dx: dx, dy: dy }

          if dx <= @physicalThreshold
            physicalAgent << agent_data 
          else
            socialAgent << agent_data 
          end

          if countOther % laneWidthOther == 0
            insensitivePos = agentPos + @insDist
          end
        end
      end

      # --- FORWARD FLOW (Same Lane) ---
      sameLane = virtualPlace.getLane()
      laneWidth = virtualPlace.getLaneWidth()
      myTurnIsOver = false

      sameLane.each do |agent|
        agentPos = agent.currentPlace.getAdvancingDistance()

        if agent.getID() == getAgentId()
          myTurnIsOver = true
          next
        elsif agentPos > searchDist
          break # Past our search boundary
        elsif agentPos < startPos
          next  # Behind our search start
        elsif agentPos == startPos && myTurnIsOver
          next  
        else
          count += 1
          
          # Exact distance from our actual agent to this target agent
          dx = distanceSoFar + (agentPos - startPos)
          dy = (@widthUnit_SameLane * ((laneWidth - (count % laneWidth)) % laneWidth))

          
          agent_data = { agent: agent, dx: dx, dy: dy }

          if dx <= @physicalThreshold
            physicalAgent << agent_data 
          else
            socialAgent << agent_data 
          end
        end
      end

      remainingDist -= availableDistance
      
      break if remainingDist <= 0

      nextLink = @javaAgent.send(:chooseNextLinkBody, currentTime, virtualPlace, virtualRoute, true)
      break if nextLink.nil?

      speed_model = @javaAgent.getSpeedModel().to_s
      if speed_model.include?("CrossingModel")
        heading_node = virtualPlace.getHeadingNode()
        distPastNode = -(distanceSoFar + availableDistance)
        crossingForce = @javaAgent.send(:calcNodeCrossingForce, currentTime, virtualPlace.getLink(), nextLink, heading_node, distPastNode)
        totalCrossingForce += crossingForce
      end

      virtualPlace.transitTo(nextLink)
      
      startPos = 0.0
    end

    return  physicalAgent,socialAgent,totalCrossingForce
  end

  def calcPhysical(physicalAgent, currentTime)
    return 0.0 if physicalAgent.empty?

    
    raw_net_force_x = 0.0
    raw_crush_pressure = 0.0
    total_body_drag = 0.0

    physical_space_limit = @physicalSpace 

    physicalAgent.each do |data|
      other_agent = data[:agent]
      dx = data[:dx]
      dy = data[:dy]
      
      # Euclidean distance
      distance = Math.sqrt(dx**2 + dy**2)
      next if distance == 0.0 

      # ---------------------------------------------------------
      # 1. FRICTION FROM CRUSHED BODIES
      # ---------------------------------------------------------
      if other_agent.hasTag("crushed")
        if distance <= physical_space_limit
           total_body_drag += @body_drag_coefficient
        end
        next 
      end

      # ---------------------------------------------------------
      # 2. LIVING AGENTS (Reactive & Proactive Forces)
      # ---------------------------------------------------------
      other_mass = PhysicsBlackboard.instance.get_mass(other_agent.getID())
      dir_x = -(dx / distance) 
      incoming_force_mag = 0.0

      # Blackboard Check: Reactive Force
      if has_blackboard_hit?(other_agent.getID(), @javaAgent.getID(), currentTime)
        incoming_accel = get_blackboard_hit_accel(other_agent.getID(), @javaAgent.getID(), currentTime)
        
        # Math for if being pushed into
        incoming_force_mag = other_mass * incoming_accel.abs
      else
        other_accel = PhysicsBlackboard.instance.get_accel(other_agent.getID())
        if other_accel > 0 && dx < 0
          dot_product = other_accel * dir_x
          if dot_product > 0
            # Math for other times
            incoming_force_mag = other_mass * dot_product
          end
        end
      end

      # Accumulate RAW Vectors and Scalars
      if incoming_force_mag > 0.0
        raw_net_force_x += incoming_force_mag * dir_x
        raw_crush_pressure += incoming_force_mag
      end
    end

    # ---------------------------------------------------------
    # 3. APPLY RESISTANCE TO THE GRAND TOTAL
    # ---------------------------------------------------------
    # calc to see if agent is crushed
    final_crush_pressure = [0.0, raw_crush_pressure - @my_resistance].max
    
    @last_crush_pressure = final_crush_pressure

    # calc to see how much the agent gets pushed
    if raw_net_force_x > 0
      net_force_x = [0.0, raw_net_force_x - @my_resistance].max
    elsif raw_net_force_x < 0
      net_force_x = [0.0, raw_net_force_x + @my_resistance].min
    else
      net_force_x = 0.0
    end

    # ---------------------------------------------------------
    # 4. APPLY BODY DRAG (Friction)
    # ---------------------------------------------------------
    #calc the added force from crushed agents
    if total_body_drag > 0.0
      if net_force_x > 0
        net_force_x = [0.0, net_force_x - total_body_drag].max
      elsif net_force_x < 0
        net_force_x = [0.0, net_force_x + total_body_drag].min
      end
    end

    # ---------------------------------------------------------
    # 5. RESOLVE STATE
    # ---------------------------------------------------------
    # Optional debug print to monitor exactly how much force is getting through
    if final_crush_pressure > 0
      puts "Agent #{@javaAgent.getID()} is feeling #{final_crush_pressure}N of combined crush pressure!"
    end

    #check with the threshold to see if crushed
    if final_crush_pressure > @crush_threshold
      self.crush_agent!
      return 0.0 
    else
      #F = m*a => a = F/m
      return net_force_x / @my_mass
    end
  end

  def calcSocial(socialAgent,lowerBound,totalCrossingForce)
    totalCrossingForce = totalCrossingForce || 0.0

    # Fetch agent-specific SFM parameters from the Java core
    empty_speed = getEmptySpeed()
    
    # Fetch properties (matching your initialize method)
    props = getSimulator().getProperties()
    personal_space = props.getDouble("@personalSpace", 2.0 * 0.522)

    socialAgent.each do |data|
      dx = data[:dx]
      dy = data[:dy]

      # ---------------------------------------------------------
      # 1. DIRECTIONAL FILTERING
      # ---------------------------------------------------------
      # In standard 1D/Lane routing, social psychological repulsion 
      # only applies to agents in front of you. Agents behind you (dx < 0) 
      # are responsible for avoiding you, so they don't apply a forward force.
      next if dx <= 0.0 

      # Calculate true Euclidean distance 
      dist = Math.sqrt(dx**2 + dy**2)

      # ---------------------------------------------------------
      # 2. SFM REPULSION MATH
      # ---------------------------------------------------------
      # This matches the Java core logic. It always yields a negative 
      # value, pushing the agent's acceleration backwards (deceleration).
      repulsion = -empty_speed * @a1 * Math.exp(@a2 * (personal_space - dist))
      
      totalCrossingForce += repulsion

      # ---------------------------------------------------------
      # 3. LOWER BOUND OPTIMIZATION (Early Exit)
      # ---------------------------------------------------------
      # If the total repulsion exceeds our maximum physical braking capacity,
      # we can stop checking further agents to save CPU.
      if totalCrossingForce <= lowerBound
        totalCrossingForce = lowerBound # Clamp to exactly the threshold
        break
      end
    end

    return totalCrossingForce
  end

  def crush_agent!
    return true if @is_crushed

    @is_crushed = true

    pos = @javaAgent.getPosition()
    # mark agent
    @javaAgent.addTag("crushed")
    # correct ghost activation
    enable_ghost_mode

    # log
    $stdout.puts "CRUSHED | id=#{@javaAgent.getID()} | x=#{pos.getX()} | y=#{pos.getY()}"
    $stdout.flush

    # store body
    GhostAgentManager.add_body(pos.getX(), pos.getY())

    return true
  end

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

  # --- Blackboard Integration ---
  
  def has_blackboard_hit?(aggressor_id, victim_id,currentTime)
   PhysicsBlackboard.instance.has_hit?(aggressor_id, victim_id, currentTime)
  end

  def get_blackboard_hit_accel(aggressor_id, victim_id,currentTime)
    PhysicsBlackboard.instance.get_hit_accel(aggressor_id, victim_id, currentTime)
  end

end