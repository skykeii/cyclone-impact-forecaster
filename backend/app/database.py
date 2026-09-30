"""SQLite database schema and helpers."""
import sqlite3
import json
from pathlib import Path
from app.config import DB_PATH

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    landfall_utc TEXT NOT NULL,
    cutoff_utc  TEXT NOT NULL,
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
    geom TEXT  -- GeoJSON geometry string
);

CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    osm_id      TEXT,
    type        TEXT NOT NULL,
    name        TEXT,
    geom        TEXT NOT NULL,  -- GeoJSON geometry string
    district_id TEXT REFERENCES districts(id)
);

CREATE TABLE IF NOT EXISTS exposure (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id     TEXT NOT NULL REFERENCES assets(id),
    event_id     TEXT NOT NULL REFERENCES events(id),
    pred_score   REAL,
    pred_exposed INTEGER,  -- 0 or 1
    obs_exposed  INTEGER   -- 0 or 1
);

CREATE TABLE IF NOT EXISTS metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL REFERENCES events(id),
    scope        TEXT NOT NULL,  -- 'pixel' or asset class name
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
    lang         TEXT NOT NULL,  -- 'en' or 'bn'
    json_content TEXT NOT NULL,
    verified     INTEGER DEFAULT 0  -- 0 or 1
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a connection to the SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    print(f"[DB] Initialized database at {DB_PATH}")


def insert_event(conn: sqlite3.Connection, event: dict):
    """Insert or replace an event row."""
    conn.execute(
        "INSERT OR REPLACE INTO events (id, name, landfall_utc, cutoff_utc, s1_before_date, s1_after_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event["id"], event["name"], event["landfall_utc"], event["cutoff_utc"],
         event.get("s1_before_date"), event.get("s1_after_date"))
    )


def insert_track_points(conn: sqlite3.Connection, event_id: str, points: list[dict]):
    """Bulk insert track points."""
    conn.executemany(
        "INSERT INTO track_points (event_id, ts, lat, lon, wind_kt, pres_hpa) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(event_id, p["ts"], p["lat"], p["lon"], p.get("wind_kt"), p.get("pres_hpa"))
         for p in points]
    )


def insert_asset(conn: sqlite3.Connection, asset: dict):
    """Insert or ignore an asset."""
    conn.execute(
        "INSERT OR IGNORE INTO assets (id, osm_id, type, name, geom, district_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (asset["id"], asset.get("osm_id"), asset["type"], asset.get("name"),
         json.dumps(asset["geom"]) if isinstance(asset["geom"], dict) else asset["geom"],
         asset.get("district_id"))
    )


def insert_exposure(conn: sqlite3.Connection, exposure: dict):
    """Insert an exposure record."""
    conn.execute(
        "INSERT INTO exposure (asset_id, event_id, pred_score, pred_exposed, obs_exposed) "
        "VALUES (?, ?, ?, ?, ?)",
        (exposure["asset_id"], exposure["event_id"], exposure.get("pred_score"),
         exposure.get("pred_exposed"), exposure.get("obs_exposed"))
    )


def insert_metrics(conn: sqlite3.Connection, m: dict):
    """Insert a metrics record."""
    conn.execute(
        "INSERT INTO metrics (event_id, scope, iou, precision_, recall, auc, baseline_iou) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (m["event_id"], m["scope"], m.get("iou"), m.get("precision_"),
         m.get("recall"), m.get("auc"), m.get("baseline_iou"))
    )


def insert_advisory(conn: sqlite3.Connection, adv: dict):
    """Insert an advisory record."""
    conn.execute(
        "INSERT INTO advisories (district_id, event_id, lang, json_content, verified) "
        "VALUES (?, ?, ?, ?, ?)",
        (adv.get("district_id"), adv["event_id"], adv["lang"],
         adv["json_content"] if isinstance(adv["json_content"], str) else json.dumps(adv["json_content"]),
         adv.get("verified", 0))
    )


if __name__ == "__main__":
    init_db()
