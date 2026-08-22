"""Helper for authoring data/connectors.json. Prints real OSM node ids + component ids.

  find_nodes.py near   LAT LON [radius=60] [name]   nearest indoor nodes
  find_nodes.py comp   ID [ID...]                   what a component is + its extremities
  find_nodes.py between A B [max=120]               closest node pairs across 2 components
  find_nodes.py check                               validate data/connectors.json
"""
import os, sys, json, math, collections
sys.path.insert(0, os.path.dirname(__file__))
from build_graph import (build, walkable, is_indoor, is_covered, indoor_subgraph, _tf)
import networkx as nx

DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def load():
    G = build(sample=False, connectors=False, verbose=False)
    Gi = indoor_subgraph(G)
    comps = sorted(nx.connected_components(Gi), key=len, reverse=True)
    cid = {n: i for i, c in enumerate(comps) for n in c}
    # node -> way names, so a printed node id is identifiable on the ground
    d = json.load(open(f"{DATA}/osm_walk.json"))
    nm = collections.defaultdict(set)
    for e in d["elements"]:
        if e["type"] != "way": continue
        t = e.get("tags", {})
        if not walkable(t): continue
        label = t.get("name") or t.get("addr:housename") or t.get("building")
        if not label:
            label = t.get("highway", "?")
            for k in ("indoor", "covered", "tunnel", "level", "bridge"):
                if t.get(k): label += f"/{k}={t[k]}"
        for n in e["nodes"]: nm[n].add(label)
    return G, Gi, comps, cid, nm


def tag(cid, comps, n):
    return f"c#{cid[n]:<3d}(n={len(comps[cid[n]]):<4d})" if n in cid else "OUTDOOR      "


def cmd_near(G, Gi, comps, cid, nm, lat, lon, radius=60.0, label=""):
    x, y = _tf.transform(float(lon), float(lat))
    hits = []
    for n in Gi.nodes:
        d = math.dist(G.nodes[n]["xy"], (x, y))
        if d <= radius: hits.append((d, n))
    hits.sort()
    print(f"\n== {label or f'{lat},{lon}'} == {len(hits)} indoor nodes within {radius:.0f}m")
    seen = set()
    for d, n in hits[:40]:
        c = cid[n]
        mark = "" if c not in seen else "  ."
        seen.add(c)
        print(f"  {d:5.1f}m  node {n:<12d} {tag(cid,comps,n)}{mark} "
              f"{' | '.join(sorted(nm[n]))[:80]}")


def cmd_comp(G, comps, cid, nm, i):
    c = comps[int(i)]
    lats = [G.nodes[n]["ll"][1] for n in c]; lons = [G.nodes[n]["ll"][0] for n in c]
    names = collections.Counter(x for n in c for x in nm[n])
    print(f"\n== component #{i} == {len(c)} nodes  centre "
          f"{sum(lats)/len(lats):.5f},{sum(lons)/len(lons):.5f}")
    print(f"   bbox lat {min(lats):.5f}..{max(lats):.5f}  lon {min(lons):.5f}..{max(lons):.5f}")
    for k, v in names.most_common(12): print(f"   {v:4d}  {k}")


def cmd_between(G, comps, cid, nm, a, b, cap=120.0):
    A, B = comps[int(a)], comps[int(b)]
    pairs = []
    for u in A:
        xu = G.nodes[u]["xy"]
        for v in B:
            d = math.dist(xu, G.nodes[v]["xy"])
            if d <= cap: pairs.append((d, u, v))
    pairs.sort()
    print(f"\n== closest links #{a} <-> #{b} == {len(pairs)} pairs under {cap:.0f}m")
    for d, u, v in pairs[:12]:
        print(f"  {d:6.1f}m  {u} -> {v}")
        print(f"          a: {' | '.join(sorted(nm[u]))[:70]}")
        print(f"          b: {' | '.join(sorted(nm[v]))[:70]}")
        print(f"          a@ {G.nodes[u]['ll'][1]:.5f},{G.nodes[u]['ll'][0]:.5f}"
              f"   b@ {G.nodes[v]['ll'][1]:.5f},{G.nodes[v]['ll'][0]:.5f}")


def cmd_check(G, comps, cid, nm):
    spec = json.load(open(f"{DATA}/connectors.json"))
    print(f"\n== {len(spec)} authored connectors ==")
    bad = 0
    for c in spec:
        a, b = c["a"], c["b"]
        if a not in G or b not in G:
            print(f"  MISSING NODE  {c['name']}"); bad += 1; continue
        d = math.dist(G.nodes[a]["xy"], G.nodes[b]["xy"])
        flag = "  <-- TOO LONG" if d > 60 else ""
        if flag: bad += 1
        print(f"  {d:5.1f}m  {tag(cid,comps,a)} -> {tag(cid,comps,b)}  {c['name']}{flag}")
    print(f"\n  {bad} problems")


if __name__ == "__main__":
    G, Gi, comps, cid, nm = load()
    a = sys.argv[1:]
    if not a: print(__doc__); sys.exit(0)
    if a[0] == "near":
        cmd_near(G, Gi, comps, cid, nm, a[1], a[2],
                 float(a[3]) if len(a) > 3 else 60.0, " ".join(a[4:]))
    elif a[0] == "comp":
        for i in a[1:]: cmd_comp(G, comps, cid, nm, i)
    elif a[0] == "between":
        cmd_between(G, comps, cid, nm, a[1], a[2], float(a[3]) if len(a) > 3 else 120.0)
    elif a[0] == "check":
        cmd_check(G, comps, cid, nm)
