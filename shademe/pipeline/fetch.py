"""Download + cache the City of Melbourne open datasets. Never re-downloads.

    python -m shademe.pipeline.fetch
"""
import json, os, urllib.request

from ..config import BBOX, COM, DATASETS
from ..paths import DATA

def fetch(name):
    path = os.path.join(DATA, f"{name}.geojson")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print(f"  cached  {name}.geojson ({os.path.getsize(path)/1e6:.1f} MB)")
        return path
    url = COM.format(DATASETS[name])
    print(f"  fetching {name} ...")
    urllib.request.urlretrieve(url, path)
    print(f"  done    {name}.geojson ({os.path.getsize(path)/1e6:.1f} MB)")
    return path

# --------------------------------------------------------------- clip
def _envelope(geom):
    xs, ys = [], []
    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for k in c:
                walk(k)
    walk(geom["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def clip_canopy(path=None, src=None):
    """canopy.geojson (271 MB, all of the municipality) -> canopy_cbd.geojson.

    dsm and tree_heights read the clipped file, and nothing else produced it: it was
    a hand-run clip until this existed, so a fresh clone got a FileNotFoundError two
    stages in. Keeps any polygon whose ENVELOPE meets BBOX, which is what the original
    hand clip did -- reproduces the checked figures exactly (23663 features).

    Note it clips to the bare BBOX, not BBOX + BUFFER_M. Buildings rasterise 500 m past
    the box so outside ones still cast in; canopy does not get that treatment.
    """
    path = path or os.path.join(DATA, "canopy_cbd.geojson")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print(f"  cached  canopy_cbd.geojson ({os.path.getsize(path)/1e6:.1f} MB)")
        return path
    src = src or os.path.join(DATA, "canopy.geojson")
    print("  clipping canopy to the CBD bbox ...")
    feats = json.load(open(src))["features"]
    keep = []
    for f in feats:
        x0, y0, x1, y1 = _envelope(f["geometry"])
        if (x1 >= BBOX["min_lon"] and x0 <= BBOX["max_lon"]
                and y1 >= BBOX["min_lat"] and y0 <= BBOX["max_lat"]):
            keep.append(f)
    json.dump({"type": "FeatureCollection", "features": keep}, open(path, "w"))
    print(f"  done    canopy_cbd.geojson ({len(keep)} of {len(feats)} polys, "
          f"{os.path.getsize(path)/1e6:.1f} MB)")
    return path


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    for n in DATASETS:
        fetch(n)
    clip_canopy()

