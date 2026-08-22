# ShadeMe — frozen interfaces

Everything below is FIXED. Do not change a field name without saying so loudly.
Three workstreams build against this in parallel.

## Artefacts on disk (produced by Phase 1/2, consumed by Phase 3)

```
out/grid.json          {"bounds":[minx,miny,maxx,maxy],"cell":2.0,"w":W,"h":H}  MGA55 metres
out/shade_HH.npy       float32 (H,W) in [0,1]. 1.0 = fully shaded. HH = 06..20 local
out/shade_HH.png       RGBA overlay for the web map, same extent as grid bounds
out/shade_bounds.json  {"west":lon,"south":lat,"east":lon,"north":lat}  WGS84, for MapLibre image overlay
out/graph.pkl          pickled networkx.Graph (see edge schema below)
data/connectors.json   hand-authored indoor links: [{"a":<osm_node_id>,"b":<osm_node_id>,"name":"Myer->Emporium"}, ...]
```

### Graph schema (networkx.Graph, undirected)
node key = OSM node id (int). Node attrs: `ll` = (lon,lat), `xy` = (x,y) MGA55 metres.
Edge attrs:
```
length     float  metres
indoor     bool
covered    bool
crossing   bool
arterial   bool
steps      bool
shade      dict[int,float]   hour(local 6..20) -> shade in [0,1]   <-- CHANGED from Phase 0 scalar
connector  bool   True if hand-authored in connectors.json
```

## HTTP API (server/main.py, uvicorn on :8000)

`GET /route?from_lat&from_lon&to_lat&to_lon&hour=16&mode=summer&compare=true`
- `mode`: `summer` | `winter` | `shortest`
- `hour`: 6..20 local. Optional; defaults to the demo-day hour.

Response:
```json
{
  "routes": {
    "shaded":   {"geojson": <LineString Feature>, "summary": {...}, "segments": [...]},
    "shortest": {"geojson": <LineString Feature>, "summary": {...}, "segments": [...]}
  },
  "weather": {"apparent_temperature":31.2,"direct_radiation":558,"diffuse_radiation":160,
              "cloud_cover":3,"uv_index":7.2,"precipitation":0.0,"wind_speed":14.0,
              "direct_fraction":0.78,"w_heat":2.6,"w_wet":0.0,"source":"demo-day 2026-01-14"},
  "hour": 16
}
```
`summary` = `{"distance_m":1355,"sun_m":94,"sun_pct":7.0,"indoor_m":209,"indoor_pct":15.5,"minutes":16.3,"heat_load":41.2}`
`segments` = `[{"coords":[[lon,lat],...],"indoor":true,"covered":false,"shade":0.9,"length":42.1}, ...]`
  Consecutive edges with the same (indoor|covered) flag are merged into one segment
  so the frontend can style indoor runs as one dashed line.

`GET /weather?hour=16&mode=summer` -> just the `weather` block above.
`GET /shade/{hour}.png` -> static shade overlay (or serve out/ as /static).
`GET /places` -> `[{"name":"Melbourne Central","lat":-37.81001,"lon":144.96280}, ...]`
`GET /health` -> `{"ok":true,"edges":N,"hours":[6..20]}`

CORS: allow all origins (hackathon).

## Weather weights
```
W_heat = clamp(0, 3, (apparent_temperature - 20) / 5)      # 0 at 20C, 3 at 35C
W_heat *= direct_fraction                                   # direct/(direct+diffuse); clouds kill shade value
W_wet  = clamp(0, 3, precipitation * 2 + wind_speed / 15)   # winter mode
W_cross = 0.4 on arterial crossings, 0.15 otherwise
```
Cost: `length * (1 + W_heat*sun_load + W_wet*exposed + W_cross*is_crossing)`
where `sun_load = (direct_radiation/1000) * (1 - shade)`, and indoor edges have
`sun_load = 0, exposed = 0`. Cap detour at 1.4x shortest.

DEMO DAY: **2026-01-26** via Open-Meteo **archive** API (env `SHADEME_SUMMER_DATE`).
Chosen because the hour slider alone flips the advice, verified end-to-end:
```
h09  20.0C f0.48 W_heat 0.00 -> SAME ROUTE (not hot, diffuse light)
h12  29.2C f0.88 W_heat 1.61 -> +18.7% dist, -72.7% sun, 44% indoor
h14  32.0C f0.88 W_heat 2.12 -> +18.0% dist, -76.6% sun
h18  30.5C f0.79 W_heat 1.66 -> SAME ROUTE (still hot, but low sun already
                                shades the direct route: nothing left to buy)
```
Shade masks in out/ are precomputed for this same date -- regenerate with
`python scripts/precompute.py YYYY-MM-DD` if the demo day changes.
Winter demo day: today, via forecast API.
NOTE: the plan's "+12% / -68%" headline came from a hardcoded W=3, not from real
weather. Do not quote it. Quote the h14 numbers above, which are reproducible.

## Demo route that MUST work
Melbourne Central (-37.81001, 144.96280) -> Federation Square (-37.81800, 144.96910)
