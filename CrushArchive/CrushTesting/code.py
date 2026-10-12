import json
import os
import subprocess




def launch_crowdwalk(properties_file, crowdwalk_jar="crowdwalk.jar"):

    # Ensure the properties file actually exists before launching
    if not os.path.exists(properties_file):
        raise FileNotFoundError(f"Cannot find properties file at: {properties_file}")

    # The command array sent to the terminal
    command = [
        "java",
        "-jar", crowdwalk_jar,
        properties_file,"-g2"
    ]
    
    print(f"Starting CrowdWalk: {' '.join(command)}")
    
    try:
        # Popen runs the process. PIPE captures the logs so they don't flood your console.
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # This blocks Python and waits for the simulation to finish
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            print("--- SIMULATION FAILED ---")
            print(stderr)
            return False
            
        print("--- SIMULATION COMPLETE ---")
        return True

    except Exception as e:
        print(f"Failed to launch JVM: {e}")
        return False

maps = [
    "1OneWay.xml", 
    "2Turn.xml", 
    "3SmallEnter.xml", 
    "4BigExit.xml", 
    "5BusyPath.xml", 
    "6Crossway2way.xml",
    "7Crossway4way.xml",
]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CROWDWALK_JAR_PATH = os.path.join(SCRIPT_DIR, "../build/libs/crowdwalk.jar")
PROPERTIES_PATH = os.path.join(SCRIPT_DIR, "prop.json")

for current_map in maps:
    print(f"PREPARING SIMULATION: {current_map}")

    with open(PROPERTIES_PATH, "r") as file:
        data = json.load(file)

    data["map_file"] = current_map
    data["individual_pedestrians_log_dir"] = f"/mnt/ssd_2tb/log-jul21/{current_map.replace('.xml', '.csv')}"

    with open(PROPERTIES_PATH, "w") as file:
        json.dump(data, file, indent=1)
    
    print(f" Updated prop.json to use {current_map}")
    print("Running CrowdWalk...")

    # 5. Execute CrowdWalk and wait for it to finish
    success = launch_crowdwalk(PROPERTIES_PATH, CROWDWALK_JAR_PATH)
    
    if success:
        print(f"Finished simulation for {current_map}")
    else:
        print(f"CRITICAL ERROR: CrowdWalk crashed while running {current_map}.")
        print("Stopping the batch process so you can investigate.")
        break # Stops the loop

print("\nALL BATCH SIMULATIONS COMPLETE!")