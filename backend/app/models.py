"""Pydantic response models for API endpoints."""
from pydantic import BaseModel
from typing import Optional


class EventOut(BaseModel):
    id: str
    name: str
    landfall_utc: str
    cutoff_utc: str
    s1_before_date: Optional[str] = None
    s1_after_date: Optional[str] = None


class TrackPointOut(BaseModel):
    ts: str
    lat: float
    lon: float
    wind_kt: Optional[float] = None
    pres_hpa: Optional[float] = None


class AssetOut(BaseModel):
    id: str
    osm_id: Optional[str] = None
    type: str
    name: Optional[str] = None
    lat: float
    lon: float
    pred_score: Optional[float] = None
    pred_exposed: Optional[int] = None
    obs_exposed: Optional[int] = None


class ExposureSummary(BaseModel):
    district_id: str
    district_name: str
    hospitals_predicted: int = 0
    hospitals_observed: int = 0
    road_km_predicted: float = 0.0
    road_km_observed: float = 0.0
    substations_predicted: int = 0
    substations_observed: int = 0
    shelters_predicted: int = 0
    shelters_observed: int = 0
    population_predicted: int = 0
    population_observed: int = 0


class MetricsOut(BaseModel):
    scope: str
    iou: Optional[float] = None
    precision_: Optional[float] = None
    recall: Optional[float] = None
    auc: Optional[float] = None
    baseline_iou: Optional[float] = None


class AdvisoryOut(BaseModel):
    district_id: str
    event_id: str
    lang: str
    content: dict
    verified: bool
    status: str = "Drafted - not sent"


class AskResponse(BaseModel):
    question: str
    answer: str
    sources: list[str] = []
