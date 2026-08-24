# ShadeMe

Walking routes through the Melbourne CBD that are priced on **today's weather**, not on a
static shade map. A FastAPI service models the thermal and UV environment of every street
segment for the current hour and returns a handful of genuinely distinct options; an Expo
app shows them.

The council's Cool Routes already sends people through the arcades. It has no weather in
it at all — that is the difference here.

---

## What it actually computes

For the half hour being walked, per edge of a 61k-edge pedestrian graph:

```
shade raster + sky view factor + surface material
        -> surface energy balance          (Ts, marched at 30 min)
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
proportionally less is left to win. Reproduce with `tools/bench_hours.py`. Measured on the
hourly grid, before the move to half-hour slots — the shape holds, but re-run it before
quoting the digits against a current build.

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
uv run shademe-api --port 8011
```

Then check it in another terminal:

```bash
curl localhost:8011/health
```

A healthy response names the graph it loaded:

```json
{"ok":true,"nodes":49065,"edges":60957,"graph_source":"out/graph.pkl (19:15)","hour":19,...}
```

`notebooks/` is a submodule and stays empty unless you ask for it — nothing in the API
or the pipeline touches it. To get it: `git submodule update --init`, then
`uv sync --group notebooks`.

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
  api/                main, engine, cost, routing, weather, sky, uv, hours
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
uv run shademe-api --port 8011
```

`GET /health` `/conditions` `/routes?from_lat=&from_lon=&to_lat=&to_lon=&unacclimatised=&vulnerable=`
`/search?q=` `/reverse?lat=&lon=`, plus OpenAPI at `/docs`.

`uv run uvicorn shademe.api.main:app --host 0.0.0.0 --port 8011` still works and is what
the container runs; `shademe-api` is the same app plus the clock flags below.

### Pinning the clock

The API prices the wall clock and takes no `hour` parameter — a demo that can be dialled
to its best hour is not evidence. The one exception is a **whole-server pin**, which is
what you launch with for a demo in August:

```bash
uv run shademe-api --date 2026-01-27 --time 16:00 --port 8011
```

**27 January 2026 reached 43.4 °C at 16:00** under a near-clear sky — the hottest archived
day in this dataset. Melbourne in late August tops out near 16 °C with UTCI inside the
no-stress band, where every rung of the thermal ladder collapses to the same walk and the
product has nothing to show. At 16:00 on 27 January it returns four distinct options
spanning a 24% detour.

The pin moves the **whole world** to one instant, not one number:

| what | follows the pin |
| --- | --- |
| weather | Open-Meteo **archive** for that day, disk-cached (`data/weather_cache_2026-01-27.json`) |
| shade rasters | the set within 2° of that day's noon sun — 27 Jan is served by `out/day_2026-01-26` |
| sun position | that day's real azimuth and elevation, at that half-hour slot |
| UV | modelled clear-sky × cloud. The **live ARPANSA measurement is refused** under a pin: it publishes the current index and no other, so on 27 January it would be this afternoon's real number for the wrong day |
| opening hours | that day's weekday — 27 Jan 2026 is a **Tuesday**, so the arcade gate is Tuesday's |
| arrival times | counted off the pinned `as_of`, so the cards do not drift |

Both flags are independent. `--date` alone prices that day at the real time of day (what
the bench scripts have always done); `--time` alone freezes today at that hour. The pinned
instant is **frozen** for the life of the process, so a ten-minute demo does not slide into
the next slot and re-march the surface temperatures halfway through.

Nothing is hidden. Every response carries `conditions.clock`:

```json
{ "pinned": true, "date": "2026-01-27", "time": "16:00",
  "real_today": "2026-08-24", "source": "SHADEME_DATE=2026-01-27 + SHADEME_TIME=16:00" }
```

the provenance one-liner reads `demo day 2026-01-27 16:00 PINNED`, and the mobile app
shows a third chip over the map reading **Pinned · 2026-01-27 · 16:00**.

`SHADEME_DATE` and `SHADEME_TIME` are the same two knobs as environment variables, for the
container and for the bench scripts, which have no argv to pass.

Two things to know before you stand up:

* **Start it a minute early.** The first engine state — shade set, edge index, surface
  energy-balance march — takes ~40 s, and the prewarm thread pays for it at start-up
  rather than making the first `/routes` wait. `GET /health` says when it is warm.
* **The archive fetch is cached to disk on first run**, so the demo survives dead wifi
  after that; a stale cache is preferred to a lie and says so in `source`. On a machine
  that has never fetched 27 January, warm it once with network up:
  `uv run python -c "from shademe.api import weather; weather.get('2026-01-27')"`

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

### Who is walking

`K` is the model's one free parameter — how much further a walk is worth to avoid one
degree of heat stress — and until now it was the same number for everybody. It is a
*preference*, and a preference belongs to a person. Two optional flags on `/routes` scale
it, and they are separate because the two reasons are **independent**:

| | acclimatised? | reduced capacity? | K multiplier |
|---|---|---|---|
| healthy Melbourne regular | yes | no | ×1.0 |
| visitor, week 1 of a heatwave | **no** | no | ×1.8 |
| elderly long-time resident | yes | **yes** | ×1.8 |
| elderly visitor, week 1 | **no** | **yes** | ×3.24 |

**Acclimatisation** is recent history: 1–2 weeks of repeated exposure earns a lower heart
rate, earlier and more dilute sweating, and steadier blood pressure in heat. A visitor, a
recent arrival and a lifelong local in the season's first hot spell are all equally
unadapted — the first hot week catches a whole city at once. **Vulnerability** is capacity:
65+, pregnancy, and cardiac or renal conditions reduce the ability to shed heat at all.
Acclimatising raises that person's own baseline; it does not lift the ceiling. So they
compose multiplicatively rather than overriding each other.

The multiplier does two things. It **scales the thermal ladder**, so the searches explore
the preferences that walker actually holds instead of offering them the nearest rung of
someone else's — `meta.k_ladder` is what was walked, `meta.k_ladder_base` what an
undeclared walker gets. And it **picks one option as `recommended`**, by scoring every
returned walk in the router's own currency, restated per route:

```
weighted minutes = minutes + K * stress_load + doors * DOOR_PENALTY_M / (1.35 * 60)
```

That is not a second objective. It is the same sum the A\* minimised over edges, divided
through by walking speed, so that walks found by different searches are comparable on one
scale — checked against the real cost function in `tests/test_walker.py`. It is thermal
only: UV is a *second* preference, not a term in this one, and combining them needs an
exchange rate between a °C-minute and a UV index-minute that nothing here measures.

**This replaces a hidden preference with a declared one.** The list was already choosing:
sorted coolest-first, with the client selecting the top card — the behaviour of someone
who would walk any distance to shed one degree, i.e. `K = ∞`, asserted nowhere and
adjustable by nobody. Every option still ships in the same order; one now carries a badge,
and `meta.walker` says which K put it there.

**Said plainly: the direction is evidenced, the magnitude is not.** ×1.8 per flag comes
from "a walk worth 6% further per degree to an adapted adult is worth about 11% to someone
who is not" — a stated preference with no more behind it than `K_DEFAULT` itself has. One
flag stands in for three quite different physiologies, and there is deliberately no
per-condition scoring: that would be a clinical risk calculator with nothing validating it.
What makes an uncalibrated number safe to ship is that **it cannot run away** — K buys only
detour, `DETOUR_CAP` bounds detour at 1.4× the direct walk, and `_route_pref` halves K
until it fits. There is no setting of these two switches that sends anyone somewhere the
unpersonalised app would refuse to send them, and the app quotes that ceiling back from
`meta.detour_cap` rather than hardcoding it in the copy.

The answers live on the device (`shademe-walker.json`, next to recents) and ride on the
query string. The server stores nothing, and an untouched dial sends no parameters at all,
so `meta.walker.k_multiplier == 1` is provably unpersonalised rather than personalised back
to the default. `K_uv` is **not** scaled: neither question asks anything bearing on how
much erythemal UV a person should collect, and borrowing a heat answer to move a UV route
would be claiming an authority it does not have.

The background prewarm thread — not the first caller — pays for the surface
energy-balance march (~40 s) and for generating the shade set when none matches today's
sun (~27 s). Calls are about 100 ms.

There is no `hour` parameter, by design: everything is priced at the wall clock in
Australia/Melbourne, snapped to the **nearest half hour** — **all 24 hours of it**, with
no clamp.

Both halves of that sentence used to be smaller. The grid was hourly, and it was clamped
to a 06:00–20:00 window.

**The half hour.** At solar noon in January the solar azimuth swings 23.4° in half an
hour, so an hourly raster is up to 30 minutes stale at its worst point. Against a rebuilt
13:30 raster, nearest-hour sampling put 2.9 % (Jan) to 3.8 % (Aug) of cells on the wrong
side of a shadow edge, and on a hot day two of three test pairs now pick a *distinct*
15:30 path rather than one of the two bracketing hours — so this moves routes, not just
reported numbers. 15 minutes was measured too and does not pay for itself. The march
costs the same either way: `surface_temp.march()` derives `dt` from the spacing of the
clock it is handed and picks `n_sub` to land on `SUB_DT`, so hourly/12 and half-hourly/6
are both dt = 300 s. The clock got finer and the integration did not, which is what keeps
the accumulation integral — the only part of the model with memory — comparable across
the change (0.0175 K max on the same forcing, checked in the tests).

**The window.** At 23:53 the app priced 20:00 and showed an 8 pm temperature, an 8 pm sky
glyph and an arcade gate that thought Melbourne Central was open at midnight. Nothing had
to be computed to remove it. Below `shadow.SUN_MIN_DEG` (5°) every mask in the shadow
sweep already returns *fully shaded*, so a night raster is the constant 1.0 in all 6.1 M
cells — `pipeline.shade` writes no file for those slots and `api.engine` reads a missing
raster as full shade. The surface march has always run the whole 24 h clock internally;
it was simply never asked to emit the dark slots.

The two changes pay for each other. The sun gate is a **strict subset** of the window it
replaces on every day of the year, so covering 24 h at half-hour resolution writes *fewer*
rasters than the window did at the same step — measured at this latitude:

| day | sunlit half-hour rasters | the old window |
|---|---|---|
| 2026-06-21 | 17 (08:30–16:30) | 29 |
| 2026-08-24 | 20 (07:30–17:00) | 29 |
| 2026-01-26 | 27 (07:00–20:00) | 29 |
| 2026-12-21 | 28 (06:30–20:00) | 29 |

And going the other way: a Ts raster is 24.2 MB on this grid and the march must produce
one for *every* slot, dark ones included — the accumulation integral is the only part of
the model with memory, so the night is what the morning is warm from. Holding all 48 is
1.16 GB, which will not fit the 2 GB container. `engine.attach_tsurf` streams each slot
onto the edges and drops the raster, which is what makes the half-hour grid fit at all.
Measured peak RSS through a cold march on this grid: 865 MB streaming the hourly clock
against 1153 MB accumulating it, and **1143 MB streaming all 48 half-hour slots** —
accumulating those was never measured because the 1.16 GB of Ts alone does not fit the
container. See DEPLOY.md.

There is no `meta.beam` flag any more either. It zeroed the radiation outside the window,
because serving a 21:30 walk off the 20:00 slot priced it on a beam that set at 20:44.
With no clamp, 21:30 reads 21:30's own row, whose direct radiation is zero because the
sun is down — `meta.condition` says `night`, from the same `SUN_MIN_DEG` the router gates
on.

Radiation comes from Open-Meteo's 15-minute series where it reaches (`meta.rad_source`),
because that is the one variable whose sub-hourly structure is real: a cloud crossing
reads 267 → 208 → 503 W/m² inside two hours the hourly series flattens to 280 and 210.
Temperature is not taken from it — at 15 minutes Open-Meteo simply interpolates its own
hourly endpoints, so there is nothing there to take.

### The sky glyph

The chip over the map used to pick its icon with a rule that fell through to cloud cover
whenever there was no beam to read:

```python
if direct + diffuse < 20:                    # dusk: no beam to read
    return "cloudy" if cloud_cover >= 60 else "sunny"
```

Cloud cover carries no information about whether the sun exists, so that rule cannot tell
a clear night from a clear dawn — and at 23:53 with 2 % cloud it drew a **sun over
Melbourne at midnight**. `api/sky.py` replaces it with two questions answered from two
different things:

* **Is the sun up?** From the sun's position, and from nothing else. Below
  `shadow.SUN_MIN_DEG` the glyph is `night` — the *same* constant the shadow sweep uses
  to decide every cell is shaded, so the icon and the router cannot disagree about
  whether there is sun to walk out of.
* **Is the beam landing?** From beam *transmission* — `direct_radiation` over what a
  clear sky would deliver at that solar elevation (Haurwitz 1946 for the global,
  Meinel & Meinel 1976 over a Kasten–Young air mass for the beam). A ratio, not a
  threshold in W/m², because 100 W/m² of beam is a heavily clouded noon and a perfectly
  clear 8 am — and the old absolute cut-offs called the second one "partly cloudy".

Checked against Open-Meteo's own clear hours (cloud ≤ 5 %, sun above 20°) over two
seasons of Melbourne archive: mean clear-sky index 1.010 sd 0.055 in summer (n=229) and
0.946 sd 0.063 in winter (n=42) — a few percent, far tighter than the 0.15 / 0.50 bands
care about. Every response carries `condition_source` naming the quantity that decided
and its value, the way `uv_source` does. Cloud cover survives in one clearly-labelled
branch: the slot in which the sun is up by the wall clock but the radiation row it came
with covers a window the sun was too low to deliver a readable beam through.

**A caveat found while calibrating this, and it is bigger than the glyph.** Open-Meteo
returns `utc_offset_seconds: 36000` for a January range as well as an August one — its
Melbourne timestamps are in a **fixed +10:00 and do not carry AEDT** — and each hourly
row is the mean over the *preceding* hour. Sweeping the offset against clear hours shows
it: the two seasons want offsets exactly one hour apart in a DST-aware frame, and the
same −30 min in the fixed frame. Everything else in this project reads a slot's row as
"local time = that slot", which is right to within half an hour in winter and **an hour
out in summer**. `weather.row_elevation` divides the glyph's beam by the right clear-sky
number — generalising the measured −30 min to the interpolated grid, and stating that the
same convention is only *assumed* for the 15-minute series — but it does not repair the
alignment underneath it, because doing so moves every temperature, radiation and UTCI
figure here and wants its own validation.

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
