"use client";

import { useEffect, useState, useMemo } from "react";
import Map, { Source, Layer, LayerProps } from "react-map-gl/maplibre";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Info, Map as MapIcon, ShieldAlert, BarChart3, ChevronDown, CheckCircle, AlertTriangle } from "lucide-react";

if (typeof window !== "undefined" && maplibregl.setWorkerUrl) {
  maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");
}

// The FastAPI backend URL
const API_URL = typeof window !== 'undefined' && window.location.hostname !== 'localhost' ? "" : "http://localhost:8000";
const EVENT_ID = "amphan";

const HAZARD_LAYER: LayerProps = {
  id: "hazard-grid",
  type: "fill",
  paint: {
    "fill-color": [
      "interpolate",
      ["linear"],
      ["get", "hazard_score"],
      0.0, "rgba(255, 255, 255, 0)",
      0.2, "rgba(255, 237, 160, 0.4)",
      0.5, "rgba(254, 178, 76, 0.6)",
      0.8, "rgba(240, 59, 32, 0.7)",
      1.0, "rgba(189, 0, 38, 0.8)",
    ],
    "fill-outline-color": "rgba(255, 255, 255, 0.1)",
  },
};

const OBSERVED_LAYER: LayerProps = {
  id: "observed-flood",
  type: "fill",
  paint: {
    "fill-color": "rgba(0, 150, 255, 0.7)",
    "fill-outline-color": "rgba(0, 100, 255, 1)",
  },
};

const TRACK_LAYER: LayerProps = {
  id: "track-line",
  type: "line",
  filter: ["==", "type", "track_line"],
  paint: {
    "line-color": "#ffffff",
    "line-width": 3,
    "line-dasharray": [2, 2],
  },
};

const TRACK_POINTS_LAYER: LayerProps = {
  id: "track-points",
  type: "circle",
  filter: ["!=", "type", "track_line"],
  paint: {
    "circle-radius": 5,
    "circle-color": "#e11d48",
    "circle-stroke-width": 2,
    "circle-stroke-color": "#ffffff",
  },
};

export default function ForecasterApp() {
  const [showObserved, setShowObserved] = useState(false);
  
  // Data
  const [trackData, setTrackData] = useState<any>(null);
  const [hazardData, setHazardData] = useState<any>(null);
  const [observedData, setObservedData] = useState<any>(null);
  const [metrics, setMetrics] = useState<any[]>([]);
  
  // District
  const [selectedDistrict, setSelectedDistrict] = useState("south_24_parganas");
  const [advisory, setAdvisory] = useState<any>(null);
  const [loadingAdvisory, setLoadingAdvisory] = useState(false);

  useEffect(() => {
    // Fetch base layers
    fetch(`${API_URL}/events/${EVENT_ID}/track`).then(r => r.json()).then(setTrackData);
    fetch(`${API_URL}/events/${EVENT_ID}/hazard`).then(r => r.json()).then(setHazardData);
    fetch(`${API_URL}/events/${EVENT_ID}/observed`).then(r => r.json()).then(setObservedData);
    fetch(`${API_URL}/events/${EVENT_ID}/metrics`).then(r => r.json()).then(setMetrics);
  }, []);

  useEffect(() => {
    // Fetch advisory when district changes
    setLoadingAdvisory(true);
    fetch(`${API_URL}/advisory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_id: EVENT_ID, district_id: selectedDistrict, lang: "en" })
    })
      .then(r => r.json())
      .then(data => {
        setAdvisory(data);
        setLoadingAdvisory(false);
      })
      .catch(() => setLoadingAdvisory(false));
  }, [selectedDistrict]);

  const pixelMetrics = metrics.find(m => m.scope === "pixel") || {};

  return (
    <div className="flex h-screen w-full bg-slate-900 text-slate-100 overflow-hidden font-sans">
      
      {/* LEFT PANEL */}
      <div className="w-96 flex flex-col h-full bg-slate-800 border-r border-slate-700 shadow-xl z-10">
        
        {/* BANNER */}
        <div className="bg-amber-500/20 border-b border-amber-500/50 p-4">
          <div className="flex items-center gap-2 text-amber-500 mb-1">
            <ShieldAlert size={18} />
            <h1 className="font-bold text-sm tracking-wider uppercase">Hindcast Prototype</h1>
          </div>
          <p className="text-xs text-amber-200/80 leading-tight">
            Not a live forecast. Retrospective model using pre-landfall data scored against actual post-event Sentinel-1 satellite observations.
          </p>
        </div>

        {/* HEADER */}
        <div className="p-5 border-b border-slate-700">
          <h2 className="text-2xl font-bold text-white mb-1">Cyclone Amphan</h2>
          <p className="text-sm text-slate-400">Impact & Vulnerability Platform</p>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          
          {/* REVEAL TOGGLE */}
          <div className="bg-slate-900/50 rounded-lg p-4 border border-slate-700">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <MapIcon size={16} className="text-slate-400" />
                <span className="font-semibold text-sm">Ground Truth Toggle</span>
              </div>
            </div>
            <p className="text-xs text-slate-400 mb-4 leading-relaxed">
              Compare our pre-landfall hazard screening against actual flood patterns observed by satellites after the storm.
            </p>
            <button
              onClick={() => setShowObserved(!showObserved)}
              className={`w-full py-2.5 px-4 rounded-md font-medium text-sm transition-all duration-300 ${
                showObserved 
                  ? "bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-500/20" 
                  : "bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-600"
              }`}
            >
              {showObserved ? "Hide Satellite Flood Layer" : "Reveal Satellite Flood Layer"}
            </button>
          </div>

          {/* ACCURACY METRICS */}
          <div className="bg-slate-900/50 rounded-lg p-4 border border-slate-700">
            <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
              <BarChart3 size={16} className="text-slate-400" /> Validation Metrics
            </h3>
            <div className="grid grid-cols-2 gap-3 mb-2">
              <div className="bg-slate-900 rounded p-3 border border-slate-800">
                <div className="text-xs text-slate-500 mb-1">Pixel IoU (Pre vs Obs)</div>
                <div className="text-lg font-bold text-emerald-400">
                  {pixelMetrics.iou ? pixelMetrics.iou.toFixed(4) : "—"}
                </div>
              </div>
              <div className="bg-slate-900 rounded p-3 border border-slate-800">
                <div className="text-xs text-slate-500 mb-1">Naive Baseline IoU</div>
                <div className="text-lg font-bold text-slate-300">
                  {pixelMetrics.baseline_iou ? pixelMetrics.baseline_iou.toFixed(4) : "—"}
                </div>
              </div>
            </div>
            <p className="text-[11px] text-slate-500 italic">
              * Note: Screening model IoU is expected to be low against raw satellite pixels due to radar noise and resolution mismatch.
            </p>
          </div>

          {/* DISTRICT PANEL */}
          <div className="space-y-3">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-semibold text-sm flex items-center gap-2">
                <Info size={16} className="text-slate-400"/> AI Advisory
              </h3>
            </div>
            
            <div className="relative">
              <select 
                className="w-full bg-slate-900 border border-slate-700 text-slate-200 text-sm rounded-md p-2.5 appearance-none focus:outline-none focus:border-blue-500"
                value={selectedDistrict}
                onChange={(e) => setSelectedDistrict(e.target.value)}
              >
                <option value="south_24_parganas">South 24 Parganas</option>
                <option value="kolkata">Kolkata</option>
                <option value="east_medinipur">East Medinipur</option>
                <option value="north_24_parganas">North 24 Parganas</option>
                <option value="howrah">Howrah</option>
              </select>
              <ChevronDown size={14} className="absolute right-3 top-3 text-slate-400 pointer-events-none" />
            </div>

            {loadingAdvisory ? (
              <div className="p-4 bg-slate-900/50 rounded-md border border-slate-800 text-sm text-slate-400 animate-pulse">
                Synthesizing hazard data & cross-checking facts...
              </div>
            ) : advisory?.content ? (
              <div className="border border-slate-700 rounded-lg overflow-hidden bg-slate-900/30">
                <div className="bg-amber-600/30 border-b border-amber-600/40 p-3 flex justify-between items-center">
                  <span className="font-bold text-xs uppercase tracking-wider text-amber-300">
                    {advisory.content.threat_level || "MODERATE"} THREAT
                  </span>
                  {advisory.verified && (
                    <span className="flex items-center gap-1 text-[10px] text-emerald-400 font-semibold bg-emerald-950/80 px-2 py-0.5 rounded border border-emerald-500/30">
                      <CheckCircle size={10} /> FACT CHECKED
                    </span>
                  )}
                </div>
                <div className="p-4 space-y-4">
                  <p className="text-xs text-slate-300 leading-relaxed font-sans">
                    {advisory.content.threat_summary}
                  </p>
                  
                  {advisory.content.critical_actions?.length > 0 && (
                    <div>
                      <h4 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">Critical Actions</h4>
                      <ul className="text-xs space-y-1 text-slate-300 list-disc list-inside">
                        {advisory.content.critical_actions.map((act: string, i: number) => (
                          <li key={i}>{act}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {advisory.content.key_assets_at_risk?.length > 0 && (
                    <div>
                      <h4 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-2">At Risk</h4>
                      <div className="flex flex-wrap gap-1.5">
                        {advisory.content.key_assets_at_risk.map((asset: any, i: number) => (
                          <span key={i} className="bg-slate-800 border border-slate-700 px-2 py-1 rounded text-[10px] text-slate-300">
                            {asset.name}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* MAP AREA */}
      <div className="flex-1 relative">
        <Map
          mapLib={maplibregl}
          initialViewState={{
            longitude: 88.3,
            latitude: 22.3,
            zoom: 7.5
          }}
          mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
        >
          {/* HAZARD LAYER */}
          {hazardData && (
            <Source id="hazard" type="geojson" data={hazardData}>
              <Layer {...HAZARD_LAYER} />
            </Source>
          )}

          {/* OBSERVED FLOOD LAYER */}
          {showObserved && observedData && (
            <Source id="observed" type="geojson" data={observedData}>
              <Layer {...OBSERVED_LAYER} />
            </Source>
          )}

          {/* TRACK LAYER */}
          {trackData && (
            <Source id="track" type="geojson" data={trackData}>
              <Layer {...TRACK_LAYER} />
              <Layer {...TRACK_POINTS_LAYER} />
            </Source>
          )}
        </Map>

        {/* MAP LEGEND */}
        <div className="absolute bottom-6 right-6 bg-slate-900/90 p-4 rounded-lg border border-slate-700 shadow-2xl backdrop-blur-sm">
          <h4 className="text-xs font-bold text-slate-300 uppercase mb-3">Legend</h4>
          
          <div className="space-y-3 text-xs text-slate-400">
            <div>
              <div className="mb-1">Predicted Hazard Score</div>
              <div className="flex h-3 w-40 rounded overflow-hidden">
                <div className="flex-1 bg-[rgba(255,237,160,0.4)]"></div>
                <div className="flex-1 bg-[rgba(254,178,76,0.6)]"></div>
                <div className="flex-1 bg-[rgba(240,59,32,0.7)]"></div>
                <div className="flex-1 bg-[rgba(189,0,38,0.8)]"></div>
              </div>
              <div className="flex justify-between mt-1 text-[10px]">
                <span>Low (0.2)</span>
                <span>High (1.0)</span>
              </div>
            </div>

            {showObserved && (
              <div className="flex items-center gap-2 pt-2 border-t border-slate-700">
                <div className="w-4 h-4 bg-[rgba(0,150,255,0.7)] border border-blue-500 rounded-sm"></div>
                <span>Actual Flooding (Sentinel-1)</span>
              </div>
            )}
            
            <div className="flex items-center gap-2">
              <div className="w-4 h-4 rounded-full bg-rose-600 border border-white flex items-center justify-center">
                <div className="w-full h-[2px] bg-white border-dashed"></div>
              </div>
              <span>Cyclone Track</span>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
