"""ShadeMe API. Real-time cool-route options for the Melbourne CBD.

The physics lives in engine/cost/routing/weather/hours; this module is only the surface
the mobile client talks to. It makes three commitments:

  * REAL TIME, NOT A SCRUBBER. There is no `hour` parameter. Every request is priced at
    the wall clock in Australia/Melbourne, clamped to the 06..20 window the shade rasters
    cover. A demo that can be dialled to its best hour is not evidence.
  * OPTIONS, NOT A PAIR. `/routes` walks a ladder of K -- the thermal knob -- and a
    second ladder under the UV objective, and returns the distinct paths that fall out of
    both. Two searches producing the same walk collapse to one option, which is the
    honest answer when there is no better way to go.
  * NO FIGURE WITHOUT ITS CONFIG. `meta.provenance` rides on every response.
  * ONLY REACHABLE PLACES ARE OFFERED. `/search` geocodes free text against
    OpenStreetMap and then drops every match the walking graph cannot reach, so the
    picker cannot hand `/routes` a destination it will refuse.
  * NO HARDCODED PLACES. There is no curated landmark list and no `/places`; everything
    offered comes from OSM, and each row carries OSM's own `opening_hours` verdict as
    `open_now` (true / false / null, where null means the tag is missing, not open).
"""
import os, time, pickle, threading
from contextlib import asynccontextmanager

import networkx as nx
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import weather
from . import routing
from . import hours
from . import geocode
from . import osm_hours
from .cost import (summarise, segments, thermal_summary, compare_thermal,
                   uv_summary, compare_uv)
from ..paths import OUT

# The shade rasters cover 06:00-20:00. Outside it we clamp and say so in `meta.clamped`
# rather than quietly pricing 23:00 on the 20:00 sun. The day priced is today unless
# SHADEME_DATE pins it; the shade set follows that date, so the first request on a new
# day regenerates it (~13 s) rather than costing today's walk on yesterday's shadows.
FIRST_HOUR, LAST_HOUR = 6, 20
FALLBACK_HOUR = 16          # only for the graph-rebuild path, never for pricing

# The thermal ladder. 0 is "shortest, no preference" (routing.route_utci short-circuits
# to the plain shortest path) and 0.30 is about as far as the 1.4x detour cap allows.
K_LADDER = (0.0, 0.03, 0.10, 0.30)

# The UV ladder, walked alongside it. A SECOND OBJECTIVE, not a second tuning of the
# first -- see cost.uv_cost. UV has no air-temperature term, so it stays informative on a
# cold clear day when every UTCI on the graph is inside the no-stress band and the thermal
# ladder collapses to one card. Melbourne sits at UV 3+ for most of the year. K_uv is
# "how much further to swap full sun for full cover", so 0.40 is already at the cap.
K_UV_LADDER = (0.10, 0.25, 0.40)

# Two options closer than ALL of these are the same walk to the person doing it, and are
# shown as one card. 1 minute is below the resolution of a walking-time estimate, 5
# degC-minutes below the dose of a street crossing, and 2 UV index-minutes is 0.03 SED --
# about 1% of a fair-skin burn threshold.
SAME_WALK_MIN = 1.0
SAME_WALK_DOSE = 5.0
SAME_WALK_UV = 2.0

CORS_ORIGINS = [o.strip() for o in os.environ.get("SHADEME_CORS_ORIGINS", "*").split(",")
                if o.strip()]

# How far a searched place may sit from the nearest walkable node. THE SAME REACH
# `/routes` allows, and that is the whole point: the picker's filter and the router's
# gate have to be one number, or search offers destinations routing then rejects.
SEARCH_SNAP_M = 300.0

# How far the origin passed to /search may be before its distances stop meaning anything.
# Generous -- 50 km is all of greater Melbourne, and someone in Werribee asking how far
# Emporium is deserves an answer. It exists for the other case: a simulator defaults to
# Cupertino, and every row then read "15899.57 km", which is worse than no distance at
# all. Beyond this the origin is dropped and the results come back unmeasured and in the
# geocoder's own order.
NEAR_MAX_M = 50_000.0

# NO CURATED PLACE LIST. There was one -- fifteen hardcoded landmarks behind `/places` --
# and it was removed: it duplicated a worse version of what `/search` already reaches, it
# went stale the moment a venue moved, and an empty box that suggests fifteen places is a
# worse answer than one that shows what you actually searched before. The client keeps its
# own recent searches on the device; the server holds no list at all.

S = {}          # graph, node arrays, places, provenance
ENG = {}        # per-day engine state, keyed on the day being priced
LAST = {}       # {"st": the most recently built state}, whatever day. See engine_state.

# TWO LOCKS, NOT ONE, and the split is the whole point of serve-while-rebuilding.
#
#   BUILD_LOCK serialises the ~40 s surface march, so two threads noticing the same stale
#   state cannot run it twice and overwrite each other.
#   APPLY_LOCK serialises solve() and apply(), which write per-edge fields onto the shared
#   graph -- microseconds, but two requests at different hours interleaving there would
#   leave one of them routing over the other one's edges.
#
# Under ONE lock a request could not price anything while a rebuild held it, which is
# exactly the 40 s wait this is here to remove. The build reads the graph's geometry and
# writes only its own arrays; apply writes only the `_`-prefixed edge fields; they do not
# touch the same state, so they do not need to exclude each other.
BUILD_LOCK = threading.RLock()
APPLY_LOCK = threading.RLock()

# Set by a request that found the state stale, to bring the warm thread's next pass
# forward from "up to WARM_EVERY_S away" to "now". The request itself does not wait for
# it -- it is served from LAST while the rebuild runs behind it.
WAKE = threading.Event()

# What the prewarm thread is doing, reported by /health. Not decoration: "the first route
# is still slow" and "the engine failed to build" look identical from the client without
# it.
WARM = {"state": "cold", "ms": None, "error": None}

# How often the warm thread checks, when nothing wakes it sooner. Well under weather.TTL
# (600 s), so the state is rebuilt within a couple of minutes of the payload moving
# rather than on the first request after it. A pass over an unchanged payload is a dict
# lookup.
WARM_EVERY_S = float(os.environ.get("SHADEME_PREWARM_S", "120"))


# --- graph ----------------------------------------------------------------------

def load_graph():
    p = f"{OUT}/graph.pkl"
    if os.path.exists(p):
        stamp = time.strftime("%H:%M", time.localtime(os.path.getmtime(p)))
        return pickle.load(open(p, "rb")), f"out/graph.pkl ({stamp})"
    from ..pipeline import graph as build_graph
    have = [h for h in range(FIRST_HOUR, LAST_HOUR + 1)
            if os.path.exists(f"{OUT}/shade_{h:02d}.npy")]
    if not have:
        raise RuntimeError(
            "no graph.pkl and no hourly shade rasters -- run "
            "`uv run python -m shademe.pipeline.build_all` before starting the API. "
            "It builds all ten pipeline stages in order; on a fresh clone that takes "
            "a while and needs network for the CoM and Overpass downloads.")
    return (build_graph.build(hours=have, verbose=False),
            f"fallback graph.build() hourly shade {have[0]}-{have[-1]}")


# Snap targets per gate state, keyed on the frozen set of shut classes. At most a
# handful of distinct sets exist (four classes), so this is bounded and never evicted.
_SNAP = {}


def open_snap(closed):
    """The nodes it is safe to snap onto once `closed` classes are removed.

    THE PICKER AND THE ROUTER HAVE TO AGREE. hours.py gates shut arcades out of the graph
    before any cost is evaluated, but snapping used to run on the FULL graph -- so at
    19:00 on a Sunday a lat/lon inside Melbourne Central snapped to a node in the shut
    mall, and /routes then correctly reported no path. The picker offered a destination
    routing refused: a 422 on the most obvious landmark in the app.

    Restricting the snap to the largest component of the GATED graph makes that
    structurally impossible. A closed venue now snaps to the nearest node that is still
    reachable -- its street entrance -- which is the honest answer: you can still walk
    there, you just cannot walk through it.
    """
    key = frozenset(closed or ())
    hit = _SNAP.get(key)
    if hit is not None:
        return hit
    if not key:
        hit = (S["ids"], S["X"], S["Y"])
    else:
        G = S["G"]
        H = nx.subgraph_view(
            G, filter_edge=lambda u, v: G[u][v].get("hours_key") not in key)
        comp = max(nx.connected_components(H), key=len)
        ids = [n for n in S["ids"] if n in comp]
        xy = np.array([G.nodes[n]["xy"] for n in ids], dtype=float)
        hit = (ids, xy[:, 0], xy[:, 1])
        print(f"[shademe] snap set for closed={sorted(key)}: "
              f"{len(ids)} of {len(S['ids'])} nodes reachable")
    _SNAP[key] = hit
    return hit


def closed_now():
    """The gate as it stands this minute -- what /search and /reverse snap against."""
    return hours.closed_keys(now_hour(), hours.now_dow())


def nearest(lat, lon, max_m=None, closed=None):
    ids, X, Y = open_snap(closed)
    x, y = S["tf"].transform(lon, lat)
    d2 = (X - x) ** 2 + (Y - y) ** 2
    i = int(np.argmin(d2))
    dist = float(np.sqrt(d2[i]))
    if max_m is not None and dist > max_m:
        return None, dist
    return ids[i], dist


def metres(lat1, lon1, lat2, lon2):
    """Straight-line metres between two lat/lon, through the same projection the graph
    is in. Planar rather than great-circle: MGA55 is what the CBD is measured in, and
    over a few kilometres the difference is centimetres."""
    x1, y1 = S["tf"].transform(lon1, lat1)
    x2, y2 = S["tf"].transform(lon2, lat2)
    return float(np.hypot(x2 - x1, y2 - y1))


def _startup():
    t0 = time.time()
    from pyproj import Transformer
    from ..config import WGS84, MGA55
    import networkx as nx
    G, src = load_graph()
    main = G.graph.get("main") or max(nx.connected_components(G), key=len)
    ids = list(main)                  # snap only to the main component; islands are traps
    xy = np.array([G.nodes[n]["xy"] for n in ids], dtype=float)
    S.update(G=G, ids=ids, X=xy[:, 0], Y=xy[:, 1], source=src,
             tf=Transformer.from_crs(WGS84, MGA55, always_xy=True))
    print(f"[shademe] graph {G.number_of_nodes()} nodes / {G.number_of_edges()} edges "
          f"from {src} in {time.time() - t0:.1f}s")
    try:
        print(f"[shademe] weather: {weather.block(now_hour())['source']}")
    except Exception as e:
        print(f"[shademe] weather prewarm failed: {e}")
    # NO PROVENANCE STAMP HERE. It cannot be known until a request fixes the day, and
    # therefore the shade set. It is stamped in engine_state() instead.
    if os.environ.get("SHADEME_NO_PREWARM", "").strip() not in ("", "0", "false"):
        print("[shademe] prewarm disabled -- the first /routes will pay for the march")
        return
    threading.Thread(target=prewarm, name="shademe-prewarm", daemon=True).start()


def warm_once():
    """Build the engine state for right now, if it is not already built.

    Everything expensive the engine needs -- the shade set for the date, the edge index,
    the surface energy-balance march, the solve for the current hour -- depends only on
    the day, the weather and the clock. NONE of it depends on which two points get asked
    for, so none of it has to be paid for by whoever asks first.

    apply() is deliberately NOT called here: it writes onto the shared graph and belongs
    to the request that picked the hour. solve() is the expensive half, and it caches.
    """
    t0 = time.time()
    WARM.update(state="building", error=None)
    from . import engine as _e
    h = now_hour()
    w = weather.block(h, now_hour=now_local().hour)
    st = engine_state()
    with APPLY_LOCK:
        if h not in st["solved"]:
            st["solved"][h] = _e.solve(st["E"], w, st["wx"], h)
    WARM.update(state="ready", ms=round((time.time() - t0) * 1000, 1))
    return time.time() - t0


def prewarm():
    """Keep the engine warm, in the background, from the moment the server comes up.

    THE 40 s BELONGS TO THE SERVER, NOT TO THE FIRST WALK. Left lazy it landed on
    whoever searched first, which is the worst possible moment to spend it: they have
    just picked a destination and are watching a spinner.

    A THREAD, NOT A BLOCKING STARTUP. `/health`, `/search` and `/conditions`
    need none of this, so the server answers them while the march runs. A route request
    arriving mid-build waits on ENG_LOCK and then gets that same state -- slow that once,
    never twice, and never a second march beside the first.

    A LOOP, NOT ONE SHOT, because the state does not stay warm on its own: it is keyed on
    the weather payload, and weather.TTL re-fetches every 10 minutes, so a startup-only
    warm goes cold again ten minutes in and the march lands back on a request. Each pass
    is a dict lookup when nothing has moved, ~1 s of edge index when only the fetch
    timestamp has, and the full march only when Open-Meteo's numbers for the day actually
    change -- which is exactly when someone would otherwise have waited for it.

    The wait between passes is interruptible: a request that finds the state stale sets
    WAKE and is served from the previous state, and this comes round immediately rather
    than up to WARM_EVERY_S later. So the stale window is as short as the march is, not
    as long as the poll interval.
    """
    while True:
        try:
            dt = warm_once()
            if dt > 2.0:                      # a real rebuild, not a cache hit
                print(f"[shademe] engine warm for hour {now_hour():02d} in {dt:.1f}s")
        except Exception as e:
            # A failed warm is not a failed server: the request path still builds its own
            # state, and raises there, where the client can see why.
            WARM.update(state="failed", error=str(e))
            print(f"[shademe] prewarm failed ({e}) -- the next /routes will pay for it")
        WAKE.wait(WARM_EVERY_S)
        WAKE.clear()


@asynccontextmanager
async def lifespan(app):
    _startup()
    yield


app = FastAPI(title="ShadeMe", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"])


# --- time and conditions --------------------------------------------------------

def now_local():
    import pandas as pd
    return pd.Timestamp.now(tz=weather.TZ)


def now_hour():
    return max(FIRST_HOUR, min(LAST_HOUR, now_local().hour))


def condition_code(w):
    """A coarse sky state for the client's glyph.

    LED BY THE BEAM, not by cloud cover. Melbourne reported 78% cloud with 438 W/m2 of
    direct radiation on one test day -- thin high cloud the sun comes straight through --
    and going by cloud alone drew a "partly cloudy" glyph over a city that was sunny.
    The beam is also the quantity this product is about: if it is landing, shade is worth
    walking for, and the icon should agree with the routing.
    """
    if w["precipitation"] >= 0.5:
        return "rain"
    if w["precipitation"] > 0.0:
        return "drizzle"
    direct, diffuse = w["direct_radiation"], w["diffuse_radiation"]
    if direct + diffuse < 20:                  # dusk: no beam to read, fall back to cloud
        return "cloudy" if w["cloud_cover"] >= 60 else "sunny"
    if w["direct_fraction"] >= 0.5 and direct >= 100:
        return "sunny"
    if direct >= 40:
        return "partly_cloudy"
    return "cloudy"


def conditions_block():
    t = now_local()
    h = now_hour()
    # now_hour() is passed separately from the hour being priced: they differ only when
    # the wall clock falls outside 06-20, and the live UV measurement is only allowed to
    # answer for the real current hour. See uv.py.
    w = weather.block(h, now_hour=t.hour)
    return {
        "as_of": t.isoformat(),
        "hour": h,
        "clamped": h != t.hour,
        "date": w["date"],
        "is_today": w["date"] == str(t.date()),
        "temperature": w["temperature"],
        "apparent_temperature": w["apparent_temperature"],
        "uv_index": w["uv_index"],
        # Which of the three UV branches answered -- measured, feed, or modelled. A UV
        # number without this is not checkable against the reading on someone's phone.
        "uv_source": w["uv_source"],
        "uv_index_feed": w["uv_index_feed"],
        "condition": condition_code(w),
        "cloud_cover": w["cloud_cover"],
        "precipitation": w["precipitation"],
        "wind_speed": w["wind_speed"],
        "relative_humidity": w["relative_humidity"],
        "direct_radiation": w["direct_radiation"],
        "direct_fraction": w["direct_fraction"],
        # Kept visible: the level correction is the 28% win and the raw feed is what
        # Open-Meteo actually said. A client showing one must be able to tell them apart.
        "temperature_raw": w["temperature_raw"],
        "bias_mode": w["bias_mode"],
        "ta_bias_offset": w["ta_bias_offset"],
        "rh_is_fallback": w["rh_is_fallback"],
        "source": w["source"],
    }, w, h


# --- engine state ---------------------------------------------------------------

def engine_state(wait=True):
    """Edge index + surface energy-balance march, cached until the weather payload moves.

    edge_index() is ~1 s and attach_tsurf() is the ~40 s march, and NOBODY SHOULD EVER
    WAIT FOR EITHER. prewarm() builds them in the background as the server comes up and
    again whenever the weather moves; this is what it calls.

    `wait` is what a request passes as False. When the state is stale and a previous one
    exists, the caller gets THE PREVIOUS ONE and the rebuild is nudged to start now,
    rather than the caller queueing behind 40 s of march. What is actually stale in it is
    the surface and wall temperatures -- the air temperature, radiation and wind used to
    price the walk come from the live weather block the request already fetched -- and it
    is at most one weather.TTL old. `meta.engine_stale` says so on the response either
    way, because a number priced on a slightly older ground temperature is still a number
    that has to be attributable.

    With nothing built at all -- a cold start, before the first build lands -- there is
    no previous state to serve and the caller waits. That is the one unavoidable wait,
    and it happens once per server.
    """
    from . import engine as _e
    wx = weather.apply_bias(weather.get())
    # THE SHADE SET FOLLOWS THE DATE BEING PRICED, not a mode name, so an October request
    # cannot be costed on January shadows.
    sk = wx.get("date") or weather.resolve_day()
    key = (sk, wx.get("ts"), wx.get("bias", {}).get("mode"))
    st = ENG.get(sk)
    if st and st["key"] == key:
        return st
    if not wait:
        prev = LAST.get("st")
        if prev is not None:
            WAKE.set()           # rebuild now, behind this request rather than in it
            return prev
    with BUILD_LOCK:
        # Re-checked inside the lock: while we queued, the thread holding it may have
        # built exactly the state we came here for.
        st = ENG.get(sk)
        if st and st["key"] == key:
            return st
        t0 = time.time()
        E = _e.edge_index(S["G"], mode=sk)
        _e.attach_tsurf(E, S["G"], wx, mode=sk)
        st = {"E": E, "wx": wx, "key": key, "solved": {}, "applied": None,
              "prov": _provenance_for(sk)}
        ENG.clear()              # one day priced at a time; the march is 40 s and 7 MB
        ENG[sk] = st
        # Published in one assignment, and never cleared: a reader outside the lock must
        # never catch ENG between the clear and the insert and conclude there is nothing
        # to serve.
        LAST["st"] = st
        print(f"[shademe] engine state built in {time.time() - t0:.1f}s")
        print(f"[shademe] provenance {st['prov']}")
        return st


def _stamp(ts):
    """An epoch -> local ISO, or None. For saying WHEN a cached thing was fetched."""
    if not ts:
        return None
    import pandas as pd
    return pd.Timestamp(float(ts), unit="s", tz="UTC").tz_convert(weather.TZ).isoformat()


def _provenance_for(shade_key):
    """Stamp the config for the shade set ACTUALLY being priced.

    Stamping once at startup against a default mode was wrong in the way the stamp exists
    to catch: the response would carry a January shade digest next to a route costed on
    August shadows. Digests are cached on (size, mtime), so this is only expensive once.
    """
    try:
        from .. import provenance as _p
        return _p.line(_p.stamp(mode=shade_key))
    except Exception as e:
        print(f"[shademe] provenance stamp failed: {e}")
        return None


def solve_apply(st, h, w):
    """Stash per-edge UTCI and stress on the graph for hour `h`. Cached per hour.

    CALLERS MUST HOLD APPLY_LOCK, and must hold it until they have finished reading the
    graph. apply() writes the `_`-prefixed fields onto the shared graph and A* reads them
    right afterwards, so a caller that released in between could route over another
    request's edges -- which now matters in a way it did not before, because two requests
    can legitimately be holding DIFFERENT engine states while one is being rebuilt.
    """
    from . import engine as _e
    solved = st["solved"].get(h)
    if solved is None:
        solved = _e.solve(st["E"], w, st["wx"], h)
        st["solved"][h] = solved
    if st["applied"] != h:
        _e.apply(S["G"], st["E"], solved)
        st["applied"] = h


# --- routes ---------------------------------------------------------------------

def describe(G, path, h, w):
    """Everything one option needs, under the hour and weather already applied."""
    out = summarise(G, path, h, w["direct_radiation"])
    out.update(thermal_summary(G, path))
    out.update(uv_summary(G, path, w.get("uv_index")))
    es = [G[u][v] for u, v in zip(path, path[1:])]
    L = np.array([float(e["length"]) for e in es])
    if L.sum() > 0:
        gw = lambda k: float((np.array([e.get(k, np.nan) for e in es]) * L).sum() / L.sum())
        out["utci_mean"] = round(gw("_utci"), 2)
        out["mrt_mean"] = round(gw("_mrt"), 2)
    # Time in unshaded outdoor sun, which is what a walker feels the length of.
    out["sun_minutes"] = round(out["sun_m"] / 1.35 / 60.0, 1)
    return out


def dominates(a, b):
    """True if `a` is at least as good as `b` on EVERY axis and better on one.

    Three axes, not one: time, thermal dose and UV dose. The UV axis is not decoration --
    a route a minute longer and a degree-minute hotter can still be the only one keeping
    you out of the sun, and dropping it as "dominated" would delete the answer to the
    question the user asked.
    """
    ax = (a["summary"]["minutes"], a["summary"].get("stress_load", 0.0),
          a["summary"].get("uv_dose", 0.0))
    bx = (b["summary"]["minutes"], b["summary"].get("stress_load", 0.0),
          b["summary"].get("uv_dose", 0.0))
    return all(x <= y for x, y in zip(ax, bx)) and any(x < y for x, y in zip(ax, bx))


def same_walk(a, b):
    """True if two options are the same walk to the person doing it. See SAME_WALK_*."""
    sa, sb = a["summary"], b["summary"]
    return (abs(sa["minutes"] - sb["minutes"]) <= SAME_WALK_MIN
            and abs(sa.get("stress_load", 0.0) - sb.get("stress_load", 0.0)) <= SAME_WALK_DOSE
            and abs(sa.get("uv_dose", 0.0) - sb.get("uv_dose", 0.0)) <= SAME_WALK_UV)


def redundant(o, m):
    """True if `o` offers nothing over `m`: on NO axis does it win by a real margin.

    The Pareto filter is strict, so an option six seconds quicker and three UV-minutes
    worse survives it -- technically a trade, practically not one. This is the second
    sieve, against the same SAME_WALK_* margins, for the same reason: they are the point
    below which the walker cannot tell two walks apart.
    """
    so, sm = o["summary"], m["summary"]
    return (so["minutes"] > sm["minutes"] - SAME_WALK_MIN
            and so.get("stress_load", 0.0) > sm.get("stress_load", 0.0) - SAME_WALK_DOSE
            and so.get("uv_dose", 0.0) > sm.get("uv_dose", 0.0) - SAME_WALK_UV)


def label_options(opts, uv_index):
    """Award each option every label it has actually earned.

    A LIST, not one string. The coolest walk and the least-UV walk are often the same
    walk, and saying so is more informative than picking a winner; when they are not, the
    two badges are exactly the choice being offered. "Least UV" is withheld when there is
    no UV to avoid rather than pinned on an arbitrary card.
    """
    if not opts:
        return
    coolest = min(opts, key=lambda o: (o["summary"].get("stress_load", 0.0),
                                       o["summary"]["minutes"]))["id"]
    least_uv = min(opts, key=lambda o: (o["summary"].get("uv_dose", 0.0),
                                        o["summary"]["minutes"]))["id"]
    spread = max(o["summary"].get("uv_dose", 0.0) for o in opts) - \
        min(o["summary"].get("uv_dose", 0.0) for o in opts)
    uv_worth_naming = bool(uv_index) and uv_index > 0 and spread > SAME_WALK_UV
    for o in opts:
        labels = []
        if o["is_shortest"]:
            labels.append("Shortest")
        if o["id"] == coolest and o["summary"].get("stress_load", 0.0) > 0:
            labels.append("Coolest")
        if uv_worth_naming and o["id"] == least_uv:
            labels.append("Least UV")
        o["labels"] = labels


@app.get("/health")
def health():
    G = S.get("G")
    if G is None:
        raise HTTPException(503, "graph not loaded")
    return {"ok": True, "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
            "graph_source": S["source"],
            "hour": now_hour(), "date": weather.resolve_day(),
            # cold / building / ready / failed. A route asked for while this says
            # "building" is correct but slow -- it waits for the march to finish.
            "engine": WARM["state"], "engine_ms": WARM["ms"],
            "engine_error": WARM["error"],
            # The weather the current surface march was built from. Routes served while a
            # rebuild is running are priced on this one and say so in meta.engine_stale.
            "engine_as_of": _stamp((LAST.get("st") or {}).get("wx", {}).get("ts")),
            # None until an engine state exists -- there is no config to report before a
            # day has been chosen.
            "provenance": next((v.get("prov") for v in ENG.values()), None)}


def _place(name, lat, lon, address=None, kind=None, snap_m=None, distance_m=None,
           hours=None, open_now=None):
    """The one shape the client's `Place` maps to, whoever produced it -- an OSM search
    hit or a reverse-geocoded GPS fix.

    `open_now` is TRUE / FALSE / NULL and null is a real answer: OSM tags opening_hours on
    some places and not others, and Photon does not return it at all. Null means "we do
    not know", and the client shows no badge -- never a Closed one."""
    return {"name": name, "lat": round(float(lat), 6), "lon": round(float(lon), 6),
            "address": address, "kind": kind,
            "opening_hours": hours, "open_now": open_now,
            "snap_m": None if snap_m is None else round(float(snap_m), 1),
            "distance_m": None if distance_m is None else round(float(distance_m), 1)}


def _by_distance(rows, near):
    """Stamp each row with its distance from `near`, and put the nearest first.

    STRAIGHT LINE, NOT THE WALK. Working out the walking distance means an A* per
    candidate, over an engine state that may still be building, before the user has
    picked anything -- and /routes gives them the real number the moment they do. The
    crow-flies figure is enough for the job this does, which is telling five Hungry
    Jack's in the CBD apart, and it can only ever under-state the walk, never promise
    something closer than it is.

    Sorted only when there IS an origin. Without one the geocoder's own relevance order
    is the best thing on offer and re-ordering it would only make matching worse.
    """
    if near is None:
        return rows
    for r in rows:
        r["distance_m"] = round(metres(near[0], near[1], r["lat"], r["lon"]), 1)
    return sorted(rows, key=lambda r: r["distance_m"])


@app.get("/search")
def search(q: str = Query("", max_length=120), limit: int = Query(8, ge=1, le=20),
           near_lat: float | None = None, near_lon: float | None = None):
    """Free-text place search over OpenStreetMap, filtered to what ShadeMe can route.

    An empty box returns the curated list rather than an empty screen, so the picker has
    one code path. Anything else goes to the geocoder, and every match is then snapped to
    the graph: `outside` counts the ones that matched the words but landed further than
    SEARCH_SNAP_M from any walkable street, which is the difference between "there is no
    such place" and "it exists, and it is not in the CBD". The client says which.

    `near_lat`/`near_lon` are optional, and when both are given -- and are within
    NEAR_MAX_M of the city at all -- every result carries `distance_m` from that point
    and the list comes back nearest first. OSM has six
    7-Elevens and five Hungry Jack's in the CBD; the name and the street cannot tell them
    apart, and the only thing that distinguishes them is which one is closest. Filtering
    and truncation happen BEFORE the sort, so `limit` cannot cut off the near ones.
    """
    q = " ".join(q.split())
    # One clock for every row in this response, so two places cannot be judged against
    # times a second apart. Real wall clock, NOT the clamped routing hour -- a place is
    # shut at 23:00 even though routing prices that walk at 20:00.
    when = now_local()
    near = None if near_lat is None or near_lon is None else (near_lat, near_lon)
    shut = closed_now()
    if near is not None and nearest(*near, closed=shut)[1] > NEAR_MAX_M:
        near = None                 # not this city -- see NEAR_MAX_M
    if len(q) < 2:
        # An empty box is an empty result, deliberately. The client fills that space with
        # the user's own recent searches, which it holds on the device -- the server has
        # no list to offer and should not invent one.
        return {"query": q, "results": [], "outside": 0, "source": "none"}
    try:
        rows = geocode.search(q)
    except geocode.GeocodeError as e:
        print(f"[shademe] place search failed: {e}")
        raise HTTPException(502, "Place search is unavailable right now. Pick one of the "
                                 "suggested places, or try again in a moment.")
    kept, outside, seen = [], 0, set()
    for r in rows:
        node, d = nearest(r["lat"], r["lon"], SEARCH_SNAP_M, shut)
        if node is None:
            outside += 1
            continue
        # DEDUPE ON WHAT IS RENDERED, not on identity. OSM splits a long road into a way
        # per block, so "collins st" comes back as several Collins Streets; where their
        # suburbs differ those are real choices, and where they do not the user is shown
        # the same two lines twice and has no way to pick between them. Neither provider
        # catches this, nor the shop mapped as both a node and a way.
        key = (r["name"].lower(), (r["address"] or "").lower())
        if key in seen:
            continue
        seen.add(key)
        r["snap_m"] = d
        kept.append(r)
    # Ranked, THEN cut. Truncating first and sorting after would show the eight the
    # geocoder liked most and call the closest of those the nearest one.
    kept = _by_distance(kept, near)[:limit]
    # ONLY NOW look up opening hours: after the graph filter and after the cut, so one
    # search costs one extra geocoder call covering just the rows about to be rendered,
    # not all 25 the provider returned. Best effort -- see geocode.fill_hours.
    geocode.fill_hours(kept)
    results = [_place(r["name"], r["lat"], r["lon"], address=r["address"],
                      kind=r["kind"], snap_m=r["snap_m"],
                      distance_m=r.get("distance_m"),
                      **osm_hours.describe(r.get("hours"), when))
               for r in kept]
    return {"query": q, "results": results, "outside": outside,
            "source": rows[0]["source"] if rows else "none"}


@app.get("/reverse")
def reverse(lat: float, lon: float):
    """Name a GPS fix. THE COORDINATES ARE RETURNED UNCHANGED -- only the label comes from
    OSM. Routing from the centroid of whatever building the fix landed in would move the
    user somewhere they did not ask to start from, without telling them."""
    node, d = nearest(lat, lon, SEARCH_SNAP_M, closed_now())
    r = geocode.reverse(lat, lon)
    return dict(_place(r["name"] if r else "My location", lat, lon,
                       address=r["address"] if r else None, kind="here", snap_m=d),
                in_coverage=node is not None)


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

    # THE SAME `closed` THE ROUTER USES. Snapping on the ungated graph is what stranded
    # a start or end inside a shut arcade and produced "no walkable path".
    s, ds = nearest(from_lat, from_lon, 300.0, closed)
    t, dt = nearest(to_lat, to_lon, 300.0, closed)
    if s is None or t is None:
        # Name the end that is off, in a unit a person reads. This is the error a real
        # user hits most -- standing outside the CBD, or a simulator defaulting to
        # Cupertino -- so "start 15986795 m from nearest node" is the wrong thing to show.
        which = "Both ends are" if (s is None and t is None) else \
                ("The starting point is" if s is None else "The destination is")
        far = max(d for d, n in ((ds, s), (dt, t)) if n is None)
        away = f"{far / 1000:.0f} km" if far >= 1000 else f"{far:.0f} m"
        raise HTTPException(400, f"{which} outside the area ShadeMe covers. "
                                 f"It only knows the Melbourne CBD, and this is "
                                 f"{away} from the nearest walkable street.")
    # The state is taken BEFORE the lock, and never inside it: the warm thread holds
    # BUILD_LOCK across its march and wants APPLY_LOCK briefly at the end, so a request
    # holding APPLY_LOCK while it waited for BUILD_LOCK would deadlock against it.
    # wait=False is the promise: a rebuild in progress is served from the previous
    # state rather than waited out. See engine_state.
    st = engine_state(wait=False)
    stale = st["wx"].get("ts") != weather.get().get("ts")
    with APPLY_LOCK:
        solve_apply(st, h, w)

        # Walk both ladders. solve() is cached per hour, so each extra search is one more A*
        # over a warm graph -- a few ms, not a few seconds.
        seen, opts, baseline = {}, [], None
        searches = ([("thermal", K, routing.route_utci) for K in K_LADDER]
                    + [("uv", K, routing.route_uv) for K in K_UV_LADDER])
        for kind, K, fn in searches:
            try:
                r = fn(G, s, t, K, closed)
            except routing.RouteError as e:
                # The graph's vocabulary is not the user's. "Snap to the same node" means the
                # two pins landed on one corner.
                msg = ("Those two points are close enough to be the same spot — "
                       "they land on the same street corner."
                       if "same graph node" in str(e) else str(e))
                raise HTTPException(422, msg)
            if baseline is None:
                baseline = describe(G, r["shortest"], h, w)
            key = tuple(r["path"])
            if key in seen:
                # A walk another search already produced. Collapsing it is the honest answer,
                # not padding the list -- and a path BOTH objectives choose is the strongest
                # card on it.
                seen[key]["reached"].append({"kind": kind, "K": K})
                continue
            summ = describe(G, r["path"], h, w)
            avoided = compare_thermal(summ, baseline)
            avoided.update(compare_uv(summ, baseline))
            avoided["extra_m"] = round(summ["distance_m"] - baseline["distance_m"], 1)
            avoided["extra_s"] = round(avoided["extra_m"] / 1.35, 1)
            # Secondary to the dose, and outdoor-only on both sides: an air-conditioned arcade
            # must not drag a mean down and read as free comfort.
            a_, b_ = summ.get("utci_mean_outdoor"), baseline.get("utci_mean_outdoor")
            avoided["utci_outdoor_delta"] = round(b_ - a_, 2) if (a_ is not None and b_ is not None) else None
            opt = {
                "id": f"{'k' if kind == 'thermal' else 'u'}{int(K * 100):03d}",
                "objective": kind,
                "K": K,
                "K_effective": r["K_effective"],
                "reached": [{"kind": kind, "K": K}],
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

    # Drop dominated options: worse on time AND heat AND UV than another on the list is
    # not a choice, it is noise. Not cosmetic -- route_utci short-circuits at K <= 0 and
    # returns the shortest path WITHOUT charging its door crossings, while every K > 0
    # search pays DOOR_PENALTY_M per door, so where the shortest path runs through an
    # arcade the baseline gets it free and the K > 0 searches land hotter than the thing
    # they are supposed to improve on. The count is reported rather than swallowed.
    kept = [o for o in opts if not any(dominates(x, o) for x in opts if x is not o)]
    dominated = len(opts) - len(kept)
    opts = kept
    # Coolest first, then least UV, then quickest. In winter the first key is 0 for every
    # option and the ordering falls through to UV, which is the axis still carrying
    # information -- so the list re-sorts itself around whatever is varying rather than
    # needing a season declared anywhere.
    opts.sort(key=lambda o: (o["summary"].get("stress_load", 0.0),
                             o["summary"].get("uv_dose", 0.0),
                             o["summary"]["minutes"]))
    # Collapse near-duplicates. THE SURVIVOR IS THE QUICKER ONE, not whichever sorted
    # first: if two walks are equivalent on every objective the tie-break left is
    # distance, and keeping the longer geometry showed a 25 m detour with nothing to show
    # for it -- and dropped `is_shortest` with the card it belonged to, so the direct
    # route stopped being labelled as such. The keeper absorbs the other's `reached`.
    merged, near = [], 0
    for o in opts:
        i = next((i for i, m in enumerate(merged) if same_walk(m, o)), None)
        if i is None:
            merged.append(o)
            continue
        near += 1
        m = merged[i]
        keep, drop = ((m, o) if m["summary"]["minutes"] <= o["summary"]["minutes"]
                      else (o, m))
        keep["reached"] = keep["reached"] + drop["reached"]
        merged[i] = keep
    opts = merged
    label_options(opts, cond.get("uv_index"))
    # An option that earned no label is a middle route nobody asked for. Keep it only if
    # it beats a labelled one somewhere by a margin a person could notice.
    named = [o for o in opts if o["labels"]]
    kept = [o for o in opts
            if o["labels"] or not any(redundant(o, m) for m in named)]
    unlabelled = len(opts) - len(kept)
    opts = kept
    for o in opts:
        # "Balanced" only means something next to something else.
        o["labels"] = o["labels"] or (["Balanced"] if len(opts) > 1 else [])
        o["label"] = o["labels"][0] if o["labels"] else None
    return {
        "conditions": cond,
        "options": opts,
        "meta": {
            "snap_m": [round(ds, 1), round(dt, 1)],
            "hour": h,
            "as_of": cond["as_of"],
            "k_ladder": list(K_LADDER),
            "k_uv_ladder": list(K_UV_LADDER),
            "uv_index": cond.get("uv_index"),
            "distinct_paths": len(opts),
            "dominated_dropped": dominated,
            "near_duplicates_merged": near,
            "unlabelled_dropped": unlabelled,
            "availability": dict(hours.describe(h, dw), enforced=respect_hours),
            "detour_cap": routing.DETOUR_CAP,
            # WHICH GROUND THIS WAS PRICED ON. `conditions` above is always live -- the
            # air temperature, radiation and wind in the cost are this request's. What can
            # lag is the surface and wall temperature march, for the ~40 s it takes to
            # rebuild after the weather moves: rather than make the walker wait it out,
            # the previous march answers and says so here. `engine_as_of` is the fetch
            # those temperatures came from, and it is at most one weather.TTL behind.
            "engine_stale": stale,
            "engine_as_of": _stamp(st["wx"].get("ts")),
            # Additive and deliberately on every response: any number taken out of this
            # API is only evidence alongside the config that made it.
            "provenance": st["prov"],
            "ms": round((time.time() - t0) * 1000, 1),
        },
    }


def run():
    """Console-script entry point: `shademe-api`. Honours HOST/PORT."""
    import uvicorn
    uvicorn.run("shademe.api.main:app",
                host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "8011")))
