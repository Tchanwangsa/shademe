"""Availability of the protected network: is this arcade open at this hour?

Separate from cost.py on purpose. Everything else in the router is a preference expressed
in metres. Opening hours are not a preference: a shut arcade is not an expensive edge, it
is an absent one, and pricing it would let a large enough K walk the user into a locked
door. So this produces a set of CLOSED keys and routing.py removes those edges before any
cost is evaluated.

The hours themselves are editorial estimates -- OSM carries opening_hours on 2 of the 1232
walkable indoor ways here, so the table is hand-written and `verified` is false on every
class. The gate is correct; the coverage is a data problem.
"""
import json, os

from ..paths import DATA

PATH = os.path.join(DATA, "indoor_hours.json")
DOW = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TZ = "Australia/Melbourne"

_T = None


def table():
    global _T
    if _T is None:
        try:
            _T = json.load(open(PATH))
        except (OSError, ValueError) as e:
            print(f"[hours] {PATH} unreadable ({e}); nothing will be gated")
            _T = {"classes": {}, "defaults": {}, "places": []}
    return _T


def default_key(indoor, covered, level=0):
    """Which class an edge falls into when no curated place contains it.

    Below-ground `covered` is split out: a pedestrian subway shuts with the station,
    while a covered footpath under an awning never closes. Both are `covered` to the
    physics -- outdoor air, no beam, no sky -- and only availability tells them apart.
    """
    d = table().get("defaults", {})
    if indoor:
        return d.get("indoor", "arcade")
    if covered:
        return (d.get("covered_below_ground", "station") if level < 0
                else d.get("covered", "public"))
    return None


def key_for(lon, lat, indoor, covered, level=0):
    """Curated place by bounding box, else the class default. None for outdoor edges."""
    if not (indoor or covered):
        return None
    for p in table().get("places", []):
        w, s, e, n = p["bbox"]
        if w <= lon <= e and s <= lat <= n:
            return p["class"]
    return default_key(indoor, covered, level)


def is_open(key, hour, dow):
    """dow: 0 = Monday .. 6 = Sunday. `hour` may be fractional.

    An unknown key is treated as OPEN: inventing a closure for an edge we know nothing
    about is the same class of error as promising a walk through a locked building.
    """
    if key is None:
        return True
    c = table().get("classes", {}).get(key)
    if c is None or c.get("always"):
        return True
    span = (c.get("week") or {}).get(DOW[int(dow) % 7])
    if span is None:
        return False
    return float(span[0]) <= float(hour) < float(span[1])


def closed_keys(hour, dow):
    """The set of class keys that are shut at this time. Cheap; there are four of them."""
    return {k for k in table().get("classes", {}) if not is_open(k, hour, dow)}


def now_dow():
    import pandas as pd
    return pd.Timestamp.now(tz=TZ).dayofweek


def describe(hour, dow):
    """What the gate is doing, for the /route meta block."""
    shut = sorted(closed_keys(hour, dow))
    return {"dow": DOW[int(dow) % 7], "hour": hour, "closed_classes": shut,
            "verified": False}
