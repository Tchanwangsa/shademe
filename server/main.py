"""Laneway API. Real-time cool-route options for the Melbourne CBD.

The physics lives in engine/cost/routing/weather/hours and is unchanged. This module is
only the surface the mobile client talks to, and it makes three commitments the old
demo API did not:

  * REAL TIME, NOT A SCRUBBER. There is no `hour` parameter. Every request is priced at
    the wall clock in Australia/Melbourne, clamped to the 06..20 window the shade rasters
    cover. A demo that can be dialled to its best hour is not evidence.
  * OPTIONS, NOT A PAIR. `/routes` walks a ladder of K -- the single thermal-preference
    knob -- and returns the distinct paths that fall out of it. Two K values that produce
    the same walk collapse to one option, which is the honest answer when there is no
    cooler way to go.
  * NO FIGURE WITHOUT ITS CONFIG. `meta.provenance` rides on every response.
"""
import os, sys, time, pickle
from datetime import datetime

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT, DATA = f"{ROOT}/out", f"{ROOT}/data"
sys.path.insert(0, f"{ROOT}/scripts")

from . import weather                                                        # noqa: E402
from . import routing                                                        # noqa: E402
from . import hours                                                          # noqa: E402
from .cost import summarise, segments, thermal_summary, compare_thermal      # noqa: E402

# THE API SERVES TODAY. weather.SUMMER_DATE is pinned to a January demo day so that the
# benchmarks and the provenance ladder stay reproducible -- scripts/ import weather
# directly and must keep getting that fixed day. The live API is the one place that
# should not: pricing the current hour against a day seven months ago is exactly the
# "real time" half-truth this rewrite was meant to remove. Overriding the constant here
# leaves every script untouched, and the archive call for today simply misses (the
# archive lags a few days) and falls through to the forecast endpoint, which serves it.
#
# The shade set follows wx["date"], so the first request on a new day regenerates it
# (~13 s) rather than costing today's walk on January shadows.
if not os.environ.get("SHADEME_SUMMER_DATE"):
    import pandas as _pd
    weather.SUMMER_DATE = str(_pd.Timestamp.now(tz=weather.TZ).date())

# The shade rasters cover 06:00-20:00. Outside it we clamp and say so in `meta.clamped`
# rather than quietly pricing 23:00 on the 20:00 sun.
FIRST_HOUR, LAST_HOUR = 6, 20
FALLBACK_HOUR = 16          # only for the graph-rebuild path, never for pricing

# The thermal-preference ladder. K is the one free knob in the cost function; 0 is
# "shortest, no preference" (routing.route_utci short-circuits to the plain shortest path)
# and 0.30 is about as far as the 1.4x detour cap will let a route wander.
K_LADDER = (0.0, 0.03, 0.10, 0.30)

# Two options closer than both of these are the same walk as far as the walker is
# concerned, and are shown as one card. 1 minute and 5 degC-minutes: below the
# resolution of a walking-time estimate, and below the dose of a single street crossing.
SAME_WALK_MIN = 1.0
SAME_WALK_DOSE = 5.0

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

S = {}          # graph, node arrays, places, provenance
ENG = {}        # per-mode engine state (edge index + surface march), built lazily


# --- graph ----------------------------------------------------------------------

def load_graph():
    p = f"{OUT}/graph.pkl"
    if os.path.exists(p):
        stamp = time.strftime("%H:%M", time.localtime(os.path.getmtime(p)))
        return pickle.load(open(p, "rb")), f"out/graph.pkl ({stamp})"
    import build_graph
    have = [h for h in range(FIRST_HOUR, LAST_HOUR + 1)
            if os.path.exists(f"{OUT}/shade_{h:02d}.npy")]
    if not have:
        raise RuntimeError("no graph.pkl and no hourly shade rasters -- run "
                           "scripts/build_graph.py before starting the API")
    return (build_graph.build(hours=have, verbose=False),
            f"fallback build_graph.build() hourly shade {have[0]}-{have[-1]}")


def nearest(lat, lon, max_m=None):
    x, y = S["tf"].transform(lon, lat)
    d2 = (S["X"] - x) ** 2 + (S["Y"] - y) ** 2
    i = int(np.argmin(d2))
    dist = float(np.sqrt(d2[i]))
    if max_m is not None and dist > max_m:
        return None, dist
    return S["ids"][i], dist


app = FastAPI(title="Laneway")
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
    ids = list(main)                  # snap only to the main component; islands are traps
    xy = np.array([G.nodes[n]["xy"] for n in ids], dtype=float)
    S.update(G=G, ids=ids, X=xy[:, 0], Y=xy[:, 1], source=src,
             tf=Transformer.from_crs(WGS84, MGA55, always_xy=True))
    places, dropped = [], []
    for name, lat, lon in PLACES_RAW:
        n, d = nearest(lat, lon, 100.0)
        (places if n else dropped).append({"name": name, "lat": lat, "lon": lon,
                                           "node": n, "snap_m": round(d, 1)})
    S["places"] = places
    print(f"[laneway] graph {G.number_of_nodes()} nodes / {G.number_of_edges()} edges "
          f"from {src} in {time.time() - t0:.1f}s")
    print(f"[laneway] places ok={len(places)} dropped={[d['name'] for d in dropped]}")
    try:
        print(f"[laneway] weather: {weather.block(now_hour(), 'summer')['source']}")
    except Exception as e:
        print(f"[laneway] weather prewarm failed: {e}")
    # NO PROVENANCE STAMP HERE. It cannot be known until a request fixes the day, and
    # therefore the shade set. It is stamped in engine_state() instead.


# --- time and conditions --------------------------------------------------------

def now_local():
    import pandas as pd
    return pd.Timestamp.now(tz=weather.TZ)


def now_hour():
    return max(FIRST_HOUR, min(LAST_HOUR, now_local().hour))


def condition_code(w):
    """A coarse sky state for the client's weather glyph.

    Derived from cloud cover and precipitation, not from an Open-Meteo weather code --
    the cached payload does not carry one, and inventing a richer taxonomy than the
    inputs support would be decoration.
    """
    if w["precipitation"] >= 0.5:
        return "rain"
    if w["precipitation"] > 0.0:
        return "drizzle"
    if w["cloud_cover"] >= 80:
        return "cloudy"
    if w["cloud_cover"] >= 30:
        return "partly_cloudy"
    return "sunny"


def conditions_block():
    h = now_hour()
    w = weather.block(h, "summer")
    t = now_local()
    return {
        "as_of": t.isoformat(),
        "hour": h,
        "clamped": h != t.hour,
        # The day actually priced. Normally today; pinned only if SHADEME_SUMMER_DATE is
        # set. Exposed so the client can say which day it is showing rather than imply
        # the reading is live when it is not.
        "date": weather.SUMMER_DATE,
        "is_today": weather.SUMMER_DATE == str(t.date()),
        "temperature": w["temperature"],
        "apparent_temperature": w["apparent_temperature"],
        "uv_index": w["uv_index"],
        "condition": condition_code(w),
        "cloud_cover": w["cloud_cover"],
        "precipitation": w["precipitation"],
        "wind_speed": w["wind_speed"],
        "relative_humidity": w["relative_humidity"],
        "direct_radiation": w["direct_radiation"],
        # Kept visible: the level correction is the 28% win and the raw feed is what
        # Open-Meteo actually said. A client that wants to show one must be able to
        # tell them apart.
        "temperature_raw": w["temperature_raw"],
        "bias_mode": w["bias_mode"],
        "ta_bias_offset": w["ta_bias_offset"],
        "rh_is_fallback": w["rh_is_fallback"],
        "source": w["source"],
    }, w, h


# --- engine state ---------------------------------------------------------------

def engine_state():
    """Edge index + surface energy-balance march, cached until the weather payload moves.

    edge_index() is ~1 s and attach_tsurf() is the ~38 s march, so both are lazy: the
    first route request pays for them, startup does not.
    """
    from . import engine as _e
    wx = weather.apply_bias(weather.get("summer"))
    # THE SHADE SET FOLLOWS THE DATE BEING PRICED, not a mode name, so a request in
    # October cannot be costed on January shadows.
    sk = wx.get("date") or "summer"
    key = (sk, wx.get("ts"), wx.get("bias", {}).get("mode"))
    st = ENG.get("summer")
    if st and st["key"] == key:
        return st
    t0 = time.time()
    E = _e.edge_index(S["G"], mode=sk)
    _e.attach_tsurf(E, S["G"], wx, mode=sk)
    st = {"E": E, "wx": wx, "key": key, "solved": {}, "applied": None,
          "prov": _provenance_for(sk)}
    ENG["summer"] = st
    print(f"[laneway] engine state built in {time.time() - t0:.1f}s")
    print(f"[laneway] provenance {st['prov']}")
    return st


def _provenance_for(shade_key):
    """Stamp the config for the shade set ACTUALLY being priced.

    Stamping once at startup against the default mode was wrong in a way the stamp
    exists to catch: `provenance.stamp()` defaults to mode="summer", which resolves to
    out/v2 (sun 2026-01-26), while a request today resolves through _dirs_for() to
    out/v2_winter (sun 2026-08-22). The response would have carried a January shade
    digest next to a route costed on August shadows -- a figure quoted with someone
    else's config, which is worse than a figure quoted with none.

    Digests are cached on (size, mtime), so this is only expensive the first time.
    """
    try:
        import provenance as _p
        return _p.line(_p.stamp(mode=shade_key))
    except Exception as e:
        print(f"[laneway] provenance stamp failed: {e}")
        return None


def apply_hour(h, w):
    """Stash per-edge UTCI and stress on the graph for hour `h`. Cached per hour."""
    from . import engine as _e
    st = engine_state()
    solved = st["solved"].get(h)
    if solved is None:
        solved = _e.solve(st["E"], w, st["wx"], h)
        st["solved"][h] = solved
    if st["applied"] != h:
        _e.apply(S["G"], st["E"], solved)
        st["applied"] = h
    return st


# --- routes ---------------------------------------------------------------------

def describe(G, path, h, w):
    """Everything one option needs, under the hour and weather already applied."""
    out = summarise(G, path, h, w["direct_radiation"])
    out.update(thermal_summary(G, path))
    es = [G[u][v] for u, v in zip(path, path[1:])]
    L = np.array([float(e["length"]) for e in es])
    if L.sum() > 0:
        gw = lambda k: float((np.array([e.get(k, np.nan) for e in es]) * L).sum() / L.sum())
        out["utci_mean"] = round(gw("_utci"), 2)
        out["mrt_mean"] = round(gw("_mrt"), 2)
    # Time in unshaded outdoor sun, which is what a walker actually feels the length of.
    out["sun_minutes"] = round(out["sun_m"] / 1.35 / 60.0, 1)
    return out


def label_for(opt, coolest_id, shortest_id):
    if opt["id"] == coolest_id:
        return "Coolest"
    if opt["id"] == shortest_id:
        return "Shortest"
    return "Balanced"


@app.get("/health")
def health():
    G = S.get("G")
    if G is None:
        raise HTTPException(503, "graph not loaded")
    return {"ok": True, "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
            "graph_source": S["source"], "places": len(S["places"]),
            "hour": now_hour(), "date": weather.SUMMER_DATE,
            # None until the first route request builds an engine state -- there is no
            # config to report before one has been chosen.
            "provenance": (ENG.get("summer") or {}).get("prov")}


@app.get("/places")
def places():
    return [{"name": p["name"], "lat": p["lat"], "lon": p["lon"]} for p in S["places"]]


@app.get("/conditions")
def get_conditions():
    block, _, _ = conditions_block()
    return block


@app.get("/routes")
def get_routes(from_lat: float, from_lon: float, to_lat: float, to_lon: float,
               respect_hours: bool = Query(True)):
    """Distinct walking options between two points, priced at the current hour."""
    t0 = time.time()
    G = S["G"]
    cond, w, h = conditions_block()
    dw = hours.now_dow()
    # Opening hours are a hard gate, not a cost: a shut arcade is an absent edge. Applied
    # identically to every option so the comparison between them stays attributable.
    closed = hours.closed_keys(h, dw) if respect_hours else set()

    s, ds = nearest(from_lat, from_lon, 300.0)
    t, dt = nearest(to_lat, to_lon, 300.0)
    if s is None or t is None:
        # Name the end that is off, and say it in a unit a person reads. This is the
        # error a real user hits most -- standing outside the CBD, or a simulator
        # defaulting to Cupertino -- so "start 15986795 m from nearest node" is the
        # wrong thing to put in front of them. The metres stay, in the parenthesis.
        which = "Both ends are" if (s is None and t is None) else \
                ("The starting point is" if s is None else "The destination is")
        far = max(d for d, n in ((ds, s), (dt, t)) if n is None)
        away = f"{far / 1000:.0f} km" if far >= 1000 else f"{far:.0f} m"
        raise HTTPException(400, f"{which} outside the area Laneway covers. "
                                 f"It only knows the Melbourne CBD, and this is "
                                 f"{away} from the nearest walkable street.")
    st = apply_hour(h, w)

    # Walk the K ladder. solve() is cached per hour, so each extra K is one more A* over
    # a graph that is already warm -- a few ms, not a few seconds.
    seen, opts, baseline = {}, [], None
    for K in K_LADDER:
        try:
            r = routing.route_utci(G, s, t, K, closed)
        except routing.RouteError as e:
            # Same treatment as the off-network case above: the graph's vocabulary is not
            # the user's. "Snap to the same node" means the two pins landed on one corner.
            msg = ("Those two points are close enough to be the same spot — "
                   "they land on the same street corner."
                   if "same graph node" in str(e) else str(e))
            raise HTTPException(422, msg)
        if baseline is None:
            baseline = describe(G, r["shortest"], h, w)
        key = tuple(r["path"])
        if key in seen:
            # Same walk as a lower K already produced. Record that this K reached it and
            # move on -- collapsing it is the honest answer, not padding the list.
            seen[key]["K_reached"].append(K)
            continue
        summ = describe(G, r["path"], h, w)
        avoided = compare_thermal(summ, baseline)
        avoided["extra_m"] = round(summ["distance_m"] - baseline["distance_m"], 1)
        avoided["extra_s"] = round(avoided["extra_m"] / 1.35, 1)
        # Secondary to the dose, and outdoor-only on both sides: an air-conditioned
        # arcade must not drag a mean down and read as free comfort.
        a, b = summ.get("utci_mean_outdoor"), baseline.get("utci_mean_outdoor")
        avoided["utci_outdoor_delta"] = round(b - a, 2) if (a is not None and b is not None) else None
        opt = {
            "id": f"k{int(K * 100):03d}",
            "K": K,
            "K_effective": r["K_effective"],
            "K_reached": [K],
            "is_shortest": list(r["path"]) == list(r["shortest"]),
            "detour_ratio": r["ratio"],
            "detour_capped": r["capped"],
            "relax_attempts": r["attempts"],
            "door_m": r["door_m"],
            "level_jump_m": r["level_jump_m"],
            "summary": summ,
            "avoided": avoided,
            "geometry": {"type": "LineString",
                         "coordinates": [list(G.nodes[n]["ll"]) for n in r["path"]]},
            "segments": segments(G, r["path"], h),
        }
        seen[key] = opt
        opts.append(opt)

    # Drop dominated options: a walk that is BOTH slower and hotter than another one on
    # the list is not a choice, it is noise. This is not cosmetic -- the mid-ladder K
    # values genuinely produce such routes here, because route_utci short-circuits at
    # K <= 0 and hands back the plain shortest path WITHOUT charging its door crossings,
    # while every K > 0 search pays DOOR_PENALTY_M per door. Where the shortest path
    # happens to run through an arcade, the baseline gets that arcade for free and the
    # K > 0 searches are pushed out of it, landing hotter than the thing they are
    # supposed to improve on. The count is reported rather than swallowed.
    kept = [o for o in opts if not any(
        other is not o
        and other["summary"]["minutes"] <= o["summary"]["minutes"]
        and other["summary"].get("stress_load", 0.0) <= o["summary"].get("stress_load", 0.0)
        and (other["summary"]["minutes"] < o["summary"]["minutes"]
             or other["summary"].get("stress_load", 0.0) < o["summary"].get("stress_load", 0.0))
        for other in opts)]
    dominated = len(opts) - len(kept)
    opts = kept
    # Coolest = least thermal dose, not least mean temperature. Ties break on time.
    opts.sort(key=lambda o: (o["summary"].get("stress_load", 0.0), o["summary"]["minutes"]))
    # Collapse near-duplicates. Two walks that differ by six seconds and half a degC-min
    # are the same walk to the person doing it, and offering both as cards makes the
    # list look precise rather than useful. The survivor is the cooler one -- the list is
    # already sorted that way -- and it absorbs the other's K so nothing is lost.
    merged, near = [], 0
    for o in opts:
        twin = next((m for m in merged
                     if abs(m["summary"]["minutes"] - o["summary"]["minutes"]) <= SAME_WALK_MIN
                     and abs(m["summary"].get("stress_load", 0.0)
                             - o["summary"].get("stress_load", 0.0)) <= SAME_WALK_DOSE), None)
        if twin:
            twin["K_reached"].extend(o["K_reached"])
            near += 1
        else:
            merged.append(o)
    opts = merged
    coolest_id = opts[0]["id"]
    shortest_id = next((o["id"] for o in opts if o["is_shortest"]), None)
    for o in opts:
        o["label"] = label_for(o, coolest_id, shortest_id)
    # One option means there is no cooler way to go right now. Say it plainly rather
    # than manufacturing a second card.
    return {
        "conditions": cond,
        "options": opts,
        "meta": {
            "snap_m": [round(ds, 1), round(dt, 1)],
            "hour": h,
            "as_of": cond["as_of"],
            "k_ladder": list(K_LADDER),
            "distinct_paths": len(opts),
            "dominated_dropped": dominated,
            "near_duplicates_merged": near,
            "availability": dict(hours.describe(h, dw), enforced=respect_hours),
            "detour_cap": routing.DETOUR_CAP,
            # Additive and deliberately on every response: any number taken out of this
            # API is only evidence alongside the config that made it. Taken from the
            # engine state so it describes the shade set this route was priced on.
            "provenance": st["prov"],
            "ms": round((time.time() - t0) * 1000, 1),
        },
    }
