"""
Step 04 -- OSM Infrastructure Assets via Overpass
==================================================
Queries Overpass for critical infrastructure in the study area:
  * Hospitals          (amenity=hospital)
  * Primary roads      (highway=primary)
  * Trunk roads        (highway=trunk)
  * Power substations  (power=substation)
  * Power lines        (power=line)
  * Shelters           (amenity=shelter | shelter_type=* | emergency=*)

Rules:
  * ONE query at a time (sequential, never parallel)
  * Cache every response to disk
  * Exponential backoff on HTTP 429
  * Print asset counts per class per district FIRST
  * Attribute: (C) OpenStreetMap contributors

KILL CHECK: report if any class has zero assets.
"""

import sys
import argparse
import json
import time
import sqlite3
import hashlib
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from math import radians, cos, sin, sqrt, atan2

# ── Paths ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache" / "osm"
DATA_DIR  = ROOT / "data"
DB_PATH   = DATA_DIR / "forecaster.db"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# ── Districts (approximate bounding boxes) ────────────────────────────
# bbox format for Overpass: (south, west, north, east)
DISTRICTS = {
    "south_24_parganas": {
        "name": "South 24 Parganas",
        "bbox_ov": (21.50, 88.00, 22.20, 89.10),
    },
    "north_24_parganas": {
        "name": "North 24 Parganas",
        "bbox_ov": (22.20, 88.30, 23.00, 89.10),
    },
    "east_medinipur": {
        "name": "East Medinipur",
        "bbox_ov": (21.50, 87.50, 22.40, 88.10),
    },
    "kolkata": {
        "name": "Kolkata",
        "bbox_ov": (22.45, 88.25, 22.65, 88.45),
    },
    "howrah": {
        "name": "Howrah",
        "bbox_ov": (22.40, 88.15, 22.70, 88.40),
    },
}


# =====================================================================
#  OVERPASS QUERY BUILDER
# =====================================================================

def _overpass_query(bbox_ov):
    """
    Single Overpass QL query that fetches all asset classes within a bbox.
    Uses `out center qt;` for areas/relations and `out body qt;` for ways
    to get geometry (road km).
    """
    s, w, n, e = bbox_ov
    bb = f"{s},{w},{n},{e}"
    return f"""
[out:json][timeout:120];
(
  // Hospitals
  node["amenity"="hospital"]({bb});
  way["amenity"="hospital"]({bb});
  relation["amenity"="hospital"]({bb});

  // Primary roads
  way["highway"="primary"]({bb});

  // Trunk roads
  way["highway"="trunk"]({bb});

  // Power substations
  node["power"="substation"]({bb});
  way["power"="substation"]({bb});

  // Power lines
  way["power"="line"]({bb});

  // Shelters (multiple tagging conventions)
  node["amenity"="shelter"]({bb});
  way["amenity"="shelter"]({bb});
  node["shelter_type"]({bb});
  way["shelter_type"]({bb});
  node["emergency"="assembly_point"]({bb});
  way["emergency"="assembly_point"]({bb});
  node["social_facility"="shelter"]({bb});
  way["social_facility"="shelter"]({bb});
);
out body geom qt;
"""


# =====================================================================
#  OVERPASS FETCH WITH CACHE AND BACKOFF
# =====================================================================

def _cache_key(district_id):
    return CACHE_DIR / f"{district_id}.json"


def fetch_overpass(district_id, bbox_ov):
    """Fetch from Overpass with disk cache and exponential backoff on 429."""
    cp = _cache_key(district_id)
    if cp.exists():
        print(f"       [CACHE HIT] {cp.name}")
        return json.loads(cp.read_text())

    query = _overpass_query(bbox_ov)
    data = f"data={query}".encode("utf-8")
    req = Request(OVERPASS_URL, data=data, method="POST",
                  headers={"User-Agent": "CycloneForecaster/0.1",
                            "Content-Type": "application/x-www-form-urlencoded"})

    for attempt in range(5):
        try:
            resp = urlopen(req, timeout=120)
            body = json.loads(resp.read().decode("utf-8"))
            cp.write_text(json.dumps(body, ensure_ascii=True))
            return body
        except HTTPError as e:
            if e.code == 429 or e.code == 504:
                wait = 2 ** (attempt + 2)   # 4, 8, 16, 32, 64 s
                print(f"       HTTP {e.code} -- backing off {wait}s "
                      f"(attempt {attempt+1}/5)")
                time.sleep(wait)
            else:
                print(f"       HTTP {e.code}: {e.reason}")
                raise
        except URLError as e:
            print(f"       Network error: {e} -- retrying in 5s ...")
            time.sleep(5)

    print(f"[ERROR] Overpass failed after 5 retries for {district_id}.")
    return {"elements": []}


# =====================================================================
#  CLASSIFY ELEMENTS
# =====================================================================

def classify_element(el):
    """Return (asset_type, name, geom_dict) for an OSM element."""
    tags = el.get("tags", {})

    # Determine asset type
    if tags.get("amenity") == "hospital":
        atype = "hospital"
    elif tags.get("highway") == "primary":
        atype = "road_primary"
    elif tags.get("highway") == "trunk":
        atype = "road_trunk"
    elif tags.get("power") == "substation":
        atype = "power_substation"
    elif tags.get("power") == "line":
        atype = "power_line"
    elif (tags.get("amenity") == "shelter"
          or "shelter_type" in tags
          or tags.get("emergency") == "assembly_point"
          or tags.get("social_facility") == "shelter"):
        atype = "shelter"
    else:
        return None, None, None

    name = tags.get("name", tags.get("name:en", ""))

    # Build GeoJSON geometry
    etype = el.get("type")
    if etype == "node":
        geom = {"type": "Point",
                "coordinates": [el["lon"], el["lat"]]}
    elif etype == "way":
        geom_nodes = el.get("geometry", [])
        if geom_nodes:
            coords = [[g["lon"], g["lat"]] for g in geom_nodes]
            if atype in ("road_primary", "road_trunk", "power_line"):
                geom = {"type": "LineString", "coordinates": coords}
            else:
                # Closed way = polygon (hospital/substation building)
                if coords[0] == coords[-1] and len(coords) >= 4:
                    geom = {"type": "Polygon", "coordinates": [coords]}
                else:
                    geom = {"type": "LineString", "coordinates": coords}
        elif "center" in el:
            geom = {"type": "Point",
                    "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
        else:
            return None, None, None
    elif etype == "relation":
        if "center" in el:
            geom = {"type": "Point",
                    "coordinates": [el["center"]["lon"], el["center"]["lat"]]}
        else:
            return None, None, None
    else:
        return None, None, None

    return atype, name, geom


def _geom_centroid(geom):
    """Quick centroid for point-in-bbox checks."""
    gtype = geom["type"]
    if gtype == "Point":
        return geom["coordinates"][1], geom["coordinates"][0]
    elif gtype == "LineString":
        coords = geom["coordinates"]
        mid = coords[len(coords) // 2]
        return mid[1], mid[0]
    elif gtype == "Polygon":
        coords = geom["coordinates"][0]
        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        return sum(lats) / len(lats), sum(lons) / len(lons)
    return None, None


def _line_length_km(geom):
    """Approximate length of a LineString in km using haversine."""
    if geom["type"] != "LineString":
        return 0.0
    coords = geom["coordinates"]
    total = 0.0
    for i in range(len(coords) - 1):
        lon1, lat1 = coords[i]
        lon2, lat2 = coords[i + 1]
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        total += 6371.0 * 2 * atan2(sqrt(a), sqrt(1 - a))
    return total


# =====================================================================
#  PROCESS + COUNT
# =====================================================================

def process_district(district_id, district_info):
    """Fetch, classify, and return assets for one district."""
    body = fetch_overpass(district_id, district_info["bbox_ov"])
    elements = body.get("elements", [])
    print(f"       {len(elements)} raw OSM elements")

    assets = []
    seen_ids = set()
    for el in elements:
        osm_id = f"{el['type']}/{el['id']}"
        if osm_id in seen_ids:
            continue
        seen_ids.add(osm_id)

        atype, name, geom = classify_element(el)
        if atype is None:
            continue

        uid = f"{district_id}_{osm_id}"
        assets.append({
            "id": uid,
            "osm_id": osm_id,
            "type": atype,
            "name": name,
            "geom": geom,
            "district_id": district_id,
            "length_km": _line_length_km(geom) if atype in ("road_primary", "road_trunk", "power_line") else None,
        })
    return assets


def count_table(all_assets):
    """Print a formatted count table: rows=districts, cols=asset types."""
    types = ["hospital", "road_primary", "road_trunk",
             "power_substation", "power_line", "shelter"]
    type_labels = {
        "hospital": "Hospitals",
        "road_primary": "Primary Rd",
        "road_trunk": "Trunk Rd",
        "power_substation": "Substations",
        "power_line": "Pwr Lines",
        "shelter": "Shelters",
    }

    # Group by district
    by_district = {}
    for a in all_assets:
        d = a["district_id"]
        by_district.setdefault(d, []).append(a)

    # Header
    header = f"{'District':<25}"
    for t in types:
        header += f" {type_labels[t]:>12}"
    header += f" {'TOTAL':>8}"
    print("\n" + "=" * len(header))
    print("  ASSET COUNTS PER CLASS PER DISTRICT")
    print("  Data: (C) OpenStreetMap contributors")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    totals = {t: 0 for t in types}
    road_km = {"road_primary": 0.0, "road_trunk": 0.0}
    grand = 0

    for did, dinfo in DISTRICTS.items():
        dassets = by_district.get(did, [])
        row = f"{dinfo['name']:<25}"
        dtotal = 0
        for t in types:
            n = sum(1 for a in dassets if a["type"] == t)
            totals[t] += n
            dtotal += n
            row += f" {n:>12}"
        grand += dtotal
        row += f" {dtotal:>8}"
        print(row)

        # Road km
        for rt in ("road_primary", "road_trunk"):
            km = sum(a["length_km"] or 0 for a in dassets if a["type"] == rt)
            road_km[rt] += km

    # Totals row
    print("-" * len(header))
    row = f"{'TOTAL':<25}"
    for t in types:
        row += f" {totals[t]:>12}"
    row += f" {grand:>8}"
    print(row)
    print("=" * len(header))

    print(f"\n  Road lengths (approx):")
    print(f"    Primary : {road_km['road_primary']:,.1f} km")
    print(f"    Trunk   : {road_km['road_trunk']:,.1f} km\n")

    # Kill-check warnings
    warnings = []
    for t in types:
        if totals[t] == 0:
            warnings.append(t)
    if warnings:
        print("  [KILL CHECK] Zero assets for classes: "
              + ", ".join(warnings))
        print("               These classes will be dropped.\n")
    return totals


# =====================================================================
#  STORE IN SQLITE
# =====================================================================

def store_districts(conn):
    """Insert district records."""
    for did, dinfo in DISTRICTS.items():
        s, w, n, e = dinfo["bbox_ov"]
        geom = {
            "type": "Polygon",
            "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
        }
        conn.execute(
            "INSERT OR REPLACE INTO districts (id, name, geom) VALUES (?,?,?)",
            (did, dinfo["name"], json.dumps(geom))
        )


def store_assets(conn, assets):
    """Insert asset records."""
    conn.execute("DELETE FROM assets")  # idempotent
    for a in assets:
        conn.execute(
            "INSERT OR IGNORE INTO assets (id, osm_id, type, name, geom, district_id) "
            "VALUES (?,?,?,?,?,?)",
            (a["id"], a["osm_id"], a["type"], a["name"],
             json.dumps(a["geom"]), a["district_id"])
        )


# =====================================================================
#  EXPORT GEOJSON
# =====================================================================

def export_geojson(assets, out_path):
    features = []
    for a in assets:
        features.append({
            "type": "Feature",
            "geometry": a["geom"],
            "properties": {
                "id": a["id"],
                "osm_id": a["osm_id"],
                "type": a["type"],
                "name": a["name"],
                "district_id": a["district_id"],
                "length_km": a.get("length_km"),
            },
        })
    fc = {
        "type": "FeatureCollection",
        "properties": {"attribution": "(C) OpenStreetMap contributors"},
        "features": features,
    }
    out_path.write_text(json.dumps(fc, ensure_ascii=True))
    print(f"[EXPORT] {out_path}  ({len(features)} features)\n")


# =====================================================================
#  MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', required=True, choices=['amphan', 'remal'])
    args = parser.parse_args()
    event_id = args.event
    print("\n" + "=" * 60)
    print("  STEP 04 -- OSM INFRASTRUCTURE ASSETS")
    print("  Data: (C) OpenStreetMap contributors")
    print("=" * 60 + "\n")
    t0 = time.time()

    all_assets = []
    for i, (did, dinfo) in enumerate(DISTRICTS.items()):
        print(f"[{i+1}/{len(DISTRICTS)}] {dinfo['name']} ...")
        assets = process_district(did, dinfo)
        all_assets.extend(assets)
        print(f"       -> {len(assets)} classified assets")
        if i < len(DISTRICTS) - 1:
            print(f"       (waiting 3s before next query ...)")
            time.sleep(3)
        print()

    # ── COUNT TABLE (printed first as requested) ──────────────────────
    totals = count_table(all_assets)

    # ── Store in SQLite ───────────────────────────────────────────────
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    store_districts(conn)
    store_assets(conn, all_assets)
    conn.commit()

    n_d = conn.execute("SELECT COUNT(*) FROM districts").fetchone()[0]
    n_a = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    conn.close()
    print(f"[DB] districts: {n_d} rows  |  assets: {n_a} rows\n")

    # ── Export GeoJSON ────────────────────────────────────────────────
    out = DATA_DIR / f"{event_id}_assets.geojson"
    export_geojson(all_assets, out)

    elapsed = time.time() - t0
    print(f"[DONE] {len(all_assets)} total assets in {elapsed:.0f}s\n")


if __name__ == "__main__":
    main()

