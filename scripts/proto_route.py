"""PHASE 0 CHECKS 2 + 3: indoor connectivity, and does the route actually change?"""
import os, sys, json, math, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from config import CELL
from shadow import sun_position, shade_factor
from build_graph import build, nearest
import networkx as nx

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
WHEN = pd.Timestamp("2026-01-14 16:00", tz="Australia/Melbourne")
DIRECT = 558.0   # W/m2 measured for that hour

PLACES = {
    "Melbourne Central": (-37.81001, 144.96280),
    "Emporium":          (-37.81180, 144.96330),
    "Myer":              (-37.81350, 144.96450),
    "Federation Square": (-37.81800, 144.96910),
}

print("computing shade grid ...")
dsm_b = np.load(f"{OUT}/dsm_buildings.npy"); dsm_c = np.load(f"{OUT}/dsm_canopy.npy")
grid  = json.load(open(f"{OUT}/grid.json"))
az, el = sun_position(WHEN)
shade = shade_factor(dsm_b, dsm_c, np.zeros_like(dsm_c), CELL, az, el)  # v1 proto: no crown base

print("building graph ...")
G = build(shade, grid)

# ---------- CHECK 2: indoor connectivity ----------
print("\n--- CHECK 2: indoor network ---")
IE = [(u,v) for u,v,d in G.edges(data=True) if d["indoor"] or d["covered"]]
Gi = nx.Graph(); Gi.add_edges_from(IE)
comps = sorted(nx.connected_components(Gi), key=len, reverse=True)
print(f"  {len(IE)} indoor/covered edges, {len(comps)} components, "
      f"largest has {len(comps[0])} nodes")
for name in ("Melbourne Central","Emporium","Myer"):
    n = nearest(G, *PLACES[name])
    inside = [i for i,c in enumerate(comps) if n in c]
    print(f"  {name:20} nearest node in indoor component {inside if inside else 'NONE'}")

# ---------- CHECK 3: THE GATE ----------
print("\n--- CHECK 3: does weighting shade change the route? ---")
def cost_fn(W):
    def f(u, v, d):
        sun = (DIRECT/1000.0) * (1.0 - d["shade"])
        pen = W * sun + (0.4 if d["arterial"] and d["crossing"] else 0.0)
        return d["length"] * (1.0 + pen)
    return f

def summarise(G, path, label):
    L = expo = 0.0; ind = 0.0
    for u, v in zip(path, path[1:]):
        d = G[u][v]; L += d["length"]
        expo += d["length"] * (1 - d["shade"])
        if d["indoor"] or d["covered"]: ind += d["length"]
    print(f"  {label:18} {L:6.0f}m total | {expo:6.0f}m in sun ({expo/L*100:4.1f}%) "
          f"| {ind:5.0f}m indoor ({ind/L*100:4.1f}%)")
    return L, expo

s = nearest(G, *PLACES["Melbourne Central"]); t = nearest(G, *PLACES["Federation Square"])
h = lambda a,b: math.dist(G.nodes[a]["xy"], G.nodes[b]["xy"])
p0 = nx.astar_path(G, s, t, heuristic=h, weight=cost_fn(0.0))
p3 = nx.astar_path(G, s, t, heuristic=h, weight=cost_fn(3.0))

L0, e0 = summarise(G, p0, "shortest (W=0)")
L3, e3 = summarise(G, p3, "shade-aware (W=3)")
same = (p0 == p3)
print(f"\n  routes identical? {same}")
print(f"  detour: {(L3/L0-1)*100:+.1f}% distance   sun exposure: {(e3/e0-1)*100:+.1f}%")
print("\n  GATE: " + ("FAIL - no product here" if same else "PASS - shade changes the route"))
json.dump({"shortest": [G.nodes[n]["ll"] for n in p0],
           "shaded":   [G.nodes[n]["ll"] for n in p3]},
          open(f"{OUT}/proto_routes.json","w"))
