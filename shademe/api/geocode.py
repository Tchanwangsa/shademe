"""Free-text place search over OpenStreetMap.

This is what replaces a hardcoded list of fifteen landmarks: anything OSM knows inside the
area ShadeMe covers -- streets, laneways, tram stops, the cafe you actually meant.

TWO PROVIDERS, ONE DATASET. Both read OpenStreetMap; they differ in how they match.

  * PHOTON answers the prefix. It is built for type-ahead, so "degrav" already returns
    Degraves Street and "flind" returns Flinders Street Station. That is the whole
    requirement for a search box someone types into.
  * NOMINATIM answers whole words only -- measured here, "degrav" and "flind" both return
    nothing at all, and "queen vic" finds three sandwich shops but not the market. It is
    the better geocoder once a query is complete, and it is the source of record for
    reverse geocoding, so it stays as the fallback and owns `reverse()`.

The chain is photon -> nominatim, and Nominatim is also tried when Photon matched nothing,
because a formal name Photon misses is exactly what Nominatim is good at. Set
SHADEME_GEOCODER to pin one provider.

RATE LIMITS ARE NOT OPTIONAL. Nominatim's usage policy caps free use at one request a
second and asks that an autocomplete send at most one request per input, not one per
keystroke; Photon's asks for fair use and self-hosting under load. So: a per-host throttle
here, a TTL cache so backspacing is free, an identifying User-Agent, and a debounce in the
client. Anything heavier than a demo should point BASE at its own instance.

A street match is a single OSM centroid, not the nearest point on the kerb -- and OSM
splits a long road into a way per block, so "Collins Street" has several. That is a
property of the data, not a bug to route around; `main.py` dedupes on what the user
actually sees and keeps the snapped distance visible in `snap_m`.
"""
import os, json, time, threading, requests

from ..config import BBOX

PHOTON = os.environ.get("SHADEME_PHOTON", "https://photon.komoot.io")
NOMINATIM = os.environ.get("SHADEME_NOMINATIM", "https://nominatim.openstreetmap.org")

# Required by Nominatim's policy -- an unidentified bulk client is the thing it exists to
# block. Points at the project rather than at a person.
HEADERS = {"User-Agent": "ShadeMe/1.0 (Melbourne cool-route routing)",
           "Accept": "application/json",
           "Accept-Language": "en-AU,en"}

# Per host, because they are different services with different policies. Nominatim's 1 rps
# is a published cap; Photon's is our own restraint on a type-ahead endpoint.
INTERVAL = {"nominatim": 1.0, "photon": 0.2}
TIMEOUT = 8                   # a search nobody is waiting for is worth less than a retry
TTL = 900                     # a street does not move; 15 min of cache is conservative
CACHE_MAX = 400

# The box the search is biased to (Photon) or ranked towards (Nominatim). Padded past
# config.BBOX so a match whose centroid sits just outside still competes -- it may be well
# inside walking range of a node. The authority on whether a result is usable is the graph
# snap in main.py, not a rectangle.
PAD = 0.01
W, Sy = BBOX["min_lon"] - PAD, BBOX["min_lat"] - PAD
E, N = BBOX["max_lon"] + PAD, BBOX["max_lat"] + PAD
CENTRE = ((BBOX["min_lat"] + BBOX["max_lat"]) / 2, (BBOX["min_lon"] + BBOX["max_lon"]) / 2)

_locks = {"nominatim": threading.Lock(), "photon": threading.Lock()}
_last = {"nominatim": 0.0, "photon": 0.0}
_cache = {}


class GeocodeError(RuntimeError):
    """A provider could not be reached, or answered with something unusable."""


def _get(host, url, params):
    """One throttled, cached GET. The lock serialises callers -- FastAPI runs sync
    endpoints in a threadpool, so two people typing could otherwise burst past the cap."""
    key = (url, tuple(sorted(params.items())))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    with _locks[host]:
        # Re-check inside the lock: while we queued, the request we were about to make may
        # already have been made and cached by whoever was holding it.
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < TTL:
            return hit[1]
        wait = INTERVAL[host] - (time.time() - _last[host])
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            _last[host] = time.time()
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise GeocodeError(f"{host}: {e}") from e
        except json.JSONDecodeError as e:
            raise GeocodeError(f"{host}: unreadable response: {e}") from e
        if len(_cache) >= CACHE_MAX:
            _cache.clear()          # demo-scale; the eviction policy is not the point
        _cache[key] = (time.time(), data)
        return data


def _row(name, address, kind, lat, lon, source):
    """The one shape both providers normalise to, so main.py never branches on which
    answered. `kind` is OSM's own word for the thing -- pedestrian, marketplace, station."""
    return {"name": name, "address": address, "kind": kind,
            "lat": float(lat), "lon": float(lon), "source": source}


def _join(*parts):
    """Comma-join the parts that are present and distinct, in order."""
    return ", ".join(dict.fromkeys(p for p in parts if p)) or None


# --- photon ---------------------------------------------------------------------

def _photon(q, limit):
    fc = _get("photon", f"{PHOTON}/api/", {
        "q": q, "limit": str(limit), "lang": "en",
        "lat": f"{CENTRE[0]:.4f}", "lon": f"{CENTRE[1]:.4f}",   # rank near the CBD
        "bbox": f"{W},{Sy},{E},{N}",
    })
    out = []
    for f in (fc or {}).get("features", []):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        c = g.get("coordinates") or []
        if len(c) < 2:
            continue
        street, num = p.get("street"), p.get("housenumber")
        # An unnamed house is its address; a named place is its name. Photon splits these
        # into different fields rather than giving one display string.
        name = p.get("name") or (f"{num} {street}" if num and street else street)
        if not name:
            continue
        kind = p.get("osm_value")
        if kind in (None, "yes"):
            kind = p.get("osm_key") or p.get("type")
        out.append(_row(name, _join(street if (street and street not in name) else None,
                                    p.get("district"), p.get("city")),
                        kind, c[1], c[0], "photon"))
    return out


# --- nominatim ------------------------------------------------------------------

def _nominatim_name(r):
    """The bold line, down the same ladder the reference client used: an English name,
    then a street address, then the head of display_name. A `name` that is only the road
    it sits on, or a bare house number, is not a name."""
    nd, a = r.get("namedetails") or {}, r.get("address") or {}
    road = a.get("road")
    name = nd.get("name:en") or nd.get("name")
    if name and name != road and not name.isdigit():
        return name
    if road:
        return f"{a['house_number']} {road}" if a.get("house_number") else road
    return (r.get("display_name") or "").split(",")[0].strip() or None


def _nominatim_context(r, name):
    """The muted second line: enough to tell two matches of one name apart. Postcode,
    state and country are dropped -- every result is in the same CBD, so they are the
    parts carrying no information here."""
    a = r.get("address") or {}
    road = a.get("road")
    area = next((a[k] for k in ("neighbourhood", "suburb", "city_district") if a.get(k)),
                None)
    city = a.get("city") or a.get("town") or a.get("municipality")
    parts = [p for p in (road if road and road not in name else None, area, city)
             if p and p != name]
    return _join(*parts)


def _nominatim(q, limit):
    rows = _get("nominatim", f"{NOMINATIM}/search", {
        "q": q, "format": "jsonv2", "addressdetails": "1", "namedetails": "1",
        "dedupe": "1", "limit": str(limit), "countrycodes": "au",
        "viewbox": f"{W},{Sy},{E},{N}", "bounded": "0",
    })
    out = []
    for r in rows if isinstance(rows, list) else []:
        name = _nominatim_name(r)
        if not name or "lat" not in r or "lon" not in r:
            continue
        # jsonv2 renames `class` to `category`; read both so the field is not silently
        # empty on whichever one the instance speaks.
        kind = r.get("type")
        if kind in (None, "yes", "house"):
            kind = r.get("category") or r.get("class") or kind
        out.append(_row(name, _nominatim_context(r, name), kind,
                        r["lat"], r["lon"], "nominatim"))
    return out


# --- the chain ------------------------------------------------------------------

PROVIDERS = {"photon": _photon, "nominatim": _nominatim}
CHAIN = [n for n in (x.strip() for x in
                     os.environ.get("SHADEME_GEOCODER", "photon,nominatim").split(","))
         if n in PROVIDERS] or ["photon", "nominatim"]


def search(q, limit=25):
    """Free-text search, ranked towards the CBD, normalised across providers.

    Deliberately over-fetches: main.py drops everything the graph cannot reach, and a
    limit of 8 here would routinely leave nothing behind that filter.

    Falls through the chain on an empty result as well as on an error -- an empty answer
    from a prefix matcher is exactly the case the whole-word matcher may still get right.
    Only the last provider's failure is raised, so one dead host does not break search.
    """
    err = None
    for name in CHAIN:
        try:
            rows = PROVIDERS[name](q, limit)
        except GeocodeError as e:
            print(f"[geocode] {name} failed: {e}")
            err = e
            continue
        if rows:
            return rows
    if err is not None:
        raise err
    return []


def reverse(lat, lon):
    """Coordinates -> one normalised row, or None. Used to name "my location" as a street.

    Nominatim only: this is the one call where whole-word matching costs nothing and its
    address breakdown is the richer of the two.
    """
    try:
        r = _get("nominatim", f"{NOMINATIM}/reverse", {
            "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
            "format": "jsonv2", "addressdetails": "1", "namedetails": "1", "zoom": "18",
        })
    except GeocodeError as e:
        print(f"[geocode] reverse failed: {e}")
        return None
    if not isinstance(r, dict) or r.get("error"):
        return None
    name = _nominatim_name(r)
    if not name:
        return None
    return _row(name, _nominatim_context(r, name), "here", lat, lon, "nominatim")
