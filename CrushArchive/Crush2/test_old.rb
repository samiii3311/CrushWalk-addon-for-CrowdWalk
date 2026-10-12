require 'RubyAgentBase.rb'
require 'matrix' # Used for 2D vector math

class PhysicalAgent < RubyAgentBase
  # Density threshold to trigger physical contact calculations
  CRITICAL_DENSITY = 2.0 
  
  # Constants from the paper
  AGENT_MASS = 70.0               # mi, mj (kg)
  AGENT_RADIUS = 0.2              # ri, rj (m)
  RESTITUTION_COEFF = 0.8         # e
  K_PRIME = 20000.0               # k' (Normal pushing coefficient)
  KAPPA_PRIME = 20000.0           # kappa' (Tangential friction coefficient)
  TIME_STEP = 1.0                 # Simulation dt (adjust based on your environment)

  TriggerFilter = [
    "calcSpeed"
  ]

  def initialize(agent, config, fallback)
      super(agent, config, fallback)
  end

  def calcSpeed(previousSpeed)
    current_link = self.getCurrentLink()
    density = calculate_link_density(current_link)

    if density >= CRITICAL_DENSITY
      # --- HIGH DENSITY: APPLY WANG ET AL. PHYSICAL CONTACT MODEL ---
      current_velocity = Vector[self.getVelocity().getX(), self.getVelocity().getY()]
      current_pos = Vector[self.getPos().getX(), self.getPos().getY()]
      
      # Calculate new velocity based on physical interactions
      new_velocity_vector = calculate_contact_velocity(current_pos, current_velocity)
      
      # Convert back to CrowdWalk's expected return format (assuming Vector3D or scalar speed)
      return new_velocity_vector.magnitude # Or return the vector depending on your CrowdWalk build
    else
      # --- LOW DENSITY: DEFAULT BEHAVIOR ---
      return super(previousSpeed)
    end
  end

  def calculate_contact_velocity(pos_i, v_i)
    total_pushing_force = Vector[0.0, 0.0]
    total_collision_impulse = Vector[0.0, 0.0]

    # Retrieve nearby agents (pseudo-code: replace with your exact CrowdWalk method)
    nearby_agents = get_nearby_agents(self, 1.0) 

    nearby_agents.each do |other_agent|
      pos_j = Vector[other_agent.getPos().getX(), other_agent.getPos().getY()]
      v_j = Vector[other_agent.getVelocity().getX(), other_agent.getVelocity().getY()]
      
      # Vector from j to i
      p_diff = pos_i - pos_j
      distance = p_diff.magnitude
      
      next if distance == 0 || distance > (AGENT_RADIUS * 2)

      normal_vec = p_diff / distance
      tangent_vec = Vector[-normal_vec[1], normal_vec[0]]
      v_diff = v_j - v_i

      # 1. COLLISION IMPULSE (Passive Collision)
      # Condition: Tendency to get close in normal direction (v_i - v_j) * (p_i - p_j) < 0
      if (v_i - v_j).inner_product(p_diff) < 0
        v_n_diff = v_diff.inner_product(normal_vec)
        
        # Delta L_ij = [ (mi * mj) / (mi + mj) ] * (1 + e) * Delta v^n * n_ij
        mass_factor = (AGENT_MASS * AGENT_MASS) / (AGENT_MASS + AGENT_MASS)
        impulse_magnitude = mass_factor * (1.0 + RESTITUTION_COEFF) * v_n_diff
        total_collision_impulse += normal_vec * impulse_magnitude
      end

      # 2. PUSHING FORCE (Active Pushing)
      # Condition: Significant contact where distance < r_i + r_j
      overlap = (AGENT_RADIUS * 2) - distance
      if overlap > 0
        # Normal repulsive force: k' * overlap * n_ij
        normal_force = normal_vec * (K_PRIME * overlap)
        
        # Tangential friction: kappa' * overlap * Delta v^t * t_ij
        v_t_diff = v_diff.inner_product(tangent_vec)
        tangential_force = tangent_vec * (KAPPA_PRIME * overlap * v_t_diff)
        
        total_pushing_force += (normal_force + tangential_force)
      end
    end

    # Integrate forces and impulses into velocity
    # dv = (Sum F / m) * dt + (Sum Delta L / m)
    dv_force = (total_pushing_force / AGENT_MASS) * TIME_STEP
    dv_impulse = (total_collision_impulse / AGENT_MASS)

    return v_i + dv_force + dv_impulse
  end

  def calculate_link_density(link)
    # Your density calculation logic here
    return 3.0 # Placeholder
  end
  
  def get_nearby_agents(agent, radius)
    # Helper to interface with CrowdWalk's spatial hashing to return an array of nearby agent objects
    # This avoids O(N^2) checks across the whole simulation.
    return [] 
  end
end