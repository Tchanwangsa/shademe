"""Edge cost + route summarisation. Pure functions, no I/O."""

WALK_MPS = 1.35
NO_STRESS_HI = 26.0      # upper edge of the UTCI no-thermal-stress band; see mrt.stress()
W_CROSS_ARTERIAL = 0.4
W_CROSS_LOCAL = 0.15

# --- v2 physical engine -------------------------------------------------------
# cost = L * (1 + K * stress(UTCI)). K is the ONE free parameter that replaces the six
# hand-tuned constants above. Units: extra metres of walking per metre, per degree of
# thermal stress -- i.e. "how much further would you walk to avoid one degree?".
# At K = 0.10 a metre of walking under 1 degC of stress costs 1.10 m, so a 10% detour
# needs the alternative to be 1.0 degC less stressful and the 40% detour cap needs 4.0.
#
# Measured usable range on this graph: 0.02 and 0.06 give near-identical routes (the
# engine is barely on), indoor routing does not switch on until ~0.12, and the detour
# cap starts binding around 0.25. 0.06 sat at the bottom of that window, which is NOT a
# neutral choice: K and a systematic MRT bias are the same lever (+2 K of MRT bias
# produces the route K=0.12 produces), so a low K silently assumes our MRT runs hot.
# 0.10 is mid-window. It is the one number in the model that is a preference rather than
# a measurement, and the personalisation loop is meant to learn it per user.
K_DEFAULT = float(__import__("os").environ.get("SHADEME_K", "0.10"))


def utci_cost(d, K=None):
    """Physical edge cost. Falls back to plain length if the engine has not run."""
    L = float(d["length"])
    s = d.get("_stress")
    if s is None:
        return L
    K = K_DEFAULT if K is None else K
    return L * (1.0 + K * float(s))


def edge_shade(d, hour):
    """Shade in [0,1] for this edge at `hour`.

    `_shade` is the transient value stashed by server/engine.apply() for the CURRENTLY
    applied hour AND mode. It must win over the pickled `shade` dict, because the graph
    was baked with the summer demo day's shadows -- reporting those on a winter route
    silently mixes summer geometry into winter numbers.
    """
    t = d.get("_shade")
    if t is not None:
        return float(t)
    s = d.get("shade", 0.0)
    if isinstance(s, dict):
        if not s:
            return 0.0
        h = int(hour)
        if h in s:
            return float(s[h])
        if str(h) in s:                      # survives a json round-trip
            return float(s[str(h)])
        k = min(s, key=lambda k: abs(int(k) - h))
        return float(s[k])
    return float(s)


def protected(d):
    return bool(d.get("indoor")) or bool(d.get("covered"))


def edge_cost(d, hour, w_heat, w_wet, direct_radiation):
    L = float(d["length"])
    if protected(d):
        sun_load = exposed = 0.0
    else:
        sun_load = (direct_radiation / 1000.0) * (1.0 - edge_shade(d, hour))
        exposed = 1.0
    if d.get("crossing"):
        w_cross = W_CROSS_ARTERIAL if d.get("arterial") else W_CROSS_LOCAL
    else:
        w_cross = 0.0
    return L * (1.0 + w_heat * sun_load + w_wet * exposed + w_cross)


def _edges(G, path):
    return [G[u][v] for u, v in zip(path, path[1:])]


def summarise(G, path, hour, direct_radiation=0.0):
    dist = sun = indoor = 0.0
    for d in _edges(G, path):
        L = float(d["length"])
        dist += L
        if protected(d):
            indoor += L
        else:
            sun += L * (1.0 - edge_shade(d, hour))
    pct = lambda v: (v / dist * 100.0) if dist > 0 else 0.0
    # heat_load: direct-beam solar dose in kJ/m2 over the walk = sunlit seconds * W/m2 / 1000
    heat = sun / WALK_MPS * (direct_radiation / 1000.0)
    return {"distance_m": round(dist, 1), "sun_m": round(sun, 1), "sun_pct": round(pct(sun), 1),
            "indoor_m": round(indoor, 1), "indoor_pct": round(pct(indoor), 1),
            "minutes": round(dist / WALK_MPS / 60.0, 1), "heat_load": round(heat, 1)}


def thermal_summary(G, path):
    """Thermal exposure over one walk. {} if the physical engine has not run.

    The headline is STRESS x TIME, not mean UTCI. Mean UTCI averages a 22.5 degC
    air-conditioned arcade together with a 40 degC footpath and reports a number that
    describes neither leg; it also cannot reach zero inside the comfort band, so a walk
    with no heat to avoid still reports a difference that looks like a benefit. Degrees
    outside the 9..26 degC no-thermal-stress band, integrated over the seconds you are
    exposed to them, is exactly what the cost function already minimises: it is zero when
    there is nothing to avoid, it is additive along the path, and it works unchanged in
    winter, where the same quantity counts cold.

    Units are degC-minutes. `heat` and `cold` are reported separately -- `_stress` is an
    unsigned distance from the band, so the sign has to come back from `_utci`.
    """
    es = [G[u][v] for u, v in zip(path, path[1:])]
    if not es or all(e.get("_stress") is None for e in es):
        return {}
    heat = cold = exp_m = out_m = out_utci = 0.0
    peak = None
    for d in es:
        s = d.get("_stress")
        if s is None:
            continue
        L = float(d["length"])
        mins = L / WALK_MPS / 60.0
        u = float(d.get("_utci", 0.0))
        if s > 0:
            if u > NO_STRESS_HI:
                heat += s * mins
            else:
                cold += s * mins
            exp_m += L
        if not protected(d):
            out_m += L
            out_utci += u * L
        if peak is None or u > peak:
            peak = u
    return {"heat_stress": round(heat, 1), "cold_stress": round(cold, 1),
            "stress_load": round(heat + cold, 1),
            "exposed_m": round(exp_m, 1),
            "utci_peak": round(peak, 2) if peak is not None else None,
            "utci_mean_outdoor": round(out_utci / out_m, 2) if out_m > 0 else None}


def compare_thermal(chosen, shortest):
    """Headline pitch line: how much stress the chosen route avoids, and what it cost.

    Percentages are only meaningful when there is stress to avoid, so they are None
    inside the comfort band rather than a suspicious 0.0.
    """
    if not chosen or not shortest:
        return {}
    out = {}
    for k in ("stress_load", "heat_stress", "cold_stress"):
        base, got = shortest.get(k, 0.0), chosen.get(k, 0.0)
        out[k + "_avoided"] = round(base - got, 1)
        out[k + "_avoided_pct"] = round((base - got) / base * 100.0, 1) if base > 0.01 else None
    return out


def segments(G, path, hour):
    """Consecutive edges sharing the same indoor|covered state merged into one segment."""
    out = []
    cur = None
    for u, v in zip(path, path[1:]):
        d = G[u][v]
        p = protected(d)
        L = float(d["length"])
        sh = edge_shade(d, hour)
        if cur is None or cur["_p"] != p:
            if cur:
                out.append(cur)
            cur = {"coords": [list(G.nodes[u]["ll"])], "indoor": False, "covered": False,
                   "shade": 0.0, "length": 0.0, "_p": p, "_ws": 0.0}
        cur["coords"].append(list(G.nodes[v]["ll"]))
        cur["indoor"] = cur["indoor"] or bool(d.get("indoor"))
        cur["covered"] = cur["covered"] or bool(d.get("covered"))
        cur["length"] += L
        cur["_ws"] += sh * L
    if cur:
        out.append(cur)
    for s in out:
        s["shade"] = round(s["_ws"] / s["length"], 3) if s["length"] > 0 else 0.0
        s["length"] = round(s["length"], 1)
        s.pop("_p"); s.pop("_ws")
    return out


def geojson(G, path, props=None):
    return {"type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [list(G.nodes[n]["ll"]) for n in path]},
            "properties": props or {}}
