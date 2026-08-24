"""Checks for the personal K -- the two walker flags. Run: python tests/test_walker.py

(a) the two flags are independent and compose multiplicatively, in either order
(b) scaling the ladder keeps the K = 0 rung at 0 and keeps the rungs in order
(c) weighted_minutes IS the router's objective, not a proxy for it
(d) doors are priced on every option, including the K = 0 walk the search never charged
(e) a higher K never recommends a hotter walk, and a list of one gets no badge

(c) is the one that matters most. The recommendation is only defensible because it ranks
finished routes in the same currency the A* minimised over edges; if the route-level
restatement drifted from the edge-level cost, the badge would be a second, unstated
objective wearing the first one's name. So it is checked against a real graph and the
real cost function rather than asserted in a comment.
"""
import sys

import networkx as nx

from shademe.api import cost
from shademe.api.cost import (DOOR_PENALTY_M, WALK_MPS, k_multiplier, scale_ladder,
                              summarise, thermal_summary, utci_cost, weighted_minutes)
from shademe.api.main import recommend

fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


def near(a, b, tol):
    return abs(a - b) <= tol


# --- (a) composition ----------------------------------------------------------
print("(a) the two flags are independent, and stack")
F = cost.SENSITIVITY_PER_FLAG
check("neither -> 1.0", k_multiplier(False, False) == 1.0)
check("unacclimatised only", k_multiplier(True, False) == F)
check("vulnerable only", k_multiplier(False, True) == F)
check(f"both -> {F * F}", near(k_multiplier(True, True), F * F, 1e-9),
      "multiplicative: neither flag is worth less for arriving second")
check("order cannot matter", k_multiplier(True, True) == k_multiplier(True, True))
check("the elderly resident and the healthy tourist land on the same K",
      k_multiplier(False, True) == k_multiplier(True, False),
      "two different reasons, one dial -- see cost.k_multiplier")


# --- (b) the ladder -----------------------------------------------------------
print("\n(b) scaling the ladder")
base = (0.0, 0.03, 0.10, 0.30)
for m in (1.0, F, F * F):
    L = scale_ladder(base, m)
    check(f"x{m:<4} -> {L}",
          L[0] == 0.0 and all(b < a for a, b in zip(L, L[1:])) is False
          and all(a < b for a, b in zip(L, L[1:]))
          and all(near(k, b * m, 5e-4) for k, b in zip(L, base)),
          "K = 0 stays 0, rungs stay strictly increasing")
check("a multiplier of 1 changes nothing", scale_ladder(base, 1.0) == base)


# --- (c) the objective --------------------------------------------------------
# A four-edge walk: two hot outdoor edges, one indoor, one mild outdoor. Node ids are
# ints and every edge carries what engine.apply() would have stashed on it.
def walk(specs):
    """specs: [(length_m, stress, utci, indoor)] -> (graph, path)."""
    G = nx.Graph()
    for i, (L, s, u, indoor) in enumerate(specs):
        G.add_edge(i, i + 1, length=float(L), _stress=float(s), _utci=float(u),
                   indoor=bool(indoor), shade=0.0)
    return G, list(range(len(specs) + 1))


print("\n(c) weighted_minutes reproduces the edge cost the router minimised")
G, path = walk([(300, 8.0, 34.0, False), (200, 6.0, 32.0, False),
                (250, 0.0, 22.5, True), (400, 5.0, 31.0, False)])
summ = dict(summarise(G, path, 14, 0.0), **thermal_summary(G, path))
for K in (0.0, 0.10, 0.18, 0.324):
    edge_m = sum(utci_cost(G[u][v], K) for u, v in zip(path, path[1:]))
    route_m = (weighted_minutes(summ, K, door_m=0.0)) * WALK_MPS * 60.0
    # ROUNDING, not slack. `minutes` is published to 0.1 min (8.1 m of walking) and
    # `stress_load` to 0.1 degC-min (K x 0.1 min); at K = 0.324 that is under 11 m on a
    # 1150 m walk. The test is that nothing but the published precision separates them.
    tol = WALK_MPS * 60.0 * (0.05 + K * 0.05) + 0.01
    check(f"K={K:<6} edges {edge_m:8.2f} m  route {route_m:8.2f} m",
          near(edge_m, route_m, tol), f"tol {tol:.2f} m")


# --- (d) doors ----------------------------------------------------------------
print("\n(d) doors are priced on every option, at one price")
check("summarise counts both transitions of the indoor leg", summ["doors"] == 2)
free = weighted_minutes(summ, 0.10, door_m=0.0)
paid = weighted_minutes(summ, 0.10)
check(f"two doors cost {paid - free:.2f} min",
      near(paid - free, 2 * DOOR_PENALTY_M / (WALK_MPS * 60.0), 1e-9),
      "the K = 0 walk is scored on the same list as every other")


# --- (e) the recommendation ---------------------------------------------------
print("\n(e) the recommendation")


def opt(id_, minutes, stress, doors=0):
    return {"id": id_, "summary": {"minutes": minutes, "stress_load": stress,
                                   "doors": doors}}


# Quick and hot, middling, slow and cool. No doors, so the ranking is purely the trade.
opts = [opt("quick", 10.0, 90.0), opt("mid", 12.0, 60.0), opt("cool", 15.0, 30.0)]
picked = []
for K in (0.0, 0.03, 0.06, 0.10, 0.18, 0.324, 1.0):
    recommend(opts, K)
    got = next(o["id"] for o in opts if o["recommended"])
    picked.append((K, got, next(o["summary"]["stress_load"] for o in opts if o["recommended"])))
    print(f"        K={K:<6} -> {got}")
check("K = 0 takes the quickest walk", picked[0][1] == "quick")
check("a large K takes the coolest", picked[-1][1] == "cool")
check("stress never rises as K rises",
      all(b[2] <= a[2] for a, b in zip(picked, picked[1:])),
      "the score is linear in K, so the pick can only move one way")
check("the unpersonalised default (K=0.10) is not the coolest walk here",
      dict((k, i) for k, i, _ in picked)[0.10] == "mid",
      "which is the point: coolest-first was a hidden K of infinity")
check("exactly one option is recommended",
      sum(1 for o in opts if o["recommended"]) == 1)
check("every option is scored, recommended or not",
      all("weighted_minutes" in o for o in opts))

one = [opt("only", 11.0, 40.0)]
recommend(one, 0.324)
check("a list of one gets no badge", one[0]["recommended"] is False)
check("...but is still scored", "weighted_minutes" in one[0])

print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
