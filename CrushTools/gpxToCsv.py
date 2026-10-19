import pandas as pd
import xml.etree.ElementTree as ET
from datetime import datetime
import os
import tkinter as tk
from tkinter import filedialog

def select_file():
    """Opens a graphical file selection window."""
    root = tk.Tk()
    root.withdraw()  # Hide the tiny main tkinter window
    root.attributes("-topmost", True)  # Bring the file picker to the front
    
    file_path = filedialog.askopenfilename(
        title="Select GPX file for Crowd Flow Data",
        filetypes=[("GPX files", "*.gpx"), ("All files", "*.*")]
    )
    root.destroy()
    return file_path

def convert_gpx_to_csv(gpx_input_path):
    if not gpx_input_path:
        print("No file selected. Operation cancelled.")
        return

    # Create output filename based on input (e.g., walk1.gpx -> walk1.csv)
    csv_output_path = os.path.splitext(gpx_input_path)[0] + ".csv"

    try:
        tree = ET.parse(gpx_input_path)
        root = tree.getroot()
    except Exception as e:
        print(f"Error parsing GPX: {e}")
        return

    # GPX Namespace handling
    ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
    data_list = []
    track_fid, track_seg_id, point_id = 0, 0, 0

    # Extracting points from the XML
    for trk in root.findall('.//gpx:trk', ns):
        for seg in trk.findall('gpx:trkseg', ns):
            for pt in seg.findall('gpx:trkpt', ns):
                lat = pt.get('lat')
                lon = pt.get('lon')
                ele = pt.find('gpx:ele', ns).text if pt.find('gpx:ele', ns) is not None else ""
                
                # Format time for CrowdWalk compatibility
                raw_time = pt.find('gpx:time', ns).text if pt.find('gpx:time', ns) is not None else ""
                formatted_time = ""
                if raw_time:
                    try:
                        dt = datetime.strptime(raw_time.replace('Z', ''), "%Y-%m-%dT%H:%M:%S")
                        formatted_time = dt.strftime("%Y/%m/%d %H:%M:%S+00")
                    except:
                        formatted_time = raw_time

                # Match the track_points.csv structure
                row = {
                    'X': lon, 'Y': lat, 'track_fid': track_fid, 'track_seg_id': track_seg_id,
                    'track_seg_point_id': point_id, 'ele': ele, 'time': formatted_time,
                    'magvar': '', 'geoidheight': '', 'name': '', 'cmt': '', 'desc': '', 
                    'src': '', 'link1_href': '', 'link1_text': '', 'link1_type': '', 
                    'link2_href': '', 'link2_text': '', 'link2_type': '', 'sym': '', 
                    'type': '', 'fix': '', 'sat': '', 'hdop': '', 'vdop': '', 
                    'pdop': '', 'ageofdgpsdata': '', 'dgpsid': '', 'geotracker_meta': ''
                }
                data_list.append(row)
                point_id += 1
            track_seg_id += 1
        track_fid += 1

    if data_list:
        df = pd.DataFrame(data_list)
        # Standardize column order
        columns = ['X', 'Y', 'track_fid', 'track_seg_id', 'track_seg_point_id', 'ele', 'time', 
                   'magvar', 'geoidheight', 'name', 'cmt', 'desc', 'src', 'link1_href', 
                   'link1_text', 'link1_type', 'link2_href', 'link2_text', 'link2_type', 
                   'sym', 'type', 'fix', 'sat', 'hdop', 'vdop', 'pdop', 'ageofdgpsdata', 
                   'dgpsid', 'geotracker_meta']
        df = df[columns]
        df.to_csv(csv_output_path, index=False)
        print(f"Successfully created: {csv_output_path}")
    else:
        print("No valid track points found in the file.")

if __name__ == "__main__":
    file_to_convert = select_file()
    convert_gpx_to_csv(file_to_convert)