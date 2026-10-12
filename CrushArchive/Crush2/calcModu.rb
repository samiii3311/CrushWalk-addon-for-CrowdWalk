# social_force_calculator.rb

module calcModu
  # This makes all methods below callable directly on the module,
  # e.g., calcModu.calculate_directional_force(dx, dy)
  module_function

  def calculate_directional_force(dx, dy)
    # Put your actual math logic here! 
    # If calc_social_force_to_heading was just doing math, you can 
    # either rename this method to that, or call it from here.
    calc_social_force_to_heading(dx, dy)
  end

  def calc_social_force_to_heading(dx, dy)
    # (Your actual calculation implementation goes here)
    # Example placeholder:
    # Math.sqrt(dx**2 + dy**2)
  end
end