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
# RE-SWEPT with the door penalty and the climb priced (DOOR_PENALTY_M = 45, keyed on
# `indoor`; RISE_M_PER_M = 3.0; LEVEL_JUMP_M = 12). That order matters: until those two
# existed, K was the only thing that could hold the router out of the arcades, so a K
# sweep was measuring arcade reachability. This one is about shade.
# 40 CBD pairs, summer 14:00 2026-01-26, shortest-path baseline 114.2 degC-min:
#
#     K       0.00  0.02  0.04  0.06  0.08  0.10  0.15  0.20  0.25  0.30  0.40  0.60
#     ind%     0.8   0.8   1.0   1.1   1.9   2.0   2.5   3.9   9.8  12.3  14.9  20.0
#     ratio   1.005 1.013 1.021 1.025 1.032 1.037 1.040 1.045 1.062 1.070 1.082 1.114
#     avoided -1.8% 20.0% 26.5% 28.4% 30.2% 31.6% 32.4% 33.5% 36.8% 38.2% 39.7% 42.1%
#
# READ THREE THINGS OFF IT.
#
# K = 0 avoids -1.8%: with no thermal preference the router still pays for doors and
# stairs, and minimising those alone costs 1.8% MORE stress than the plain shortest path.
# That is the price of the two new terms with no heat benefit to set against them, and it
# is the honest bottom of this table -- not a bug.
#
# There are two regimes, and they buy the benefit differently. Below ~0.2 the router buys
# shade outdoors: indoor share stays at 2%, and 0.06 -> 0.10 buys 3.2 pp of avoided
# stress for 1.2 pp of detour. From ~0.22 up it starts buying benefit by paying the door
# instead -- indoor share goes 3.9% -> 9.8% between 0.20 and 0.25, and most of what looks
# like a better shade result after that is arcade. Having just decided (DOOR_PENALTY_M)
# that indoor is not what we want to win on, K belongs in the first regime.
#
# 0.10 is the knee of that first regime. 0.06 -> 0.10 buys 3.2 pp; 0.10 -> 0.20 buys 1.9
# pp and opens the door. The detour cap still never binds: 1.114 at K = 0.6, against a
# 1.4 cap. Note the door does NOT reintroduce a threshold -- at 0.01 resolution the
# indoor share ramps (5.0% at 0.22, 8.2% at 0.24, 9.8% at 0.25) rather than jumping.
#
# The previous sweep in this comment was measured before any of that and is superseded
# wholesale, including its "no threshold at all" claim, which was true of a router that
# could not be charged for a door. K remains a preference rather than a measurement: K
# and a systematic MRT bias are the same lever, so a low K silently assumes our MRT runs
# hot. The personalisation loop is meant to learn it per user.
K_DEFAULT = float(__import__("os").environ.get("SHADEME_K", "0.10"))


# --- what indoor is worth: the door penalty -----------------------------------
# `cost = L * (1 + K*stress)` gives an indoor edge stress = 0, so it costs exactly its
# length -- the FLOOR of the cost function. No outdoor edge, however deeply shaded, can
# match it, and nothing already in the model restrains that:
#
#   * INDOOR_TA is not a lever. 22.5 C and 26.0 C produce byte-identical routes over 40
#     pairs, because both sit inside the 9..26 no-thermal-stress band. Only pushing
#     indoor air ABOVE 26 changes a route, which is not a claim anyone wants to make.
#   * DETOUR_CAP cannot bind. Indoor routes average ratio 1.04 -- an arcade is not a
#     detour, it is the same corridor with a roof on it. A distance cap never fires.
#
# So the preference is priced where it is actually incurred: per TRANSITION, in metres.
# Same shape as a transit transfer penalty, and it is the only shape that scales right --
# a long arcade leg amortises the door, a 40 m opportunistic duck through a foyer does
# not. It needs a state-augmented search to express (routing._astar_state).
#
# THE POSITION IT ENCODES. An arcade is worth using as a LEG of the walk, not as a
# 40-metre dodge. Three reasons to be sparing rather than generous: our indoor physics is
# an assertion (a fixed INDOOR_TA and stress = 0), not a measurement, so every indoor
# metre is a metre the engine did not have to model; opening hours are editorial
# estimates (data/indoor_hours.json ships verified: false on every class); and the pitch
# is a weather-aware SHADE router -- the council's Cool Routes already sends people
# through the arcades, with no weather at all. A route that wins by going indoors is
# winning on someone else's ground.
#
# MEASURED -- scripts/bench_indoor.py, 40 CBD pairs, summer 14:00 2026-01-26, K = 0.10,
# vertical rise priced, keyed on `indoor`, shortest-path baseline 114.2 degC-min.
# `dodge` and `leg` are indoor stretches per route under 50 m and over 150 m: a share
# cannot tell a duck through a foyer from an arcade walked end to end, and the whole
# decision is about which of those we are buying.
#
#     door m     0     10     15     20     25     30     45     60     90    inf
#     ind%    10.6    8.3    5.7    3.4    2.5    2.0    2.0    1.9    0.8    0.7
#     doors    3.1    2.4    1.4    0.9    0.7    0.6    0.5    0.5    0.3    0.3
#     dodge   0.38   0.17   0.15   0.15   0.15   0.15   0.12   0.12   0.12   0.10
#     leg     0.38   0.28   0.20   0.12   0.07   0.03   0.03   0.03   0.00   0.00
#     avoided 35.9%  35.0%  33.7%  32.7%  32.0%  31.6%  31.6%  31.3%  30.5%  30.2%
#
# THE TABLE DOES NOT SAY WHAT WE EXPECTED IT TO SAY, so read it carefully. The penalty
# does NOT cleanly separate ducking from arcade legs. Ducking is gone by 15 m -- 0.38
# dodges per route down to 0.15, which is the FLOOR (those five residual stretches are
# routes whose endpoint snapped inside a building; the origin is never charged a
# transition, so no penalty can remove them). Everything above 15 m removes LEGS: 0.20
# per route at 15, 0.03 by 30, none at all by 90. There is no value that keeps the spine
# and drops the dodges, because past 15 m the spine is what is being paid for.
#
# So 45 m is not "ducking removed". It is indoor routing close to OFF: 2.0% against a
# 0.7% floor, and it costs 4.3 pp of avoided stress against a free door. On the demo pair
# (Melbourne Central -> Federation Square) it is stark -- 56.2% indoor and 67.0% avoided
# with the door free, 0.0% and 4.7% at 45 m, because chaining five genuine arcade legs
# (95, 353, 206, 43, 94 m) costs ten transitions and 450 m of penalty against ~390 m of
# thermal benefit. That is the number this decision is really about, and quoting the
# 40-pair mean without it would hide the thing a demo will show on screen.
#
# WHY 45 ANYWAY -- the position, stated so it can be disagreed with. 45 m is two costs in
# one number, and only one of them is a door:
#
#   ~15 m is what a door actually costs. Push through, escalator, wayfinding, crowd; 15 m
#   is 11 s at 1.35 m/s. That is the honest per-transition time, and it is exactly where
#   the dodge column bottoms out.
#
#   ~30 m is DISTRUST OF OUR OWN INDOOR PHYSICS. Indoor edges get stress = 0 by fiat from
#   a fixed INDOOR_TA. Every indoor metre is a metre the engine asserted instead of
#   modelling, so the benefit the router is buying with those doors is partly fictional --
#   and the correct response to an inflated benefit is to discount it. The right SHAPE for
#   that discount is per-metre, not per-transition, but INDOOR_TA cannot express it (22.5
#   and 26.0 give byte-identical routes; both are inside the no-stress band), so the door
#   is the only lever there is and it carries both jobs.
#
# WHEN THE INDOOR MODEL STOPS BEING A STUB, DROP THIS TO 15 and put the discount where it
# belongs. That is why both halves are written down rather than just the sum.
#
# The failure modes are not symmetric, which is what settles it. Too high and the user
# walks a hotter footpath than they had to -- about 4 degC-min out of 114, graceful and
# bounded. Too low and we send someone through doors on an asserted benefit, into a
# building whose opening hours we have NOT verified (data/indoor_hours.json ships
# verified: false on every class), where a wrong turn is unrecoverable in a way a hot
# footpath is not. For a heat-safety tool aimed at people who are already vulnerable, the
# bounded failure is the one to take.
#
# 45 is mid-plateau, with a caveat. Routes are piecewise constant in this parameter; at
# 1 m resolution every value in 40..49 returns the SAME 40 routes and 39 and 50 do not,
# so 45 is the centre of a measured step rather than a round number off a grid. The
# caveat: below ~25 m the routes change almost every metre, so the plateaus up there are
# partly stability by exhaustion -- there is little indoor left to reshuffle. A value in
# the teens would be balanced on a slope, which is a second, weaker reason to prefer this
# end. bench_indoor.py marks route-identical rows `=`.
DOOR_PENALTY_M = 45.0

# KEYED ON `indoor`, NOT `protected()`. A covered footpath under an awning has no door;
# charging a transition onto it is simply wrong, and it measurably suppresses exactly the
# edges we most want used. Same 40 pairs, same weather, door = 45 m:
#
#                      covered in route   avoided
#     keyed `indoor`         1.5%          31.6%
#     keyed protected()      0.4%          30.3%
#
# Awnings are free shade with no door to open, and the protected() arm walks past them.
def door_state(d):
    """The state the door penalty is charged on crossing. See DOOR_PENALTY_M."""
    return bool(d.get("indoor"))


# --- vertical travel ----------------------------------------------------------
# Naismith's rule: 600 m on the flat per 100 m of ascent, i.e. 6 equivalent metres per
# metre climbed. It is a TIME rule, which is why the climb is converted to equivalent
# length and then multiplied by the thermal term rather than added after it -- stairs
# are more minutes of walking, and stress x time is the quantity being minimised. A
# sunlit staircase should cost more than a shaded one for the same reason a sunlit
# footpath does.
#
# The graph is undirected, so an edge cannot know whether you climb or descend it.
# Naismith charges ascent only; the usual compromise for a direction-blind graph is to
# charge half in each direction, which is exact over a round trip and splits the error
# on a one-way leg. Both halves of that are named here so neither can be quietly tuned.
NAISMITH_M_PER_M = 6.0
DIRECTION_F = 0.5
RISE_M_PER_M = NAISMITH_M_PER_M * DIRECTION_F     # 3.0 equivalent metres per metre risen

# MEASURED, with the door free so the climb is isolated (40 pairs, 14:00, K = 0.10):
#
#     m per m risen    0.0    1.5    3.0    6.0   12.0
#     climb m/route    5.4    2.1    1.2    1.0    0.5
#     indoor %        13.7   12.6   12.0   11.8   11.7
#     avoided        37.5%  36.9%  36.9%  36.8%  36.5%
#
# Pricing the climb at all is what matters; the exact ratio barely does. Going from free
# to Naismith removes 4.2 m of unpaid climb per route and 1.7 pp of indoor share, and
# quadrupling the ratio after that changes the result by 0.4 pp. That is the argument for
# taking the standard number rather than tuning one: the sweep cannot tell 3 from 12, so
# a "fitted" value here would be fitting noise, and 3 is what Naismith says with the
# direction convention stated above.

# Escalators and travelators carry you: the climb is free, the plan length (17 m mean --
# the run is diagonal) is already charged as ordinary walking. The distinction is real
# and it is already on the edge: OSM tags an escalator `highway=steps` + `conveying`, so
# `conveying and rise_m > 0` IS the escalator set (175 edges, 724 m of rise) and stairs
# are the rest (828 edges, 2141 m). Lifts arguably deserve a wait penalty instead of a
# rise cost, but `highway=elevator` gives rise_m = 0 and there are 2 such edges in this
# extract, so there is nothing here to measure and none is charged.
def rise_cost(d, per_m=None):
    """Equivalent metres of flat walking for the climb on this edge. 0.0 if none.

    `per_m` overrides RISE_M_PER_M so a sweep measures THIS function rather than a copy
    of it -- the last set of stale figures in this file came from a second implementation.
    """
    r = float(d.get("rise_m") or 0.0)
    if r <= 0.0 or d.get("conveying"):
        return 0.0
    return (RISE_M_PER_M if per_m is None else float(per_m)) * r


def equiv_length(d, per_m=None):
    """Plan length + the climb expressed as flat metres. What the walk actually costs.

    Used for cost AND for the exposure minutes in thermal_summary: time on a staircase
    is time in the sun. Plain `length` is kept for anything that means DISTANCE -- the
    detour ratio and the cap are about how much further you are being sent, and a flight
    of stairs does not move you further.
    """
    return float(d["length"]) + rise_cost(d, per_m)


# A level change with no stepped or conveying edge at the junction is a climb the graph
# does not record: 224 of the 490 nodes that join two storeys have no staircase incident,
# 109 of them next to one of the 1060 AUTO-SNAPPED connectors (build_graph.autosnap joins
# indoor-component endpoints within 15 m; only 6 of the 1066 connectors are hand-authored,
# and just 4 of these junctions touch one of those). Charging zero
# there is free teleportation between storeys, and it flatters exactly the indoor routes
# this file is trying to price honestly. STOREY_M mirrors build_graph.STOREY_M.
STOREY_M = 4.0
LEVEL_JUMP_M = STOREY_M * RISE_M_PER_M            # 12 equivalent metres per storey
#
# MEASURED (same run, door free, rise priced at 3.0):
#
#     level m       0.0    6.0   12.0   24.0
#     jumps/route  1.20   0.15   0.07   0.07     <- free storey changes actually taken
#     indoor %     12.0   10.9   10.6   10.6
#     avoided     36.9%  36.0%  35.9%  35.9%
#
# The router was taking 1.2 unrecorded storey changes per route because they were free.
# 12 and 24 give identical routes, so 12 is the start of a plateau rather than a value
# balanced on a slope -- and it is not a free parameter anyway: it is STOREY_M times the
# rise price, both of which are already fixed. It is charged only where NEITHER edge at
# the junction is stepped or conveying (routing._astar_state.transition), so it never
# double-charges a climb that rise_m already carries.
#
# It is still an IMPUTATION. Some of those junctions are real unmapped stairs, some are
# auto-snapped connectors that should not exist at all, and this cannot tell them apart.
# The defensible claim is only that one storey of climb is closer to the truth than zero.


def utci_cost(d, K=None, rise=None):
    """Physical edge cost. Falls back to plain length if the engine has not run.

    The per-transition costs (a door, an unrecorded storey) are NOT here -- an edge
    cannot see the edge before it. routing._astar_state adds them.
    """
    L = equiv_length(d, rise)
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
    dist = sun = indoor = climb = equiv = 0.0
    es = _edges(G, path)
    for d in es:
        L = float(d["length"])
        dist += L
        equiv += equiv_length(d)
        if not d.get("conveying"):
            climb += float(d.get("rise_m") or 0.0)
        if protected(d):
            indoor += L
        else:
            sun += L * (1.0 - edge_shade(d, hour))
    doors = sum(1 for a, b in zip(es, es[1:]) if door_state(a) != door_state(b))
    pct = lambda v: (v / dist * 100.0) if dist > 0 else 0.0
    # heat_load: direct-beam solar dose in kJ/m2 over the walk = sunlit seconds * W/m2 / 1000
    heat = sun / WALK_MPS * (direct_radiation / 1000.0)
    # `minutes` walks the EQUIVALENT length: stairs cost time (Naismith), and the walk
    # duration the user is shown should be the one the router was minimising.
    return {"distance_m": round(dist, 1), "sun_m": round(sun, 1), "sun_pct": round(pct(sun), 1),
            "indoor_m": round(indoor, 1), "indoor_pct": round(pct(indoor), 1),
            "minutes": round(equiv / WALK_MPS / 60.0, 1), "heat_load": round(heat, 1),
            "climb_m": round(climb, 1), "doors": doors}


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
        mins = equiv_length(d) / WALK_MPS / 60.0    # a staircase is minutes, not metres
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

    THE UI LEADS WITH `stress_load_avoided`, NOT `stress_load_avoided_pct`. The two do
    not peak at the same hour, and the percentage peaks in the wrong place. Measured over
    40 CBD pairs in one process (scripts/bench_hours.py, summer, K=0.10, door 45 m):

        hour   baseline degC-min   avoided degC-min   avoided pct
          10                25.6               14.9         58.2
          13                91.1               38.7         42.5
          17               160.4               29.7         18.5

    17:00 is when the city is hottest and it is when the percentage reads WORST -- twice
    the dose avoided as at 10:00, a third of the percentage. The denominator is moving:
    late sun is low, the whole city is in long shadow, and the shortest path is already
    shaded, so proportionally less is left to win precisely when the walk is worst. That
    is a property of the ratio, not of the engine, and a demo driven off the percentage
    undersells itself at exactly the hour that matters. The degC-minute figure is a dose:
    additive, zero when there is nothing to avoid, and the quantity minimised here.
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
