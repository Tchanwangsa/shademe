"""Rebuild the shade decomposition ladder from the DSMs, and check both ends against disk.

WHY THIS EXISTS. "The canopy fix changed mean shade by +4.55 pp" was never one change.
The rasters it was measured against (out/shade_*.npy) were built with RAY_STEP = 1.0,
which was already stale at the baseline commit -- the code on disk said 0.25. So the
quoted delta stacked a ray-step fix committed weeks earlier, a beam-height fix, the
allometric crown, the trunk gap and the transmissivity change into a single number, and
there was no way to ask which rung moved what.

The fix is NOT to keep a legacy raster set around to diff against. That set would exist
only to be compared against once, and it would rot the moment anything upstream of it
changed. Every rung here is rebuilt from the DSMs on demand through the SHIPPED
shademe/physics/shadow.py -- each one is a different set of arguments, never a second code path
-- and the two ends are verified bit-for-bit against what is actually on disk:

    rung 0  == out/shade_HH.npy       the legacy set the old figures were measured on
    rung 5  == out/v2/shade_HH.npy    the set the engine reads today

If either anchor fails, a raster on disk no longer matches the code that claims to
produce it, this script exits non-zero, and no figure derived from it should be quoted
until that is resolved. The baseline is reproducible rather than stored.

    python tools/bench_shade_ladder.py                 # 14:00, the quoted hour
    python tools/bench_shade_ladder.py --hour 9
    python tools/bench_shade_ladder.py --day           # every hour 06..20, day mean
"""
import os, sys, json, time, argparse
import numpy as np
import pandas as pd

from shademe.config import CELL, TAU_LEAF
from shademe.physics.shadow import sun_position, shade_factor, RAY_STEP
from shademe import provenance

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = f"{ROOT}/out"
TZ = "Australia/Melbourne"
DAY = os.environ.get("SHADEME_DATE", "2026-01-26")

# The legacy canopy transmissivity: a hand-picked CANOPY_BLOCK = 0.7, i.e. a crown that
# passes 0.30 of the beam. Named here rather than in config.py because nothing live uses
# it any more -- it is a rung, not a setting.
TAU_LEGACY = 0.30

# Each rung is the previous rung's arguments plus one change. The order is the order the
# changes really landed, which is what makes the per-rung deltas attributable.
RUNGS = [
    ("baseline on disk",              dict(step=1.0, beam="step", canopy="v1",
                                           base="zeros", tau=TAU_LEGACY)),
    ("RAY_STEP 1.0 -> 0.25",          dict(step=RAY_STEP)),
    ("beam height k -> hypot",        dict(beam="hypot")),
    ("allometric crown top",          dict(canopy="v2")),
    ("crown-base slab (trunk gap)",   dict(base="v2")),
    ("crown blocks 1-TAU_LEAF",       dict(tau=TAU_LEAF)),
]


def load_dsms():
    d = {"b": np.load(f"{OUT}/dsm_buildings.npy"),
         "v1": np.load(f"{OUT}/dsm_canopy.npy"),
         "v2": np.load(f"{OUT}/dsm_canopy_v2.npy")}
    d["zeros"] = np.zeros_like(d["v1"])
    d["base_v2"] = np.load(f"{OUT}/dsm_canopy_base_v2.npy")
    return d


def build(cfg, dsm, az, el):
    top = dsm[cfg["canopy"]]
    base = dsm["zeros"] if cfg["base"] == "zeros" else dsm["base_v2"]
    return shade_factor(dsm["b"], top, base, CELL, az, el,
                        tau_leaf=cfg["tau"], step=cfg["step"], beam=cfg["beam"])


def anchor_paths(hour):
    """(bottom, top) rungs' expected files. The top one is asked of the ENGINE, so the
    check follows the v2 -> legacy fallback rather than assuming a directory."""
    bottom = f"{OUT}/shade_{hour:02d}.npy"
    try:
        from shademe.api.engine import _shade_path
        top = _shade_path(hour, "summer")
    except Exception:
        top = f"{OUT}/v2/shade_{hour:02d}.npy"
    return bottom, top


def check(arr, path):
    """Bit-for-bit against disk. Returns (verdict, detail)."""
    if not os.path.exists(path):
        return "MISSING", f"{os.path.relpath(path, ROOT)} not on disk"
    disk = np.load(path)
    if disk.shape != arr.shape:
        return "FAIL", f"shape {arr.shape} vs {disk.shape}"
    if np.array_equal(disk, arr):
        return "PASS", f"identical to {os.path.relpath(path, ROOT)}"
    d = float(np.abs(disk.astype(np.float64) - arr).max())
    return "FAIL", (f"differs from {os.path.relpath(path, ROOT)}: "
                    f"max |d| {d:.4f}, mean {float(disk.mean()):.4f} on disk "
                    f"vs {float(arr.mean()):.4f} rebuilt")


def ladder(hour, dsm, verbose=True):
    when = pd.Timestamp(f"{DAY} {hour:02d}:00", tz=TZ)
    az, el = sun_position(when)
    bottom_p, top_p = anchor_paths(hour)
    cfg, rows, prev = {}, [], None
    if verbose:
        print(f"\n{hour:02d}:00   az {az:.1f}  el {el:.1f}   {DAY}")
        print(f"  {'rung':<32}{'mean shade':>12}{'step':>10}   anchor")
    for i, (label, delta) in enumerate(RUNGS):
        cfg.update(delta)
        t0 = time.time()
        arr = build(cfg, dsm, az, el)
        m = float(arr.mean())
        step = None if prev is None else (m - prev) * 100.0
        row = {"rung": i, "label": label, "mean": m, "step_pp": step,
               "config": dict(cfg), "secs": round(time.time() - t0, 2)}
        if i == 0:
            row["verdict"], row["detail"] = check(arr, bottom_p)
            row["anchor"] = os.path.relpath(bottom_p, ROOT)
        elif i == len(RUNGS) - 1:
            row["verdict"], row["detail"] = check(arr, top_p)
            row["anchor"] = os.path.relpath(top_p, ROOT)
        rows.append(row)
        prev = m
        if verbose:
            s = "" if step is None else f"{step:+.2f} pp"
            a = f"   {row['verdict']}  {row['detail']}" if "verdict" in row else ""
            print(f"  {label:<32}{m:>12.4f}{s:>10}{a}")
        del arr
    if verbose:
        net = (rows[-1]["mean"] - rows[0]["mean"]) * 100.0
        print(f"  {'NET':<32}{'':>12}{net:>+9.2f} pp")
    return {"hour": hour, "az": az, "el": el, "rungs": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hour", type=int, default=14)
    ap.add_argument("--day", action="store_true", help="every hour 06..20")
    ap.add_argument("--json", default=f"{OUT}/bench_shade_ladder.json")
    a = ap.parse_args()

    hours = list(range(6, 21)) if a.day else [a.hour]
    stamp = provenance.stamp()
    print("bench_shade_ladder -- every rung rebuilt from the DSMs\n")
    print(provenance.block(stamp))

    dsm = load_dsms()
    print(f"\n  canopy     v1 flat {dsm['v1'][dsm['v1']>0].mean():.2f} m  ->  "
          f"v2 allometric {dsm['v2'][dsm['v2']>0].mean():.2f} m  "
          f"(crown base mean {dsm['base_v2'][dsm['base_v2']>0].mean():.2f} m)")

    res = [ladder(h, dsm) for h in hours]

    fails = [(r["hour"], row) for r in res for row in r["rungs"]
             if row.get("verdict") not in (None, "PASS")]
    if len(hours) > 1:
        print("\nday mean by rung (06..20):")
        for i, (label, _) in enumerate(RUNGS):
            ms = [r["rungs"][i]["mean"] for r in res]
            base = np.mean([r["rungs"][0]["mean"] for r in res])
            print(f"  {label:<32}{np.mean(ms):>12.4f}{(np.mean(ms)-base)*100:>+9.2f} pp vs baseline")

    json.dump({"day": DAY, "provenance": stamp, "rungs": [r[0] for r in RUNGS],
               "hours": res}, open(a.json, "w"), indent=1)
    print(f"\nwrote {os.path.relpath(a.json, ROOT)}")

    print("\nfigures from this run, safe to quote WITH this line:")
    print("  " + provenance.line(stamp))
    if fails:
        print(f"\nANCHOR FAILURES: {len(fails)} -- a raster on disk does not match the "
              f"code that claims to produce it.")
        for h, row in fails:
            print(f"  {h:02d}:00  rung {row['rung']} {row['label']}: "
                  f"{row['verdict']}  {row['detail']}")
        sys.exit(1)
    print("\nANCHORS OK -- both ends of the ladder reproduce the rasters on disk exactly.")
