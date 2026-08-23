"""Download + cache the City of Melbourne open datasets. Never re-downloads.

    python -m shademe.pipeline.fetch
"""
import os, urllib.request

from ..config import COM, DATASETS
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

if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    for n in DATASETS:
        fetch(n)
