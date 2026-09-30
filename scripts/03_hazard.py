"""
Step 03 -- Deterministic Hazard Model
=====================================
Builds a SIMPLIFIED SCREENING MODEL (NOT a hydrodynamic simulator).

Components (weighted score 0-1 per grid cell):
  1. Elevation        (0.25)  -- lower = higher risk
  2. Coastal distance  (0.20)  -- closer = higher risk
  3. Hydro connectivity(0.15)  -- low-elev + near coast = connected
  4. Surge proxy       (0.25)  -- pressure-deficit sea-level rise + wind
                                  setup vs elevation, within coastal reach
  5. Rainfall          (0.15)  -- accumulated precip 7 days pre-landfall

Data sources:
  * Pre-cutoff track from SQLite (IBTrACS, step 01)
  * Elevation from Open-Meteo (SRTM-backed)
  * Rainfall from Open-Meteo Historical API (ERA5)
  * Coastline approximated from reference points

Outputs:
  * data/amphan_hazard.geojson   (grid cells with scores)
  * Console summary
"""

import sys
import argparse
import json
import time
import sqlite3
import hashlib
from pathlib import Path
from math import radians, cos, sin, sqrt, atan2
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

import numpy as np

# ── Paths ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
DATA_DIR  = ROOT / "data"
DB_PATH   = DATA_DIR / "forecaster.db"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ─────────────────────────────────────────────────────────────
BBOX = {"west": 87.5, "south": 21.5, "east": 89.1, "north": 23.0}
GRID_RES  = 0.05          # degrees (~5.5 km) -- balances detail vs API calls
BATCH_SZ  = 80            # locations per Open-Meteo request
REQUEST_DELAY = 1.5       # seconds between API calls (rate-limit respect)

OPEN_METEO_URL = "https://archive-api.open-meteo.com/v1/archive"

WEIGHTS = {
    "elevation":    0.25,
    "coastal_dist": 0.20,
    "hydro_conn":   0.15,
    "surge_proxy":  0.25,
    "rainfall":     0.15,
}

# Approximate coastline: (lat, lon) from Digha to Sundarbans east
COAST_POINTS = [
    (21.63, 87.50), (21.60, 87.65), (21.58, 87.80),
    (21.55, 87.95), (21.55, 88.10), (21.58, 88.25),
    (21.62, 88.35), (21.60, 88.50), (21.57, 88.65),
    (21.55, 88.80), (21.52, 88.95), (21.50, 89.10),
]


# =====================================================================
#  UTILITIES
# =====================================================================

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def coastal_distance(lat, lon):
    return min(haversine_km(lat, lon, cp[0], cp[1]) for cp in COAST_POINTS)


# =====================================================================
#  1. LOAD PRE-CUTOFF TRACK
# =====================================================================

def load_track():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    ev = conn.execute("SELECT * FROM events WHERE id=event_id").fetchone()
    if not ev:
        print("[ERROR] No event_id event in DB.  Run 01_ibtracs.py first.")
        sys.exit(1)
    cutoff = ev["cutoff_utc"]

    rows = conn.execute(
        "SELECT ts, lat, lon, wind_kt, pres_hpa FROM track_points "
        "WHERE event_id=event_id AND ts <= ? ORDER BY ts", (cutoff,)
    ).fetchall()
    conn.close()

    track = [dict(r) for r in rows]
    print(f"[TRACK] {len(track)} pre-cutoff points (cutoff = {cutoff})")
    return track, cutoff


# =====================================================================
#  2. SURGE ESTIMATE FROM TRACK INTENSITY
# =====================================================================

def estimate_surge(track):
    """
    Simplified surge proxy (NOT hydrodynamic).
      Inverted barometer:  dP (hPa) * 0.01 m/hPa
      Wind setup (rough):  V_max^2 / (g * D_eff)
        with V_max in m/s, g=9.81, D_eff=100 m (effective shelf depth)
    """
    pressures = [r["pres_hpa"] for r in track if r["pres_hpa"]]
    winds     = [r["wind_kt"]  for r in track if r["wind_kt"]]
    if not pressures or not winds:
        print("[WARN] No pressure/wind data -- surge proxy disabled.")
        return 0.0, None, None

    min_p = min(pressures)
    max_w = max(winds)
    dp = max(0, 1013 - min_p)
    ib_m = dp * 0.01
    v_ms = max_w * 0.5144
    ws_m = v_ms ** 2 / (9.81 * 100)
    surge_m = ib_m + ws_m

    print(f"[SURGE] Min pressure   : {min_p} hPa  (dP = {dp} hPa)")
    print(f"        Max wind       : {max_w} kt   ({v_ms:.1f} m/s)")
    print(f"        Inv. barometer : {ib_m:.2f} m")
    print(f"        Wind setup     : {ws_m:.2f} m")
    print(f"        Est. surge     : {surge_m:.1f} m")
    print(f"        NOTE: simplified screening proxy, NOT hydrodynamic.\n")
    return surge_m, min_p, max_w


# =====================================================================
#  3. CREATE GRID
# =====================================================================

def make_grid():
    lons = np.arange(BBOX["west"], BBOX["east"] + GRID_RES / 2, GRID_RES)
    lats = np.arange(BBOX["south"], BBOX["north"] + GRID_RES / 2, GRID_RES)
    grid = []
    for lat in lats:
        for lon in lons:
            grid.append({"lat": round(float(lat), 4),
                         "lon": round(float(lon), 4)})
    print(f"[GRID] {len(lons)}x{len(lats)} = {len(grid)} cells  "
          f"(res {GRID_RES} deg ~ {GRID_RES*111:.1f} km)\n")
    return grid, lons.tolist(), lats.tolist()


# =====================================================================
#  4. OPEN-METEO: ELEVATION + RAINFALL
# =====================================================================

def _cache_path(batch_idx):
    return CACHE_DIR / f"openmeteo_batch_{batch_idx:04d}.json"


def _fetch_batch(lats, lons, start, end, batch_idx):
    """Fetch one batch from Open-Meteo Historical API, with cache."""
    cp = _cache_path(batch_idx)
    if cp.exists():
        return json.loads(cp.read_text())

    lat_str = ",".join(f"{x:.4f}" for x in lats)
    lon_str = ",".join(f"{x:.4f}" for x in lons)
    url = (f"{OPEN_METEO_URL}?latitude={lat_str}&longitude={lon_str}"
           f"&start_date={start}&end_date={end}"
           f"&daily=precipitation_sum&timezone=UTC")

    req = Request(url, headers={"User-Agent": "CycloneForecaster/0.1"})
    for attempt in range(4):
        try:
            resp = urlopen(req, timeout=60)
            body = json.loads(resp.read().decode())
            cp.write_text(json.dumps(body))
            return body
        except HTTPError as e:
            if e.code == 429:
                wait = 2 ** (attempt + 1)
                print(f"           429 -- backing off {wait}s ...")
                time.sleep(wait)
            else:
                raise
        except URLError as e:
            print(f"           Network error: {e}, retrying ...")
            time.sleep(3)
    print(f"[ERROR] Batch {batch_idx} failed after retries.")
    return None


def fetch_openmeteo(grid):
    """Fetch elevation + 7-day rainfall for every grid cell."""
    start_date = "2020-05-13"
    end_date   = "2020-05-20"

    print(f"[METEO] Querying Open-Meteo ERA5  ({start_date} to {end_date})")
    print(f"        {len(grid)} points in batches of {BATCH_SZ}")

    batches = [grid[i:i + BATCH_SZ] for i in range(0, len(grid), BATCH_SZ)]
    print(f"        {len(batches)} batches\n")

    for bi, batch in enumerate(batches):
        blats = [c["lat"] for c in batch]
        blons = [c["lon"] for c in batch]
        body = _fetch_batch(blats, blons, start_date, end_date, bi)

        if body is None:
            for c in batch:
                c["elevation"] = None
                c["total_precip_mm"] = None
            continue

        # Response is a list if >1 location, else a single dict
        if isinstance(body, list):
            results = body
        else:
            results = [body]

        for j, res in enumerate(results):
            if j >= len(batch):
                break
            batch[j]["elevation"] = res.get("elevation")
            daily = res.get("daily", {})
            precip_vals = daily.get("precipitation_sum", [])
            batch[j]["total_precip_mm"] = sum(
                v for v in precip_vals if v is not None
            )

        pct = (bi + 1) / len(batches) * 100
        print(f"        batch {bi+1}/{len(batches)}  ({pct:.0f}%)")
        if bi < len(batches) - 1:
            time.sleep(REQUEST_DELAY)

    # Stats
    elevs  = [c["elevation"] for c in grid if c["elevation"] is not None]
    rains  = [c["total_precip_mm"] for c in grid if c["total_precip_mm"] is not None]
    print(f"\n[METEO] Elevation range : {min(elevs):.0f} - {max(elevs):.0f} m"
          if elevs else "\n[METEO] No elevation data!")
    print(f"[METEO] Rainfall range  : {min(rains):.0f} - {max(rains):.0f} mm"
          if rains else "[METEO] No rainfall data!")
    print()
    return grid


# =====================================================================
#  5. COMPUTE HAZARD SCORES
# =====================================================================

def score_grid(grid, surge_m):
    """Compute per-cell component scores and weighted hazard."""
    max_rain = max((c.get("total_precip_mm") or 0) for c in grid) or 1.0

    for c in grid:
        elev = c.get("elevation") or 0
        if elev < -50:          # ocean / NoData sentinel
            elev = 0
        dist = coastal_distance(c["lat"], c["lon"])
        rain = c.get("total_precip_mm") or 0

        # Component scores (all 0-1, higher = more risk)
        s_elev = max(0.0, min(1.0, 1.0 - elev / 15.0))
        s_dist = max(0.0, min(1.0, 1.0 - dist / 60.0))
        s_hydro = max(0.0, 1.0 - elev / 5.0) * max(0.0, 1.0 - dist / 30.0)
        s_hydro = min(1.0, s_hydro)

        if dist < 40 and elev < surge_m and surge_m > 0:
            s_surge = (1.0 - elev / surge_m) * max(0.0, 1.0 - dist / 40.0)
            s_surge = max(0.0, min(1.0, s_surge))
        else:
            s_surge = 0.0

        s_rain = min(1.0, rain / max_rain)

        hazard = (WEIGHTS["elevation"]    * s_elev
                + WEIGHTS["coastal_dist"] * s_dist
                + WEIGHTS["hydro_conn"]   * s_hydro
                + WEIGHTS["surge_proxy"]  * s_surge
                + WEIGHTS["rainfall"]     * s_rain)

        c.update({
            "coastal_dist_km": round(dist, 2),
            "s_elev":  round(s_elev, 4),
            "s_dist":  round(s_dist, 4),
            "s_hydro": round(s_hydro, 4),
            "s_surge": round(s_surge, 4),
            "s_rain":  round(s_rain, 4),
            "hazard_score": round(hazard, 4),
        })

    scores = [c["hazard_score"] for c in grid]
    print(f"[HAZARD] Score range: {min(scores):.3f} - {max(scores):.3f}")
    print(f"         Mean: {np.mean(scores):.3f}  Median: {np.median(scores):.3f}")

    thresholds = [0.3, 0.5, 0.7]
    for t in thresholds:
        n = sum(1 for s in scores if s >= t)
        print(f"         >= {t}: {n} cells ({n/len(scores)*100:.1f}%)")
    print()
    return grid


# =====================================================================
#  6. EXPORT GEOJSON
# =====================================================================

def export_geojson(grid, out_path, surge_m, cutoff):
    half = GRID_RES / 2
    features = []
    for c in grid:
        lat, lon = c["lat"], c["lon"]
        # Grid cell as a small rectangle
        coords = [[
            [round(lon - half, 5), round(lat - half, 5)],
            [round(lon + half, 5), round(lat - half, 5)],
            [round(lon + half, 5), round(lat + half, 5)],
            [round(lon - half, 5), round(lat + half, 5)],
            [round(lon - half, 5), round(lat - half, 5)],
        ]]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": coords},
            "properties": {
                "lat": lat, "lon": lon,
                "elevation_m":      c.get("elevation"),
                "coastal_dist_km":  c.get("coastal_dist_km"),
                "total_precip_mm":  c.get("total_precip_mm"),
                "s_elev":           c.get("s_elev"),
                "s_dist":           c.get("s_dist"),
                "s_hydro":          c.get("s_hydro"),
                "s_surge":          c.get("s_surge"),
                "s_rain":           c.get("s_rain"),
                "hazard_score":     c.get("hazard_score"),
            },
        })

    fc = {
        "type": "FeatureCollection",
        "properties": {
            "model": "simplified_screening",
            "disclaimer": "Simplified screening model, NOT a hydrodynamic simulator.",
            "weights": WEIGHTS,
            "surge_estimate_m": round(surge_m, 2),
            "cutoff_utc": cutoff,
            "grid_resolution_deg": GRID_RES,
        },
        "features": features,
    }
    out_path.write_text(json.dumps(fc))
    print(f"[EXPORT] {out_path}  ({len(features)} cells)\n")


# =====================================================================
#  MAIN
# =====================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', required=True, choices=['amphan', 'remal'])
    args = parser.parse_args()
    event_id = args.event
    print("\n" + "=" * 60)
    print("  STEP 03 -- DETERMINISTIC HAZARD MODEL")
    print("  (Simplified screening -- NOT hydrodynamic)")
    print("=" * 60 + "\n")
    t0 = time.time()

    # 1. Track
    track, cutoff = load_track()

    # 2. Surge estimate
    surge_m, min_p, max_w = estimate_surge(track)

    # 3. Grid
    grid, lons, lats = make_grid()

    # 4. Open-Meteo elevation + rainfall
    grid = fetch_openmeteo(grid)

    # 5. Hazard scores
    grid = score_grid(grid, surge_m)

    # 6. Export
    out = DATA_DIR / f"{event_id}_hazard.geojson"
    export_geojson(grid, out, surge_m, cutoff)

    # Summary
    elapsed = time.time() - t0
    high_risk = sum(1 for c in grid if c["hazard_score"] >= 0.5)
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Grid cells         : {len(grid)}")
    print(f"  Resolution         : {GRID_RES} deg (~{GRID_RES*111:.1f} km)")
    print(f"  Surge estimate     : {surge_m:.1f} m")
    print(f"  High-risk (>=0.5)  : {high_risk} cells "
          f"({high_risk/len(grid)*100:.1f}%)")
    print(f"  Output             : {out}")
    print(f"  Elapsed            : {elapsed:.0f}s")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

