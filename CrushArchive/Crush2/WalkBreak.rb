require 'RubyAgentBase.rb'
require 'GhostAgentManager.rb'
require 'TelemetryHandler.rb'
require 'clacModu.rb'

# Inherits from base directly. We let Java handle living-agent collision avoidance.
class WalkBreak < RubyAgentBase
  TriggerFilter = ["update"]

  def initialize(agent, config, fallback)
    super(agent, config, fallback)

    @is_crushed = false
    @is_on_first_link = true

    # DYNAMIC PARAMETERS
    props = getSimulator().getProperties()
  
    @personal_space = props.getDouble("personalSpace", 1.02265769054586)
    @empty_speed = props.getDouble("emptySpeed", 2.0 * 0.522)
    @width_unit_other_lane = props.getDouble("widthUnit_OtherLane", 0.9)
    @width_unit_same_lane = props.getDouble("widthUnit_SameLane", 0.9)
    @insensitive_distance = props.getDouble("insensitiveDistanceInCounterFlow", @personal_space*0.5)
  end

  # ============================================================
  def update
    
  end

  def create_working_place
    # 1. Fetch the Java Place object
    current_place = get_current_place()
    
    # 2. Call the Java duplicate() method directly via JRuby
    working_place = current_place.duplicate()
    
    return working_place
  end

  def accumulate_social_forces(current_time, lower_bound)
    return 0.0 if ghost?

    total_force = 0.0
    max_distance = (@personal_space + @empty_speed) * (current_time.tick_unit + 1.0)

    working_place = create_working_place()
    working_route_plan = @javaAgent.getRoutePlan().duplicate()
    relative_pos = working_place.advancing_distance

    relative_pos = working_place.getAdvancingDistance(max_distance)

    count = 0
    count_other = 0

    while working_place.advancing_distance > 0
      break if total_force < lower_bound

      # Call the external module inside the block!
      count_other = search_other_lane(working_place, relative_pos, count_other) do |dx, dy|
        total_force += SocialForceCalculator.calculate_directional_force(dx, dy)
        total_force >= lower_bound 
      end

      break if total_force < lower_bound

      # Call the external module inside the block!
      count = search_same_lane(working_place, relative_pos, count) do |dx, dy|
        total_force += SocialForceCalculator.calculate_directional_force(dx, dy)
        total_force >= lower_bound 
      end

      relative_pos -= working_place.link_length
      next_link = choose_next_link_body(current_time, working_place, working_route_plan, true)
      
      break if next_link.nil?

      if speed_model == SpeedCalculationModel::CROSSING_MODEL
        total_force += calc_node_crossing_force(
          current_time,
          working_place.link,
          next_link,
          working_place.heading_node,
          -relative_pos
        )
      end

      working_place.transit_to(next_link)
    end

    total_force
  end

  private

  # 4. Search Function: Other Lane (Yields valid dx/dy to the block)
  def search_other_lane(working_place, relative_pos, count_other)
    other_lane = working_place.other_lane
    lane_width_other = working_place.other_lane_width
    link_length = working_place.link_length
    insensitive_pos = 0.0

    # Iterates backwards idiomatically
    other_lane.reverse_each do |agent|
      next if agent.ghost?

      agent_pos = link_length - agent.advancing_distance

      if agent_pos > working_place.advancing_distance
        break # Out of search range
      elsif agent_pos <= relative_pos
        next # Behind the current agent, ignore
      elsif agent_pos <= insensitive_pos
        next # Too close to previous agent (overcrowded condition)
      else
        count_other += 1
        dx = agent_pos - relative_pos
        dy = @width_unit_other_lane * (((lane_width_other - (count_other % lane_width_other)) % lane_width_other) + 1)

        # Yield to calculation; break out of loop if block returns false
        continue_search = yield(dx, dy)

        if count_other % lane_width_other == 0
          insensitive_pos = agent_pos + @insensitive_distance
        end

        break unless continue_search
      end
    end

    count_other # Returns updated count for the next link in the while loop
  end

  # 5. Search Function: Same Lane (Yields valid dx/dy to the block)
  def search_same_lane(working_place, relative_pos, count)
    same_lane = working_place.lane
    lane_width = working_place.lane_width
    my_turn_is_over = false

    same_lane.each do |agent|
      next if agent.ghost?

      agent_pos = agent.advancing_distance

      if agent == self
        my_turn_is_over = true
        next
      elsif agent_pos > working_place.advancing_distance
        break # Out of search range
      elsif agent_pos < relative_pos
        next # Behind the current agent, ignore
      elsif agent_pos == relative_pos && my_turn_is_over
        next # Same position, but sequence is behind (only impacted if leading)
      else
        count += 1
        dx = agent_pos - relative_pos
        dy = @width_unit_same_lane * ((lane_width - (count % lane_width)) % lane_width)

        # Yield to calculation; break out of loop if block returns false
        continue_search = yield(dx, dy)

        break unless continue_search
      end
    end

    count # Returns updated count for the next link in the while loop
  end
end