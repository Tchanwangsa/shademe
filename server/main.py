"""ShadeMe API. FastAPI + preloaded graph. See CONTRACT.md."""
import os, sys, json, time, pickle
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT, DATA, WEB = f"{ROOT}/out", f"{ROOT}/data", f"{ROOT}/web"
sys.path.insert(0, f"{ROOT}/scripts")

from . import weather                                        # noqa: E402
from .cost import summarise, segments, geojson, thermal_summary, compare_thermal              # noqa: E402
from . import routing                                        # noqa: E402
from . import hours                                          # noqa: E402

HOURS = list(range(6, 21))
DEMO_HOUR = 16
PLACES_RAW = [
    ("Melbourne Central", -37.81001, 144.96280),
    ("Federation Square", -37.81800, 144.96910),
    ("Flinders Street Station", -37.81820, 144.96700),
    ("Southern Cross Station", -37.81830, 144.95270),
    ("Queen Victoria Market", -37.80700, 144.95680),
    ("State Library", -37.80980, 144.96490),
    ("Emporium", -37.81180, 144.96330),
    ("Myer", -37.81350, 144.96450),
    ("Bourke St Mall", -37.81390, 144.96430),
    ("Docklands", -37.81690, 144.94620),
    ("Carlton Gardens", -37.80540, 144.97120),
    ("RMIT", -37.80780, 144.96360),
    ("Parliament Station", -37.81100, 144.97270),
    ("Crown Casino", -37.82250, 144.95840),
    ("Melbourne Museum", -37.80330, 144.97150),
]

S = {}          # module state: graph, node arrays, places


def _shade_grid(hour, day=None):
    """Fallback path only: compute one hour's shade grid the way proto_route.py does.

    `day` used to be pinned to 2026-01-14 -- a date that appears nowhere else in the
    codebase and is twelve days off the demo day everything else uses. It now defaults
    to the day actually being priced.
    """
    import pandas as pd
    from config import CELL
    from shadow import sun_position, shade_factor
    grid = json.load(open(f"{OUT}/grid.json"))
    day = day or weather.get("summer").get("date") or weather.SUMMER_DATE
    when = pd.Timestamp(f"{day} {hour:02d}:00", tz=weather.TZ)
    az, el = sun_position(when)
    dsm_c = np.load(f"{OUT}/dsm_canopy.npy")
    # Fallback path, pinned to the v1 flat canopy: zeros == legacy crown-to-pavement.
    # The served rasters are out/v2/, built by scripts/regen_shade_v2.py with the
    # real crown base from out/dsm_canopy_base_v2.npy.
    sh = shade_factor(np.load(f"{OUT}/dsm_buildings.npy"), dsm_c,
                      np.zeros_like(dsm_c), CELL, az, el)
    return sh, grid


def _sample_one_hour(G, sh, grid, hour, n=8):
    """Fallback shade sampling: edge['shade'] = {hour: float}, same recipe as proto_route."""
    minx, miny, maxx, maxy = grid["bounds"]
    from config import CELL
    H, W = sh.shape
    sun = []
    for u, v, d in G.edges(data=True):
        if d.get("indoor") or d.get("covered"):
            d["shade"] = {hour: 1.0}
        else:
            sun.append((u, v))
    if not sun:
        return
    xy = np.array([[G.nodes[u]["xy"], G.nodes[v]["xy"]] for u, v in sun])
    f = ((np.arange(n) + 0.5) / n)[None, :, None]
    pts = xy[:, 0][:, None, :] + (xy[:, 1] - xy[:, 0])[:, None, :] * f
    r = ((maxy - pts[..., 1]) / CELL).astype(np.int32)
    c = ((pts[..., 0] - minx) / CELL).astype(np.int32)
    inside = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    flat = np.clip(r, 0, H - 1) * W + np.clip(c, 0, W - 1)
    cnt = np.maximum(inside.sum(1), 1)
    vals = (sh.ravel()[flat] * inside).sum(1) / cnt
    for (u, v), s in zip(sun, vals):
        G[u][v]["shade"] = {hour: float(s)}


def load_graph():
    p = f"{OUT}/graph.pkl"
    if os.path.exists(p):
        G = pickle.load(open(p, "rb"))
        return G, f"out/graph.pkl ({time.strftime('%H:%M', time.localtime(os.path.getmtime(p)))})"
    import inspect, build_graph
    if "sample" in inspect.signature(build_graph.build).parameters:      # Phase 2 signature
        have = [h for h in HOURS if os.path.exists(f"{OUT}/shade_{h:02d}.npy")]
        if have:
            return build_graph.build(hours=have, verbose=False), \
                   f"fallback build_graph.build() hourly shade {have[0]}-{have[-1]}"
        G = build_graph.build(hours=[DEMO_HOUR], sample=False, verbose=False)
        sh, grid = _shade_grid(DEMO_HOUR)
        _sample_one_hour(G, sh, grid, DEMO_HOUR)
    else:                                                                # Phase 0 signature
        sh, grid = _shade_grid(DEMO_HOUR)
        G = build_graph.build(sh, grid)
    return G, f"fallback build_graph.build() single-hour shade @ {DEMO_HOUR}:00"


def graph_hours(G):
    for _, _, d in G.edges(data=True):
        s = d.get("shade")
        if isinstance(s, dict) and s:
            return sorted(int(k) for k in s)
    return HOURS


def nearest(lat, lon, max_m=None):
    x, y = S["tf"].transform(lon, lat)
    d2 = (S["X"] - x) ** 2 + (S["Y"] - y) ** 2
    i = int(np.argmin(d2))
    dist = float(np.sqrt(d2[i]))
    if max_m is not None and dist > max_m:
        return None, dist
    return S["ids"][i], dist


app = FastAPI(title="ShadeMe")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


@app.on_event("startup")
def startup():
    t0 = time.time()
    from pyproj import Transformer
    from config import WGS84, MGA55
    import networkx as nx
    G, src = load_graph()
    main = G.graph.get("main") or max(nx.connected_components(G), key=len)
    ids = list(main)                      # snap only to the main component; islands are traps
    xy = np.array([G.nodes[n]["xy"] for n in ids], dtype=float)
    S.update(G=G, ids=ids, X=xy[:, 0], Y=xy[:, 1], source=src,
             tf=Transformer.from_crs(WGS84, MGA55, always_xy=True),
             hours=graph_hours(G))
    places, dropped = [], []
    for name, lat, lon in PLACES_RAW:
        n, d = nearest(lat, lon, 100.0)
        (places if n else dropped).append({"name": name, "lat": lat, "lon": lon,
                                           "node": n, "snap_m": round(d, 1)})
    S["places"] = places
    print(f"[shademe] graph {G.number_of_nodes()} nodes / {G.number_of_edges()} edges "
          f"from {src} in {time.time()-t0:.1f}s")
    print(f"[shademe] places ok={len(places)} dropped={[d['name'] for d in dropped]}")
    for m in ("summer", "winter"):                  # prewarm so the first demo call isn't 4s
        try:
            print(f"[shademe] weather {m}: {weather.block(DEMO_HOUR, m)['source']}")
        except Exception as e:
            print(f"[shademe] weather {m} prewarm failed: {e}")
    # Stamp the config ONCE at startup and hand it back on every route, so a figure
    # screenshotted out of the app carries the graph, rasters and K that produced it.
    # Digests are cached on (size, mtime) in out/.provenance_cache.json.
    try:
        import provenance as _p
        S["prov"] = _p.line()
        print(f"[shademe] provenance {S['prov']}")
    except Exception as e:
        S["prov"] = None
        print(f"[shademe] provenance stamp failed: {e}")


# --- v2 physical engine (additive; ?engine=utci) --------------------------------
ENG = {}


def _shade_key(wm, wx=None):
    """What to hand engine as `mode`: the day being priced, else the legacy mode name."""
    wx = wx if wx is not None else weather.get(wm)
    return wx.get("date") or wm


def _engine_state(mode):
    """Per-mode engine state, rebuilt only when the weather payload changes.

    edge_index() is ~1 s; attach_tsurf() runs the ~38 s energy-balance march, so both are
    done lazily on first use and cached, never at startup and never per request.
    """
    from . import engine as _e
    wm = "summer" if mode != "winter" else "winter"
    # The bias-corrected payload, not the raw one: attach_tsurf() marches the surface and
    # facade energy balance off wx["hours"] directly, and it has to see the same air
    # temperature the UTCI terms in solve() see. The bias mode is in the cache key so
    # flipping SHADEME_BIAS_LEVEL rebuilds the march instead of silently reusing it.
    wx = weather.apply_bias(weather.get(wm))
    # THE SHADE SET FOLLOWS THE DATE, NOT THE MODE. `wm` still selects which weather
    # payload to price; the sun geometry comes from the day that payload is for, so an
    # October request can no longer be costed on January shadows. engine._dirs_for()
    # accepts a YYYY-MM-DD here and generates the set if none on disk is close enough.
    sk = _shade_key(wm, wx)
    key = (wm, sk, wx.get("date"), wx.get("ts"), wx.get("bias", {}).get("mode"))
    st = ENG.get(wm)
    if st and st["key"] == key:
        return st
    t0 = time.time()
    E = _e.edge_index(S["G"], mode=sk)
    _e.attach_tsurf(E, S["G"], wx, mode=sk)
    st = {"E": E, "wx": wx, "key": key, "solved": {}, "applied": None}
    ENG[wm] = st
    print(f"[shademe] engine state for {wm} built in {time.time()-t0:.1f}s")
    return st


def _hour(hour, mode):
    if hour is not None:
        return max(6, min(20, int(hour)))
    if mode == "summer":
        return DEMO_HOUR
    import pandas as pd
    return max(6, min(20, pd.Timestamp.now(tz=weather.TZ).hour))


@app.get("/health")
def health():
    G = S.get("G")
    if G is None:
        raise HTTPException(503, "graph not loaded")
    return {"ok": True, "edges": G.number_of_edges(), "nodes": G.number_of_nodes(),
            "hours": S["hours"], "graph_source": S["source"], "places": len(S["places"])}


@app.get("/places")
def places():
    return [{"name": p["name"], "lat": p["lat"], "lon": p["lon"]} for p in S["places"]]


@app.get("/weather")
def get_weather(hour: int = Query(None), mode: str = Query("summer")):
    h = _hour(hour, mode)
    return weather.block(h, mode)


@app.get("/route")
def get_route(from_lat: float, from_lon: float, to_lat: float, to_lon: float,
              hour: int = Query(None), mode: str = Query("summer"),
              compare: bool = Query(True), engine: str = Query("legacy"),
              K: float = Query(None), dow: int = Query(None),
              respect_hours: bool = Query(True)):
    t0 = time.time()
    G = S["G"]
    h = _hour(hour, mode)
    w = weather.block(h, mode)
    # Opening hours are a hard gate, not a cost: a shut arcade is an absent edge. Applied
    # to the shade route AND its shortest-path baseline, so the two stay comparable.
    dw = hours.now_dow() if dow is None else int(dow)
    closed = hours.closed_keys(h, dw) if respect_hours else set()
    s, ds = nearest(from_lat, from_lon, 300.0)
    t, dt = nearest(to_lat, to_lon, 300.0)
    if s is None or t is None:
        raise HTTPException(400, f"point off the pedestrian network "
                                 f"(start {ds:.0f}m, end {dt:.0f}m from nearest node)")
    use_utci = str(engine).lower() in ("utci", "v2", "physical")
    solved = None
    if use_utci:
        from . import engine as _e
        from .cost import K_DEFAULT
        st = _engine_state(mode)
        # solve() depends only on (hour, weather), never on origin/destination -> cache it,
        # and only re-stash onto the graph when the applied hour actually changes.
        solved = st["solved"].get(h)
        if solved is None:
            solved = _e.solve(st["E"], w, st["wx"], h)
            st["solved"][h] = solved
        if st["applied"] != h:
            _e.apply(G, st["E"], solved)
            st["applied"] = h
        try:
            r = routing.route_utci(G, s, t, K, closed)
        except routing.RouteError as e:
            raise HTTPException(422, str(e))
    else:
        weights = {"w_heat": w["w_heat"], "w_wet": w["w_wet"],
                   "direct_radiation": w["direct_radiation"]}
        try:
            r = routing.route(G, s, t, h, weights, closed)
        except routing.RouteError as e:
            raise HTTPException(422, str(e))
    direct = w["direct_radiation"]

    def _thermal(path):
        """Path thermal state. `stress_load` (degC-min) is the headline; the
        length-weighted means are kept as secondary, with UTCI computed OUTDOOR-ONLY so
        an air-conditioned arcade cannot drag the average down and read as free comfort.
        """
        if not use_utci:
            return {}
        es = [G[u][v] for u, v in zip(path, path[1:])]
        L = np.array([float(e["length"]) for e in es])
        out = thermal_summary(G, path)
        if L.sum() > 0:
            gw = lambda k: float((np.array([e.get(k, np.nan) for e in es]) * L).sum() / L.sum())
            out.update({"utci_mean": round(gw("_utci"), 2), "mrt_mean": round(gw("_mrt"), 2),
                        "stress_mean": round(gw("_stress"), 3)})
        return out

    def pack(path, extra=None):
        props = {"hour": h, "mode": mode}
        props.update(extra or {})
        summ = summarise(G, path, h, direct)
        summ.update(_thermal(path))
        return {"geojson": geojson(G, path, props), "summary": summ,
                "segments": segments(G, path, h)}

    eff = ({"K_effective": r["K_effective"]} if use_utci
           else {"w_heat_effective": r["w_heat_effective"]})
    routes = {"shaded": pack(r["path"], dict(eff, detour_ratio=r["ratio"],
                                             detour_capped=r["capped"])),
              "shortest": pack(r["shortest"])}
    # The pitch line: stress avoided, and the seconds it cost. Both routes are summarised
    # under the SAME applied hour and weather, so the difference is attributable.
    cmp_ = compare_thermal(routes["shaded"]["summary"], routes["shortest"]["summary"])
    if cmp_:
        cmp_["extra_m"] = round(routes["shaded"]["summary"]["distance_m"]
                                - routes["shortest"]["summary"]["distance_m"], 1)
        cmp_["extra_s"] = round(cmp_["extra_m"] / 1.35, 1)
        routes["shaded"]["summary"]["avoided"] = cmp_
    return {"routes": routes, "weather": w, "hour": h, "avoided": cmp_,
            "meta": {"snap_m": [round(ds, 1), round(dt, 1)],
                     "engine": "utci" if use_utci else "legacy",
                     "w_heat_requested": w["w_heat"],
                     "w_heat_effective": r.get("w_heat_effective"),
                     "K_effective": r.get("K_effective"),
                     # The per-transition prices are reported next to K because they are
                     # the other half of the same decision, and because the relaxation
                     # loop sheds K alone: door_m and level_jump_m are what the walker
                     # pays, not a preference we trade away when a route runs long.
                     "door_m": r.get("door_m"), "level_jump_m": r.get("level_jump_m"),
                     "detour_ratio": r["ratio"], "detour_capped": r["capped"],
                     "relax_attempts": r["attempts"],
                     "availability": dict(hours.describe(h, dw), enforced=respect_hours),
                     # Additive, and deliberately on every response: any number taken out
                     # of this API is only evidence alongside the config that made it.
                     "provenance": S.get("prov"),
                     "ms": round((time.time() - t0) * 1000, 1)}}


@app.get("/shade/{hour}.png")
def shade_png(hour: int, mode: str = Query("summer")):
    """The overlay must come from the SAME raster set the router priced the route on.

    These pngs used to be served straight out of out/, which is the LEGACY set -- flat
    8 m crowns extruded to the pavement, blocking 0.7 -- while the engine has been
    costing edges off out/v2/. That put two different shade models on one screen and
    invited the difference between them to be read as a result. Resolve the directory
    through engine._shade_path so the picture and the number cannot drift apart.
    """
    from . import engine as _e
    wm = "summer" if mode != "winter" else "winter"
    cands = [_e._shade_path(hour, _shade_key(wm)).replace(".npy", ".png"),
             f"{OUT}/shade_{hour:02d}.png", f"{OUT}/shade_{hour}.png"]
    for p in cands:
        if os.path.exists(p):
            return FileResponse(p, media_type="image/png")
    raise HTTPException(404, f"no shade overlay for hour {hour} yet")


@app.get("/provenance")
def get_provenance(mode: str = Query("summer")):
    """Which configuration produced the numbers this API is returning.

    Any figure quoted from a screenshot, a demo or a doc should be quotable WITH this,
    or it is a number against a moving reference. `line` is the compact form meant to
    sit under a table; the rest is the full stamp.
    """
    import provenance as _p
    # Stamp the set the ROUTER would resolve, not the one the legacy mode table names.
    # Those diverge the moment a date has its own generated set, and a provenance line
    # that names the wrong rasters is worse than none.
    s = _p.stamp(mode=_shade_key("summer" if mode != "winter" else "winter"))
    return {"line": _p.line(s), **s}


@app.get("/shade/bounds.json")
def shade_bounds():
    p = f"{OUT}/shade_bounds.json"
    if os.path.exists(p):
        return json.load(open(p))
    raise HTTPException(404, "out/shade_bounds.json not generated yet")


os.makedirs(OUT, exist_ok=True)
app.mount("/static", StaticFiles(directory=OUT), name="static")

if os.path.isdir(WEB) and os.path.exists(f"{WEB}/index.html"):
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
else:
    @app.get("/")
    def root():
        return JSONResponse({"ok": True, "app": "ShadeMe",
                             "note": "web/index.html not present yet",
                             "endpoints": ["/health", "/places", "/weather", "/route",
                                           "/shade/{hour}.png", "/static/..."]})
