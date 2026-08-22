"""Availability of the protected network: is this arcade open at this hour?

Separate from cost.py on purpose. Everything else in the router is a preference
expressed in metres -- how much further you would walk to avoid a degree, or a door.
Opening hours are not a preference. A shut arcade is not an expensive edge, it is an
absent one, and pricing it would let a large enough K walk the user into a locked door.
So this module produces a set of CLOSED keys and routing.py removes those edges from
consideration before any cost is evaluated.

The hours themselves are editorial estimates -- see the _README in data/indoor_hours.json.
OSM has opening_hours on 2 of the 1232 walkable indoor ways in this extract, so there is
nothing to import and the table is hand-written. The gate is correct; the coverage is a
data problem, and `verified` is false on every class until someone checks one.
"""
import json, os

DATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
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

    Below-ground `covered` is split out from the rest: a pedestrian subway is enclosed
    and shuts with the station, while a covered footpath under an awning is just a roof
    over a public street and never closes. Both are `covered` to the physics -- outdoor
    air, no beam, no sky -- and only availability tells them apart.
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

    An unknown key is treated as OPEN. The gate exists to stop the router promising a
    walk through a locked building, and inventing a closure for an edge we have no
    information about would be the same class of error in the other direction.
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
