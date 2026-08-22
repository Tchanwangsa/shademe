"""A* over the pedestrian graph, with a 1.4x detour cap."""
import math
import networkx as nx
from .cost import edge_cost, utci_cost, K_DEFAULT

DETOUR_CAP = 1.4
MAX_RELAX = 4          # halvings of w_heat before we give up and use the shortest path


class RouteError(Exception):
    pass


def _h(G, t):
    tx, ty = G.nodes[t]["xy"]
    return lambda n, _t: math.hypot(G.nodes[n]["xy"][0] - tx, G.nodes[n]["xy"][1] - ty)


def _astar(G, s, t, w):
    try:
        return nx.astar_path(G, s, t, heuristic=_h(G, t), weight=w)
    except nx.NodeNotFound:
        raise RouteError("start or destination did not snap to the pedestrian graph")
    except nx.NetworkXNoPath:
        raise RouteError("no walkable path between those points (disconnected graph component)")


def path_length(G, path):
    return sum(float(G[u][v]["length"]) for u, v in zip(path, path[1:]))


def shortest(G, s, t):
    """Pure-distance path. Heuristic is euclidean metres, so admissible."""
    return _astar(G, s, t, lambda u, v, d: float(d["length"]))


def route_utci(G, s, t, K=None):
    """A* under the physical UTCI cost. Relaxes K (not six weights) to honour the cap.

    Requires server.engine.apply() to have stashed `_stress` on the edges first.
    """
    if s == t:
        raise RouteError("start and destination snap to the same graph node")
    sp = shortest(G, s, t)
    base = path_length(G, sp)
    K = K_DEFAULT if K is None else float(K)
    if K <= 0:
        return {"path": sp, "shortest": sp, "K_effective": 0.0, "attempts": 0,
                "capped": False, "ratio": 1.0, "base_m": round(base, 1)}
    best, ratio, i = sp, 1.0, 0
    for i in range(MAX_RELAX + 1):
        p = _astar(G, s, t, lambda u, v, d: utci_cost(d, K))
        ratio = (path_length(G, p) / base) if base > 0 else 1.0
        if ratio <= DETOUR_CAP:
            best = p
            break
        K *= 0.5
        if i == MAX_RELAX:
            best, K, ratio = sp, 0.0, 1.0
    return {"path": best, "shortest": sp, "K_effective": round(K, 4), "attempts": i,
            "capped": i > 0, "ratio": round(ratio, 3), "base_m": round(base, 1)}


def route(G, s, t, hour, weights):
    """weights: {'w_heat','w_wet','direct_radiation'}.

    Returns {'path','shortest','w_heat_effective','w_wet','attempts','capped','ratio',...}.
    Cost >= length always (penalties non-negative) so plain euclidean stays admissible.
    """
    if s == t:
        raise RouteError("start and destination snap to the same graph node")
    sp = shortest(G, s, t)
    base = path_length(G, sp)
    wh = float(weights.get("w_heat", 0.0))
    ww = float(weights.get("w_wet", 0.0))
    direct = float(weights.get("direct_radiation", 0.0))
    if wh <= 0 and ww <= 0:
        return {"path": sp, "shortest": sp, "w_heat_effective": 0.0, "w_wet": ww,
                "attempts": 0, "capped": False, "ratio": 1.0, "base_m": round(base, 1)}

    best, ratio = sp, 1.0
    for i in range(MAX_RELAX + 1):
        p = _astar(G, s, t, lambda u, v, d: edge_cost(d, hour, wh, ww, direct))
        ratio = (path_length(G, p) / base) if base > 0 else 1.0
        if ratio <= DETOUR_CAP:
            best = p
            break
        wh *= 0.5                              # too far: shed heat weight and retry
        ww *= 0.5
        if i == MAX_RELAX:                     # never fit -> fall back to shortest
            best, wh, ww, ratio = sp, 0.0, 0.0, 1.0
    return {"path": best, "shortest": sp, "w_heat_effective": round(wh, 3),
            "w_wet": round(ww, 3), "attempts": i, "capped": i > 0,
            "ratio": round(ratio, 3), "base_m": round(base, 1)}
