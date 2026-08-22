"""Pull the walkable OSM network for the CBD, incl. indoor/covered ways."""
import os, sys, json, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(__file__))
from config import BBOX

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
B = f'{BBOX["min_lat"]},{BBOX["min_lon"]},{BBOX["max_lat"]},{BBOX["max_lon"]}'

QUERY = f"""[out:json][timeout:180];
(
  way["highway"]["highway"!~"motorway|motorway_link|trunk|trunk_link|construction|proposed"]({B});
);
out body;
>;
out skel qt;"""

if __name__ == "__main__":
    path = os.path.join(DATA, "osm_walk.json")
    if os.path.exists(path) and os.path.getsize(path) > 100000:
        print(f"cached osm_walk.json ({os.path.getsize(path)/1e6:.1f} MB)")
    else:
        print("querying overpass ...")
        req = urllib.request.Request(
            "https://overpass-api.de/api/interpreter",
            data=urllib.parse.urlencode({"data": QUERY}).encode(),
            headers={"User-Agent": "shademe-melbhack/0.1 (hackathon project)"},
        )
        with urllib.request.urlopen(req, timeout=300) as r, open(path, "wb") as f:
            f.write(r.read())
        print(f"done ({os.path.getsize(path)/1e6:.1f} MB)")
    d = json.load(open(path))
    els = d["elements"]
    print(f"  {sum(1 for e in els if e['type']=='way')} ways, "
          f"{sum(1 for e in els if e['type']=='node')} nodes")
