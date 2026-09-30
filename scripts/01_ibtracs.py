"""
Step 01 — IBTrACS Loader
========================
Downloads the IBTrACS v04r01 North Indian CSV, filters to {event_id.capitalize()} (2020),
prints the full track, identifies landfall, defines a pre-landfall cutoff, and
writes:
  * cache/ibtracs_NI.csv          (raw download, cached)
  * data/amphan_track.geojson     (GeoJSON for the map)
  * data/forecaster.db            (events + track_points rows)

Cutoff justification
--------------------
Landfall of Amphan occurred at approximately 2020-05-20 10:30 UTC near Bakkhali,
West Bengal (21.6°N, 88.3°E).  We set cutoff_utc = landfall_utc so the hazard
model uses only the track information that was available in real-time forecasts
up to and including landfall.  No post-landfall weakening track is leaked.

IBTrACS CSV format notes
-------------------------
- Row 0 = column headers
- Row 1 = units row (must be skipped)
- Key cols: SID, NAME, ISO_TIME, LAT, LON, WMO_WIND, WMO_PRES,
            USA_WIND, USA_PRES, BASIN
- Amphan NAME = event_id, BASIN = "NI"
"""

import sys
import argparse, json, sqlite3
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError

import pandas as pd

# ── Paths ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
DATA_DIR  = ROOT / "data"
DB_PATH   = DATA_DIR / "forecaster.db"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

IBTRACS_URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs"
    "/v04r01/access/csv/ibtracs.NI.list.v04r01.csv"
)
CACHE_FILE = CACHE_DIR / "ibtracs_NI.csv"

# ── 1.  Download (with cache) ─────────────────────────────────────────
def download_ibtracs() -> Path:
    if CACHE_FILE.exists():
        size_mb = CACHE_FILE.stat().st_size / 1e6
        print(f"[CACHE HIT] {CACHE_FILE}  ({size_mb:.1f} MB)")
        return CACHE_FILE

    print(f"[DOWNLOAD] {IBTRACS_URL}")
    print(f"           (NOAA can be slow — 300 s timeout) ", end="", flush=True)
    req = Request(IBTRACS_URL, headers={"User-Agent": "CycloneForecaster/0.1"})
    try:
        resp = urlopen(req, timeout=300)
    except URLError as e:
        print(f"\n[ERROR] Download failed: {e}")
        sys.exit(1)

    chunks = []
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
        print(".", end="", flush=True)

    data = b"".join(chunks)
    CACHE_FILE.write_bytes(data)
    print(f"\n[SAVED]    {CACHE_FILE}  ({len(data)/1e6:.1f} MB)")
    return CACHE_FILE


# ── 2.  Parse & filter ────────────────────────────────────────────────
def load_amphan(csv_path: Path) -> pd.DataFrame:
    """Load IBTrACS CSV, skip units row, filter to Amphan."""
    # Row 1 is the units row — skip it
    df = pd.read_csv(csv_path, skiprows=[1], low_memory=False)

    # Filter to Amphan
    mask = df["NAME"].str.strip().str.upper() == event_id
    amphan = df[mask].copy()

    if amphan.empty:
        # Try by SID pattern
        mask2 = df["SID"].str.contains("2020", na=False) & (df["BASIN"] == "NI")
        print(f"[WARN] NAME filter returned 0 rows; SID+BASIN filter: {mask2.sum()} rows")
        print("       Unique NAMEs in 2020 NI storms:", df[mask2]["NAME"].unique())
        sys.exit(1)

    print(f"[FILTER]   {len(amphan)} track points for {event_id.capitalize()}")
    print(f"           SID = {amphan['SID'].iloc[0]}")

    # Normalise columns
    amphan["LAT"] = pd.to_numeric(amphan["LAT"], errors="coerce")
    amphan["LON"] = pd.to_numeric(amphan["LON"], errors="coerce")
    amphan["WMO_WIND"] = pd.to_numeric(amphan["WMO_WIND"], errors="coerce")
    amphan["WMO_PRES"] = pd.to_numeric(amphan["WMO_PRES"], errors="coerce")
    amphan["USA_WIND"] = pd.to_numeric(amphan["USA_WIND"], errors="coerce")
    amphan["USA_PRES"] = pd.to_numeric(amphan["USA_PRES"], errors="coerce")

    # Prefer USA agency wind/pressure if WMO is missing
    amphan["wind_kt"] = amphan["WMO_WIND"].fillna(amphan["USA_WIND"])
    amphan["pres_hpa"] = amphan["WMO_PRES"].fillna(amphan["USA_PRES"])

    return amphan


# ── 3.  Identify landfall ─────────────────────────────────────────────
def find_landfall(df: pd.DataFrame) -> dict:
    """
    Identify the landfall point.  IBTrACS has a LANDFALL column (0 = at
    landfall distance).  If that's unreliable, fall back to the point
    where latitude is >= 21.5°N and the storm is closest to 88.3°E
    (Bakkhali area).
    """
    # Try LANDFALL column (distance to land in km; 0 = at landfall)
    if "LANDFALL" in df.columns:
        lf_col = pd.to_numeric(df["LANDFALL"], errors="coerce")
        at_land = df[lf_col == 0]
        if not at_land.empty:
            row = at_land.iloc[0]
            print(f"[LANDFALL] (LANDFALL=0 flag)  {row['ISO_TIME']}  "
                  f"({row['LAT']:.1f}°N, {row['LON']:.1f}°E)  "
                  f"wind={row['wind_kt']} kt  pres={row['pres_hpa']} hPa")
            return {
                "ts": str(row["ISO_TIME"]).strip(),
                "lat": float(row["LAT"]),
                "lon": float(row["LON"]),
            }

    # Fallback: first point where lat >= 21.5 (coastal West Bengal)
    coastal = df[df["LAT"] >= 21.5].sort_values("ISO_TIME")
    if not coastal.empty:
        row = coastal.iloc[0]
        print(f"[LANDFALL] (lat >= 21.5 heuristic)  {row['ISO_TIME']}  "
              f"({row['LAT']:.1f}°N, {row['LON']:.1f}°E)")
        return {
            "ts": str(row["ISO_TIME"]).strip(),
            "lat": float(row["LAT"]),
            "lon": float(row["LON"]),
        }

    # Last resort: use peak intensity
    peak = df.loc[df["wind_kt"].idxmax()]
    print(f"[LANDFALL] (peak intensity fallback)  {peak['ISO_TIME']}")
    return {
        "ts": str(peak["ISO_TIME"]).strip(),
        "lat": float(peak["LAT"]),
        "lon": float(peak["LON"]),
    }


# ── 4.  Print track table ─────────────────────────────────────────────
def print_track(df: pd.DataFrame, landfall_ts: str):
    cutoff_marker = " <-- CUTOFF (landfall)"
    print("\n" + "=" * 90)
    print(f"{'TIME (UTC)':<22} {'LAT':>7} {'LON':>7} {'WIND kt':>8} {'PRES hPa':>9}  FLAGS")
    print("-" * 90)
    for _, r in df.iterrows():
        ts = str(r["ISO_TIME"]).strip()
        flag = cutoff_marker if ts == landfall_ts else ""
        wind = r["wind_kt"]
        pres = r["pres_hpa"]
        w_str = f"{wind:8.0f}" if pd.notna(wind) else "     N/A"
        p_str = f"{pres:9.0f}" if pd.notna(pres) else "      N/A"
        print(f"{ts:<22} {r['LAT']:7.1f} {r['LON']:7.1f} {w_str} {p_str}  {flag}")
    print("=" * 90 + "\n")


# ── 5.  Write GeoJSON ─────────────────────────────────────────────────
def write_geojson(df: pd.DataFrame, landfall_ts: str, out_path: Path):
    features = []
    coords = []

    for _, r in df.iterrows():
        ts = str(r["ISO_TIME"]).strip()
        lon, lat = float(r["LON"]), float(r["LAT"])
        coords.append([lon, lat])
        is_cutoff = ts == landfall_ts
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "ts": ts,
                "wind_kt": None if pd.isna(r["wind_kt"]) else float(r["wind_kt"]),
                "pres_hpa": None if pd.isna(r["pres_hpa"]) else float(r["pres_hpa"]),
                "is_cutoff": is_cutoff,
            }
        })

    # Track line
    features.insert(0, {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"type": "track_line", "event": event_id}
    })

    fc = {"type": "FeatureCollection", "features": features}
    out_path.write_text(json.dumps(fc, indent=2))
    print(f"[GEOJSON]  {out_path}  ({len(features)} features)")


# ── 6.  Write SQLite ──────────────────────────────────────────────────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    landfall_utc   TEXT NOT NULL,
    cutoff_utc     TEXT NOT NULL,
    s1_before_date TEXT,
    s1_after_date  TEXT
);
CREATE TABLE IF NOT EXISTS track_points (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id),
    ts       TEXT NOT NULL,
    lat      REAL NOT NULL,
    lon      REAL NOT NULL,
    wind_kt  REAL,
    pres_hpa REAL
);
CREATE TABLE IF NOT EXISTS districts (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    geom TEXT
);
CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    osm_id      TEXT,
    type        TEXT NOT NULL,
    name        TEXT,
    geom        TEXT NOT NULL,
    district_id TEXT REFERENCES districts(id)
);
CREATE TABLE IF NOT EXISTS exposure (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     TEXT NOT NULL REFERENCES assets(id),
    event_id     TEXT NOT NULL REFERENCES events(id),
    pred_score   REAL,
    pred_exposed INTEGER,
    obs_exposed  INTEGER
);
CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL REFERENCES events(id),
    scope        TEXT NOT NULL,
    iou          REAL,
    precision_   REAL,
    recall       REAL,
    auc          REAL,
    baseline_iou REAL
);
CREATE TABLE IF NOT EXISTS advisories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    district_id  TEXT REFERENCES districts(id),
    event_id     TEXT NOT NULL REFERENCES events(id),
    lang         TEXT NOT NULL,
    json_content TEXT NOT NULL,
    verified     INTEGER DEFAULT 0
);
"""


def write_sqlite(df: pd.DataFrame, landfall_ts: str, event_id: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)

    # Upsert event
    conn.execute(
        "INSERT OR REPLACE INTO events (id, name, landfall_utc, cutoff_utc) VALUES (?,?,?,?)",
        (event_id, event_id, landfall_ts, landfall_ts)
    )

    # Clear old track points then insert
    conn.execute("DELETE FROM track_points WHERE event_id = 'amphan' if args.event == 'amphan' else 'remal'")
    rows = []
    for _, r in df.iterrows():
        rows.append((
            event_id,
            str(r["ISO_TIME"]).strip(),
            float(r["LAT"]),
            float(r["LON"]),
            None if pd.isna(r["wind_kt"]) else float(r["wind_kt"]),
            None if pd.isna(r["pres_hpa"]) else float(r["pres_hpa"]),
        ))
    conn.executemany(
        "INSERT INTO track_points (event_id, ts, lat, lon, wind_kt, pres_hpa) VALUES (?,?,?,?,?,?)",
        rows
    )

    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM track_points WHERE event_id=?", (event_id,)).fetchone()[0]
    print(f"[SQLITE]   {DB_PATH}")
    print(f"           events: 1 row  |  track_points: {count} rows")
    conn.close()


# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', required=True, choices=['amphan', 'remal'])
    args = parser.parse_args()
    event_id = 'amphan' if args.event == 'amphan' else 'remal'
    print("\n" + "=" * 60)
    print("  STEP 01 — IBTrACS LOADER  ({event_id.capitalize()}, 2020)")
    print("=" * 60 + "\n")

    csv_path = download_ibtracs()
    df = load_amphan(csv_path, event_id)
    lf = find_landfall(df)
    landfall_ts = lf["ts"]

    print(f"\n[CUTOFF]   cutoff_utc = {landfall_ts}")
    print(f"           Justification: cutoff equals landfall time so the hazard")
    print(f"           model uses only pre-landfall + at-landfall track data.")
    print(f"           No post-landfall weakening data is leaked.\n")

    print_track(df, landfall_ts)
    write_geojson(df, landfall_ts, DATA_DIR / f"{event_id}_track.geojson")
    write_sqlite(df, landfall_ts, event_id)

    # Summary
    peak = df.loc[df["wind_kt"].idxmax()]
    print(f"\n[SUMMARY]")
    print(f"  Storm:         {event_id.capitalize()}")
    print(f"  SID:           {df['SID'].iloc[0]}")
    print(f"  Track points:  {len(df)}")
    print(f"  Peak wind:     {peak['wind_kt']:.0f} kt at {str(peak['ISO_TIME']).strip()}")
    print(f"  Min pressure:  {df['pres_hpa'].min():.0f} hPa")
    print(f"  Landfall:      {landfall_ts}  ({lf['lat']:.1f}°N, {lf['lon']:.1f}°E)")
    print(f"  Cutoff:        {landfall_ts}")
    print()


if __name__ == "__main__":
    main()

