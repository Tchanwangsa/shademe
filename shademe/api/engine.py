"""Physical comfort engine: shade + SVF + materials -> Ts -> MRT -> UTCI -> edge cost.

Replaces six hand-tuned weights with one free knob, K. Pure computation + caching, no
HTTP concerns. Once per weather refresh, not per request:

    weather -> surface_temp.march() over the 2 m raster  -> Ts(hour)
            -> sample Ts and SVF onto graph edges
            -> mrt() per edge -> utci() per edge -> stress() -> cost multiplier

The raster march is ~33 s, so its result is cached keyed on every input to it.
"""
import os, re, json, time, shutil, sys, hashlib, subprocess
import numpy as np
import pandas as pd

from ..physics import surface_temp as ST
from ..physics import mrt as MRT
from ..physics.shadow import sun_position
from ..paths import OUT
from . import uv as UV

PHYSICS_SRC = ("surface_temp.py", "mrt.py", "shadow.py")
PHYSICS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "physics"))

# THE WHOLE CLOCK. This was 06..20 -- the hours a shade set has files for -- and the API
# clamped the wall clock into it. The rasters were never the reason: below
# shadow.SUN_MIN_DEG every mask in the sweep returns "fully shaded", so a night raster is
# the constant 1.0 and pipeline.shade does not write one. _shade_path() returns None for
# those hours and every reader below treats that as FULL SHADE, which is what it is.
#
# So an old set built only for 06..20 keeps working unchanged: hours 21..05 simply have
# no file, which is the same statement as "the sun is down". Nothing has to be
# regenerated to route at 3am.
HOURS = list(range(24))

# Probe hour for "does this directory hold a shade set". Must be an hour the sun is up
# for on every day of the year, since the dark hours legitimately have no file: 13:00
# local is above 27 deg in Melbourne even at the winter solstice.
PROBE_HOUR = 13
TZ = "Australia/Melbourne"
N_SAMPLE = 8

# --- indoor / covered handling ------------------------------------------------
# Stated loudly because it drives the whole indoor pitch: Melbourne Central, Emporium and
# the major arcades are AIR CONDITIONED, so treating an indoor edge as "outdoor air with
# no sun" would read 32 C in a heatwave and badly undersell the network this product is
# built around.
#   indoor  -> conditioned air, MRT = air (isothermal enclosure), near-still air
#   covered -> outdoor air, but no beam and no sky view: MRT = air
# INDOOR_TA is a stated assumption, not a measurement. SHADEME_INDOOR_TA=0 models
# unconditioned arcades.
INDOOR_TA = float(os.environ.get("SHADEME_INDOOR_TA", "22.5"))
INDOOR_VA = 0.5      # m/s, UTCI's lower validity bound; still indoor air
COVERED_VA_F = 0.6   # arcades are sheltered: knock the outdoor wind down


# Shade rasters are DAY-SPECIFIC: noon sun elevation runs 27.5 deg (June) to 75 deg
# (December) and shadow length goes as 1/tan(elevation), so reusing one season's shadows
# in another points them the wrong way and makes them far too short. One set per day.
SHADE_DIRS = {"summer": ["v2", ""], "winter": ["v2_winter", "v2", ""],
              "cold": ["v2_cold", "v2_winter", ""]}

# The set is chosen from the DAY BEING PRICED, not from a mode name. `mode` still names
# the argument and accepts either a legacy season or a YYYY-MM-DD date; passing a date is
# what makes the choice automatic. The manual switch defaulted to summer and was never
# derived from the date, so an October request was priced on 26 January shadows -- about
# 56% too short.
SHADE_TOL_DEG = float(os.environ.get("SHADEME_SHADE_TOL", "2.0"))   # noon-elevation slack
SHADE_GEN = os.environ.get("SHADEME_SHADE_GEN", "1") == "1"         # generate if none fits
SHADE_CACHE_MAX = int(os.environ.get("SHADEME_SHADE_CACHE", "3"))   # generated sets kept
GEN_PREFIX = "day_"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _noon_elev(day):
    """Solar elevation at 13:00 local. The single number that sets shadow length."""
    return float(sun_position(pd.Timestamp(f"{day} 13:00", tz=TZ))[1])


def _sets_on_disk():
    """{dirname: day} for every shade set present, read from each set's own manifest.

    Read rather than tabulated: the sun positions a set was built with are a property of
    the files, not of a mapping kept here that can drift away from them.
    """
    out = {}
    for d in sorted(os.listdir(OUT)):
        p = os.path.join(OUT, d, "shade_means_v2.json")
        if not os.path.isdir(os.path.join(OUT, d)) or not os.path.exists(p):
            continue
        try:
            day = json.load(open(p)).get("day")
        except (ValueError, OSError):
            continue
        if day and os.path.exists(os.path.join(OUT, d, f"shade_{PROBE_HOUR:02d}.npy")):
            out[d] = day
    return out


def _best_set(day):
    """(dirname, day, elevation gap) of the closest set on disk, or (None, None, inf)."""
    want = _noon_elev(day)
    best, bday, gap = None, None, float("inf")
    for d, dday in _sets_on_disk().items():
        g = abs(_noon_elev(dday) - want)
        if g < gap:
            best, bday, gap = d, dday, g
    return best, bday, gap


def _prune_generated(keep):
    """Bound the disk cost of on-demand sets. Each one is ~350 MB of float32 raster."""
    gen = [d for d in os.listdir(OUT)
           if d.startswith(GEN_PREFIX) and os.path.isdir(os.path.join(OUT, d))]
    gen = [d for d in gen if d != keep]
    gen.sort(key=lambda d: os.path.getmtime(os.path.join(OUT, d)), reverse=True)
    for d in gen[max(0, SHADE_CACHE_MAX - 1):]:
        shutil.rmtree(os.path.join(OUT, d), ignore_errors=True)
        print(f"[shade] pruned generated set {d}")


def ensure_shade_set(day):
    """Directory name holding shade rasters valid for `day`, generating one if needed.

    Generation SHELLS OUT to the pipeline rather than re-marching here: re-implementing
    the march in the engine is how you end up with two shade models in one process.
    """
    d, dday, gap = _best_set(day)
    if d is not None and gap <= SHADE_TOL_DEG:
        return d
    if not SHADE_GEN:
        print(f"[shade] {day}: closest set {d} is {gap:.1f} deg off at noon, "
              f"generation disabled -- using it anyway")
        return d
    name = f"{GEN_PREFIX}{day}"
    if os.path.exists(os.path.join(OUT, name, f"shade_{PROBE_HOUR:02d}.npy")):
        return name
    print(f"[shade] {day}: closest set {d} ({dday}) is {gap:.1f} deg off at noon "
          f"-> generating {name}")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "shademe.pipeline.shade", day, name],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(os.path.join(OUT, name, "shade_means_v2.json")):
        print(f"[shade] generation FAILED for {day}, falling back to {d}\n"
              f"{(r.stderr or '')[-500:]}")
        shutil.rmtree(os.path.join(OUT, name), ignore_errors=True)
        return d
    print(f"[shade] generated {name} in {time.time()-t0:.1f}s")
    _prune_generated(name)
    return name


def _dirs_for(mode):
    """Ordered candidate directories for a mode name OR a YYYY-MM-DD day."""
    if isinstance(mode, str) and _DATE_RE.match(mode):
        d = ensure_shade_set(mode)
        return ([d] if d else []) + ["v2", ""]
    return SHADE_DIRS.get(mode, SHADE_DIRS["summer"])


def _shade_path(hour, mode="summer"):
    """The raster for this hour, or None when there is none -- meaning FULL SHADE.

    None is a real answer, not a failure: the sun is below SUN_MIN_DEG for the dark half
    of the clock and pipeline.shade deliberately writes no file for those hours. Callers
    substitute 1.0.

    A missing file with the sun UP is a different thing entirely -- a half-built set --
    and is reported rather than silently priced as a shaded street.
    """
    # THE SUN DECIDES FIRST, THE DISK SECOND, and that order is not cosmetic. The
    # directory chain ends in the January set as a last resort, so asking the disk first
    # answered "is 07:00 dark on 24 August?" with out/v2/shade_07.npy -- a SUMMER DAWN
    # raster stood in for a winter night, which is the one substitution the shade-set
    # machinery above exists to prevent. Below SUN_MIN_DEG there is no file to look for.
    if not _sun_up(_shade_day(mode), hour):
        return None
    for d in _dirs_for(mode):
        p = os.path.join(OUT, d, f"shade_{hour:02d}.npy") if d else f"{OUT}/shade_{hour:02d}.npy"
        if os.path.exists(p):
            return p
    print(f"[shade] MISSING shade_{hour:02d}.npy for {mode} with the sun up "
          f"-- that hour will be priced as fully shaded")
    return None


def _shade_day(mode):
    """The day whose sunrise and sunset decide which hours have rasters.

    The day BEING PRICED when one was given, not the day the closest set on disk was
    generated for: whether the sun is up is a fact about today, and the set is only ever
    within SHADE_TOL_DEG of today anyway.
    """
    if isinstance(mode, str) and _DATE_RE.match(mode):
        return mode
    return _raster_day(mode)


def _sun_up(day, hour):
    """Is the sun high enough at `day hour:00` for the shadow sweep to resolve anything?

    The SAME test the sweep makes (shadow.SUN_MIN_DEG), so "there is no raster" and
    "there is no beam" are one statement rather than two that can drift apart.
    """
    from ..physics.shadow import SUN_MIN_DEG
    try:
        return sun_position(pd.Timestamp(f"{day} {int(hour):02d}:00", tz=TZ))[1] >= SUN_MIN_DEG
    except Exception:
        return True                              # cannot tell -> treat a gap as a fault


# Which SVF raster the engine reads. svf_veg is svf_bldg with the canopy's TRUE
# cosine-weighted blocking removed (physics/canopy_svf.py); svf_all is the legacy
# horizon-max raster, which extrudes every crown to the pavement and over-blocks the sky
# by ~2x in the mean. svf_all is the fallback so an un-regenerated checkout still runs.
SVF_PREF = os.environ.get("SHADEME_SVF", "veg")


def _svf_path():
    """Path to the SVF raster in use, or None if none has been built yet."""
    order = ([f"{OUT}/svf_veg.npy", f"{OUT}/svf_all.npy"] if SVF_PREF == "veg"
             else [f"{OUT}/svf_all.npy"])
    for p in order:
        if os.path.exists(p):
            return p
    return None


def grid():
    return json.load(open(f"{OUT}/grid.json"))


def _sampler(G, edges, g):
    """Vectorised raster gather along each edge. Same pattern as pipeline.graph."""
    minx, miny, maxx, maxy = g["bounds"]; H, W, cell = g["h"], g["w"], g["cell"]
    xy = np.array([[G.nodes[u]["xy"], G.nodes[v]["xy"]] for u, v in edges])
    f = ((np.arange(N_SAMPLE) + 0.5) / N_SAMPLE)[None, :, None]
    pts = xy[:, 0][:, None, :] + (xy[:, 1] - xy[:, 0])[:, None, :] * f
    r = ((maxy - pts[..., 1]) / cell).astype(np.int32)
    c = ((pts[..., 0] - minx) / cell).astype(np.int32)
    inside = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    flat = np.clip(r, 0, H - 1) * W + np.clip(c, 0, W - 1)
    cnt = np.maximum(inside.sum(1), 1)

    def take(arr, mode="mean"):
        v = np.asarray(arr.ravel()[flat])
        if mode == "mode":                       # dominant class along the edge
            out = np.zeros(len(edges), dtype=np.int32)
            for i in range(len(edges)):
                vals = v[i][inside[i]]
                out[i] = np.bincount(vals.astype(np.int64)).argmax() if vals.size else 0
            return out
        return (v * inside).sum(1) / cnt
    return take


def _raster_day(mode):
    """The day the rasters in this mode's directory were generated for.

    Read from the directory's own manifest rather than a mapping kept here, because the
    deck march has to use the sun positions the rasters it overwrites were built with.
    Asking for 23 Aug and being served the 22 Aug set must report 22 Aug.
    """
    for d in _dirs_for(mode):
        p = os.path.join(OUT, d, "shade_means_v2.json") if d else f"{OUT}/shade_means_v2.json"
        if os.path.exists(p):
            try:
                day = json.load(open(p)).get("day")
                if day:
                    return day
            except (ValueError, OSError):
                pass
    return os.environ.get("SHADEME_DATE", "2026-01-26")


def _apply_decks(G, edges, E, g, mode):
    """Overwrite the flat raster gather for edges that are not on the ground.

    The hourly rasters answer "is this GROUND CELL shadowed", and a 2.5D height field has
    one value per cell, so a walkway on a structure IS the terrain there -- the outdoor
    bridge ways read 0.75 mean shade at 14:00 against 0.22 for street-level footpath.
    physics.shadow.point_shade re-marches them with the receiver raised.

    Cheap enough per request: ~1000 edges x 8 points x 15 hours. Silently a no-op on a
    graph built before decks were carried, so an old pickle still loads.
    """
    deck = [(u, v) for u, v in edges if G[u][v].get("deck")]
    if not deck:
        return
    from ..pipeline import graph as BG
    idx = {e: i for i, e in enumerate(edges)}
    try:
        vals, _z = BG.deck_shade(G, deck, g, _raster_day(mode), HOURS)
    except (OSError, ValueError) as e:
        print(f"[engine] deck re-sample skipped ({e})")
        return
    cols = np.array([idx[e] for e in deck])
    for i in range(len(HOURS)):
        E["shade"][i, cols] = vals[i]


def edge_index(G, mode="summer"):
    """Stable ordering of edges + the static per-edge arrays the engine needs."""
    edges = list(G.edges())
    d = [G[u][v] for u, v in edges]
    g = grid()
    take = _sampler(G, edges, g)
    n = len(edges)
    E = {
        "edges": edges,
        "length": np.array([float(x["length"]) for x in d], dtype=np.float64),
        "indoor": np.array([bool(x.get("indoor")) for x in d]),
        "covered": np.array([bool(x.get("covered")) for x in d]),
        "shade": np.zeros((len(HOURS), n), dtype=np.float32),
    }
    for i, h in enumerate(HOURS):
        p = _shade_path(h, mode)
        # No raster = the sun is down = every edge fully shaded. Not loaded, not stored
        # as a raster, and not a special case anywhere downstream: 1.0 is the same
        # number the sweep would have written into every cell of a night file.
        E["shade"][i] = 1.0 if p is None else take(np.load(p, mmap_mode="r"))
    _apply_decks(G, edges, E, g, mode)
    svf_p = _svf_path()
    if svf_p:
        E["svf"] = take(np.load(svf_p, mmap_mode="r")).astype(np.float32)
    else:                                        # SVF not built yet: open-sky fallback
        E["svf"] = np.full(n, np.nan, dtype=np.float32)
    mat_p = f"{OUT}/material_id.npy"
    E["mat_id"] = (take(np.load(mat_p, mmap_mode="r"), mode="mode")
                   if os.path.exists(mat_p) else np.zeros(n, dtype=np.int32))
    # protected edges: no sky, no beam
    prot = E["indoor"] | E["covered"]
    E["shade"][:, prot] = 1.0
    E["svf"] = np.where(prot, 0.0, E["svf"])
    return E


def _file_sig(path):
    """(size, mtime) signature, cheap and enough to spot a regenerated raster.

    None for a path that is None -- a dark hour has no raster, and "no file" is a
    perfectly good cache key for it.
    """
    if path is None:
        return None
    try:
        st = os.stat(path); return [st.st_size, int(st.st_mtime)]
    except OSError:
        return None


def _key(wx_hours, date, mode="summer"):
    """Cache key over EVERY input to the march, not just the weather: material
    properties, the material raster, SVF, the shade rasters and the physics source
    itself. Keying on weather alone silently served a stale Ts after a beta/k_deep
    update, and editing surface_temp.py changes Ts without touching a raster.
    """
    try:
        props = json.load(open(f"{OUT}/material_props.json"))
    except Exception:
        props = {}
    sig = {"d": date,
           "w": {str(k): wx_hours[k] for k in sorted(wx_hours)},
           "props": props,
           "mat": _file_sig(f"{OUT}/material_id.npy"),
           "svf": [_svf_path(), _file_sig(_svf_path() or "")],
           "mode": mode,
           "shade": [_file_sig(_shade_path(h, mode)) for h in HOURS],
           "code": [_file_sig(os.path.join(PHYSICS_DIR, f)) for f in PHYSICS_SRC]}
    return hashlib.sha256(json.dumps(sig, sort_keys=True, default=str).encode()).hexdigest()[:16]


# Persisting the full Ts rasters costs ~166 MB per weather payload and eight stale ones
# had reached 1.3 GB -- the actual deployment blocker. The router never reads a raster: it
# reads 8 samples along each of 60,865 edges, 7 MB for the whole day. So the EDGE cache
# below is the one kept, and the raster cache is opt-in for the day someone wants to paint
# MRT on the map.
RASTER_CACHE = os.environ.get("SHADEME_TSURF_RASTER", "0") == "1"


def surface_temps(wx, g, cache=None, mode="summer", on_hour=None):
    """{hour: Ts raster (K)} for the whole grid. The march is ~33 s.

    `on_hour(hour, ts)` streams instead: each hour is handed over as it is reached and
    NOT accumulated, and the return is empty. That is the mode attach_tsurf uses, and
    going to the full 24 h clock is why it has to. A Ts raster is 24 MB on the real grid
    and the router never reads one -- it reads 8 samples along each edge -- so holding 24
    of them is 582 MB spent to be immediately thrown away. Measured peak RSS over the
    same 24 h march on this grid: 865 MB streaming against 1153 MB accumulating, and the
    same 865 MB as the OLD 15-hour window streamed. The extra nine hours are free.
    See surface_temp.march's own note on peak RAM.
    """
    cache = RASTER_CACHE if cache is None else cache
    key = _key(wx["hours"], wx.get("date", "?"), mode)
    path = f"{OUT}/tsurf_cache_{key}.npz"
    if cache and os.path.exists(path):
        z = np.load(path)
        if on_hour is not None:
            for k in z.files:
                on_hour(int(k), z[k])
            return {}
        return {int(k): z[k] for k in z.files}
    _sp = _svf_path()
    svf = (np.load(_sp) if _sp else np.ones((g["h"], g["w"]), dtype=np.float32))
    mat = (np.load(f"{OUT}/material_id.npy") if os.path.exists(f"{OUT}/material_id.npy")
           else np.zeros((g["h"], g["w"]), dtype=np.uint8))
    props = json.load(open(f"{OUT}/material_props.json"))
    # Only the hours with a raster. march() fills the rest with DEFAULT_SHADE = 1.0,
    # which is exactly right for them and is why the dark half of the clock needs no
    # files: see _shade_path.
    shade = {h: np.load(p, mmap_mode="r") for h, p in
             ((h, _shade_path(h, mode)) for h in HOURS) if p is not None}
    keep = cache or on_hour is None
    ts = ST.march(shade, svf, mat, props, wx["hours"], hours=HOURS,
                  keep=keep, on_hour=on_hour)
    if cache:
        np.savez_compressed(path, **{str(k): v for k, v in ts.items()})
    return ts if on_hour is None else {}


def _props_vec(props, key, default):
    v = np.full(256, default, dtype=np.float64)
    for k, p in props.items():
        v[int(k)] = float(p.get(key, default))
    return v


def solve(E, wx_block, wx_full, hour, K=None, g=None):
    """Per-edge UTCI, stress and cost multiplier for one hour.

    K is imported from cost.K_DEFAULT rather than defaulted here: it used to default to
    0.06 while the router used 0.10, so the engine and the router disagreed about the only
    free parameter in the model. One K, one place.

    `cost` here is PLAN length x mult, with no climb and no door penalty, so it is
    comparable across edges at one hour and is NOT the number the router minimised. It is
    a diagnostic; routing re-derives cost from `_stress`. Do not start using it.

    Returns a dict of per-edge arrays aligned with E['edges'].
    """
    from .cost import K_DEFAULT
    K = K_DEFAULT if K is None else float(K)
    g = g or grid()
    hi = HOURS.index(int(hour)) if int(hour) in HOURS else 0
    ta = float(wx_block["temperature"])
    rh = float(wx_block["relative_humidity"])
    vp = float(wx_block["vapour_pressure_hpa"])
    va10 = float(wx_block["wind_speed_ms"])          # m/s -- NOT the legacy km/h field
    i_dir = float(wx_block["direct_radiation"])      # horizontal plane
    i_dif = float(wx_block["diffuse_radiation"])
    cloud = float(wx_block["cloud_cover"]) / 100.0
    _, elev = sun_position(pd.Timestamp(f"{wx_full.get('date','2026-01-26')} {int(hour):02d}:00", tz=TZ))

    if "_albg" not in E:                       # per-edge ground albedo, static
        _p = json.load(open(f"{OUT}/material_props.json"))
        E["_albg"] = _props_vec(_p, "albedo", 0.15)[np.clip(E["mat_id"], 0, 255)]
    alb_g = E["_albg"]

    ts_k = E.get("tsurf_k", {}).get(int(hour))          # KELVIN -- see attach_tsurf
    tsurf_c = (ts_k - 273.15) if ts_k is not None else np.full(len(E["length"]), ta + 8.0)

    svf = np.nan_to_num(E["svf"], nan=0.6)
    shade = E["shade"][hi].astype(np.float64)
    prot = E["indoor"] | E["covered"]

    # Facade temperature for MRT's (1-svf)*l_wall term. Falls back to air only if the
    # wall march has not run -- and that fallback IS the old low bias.
    t_wall_c = E.get("twall_c", {}).get(int(hour))      # CELSIUS -- see _attach_walls
    tmrt = MRT.mrt(ta=ta, svf=svf, shade=shade, i_dir_h=i_dir, i_dif=i_dif,
                   elev_deg=elev, tsurf_c=tsurf_c, t_wall_c=t_wall_c,
                   albedo_g=alb_g, rh=rh, cloud=cloud)
    # protected edges: no beam, no sky -> isothermal enclosure at the local air temp
    ta_e = np.where(E["indoor"], INDOOR_TA, ta)
    tmrt = np.where(prot, ta_e, tmrt)
    va_e = np.where(E["indoor"], INDOOR_VA, np.where(E["covered"], va10 * COVERED_VA_F, va10))
    vp_e = np.where(E["indoor"], MRT.vapour_pressure(INDOOR_TA, 50.0), vp)

    u, clamped = MRT.utci(ta_e, tmrt, np.maximum(va_e, 0.5), vp_hpa=vp_e)
    s = MRT.stress(u)
    mult = 1.0 + K * s

    # --- UV exposure, an INDEPENDENT objective ---------------------------------
    # Deliberately not derived from stress, mrt or mult. UV has no air-temperature term,
    # no surface memory and no wind term, so the two fields disagree about the same street
    # all the time: a cold, still, cloudless winter laneway scores zero thermal stress and
    # still carries most of the day's UV. That disagreement is why a separate option is
    # worth offering. The other half is SVF -- UV is roughly half skylight even under a
    # clear sky, so a fully shaded spot under open sky still receives ~half the index.
    i_tot = i_dir + i_dif
    direct_fraction = (i_dir / i_tot) if i_tot > 0 else 0.0
    uv_frac = np.clip(UV.exposure(shade, svf, direct_fraction), 0.0, 1.0)
    uv_frac = np.where(prot, 0.0, uv_frac)      # no beam and no sky under a roof

    return {"mrt": tmrt, "utci": u, "stress": s, "mult": mult, "shade": shade,
            "cost": E["length"] * mult, "clamped": clamped,
            "ta_edge": ta_e, "elev": elev, "uv_frac": uv_frac,
            "uv_index": wx_block.get("uv_index"),
            "t_wall_c": ta if t_wall_c is None else float(t_wall_c)}


def wall_temps(wx, t_env_k=None):
    """{hour: effective facade temperature in degC} for MRT's longwave wall term.

    Closes the "walls radiate at air temperature" bias, which made every MRT low. Marches
    the same balance on a vertical facet for eight orientations and collapses them with
    the QUARTIC mean, the average that preserves emitted flux. Nothing new was added to
    the MRT formula -- (1-svf)*l_wall was always there -- so this cannot double count.

    t_env_k  {hour: K} the ground temperature each facade faces. One-way coupling: hot
             pavement warms the wall, not the reverse. Without it a shaded facade radiates
             to a cold sky over a hemisphere at air temperature and comes out BELOW air,
             when in reality it is looking at 50 C asphalt.
    """
    hrs = wx["hours"]
    hrs = {int(k): v for k, v in hrs.items()}
    date = wx.get("date", "2026-01-26")
    sun = {h: sun_position(pd.Timestamp(f"{date} {h:02d}:00", tz=TZ)) for h in hrs}
    W = ST.wall_march(hrs, sun, hours=HOURS, t_env_k=t_env_k)
    return {h: ST.wall_effective_c(v) for h, v in W.items()}


def _attach_walls(E, wx, mode=None):
    """Sets E['twall_c'][hour] -> effective facade temperature, degC.

    The ground temperature facades see is the mean over OUTDOOR edges: indoor and covered
    edges carry conditioned or sheltered air, not sunlit pavement.
    """
    out = E["tsurf_k"]
    outdoor = ~(E["indoor"] | E["covered"])
    env = {h: float(v[outdoor].mean()) if outdoor.any() else float(v.mean())
           for h, v in out.items()}
    E["twall_c"] = wall_temps(wx, t_env_k=env)


def attach_tsurf(E, G, wx, g=None, mode="summer", cache=True):
    """Sample the hourly Ts rasters onto edges. Sets E['tsurf_k'][hour] -> (n,) float, K.

    Once per weather refresh, not per request. Protected edges get the indoor or outdoor
    air temperature instead: there is no sunlit pavement under a shopping centre.
    """
    g = g or grid()
    # Per-EDGE cache: the march's key plus the graph's signature, since edge ordering and
    # geometry are inputs to the sampling. ~7 MB instead of ~166 MB, and a hit skips the
    # 33 s march entirely.
    key = _key(wx["hours"], wx.get("date", "?"), mode)
    gsig = hashlib.sha256(json.dumps([_file_sig(f"{OUT}/graph.pkl"), len(E["edges"]),
                                      N_SAMPLE], default=str).encode()).hexdigest()[:8]
    path = f"{OUT}/tsurf_edge_{key}_{gsig}.npz"
    if cache and os.path.exists(path):
        z = np.load(path)
        E["tsurf_k"] = {int(k): z[k].astype(np.float64) for k in z.files}
        _attach_walls(E, wx, mode)
        return E["tsurf_k"]
    take = _sampler(G, E["edges"], g)
    E["tsurf_k"] = {}

    def sample(h, ts_h):
        """8 samples per edge, then the raster is dropped. See surface_temps(on_hour)."""
        v = take(ts_h).astype(np.float64)
        row = wx["hours"].get(h) or wx["hours"].get(str(h)) or {}
        ta = float(row.get("temperature_2m") or 20.0)
        E["tsurf_k"][h] = np.where(E["indoor"], INDOOR_TA + 273.15,
                                   np.where(E["covered"], ta + 273.15, v))

    surface_temps(wx, g, mode=mode, on_hour=sample)
    missing = [h for h in HOURS if h not in E["tsurf_k"]]
    if missing:
        raise RuntimeError(f"surface march emitted no Ts for hours {missing}")
    if cache:
        np.savez_compressed(path, **{str(k): v.astype(np.float32)
                                     for k, v in E["tsurf_k"].items()})
    _attach_walls(E, wx, mode)
    return E["tsurf_k"]


def apply(G, E, res):
    """Stash the solved per-edge thermal state on the graph for the A* weight function.

    Transient: `_stress` / `_utci` / `_mrt` are recomputed every solve and must never be
    pickled into out/graph.pkl -- they depend on live weather.
    """
    sh, uvf = res.get("shade"), res.get("uv_frac")
    for i, ((u, v), s, u_, m) in enumerate(zip(E["edges"], res["stress"], res["utci"], res["mrt"])):
        d = G[u][v]
        d["_stress"] = float(s); d["_utci"] = float(u_); d["_mrt"] = float(m)
        if sh is not None:
            d["_shade"] = float(sh[i])
        if uvf is not None:
            d["_uv_frac"] = float(uvf[i])


def clear(G):
    for _, _, d in G.edges(data=True):
        d.pop("_stress", None); d.pop("_utci", None); d.pop("_mrt", None)
        d.pop("_shade", None); d.pop("_uv_frac", None)
