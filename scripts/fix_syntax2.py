import os, glob

for path in glob.glob("scripts/*.py"):
    with open(path, "r") as f:
        content = f.read()
    
    # fix the choices array
    content = content.replace("choices=['amphan', 'remal']", "choices=['amphan', 'remal']")
    
    # fix load_amphan function name if it exists
    content = content.replace("load_amphan", "load_amphan") # Wait, actually 01_ibtracs calls load_amphan(csv_path), which got renamed to load_amphan? 
    # Just fix the ones that broke
    
    with open(path, "w") as f:
        f.write(content)
