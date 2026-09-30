"""
Step 05 -- Validation & Asset Exposure
======================================
1.  Rasterize the predicted hazard geojson to match the S1 TIF (100m).
2.  Compute pixel-level metrics (IoU, Precision, Recall, AUC) against the S1 flood mask.
3.  Compute a naive baseline (Elevation < 5m & Coast Dist < 50km).
4.  Determine asset exposure (predicted vs observed) by sampling the rasters.
5.  Compute asset-level confusion matrices.
6.  Store all results in SQLite `metrics` and `exposure` tables.
7.  Print a plain-language summary comparing against the baseline.
"""

import sqlite3
import json
import time
import sys
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import rasterio
from rasterio.features import rasterize
from sklearn.metrics import roc_auc_score, precision_score, recall_score, confusion_matrix

# ── Paths ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent.parent
DATA_DIR  = ROOT / "data"
DB_PATH   = DATA_DIR / "forecaster.db"

HAZARD_GJ = DATA_DIR / f"{event_id}_hazard.geojson"
S1_TIF    = DATA_DIR / f"{event_id}_flood_observed.tif"
EVENT_ID  = event_id

THRESHOLD = 0.5  # Hazard score threshold for binary classification

def compute_iou(y_true, y_pred):
    tp = np.sum((y_true == 1) & (y_pred == 1))
    fp = np.sum((y_true == 0) & (y_pred == 1))
    fn = np.sum((y_true == 1) & (y_pred == 0))
    denom = tp + fp + fn
    return tp / denom if denom > 0 else 0.0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', required=True, choices=['amphan', 'remal'])
    args = parser.parse_args()
    event_id = args.event
    print("\n" + "=" * 60)
    print("  STEP 05 -- VALIDATION & ASSET EXPOSURE")
    print("=" * 60 + "\n")
    t0 = time.time()

    if not HAZARD_GJ.exists() or not S1_TIF.exists():
        print("[ERROR] Missing hazard geojson or S1 TIF. Run previous steps.")
        sys.exit(1)

    print("[1/5] Loading rasters and shapes...")
    # Load observed S1 flood mask
    with rasterio.open(S1_TIF) as src:
        s1_data = src.read(1)
        transform = src.transform
        shape = s1_data.shape

    # Load predicted hazard grid
    gdf = gpd.read_file(HAZARD_GJ)

    # Rasterize predicted hazard scores onto the S1 grid
    print("[2/5] Rasterizing predictions to 100m grid for pixel-level evaluation...")
    shapes_pred = ((geom, score) for geom, score in zip(gdf.geometry, gdf["hazard_score"]))
    pred_raster = rasterize(shapes_pred, out_shape=shape, transform=transform, fill=0.0, dtype='float32')

    # Rasterize naive baseline (elev < 5m and dist < 50km)
    shapes_base = []
    for geom, elev, dist in zip(gdf.geometry, gdf["elevation_m"], gdf["coastal_dist_km"]):
        val = 1.0 if (elev is not None and elev < 5.0 and dist is not None and dist < 50.0) else 0.0
        shapes_base.append((geom, val))
    base_raster = rasterize(shapes_base, out_shape=shape, transform=transform, fill=0.0, dtype='float32')

    # Create evaluation mask (only evaluate within the study area polygons)
    shapes_mask = ((geom, 1) for geom in gdf.geometry)
    eval_mask = rasterize(shapes_mask, out_shape=shape, transform=transform, fill=0, dtype='uint8')

    # Flatten arrays for metrics
    valid_idx = (eval_mask == 1)
    y_true = (s1_data[valid_idx] > 0).astype(int)
    y_pred_prob = pred_raster[valid_idx]
    y_pred = (y_pred_prob >= THRESHOLD).astype(int)
    y_base = base_raster[valid_idx].astype(int)

    print("[3/5] Computing pixel-level metrics...")
    
    # ── PIXEL METRICS ──────────────────────────────────────────────────
    iou_pred = compute_iou(y_true, y_pred)
    prec_pred = precision_score(y_true, y_pred, zero_division=0)
    rec_pred = recall_score(y_true, y_pred, zero_division=0)
    
    # Handle edge case if y_true has only one class
    if len(np.unique(y_true)) > 1:
        auc_pred = roc_auc_score(y_true, y_pred_prob)
    else:
        auc_pred = 0.0

    iou_base = compute_iou(y_true, y_base)
    prec_base = precision_score(y_true, y_base, zero_division=0)
    rec_base = recall_score(y_true, y_base, zero_division=0)

    # ── ASSET EXPOSURE ─────────────────────────────────────────────────
    print("[4/5] Evaluating asset exposure...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    assets = conn.execute("SELECT id, type, geom FROM assets WHERE district_id IS NOT NULL").fetchall()
    
    exposure_records = []
    cm_data = []

    for a in assets:
        asset_id = a["id"]
        atype = a["type"]
        geom = json.loads(a["geom"])
        
        # Get centroid
        if geom["type"] == "Point":
            lon, lat = geom["coordinates"]
        elif geom["type"] == "LineString":
            coords = geom["coordinates"]
            lon, lat = coords[len(coords)//2]
        elif geom["type"] == "Polygon":
            coords = geom["coordinates"][0]
            lon = sum(c[0] for c in coords)/len(coords)
            lat = sum(c[1] for c in coords)/len(coords)
        else:
            continue

        # Point sample from rasters
        try:
            row, col = ~transform * (lon, lat)
            row, col = int(row), int(col)
            
            if 0 <= row < shape[0] and 0 <= col < shape[1]:
                p_score = float(pred_raster[row, col])
                obs = int(s1_data[row, col] > 0)
            else:
                p_score = 0.0
                obs = 0
        except:
            p_score = 0.0
            obs = 0

        pred_exp = 1 if p_score >= THRESHOLD else 0
        
        exposure_records.append((asset_id, EVENT_ID, p_score, pred_exp, obs))
        cm_data.append({"type": atype, "y_true": obs, "y_pred": pred_exp})

    # Save to SQLite
    print("[5/5] Saving to database...")
    conn.execute("DELETE FROM exposure WHERE event_id = ?", (EVENT_ID,))
    conn.executemany(
        "INSERT INTO exposure (asset_id, event_id, pred_score, pred_exposed, obs_exposed) VALUES (?, ?, ?, ?, ?)",
        exposure_records
    )

    conn.execute("DELETE FROM metrics WHERE event_id = ?", (EVENT_ID,))
    
    # Store pixel metrics
    conn.execute(
        "INSERT INTO metrics (event_id, scope, iou, precision_, recall, auc, baseline_iou) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (EVENT_ID, "pixel", iou_pred, prec_pred, rec_pred, auc_pred, iou_base)
    )

    # Asset Confusion Matrices
    df_cm = pd.DataFrame(cm_data)
    for atype, grp in df_cm.groupby("type"):
        y_t = grp["y_true"]
        y_p = grp["y_pred"]
        a_iou = compute_iou(y_t, y_p)
        a_prec = precision_score(y_t, y_p, zero_division=0)
        a_rec = recall_score(y_t, y_p, zero_division=0)
        
        conn.execute(
            "INSERT INTO metrics (event_id, scope, iou, precision_, recall, auc, baseline_iou) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (EVENT_ID, atype, a_iou, a_prec, a_rec, None, None)
        )

    conn.commit()
    conn.close()

    # ── SUMMARY ────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  VALIDATION SUMMARY")
    print("="*60)
    print(f"  Pixel-Level Metrics (Threshold = {THRESHOLD})")
    print(f"    Hazard Model : IoU = {iou_pred:.4f}, Prec = {prec_pred:.4f}, Rec = {rec_pred:.4f}, AUC = {auc_pred:.4f}")
    print(f"    Baseline     : IoU = {iou_base:.4f}, Prec = {prec_base:.4f}, Rec = {rec_base:.4f}")
    
    print("\n  Asset-Level Metrics (by class)")
    for atype, grp in df_cm.groupby("type"):
        y_t = grp["y_true"]
        y_p = grp["y_pred"]
        tn, fp, fn, tp = confusion_matrix(y_t, y_p, labels=[0, 1]).ravel()
        print(f"    {atype:<16} : TP={tp:<3} FP={fp:<3} FN={fn:<3} TN={tn:<5} (IoU: {compute_iou(y_t, y_p):.3f})")

    print("\n  CONCLUSION:")
    if iou_pred > iou_base:
        diff = ((iou_pred - iou_base) / iou_base) * 100 if iou_base > 0 else float('inf')
        print(f"    YES, the hazard model BEATS the naive elevation baseline by {diff:.1f}% in IoU.")
        print("    The multi-factor approach (distance, surge, rain) isolates the true flooded areas better than just low elevation.")
    else:
        print("    NO, the hazard model did NOT beat the naive baseline in pixel IoU.")
        print("    Sentinel-1 flooding often heavily correlates with simply being low-lying, ")
        print("    or our surge parameterization over-predicted compared to actual inundation.")
        
    print(f"\n[DONE] Saved exposure ({len(exposure_records)} assets) and metrics to DB. Elapsed: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()

