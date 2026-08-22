"""Precompute hourly shade masks (06..20) for the demo day -> .npy + RGBA .png + bounds."""
import os, sys, json, time, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from config import CELL, WGS84, MGA55
from shadow import sun_position, shade_factor
from pyproj import Transformer
from PIL import Image

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
DAY = (sys.argv[1] if len(sys.argv) > 1
       else os.environ.get("SHADEME_SUMMER_DATE", "2026-01-26"))
TZ = "Australia/Melbourne"
HOURS = range(6, 21)

SHADE_RGB = (28, 40, 70)          # cool navy; reads as shadow on a dark basemap
ALPHA_X = [0.0, 0.7, 1.0]         # shade -> alpha ramp (canopy 0.7, buildings 1.0)
ALPHA_Y = [0.0, 90.0, 140.0]


def wgs84_bounds(bounds):
    """MGA55 bbox -> WGS84 corners. The grid is ~1.2deg off true north here (grid
    convergence), so give MapLibre the 4 real corners plus an enveloping bbox."""
    minx, miny, maxx, maxy = bounds
    tf = Transformer.from_crs(MGA55, WGS84, always_xy=True)
    corners = {}
    for k, (x, y) in dict(nw=(minx, maxy), ne=(maxx, maxy),
                          se=(maxx, miny), sw=(minx, miny)).items():
        lon, lat = tf.transform(x, y)
        corners[k] = [lon, lat]
    lons = [c[0] for c in corners.values()]
    lats = [c[1] for c in corners.values()]
    return corners, dict(west=min(lons), south=min(lats), east=max(lons), north=max(lats))


def to_png(shade):
    h, w = shade.shape
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[..., 0], img[..., 1], img[..., 2] = SHADE_RGB
    img[..., 3] = np.interp(shade, ALPHA_X, ALPHA_Y).astype(np.uint8)
    return Image.fromarray(img, mode="RGBA")


if __name__ == "__main__":
    dsm_b = np.load(f"{OUT}/dsm_buildings.npy")
    dsm_c = np.load(f"{OUT}/dsm_canopy.npy")
    grid = json.load(open(f"{OUT}/grid.json"))
    print(f"grid {grid['w']}x{grid['h']} @ {grid['cell']}m")

    corners, bbox = wgs84_bounds(grid["bounds"])
    meta = dict(bbox, coordinates=[corners["nw"], corners["ne"], corners["se"], corners["sw"]],
                corners=corners, crs=MGA55, cell=grid["cell"],
                w=grid["w"], h=grid["h"], day=DAY, tz=TZ, hours=list(HOURS))
    json.dump(meta, open(f"{OUT}/shade_bounds.json", "w"), indent=1)
    print(f"bounds W{bbox['west']:.5f} S{bbox['south']:.5f} "
          f"E{bbox['east']:.5f} N{bbox['north']:.5f}")

    means = {}
    for hh in HOURS:
        t0 = time.time()
        when = pd.Timestamp(f"{DAY} {hh:02d}:00", tz=TZ)
        az, el = sun_position(when)
        # v1 path: flat 8 m canopy, no crown-base raster exists for it. Zeros ==
        # the legacy crown-to-pavement model. The live rasters come from
        # scripts/regen_shade_v2.py, which passes the real crown base.
        shade = shade_factor(dsm_b, dsm_c, np.zeros_like(dsm_c), CELL, az, el)
        np.save(f"{OUT}/shade_{hh:02d}.npy", shade)
        to_png(shade).save(f"{OUT}/shade_{hh:02d}.png", optimize=True)
        m = float(shade.mean())
        means[hh] = m
        print(f"  {hh:02d}:00  az {az:6.1f}  el {el:6.1f}  mean shade {m:.3f}  "
              f"{time.time()-t0:5.1f}s")

    print("\nmean shade by hour (want a U):")
    for hh, m in means.items():
        print(f"  {hh:02d}  {m:.3f}  " + "#" * int(m * 60))
    lo = min(means, key=means.get)
    print(f"\nminimum at {lo:02d}:00 ({means[lo]:.3f}); "
          f"ends {means[6]:.3f} / {means[20]:.3f}")
    json.dump({str(k): v for k, v in means.items()},
              open(f"{OUT}/shade_means.json", "w"), indent=1)
