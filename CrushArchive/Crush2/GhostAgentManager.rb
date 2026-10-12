class GhostAgentManager
  @@bodies = []

  def self.add_body(x, y)
    @@bodies << {x: x, y: y}
  end

  def self.get_bodies
    return @@bodies
  end
end