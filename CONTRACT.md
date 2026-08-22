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
data/connectors.json   the 6 HAND-AUTHORED links only (auto-snap is not written here): [{"a":<osm_node_id>,"b":<osm_node_id>,"name":"Myer->Emporium"}, ...]
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
connector  bool   True for a synthetic link, i.e. an edge with no OSM way behind it.
                  1060 of the 1066 are build_graph.autosnap output (a straight line
                  between two mapped nodes <=15 m apart, same storey, <=2 per endpoint);
                  only 6 come from connectors.json. `name` is set on the hand-authored
                  six and None on the automatic ones -- that is how to tell them apart.
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
  "weather": {"temperature":32.0,"apparent_temperature":31.8,"direct_radiation":663.0,
              "diffuse_radiation":104.0,"cloud_cover":0.0,"uv_index":7.7,"precipitation":0.0,
              "wind_speed":11.2,"wind_speed_ms":3.11,"relative_humidity":27.6,
              "rh_is_fallback":false,"vapour_pressure_hpa":13.08,"direct_fraction":0.864,
              "w_heat":2.033,"w_wet":0.0,"ta_bias_offset":-0.858,
              "bias_mode":"season-shape[DJF]+level","temperature_raw":31.1,
              "apparent_temperature_raw":30.9,"relative_humidity_raw":29.0,
              "source":"open-meteo archive 2026-01-26 | 2026-01-26 16:00 Australia/Melbourne"},
  "hour": 16
}
```
`summary` = `{"distance_m":1355,"sun_m":94,"sun_pct":7.0,"indoor_m":209,"indoor_pct":15.5,"minutes":16.3,"heat_load":41.2,"climb_m":4.0,"doors":0}`
  `climb_m` is metres of stairs climbed, escalators excluded (they carry you); `doors` is
  indoor↔outdoor transitions, the thing `cost.DOOR_PENALTY_M` charges for. **`minutes`
  walks the EQUIVALENT length**, i.e. plan distance plus the climb converted by Naismith's
  rule, so it is the duration the router was actually minimising rather than distance/speed.
`segments` = `[{"coords":[[lon,lat],...],"indoor":true,"covered":false,"shade":0.9,"length":42.1}, ...]`
  Consecutive edges with the same (indoor|covered) flag are merged into one segment
  so the frontend can style indoor runs as one dashed line.

### `avoided` — and which of its two numbers the UI leads with

With `?engine=utci` the response carries a top-level `avoided` block (also copied to
`routes.shaded.summary.avoided`), from `cost.compare_thermal`:

```json
"avoided": {"stress_load_avoided": 10.9, "stress_load_avoided_pct": 12.2,
            "heat_stress_avoided": 10.9, "heat_stress_avoided_pct": 12.2,
            "cold_stress_avoided": 0.0,  "cold_stress_avoided_pct": null,
            "extra_m": 63.4, "extra_s": 47.0}
```

(a real response: Melbourne Central → Federation Square, 17:00, `?engine=utci`.)

**Lead with `stress_load_avoided` — the °C·minutes. The percentage is the subtitle.**
Measured over 40 CBD pairs in one process on one weather payload, summer, K = 0.10,
door 45 m — `python scripts/bench_hours.py`:

```
hour     baseline   avoided   avoided
         degC-min  degC-min       pct
  10         25.6      14.9     58.2%     <- the percentage peaks here
  13         91.1      38.7     42.5%     <- the dose peaks here
  17        160.4      29.7     18.5%     <- the CITY peaks here
```

The percentage is a ratio to a denominator that is itself moving. At 10:00 the baseline
walk carries 25.6 °C·min of stress and the route removes 58% of it; at 17:00 the baseline
carries 160.4 and the route removes 18.5% — **twice the dose, a third of the percentage**.
Late sun is low, the whole city is in long shadow, and the shortest path is already
shaded, so there is proportionally less left to win exactly when the walk is worst. A demo
driven off the percentage looks weakest at the hour heat risk is highest, for a reason
that is not about the engine.

`stress_load` is °C outside the 9–26 °C no-thermal-stress band integrated over the minutes
you are exposed to them — a dose. It is additive, it is zero when there is nothing to
avoid, it counts cold in winter unchanged, and it is what the cost function minimises.
`*_pct` is `null` inside the comfort band rather than a suspicious `0.0`, so the UI must
handle null before it can render a percentage at all.

`GET /weather?hour=16&mode=summer` -> just the `weather` block above.

`temperature` / `apparent_temperature` / `relative_humidity` are **bias-corrected**;
`*_raw` are what Open-Meteo actually returned. `ta_bias_offset` is the degrees C
subtracted (it can be negative — the correction lifts most hours) and `bias_mode` names
what was applied: `"season-shape[DJF]+level"` by default, `"season-shape[DJF]"` with
`SHADEME_BIAS_LEVEL=0`, `"off (SHADEME_BIAS=0)"` when disabled, `"unavailable ..."` when
the fit has not been run. Display the corrected values; quote `bias_mode` next to any
temperature that leaves the app. See ENGINE_CONTRACT.md "Open-Meteo diurnal bias".
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
CONFIG THESE FOUR LINES WERE MEASURED UNDER: the **legacy** cost path
(`?engine=legacy`, `W_heat`), against the **legacy** shade set in `out/` — flat 8 m
crowns, `RAY_STEP = 1.0`, crown blocks 0.7. They are NOT comparable with anything the
UTCI engine reports, which reads `out/v2/`. Re-measuring them against the shipped
rasters would move them. Do not mix the two on one slide.

Shade masks: `out/` is the LEGACY set (`scripts/precompute.py`) and exists only as the
bottom rung of `scripts/bench_shade_ladder.py`. The SHIPPED set the engine reads is
`out/v2/`, from `python scripts/regen_shade_v2.py YYYY-MM-DD`, which also writes the
`.png` overlays served by `/shade/{hour}.png`.
Winter demo day: today, via forecast API.
NOTE: the plan's "+12% / -68%" headline came from a hardcoded W=3, not from real
weather. Do not quote it. Quote the h14 numbers above, which are reproducible.

## Every reported figure carries its config

`GET /route` returns `meta.provenance`; `GET /provenance` returns the full stamp;
`python scripts/provenance.py --line` prints it. Graph hash, shade raster set digest,
SVF raster, K, INDOOR_TA, TAU_LEAF, RAY_STEP, beam convention, weather day, commit.
A figure quoted without that line is a number against a moving reference — see the
Provenance section of ENGINE_CONTRACT.md for why this rule exists.

## Demo route that MUST work
Melbourne Central (-37.81001, 144.96280) -> Federation Square (-37.81800, 144.96910)

At 14:00, `?engine=utci`, under
`graph 90ac4f4f79d3 (49065n/60868e) · shade v2 a6c381d74f68 · svf svf_veg.npy 1039ee7ed6f9
· K=0.1 · INDOOR_TA=22.5 · TAU_LEAF=0.03 RAY_STEP=0.25 beam=hypot · day 2026-01-26 · @9e8657a`:

```
                 dist     indoor              outdoor           whole route
shortest       1202 m    97 m @ 22.3 C   1105 m @ 32.88 C      32.03 C
shade-aware    1271 m   792 m @ 22.3 C    480 m @ 32.13 C      26.00 C
```

READ THIS BEFORE QUOTING THE 6.03 C: the outdoor stretches differ by **0.75 C**. That
is the entire contribution of the shade model. The other 5.3 C is air conditioning —
`INDOOR_TA = 22.5`, a stated assumption, not a measurement. Stage 04 of the roadmap
exists to find a pair whose benefit survives switching indoor routing off.
