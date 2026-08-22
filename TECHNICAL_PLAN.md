# ShadeMe — weather-aware pedestrian routing for Melbourne

**Track:** Climate Action · **Deadline:** Mon 24 Aug, 11:59pm AEST

> Melbourne has a second, hidden pedestrian network — arcades, subways and building
> pass-throughs — that Google Maps ignores. We route you through it based on the weather.

**Thesis (two legs, both in-track):**
- **Summer = adaptation.** SDG 13.1, "strengthen resilience and adaptive capacity to
  climate-related hazards." Heat is Australia's deadliest natural hazard.
- **Winter = mitigation.** People Uber a 900m trip when walking is miserable. Making
  walking tolerable year-round is mode shift, and mode shift is emissions reduction.

Do **not** pitch this as "a shade app". Shade is one input to a cost function.

---

## Prior art — cite it, don't hide it

| | What it does | Gap we exploit |
|---|---|---|
| **shademap.app** | Geometric shadow sim from terrain + OSM buildings | No clouds, no heat, no trees, no routing |
| **Cool Routes** (ASU SHaDE Lab, Jun 2026) | MRT-based routing, hourly forecasts, real | Built for **Tempe AZ** — clear-sky climate, no arcade network, states citywide sim takes *hours* |

Say in the pitch: *"ASU proved the mechanism — 70% of trips found a cooler route, 4.5°F
average reduction. Here's why it doesn't transfer to Melbourne."* Prior art that validates
your problem is an asset. Judges who find it after you hid it will end you.

---

## Verified data sources

All confirmed working. No API keys anywhere.

| Source | Endpoint / ID | Notes |
|---|---|---|
| Building footprints + **height** | `building-outlines-2015` | 20,861 polys, **100% have height** via `ovlhgt_ahd - base_ahd`. Max 298m (Eureka). 19MB geojson, 12s download |
| Tree canopy | `tree-canopies-2021-urban-forest` | 57,980 **polygons** (not points) |
| Indoor / covered ways | Overpass API | **1,181 ways** in CBD: 338 indoor corridors, 135 building_passage, 120 covered. Melbourne Central, Royal Arcade, Degraves + Elizabeth St Subways all mapped |
| Live weather + radiation | Open-Meteo | `direct_radiation`, `diffuse_radiation`, `cloud_cover`, `apparent_temperature`, `uv_index`, plus archive API for demo days |
| Pedestrian graph | Overpass (**not** CoM) | CoM `pedestrian-network` is geometry-only — no attributes. OSM carries indoor/covered/crossing/steps tags |
| Drinking fountains | `drinking-fountains` | 332. Stretch goal |

CoM base: `https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{id}/exports/geojson`

**Not available — confirmed:** pedestrian signal timing. data.vic returns 0 results for
SCATS / traffic signals. Proxy it with a fixed delay penalty on `highway=crossing` nodes,
scaled by the road class crossed. ~20 min of work, do not claim it's signal-timed.

---

## The core algorithm

The only genuinely hard part. Naive shadow casting is 85k edges x 20k buildings = 1.7
billion point-in-polygon tests. **Don't compute shadow polygons.** Rasterise and sweep.

### Ratti–Richens shadow sweep

Burn building + canopy heights into a 2m grid (DSM). For a sun azimuth/elevation, march
toward the sun taking a running max of `height - distance * tan(elevation)`. Ground cell
is shadowed where that exceeds zero.

```python
import numpy as np

def shadow_mask(dsm, cell, az_deg, el_deg, max_h=300.0):
    """dsm: 2D float32 heights (m). Returns bool mask, True = in shadow."""
    if el_deg < 5.0:
        return np.ones_like(dsm, dtype=bool)      # sun too low, all shadow
    az, el = np.radians(az_deg), np.radians(el_deg)
    dx, dy = np.sin(az), np.cos(az)               # direction TOWARD the sun
    n = int(min(max_h / (np.tan(el) * cell), 500))
    acc = np.zeros_like(dsm)
    for k in range(1, n + 1):
        sx, sy = int(round(k * dx)), int(round(-k * dy))   # row = -lat
        shifted = np.roll(np.roll(dsm, -sy, axis=0), -sx, axis=1)
        acc = np.maximum(acc, shifted - k * cell * np.tan(el))
    return acc > 0.05
```

~2000x1950 grid, ~400 steps, **runs in seconds**. Precompute 24 hourly masks, ship as
static PNGs. Zero runtime compute risk during the demo.

**Gotchas that will cost you an hour each:**
- **Project to metres first.** EPSG:4326 → **EPSG:28355** (MGA zone 55) via `pyproj`.
  Doing the sweep in degrees gives garbage.
- **Buffer the DSM 500m beyond your bbox** or buildings just outside cast no shadow in.
- Clamp negative heights to 0 (min in the data is -0.4m).
- Trees give **dappled** shade — weight canopy at ~0.7 block, buildings 1.0.
- `np.roll` wraps around the array edge. With a 500m buffer the wrap lands in throwaway
  margin, but zero the border if you see artefacts.

### Cost function

```
shade      = 1 - sunlit_fraction(edge)          # sampled from the mask
sun_load   = (direct_radiation / 1000) * (1 - shade)
cost(edge) = length * (1 + W_heat * sun_load
                         + W_wet  * exposed_to_rain
                         + W_cross * is_signalised_crossing)
indoor edge: sun_load = 0, exposed_to_rain = 0
```

`W_heat` from `apparent_temperature` (0 at 20°C, ~3 at 35°C). `W_wet` from
`precipitation` + `wind_speed`. **Cap total detour at 1.4x the shortest path** — nobody
walks 3x further for shade.

### Why the radiation split is the whole trick

You never track individual clouds. Direct beam is the component a shadow blocks; diffuse
comes from the whole sky dome and shade barely touches it. Real Melbourne data:

```
14 Jan 10:00   cloud 100%   direct  88   diffuse 345  →  20% direct   shade ~ pointless
14 Jan 16:00   cloud   3%   direct 558   diffuse 160  →  78% direct   shade ~ everything
```

Same day, six hours apart, **4x swing in the value of shade.** A geometric shade map draws
the identical shadow both times and is confidently wrong. This is the differentiator —
put this table in the video.

---

## Phases

### Phase 0 — PROTOTYPE / GO–NO-GO  (~4h)  ← start here

Three falsifiable checks. If all three pass, build it. If #3 fails, **kill the idea**.

1. **Shadows render.** Fetch buildings, rasterise DSM, run the sweep for 14 Jan 16:00,
   dump a PNG. Eyeball it: Eureka Tower's shadow must be enormous and pointing the
   correct direction for that azimuth. Verify one shadow length by hand
   (`length = height / tan(elevation)`).
2. **Indoor network connects.** Pull OSM indoor ways, build the subgraph, confirm
   Melbourne Central → Emporium → Myer is one connected component.
3. **THE GATE — routing actually changes.** Run A* from Melbourne Central to Federation
   Square with `W_heat = 0`, then `W_heat = 3`. **The two routes must differ.**
   If shade-weighted routing returns the same path as shortest-path, there is no product
   and no amount of polish saves it. Check this before writing any frontend.

### Phase 0 RESULTS — run 22 Aug, ~04:30. **VERDICT: BUILD IT.**

| Check | Result |
|---|---|
| 1. Shadows render + geometrically correct | **PASS** |
| 2. Indoor network connects | **PARTIAL** — see below |
| 3. Shade weighting changes the route | **PASS** |

**Check 1.** 14 Jan 16:00 → sun azimuth 286.1°, elevation 53.6°. Shadow traced from Eureka
Tower at bearing 106° — exactly anti-solar. DSM build 4.6s, shade grid **0.4s** for 6.1M
cells. Renders as recognisable Melbourne: Hoddle Grid, Yarra, canopy shadow along the bank.

> **This changes the plan: shadow compute is 0.4s, not the 5h risk I budgeted.** You do not
> need precomputed PNGs for correctness — you can compute live per request. Still ship
> precomputed hourly frames for the *frontend* slider (instant scrubbing), but the compute
> risk is gone. Reallocate that time to the indoor network.

**Check 3 — the gate, Melbourne Central → Federation Square:**

```
shortest    (W=0)   1206m   299m in sun (24.8%)    97m indoor ( 8.0%)
shade-aware (W=3)   1355m    94m in sun ( 7.0%)   209m indoor (15.5%)

+12.3% distance  ->  -68.5% sun exposure
```

**"12% longer, 68% less sun" is your headline number.** Put it in the video.

**Check 2 — the real remaining work.** 3,151 indoor/covered edges but **270 disconnected
components** (largest 303 nodes). 827/3,217 indoor nodes touch the outdoor network, which
is why routing already uses 209m of indoor without help.

```
Melbourne Central   nearest indoor node   6m   component #0  (303 nodes)
Emporium                                 16m   component #0  (303 nodes)   <- same, connects
Myer                                     22m   component #198 (2 nodes)    <- orphaned
```

Melbourne Central ↔ Emporium **is** a real routable indoor path. Myer is not linked in OSM.
**Action: hand-author ~15–20 connector edges** stitching the major components (Myer, David
Jones, QV, Melbourne Central–Emporium bridge, Degraves/Campbell Arcade). Store as a small
`data/connectors.json` of node-pairs merged at graph build. ~1–2h, legitimate original work,
and it converts 270 islands into the network the pitch describes. **This is now the long
pole, not shadows.**

Working files: `scripts/{config,fetch_data,build_dsm,shadow,fetch_osm,build_graph}.py`,
prototypes `proto_shadow.py` / `proto_route.py` / `proto_indoor_check.py` / `proto_render.py`.

---

### Phase 1 — Shadow engine  (5h)
`fetch_data.py` (cache to disk, never re-download) · `build_dsm.py` (rasterio burn,
buildings then canopy at 0.7) · `shadow.py` (sweep above) · `precompute.py` → 24 hourly
masks as PNG + a packed `.npy` for edge sampling. Sun position via `pvlib` or `astral`
(allowed — attribute it in the Devpost).

### Phase 2 — Graph  (4h)
Overpass → pedestrian ways. Split at shared nodes, build adjacency in `networkx`. Tag each
edge `indoor` / `covered` / `crossing` / `steps`. Sample the shadow mask along each edge
(~10 points) → `sunlit_fraction` per edge per hour. **Risk:** indoor ways may not join the
street graph. Mitigation: snap endpoints within 15m. Budget an hour for this specifically.

### Phase 3 — Routing + API  (3h)
A* with the cost function. FastAPI: `GET /route?from&to&time` → GeoJSON with per-segment
`indoor` flags and a summary (distance, sun-exposed metres, estimated heat load, indoor %).

### Phase 4 — Weather  (2h)
Open-Meteo current + archive. Derive `W_heat` / `W_wet`. Cache 10 min. **Ship a "demo day"
override** pinned to 14 Jan — you cannot record a shade demo in August, it's 9.9°C outside.

### Phase 5 — Frontend  (9h)
MapLibre GL JS, dark basemap, **vanilla JS — no build step**, you don't have time for one.
- Shadow mask as a **PNG image overlay**, 24 frames, hour slider swaps the source. This is
  how you get animated shadows with zero frontend compute, and it's the money shot.
- Route as GeoJSON. **Indoor segments styled distinctly** (dashed / glowing) — the viewer
  must *see* it dive through Melbourne Central.
- Side-by-side: "Google's route" (shortest) vs ours, with the exposure delta.
- Season toggle: Summer (heat) / Winter (rain + wind).

### Phase 6 — Polish + submission  (8h)
Opening-hours handling (fallback table for the major centres — Myer at 11pm is not a
route; *"this route closes in 20 minutes"* reads as thought-through). Design pass.
**Video by 8pm Monday.**

---

## Schedule

| When | Milestone |
|---|---|
| **Sat (today)** | Phase 0 complete + Phase 1 started. Shadows drawing over the CBD by tonight = safe |
| **Sunday** | Phases 2–3. End of day a real route must render |
| **Monday** | Phase 4–5, polish, **video by 8pm** |

Full scope is ~38 person-hours against ~28–30 available. **Solo: cut to MVP.** Two people:
fits with polish. Three or four: one on shadows, one on graph/routing, one on frontend.

**MVP if tight:** CBD only, walking only, precomputed masks, indoor links, map + route +
time slider + one weather weight. Cut personalisation, drinking fountains, tram dwell-time.

---

## Risks

| Risk | Mitigation |
|---|---|
| **Shadow accuracy rabbit hole** | 2m hourly is plenty. Nobody can tell in a video. Ship it and move on |
| Indoor graph doesn't connect | 15m endpoint snapping. Worst case hand-author ~20 links — still legitimate, still core logic |
| Demo route breaks live | Hand-verify Melbourne Central → Fed Square early, make that one route bulletproof |
| It's August, not January | Demo-day override pinned to a real summer date, using the archive API |
| "This already exists" | Cite Cool Routes first, then the radiation table |

## Stack

Python 3.11 · numpy, rasterio, shapely, pyproj, networkx, pvlib · FastAPI · MapLibre GL JS

```
data/      cached downloads (gitignore the big geojson)
scripts/   fetch_data.py build_dsm.py shadow.py build_graph.py precompute.py
server/    main.py routing.py cost.py weather.py
web/       index.html app.js style.css
out/       shadow_HH.png graph.pkl
```

## Submission checklist

Description (problem/solution/build) · track = Climate Action · **≤3 min video showing it
working** · public GitHub repo · **AI-assistance + dataset attribution disclosed**
(CoM Open Data CC-BY, OpenStreetMap ODbL, Open-Meteo CC-BY) · first-year flag if applicable.

All code must be written inside the event window (opened Fri 21 Aug 5pm) — you're clear.
