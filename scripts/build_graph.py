"""OSM -> routable pedestrian graph, edges tagged + hourly shade sampled.

Run directly to produce out/graph.pkl. Indoor stitching = 15m endpoint auto-snap
plus hand-authored links in data/connectors.json.
"""
import os, sys, json, math, time, pickle, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from config import CELL, WGS84, MGA55
from pyproj import Transformer
import networkx as nx

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
OUT  = os.path.join(os.path.dirname(__file__), "..", "out")
_tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)

HOURS = list(range(6, 21))
SNAP_M = 15.0       # auto-snap radius for indoor component endpoints
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
    return (t.get("indoor") == "yes" or t.get("highway") == "corridor"
            or t.get("tunnel") == "building_passage" or t.get("level") is not None)


def is_covered(t):
    return t.get("covered") in ("yes","arcade") or t.get("man_made") == "bridge"


def levels(t):
    """Set of storeys a way occupies. Untagged -> {0}. Used to stop snapping
    a level-3 corridor onto the footpath 12m below it."""
    raw = t.get("level", t.get("layer"))
    if raw is None: return {0}
    out = set()
    for part in str(raw).replace("-", ";-").split(";"):
        part = part.strip()
        if not part: continue
        try: out.add(int(float(part)))
        except ValueError: pass
    return out or {0}


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
               arterial=False, steps=False, connector=True, note=note, name=name)
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
        lv = levels(t)
        nd = [n for n in w["nodes"] if n in P]
        for n in nd: nlev.setdefault(n, set()).update(lv)
        for a, b in zip(nd, nd[1:]):
            L = math.dist(P[a], P[b])
            if L <= 0: continue
            G.add_edge(a, b, length=L, indoor=ind, covered=cov, crossing=cross,
                       arterial=arterial, steps=t.get("highway") == "steps",
                       connector=False)
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
    G.graph["hours"] = list(hours)
    G.graph["main"] = max(nx.connected_components(G), key=len)
    return G


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
