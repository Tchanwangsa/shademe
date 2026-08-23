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

|         | cost per metre           | knob            |
| ------- | ------------------------ | --------------- |
| thermal | `1 + K * stress(UTCI)` | `K = 0.10`    |
| UV      | `1 + K_uv * uv_frac`   | `K_uv = 0.25` |

Both are walked as a ladder, the resulting paths are de-duplicated and Pareto-filtered,
and what survives is what the app shows. Where both objectives pick the same walk, that
is one card, not two.

**The headline number is a dose in degC-minutes, not a percentage.** The two do not peak
at the same hour, and the percentage reads worst exactly when the city is hottest:

| hour | baseline degC-min | avoided degC-min | avoided % |
| ---- | ----------------- | ---------------- | --------- |
| 10   | 25.6              | 14.9             | 58.2      |
| 13   | 91.1              | 38.7             | 42.5      |
| 17   | 160.4             | 29.7             | 18.5      |

Late sun is low, the whole city is in shadow, and the shortest path is already shaded, so
proportionally less is left to win. Reproduce with `tools/bench_hours.py`.

---

## Prerequisites

|                                                              | Version         | Install                                             | Needed for             |
| ------------------------------------------------------------ | --------------- | --------------------------------------------------- | ---------------------- |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 0.11+           | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | everything Python      |
| [Python](https://www.python.org/downloads/)                   | 3.12.x          | **uv installs it for you** — see below       | the API and pipeline   |
| [Node.js](https://nodejs.org/en/download)                     | 20 LTS or newer | `brew install node`                               | the Expo app only      |
| [Xcode](https://apps.apple.com/au/app/xcode/id497799835)      | 16+             | App Store, then open it once                        | the iOS simulator only |
| [Docker](https://www.docker.com/products/docker-desktop/)     | 24+             | Docker Desktop                                      | deploying only         |

**You do not need to install Python yourself.** uv reads `.python-version`, downloads
CPython 3.12 into its own store and builds the venv from `uv.lock`. Note there is no bare
`python` on a stock macOS — every command below is prefixed with `uv run`, which is what
puts the project's interpreter and its packages on the path. Running `python ...` without
it gives `zsh: command not found: python`.

You also need **~3 GB of free disk** (600 MB of downloaded sources, ~900 MB of derived
rasters, the rest is the venv and node_modules) and a network connection for the first
build — the pipeline downloads from City of Melbourne's open data portal and from
OpenStreetMap via Overpass.

Only the API is required. The Expo app is optional: `/docs` gives you a browser UI that
exercises every endpoint.

---

## Quickstart

Four commands, in this order. The third one is the one people miss — the API **cannot**
start until the data is built, and it will refuse with a message telling you so.

```bash
git clone https://github.com/Tchanwangsa/shademe.git && cd shademe
uv sync
uv run python -m shademe.pipeline.build_all
uv run uvicorn shademe.api.main:app --host 0.0.0.0 --port 8011
```

Then check it in another terminal:

```bash
curl localhost:8011/health
```

A healthy response names the graph it loaded:

```json
{"ok":true,"nodes":49065,"edges":60957,"graph_source":"out/graph.pkl (19:15)","hour":19,...}
```

`build_all` takes about **7.5 minutes** from a clean clone and only has to be run once —
see [Building the derived data](#building-the-derived-data) for what it does and how to
resume it if a download fails. Everything after that starts in seconds.

The first `/routes` request then takes ~50 s while it runs the surface energy-balance
march for the day; every one after that is ~100 ms.

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

Assuming [Quickstart](#quickstart) has been done once — `uv sync` **and**
`uv run python -m shademe.pipeline.build_all` — the server is:

```bash
uv run uvicorn shademe.api.main:app --host 0.0.0.0 --port 8011
```

`GET /health` `/conditions` `/routes?from_lat=&from_lon=&to_lat=&to_lon=`
`/search?q=` `/reverse?lat=&lon=`, plus OpenAPI at `/docs`.

### Place search

`/search` geocodes free text against OpenStreetMap. There is no curated landmark list and
no `/places` -- that list was the same for every user and went stale whenever a venue
moved, so the picker now shows the device's own recent picks when the box is empty and
OSM's answer once anything is typed. Two providers, one dataset:
**Photon** answers first because it matches prefixes — `degrav` already finds Degraves
Street — and **Nominatim** is the fallback and owns `/reverse`, because it matches whole
words only (`degrav` and `flind` return nothing there) but is the better geocoder on a
finished query. `SHADEME_GEOCODER=nominatim` pins one; `SHADEME_PHOTON` and
`SHADEME_NOMINATIM` point at self-hosted instances.

Every match is snapped to the walking graph and dropped beyond 300 m — **the same reach
`/routes` allows**, so search cannot offer a destination routing will then refuse.
Matches dropped for that are counted in `outside` rather than silently discarded, which
is how the client can say "outside the CBD" instead of "no such place".

Each result carries OSM's own `opening_hours` verdict as `open_now` -- **three-valued**,
where `null` means the place carries no such tag rather than that it is open. Only `false`
draws a Closed badge; marking every untagged place shut would close half the CBD. Photon
returns no tags at all, so the hours come from one batched Nominatim `/lookup` issued
after the graph filter and the limit, covering only the rows about to be rendered.
`api/osm_hours.py` parses the subset of the opening_hours grammar Melbourne actually uses
and returns unknown -- never "closed" -- for anything it cannot read. This is display
only: it never removes an edge or changes a route, unlike `data/indoor_hours.json`.

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

Neither `data/` nor `out/` is in git: the sources are hundreds of MB and everything
derived from them is ~1 GB. A fresh clone therefore has to build both before the API
will start. One command does it:

```bash
uv run python -m shademe.pipeline.build_all
```

It runs the ten stages below in dependency order, skips any whose outputs already
exist (so a re-run after a failure resumes), and stops at the first stage that fails.
`--force` rebuilds regardless; `--only <stage>` runs one. Measured 7.5 min from an empty
tree on an M-series Mac; `fetch` (155 s, the 271 MB canopy download) and `svf` (160 s) are
most of it. `fetch`, `fetch_osm`, `tree_heights` and `materials` all download, so it needs
network, and Overpass can rate-limit you -- just re-run, completed stages are skipped.

The stages, and what each writes:

```bash
uv run python -m shademe.pipeline.fetch          # CoM buildings + canopy, clipped to the CBD
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

There is no bare `python` on a stock macOS -- every command here needs the `uv run`
prefix, which is also what puts the project's venv on the path.

### Mobile app

Optional — the API's `/docs` already exercises every endpoint in a browser. Needs
**Node 20+** and **Xcode 16+**, and the API running.

```bash
cd mobile
npm install
npx expo run:ios
```

The first `run:ios` compiles a native dev client (several minutes, and it will ask to
install CocoaPods if you have not). Later runs reuse it. `npm start` alone is not enough:
this uses `expo-dev-client` and native modules (MapLibre, Reanimated, gesture-handler),
so it does **not** run in Expo Go.

`mobile/.env` is committed and already points at `http://localhost:8011`, which is right
for the simulator — it shares the Mac's network stack. For a **physical phone**, set
`EXPO_PUBLIC_API_BASE` to the Mac's LAN address (`ipconfig getifaddr en0`) and start the
API with `--host 0.0.0.0`; `localhost` on a phone means the phone. `EXPO_PUBLIC_*` is
inlined at bundle time, so restart Metro after editing it.

**Expo 57**: check the versioned docs at [https://docs.expo.dev/versions/v57.0.0/](https://docs.expo.dev/versions/v57.0.0/) before
changing anything there.

### Deployment

The image is self-contained: `docker build` runs the whole pipeline inside it, so there
is no volume to mount and no prebuilt `out/` needed on the host.

```bash
docker build -t shademe .
docker run -p 8011:8011 shademe
```

Or `docker compose up --build`. The build downloads ~400 MB and rasterises the grid —
measured 7.5 min from empty on an M-series Mac — and wants ~4 GB given to the Docker VM.
The container then starts in seconds and needs **2 GB** to serve: ~585 MB steady state
plus the surface march.

Do not mount anything over `/app/out`: the rasters ship in the image, and a volume there
would shadow them. Full instructions for AWS, Fly and Render, the environment variables,
and what to check when it breaks are in [DEPLOY.md](DEPLOY.md).

---

## Calibration and provenance

Every constant that moves a route is named in one place and has a reason written next to
it. No figure should be quoted without the config that produced it — `meta.provenance`
rides on every API response, and `python -m shademe.provenance` prints the same stamp.

| constant             | value         | what it means                                                                                                                                                   | re-measure with                          |
| -------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| `K`                | 0.10          | extra metres walked per metre per degC of stress. Knee of the outdoor-shade regime; above ~0.22 the router buys benefit by going indoors instead                | `tools/bench_indoor.py --sweep K`      |
| `K_uv`             | 0.25          | how much further to swap full sun for full cover.**Not swept** — a stated preference, and there is no UV ground truth to fit it against                  | —                                       |
| `DOOR_PENALTY_M`   | 45            | per indoor transition. Two costs in one number: ~15 m is what a door really costs, ~30 m is a deliberate discount on indoor physics we assert rather than model | `tools/bench_indoor.py`                |
| `RISE_M_PER_M`     | 3.0           | Naismith (6 m per m climbed), halved because the graph is undirected                                                                                            | `tools/bench_indoor.py --sweep rise`   |
| `TAU_LEAF`         | 0.03          | SOLWEIG leaf-on transmissivity, shared by the shade and SVF paths                                                                                               | —                                       |
| `UV_DIFFUSE_FLOOR` | 0.45          | clear-sky diffuse share of erythemal UV. Why an arcade beats the shade of a pole                                                                                | —                                       |
| `INDOOR_TA`        | 22.5          | asserted, not measured. Arcades are air conditioned                                                                                                             | —                                       |
| bias correction      | shape + level | Open-Meteo diurnal shape and a flat +1.00 degC level, fitted on 12 CoM sensors / 243k site-hours. The level is the 28% win                                      | `tools/validate_sensors.py --fit-bias` |

### What is not validated

- **The shadow model has no external ground truth.** The CoM lux sensors saturate, so
  `tools/validate_sensors.py --shade-lux` came back null. Melbourne's open data cannot
  supply one.
- **`K` and a systematic MRT bias are the same lever**, so a low `K` silently assumes our
  MRT runs hot.

### Sources evaluated and turned down

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
