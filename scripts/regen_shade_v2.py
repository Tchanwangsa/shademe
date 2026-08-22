"""Generate the SHIPPED hourly shade rasters (out/v2/) -- the set the engine reads.

This is the generator for what scripts/bench_shade_ladder.py calls the top rung: the
allometric crown top, the crown-base slab, and SOLWEIG's leaf transmissivity, marched at
RAY_STEP with the hypot beam height. scripts/precompute.py still writes the LEGACY set
into out/ -- flat 8 m canopy, no crown base -- which is the bench's bottom rung and the
only reason those files are kept.

It also writes the .png overlays, because the map has to show the same model the router
priced. Serving out/shade_HH.png next to a route costed off out/v2/shade_HH.npy puts two
different shade models on one screen and calls the difference a result.

    python scripts/regen_shade_v2.py                    # 2026-01-26 -> out/v2
    python scripts/regen_shade_v2.py 2026-08-22 v2_winter
"""
import os, sys, json, time, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import CELL
from shadow import sun_position, shade_factor
from precompute import to_png
import provenance

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))
DAY = (sys.argv[1] if len(sys.argv) > 1
       else os.environ.get("SHADEME_SUMMER_DATE", "2026-01-26"))
V2 = os.path.join(OUT, sys.argv[2] if len(sys.argv) > 2 else "v2")
TZ, HOURS = "Australia/Melbourne", range(6, 21)

if __name__ == "__main__":
    os.makedirs(V2, exist_ok=True)
    dsm_b = np.load(f"{OUT}/dsm_buildings.npy")
    c_old = np.load(f"{OUT}/dsm_canopy.npy")
    c_new = np.load(f"{OUT}/dsm_canopy_v2.npy")
    c_base = np.load(f"{OUT}/dsm_canopy_base_v2.npy")
    print(f"canopy: flat {c_old[c_old>0].mean():.2f}m -> allometric "
          f"{c_new[c_new>0].mean():.2f}m on a {c_base[c_base>0].mean():.2f}m trunk")
    print(f"day {DAY} -> {os.path.relpath(V2, OUT+'/..')}\n")

    means = {}
    for hh in HOURS:
        t0 = time.time()
        az, el = sun_position(pd.Timestamp(f"{DAY} {hh:02d}:00", tz=TZ))
        s_new = shade_factor(dsm_b, c_new, c_base, CELL, az, el)
        np.save(f"{V2}/shade_{hh:02d}.npy", s_new)
        to_png(s_new).save(f"{V2}/shade_{hh:02d}.png", optimize=True)
        # The legacy set is the bench's bottom rung; report the gap but never assume it.
        try:
            old = float(np.load(f"{OUT}/shade_{hh:02d}.npy").mean())
        except OSError:
            old = float("nan")
        means[hh] = (old, float(s_new.mean()))
        print(f"  {hh:02d}:00 az {az:6.1f} el {el:6.1f}  mean shade "
              f"{old:.4f} -> {means[hh][1]:.4f} "
              f"({(means[hh][1]-old)*100:+.2f} pp)  {time.time()-t0:5.1f}s")

    stamp = provenance.stamp()
    json.dump({"day": DAY, "provenance": stamp,
               "means": {str(k): {"legacy": v[0], "shipped": v[1]} for k, v in means.items()}},
              open(f"{V2}/shade_means_v2.json", "w"), indent=1)
    o = np.mean([v[0] for v in means.values()]); n = np.mean([v[1] for v in means.values()])
    print(f"\nday mean shade {o:.4f} -> {n:.4f}  ({(n-o)*100:+.2f} pp, {(n/o-1)*100:+.1f}%)")
    print("\n" + provenance.line(stamp))
    print("\nthe legacy->shipped gap above is FIVE stacked changes, not one -- "
          "run scripts/bench_shade_ladder.py to attribute it.")
