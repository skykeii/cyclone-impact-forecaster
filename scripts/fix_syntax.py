import os, glob, re

for path in glob.glob("scripts/*.py"):
    with open(path, "r") as f:
        content = f.read()
    
    # fix the f-string quotes
    content = content.replace('{event_id.capitalize()}', '{event_id.capitalize()}')
    content = content.replace('"{event_id.capitalize()}"', 'event_id')
    content = content.replace('event_id', 'event_id')
    
    with open(path, "w") as f:
        f.write(content)
