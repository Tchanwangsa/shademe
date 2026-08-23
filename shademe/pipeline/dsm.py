"""Burn building + canopy heights into a 2 m raster (DSM) in MGA55 metres.

    python -m shademe.pipeline.dsm
"""
import os, json, numpy as np
from pyproj import Transformer
from shapely.geometry import shape
import shapely
from rasterio.features import rasterize
from rasterio.transform import from_origin

from ..config import BBOX, WGS84, MGA55, CELL, BUFFER_M, CANOPY_HEIGHT
from ..paths import DATA, OUT

_tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)

def to_mga(geom):
    """Vectorised reprojection of a shapely geom, lon/lat -> metres."""
    return shapely.transform(geom, lambda c: np.column_stack(_tf.transform(c[:, 0], c[:, 1])))

def grid_spec():
    xs, ys = _tf.transform([BBOX["min_lon"], BBOX["max_lon"]],
                           [BBOX["min_lat"], BBOX["max_lat"]])
    minx, maxx = min(xs) - BUFFER_M, max(xs) + BUFFER_M
    miny, maxy = min(ys) - BUFFER_M, max(ys) + BUFFER_M
    w = int((maxx - minx) / CELL)
    h = int((maxy - miny) / CELL)
    return from_origin(minx, maxy, CELL, CELL), h, w, (minx, miny, maxx, maxy)

def load_shapes(path, height_fn):
    feats = json.load(open(path))["features"]
    out, skipped = [], 0
    for f in feats:
        g = f.get("geometry")
        if not g:
            skipped += 1; continue
        try:
            hgt = height_fn(f["properties"])
        except Exception:
            skipped += 1; continue
        if hgt is None or hgt <= 0:
            skipped += 1; continue
        out.append((to_mga(shape(g)), float(hgt)))
    return out, skipped

def building_height(p):
    o, b = p.get("ovlhgt_ahd"), p.get("base_ahd")
    if o is None or b is None:
        return None
    return max(0.0, o - b)          # clamp: data min is -0.4

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    transform, h, w, bounds = grid_spec()
    print(f"grid {w} x {h} @ {CELL}m  ({w*h/1e6:.1f}M cells)")

    print("loading buildings ...")
    b, sk = load_shapes(os.path.join(DATA, "buildings.geojson"), building_height)
    print(f"  {len(b)} buildings ({sk} skipped)  max height {max(x[1] for x in b):.1f}m")

    print("loading canopy ...")
    c, sk = load_shapes(os.path.join(DATA, "canopy_cbd.geojson"), lambda p: CANOPY_HEIGHT)
    print(f"  {len(c)} canopy polys ({sk} skipped)")

    print("rasterising ...")
    # rasterio has no max-merge: sort ascending so the tallest overwrites last
    b.sort(key=lambda x: x[1])
    c.sort(key=lambda x: x[1])
    dsm_b = rasterize(b, out_shape=(h, w), transform=transform,
                      dtype="float32", fill=0.0)
    dsm_c = rasterize(c, out_shape=(h, w), transform=transform,
                      dtype="float32", fill=0.0)

    np.save(os.path.join(OUT, "dsm_buildings.npy"), dsm_b)
    np.save(os.path.join(OUT, "dsm_canopy.npy"), dsm_c)
    json.dump({"bounds": bounds, "cell": CELL, "w": w, "h": h},
              open(os.path.join(OUT, "grid.json"), "w"))
    print(f"  buildings: {(dsm_b>0).sum()/1e6:.2f}M cells built, max {dsm_b.max():.1f}m")
    print(f"  canopy:    {(dsm_c>0).sum()/1e6:.2f}M cells, {(dsm_c>0).mean()*100:.1f}% cover")
    print("saved out/dsm_*.npy")
