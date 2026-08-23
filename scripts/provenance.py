"""One stamp that says which configuration produced a number.

Every figure this project has reported -- "shade is 0.3513", "the route saves 6.03 C",
"the canopy fix moved shade by +4.55 pp" -- was computed against a moving reference. The
rasters in out/ were built with RAY_STEP = 1.0 while the code on disk already said 0.25,
so a single quoted delta silently stacked three separate changes, one of them committed
weeks earlier. A number without its configuration is not evidence.

So: stamp() returns everything a figure depends on, and line() compresses it to one line
short enough to sit under a table in a doc or at the top of a memory file. Anything that
prints a demo figure should print one of these next to it.

    python scripts/provenance.py            # human-readable block
    python scripts/provenance.py --line     # the one-liner for docs
    python scripts/provenance.py --json     # the full stamp

WHAT IT DOES NOT CATCH. The stamp hashes inputs and constants, not intent. Two runs with
the same stamp are the same computation; two runs with different stamps differ somewhere
in here, but the stamp cannot tell you which difference mattered. That is what
scripts/bench_shade_ladder.py is for.
"""
import os, sys, json, hashlib, subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS, OUT = f"{ROOT}/scripts", f"{ROOT}/out"
for p in (ROOT, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

# Full-file sha256 is exact but the shade set alone is ~360 MB, so digests are cached on
# (size, mtime_ns). Cheap enough to stamp every reported figure, which is the point.
_CACHE = f"{OUT}/.provenance_cache.json"


def _cache_load():
    try:
        return json.load(open(_CACHE))
    except Exception:
        return {}


def sha(path, cache=None):
    """sha256 of a file, first 12 hex. None if it does not exist."""
    if not os.path.exists(path):
        return None
    st = os.stat(path)
    key = os.path.relpath(path, ROOT)
    tag = f"{st.st_size}:{st.st_mtime_ns}"
    if cache is not None and cache.get(key, [None, None])[0] == tag:
        return cache[key][1]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    d = h.hexdigest()[:12]
    if cache is not None:
        cache[key] = [tag, d]
    return d


def _rollup(digests):
    """One id for a SET of files: sha over the member digests, order-sensitive."""
    live = [d for d in digests if d]
    if not live:
        return None
    return hashlib.sha256("".join(live).encode()).hexdigest()[:12]


def _git():
    def run(*a):
        try:
            return subprocess.run(a, cwd=ROOT, capture_output=True, text=True,
                                  timeout=5).stdout.strip()
        except Exception:
            return ""
    commit = run("git", "rev-parse", "--short", "HEAD") or None
    dirty = bool(run("git", "status", "--porcelain"))
    return {"commit": commit, "dirty": dirty}


def stamp(mode="summer", hours=range(6, 21)):
    """Everything a reported figure depends on. Safe to call from anywhere."""
    cache = _cache_load()
    hours = list(hours)
    s = {"git": _git(), "mode": mode}

    # --- constants that change every number downstream --------------------------
    try:
        from config import CELL, TAU_LEAF, CANOPY_HEIGHT
        from shadow import RAY_STEP, BEAM
        s["physics"] = {"CELL": CELL, "TAU_LEAF": TAU_LEAF, "RAY_STEP": RAY_STEP,
                        "BEAM": BEAM, "CANOPY_HEIGHT": CANOPY_HEIGHT}
    except Exception as e:                                   # pragma: no cover
        s["physics"] = {"error": repr(e)}

    try:
        from server.cost import (K_DEFAULT, K_UV_DEFAULT, DOOR_PENALTY_M,
                                 RISE_M_PER_M, LEVEL_JUMP_M)
        from server.engine import INDOOR_TA, COVERED_VA_F, INDOOR_VA
        from server.uv import UV_DIFFUSE_FLOOR
        # The door and the climb move routes as surely as K does -- a figure quoted
        # without them is as unattributable as one quoted without the shade rasters.
        s["cost"] = {"K": K_DEFAULT, "K_uv": K_UV_DEFAULT,
                     "uv_diffuse_floor": UV_DIFFUSE_FLOOR, "INDOOR_TA": INDOOR_TA,
                     "INDOOR_VA": INDOOR_VA, "COVERED_VA_F": COVERED_VA_F,
                     "door_m": DOOR_PENALTY_M, "rise_m_per_m": RISE_M_PER_M,
                     "level_jump_m": LEVEL_JUMP_M}
    except Exception as e:
        s["cost"] = {"error": repr(e)}

    # --- the rasters the ENGINE resolves, not the ones in out/ ------------------
    # _shade_path() encodes the v2 -> legacy fallback, so asking it is the only way to
    # stamp what was actually read rather than what happens to sit on disk.
    shade_day = None
    try:
        from server.engine import _shade_path, _svf_path, _raster_day
        paths = [_shade_path(h, mode) for h in hours]
        shade_day = _raster_day(mode)
    except Exception:
        paths = [f"{OUT}/shade_{h:02d}.npy" for h in hours]
        _svf_path = lambda: (f"{OUT}/svf_veg.npy" if os.path.exists(f"{OUT}/svf_veg.npy")
                             else f"{OUT}/svf_all.npy")
    digs = [sha(p, cache) for p in paths]
    dirs = sorted({os.path.relpath(os.path.dirname(p), OUT) for p in paths})
    # The DAY the shadows were cast for. Distinct from demo_day below: the shade set is
    # now chosen from the date being priced (server/engine._dirs_for), so a stamp that
    # named only the demo day would read "shade v2_winter ... day 2026-01-26" and invite
    # a reader to think the shadows were January's.
    s["shade"] = {"dir": "/".join(d if d != "." else "out" for d in dirs),
                  "day": shade_day,
                  "hours": [hours[0], hours[-1]], "set_sha": _rollup(digs),
                  "files": {f"{h:02d}": d for h, d in zip(hours, digs)}}
    svf = _svf_path()
    s["svf"] = {"path": os.path.relpath(svf, ROOT) if svf else None,
                "sha": sha(svf, cache) if svf else None}

    # --- graph + DSMs -----------------------------------------------------------
    gp = f"{OUT}/graph.pkl"
    s["graph"] = {"sha": sha(gp, cache), "path": "out/graph.pkl"}
    try:
        import pickle
        G = pickle.load(open(gp, "rb"))
        s["graph"].update(nodes=G.number_of_nodes(), edges=G.number_of_edges())
    except Exception:
        pass
    s["dsm"] = {n: sha(f"{OUT}/{n}.npy", cache) for n in
                ("dsm_buildings", "dsm_canopy", "dsm_canopy_v2", "dsm_canopy_base_v2")}

    # --- source of the physics itself -------------------------------------------
    s["src"] = {n: sha(f"{SCRIPTS}/{n}", cache) for n in
                ("shadow.py", "surface_temp.py", "mrt.py", "canopy_svf.py")}

    # --- which day the weather came from ----------------------------------------
    # One cache file per DAY now (weather_cache_YYYY-MM-DD.json), rather than one per
    # season -- server/weather no longer has modes. Stamp every day present so a figure
    # can be traced to the payload it was priced on.
    s["weather"] = {}
    import glob
    for p in sorted(glob.glob(f"{ROOT}/data/weather_cache_*.json")):
        name = os.path.basename(p)[len("weather_cache_"):-len(".json")]
        try:
            w = json.load(open(p))
            s["weather"][name] = {"date": w.get("date") or w.get("day"),
                                  "sha": sha(p, cache)}
        except Exception:
            s["weather"][name] = {"sha": sha(p, cache)}
    # Read the LIVE value, not the environment variable that is only one way of setting
    # it. `resolve_day()` returns the pin if one is set and today otherwise, which is
    # exactly what the engine will have priced; re-reading the env var here stamped
    # "demo day 2026-01-26" onto routes actually costed on today's weather -- a figure
    # carrying someone else's config, which is the failure this module exists to prevent.
    try:
        from server.weather import resolve_day as _rd
        s["demo_day"] = _rd()
    except Exception:
        s["demo_day"] = os.environ.get("SHADEME_DATE") or \
            os.environ.get("SHADEME_SUMMER_DATE", "2026-01-26")

    # --- the bias correction applied to that weather -----------------------------
    # Every temperature the engine reports now passes through this, so a figure quoted
    # without it is quoted without its configuration -- which is the whole reason this
    # module exists. Stamped as the mode string plus the sha of the fitted table, so a
    # re-fit shows up as a different stamp even at the same mode.
    try:
        from server.weather import BIAS_PATH, ta_offset
        # Keyed on the day ACTUALLY being priced. This used to read a "summer" entry out
        # of the weather-cache map and fall back to a hardcoded January date when it was
        # missing, so an August request was stamped `season-shape[DJF]` while the engine
        # had applied `[JJA]` -- the stamp naming a correction that was never used.
        _, bm = ta_offset(14, s.get("demo_day") or "2026-01-26")
        s["bias"] = {"mode": bm, "table": sha(BIAS_PATH, cache)}
    except Exception as e:
        s["bias"] = {"error": repr(e)}

    try:
        os.makedirs(OUT, exist_ok=True)
        json.dump(cache, open(_CACHE, "w"))
    except Exception:
        pass
    return s


def line(s=None, **kw):
    """The one-liner that goes under a figure. Everything that can move a number."""
    s = s or stamp(**kw)
    g, p, c = s.get("graph", {}), s.get("physics", {}), s.get("cost", {})
    bits = [
        f"graph {g.get('sha')}" + (f" ({g['nodes']}n/{g['edges']}e)" if "nodes" in g else ""),
        f"shade {s['shade']['dir']} {s['shade']['set_sha']}"
        + (f" (sun {s['shade']['day']})" if s['shade'].get('day') else ""),
        f"svf {os.path.basename(s['svf']['path'] or '-')} {s['svf']['sha']}",
        f"K={c.get('K')}" if "K" in c else None,
        # A UV figure is as unattributable without these as a thermal one is without K.
        f"K_uv={c.get('K_uv')} uvdif={c.get('uv_diffuse_floor')}" if "K_uv" in c else None,
        f"door={c.get('door_m')}m rise={c.get('rise_m_per_m')}x" if "door_m" in c else None,
        f"INDOOR_TA={c.get('INDOOR_TA')}" if "INDOOR_TA" in c else None,
        f"TAU_LEAF={p.get('TAU_LEAF')} RAY_STEP={p.get('RAY_STEP')} beam={p.get('BEAM')}",
        f"demo day {s.get('demo_day')}",
        f"bias {s['bias'].get('mode')}/{s['bias'].get('table')}" if "bias" in s else None,
        f"@{s['git'].get('commit')}" + ("+dirty" if s["git"].get("dirty") else ""),
    ]
    return " · ".join(b for b in bits if b)


def block(s=None, **kw):
    """Multi-line form for a run log."""
    s = s or stamp(**kw)
    w = "  ".join(f"{k} {v.get('date')}" for k, v in s.get("weather", {}).items())
    return "\n".join([
        f"  git        {s['git'].get('commit')}{'  (dirty tree)' if s['git'].get('dirty') else ''}",
        f"  graph      {s['graph'].get('sha')}  "
        f"{s['graph'].get('nodes','?')} nodes / {s['graph'].get('edges','?')} edges",
        f"  shade      {s['shade']['dir']}/  set {s['shade']['set_sha']}  "
        f"sun {s['shade'].get('day')}  "
        f"hours {s['shade']['hours'][0]}..{s['shade']['hours'][1]}  mode {s['mode']}",
        f"  svf        {s['svf']['path']}  {s['svf']['sha']}",
        f"  dsm        " + "  ".join(f"{k.replace('dsm_','')} {v}" for k, v in s["dsm"].items()),
        f"  physics    " + "  ".join(f"{k}={v}" for k, v in s["physics"].items()),
        f"  cost       " + "  ".join(f"{k}={v}" for k, v in s["cost"].items()),
        f"  weather    {w}   demo day {s.get('demo_day')}",
        f"  bias       {s.get('bias',{}).get('mode')}  table {s.get('bias',{}).get('table')}",
        f"  src        " + "  ".join(f"{k} {v}" for k, v in s["src"].items()),
    ])


if __name__ == "__main__":
    mode = "winter" if "--winter" in sys.argv else "summer"
    s = stamp(mode=mode)
    if "--json" in sys.argv:
        print(json.dumps(s, indent=1))
    elif "--line" in sys.argv:
        print(line(s))
    else:
        print("provenance stamp\n" + block(s) + "\n\none-liner for docs:\n  " + line(s))
