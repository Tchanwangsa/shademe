"""Redo CHECK 2 properly: my first test snapped to the nearest ANY node, not indoor."""
import os, sys, json, math, numpy as np
sys.path.insert(0, os.path.dirname(__file__))
from build_graph import build
import networkx as nx
OUT = os.path.join(os.path.dirname(__file__), "..", "out")
PLACES = {"Melbourne Central": (-37.81001,144.96280), "Emporium": (-37.81180,144.96330),
          "Myer": (-37.81350,144.96450), "Fed Square": (-37.81800,144.96910)}

G = build()
Gi = nx.Graph()
Gi.add_edges_from((u,v) for u,v,d in G.edges(data=True) if d["indoor"] or d["covered"])
comps = sorted(nx.connected_components(Gi), key=len, reverse=True)
cid = {n:i for i,c in enumerate(comps) for n in c}

from pyproj import Transformer
from config import WGS84, MGA55
tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)
print(f"\nindoor subgraph: {Gi.number_of_edges()} edges / {len(comps)} components")
print(f"top component sizes: {[len(c) for c in comps[:6]]}\n")
for name,(lat,lon) in PLACES.items():
    x,y = tf.transform(lon,lat)
    best = min(Gi.nodes, key=lambda n:(G.nodes[n]['xy'][0]-x)**2+(G.nodes[n]['xy'][1]-y)**2)
    d = math.dist(G.nodes[best]['xy'], (x,y))
    print(f"  {name:19} nearest INDOOR node {d:5.0f}m away, component #{cid[best]} "
          f"(size {len(comps[cid[best]])})")

# how much of the indoor net actually touches the street graph?
touch = sum(1 for n in Gi.nodes if any(not (G[n][m]['indoor'] or G[n][m]['covered'])
                                        for m in G.neighbors(n)))
print(f"\n  {touch}/{Gi.number_of_nodes()} indoor nodes connect to the outdoor network")
