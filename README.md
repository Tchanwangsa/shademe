# ShadeMe

Walking routes through the Melbourne CBD that are priced on **today's weather**, not on a
static shade map. A FastAPI service models the thermal and UV environment of every street
segment for the current hour and returns a handful of genuinely distinct options; an Expo
app shows them.

The council's Cool Routes already sends people through the arcades. It has no weather in
it at all — that is the difference here.

---

## What it actually computes

For the hour being walked, per edge of a 61k-edge pedestrian graph:

```
shade raster + sky view factor + surface material
        -> surface energy balance          (Ts, marched hourly)
        -> mean radiant temperature        (SOLWEIG six-direction form)
        -> UTCI                            (Brode et al. 2012 polynomial)
        -> thermal stress = degrees outside the 9..26 degC no-stress band
```

and, independently, a UV exposure fraction from the beam/sky split.

Two objectives are then routed separately:

| | cost per metre | knob |
|---|---|---|
| thermal | `1 + K * stress(UTCI)` | `K = 0.10` |
| UV | `1 + K_uv * uv_frac` | `K_uv = 0.25` |

Both are walked as a ladder, the resulting paths are de-duplicated and Pareto-filtered,
and what survives is what the app shows. Where both objectives pick the same walk, that
is one card, not two.

**The headline number is a dose in degC-minutes, not a percentage.** The two do not peak
at the same hour, and the percentage reads worst exactly when the city is hottest:

| hour | baseline degC-min | avoided degC-min | avoided % |
|---|---|---|---|
| 10 | 25.6 | 14.9 | 58.2 |
| 13 | 91.1 | 38.7 | 42.5 |
| 17 | 160.4 | 29.7 | 18.5 |

Late sun is low, the whole city is in shadow, and the shortest path is already shaded, so
proportionally less is left to win. Reproduce with `tools/bench_hours.py`.

---

## Layout

```
shademe/
  config.py           CBD bbox, projection, canopy constants
  paths.py            data/ and out/ locations (SHADEME_DATA_DIR / SHADEME_OUT_DIR)
  provenance.py       the stamp that says which config produced a number
  physics/            shadow, svf, canopy_svf, surface_temp, mrt  -- pure, no I/O
  pipeline/           fetch -> dsm -> tree_heights -> shade -> materials -> graph
  api/                main, engine, cost, routing, weather, uv, hours
tests/                run directly; exit non-zero on failure
tools/                benchmarks, source evaluations, sensor validation
data/                 inputs (mostly fetched; three files are hand-authored)
out/                  everything derived: rasters, graph. ~1 GB, gitignored
mobile/               Expo app (React Native, MapLibre)
```

---

## Running it

Python is managed with [uv](https://docs.astral.sh/uv/). `uv sync` creates the
environment from `uv.lock`; no other setup.

```bash
uv sync
uv run uvicorn shademe.api.main:app --host 0.0.0.0 --port 8011
```

`GET /health` `/places` `/conditions` `/routes?from_lat=&from_lon=&to_lat=&to_lon=`
`/search?q=` `/reverse?lat=&lon=`, plus OpenAPI at `/docs`.

### Place search

`/search` geocodes free text against OpenStreetMap, so the picker is not limited to the
fifteen landmarks `/places` still returns for an empty box. Two providers, one dataset:
**Photon** answers first because it matches prefixes — `degrav` already finds Degraves
Street — and **Nominatim** is the fallback and owns `/reverse`, because it matches whole
words only (`degrav` and `flind` return nothing there) but is the better geocoder on a
finished query. `SHADEME_GEOCODER=nominatim` pins one; `SHADEME_PHOTON` and
`SHADEME_NOMINATIM` point at self-hosted instances.

Every match is snapped to the walking graph and dropped beyond 300 m — **the same reach
`/routes` allows**, so search cannot offer a destination routing will then refuse.
Matches dropped for that are counted in `outside` rather than silently discarded, which
is how the client can say "outside the CBD" instead of "no such place".

Both providers are rate-limited free services. `geocode.py` throttles per host (1 rps for
Nominatim, per its usage policy), caches for 15 minutes, and sends an identifying
User-Agent; the client debounces 300 ms so a search fires per word typed rather than per
keystroke. Anything past demo traffic should self-host.

The first `/routes` call of the day pays for the surface energy-balance march (~40 s) and
regenerates the shade set if none matches today's sun (~13 s). Every call after that is
about 100 ms. There is no `hour` parameter, by design: everything is priced at the wall
clock in Australia/Melbourne, clamped to the 06:00–20:00 window the rasters cover.

Bind to `0.0.0.0` for a physical phone — `localhost` resolves to the phone itself.

### Building the derived data

`out/` is not in git. From an empty one, in order:

```bash
uv run python -m shademe.pipeline.fetch          # CoM buildings + canopy
uv run python -m shademe.pipeline.fetch_osm      # walkable OSM network
uv run python -m shademe.pipeline.dsm            # 2 m building + canopy height rasters
uv run python -m shademe.pipeline.tree_heights   # allometric crowns (top + trunk base)
uv run python -m shademe.pipeline.shade_legacy   # legacy shade set (bench baseline)
uv run python -m shademe.pipeline.shade          # the shipped shade set -> out/v2
uv run python -m shademe.physics.svf             # svf_bldg + svf_all
uv run python -m shademe.physics.canopy_svf      # svf_veg, the one the engine reads
uv run python -m shademe.pipeline.materials      # surface materials + thermal properties
uv run python -m shademe.pipeline.graph          # out/graph.pkl
```

### Mobile app

```bash
cd mobile && npm install && npx expo run:ios
```

Set `EXPO_PUBLIC_API_BASE` in `mobile/.env` — `http://localhost:8011` for the simulator
(it shares the Mac's network stack), the Mac's LAN address for a real device. It is
inlined at bundle time, so restart Metro after editing. **Expo 57**: check the versioned
docs at <https://docs.expo.dev/versions/v57.0.0/> before changing anything there.

### Deployment

```bash
docker build -t shademe . && docker run -p 8011:8011 -v "$PWD/out:/data/out" shademe
```

The image carries the code and `data/`; `out/` is a volume, because it is a gigabyte of
regenerable rasters. `PORT`, `SHADEME_OUT_DIR` and `SHADEME_CORS_ORIGINS` are the knobs
that matter for a real deploy.

---

## Calibration and provenance

Every constant that moves a route is named in one place and has a reason written next to
it. No figure should be quoted without the config that produced it — `meta.provenance`
rides on every API response, and `python -m shademe.provenance` prints the same stamp.

| constant | value | what it means | re-measure with |
|---|---|---|---|
| `K` | 0.10 | extra metres walked per metre per degC of stress. Knee of the outdoor-shade regime; above ~0.22 the router buys benefit by going indoors instead | `tools/bench_indoor.py --sweep K` |
| `K_uv` | 0.25 | how much further to swap full sun for full cover. **Not swept** — a stated preference, and there is no UV ground truth to fit it against | — |
| `DOOR_PENALTY_M` | 45 | per indoor transition. Two costs in one number: ~15 m is what a door really costs, ~30 m is a deliberate discount on indoor physics we assert rather than model | `tools/bench_indoor.py` |
| `RISE_M_PER_M` | 3.0 | Naismith (6 m per m climbed), halved because the graph is undirected | `tools/bench_indoor.py --sweep rise` |
| `TAU_LEAF` | 0.03 | SOLWEIG leaf-on transmissivity, shared by the shade and SVF paths | — |
| `UV_DIFFUSE_FLOOR` | 0.45 | clear-sky diffuse share of erythemal UV. Why an arcade beats the shade of a pole | — |
| `INDOOR_TA` | 22.5 | asserted, not measured. Arcades are air conditioned | — |
| bias correction | shape + level | Open-Meteo diurnal shape and a flat +1.00 degC level, fitted on 12 CoM sensors / 243k site-hours. The level is the 28% win | `tools/validate_sensors.py --fit-bias` |

### What is not validated

- **The shadow model has no external ground truth.** The CoM lux sensors saturate, so
  `tools/validate_sensors.py --shade-lux` came back null. Melbourne's open data cannot
  supply one.
- **Opening hours are editorial.** OSM carries `opening_hours` on 2 of 1232 walkable
  indoor ways, so `data/indoor_hours.json` is hand-written and ships `verified: false` on
  every class. The gate itself is correct; the coverage is a data problem.
- **`K` and a systematic MRT bias are the same lever**, so a low `K` silently assumes our
  MRT runs hot.

### Sources evaluated and turned down

- Himawari satellite radiation — over-states clear-sky diffuse, moves UTCI 0.2 degC
  (`tools/eval_satellite_radiation.py`).
- BOM station observations — 53% better at 32 degC+, but only for the one hour already
  observed; the anomaly does not persist into the hours the app shows
  (`tools/eval_bom_obs.py`).
- Open-Meteo's BOM-model endpoint — coarser than what we use, and returned no data.

---

## Tests

Plain scripts, no framework. Each exits non-zero on failure.

```bash
uv run python tests/test_mrt.py            # UTCI vs published refs, MRT identities
uv run python tests/test_shadow.py         # crown-slab shadow path
uv run python tests/test_surface_temp.py   # energy balance, stability, enclosure identity
uv run python tests/test_canopy_svf.py     # analytic canopy blocking
uv run python tests/test_svf.py            # convergence + real-DSM sanity (slow)
uv run python tests/test_weather_bias.py   # the bias correction end to end
```

## Data

City of Melbourne open data (buildings, tree canopy, tree dimensions, road surface types,
microclimate sensors), OpenStreetMap via Overpass, Open-Meteo forecast and archive, and
ARPANSA's live UV network — which is a spectroradiometer measurement, and the same number
the BOM app shows.
