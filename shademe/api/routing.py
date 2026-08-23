"""A* over the pedestrian graph, with a 1.4x detour cap."""
import math, heapq, itertools
import networkx as nx
from .cost import (edge_cost, utci_cost, uv_cost, K_DEFAULT, K_UV_DEFAULT,
                   DOOR_PENALTY_M, LEVEL_JUMP_M, door_state)

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


def _astar_state(G, s, t, w, door=0.0, level_jump=0.0, closed=None, key=None):
    """A* over (node, previous-edge state) so per-TRANSITION costs are expressible.

    nx.astar_path's weight function sees one edge at a time and cannot charge for walking
    THROUGH a door -- only for being on one side of it, which cost.py explains we must not
    do. Carrying the previous edge's state in the search key is the standard fix (it is
    how transit routers price a transfer) and costs one extra relaxation per state.

    State is (indoor, level, stepped), or None at the origin: we do not know whether the
    user is standing inside a building, so the FIRST edge is never charged a transition.

    The euclidean heuristic stays ADMISSIBLE on the doubled graph -- every transition cost
    is non-negative and every edge costs at least its plan length.
    """
    if s not in G or t not in G:
        raise RouteError("start or destination did not snap to the pedestrian graph")
    tx, ty = G.nodes[t]["xy"]

    def h(n):
        x, y = G.nodes[n]["xy"]
        return math.hypot(x - tx, y - ty)

    k = key or door_state          # overridable so the keying choice can be BENCHED

    def state(d):
        return (bool(k(d)), int(d.get("level") or 0),
                bool(d.get("rise_m") or 0.0) or bool(d.get("conveying")))

    def transition(prev, cur):
        """Cost of the join between two consecutive edges. See cost.DOOR_PENALTY_M."""
        if prev is None:
            return 0.0
        c = 0.0
        if door and prev[0] != cur[0]:
            c += door
        # A storey change with no staircase on either side is an unrecorded climb.
        # Where a stepped or conveying edge is present, rise_m already carries it.
        if level_jump and prev[1] != cur[1] and not (prev[2] or cur[2]):
            c += level_jump * abs(prev[1] - cur[1])
        return c

    cnt = itertools.count()
    start = (s, None)
    dist = {start: 0.0}
    prev = {}
    pq = [(h(s), 0.0, next(cnt), start)]
    while pq:
        _, c, _, key = heapq.heappop(pq)
        n, st = key
        if c > dist.get(key, float("inf")) + 1e-9:
            continue
        if n == t:
            path = [n]
            while key in prev:
                key = prev[key]
                path.append(key[0])
            return list(reversed(path))
        for m in G[n]:
            d = G[n][m]
            if closed and d.get("hours_key") in closed:
                continue          # shut: an ABSENT edge, never an expensive one
            step = w(d)
            if step is None:
                continue
            cs = state(d)
            nc = c + step + transition(st, cs)
            nk = (m, cs)
            if nc < dist.get(nk, float("inf")) - 1e-12:
                dist[nk] = nc
                prev[nk] = key
                heapq.heappush(pq, (nc + h(m), nc, next(cnt), nk))
    raise RouteError("no walkable path between those points (disconnected graph component)")


def gate(closed):
    """Wrap a weight function so shut edges are REMOVED, not merely made expensive.

    networkx reads a weight of None as "this edge does not exist", which is the honest
    encoding: any finite penalty leaves a large enough K free to route someone into a door
    that will not open. `closed` comes from hours.closed_keys().
    """
    if not closed:
        return lambda f: f
    def wrap(f):
        def w(u, v, d):
            return None if d.get("hours_key") in closed else f(u, v, d)
        return w
    return wrap


def path_length(G, path):
    return sum(float(G[u][v]["length"]) for u, v in zip(path, path[1:]))


def shortest(G, s, t, closed=None):
    """Pure-distance path. Heuristic is euclidean metres, so admissible."""
    return _astar(G, s, t, gate(closed)(lambda u, v, d: float(d["length"])))


def _route_pref(G, s, t, K, weight, closed=None, door=None, level_jump=None):
    """A* under a preference of the form `length * (1 + K * <badness>)`. Relaxes K only.

    Shared by route_utci and route_uv so the two objectives cannot drift apart in the
    parts that are not about the objective -- baseline, cap, relaxation, the K <= 0
    short-circuit and the door/climb accounting are one implementation.

    THE RELAXATION LOOP SHEDS K ONLY. The door penalty and the imputed storey climb are
    costs the walker really pays, not a preference to trade away because the walk turned
    out long; shedding them under pressure would mean the longer the detour, the more
    freely we send someone through a building.

    The cap stays denominated in PLAN length -- it answers "how much further are we
    sending you", and a staircase does not send you further.
    """
    if s == t:
        raise RouteError("start and destination snap to the same graph node")
    sp = shortest(G, s, t, closed)
    base = path_length(G, sp)
    door = DOOR_PENALTY_M if door is None else float(door)
    level_jump = LEVEL_JUMP_M if level_jump is None else float(level_jump)
    K = float(K)
    if K <= 0:
        # "No preference" returns the shortest path itself, and reports door/climb as
        # 0.0 because it was not charged them. Searching at K = 0 instead would return
        # something slightly different AND slightly hotter -- the two penalties with no
        # thermal benefit to set against them cost 1.8% of stress over 40 pairs.
        return {"path": sp, "shortest": sp, "K_effective": 0.0, "attempts": 0,
                "capped": False, "ratio": 1.0, "base_m": round(base, 1),
                "door_m": 0.0, "level_jump_m": 0.0}
    best, ratio, i = sp, 1.0, 0
    for i in range(MAX_RELAX + 1):
        p = _astar_state(G, s, t, lambda d: weight(d, K), door, level_jump, closed)
        ratio = (path_length(G, p) / base) if base > 0 else 1.0
        if ratio <= DETOUR_CAP:
            best = p
            break
        K *= 0.5
        if i == MAX_RELAX:
            best, K, ratio = sp, 0.0, 1.0
            door = level_jump = 0.0        # fell back to shortest: nothing was charged
    return {"path": best, "shortest": sp, "K_effective": round(K, 4), "attempts": i,
            "capped": i > 0, "ratio": round(ratio, 3), "base_m": round(base, 1),
            "door_m": round(door, 1), "level_jump_m": round(level_jump, 1)}


def route_utci(G, s, t, K=None, closed=None, door=None, level_jump=None):
    """A* under the physical UTCI cost. Requires engine.apply() to have stashed `_stress`.

    `closed` gates shut arcades out of BOTH this route and its shortest-path baseline --
    comparing a route that respects opening hours against one that walks through a locked
    building would attribute the difference to the shade model.
    """
    return _route_pref(G, s, t, K_DEFAULT if K is None else K, utci_cost,
                       closed, door, level_jump)


def route_uv(G, s, t, K=None, closed=None, door=None, level_jump=None):
    """A* under the UV cost. Requires engine.apply() to have stashed `_uv_frac`.

    A DIFFERENT OBJECTIVE, not a differently-tuned one. It shares every constraint with
    route_utci so the two are comparable: where they return the same path there is
    nothing to choose, and where they diverge the divergence is real.
    """
    return _route_pref(G, s, t, K_UV_DEFAULT if K is None else K, uv_cost,
                       closed, door, level_jump)


def route(G, s, t, hour, weights, closed=None):
    """weights: {'w_heat','w_wet','direct_radiation'}.

    Returns {'path','shortest','w_heat_effective','w_wet','attempts','capped','ratio',...}.
    Cost >= length always (penalties non-negative) so plain euclidean stays admissible.
    """
    if s == t:
        raise RouteError("start and destination snap to the same graph node")
    g = gate(closed)
    sp = shortest(G, s, t, closed)
    base = path_length(G, sp)
    wh = float(weights.get("w_heat", 0.0))
    ww = float(weights.get("w_wet", 0.0))
    direct = float(weights.get("direct_radiation", 0.0))
    if wh <= 0 and ww <= 0:
        return {"path": sp, "shortest": sp, "w_heat_effective": 0.0, "w_wet": ww,
                "attempts": 0, "capped": False, "ratio": 1.0, "base_m": round(base, 1)}

    best, ratio = sp, 1.0
    for i in range(MAX_RELAX + 1):
        p = _astar(G, s, t, g(lambda u, v, d: edge_cost(d, hour, wh, ww, direct)))
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
