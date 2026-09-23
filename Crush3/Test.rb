# test.rb
require 'digest'
require 'RubyAgentBase.rb'
require 'PhysicsBlackboard.rb'
require 'GhostAgentManager.rb'
require 'TelemetryHandler.rb'

class Test < RubyAgentBase

  TriggerFilter = [
    "calcSpeed"
  ]

  # ponytail: temporary diagnostic counters for the all-zero compression_pressure/
  # crush_now/queue_count issue. Remove once the chokepoint is identified.
  @@dbg_physical_nonempty = 0
  @@dbg_physical_empty = 0
  @@dbg_register_push = 0
  @@dbg_max_raw_pressure = 0.0
  @@dbg_last_tick = -1

  def initialize(agent, config, fallback)
    super(agent, config, fallback)

    props = getSimulator().getProperties()
    @personalSpace = props.getDouble("personalSpace", 2.0 * 0.522)
    @widthUnit_OtherLane = props.getDouble("widthUnit_OtherLane", 0.9)
    @widthUnit_SameLane = props.getDouble("widthUnit_SameLane", 0.9)
    @physicalThreshold = props.getDouble("physicalThreshold", 0.9)
    @insDist = props.getDouble("insensitiveDistanceInCounterFlow", @personalSpace * 0.5)
    @a0 = props.getDouble("a0", 0.962)
    @a1 = props.getDouble("a1", 0.8497467021796484659)
    @a2 = props.getDouble("a2", 4.682)

    mass_term = ItkTerm.getArg(@fallback, "mass")
    @my_mass = mass_term ? mass_term.getDouble() : 60.0
    PhysicsBlackboard.instance.log_mass(getAgentId(), @my_mass)

    @body_drag_coefficient = props.getDouble("bodyDrag", 0.5 * @my_mass * 9.8)

    @crush_threshold = props.getDouble("crushThreshold", 1112.0)
    # Crushed only after the pressure stays above crushThreshold for crushDuration
    # seconds in a row. Kroll et al. 2017: 1112 N can be fatal when held 4-6 min.
    @crush_duration = props.getDouble("crushDuration", 240.0)
    # Hysteresis: once the timer has started, dips down to crushThreshold - crushMargin
    # still count as sustained, so small force jitter doesn't reset it.
    @crush_margin = props.getDouble("crushMargin", 50.0)
    @time_over_threshold = 0.0

    space_term = ItkTerm.getArg(@fallback, "physicalSpace")
    @physicalSpace = space_term ? space_term.getDouble() : props.getDouble("physicalSpace", 0.5)

    @my_resistance = @my_mass * 9.8 * 0.5
    # Share of an agent's received pressure it passes on to the agent in front (A -> B -> C).
    # 1.0 = full: the front of a queue feels everyone behind it. Below 1 the build-up levels
    # off at (own push) / (1 - pressureTransfer), e.g. ~750 N at 0.9 -- too low to ever crush.
    # ponytail: uncited knob; calibrate against measured crowd forces if a source turns up.
    @pressure_transfer = props.getDouble("pressureTransfer", 1.0)
    @last_raw_pressure = 0.0
    @push_resistance = props.getDouble("pushResistance", @my_mass * 9.8 * 0.05)
    @last_crush_pressure = 0.0
    @last_net_force = 0.0
    @last_social_force = 0.0
    @last_blocked_by = nil
  end

  def init_speed_factor(mean = 1.0, std = 0.2, min_val = 0.6, max_val = 1.5)
    agent_id = @javaAgent.getID().to_s

    # Return from blackboard if already calculated for this unique agent
    existing_factor = PhysicsBlackboard.instance.get_speed_factor(agent_id)
    return existing_factor if existing_factor

    # Trim MD5 hex digest to 8 chars (32-bit int) to prevent massive 128-bit Bignums
    agent_hash = Digest::MD5.hexdigest(agent_id)[0..7].to_i(16)

    u1 = [(agent_hash % 100000) / 100000.0, 0.0001].max
    u2 = ((agent_hash / 100000) % 100000) / 100000.0

    standard_normal = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math::PI * u2)
    factor = [[min_val, mean + (standard_normal * std)].max, max_val].min.round(3)

    # Save permanently in the PhysicsBlackboard registry
    PhysicsBlackboard.instance.set_speed_factor(agent_id, factor)
    PhysicsBlackboard.instance.set_agent_hash(agent_id, agent_hash)

    return factor
  end

  def ensure_fresh_config(agent_id)
    return if @config_checked
    @config_checked = true

    current_signature = "#{agent_id}:#{@javaAgent.generatedTime.getRelativeTime()}"
    stored_signature = @javaAgent.config.respond_to?(:getArgString) ? @javaAgent.config.getArgString("agent_signature") : nil

    if stored_signature != current_signature
      @javaAgent.config = ItkTerm.newTerm()
      @javaAgent.config.setArg("agent_signature", current_signature)
    end
  end

  def calcSpeed(previousSpeed)
    currentTime = getCurrentTime()
    agent_id = @javaAgent.getID().to_s
    ensure_fresh_config(agent_id)
    speed_factor = init_speed_factor()

    dbg_tick = currentTime.getRelativeTime().to_i
    if dbg_tick != @@dbg_last_tick && dbg_tick % 30 == 0
      @@dbg_last_tick = dbg_tick
      $stdout.puts "DEBUG t=#{dbg_tick} physicalNonEmpty=#{@@dbg_physical_nonempty} " \
        "physicalEmpty=#{@@dbg_physical_empty} registerPush=#{@@dbg_register_push} " \
        "maxRawPressure=#{@@dbg_max_raw_pressure.round(2)} myResistance=#{@my_resistance.round(2)}"
      $stdout.flush
    end

    _speed = calcSpeedBody(previousSpeed, currentTime, speed_factor)
    _speed = @javaAgent.currentPlace.getLink().calcRestrictedSpeed(_speed, @javaAgent, currentTime)

    deltaDistance = _speed * currentTime.getTickUnit()

    if @javaAgent.currentPlace.isBeyondLinkWithAdvance(deltaDistance)
      _speed = @javaAgent.currentPlace.getHeadingNode().calcRestrictedSpeed(_speed, @javaAgent, currentTime)
    end

    _speed = @javaAgent.obstructer.calcAffectedSpeed(_speed)

    telemetry_data = {
      pressure: @last_crush_pressure,
      raw_pressure: @last_raw_pressure,
      speed: _speed,
      empty_speed: @desired_empty_speed,
      net_force: @last_net_force,
      social_force: @last_social_force,
      link_id: getCurrentLinkId(),
      position: @javaAgent.getPositionOnLink(),
      link_direction: @javaAgent.isForwardDirection() ? 1 : -1,  # 1 = from->to, -1 = to->from
      blocked_by: @last_blocked_by
    }
    TelemetryHandler.update_telemetry(@javaAgent, telemetry_data, currentTime)

    return _speed
  end

  def calcSpeedBody(previousSpeed, currentTime, speed_factor)
    # Scale default link empty speed by the agent's individual factor
    @desired_empty_speed = getEmptySpeed() * speed_factor
    baseSpeed = @javaAgent.currentPlace.getLink().calcEmptySpeedForAgent(@desired_empty_speed, @javaAgent, currentTime)
    agentID = @javaAgent.getID().to_s

    @last_blocked_by = nil

    accel = calcAccel(baseSpeed, previousSpeed, currentTime)
    PhysicsBlackboard.instance.log_accel(agentID, accel)

    _speed = previousSpeed + (accel * currentTime.getTickUnit())

    if _speed > baseSpeed
      _speed = baseSpeed
    elsif _speed < 0
      distanceFromStart = @javaAgent.currentPlace.getAdvancingDistance()
      wantedBackDist = _speed * currentTime.getTickUnit()

      closest_agent_behind = nil
      min_dist_behind = Float::INFINITY

      @javaAgent.currentPlace.getLane().each do |other_agent|
        next if other_agent.isGhost() && !other_agent.hasTag("crushed")
        next if other_agent.getID().to_s == agentID

        other_pos = other_agent.currentPlace.getAdvancingDistance()
        next unless other_pos < distanceFromStart

        dist_behind = distanceFromStart - other_pos
        if dist_behind < min_dist_behind
          min_dist_behind = dist_behind
          closest_agent_behind = other_agent
        end
      end

      availableBackDist = -distanceFromStart
      limit_from_agent = -Float::INFINITY

      if closest_agent_behind
        limit_from_agent = -[min_dist_behind - @physicalSpace, 0.0].max
        availableBackDist = [availableBackDist, limit_from_agent].max
      end

      if wantedBackDist < availableBackDist
        _speed = availableBackDist / currentTime.getTickUnit()

        if closest_agent_behind && availableBackDist == limit_from_agent
          @@dbg_register_push += 1
          PhysicsBlackboard.instance.register_push(
            agentID,
            closest_agent_behind.getID(),
            accel,
            currentTime
          )
          @last_blocked_by = closest_agent_behind.getID()
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
    # Drive = how hard this agent pushes forward (wants to walk faster than it can). Its net
    # accel is usually negative in a jam (the agent ahead brakes it), so that can't be the push.
    PhysicsBlackboard.instance.log_drive(getAgentId(), @my_mass * [_accel, 0.0].max)

    speed_model = @javaAgent.getSpeedModel().to_s

    case speed_model
    when /LaneModel/
      distToPredecessor = @javaAgent.send(:calcDistanceToPredecessor, currentTime)
      _accel += @javaAgent.send(:calcSocialForce, distToPredecessor)

    when /PlainModel/, /CrossingModel/
      lowerBound = -((baseSpeed / currentTime.getTickUnit()) + _accel)
      physicalAgent, socialAgent, totalCrossingForce = search(currentTime)

      physicalForce = calcPhysical(physicalAgent, currentTime)

      socialForce = calcSocial(socialAgent, lowerBound, totalCrossingForce)
      @last_social_force = socialForce

      _accel += (physicalForce + socialForce)

      recovering_accel = getSimulator().getProperties().getDouble("accelerationOfRecoveringHeadAgent", 0.0)
      if recovering_accel > 0.0 && _accel <= 0 && previousSpeed <= 0 &&
         @javaAgent.currentPlace.getIndexFromHeadingInLane(@javaAgent) == 0
        _accel = recovering_accel
      end

    else
      logWithLevel(:error, "SpeedModel", "Unknown Speed Model: #{speed_model}")
    end

    return _accel
  end

  #maybe should use a copy instead of the if-文
  #->using the copy and moving it causes added calculations to the sim. To reduce this only use it to move to the next link
  def search(currentTime)
    return [[], [], 0.0] if @javaAgent.isGhost()

    #first instance
    physicalAgent = []
    socialAgent = []
    totalCrossingForce = 0.0

    tickUnit = currentTime.getTickUnit()

    maxSearch = (@personalSpace + @desired_empty_speed) * (tickUnit + 1.0)
    remainingDist = maxSearch

    virtualPlace = @javaAgent.currentPlace.duplicate()
    virtualRoute = @javaAgent.routePlan.duplicate()

    startPos = virtualPlace.getAdvancingDistance()

    count = 0
    countOther = 0

    while remainingDist > 0
      linkLength = virtualPlace.getLinkLength()

      availableDistance = linkLength - startPos

      searchDist = startPos + [remainingDist, availableDistance].min
      distanceSoFar = maxSearch - remainingDist

      # --- COUNTER FLOW (Other Lane) ---
      otherLane = virtualPlace.getOtherLane()
      laneWidthOther = virtualPlace.getOtherLaneWidth()
      insensitivePos = 0.0

      (otherLane.size() - 1).downto(0) do |i|
        agent = otherLane.get(i)

        # Counterflow agents are moving the opposite direction, so their coordinate is inverted
        agentPos = linkLength - agent.currentPlace.getAdvancingDistance()

        break if agentPos > searchDist                    # Past our search boundary
        next if agentPos <= startPos - @physicalSpace      # Behind our search start
        next if agentPos <= insensitivePos                 # Too close in counterflow

        countOther += 1

        # Exact distance from our actual agent to this target agent
        dx = distanceSoFar + (agentPos - startPos)
        dy = @widthUnit_OtherLane * (((laneWidthOther - (countOther % laneWidthOther)) % laneWidthOther) + 1)

        bucket = (dx <= @physicalThreshold) ? physicalAgent : socialAgent
        bucket << { agent: agent, dx: dx, dy: dy }

        insensitivePos = agentPos + @insDist if countOther % laneWidthOther == 0
      end

      # --- FORWARD FLOW (Same Lane) ---
      laneWidth = virtualPlace.getLaneWidth()
      myTurnIsOver = false

      virtualPlace.getLane().each do |agent|
        agentPos = agent.currentPlace.getAdvancingDistance()

        if agent.getID() == getAgentId()
          myTurnIsOver = true
          next
        end

        break if agentPos > searchDist                 # Past our search boundary
        if agentPos < startPos                         # Behind us
          # People directly behind push us forward: keep them as physical contacts (dx < 0,
          # directly behind -> dy = 0). Current link only; crushed bodies behind don't push.
          # ponytail: agents behind on the previous link are not seen; add if merges need it.
          if distanceSoFar == 0.0 && startPos - agentPos <= @physicalThreshold && !agent.isGhost()
            # share: a lane holds a whole row of people side by side (laneWidth of them), and one
            # agent's push is spread over the row it pushes on -- counting it in full for each
            # agent in front would multiply pressure by laneWidth every row (-> infinity).
            physicalAgent << { agent: agent, dx: agentPos - startPos, dy: 0.0, behind: true,
                               share: 1.0 / [laneWidth, 1].max }
          end
          next
        end
        next if agentPos == startPos && myTurnIsOver

        count += 1

        # Exact distance from our actual agent to this target agent
        dx = distanceSoFar + (agentPos - startPos)
        dy = @widthUnit_SameLane * ((laneWidth - (count % laneWidth)) % laneWidth)

        bucket = (dx <= @physicalThreshold) ? physicalAgent : socialAgent
        bucket << { agent: agent, dx: dx, dy: dy }
      end

      remainingDist -= availableDistance
      break if remainingDist <= 0

      nextLink = @javaAgent.send(:chooseNextLinkBody, currentTime, virtualPlace, virtualRoute, true)
      break if nextLink.nil?

      if @javaAgent.getSpeedModel().to_s.include?("CrossingModel")
        distPastNode = -(distanceSoFar + availableDistance)
        totalCrossingForce += @javaAgent.send(:calcNodeCrossingForce, currentTime,
                                              virtualPlace.getLink(), nextLink,
                                              virtualPlace.getHeadingNode(), distPastNode)
      end

      virtualPlace.transitTo(nextLink)

      startPos = 0.0
    end

    return [physicalAgent, socialAgent, totalCrossingForce]
  end

  def calcPhysical(physicalAgent, currentTime)
    if physicalAgent.empty?
      @@dbg_physical_empty += 1
      @last_crush_pressure = 0.0
      @last_raw_pressure = 0.0
      PhysicsBlackboard.instance.log_pressure(getAgentId(), 0.0)
      @time_over_threshold = 0.0  # no contact -> pressure released
      return 0.0
    end
    @@dbg_physical_nonempty += 1

    raw_net_force_x = 0.0
    raw_crush_pressure = 0.0
    total_body_drag = 0.0

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
        total_body_drag += @body_drag_coefficient if distance <= @physicalSpace
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
      elsif data[:behind]
        # Same-lane agent behind pushing us forward (not counterflow, which walks the other way): its own drive plus the pressure it receives from
        # the agents behind it, passed on (A -> B -> C). Uses the value the agent behind
        # logged last (this tick if it already updated, else last tick).
        # ponytail: one-tick lag per person when the update order is back-to-front; fine at
        # 1 s ticks, revisit if tick length changes.
        push = PhysicsBlackboard.instance.get_drive(other_agent.getID()) +
               @pressure_transfer * PhysicsBlackboard.instance.get_pressure(other_agent.getID())
        incoming_force_mag = push * data[:share] * dir_x if dir_x > 0
      end

      # Accumulate RAW Vectors and Scalars
      if incoming_force_mag > 0.0
        raw_net_force_x += incoming_force_mag * dir_x
        raw_crush_pressure += incoming_force_mag
      end
    end

    @@dbg_max_raw_pressure = raw_crush_pressure if raw_crush_pressure > @@dbg_max_raw_pressure
    @last_raw_pressure = raw_crush_pressure
    PhysicsBlackboard.instance.log_pressure(getAgentId(), raw_crush_pressure)  # passed on to the agent in front

    # ---------------------------------------------------------
    # 3. CRUSH PRESSURE — uses the HIGH threshold (injury-relevant)
    # ---------------------------------------------------------
    final_crush_pressure = [0.0, raw_crush_pressure - @my_resistance].max
    @last_crush_pressure = final_crush_pressure

    # ---------------------------------------------------------
    # 3b. MOMENTUM TRANSFER — uses the LOW threshold (physical push)
    # ---------------------------------------------------------
    if raw_net_force_x > 0
      net_force_x = [0.0, raw_net_force_x - @push_resistance].max
    elsif raw_net_force_x < 0
      net_force_x = [0.0, raw_net_force_x + @push_resistance].min
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
    #check with the threshold to see if crushed -- must be sustained, resets when pressure drops
    @last_net_force = net_force_x
    active_threshold = @time_over_threshold > 0 ? @crush_threshold - @crush_margin : @crush_threshold
    if final_crush_pressure > active_threshold
      @time_over_threshold += currentTime.getTickUnit()
    else
      @time_over_threshold = 0.0
    end
    if @time_over_threshold >= @crush_duration
      crush_agent!
      @last_net_force = 0.0
      return 0.0
    end

    #F = m*a => a = F/m
    return net_force_x / @my_mass
  end

  def calcSocial(socialAgent, lowerBound, totalCrossingForce)
    totalCrossingForce ||= 0.0

    # Individualized desired speed (getEmptySpeed() * this agent's speed_factor)
    empty_speed = @desired_empty_speed

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
      totalCrossingForce += -empty_speed * @a1 * Math.exp(@a2 * (@personalSpace - dist))

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

    @javaAgent.setGhost(true) if @javaAgent.respond_to?(:setGhost)
    @javaAgent.setSpeed(0.0) if @javaAgent.respond_to?(:setSpeed)
  end

  # --- Blackboard Integration ---

  def has_blackboard_hit?(aggressor_id, victim_id, currentTime)
    PhysicsBlackboard.instance.has_hit?(aggressor_id, victim_id, currentTime)
  end

  def get_blackboard_hit_accel(aggressor_id, victim_id, currentTime)
    PhysicsBlackboard.instance.get_hit_accel(aggressor_id, victim_id, currentTime)
  end

end
