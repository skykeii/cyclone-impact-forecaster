"""Quick verification of Step 01 outputs."""
import sqlite3
import json
from pathlib import Path

DB = Path("data/forecaster.db")
GJ = Path("data/amphan_track.geojson")

# --- SQLite ---
print("=== EVENTS TABLE ===")
conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
for r in conn.execute("SELECT * FROM events").fetchall():
    print(dict(r))

print("\n=== TRACK POINTS (first 3, last 3) ===")
rows = conn.execute(
    "SELECT ts, lat, lon, wind_kt, pres_hpa FROM track_points "
    "WHERE event_id='amphan' ORDER BY ts"
).fetchall()
for r in rows[:3]:
    print(dict(r))
print(f"  ... ({len(rows) - 6} more rows) ...")
for r in rows[-3:]:
    print(dict(r))
print(f"Total: {len(rows)} track points")
conn.close()

# --- GeoJSON ---
print("\n=== GEOJSON ===")
data = json.loads(GJ.read_text())
print(f"Features: {len(data['features'])}")
print(f"Track line: {data['features'][0]['geometry']['type']}")
cutoffs = [f for f in data["features"] if f["properties"].get("is_cutoff")]
if cutoffs:
    print(f"Cutoff point: {cutoffs[0]['properties']}")
    print(f"Cutoff coords: {cutoffs[0]['geometry']['coordinates']}")
else:
    print("WARNING: No cutoff point found!")
