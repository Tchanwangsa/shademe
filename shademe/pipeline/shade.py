"""Generate the SHIPPED half-hourly shade rasters -- the set the engine reads.

Allometric crown top, crown-base slab, SOLWEIG leaf transmissivity, marched at RAY_STEP
with the hypot beam height. Also writes the .png overlays, because the map has to show
the same model the router priced.

    python -m shademe.pipeline.shade                     # 2026-01-26 -> out/v2
    python -m shademe.pipeline.shade 2026-08-22 v2_winter

HALF-HOURLY, NOT HOURLY. The grid is timegrid.SLOTS and the files are shade_HHMM.npy --
shade_0630.npy is 06:30. Reasoning and the measurement behind the 30 minutes are in
shademe/timegrid.py; the short version is that the solar azimuth moves 23.4 deg in half
an hour at a January solar noon, which put ~3% of cells on the wrong side of a shadow
edge on the hourly grid. Sets written before this change hold shade_HH.npy and are still
readable -- api.engine falls back to the containing hour for a half-hour slot -- but the
manifest's `step_min` marks them as coarse so a set that fits the DAY but not the STEP is
regenerated rather than silently served as if it were half-hourly.

api.engine.ensure_shade_set() shells out to this for a day it has no set for.
shade_legacy.py writes the pre-v2 hourly set (flat 8 m canopy, no crown base), which is
the bottom rung of tools/bench_shade_ladder.py and the only reason it is kept.
"""
import os, sys, json, time, numpy as np, pandas as pd

from ..config import CELL
from ..physics.shadow import sun_position, shade_factor, SUN_MIN_DEG
from ..paths import OUT
from .shade_legacy import to_png
from .. import provenance
from .. import timegrid as TG

DAY = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SHADEME_DATE", "2026-01-26")
V2 = os.path.join(OUT, sys.argv[2] if len(sys.argv) > 2 else "v2")
TZ, SLOTS = "Australia/Melbourne", TG.SLOTS

# THE WHOLE CLOCK, and it costs LESS than the window it replaces. SLOTS was 06:00..20:00,
# which is why the API clamped the wall clock into that window and drew a sun over
# Melbourne at midnight.
#
# The dark slots need no file: below SUN_MIN_DEG every mask in the sweep returns "fully
# shaded", so the raster would be the constant 1.0 in all 6.1 M cells -- 24 MB of it.
# api.engine reads a missing raster as full shade, so writing one would only be storing
# the number 1 six million times.
#
# The sun gate is a STRICT SUBSET of the old window on every day of the year, so this
# both extends the set to the full day and SHRINKS it in every season. Measured at this
# latitude, half-hour slots that actually get a file:
#
#     2026-06-21  17  (08:30..16:30)      2026-01-26  27  (07:00..20:00)
#     2026-08-24  20  (07:30..17:00)      2026-12-21  28  (06:30..20:00)
#
# against 29 files for the window regardless of season -- so ~470 MB in January and
# ~400 MB in winter, where the window cost 676 MB every day of the year.

if __name__ == "__main__":
    os.makedirs(V2, exist_ok=True)
    dsm_b = np.load(f"{OUT}/dsm_buildings.npy")
    c_old = np.load(f"{OUT}/dsm_canopy.npy")
    c_new = np.load(f"{OUT}/dsm_canopy_v2.npy")
    c_base = np.load(f"{OUT}/dsm_canopy_base_v2.npy")
    print(f"canopy: flat {c_old[c_old>0].mean():.2f}m -> allometric "
          f"{c_new[c_new>0].mean():.2f}m on a {c_base[c_base>0].mean():.2f}m trunk")
    print(f"day {DAY} -> {os.path.relpath(V2, OUT+'/..')}  "
          f"{len(SLOTS)} slots at {TG.STEP_MIN} min\n")

    means, night, t_all = {}, [], time.time()
    for s in SLOTS:
        t0 = time.time()
        az, el = sun_position(pd.Timestamp(f"{DAY} {TG.label(s)}", tz=TZ))
        if el < SUN_MIN_DEG:
            night.append(s)
            means[s] = (float("nan"), 1.0)
            print(f"  {TG.label(s)} az {az:6.1f} el {el:6.1f}  sun down -- no file, "
                  f"read as full shade")
            continue
        s_new = shade_factor(dsm_b, c_new, c_base, CELL, az, el)
        np.save(f"{V2}/shade_{TG.hhmm(s)}.npy", s_new)
        to_png(s_new).save(f"{V2}/shade_{TG.hhmm(s)}.png", optimize=True)
        # The legacy set is the bench's bottom rung; report the gap on the hours it has,
        # never assume it. It is hourly, so the half hours have nothing to compare to.
        try:
            old = float(np.load(f"{OUT}/shade_{TG.hour_of(s):02d}.npy").mean()) \
                if TG.is_hour(s) else float("nan")
        except OSError:
            old = float("nan")
        means[s] = (old, float(s_new.mean()))
        print(f"  {TG.label(s)} az {az:6.1f} el {el:6.1f}  mean shade "
              f"{'   n/a' if np.isnan(old) else f'{old:.4f}'} -> {means[s][1]:.4f}"
              f"{'' if np.isnan(old) else f'  ({(means[s][1]-old)*100:+.2f} pp)'}"
              f"  {time.time()-t0:5.1f}s")

    # MANIFEST FIRST, THEN STAMP, THEN REWRITE. Two orderings are wrong here and both
    # were: stamping the default mode reported out/v2's January digests under a run that
    # wrote August, and stamping the DAY before the manifest exists makes the set
    # invisible to engine._sets_on_disk(), so the stamp resolves the set this run just
    # replaced. The manifest is what makes a directory discoverable, so it goes down
    # first and gets the stamp written into it on the second pass.
    lit = [s for s in SLOTS if s not in night]
    manifest = {"day": DAY, "provenance": None,
                "step_min": TG.STEP_MIN, "slots": list(SLOTS),
                # Listed explicitly so a reader of the manifest is never left guessing
                # whether a missing shade_0300.npy is night or a half-finished run.
                "night_slots": night, "sunlit_slots": lit,
                "means": {str(k): {"legacy": v[0], "shipped": v[1]}
                          for k, v in means.items()}}
    mpath = f"{V2}/shade_means_v2.json"
    json.dump(manifest, open(mpath, "w"), indent=1)
    stamp = provenance.stamp(mode=DAY)
    manifest["provenance"] = stamp
    json.dump(manifest, open(mpath, "w"), indent=1)
    hourly = {s: v for s, v in means.items() if not np.isnan(v[0])}
    o = np.mean([v[0] for v in hourly.values()]); n = np.mean([v[1] for v in hourly.values()])
    print(f"\n{len(lit)} sunlit slots {TG.label(lit[0])}..{TG.label(lit[-1])} in "
          f"{time.time()-t_all:.1f}s, {len(night)} dark slots with no file")
    print(f"day mean shade over the {len(hourly)} whole hours the legacy set also has: "
          f"{o:.4f} -> {n:.4f}  ({(n-o)*100:+.2f} pp, {(n/o-1)*100:+.1f}%)")
    print("\n" + provenance.line(stamp))
    print("\nthe legacy->shipped gap above is FIVE stacked changes, not one -- "
          "run tools/bench_shade_ladder.py to attribute it.")
