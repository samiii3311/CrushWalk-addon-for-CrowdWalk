import pandas as pd
import matplotlib.pyplot as plt

# 1. Load the simulation output data
csv_file = "/mnt/ssd_2tb/log0518/slowdownTest.csv"

try:
    df = pd.read_csv(csv_file)
except FileNotFoundError:
    print(f"Error: Could not find '{csv_file}'.")
    exit()

# 2. Calculate the average speed of all agents at each simulation step
# We group by the time step and compute the mean of 'current_speed'
avg_speed_per_step = (
    df.groupby('current_traveling_period')['current_speed']
    .mean()
    .reset_index()
)

# Sort by time to ensure the line plots chronologically
avg_speed_per_step = avg_speed_per_step.sort_values(by='current_traveling_period')

# 3. Set up the plotting environment
plt.figure(figsize=(10, 6))

# Plot the aggregate average line
plt.plot(
    avg_speed_per_step['current_traveling_period'], 
    avg_speed_per_step['current_speed'], 
    label="All Agents (Average)", 
    color="crimson",  # Distinct color for an aggregate line
    linewidth=2.5
)

# 4. Styling the graph for your presentation
plt.title("Average Pedestrian Velocity Profile Across All Agents", fontsize=14, fontweight='bold')
plt.xlabel("Simulation Steps (Ticks)", fontsize=12)
plt.ylabel("Average Velocity (m/s)", fontsize=12)
plt.grid(True, linestyle='--', alpha=0.6)
plt.legend(fontsize=11, loc='upper right')
plt.tight_layout()

plt.show()