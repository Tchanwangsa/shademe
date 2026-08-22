"""Regenerate hourly shade rasters against the allometric canopy DSM (v2).

Writes to out/v2/ so the live Phase-2 demo in out/ keeps working until we cut over.
Same day/hours/method as scripts/precompute.py -- only the canopy DSM changes.
"""
import os, sys, json, time, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from config import CELL
from shadow import sun_position, shade_factor

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
# usage: regen_shade_v2.py [YYYY-MM-DD] [outdir]
DAY = (sys.argv[1] if len(sys.argv) > 1
       else os.environ.get("SHADEME_SUMMER_DATE", "2026-01-26"))
V2  = os.path.join(OUT, sys.argv[2] if len(sys.argv) > 2 else "v2")
TZ, HOURS = "Australia/Melbourne", range(6, 21)

if __name__ == "__main__":
    os.makedirs(V2, exist_ok=True)
    dsm_b = np.load(f"{OUT}/dsm_buildings.npy")
    c_old = np.load(f"{OUT}/dsm_canopy.npy")
    c_new = np.load(f"{OUT}/dsm_canopy_v2.npy")
    print(f"canopy: flat {c_old[c_old>0].mean():.2f}m -> allometric {c_new[c_new>0].mean():.2f}m")
    means = {}
    for hh in HOURS:
        t0 = time.time()
        az, el = sun_position(pd.Timestamp(f"{DAY} {hh:02d}:00", tz=TZ))
        s_new = shade_factor(dsm_b, c_new, CELL, az, el)
        np.save(f"{V2}/shade_{hh:02d}.npy", s_new)
        try:
            s_old = np.load(f"{OUT}/shade_{hh:02d}.npy"); old = float(s_old.mean())
        except OSError:
            old = float("nan")
        means[hh] = (old, float(s_new.mean()))
        print(f"  {hh:02d}:00 az {az:6.1f} el {el:6.1f}  mean shade "
              f"{old:.4f} -> {means[hh][1]:.4f} "
              f"({(means[hh][1]-old)*100:+.2f} pp)  {time.time()-t0:5.1f}s")
    json.dump({"day": DAY, "means": {str(k): {"old": v[0], "new": v[1]} for k, v in means.items()}},
              open(f"{V2}/shade_means_v2.json", "w"), indent=1)
    o = np.mean([v[0] for v in means.values()]); n = np.mean([v[1] for v in means.values()])
    print(f"\nday mean shade {o:.4f} -> {n:.4f}  ({(n-o)*100:+.2f} pp, {(n/o-1)*100:+.1f}%)")
