"""Quick script to check if Sentinel-1 post-landfall imagery exists for Cyclone Remal."""
import ee
import os
from pathlib import Path
import sys

# Ensure backend package can be imported
sys.path.append(str(Path(__file__).resolve().parent.parent / "backend"))
from app.config import EVENTS

def check_remal():
    project = os.environ.get("GEE_PROJECT", "project4-491910")
    print(f"Authenticating to GEE project: {project}...")
    ee.Initialize(project=project)
    
    remal = EVENTS["remal"]
    bbox = remal["study_bbox"]
    region = ee.Geometry.Rectangle([bbox["west"], bbox["south"], bbox["east"], bbox["north"]])
    
    # Check for post-landfall scenes (May 27 to June 5, 2024)
    collection = (ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate('2024-05-27', '2024-06-05')
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
        .filter(ee.Filter.eq('instrumentMode', 'IW')))
        
    count = collection.size().getInfo()
    print(f"\nFound {count} post-landfall Sentinel-1 scenes for Remal.")
    if count == 0:
        print("NO SCENES EXIST. We must skip Remal according to instructions.")
    else:
        print("Scenes exist! We can proceed with Phase 7.")

if __name__ == "__main__":
    check_remal()
