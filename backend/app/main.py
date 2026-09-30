"""FastAPI application entry point."""
import json
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Optional

from app.config import DATA_DIR, DB_PATH
from app.database import get_connection, init_db
from app.models import (
    EventOut, TrackPointOut, MetricsOut, AdvisoryOut, AskResponse
)

from fastapi.staticfiles import StaticFiles
import os

app = FastAPI(
    title="Cyclone Impact & Infrastructure Vulnerability Forecaster",
    version="0.1.0",
    description="Hindcast system for cyclone impact assessment using real satellite and meteorological data.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the Next.js static export if running in the single container
if os.path.exists("/app/frontend_static"):
    app.mount("/", StaticFiles(directory="/app/frontend_static", html=True), name="frontend")
elif os.path.exists(Path(__file__).resolve().parent.parent.parent / "frontend" / "out"):
    app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent.parent.parent / "frontend" / "out"), html=True), name="frontend")



@app.on_event("startup")
def startup():
    init_db()


# ── Events ─────────────────────────────────────────────────────────────

@app.get("/events", response_model=list[EventOut])
def list_events():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM events").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Event not found")
    return dict(row)


# ── Track ──────────────────────────────────────────────────────────────

@app.get("/events/{event_id}/track")
def get_track(event_id: str):
    """Return track as GeoJSON FeatureCollection."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT ts, lat, lon, wind_kt, pres_hpa FROM track_points WHERE event_id = ? ORDER BY ts",
        (event_id,)
    ).fetchall()
    conn.close()
    if not rows:
        raise HTTPException(404, "No track data for this event")

    features = []
    coords = []
    for r in rows:
        coords.append([r["lon"], r["lat"]])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "ts": r["ts"],
                "wind_kt": r["wind_kt"],
                "pres_hpa": r["pres_hpa"],
            }
        })

    # Add line feature for the track
    features.insert(0, {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"type": "track_line"}
    })

    return {"type": "FeatureCollection", "features": features}


# ── Hazard raster (as GeoJSON vectorised) ─────────────────────────────

@app.get("/events/{event_id}/hazard")
def get_hazard(event_id: str):
    """Return predicted hazard zones as GeoJSON."""
    path = DATA_DIR / f"{event_id}_hazard.geojson"
    if not path.exists():
        raise HTTPException(404, "Hazard data not yet generated")
    return JSONResponse(json.loads(path.read_text()))


# ── Observed flood ────────────────────────────────────────────────────

@app.get("/events/{event_id}/observed")
def get_observed(event_id: str):
    """Return observed Sentinel-1 flood mask as GeoJSON."""
    path = DATA_DIR / f"{event_id}_flood_observed.geojson"
    if not path.exists():
        raise HTTPException(404, "Observed flood data not yet generated")
    return JSONResponse(json.loads(path.read_text()))


# ── Exposure ──────────────────────────────────────────────────────────

@app.get("/events/{event_id}/exposure")
def get_exposure(event_id: str, district: Optional[str] = Query(None)):
    """Return exposed assets as GeoJSON."""
    conn = get_connection()
    query = """
        SELECT a.id, a.osm_id, a.type, a.name, a.geom, a.district_id,
               e.pred_score, e.pred_exposed, e.obs_exposed
        FROM assets a
        LEFT JOIN exposure e ON a.id = e.asset_id AND e.event_id = ?
        WHERE 1=1
    """
    params = [event_id]
    if district:
        query += " AND a.district_id = ?"
        params.append(district)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    features = []
    for r in rows:
        geom = json.loads(r["geom"]) if isinstance(r["geom"], str) else r["geom"]
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": r["id"],
                "osm_id": r["osm_id"],
                "type": r["type"],
                "name": r["name"],
                "district_id": r["district_id"],
                "pred_score": r["pred_score"],
                "pred_exposed": r["pred_exposed"],
                "obs_exposed": r["obs_exposed"],
            }
        })

    return {"type": "FeatureCollection", "features": features}


# ── Metrics ───────────────────────────────────────────────────────────

@app.get("/events/{event_id}/metrics", response_model=list[MetricsOut])
def get_metrics(event_id: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT scope, iou, precision_, recall, auc, baseline_iou FROM metrics WHERE event_id = ?",
        (event_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Advisory ──────────────────────────────────────────────────────────

class AdvisoryRequest(BaseModel):
    event_id: str
    district_id: str
    lang: str = "en"


@app.post("/advisory", response_model=AdvisoryOut)
async def generate_advisory(req: AdvisoryRequest):
    """Generate or retrieve a Gemini advisory for a district."""
    conn = get_connection()
    # Check for existing
    row = conn.execute(
        "SELECT * FROM advisories WHERE event_id = ? AND district_id = ? AND lang = ?",
        (req.event_id, req.district_id, req.lang)
    ).fetchone()

    if row:
        conn.close()
        return AdvisoryOut(
            district_id=row["district_id"],
            event_id=row["event_id"],
            lang=row["lang"],
            content=json.loads(row["json_content"]),
            verified=bool(row["verified"]),
        )

    # Generate new advisory (import here to avoid circular / heavy imports)
    from pipeline.step08_advisory import generate_advisory_for_district
    result = await generate_advisory_for_district(req.event_id, req.district_id, req.lang)
    conn.close()

    return AdvisoryOut(
        district_id=req.district_id,
        event_id=req.event_id,
        lang=req.lang,
        content=result["content"],
        verified=result["verified"],
    )


# ── Ask endpoint ──────────────────────────────────────────────────────

class AskRequest(BaseModel):
    event_id: str
    question: str


@app.post("/ask", response_model=AskResponse)
async def ask_question_endpoint(req: AskRequest):
    """Answer natural-language questions strictly from the database."""
    from pipeline.step08_advisory import answer_question
    result = await answer_question(req.event_id, req.question)
    return result


# ── Health ────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "db": str(DB_PATH)}

# Serve the Next.js static export at root level (must be at the bottom)
if os.path.exists('/app/frontend_static'):
    app.mount('/', StaticFiles(directory='/app/frontend_static', html=True), name='frontend')
elif os.path.exists(str(Path(__file__).resolve().parent.parent.parent / 'frontend' / 'out')):
    app.mount('/', StaticFiles(directory=str(Path(__file__).resolve().parent.parent.parent / 'frontend' / 'out'), html=True), name='frontend')
