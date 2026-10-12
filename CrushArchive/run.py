#Note
#DynamicAgentLogger inputs Current Time, 
#The agents Generated Time, 
#and the agents ID
#automatically 
#Rework TelemetryHandler.rb for different log
import json
import subprocess
import pandas as pd
import matplotlib.pyplot as plt
import os

# --- CONFIGURATION ---
CROWDWALK_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "/home/wood/CrowdWalk/crowdwalk"))
PROPERTIES_PATH = "./CrushTest/prop.json"
BASE_LOG_DIR = "/mnt/ssd_2tb/log0518"
QUICKSTART_PATH = os.path.join(CROWDWALK_ROOT, "quickstart.sh")

# Testing how different physical sizes (radii) affect crushing
TEST_RADII = [0.3, 0.35, 0.4, 0.45, 0.5]

def update_properties():
    with open(PROPERTIES_PATH, 'r') as f:
        data = json.load(f)
    
    #Update the Ruby-linked parameter
    # data["body_radius"] = radius 
    # data["pressure_threshold"] = 10.0
    # data["recovery_rate"]
    # data["compression_gain"]
    # data["personal_space_radius"]
    # data["firmness"]
    # data["strength"]
    # data["sharpness"]
    # data["influence_dist"]
    #data[""]
    #data[""]

    #Dynamic filename for the SSD
    log_file = f"{BASE_LOG_DIR}/slowdownTest.csv"
    data["dynamic_logging"]["file"] = log_file
    
    with open(PROPERTIES_PATH, 'w') as f:
        json.dump(data, f, indent=4)
    return log_file

def run_simulation():
    # Execute with your quickstart command
    subprocess.run(["sh", "quickstart.sh", PROPERTIES_PATH, "-c"], check=True)

if __name__ == "__main__":
    results = []
    for r in TEST_RADII:
        #print(f"\n>>> TESTING AGENT RADIUS: {r}")

        #get log name and update prop here!!!
        csv_path = update_properties()
        run_simulation()
        
        #from here is math for comparison 
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)

            idx_max = df['p_acc'].idxmax()
            peak_p = df.loc[idx_max, 'p_acc']
            peak_time = df.loc[idx_max, 'current_traveling_period']
            avg_comp = df['compression'].mean()

            results.append({
                "radius": r,
                "peak_pressure": df['p_acc'].max(),
                "timestamp_of_peak":peak_time,
                "avg_compression": df['compression'].mean()
            })

    # Summary and Comparison Graph
    summary_df = pd.DataFrame(results)
    summary_df.to_csv(f"{BASE_LOG_DIR}/radius_experiment_summary.csv", index=False)
    
    # --- PLOTTING ---
    plt.figure(figsize=(10, 6))
    
    # Plotting the data
    plt.plot(summary_df['radius'], summary_df['peak_pressure'], 
             marker='o', color='blue', linestyle='-', linewidth=2)
    

    for i, row in summary_df.iterrows():
        plt.annotate(
            f"{int(row['timestamp_of_peak'])}s", # Text to display (rounded to int)
            (row['radius'], row['peak_pressure']), # Point to anchor to
            textcoords="offset points", # How to interpret the next argument
            xytext=(0, 12),             # Move text 12 points vertically above marker
            ha='center',                # Center the text horizontally
            fontsize=9,
            fontweight='bold',
            color='darkblue'
        )

    # FORCE X-AXIS TICKS: Only show the values in your test list
    plt.xticks(TEST_RADII) 
    
    plt.xlabel('Agent Body Radius (m)')
    plt.ylabel('Peak Pressure Observed (p_acc)')
    plt.title('Effect of Physical Agent Size on Crowd Stress')
    
    plt.margins(y=0.15)

    # Optional: Add a grid that aligns exactly with your measurements
    plt.grid(True, which='both', linestyle='--', alpha=0.5)
    
    plt.savefig(f"{BASE_LOG_DIR}/radius_vs_pressure.png")
    print(f"finish")
