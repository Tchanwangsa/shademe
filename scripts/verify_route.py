"""PHASE 2 GATE: re-run the demo route off out/graph.pkl and compare to the Phase 0 baseline."""
import os, sys, json, math, pickle
sys.path.insert(0, os.path.dirname(__file__))
from build_graph import nearest_walkable
import networkx as nx

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
HOUR = int(sys.argv[1]) if len(sys.argv) > 1 else 16
DIRECT = 558.0    # W/m2 for 2026-01-14 16:00
BASELINE = {"distance": 1206, "sun_pct": 24.8, "indoor_pct": 8.0,
            "shaded_distance": 1355, "shaded_sun_pct": 7.0, "shaded_indoor_pct": 15.5}

A = (-37.81001, 144.96280)   # Melbourne Central
B = (-37.81800, 144.96910)   # Federation Square


def cost_fn(W, hour):
    def f(u, v, d):
        sun = 0.0 if (d["indoor"] or d["covered"]) else (DIRECT/1000.0)*(1.0 - d["shade"][hour])
        pen = W*sun + (0.4 if d["arterial"] and d["crossing"] else 0.15 if d["crossing"] else 0.0)
        return d["length"] * (1.0 + pen)
    return f


def summarise(G, path, hour):
    L = expo = ind = con = 0.0
    for u, v in zip(path, path[1:]):
        d = G[u][v]
        L += d["length"]
        expo += d["length"] * (1 - d["shade"][hour])
        if d["indoor"] or d["covered"]: ind += d["length"]
        if d["connector"]: con += d["length"]
    return dict(distance=L, sun=expo, sun_pct=expo/L*100, indoor=ind,
                indoor_pct=ind/L*100, connector_m=con, minutes=L/80.0)


if __name__ == "__main__":
    G = pickle.load(open(f"{OUT}/graph.pkl", "rb"))
    s, t = nearest_walkable(G, *A), nearest_walkable(G, *B)
    h = lambda a, b: math.dist(G.nodes[a]["xy"], G.nodes[b]["xy"])
    p0 = nx.astar_path(G, s, t, heuristic=h, weight=cost_fn(0.0, HOUR))
    p3 = nx.astar_path(G, s, t, heuristic=h, weight=cost_fn(3.0, HOUR))
    r0, r3 = summarise(G, p0, HOUR), summarise(G, p3, HOUR)

    print(f"Melbourne Central -> Federation Square @ {HOUR:02d}:00\n")
    for lbl, r in (("shortest   (W_heat=0)", r0), ("shade-aware (W_heat=3)", r3)):
        print(f"  {lbl}  {r['distance']:6.0f}m | {r['sun']:6.0f}m sun ({r['sun_pct']:4.1f}%) "
              f"| {r['indoor']:6.0f}m indoor ({r['indoor_pct']:4.1f}%) "
              f"| {r['minutes']:4.1f} min | {r['connector_m']:.0f}m on connectors")
    same = p0 == p3
    print(f"\n  identical? {same}   detour {(r3['distance']/r0['distance']-1)*100:+.1f}%   "
          f"sun {(r3['sun']/r0['sun']-1)*100:+.1f}%")
    print(f"  detour cap 1.4x: {'OK' if r3['distance'] <= 1.4*r0['distance'] else 'EXCEEDED'}")
    print("\n  vs Phase 0 baseline:")
    print(f"    shortest indoor%   {BASELINE['indoor_pct']:.1f} -> {r0['indoor_pct']:.1f}")
    print(f"    shaded   indoor%   {BASELINE['shaded_indoor_pct']:.1f} -> {r3['indoor_pct']:.1f}"
          f"   ({'UP' if r3['indoor_pct'] > BASELINE['shaded_indoor_pct'] else 'DOWN'})")
    print(f"    shaded   sun%      {BASELINE['shaded_sun_pct']:.1f} -> {r3['sun_pct']:.1f}")
    print("\n  GATE: " + ("FAIL - routes identical" if same else "PASS"))

    json.dump({"hour": HOUR,
               "shortest": {"coords": [G.nodes[n]["ll"] for n in p0], "summary": r0},
               "shaded":   {"coords": [G.nodes[n]["ll"] for n in p3], "summary": r3}},
              open(f"{OUT}/verify_routes.json", "w"))

    # where does the shaded route pass through named/connector links?
    seen = []
    for u, v in zip(p3, p3[1:]):
        d = G[u][v]
        if d["connector"] and d.get("name") and d["name"] not in seen:
            seen.append(d["name"])
    if seen: print("\n  hand-authored connectors used: " + ", ".join(seen))
