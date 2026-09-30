"""
Step 02 -- Sentinel-1 Flood Ground Truth via Google Earth Engine
================================================================
1.  Verify the DEM catalog ID (COPERNICUS/DEM/GLO30).
2.  List COPERNICUS/S1_GRD IW scenes for the Amphan study area
    (10--30 May 2020).
3.  Pick one pre-event and one post-event scene.
4.  Change detection: VV ratio (pre/post in power), threshold 1.25.
5.  Mask permanent water (JRC GSW occurrence >= 80 %) and steep slopes
    (>= 5 deg from DEM).
6.  Compute flooded area (km^2) and export the mask.

KILL CHECKS
-----------
*  GEE authentication fails --> stop.
*  No usable post-event S1 scene --> stop.
"""

import ee
import os
import sys
import argparse
import io
import json
import zipfile
import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from urllib.error import URLError

# ── Paths ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
DATA_DIR  = ROOT / "data"
CACHE_DIR = ROOT / "cache"
DB_PATH   = DATA_DIR / "forecaster.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Study area (West Bengal coast) ────────────────────────────────────
BBOX = [87.5, 21.5, 89.1, 23.0]          # [W, S, E, N]

# ── GEE dataset IDs (verified via GEE catalog) ───────────────────────
S1_ID  = "COPERNICUS/S1_GRD"
DEM_ID = "COPERNICUS/DEM/GLO30"           # band: DEM
GSW_ID = "JRC/GSW1_4/GlobalSurfaceWater"  # band: occurrence

# ── Parameters ────────────────────────────────────────────────────────
FLOOD_RATIO_THRESHOLD  = 1.25        # UN-SPIDER recommended
SLOPE_MAX_DEG          = 5.0         # mask slopes steeper than this
PERMANENT_WATER_PCT    = 80          # JRC occurrence >= this = permanent
SPECKLE_RADIUS_M       = 50          # focal-mean kernel radius
EXPORT_SCALE_M         = 100         # resolution for raster export
VECTOR_SCALE_M         = 500         # resolution for GeoJSON vectorisation
LANDFALL_DATE          = "2020-05-20"


# =====================================================================
#  1.  GEE AUTHENTICATION
# =====================================================================
def init_gee():
    project = os.environ.get("GEE_PROJECT")
    print("[AUTH] Initializing Google Earth Engine ...")
    if project:
        print(f"       GEE_PROJECT = {project}")

    try:
        ee.Initialize(project=project)
        print("[AUTH] OK\n")
        return
    except Exception as e1:
        print(f"[AUTH] Direct init failed ({e1})")
        print("[AUTH] Attempting interactive authentication ...")
        try:
            ee.Authenticate()
            ee.Initialize(project=project)
            print("[AUTH] OK (after browser auth)\n")
            return
        except Exception as e2:
            print(f"\n{'='*60}")
            print("  KILL CHECK: GEE AUTHENTICATION FAILED")
            print(f"{'='*60}")
            print(f"  Error: {e2}\n")
            print("  To fix:")
            print("   1. pip install earthengine-api")
            print("   2. earthengine authenticate")
            print("   3. Set env GEE_PROJECT=<your-gcp-project-id>")
            print("   4. Enable Earth Engine API in GCP console")
            print(f"{'='*60}\n")
            sys.exit(1)


# =====================================================================
#  2.  VERIFY DEM CATALOG ID
# =====================================================================
def verify_dem():
    print("[DEM] Verifying COPERNICUS/DEM/GLO30 ...")
    try:
        dem_col = ee.ImageCollection(DEM_ID)
        sample = dem_col.first()
        bands = sample.bandNames().getInfo()
        print(f"       Bands: {bands}")
        assert "DEM" in bands, f"Expected 'DEM' band, got {bands}"

        # Quick elevation sample at Kolkata (22.57N, 88.36E)
        pt = ee.Geometry.Point(88.36, 22.57)
        val = sample.select("DEM").reduceRegion(
            ee.Reducer.first(), pt, 30
        ).get("DEM").getInfo()
        print(f"       Sample elevation at Kolkata: {val} m")
        print("[DEM] OK -- catalog ID and band name confirmed.\n")
    except Exception as e:
        print(f"\n[KILL CHECK] DEM verification failed: {e}")
        sys.exit(1)


# =====================================================================
#  3.  LIST SENTINEL-1 SCENES
# =====================================================================
def list_s1_scenes(aoi):
    print("[S1] Listing COPERNICUS/S1_GRD IW VV scenes (10-30 May 2020) ...")
    s1 = (
        ee.ImageCollection(S1_ID)
        .filterBounds(aoi)
        .filterDate("2020-05-10", "2020-05-31")
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
    )

    count = s1.size().getInfo()
    print(f"       {count} scenes found.\n")

    if count == 0:
        print("[KILL CHECK] Zero S1 scenes -- cannot build flood mask.")
        sys.exit(1)

    info = s1.getInfo()["features"]
    scenes = []
    print(f"{'#':>3}  {'DATE':>12}  {'ORBIT':>6}  {'PASS':>12}  ASSET ID")
    print("-" * 100)
    for i, feat in enumerate(info):
        p = feat["properties"]
        ts_ms = p.get("system:time_start", 0)
        d = datetime.utcfromtimestamp(ts_ms / 1000)
        date_str = d.strftime("%Y-%m-%d")
        orbit = p.get("relativeOrbitNumber_start", "?")
        pass_dir = p.get("orbitProperties_pass", "?")
        aid = feat["id"]
        print(f"{i+1:3}  {date_str:>12}  {orbit!s:>6}  {pass_dir:>12}  {aid}")
        scenes.append(dict(id=aid, date=date_str, date_dt=d, orbit=orbit,
                           pass_dir=pass_dir))
    print()
    return scenes, s1


# =====================================================================
#  4.  SELECT PRE / POST PAIR
# =====================================================================
def select_pair(scenes):
    lf = datetime.strptime(LANDFALL_DATE, "%Y-%m-%d")
    pre  = [s for s in scenes if s["date_dt"].date() < lf.date()]
    post = [s for s in scenes if s["date_dt"].date() > lf.date()]

    # Scenes exactly ON landfall date are ambiguous -- exclude them
    on_day = [s for s in scenes if s["date_dt"].date() == lf.date()]
    if on_day:
        print(f"[NOTE] {len(on_day)} scene(s) on landfall day excluded "
              f"from both pre/post to avoid ambiguity.\n")

    if not pre:
        print("[KILL CHECK] No pre-event S1 scene found.")
        sys.exit(1)
    if not post:
        print(f"\n{'='*60}")
        print("  KILL CHECK: NO POST-EVENT S1 SCENE EXISTS")
        print(f"{'='*60}")
        print("  Cannot create flood ground truth -- dropping event.")
        print(f"{'='*60}\n")
        sys.exit(1)

    # Latest pre, earliest post (closest to landfall)
    pre_pick  = max(pre,  key=lambda s: s["date_dt"])
    post_pick = min(post, key=lambda s: s["date_dt"])

    print(f"[SELECT] Pre-event  : {pre_pick['date']}  "
          f"(orbit {pre_pick['orbit']}, {pre_pick['pass_dir']})")
    print(f"[SELECT] Post-event : {post_pick['date']}  "
          f"(orbit {post_pick['orbit']}, {post_pick['pass_dir']})")
    print()
    return pre_pick, post_pick


# =====================================================================
#  5.  CHANGE DETECTION
# =====================================================================
def build_flood_mask(s1_col, aoi, pre_info, post_info):
    print("[FLOOD] Running change detection ...")

    # ── composites for pre / post dates ───────────────────────────────
    def day_composite(date_str):
        d = datetime.strptime(date_str, "%Y-%m-%d")
        d1 = (d + timedelta(days=1)).strftime("%Y-%m-%d")
        return (
            s1_col.filterDate(date_str, d1)
            .select("VV")
            .mean()
            .clip(aoi)
        )

    pre_vv  = day_composite(pre_info["date"])
    post_vv = day_composite(post_info["date"])

    # ── speckle filter (focal mean) ───────────────────────────────────
    pre_sm  = pre_vv.focal_mean(radius=SPECKLE_RADIUS_M, units="meters")
    post_sm = post_vv.focal_mean(radius=SPECKLE_RADIUS_M, units="meters")
    print(f"       Speckle filter: focal mean, radius {SPECKLE_RADIUS_M} m")

    # ── convert dB --> linear power ───────────────────────────────────
    pre_pow  = ee.Image(10).pow(pre_sm.divide(10))
    post_pow = ee.Image(10).pow(post_sm.divide(10))

    # ── ratio = pre / post; ratio > threshold --> flooding ────────────
    ratio = pre_pow.divide(post_pow)
    flood_raw = ratio.gt(FLOOD_RATIO_THRESHOLD)
    print(f"       Ratio threshold: {FLOOD_RATIO_THRESHOLD}")

    # ── mask permanent water (JRC Global Surface Water v1.4) ──────────
    gsw = ee.Image(GSW_ID).select("occurrence")
    perm_water = gsw.gte(PERMANENT_WATER_PCT)
    print(f"       Permanent water mask: JRC GSW occurrence >= {PERMANENT_WATER_PCT}%")

    # ── mask steep slopes (Copernicus DEM GLO-30) ─────────────────────
    dem   = ee.ImageCollection(DEM_ID).select("DEM").mosaic()
    slope = ee.Terrain.slope(dem)
    steep = slope.gte(SLOPE_MAX_DEG)
    print(f"       Slope mask: >= {SLOPE_MAX_DEG} deg")

    # ── final flood mask ──────────────────────────────────────────────
    flood = flood_raw.And(perm_water.Not()).And(steep.Not())
    flood = flood.rename("flood").toByte()
    print("[FLOOD] Flood mask computed.\n")

    return flood


# =====================================================================
#  6.  COMPUTE AREA STATISTICS
# =====================================================================
def compute_area(flood, aoi):
    print("[AREA] Computing flooded area ...")
    flood_area = flood.multiply(ee.Image.pixelArea())
    stats = flood_area.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=aoi,
        scale=EXPORT_SCALE_M,
        maxPixels=1e9,
        bestEffort=True,
    )
    area_m2 = stats.get("flood").getInfo()
    area_km2 = (area_m2 or 0) / 1e6

    # Total study area
    total_area = aoi.area(maxError=100).getInfo() / 1e6

    print(f"       Flooded area : {area_km2:,.1f} km2")
    print(f"       Study area   : {total_area:,.1f} km2")
    if total_area > 0:
        print(f"       Fraction     : {area_km2/total_area*100:.1f}%")
    print()
    return area_km2, total_area


# =====================================================================
#  7.  EXPORT FLOOD MASK (GeoTIFF + GeoJSON)
# =====================================================================
def export_raster(flood, aoi, out_path):
    print(f"[EXPORT] Downloading flood mask GeoTIFF ({EXPORT_SCALE_M} m) ...")
    try:
        url = flood.unmask(0).toByte().getDownloadURL({
            "name":   f"{event_id}_flood",
            "region": aoi.getInfo()["coordinates"],
            "scale":  EXPORT_SCALE_M,
            "crs":    "EPSG:4326",
            "format": "GEO_TIFF",
        })
    except Exception as e:
        print(f"[EXPORT] getDownloadURL failed: {e}")
        print("         Trying with lower resolution ...")
        url = flood.unmask(0).toByte().getDownloadURL({
            "name":   f"{event_id}_flood",
            "region": aoi.getInfo()["coordinates"],
            "scale":  250,
            "crs":    "EPSG:4326",
            "format": "GEO_TIFF",
        })

    req = Request(url, headers={"User-Agent": "CycloneForecaster/0.1"})
    t0 = time.time()
    resp = urlopen(req, timeout=300)
    data = resp.read()
    elapsed = time.time() - t0

    # Handle potential zip wrapper
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            tifs = [n for n in zf.namelist() if n.endswith(".tif")]
            if tifs:
                data = zf.read(tifs[0])

    out_path.write_bytes(data)
    print(f"         Saved: {out_path}  ({len(data)/1e6:.1f} MB, {elapsed:.0f}s)\n")
    return out_path


def export_geojson(flood, aoi, out_path):
    """Vectorise the flood mask at coarse resolution for the API."""
    print(f"[VECTOR] Vectorising flood mask ({VECTOR_SCALE_M} m) ...")
    try:
        vectors = flood.selfMask().reduceToVectors(
            geometry=aoi,
            scale=VECTOR_SCALE_M,
            geometryType="polygon",
            eightConnected=True,
            maxPixels=1e8,
            bestEffort=True,
        )
        n = vectors.size().getInfo()
        if n > 20000:
            print(f"[VECTOR] {n} polygons -- too many, simplifying ...")
            vectors = vectors.map(
                lambda f: f.setGeometry(f.geometry().simplify(1000))
            )

        fc = vectors.getInfo()
        out_path.write_text(json.dumps(fc))
        print(f"         Saved: {out_path}  ({len(fc['features'])} polygons)\n")
    except Exception as e:
        print(f"[VECTOR] Vectorisation failed: {e}")
        print("         (Raster GeoTIFF is still available.)\n")


# =====================================================================
#  8.  UPDATE SQLITE
# =====================================================================
def update_db(pre_date, post_date):
    print("[DB] Updating scene dates in events table ...")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE events SET s1_before_date = ?, s1_after_date = ? WHERE id = event_id",
        (pre_date, post_date),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM events WHERE id = event_id").fetchone()
    if row:
        print(f"       s1_before_date = {row[4]}")
        print(f"       s1_after_date  = {row[5]}")
    conn.close()
    print()


# =====================================================================
#  MAIN
# =====================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', required=True, choices=['amphan', 'remal'])
    args = parser.parse_args()
    event_id = args.event
    print("\n" + "=" * 60)
    print("  STEP 02 -- SENTINEL-1 FLOOD GROUND TRUTH")
    print("=" * 60 + "\n")

    t_start = time.time()

    # 1. Auth
    init_gee()

    # 2. Verify DEM
    verify_dem()

    # 3. List scenes
    aoi = ee.Geometry.Rectangle(BBOX)
    scenes, s1_col = list_s1_scenes(aoi)

    # 4. Select pair
    pre_info, post_info = select_pair(scenes)

    # 5. Build flood mask
    flood = build_flood_mask(s1_col, aoi, pre_info, post_info)

    # 6. Area stats
    area_km2, total_km2 = compute_area(flood, aoi)

    # 7. Export
    tif_path = DATA_DIR / f"{event_id}_flood_observed.tif"
    export_raster(flood, aoi, tif_path)

    gjson_path = DATA_DIR / f"{event_id}_flood_observed.geojson"
    export_geojson(flood, aoi, gjson_path)

    # 8. Update DB
    update_db(pre_info["date"], post_info["date"])

    # Summary
    elapsed = time.time() - t_start
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Pre-event scene  : {pre_info['date']}")
    print(f"  Post-event scene : {post_info['date']}")
    print(f"  Flooded area     : {area_km2:,.1f} km2 / {total_km2:,.1f} km2 "
          f"({area_km2/total_km2*100:.1f}%)" if total_km2 else "")
    print(f"  Raster output    : {tif_path}")
    print(f"  GeoJSON output   : {gjson_path}")
    print(f"  Elapsed          : {elapsed:.0f}s")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

