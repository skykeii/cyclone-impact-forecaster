import os
import re

files = ["01_ibtracs.py", "02_flood_truth.py", "03_hazard.py", "04_assets.py", "05_validate.py"]

for f in files:
    path = f"scripts/{f}"
    with open(path, "r") as file:
        content = file.read()
    
    # Check if already refactored
    if "import argparse" not in content:
        content = "import argparse\n" + content
        
    main_match = re.search(r'def main\(\):', content)
    if main_match and "argparse.ArgumentParser()" not in content:
        replacement = """def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', required=True, choices=['amphan', 'remal'])
    args = parser.parse_args()
    event_id = args.event
"""
        content = content.replace("def main():", replacement)
        
    # Replace "amphan" with event_id
    content = re.sub(r'"amphan"', 'event_id', content)
    content = re.sub(r"'amphan'", 'event_id', content)
    
    # Fix paths
    content = re.sub(r'"amphan_', 'f"{event_id}_', content)
    content = re.sub(r"'amphan_", 'f"{event_id}_', content)
    
    # Fix print statements that have "amphan"
    content = re.sub(r'Cyclone Amphan', '{event_id.capitalize()}', content)
    
    with open(path, "w") as file:
        file.write(content)
