"""Download + cache the open datasets. Never re-downloads."""
import os, sys, urllib.request
sys.path.insert(0, os.path.dirname(__file__))
from config import COM, DATASETS

DATA = os.path.join(os.path.dirname(__file__), "..", "data")

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
