"""OSM -> routable pedestrian graph, edges tagged and hourly shade sampled.

    python -m shademe.pipeline.graph      # writes out/graph.pkl

Indoor stitching is a 15 m endpoint auto-snap plus hand-authored links in
data/connectors.json.
"""
import os, json, math, time, pickle, collections, numpy as np
from pyproj import Transformer
import networkx as nx

from ..config import CELL, WGS84, MGA55
from ..paths import DATA, OUT

_tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)

HOURS = list(range(6, 21))
SNAP_M = 15.0       # auto-snap radius for indoor component endpoints
# The day the pickled shade dict is baked for. The API re-samples per request
# date, so this only fixes what a fresh `graph.pkl` carries as its baseline.
BAKE_DATE = os.environ.get("SHADEME_DATE", "2026-01-26")
SNAP_PER_NODE = 2   # max connectors grown off one endpoint
N_SAMPLE = 8        # shade sample points per edge

WALK = {"footway","path","pedestrian","steps","corridor","living_street","residential",
        "service","unclassified","tertiary","tertiary_link","secondary","secondary_link",
        "primary","primary_link","track","road","cycleway","platform"}
BIG  = {"primary","primary_link","secondary","secondary_link"}   # arterials: worse crossings


def walkable(t):
    if t.get("highway") not in WALK: return False
    if t.get("foot") in ("no","private"): return False
    if t.get("access") in ("no","private"): return False
    return True


def is_indoor(t):
    """Inside a building: conditioned air, no sky, no beam. See api.engine.INDOOR_TA.

    `level is not None` USED to be part of this test and was wrong in both directions. A
    level tag is a statement about vertical position, not about walls: 450 of the 1232
    ways it caught -- about 5 km, including William Barak Bridge and QV Square -- are open
    to the sky and were priced as 22.5 degC air-conditioned interior. Vertical position is
    now handled where it belongs: below_ground() sends subways to `covered`, and
    deck_height() raises the shade receiver for everything above.
    """
    return (t.get("indoor") == "yes" or t.get("highway") == "corridor"
            or t.get("tunnel") == "building_passage")


def below_ground(t):
    """Any part of the way is under the surface -> no sky and no beam, but not indoor.

    Degraves Street Subway, Campbell Arcade, the station concourses. Enclosed, so
    modelling them as sunlit is absurd; not shopping-centre air conditioning either, so
    INDOOR_TA would oversell them. `covered` is exactly the middle case the engine has.
    Steps that SPAN the surface (level=0;-1) count -- most of their length is below.
    """
    return min(storeys(t)) < 0


def is_covered(t):
    return (t.get("covered") in ("yes","arcade") or t.get("man_made") == "bridge"
            or below_ground(t))


def storeys(t):
    """Set of BUILDING STOREYS a way occupies, from `level` alone. Untagged -> {0}.

    Deliberately not levels(): that falls back to `layer`, a rendering z-order tag, not a
    height. 569 walkable ways here carry a layer with no level and are ordinary streets
    passing under or over something else. Reading layer as a storey sent 22 km of footpath
    to `covered` and made every way crossing a bridge into a deck.
    """
    return _parse_levels(t.get("level"))


def levels(t):
    """Set of levels a way occupies, `level` or failing that `layer`. Untagged -> {0}.

    Only for the snapping guard, which genuinely wants both: a way separated by either
    tag should not be auto-joined to the footpath below it. Physics reads storeys()."""
    raw = t.get("level", t.get("layer"))
    return _parse_levels(raw)


def _parse_levels(raw):
    if raw is None: return {0}
    out = set()
    for part in str(raw).replace("-", ";-").split(";"):
        part = part.strip()
        if not part: continue
        try: out.add(int(float(part)))
        except ValueError: pass
    return out or {0}


STOREY_M = 4.0      # fallback deck height per storey when the DSM has no structure
DECK_MIN_M = 2.0    # below this the "deck" is just DSM noise; treat the point as ground
STEP_RISE_M = 0.17  # AS 1428 riser; only used when step_count is mapped


def _way_len(P, nd):
    return sum(math.dist(P[a], P[b]) for a, b in zip(nd, nd[1:]))


def is_deck(t):
    """Does this way sit on something, rather than on the ground?

    Kept narrow. The deck height is read off the building DSM, so a plain footpath
    clipping a tower footprint would otherwise pick up 100 m of "deck" and be declared
    permanently sunlit. A way only qualifies if it SAYS it is elevated.
    """
    return t.get("bridge") in ("yes", "viaduct", "boardwalk") or max(storeys(t)) > 0


def step_rise(t):
    """Metres climbed by a stepped way. None when the way is not steps.

    step_count is mapped on 278 of 848 stepped ways here; otherwise fall back to the level
    span, then to one storey. Sign is dropped -- the graph is undirected, so an edge
    cannot know which way you are walking.
    """
    if t.get("highway") != "steps":
        return None
    n = t.get("step_count")
    if n is not None:
        try:
            return abs(float(n)) * STEP_RISE_M
        except ValueError:
            pass
    lv = storeys(t)
    span = max(lv) - min(lv)
    return abs(span) * STOREY_M if span else STOREY_M


# ---------- spatial index (uniform bucket hash over MGA55 metres) ----------
_BUCKET = 20.0

def _index(P):
    idx = {}
    for n, (x, y) in P.items():
        idx.setdefault((int(x // _BUCKET), int(y // _BUCKET)), []).append(n)
    return idx


def _near(idx, P, x, y, r):
    ci, cj, k = int(x // _BUCKET), int(y // _BUCKET), int(r // _BUCKET) + 1
    out = []
    for i in range(ci - k, ci + k + 1):
        for j in range(cj - k, cj + k + 1):
            for n in idx.get((i, j), ()):
                d = math.hypot(P[n][0] - x, P[n][1] - y)
                if d <= r: out.append((d, n))
    out.sort()
    return out


def indoor_subgraph(G):
    Gi = nx.Graph()
    Gi.add_edges_from((u, v) for u, v, d in G.edges(data=True) if d["indoor"] or d["covered"])
    return Gi


def indoor_components(G):
    return sorted(nx.connected_components(indoor_subgraph(G)), key=len, reverse=True)


def _add_connector(G, P, a, b, indoor, note, name=None, covered=False):
    L = math.dist(P[a], P[b])
    G.add_edge(a, b, length=max(L, 1.0), indoor=indoor, covered=covered, crossing=False,
               arterial=False, steps=False, connector=True, note=note, name=name,
               level=0, deck=False, conveying=False, rise_m=0.0)
    return L


def autosnap(G, P, nlev, radius=SNAP_M, per_node=SNAP_PER_NODE):
    """Join degree-1 endpoints of indoor components to whatever is within `radius`.
    Only endpoints -- snapping every indoor node would tunnel through walls."""
    Gi = indoor_subgraph(G)
    comp = {n: i for i, c in enumerate(nx.connected_components(Gi)) for n in c}
    ends = [n for n in Gi.nodes if Gi.degree(n) == 1]
    idx = _index({n: P[n] for n in G.nodes})
    n_stitch = n_ground = 0
    for u in ends:
        x, y = P[u]
        cands = []
        for d, v in _near(idx, P, x, y, radius):
            if v == u or G.has_edge(u, v): continue
            if not (nlev.get(u, {0}) & nlev.get(v, {0})): continue   # different storey
            if v in comp:
                if comp[v] == comp[u]: continue                      # same island, no gain
                cands.append((0, d, v))                              # indoor merge: priority
            else:
                cands.append((1, d, v))                              # ground it to the street
        cands.sort()
        for rank, d, v in cands[:per_node]:
            if rank == 0:
                _add_connector(G, P, u, v, True, "auto: indoor endpoint snap")
                n_stitch += 1
            else:
                # a doorway onto the footpath is not itself indoor
                G.add_edge(u, v, length=max(d, 1.0), indoor=False, covered=False,
                           crossing=False, arterial=False, steps=False,
                           level=0, deck=False, conveying=False, rise_m=0.0,
                           connector=True, note="auto: doorway snap to street")
                n_ground += 1
    return n_stitch, n_ground


def apply_connectors(G, P, path=None):
    path = path or os.path.join(DATA, "connectors.json")
    if not os.path.exists(path):
        print("  no connectors.json"); return 0
    spec = json.load(open(path))
    ok = skip = 0
    for c in spec:
        a, b = c["a"], c["b"]
        if a not in G or b not in G:
            print(f"  ! connector '{c.get('name')}' missing node {a if a not in G else b}")
            skip += 1; continue
        # "indoor": false for links that are genuinely open to the sky -- shade gets sampled
        L = _add_connector(G, P, a, b, c.get("indoor", True), c.get("note", ""),
                           c.get("name"), c.get("covered", False))
        if L > 80:
            print(f"  ! connector '{c.get('name')}' is {L:.0f}m -- implausibly long")
        ok += 1
    print(f"  connectors.json: {ok} applied, {skip} skipped")
    return ok


def edge_points(G, edges, grid, n=N_SAMPLE):
    """(rows, cols, inside) for n evenly spaced sample points along each edge."""
    minx, miny, maxx, maxy = grid["bounds"]
    H, W = grid["h"], grid["w"]
    xy = np.array([[G.nodes[u]["xy"], G.nodes[v]["xy"]] for u, v in edges])   # (E,2,2)
    f = ((np.arange(n) + 0.5) / n)[None, :, None]
    pts = xy[:, 0][:, None, :] + (xy[:, 1] - xy[:, 0])[:, None, :] * f        # (E,n,2)
    r = ((maxy - pts[..., 1]) / grid["cell"]).astype(np.int32)
    c = ((pts[..., 0] - minx) / grid["cell"]).astype(np.int32)
    inside = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    return np.clip(r, 0, H - 1), np.clip(c, 0, W - 1), inside


def deck_height(G, edges, rows, cols, dsm_b):
    """Metres above the receiver's own ground cell, per sample point, for deck edges.

    Read off the building DSM where there is a structure, and from the level tag only
    where there is not. The DSM is the actual elevation rather than storeys x 4 m, and
    because point_shade() excludes any blocker at or below the receiver, a deck whose z0
    IS its own DSM value cannot shadow itself -- which is the entire bug. Non-deck edges
    get 0.0 and behave exactly as before.
    """
    z = np.zeros(rows.shape, dtype=np.float32)
    h_dsm = dsm_b[rows, cols]
    for i, (u, v) in enumerate(edges):
        d = G[u][v]
        if not d.get("deck"):
            continue
        fallback = max(int(d.get("level", 0)), 0) * STOREY_M
        z[i] = np.where(h_dsm[i] > DECK_MIN_M, h_dsm[i], fallback)
    return z


def deck_shade(G, deck, grid, day, hours=HOURS, n=N_SAMPLE, canopy=None):
    """Shade for `deck` edges with the receiver raised. -> (array (H, E), z per point).

    Split out from sample_elevated() because api.engine.edge_index() needs the same
    numbers in a different container -- it re-samples per day at request time and never
    touches the pickled `shade` dict. One implementation, two callers.

    `day` must be the day the rasters being overwritten were generated for, and `canopy`
    likewise: build() bakes the LEGACY ground set into the pickle while the engine
    re-samples out/v2 at runtime, so the two callers pass different pairs on purpose.
    """
    import pandas as pd
    from ..physics.shadow import point_shade, sun_position
    from ..config import TAU_LEAF
    top, base = canopy or ("dsm_canopy_v2.npy", "dsm_canopy_base_v2.npy")
    dsm_b = np.load(f"{OUT}/dsm_buildings.npy")
    dsm_c = np.load(f"{OUT}/{top}")
    dsm_cb = (np.zeros_like(dsm_c) if base is None else np.load(f"{OUT}/{base}"))
    rows, cols, inside = edge_points(G, deck, grid, n)
    z = deck_height(G, deck, rows, cols, dsm_b)
    cnt = np.maximum(inside.sum(1), 1)
    fr, fc, fz = rows.ravel(), cols.ravel(), z.ravel()
    out = np.zeros((len(hours), len(deck)), dtype=np.float32)
    for i, h in enumerate(hours):
        az, el = sun_position(pd.Timestamp(f"{day} {h:02d}:00", tz="Australia/Melbourne"))
        v = point_shade(dsm_b, dsm_c, dsm_cb, grid["cell"], az, el, fr, fc, fz,
                        tau_leaf=TAU_LEAF)
        out[i] = (v.reshape(rows.shape) * inside).sum(1) / cnt
    return out, z


def sample_elevated(G, edges, grid, day, hours=HOURS, n=N_SAMPLE, canopy=None,
                    verbose=True):
    """Write deck_shade() into edge['shade'], overwriting the flat ground gather."""
    deck = [(u, v) for u, v in edges if G[u][v].get("deck")]
    if not deck:
        return 0
    try:
        vals, z = deck_shade(G, deck, grid, day, hours, n, canopy)
    except OSError as e:
        print(f"  ! deck re-sample skipped, missing {e.filename}")
        return 0
    for i, h in enumerate(hours):
        was = np.mean([G[u][v_]["shade"][h] for u, v_ in deck])
        for (u, v_), sv in zip(deck, vals[i]):
            G[u][v_]["shade"][h] = float(sv)
        if verbose and h in (10, 14, 17):
            print(f"  deck re-sample @{h:02d}:00  {was:.3f} -> {vals[i].mean():.3f} "
                  f"over {len(deck)} edges (mean deck {z[z > 0].mean():.1f} m up)")
    return len(deck)


def sample_hourly(G, grid, hours=HOURS, n=N_SAMPLE, out=OUT):
    """Fill edge['shade'] = {hour: float}. Indoor/covered edges are 1.0 always."""
    minx, miny, maxx, maxy = grid["bounds"]
    H, W = grid["h"], grid["w"]
    sun, full = [], {h: 1.0 for h in hours}
    for u, v, d in G.edges(data=True):
        if d["indoor"] or d["covered"]:
            d["shade"] = dict(full)
        else:
            sun.append((u, v))
    if not sun: return

    xy = np.array([[G.nodes[u]["xy"], G.nodes[v]["xy"]] for u, v in sun])   # (E,2,2)
    f = ((np.arange(n) + 0.5) / n)[None, :, None]
    pts = xy[:, 0][:, None, :] + (xy[:, 1] - xy[:, 0])[:, None, :] * f      # (E,n,2)
    r = ((maxy - pts[..., 1]) / CELL).astype(np.int32)
    c = ((pts[..., 0] - minx) / CELL).astype(np.int32)
    inside = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    flat = (np.clip(r, 0, H - 1) * W + np.clip(c, 0, W - 1))
    cnt = inside.sum(1)
    cnt[cnt == 0] = 1

    for h in hours:
        p = f"{out}/shade_{h:02d}.npy"
        if not os.path.exists(p):
            print(f"  ! missing {p}; shade[{h}] = 0"); vals = np.zeros(len(sun), dtype=np.float32)
        else:
            g = np.load(p, mmap_mode="r").ravel()
            vals = (np.asarray(g[flat]) * inside).sum(1) / cnt
        for (u, v), s in zip(sun, vals):
            G[u][v].setdefault("shade", {})[h] = float(s)


def build(hours=HOURS, sample=True, snap=True, connectors=True, verbose=True):
    d = json.load(open(f"{DATA}/osm_walk.json"))
    nodes, ways = {}, []
    for e in d["elements"]:
        if e["type"] == "node":
            nodes[e["id"]] = (e["lon"], e["lat"])
        elif e["type"] == "way" and walkable(e.get("tags", {})):
            ways.append(e)
    if verbose: print(f"  {len(ways)} walkable ways, {len(nodes)} nodes")

    ids = list(nodes)
    xs, ys = _tf.transform([nodes[i][0] for i in ids], [nodes[i][1] for i in ids])
    P = {i: (x, y) for i, x, y in zip(ids, xs, ys)}

    G = nx.Graph()
    nlev = {}
    for w in ways:
        t = w.get("tags", {})
        ind, cov = is_indoor(t), is_covered(t)
        cross = t.get("footway") == "crossing" or t.get("highway") == "crossing"
        arterial = t.get("highway") in BIG
        lv, sto = levels(t), storeys(t)
        nd = [n for n in w["nodes"] if n in P]
        for n in nd: nlev.setdefault(n, set()).update(lv)
        # Vertical metadata is carried, not consumed, here. It was being discarded at
        # the door, which is why the graph could not charge for a staircase.
        deck = is_deck(t) and not ind
        conveying = bool(t.get("conveying")) or t.get("highway") == "elevator"
        rise = step_rise(t)
        wl = _way_len(P, nd)
        for a, b in zip(nd, nd[1:]):
            L = math.dist(P[a], P[b])
            if L <= 0: continue
            G.add_edge(a, b, length=L, indoor=ind, covered=cov, crossing=cross,
                       arterial=arterial, steps=t.get("highway") == "steps",
                       connector=False, level=max(sto), deck=deck, conveying=conveying,
                       rise_m=(round(rise * L / max(wl, 1e-6), 3)
                               if rise is not None else 0.0))
    for n in G.nodes:
        G.nodes[n]["ll"] = nodes[n]; G.nodes[n]["xy"] = P[n]

    comps0 = indoor_components(G)
    if verbose:
        print(f"  base graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        print(f"  indoor before stitching: {len(comps0)} components, "
              f"largest {len(comps0[0])} nodes")

    if snap:
        st, gr = autosnap(G, P, nlev)
        if verbose: print(f"  auto-snap: {st} indoor-indoor, {gr} doorway-to-street")
    if connectors:
        apply_connectors(G, P)

    comps1 = indoor_components(G)
    if verbose:
        print(f"  indoor after  stitching: {len(comps1)} components, "
              f"largest {len(comps1[0])} nodes  (sizes {[len(c) for c in comps1[:8]]})")

    if sample:
        grid = json.load(open(f"{OUT}/grid.json"))
        t0 = time.time()
        sample_hourly(G, grid, hours)
        if verbose: print(f"  shade sampled for {len(hours)} hours in {time.time()-t0:.1f}s")
        # Decks last: this OVERWRITES the flat gather for ways not on the ground, and
        # the day must match the rasters sample_hourly just read.
        nd_ = sample_elevated(G, list(G.edges()), grid, BAKE_DATE, hours,
                              canopy=("dsm_canopy.npy", None), verbose=verbose)
        if verbose: print(f"  deck edges re-sampled with a raised receiver: {nd_}")
    attach_hours_key(G, verbose=verbose)
    G.graph["hours"] = list(hours)
    G.graph["main"] = max(nx.connected_components(G), key=len)
    return G


def attach_hours_key(G, verbose=True):
    """Stamp each protected edge with the availability class it belongs to.

    Baked rather than looked up per request: the alternative is a bbox test against every
    curated place for all 61k edges on every route. The classes are stable strings, so
    changing the HOURS is a data edit with no rebuild.
    """
    from ..api.hours import key_for
    n = collections.Counter()
    for u, v, d in G.edges(data=True):
        if not (d["indoor"] or d["covered"]):
            continue
        (lo1, la1), (lo2, la2) = G.nodes[u]["ll"], G.nodes[v]["ll"]
        k = key_for((lo1 + lo2) / 2, (la1 + la2) / 2,
                    d["indoor"], d["covered"], d.get("level", 0))
        d["hours_key"] = k
        n[k] += 1
    if verbose:
        print("  availability classes: " + ", ".join(f"{k} {v}" for k, v in n.most_common()))


def nearest(G, lat, lon):
    x, y = _tf.transform(lon, lat)
    return min(G.nodes, key=lambda n: (G.nodes[n]["xy"][0]-x)**2 + (G.nodes[n]["xy"][1]-y)**2)


def nearest_walkable(G, lat, lon):
    """Same, but ignores isolated islands -- only the main connected component."""
    main = G.graph.get("main")
    if not main:
        main = G.graph["main"] = max(nx.connected_components(G), key=len)
    x, y = _tf.transform(lon, lat)
    return min(main, key=lambda n: (G.nodes[n]["xy"][0]-x)**2 + (G.nodes[n]["xy"][1]-y)**2)


if __name__ == "__main__":
    t0 = time.time()
    G = build()
    ind = sum(1 for _, _, d in G.edges(data=True) if d["indoor"] or d["covered"])
    con = sum(1 for _, _, d in G.edges(data=True) if d["connector"])
    print(f"\n  {G.number_of_nodes()} nodes / {G.number_of_edges()} edges  "
          f"({ind} indoor-or-covered, {con} connectors)")
    print(f"  main component: {len(G.graph['main'])} nodes "
          f"({len(G.graph['main'])/G.number_of_nodes()*100:.1f}%)")
    for h in (8, 13, 16, 20):
        v = [d["shade"][h] for _, _, d in G.edges(data=True)]
        print(f"  mean edge shade @{h:02d}:00 = {sum(v)/len(v):.3f}")
    with open(f"{OUT}/graph.pkl", "wb") as f:
        pickle.dump(G, f, protocol=4)
    print(f"  wrote out/graph.pkl ({os.path.getsize(OUT+'/graph.pkl')/1e6:.1f} MB) "
          f"in {time.time()-t0:.1f}s")
