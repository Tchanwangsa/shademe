"""Edge cost + route summarisation. Pure functions, no I/O."""
from .. import timegrid as TG      # pure constants and arithmetic; keeps the no-I/O rule

WALK_MPS = 1.35
NO_STRESS_HI = 26.0      # upper edge of the UTCI no-thermal-stress band; see mrt.stress()
W_CROSS_ARTERIAL = 0.4
W_CROSS_LOCAL = 0.15

# --- the thermal objective ----------------------------------------------------
# cost = L * (1 + K * stress(UTCI)). K is the ONE free parameter of the model, in extra
# metres walked per metre per degC of stress: "how much further to avoid one degree?".
#
# 0.10 is the knee of the outdoor-shade regime. Swept over 40 CBD pairs with the door and
# climb priced: below ~0.2 the router buys shade OUTDOORS (indoor share flat at 2%), and
# from ~0.22 up it buys benefit by paying the door instead. The detour cap never binds.
# A preference, not a measurement -- K and a systematic MRT bias are the same lever, so a
# low K silently assumes our MRT runs hot. Re-sweep: tools/bench_indoor.py --sweep K
K_DEFAULT = float(__import__("os").environ.get("SHADEME_K", "0.10"))


# --- what indoor is worth: the door penalty -----------------------------------
# An indoor edge has stress = 0, so it costs exactly its length -- the floor of the cost
# function, which no outdoor edge can match. Nothing else restrains that: INDOOR_TA is not
# a lever (22.5 and 26.0 give byte-identical routes, both inside the no-stress band) and
# DETOUR_CAP cannot bind (an arcade is not a detour, it is the same corridor with a roof
# on it). So it is priced per TRANSITION, in metres -- the only shape that scales right,
# since a long arcade leg amortises the door and a 40 m duck through a foyer does not.
# Expressing it needs the state-augmented search in routing._astar_state.
#
# 45 m is two costs in one number. ~15 m is what a door really costs (push through,
# escalator, wayfinding, crowd -- 11 s at 1.35 m/s, and where the measured dodge count
# bottoms out). ~30 m is a deliberate discount on indoor physics we ASSERT rather than
# model, because the right response to an inflated benefit is to discount it and
# INDOOR_TA cannot express a per-metre discount. WHEN THE INDOOR MODEL STOPS BEING A
# STUB, DROP THIS TO 15.
#
# The measured sweep does NOT cleanly separate ducking from arcade legs -- ducking is
# gone by 15 m and everything above that removes LEGS -- so 45 m is indoor routing close
# to off, costing 4.3 pp of avoided stress against a free door. The failure modes decide
# it: too high is a hotter footpath (bounded, ~4 degC-min of 114), too low sends someone
# through doors on an asserted benefit into a building whose hours we have not verified.
DOOR_PENALTY_M = 45.0

# KEYED ON `indoor`, NOT `protected()`. An awning has no door, and charging a transition
# onto it suppresses exactly the edges we most want used: 1.5% covered / 31.6% avoided
# keyed `indoor`, against 0.4% / 30.3% keyed protected().
def door_state(d):
    """The state the door penalty is charged on crossing. See DOOR_PENALTY_M."""
    return bool(d.get("indoor"))


# --- vertical travel ----------------------------------------------------------
# Naismith: 600 m flat per 100 m of ascent. It is a TIME rule, hence equivalent length
# multiplied by the thermal term rather than added after it -- a sunlit staircase should
# cost more than a shaded one. The graph is undirected, so charge half in each direction:
# exact over a round trip, and it splits the error on a one-way leg. Both halves are named
# so neither can be quietly tuned. Measured: pricing the climb at all is what matters
# (free -> Naismith removes 4.2 m of unpaid climb per route); the exact ratio does not.
NAISMITH_M_PER_M = 6.0
DIRECTION_F = 0.5
RISE_M_PER_M = NAISMITH_M_PER_M * DIRECTION_F     # 3.0 equivalent metres per metre risen

# Escalators carry you: the climb is free, and the plan length is already charged as
# ordinary walking. OSM tags them `highway=steps` + `conveying`, so `conveying and
# rise_m > 0` IS the escalator set (175 edges) and stairs are the rest (828).
def rise_cost(d, per_m=None):
    """Equivalent metres of flat walking for the climb on this edge. 0.0 if none.

    `per_m` overrides RISE_M_PER_M so a sweep measures THIS function rather than a copy.
    """
    r = float(d.get("rise_m") or 0.0)
    if r <= 0.0 or d.get("conveying"):
        return 0.0
    return (RISE_M_PER_M if per_m is None else float(per_m)) * r


def equiv_length(d, per_m=None):
    """Plan length + the climb as flat metres. What the walk actually costs.

    Used for cost AND for the exposure minutes in thermal_summary: time on a staircase is
    time in the sun. Plain `length` stays for anything meaning DISTANCE -- the detour
    ratio and cap are about how much further you are sent, and stairs do not move you.
    """
    return float(d["length"]) + rise_cost(d, per_m)


# A level change with no stepped or conveying edge at the junction is a climb the graph
# never recorded -- 224 of the 490 nodes joining two storeys have no staircase incident --
# and charging zero there is free teleportation between storeys, which flatters exactly
# the indoor routes this file is trying to price honestly.
STOREY_M = 4.0
LEVEL_JUMP_M = STOREY_M * RISE_M_PER_M            # 12 equivalent metres per storey
# Measured at 1.20 free storey changes per route before this existed. Not a free
# parameter: STOREY_M times the rise price, both already fixed. Charged only where NEITHER
# edge at the junction is stepped or conveying, so it cannot double-charge rise_m.
#
# Still an IMPUTATION -- some of those junctions are real unmapped stairs, some are
# auto-snapped connectors that should not exist, and this cannot tell them apart. The
# defensible claim is only that one storey of climb is closer to the truth than zero.


def utci_cost(d, K=None, rise=None):
    """Physical edge cost. Falls back to plain length if the engine has not run.

    Per-transition costs (a door, an unrecorded storey) are NOT here -- an edge cannot
    see the edge before it. routing._astar_state adds them.
    """
    L = equiv_length(d, rise)
    s = d.get("_stress")
    if s is None:
        return L
    K = K_DEFAULT if K is None else K
    return L * (1.0 + K * float(s))


# --- whose cost is it: the personal K -----------------------------------------
# K is a PREFERENCE, and a preference belongs to a person. The same UTCI is not the same
# experience for everyone standing in it, and two of the reasons are INDEPENDENT:
#
#   ACCLIMATISATION is recent history. One to two weeks of repeated exposure earns a
#   lower heart rate, earlier and more dilute sweating and a steadier blood pressure in
#   heat. Someone who has not built that up -- a visitor, a recent arrival, or anyone at
#   all in the first hot week of a season, which catches a whole city unadapted at once
#   -- is working harder for the same reading on the same footpath.
#
#   VULNERABILITY is capacity, not history. Age, pregnancy, and cardiac or renal
#   conditions reduce the ability to shed heat at all. Acclimatising raises that person's
#   own baseline; it does not lift the ceiling, so it does not substitute for this.
#
# They compose rather than override, which is why there are two questions and not one
# severity slider: a long-time resident can be vulnerable, a healthy visitor can be
# unacclimatised, and an elderly visitor in the first week of a heatwave is both.
#
# WHAT THIS IS NOT. Not a clinical risk score, and deliberately not per-condition: one
# flag covers three quite different physiologies, which is exactly as fine-grained as the
# evidence behind the number here. The DIRECTION is well established. The MAGNITUDE is
# asserted -- 1.8x per flag, from "a walk worth 6% further per degree of stress to an
# adapted adult is worth about 11% to someone who is not" -- and it is a stated
# preference with nothing more behind it than K_DEFAULT itself has. Said plainly, in the
# same words that file uses about K: a preference, not a measurement.
#
# WHAT MAKES AN UNCALIBRATED NUMBER SAFE TO SHIP is that it cannot run away. K buys one
# thing, detour; routing.DETOUR_CAP bounds the detour at 1.4x the direct walk whatever K
# asks for, and _route_pref halves K until it fits. So the worst case of an overestimate
# is a walk 40% longer than the shortest one, reported on the card as `detour_capped`
# with the `K_effective` it actually landed on. There is no setting of these two flags
# that sends anyone anywhere the unpersonalised app would refuse to send them.
#
# NOT APPLIED TO K_UV. Heat acclimatisation and cardiac capacity have nothing to do with
# how much erythemal UV a person should collect -- that scales with skin type, which this
# does not ask about. Scaling both ladders from one answer would be borrowing the
# authority of a heat question to move a UV route.
SENSITIVITY_PER_FLAG = 1.8


def k_multiplier(unacclimatised=False, vulnerable=False):
    """How much more one degree of stress is worth to this walker. 1.0 by default.

    MULTIPLICATIVE, because the two conditions are independent: both flags land on 3.24x
    whichever order they are applied in. Additive composition would make the second flag
    worth less than the first, which is a claim about an interaction between them that
    nothing here can support.
    """
    m = 1.0
    if unacclimatised:
        m *= SENSITIVITY_PER_FLAG
    if vulnerable:
        m *= SENSITIVITY_PER_FLAG
    return round(m, 4)


def scale_ladder(ladder, mult):
    """The rungs of K this walker's preferences actually span.

    SCALED, not shifted or replaced, so K = 0 stays 0 under every multiplier. Keeping the
    no-preference rung is deliberate: the shortest walk is the baseline every "less heat"
    figure on every card is measured against, and withholding it from a vulnerable user
    would remove the comparison rather than the risk.

    Rungs pushed past the detour cap do NOT produce longer detours -- _route_pref halves
    them back under it -- so they return a walk a lower rung already found and collapse in
    /routes' de-duplication. A ladder that ends in one card is the honest report that
    there is nothing further to buy: past a point the limit is the cap, not the person.
    """
    return tuple(round(k * float(mult), 4) for k in ladder)


def weighted_minutes(summary, K, door_m=None):
    """The router's own objective, restated per ROUTE in minutes, under this walker's K.

    The edge cost sums along a path to `equivalent metres + K * sum(L * s)`, and
    thermal_summary's `stress_load` is `sum(s * L) / (WALK_MPS * 60)`, so dividing the
    whole thing through by WALK_MPS * 60 leaves a quantity in minutes:

        weighted minutes = minutes + K * stress_load + doors * door_m / (WALK_MPS * 60)

    NOT A NEW OBJECTIVE -- the same one the A* minimised, evaluated on a finished route so
    that walks produced by DIFFERENT searches can be ranked against each other on one
    scale. That is the whole reason it exists: /routes walks a ladder, and a ladder
    returns options, not a recommendation. Someone still has to say which one is for you,
    and the honest way to say it is with the parameter the model already has.

    DOORS ARE PRICED HERE FOR EVERY OPTION, including the K = 0 walk that was not charged
    them during its search (see routing._route_pref). Ranking options means one price
    list; leaving the baseline's doors free would recommend it for being cheaper to
    search, not better to walk.

    THERMAL ONLY. UV dose is not folded in, at any weight. cost.uv_cost is a second
    preference rather than a re-weighting of the first, and combining them needs an
    exchange rate between a degC-minute and a UV index-minute that nothing in this
    project measures. The UV options stay on the list carrying their own badge; this
    ranks on heat and says so.

    The imputed storey climb (LEVEL_JUMP_M) is left out -- it is charged per transition
    inside the search and never reaches the summary. It is small beside the door price
    and falls on the same indoor walks, so it moves the scores together.
    """
    d = DOOR_PENALTY_M if door_m is None else float(door_m)
    doors = float(summary.get("doors", 0) or 0)
    return (float(summary["minutes"])
            + float(K) * float(summary.get("stress_load", 0.0) or 0.0)
            + doors * d / (WALK_MPS * 60.0))


# --- the UV objective ---------------------------------------------------------
# A SECOND preference, not a re-weighting of the first. `_uv_frac` (engine.solve) is the
# share of the open-sky UV index reaching an edge: 1 in open sun, 0 under a roof, ~0.5 in
# a building's shadow on an open plaza, because roughly half of erythemal UV is skylight.
#
#     cost = equivalent length * (1 + K_uv * uv_frac)
#
# Normalising to a fraction makes K_uv readable as "how much further to swap full sun for
# full cover". Costing the raw index would make the knob mean something different on a UV
# 3 winter day than a UV 12 January one, and the SHAPE of the least-UV path does not
# depend on the day's level anyway -- only on the beam/sky split.
#
# NOT SWEPT, said plainly: K has a 12-point sweep behind it and this has nothing of the
# sort. 0.25 spans the space reachable under the detour cap, and there is no UV ground
# truth on the graph to fit it against. A stated preference.
K_UV_DEFAULT = float(__import__("os").environ.get("SHADEME_K_UV", "0.25"))


def uv_cost(d, K=None, rise=None):
    """Edge cost under the UV preference. Plain length if the engine has not run."""
    L = equiv_length(d, rise)
    f = d.get("_uv_frac")
    if f is None:
        return L
    K = K_UV_DEFAULT if K is None else float(K)
    return L * (1.0 + K * float(f))


def uv_summary(G, path, uv_index):
    """UV dose over one walk. {} if the engine has not run or there is no index.

    The dose is index-minutes: the local index integrated over the time spent in it,
    exactly parallel to the degC-minutes thermal_summary reports -- additive along the
    path, zero when there is nothing to avoid, and the quantity the router minimises.
    `sed` restates it in standard erythemal doses (~2 SED reddens untanned fair skin).

    Distinct from thermal exposure on purpose: a walk can be 100% thermally comfortable
    and still collect a full dose.
    """
    from . import uv as UV
    es = _edges(G, path)
    if not es or all(e.get("_uv_frac") is None for e in es):
        return {}
    idx = 0.0 if uv_index is None else float(uv_index)
    dose = exposed_min = frac_m = m = 0.0
    peak = 0.0
    for d in es:
        f = d.get("_uv_frac")
        if f is None:
            continue
        f = float(f)
        L = float(d["length"])
        mins = equiv_length(d) / WALK_MPS / 60.0
        dose += idx * f * mins
        if f > 0.5:
            exposed_min += mins
        frac_m += f * L
        m += L
        peak = max(peak, idx * f)
    return {"uv_dose": round(dose, 2),
            "uv_sed": round(UV.sed(dose), 3),
            "uv_exposed_minutes": round(exposed_min, 1),
            "uv_mean_frac": round(frac_m / m, 3) if m > 0 else None,
            "uv_peak": round(peak, 1),
            "uv_index": None if uv_index is None else round(idx, 1)}


def compare_uv(chosen, shortest):
    """How much UV dose the chosen route avoids against the direct one."""
    if not chosen or not shortest:
        return {}
    base, got = shortest.get("uv_dose", 0.0), chosen.get("uv_dose", 0.0)
    return {"uv_dose_avoided": round(base - got, 2),
            "uv_sed_avoided": round(shortest.get("uv_sed", 0.0) - chosen.get("uv_sed", 0.0), 3),
            "uv_dose_avoided_pct": round((base - got) / base * 100.0, 1) if base > 0.01 else None}


def edge_shade(d, when):
    """Shade in [0,1] for this edge at `when` (a timegrid slot, or an hour).

    `_shade` is the transient value engine.apply() stashed for the currently applied slot
    AND shade set. It must win over the pickled `shade` dict, which was baked with one
    day's shadows -- reporting those on another day's route mixes the two silently.

    The pickled dict is keyed in WHOLE HOURS and the router now asks in half-hour slots,
    so both sides are put on the slot grid before the nearest-key search. Comparing 810
    against keys 6..20 directly would have quietly returned 20:00 for every afternoon
    walk -- the fallback is rare, but a rare wrong answer is the expensive kind.
    """
    t = d.get("_shade")
    if t is not None:
        return float(t)
    s = d.get("shade", 0.0)
    if isinstance(s, dict):
        if not s:
            return 0.0
        slot = TG.as_slot(when)
        for k in (slot, str(slot), TG.hour_of(slot), str(TG.hour_of(slot))):
            if k in s:                       # str() survives a json round-trip
                return float(s[k])
        return float(s[min(s, key=lambda k: abs(TG.as_slot(int(k)) - slot))])
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
    # direct-beam solar dose in kJ/m2 = sunlit seconds * W/m2 / 1000
    heat = sun / WALK_MPS * (direct_radiation / 1000.0)
    # `minutes` walks the EQUIVALENT length: stairs cost time, and the duration shown
    # should be the one the router minimised.
    return {"distance_m": round(dist, 1), "sun_m": round(sun, 1), "sun_pct": round(pct(sun), 1),
            "indoor_m": round(indoor, 1), "indoor_pct": round(pct(indoor), 1),
            "minutes": round(equiv / WALK_MPS / 60.0, 1), "heat_load": round(heat, 1),
            "climb_m": round(climb, 1), "doors": doors}


def thermal_summary(G, path):
    """Thermal exposure over one walk. {} if the physical engine has not run.

    The headline is STRESS x TIME, in degC-minutes, not mean UTCI. A mean averages a
    22.5 degC arcade together with a 40 degC footpath and describes neither, and it
    cannot reach zero inside the comfort band, so a walk with nothing to avoid still
    reports a difference that looks like a benefit. Degrees outside the 9..26 no-stress
    band integrated over exposed seconds is what the cost function already minimises: it
    is zero when there is nothing to avoid, additive along the path, and works unchanged
    in winter, where it counts cold.

    `heat` and `cold` are separate because `_stress` is an unsigned distance from the
    band, so the sign has to come back from `_utci`.
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
    """How much stress the chosen route avoids against the direct one.

    Percentages are None inside the comfort band rather than a suspicious 0.0.

    THE UI LEADS WITH `stress_load_avoided`, NOT the percentage. Measured over 40 pairs
    (tools/bench_hours.py, summer, K=0.10, door 45 m, HOURLY shade grid -- these predate
    the move to 30-minute slots, so re-run before quoting the digits):

        hour   baseline degC-min   avoided degC-min   avoided pct
          10                25.6               14.9         58.2
          13                91.1               38.7         42.5
          17               160.4               29.7         18.5

    17:00 is when the city is hottest and when the percentage reads WORST -- twice the
    dose avoided as at 10:00, a third of the percentage. The denominator moves: late sun
    is low, the city is in long shadow, and the shortest path is already shaded, so
    proportionally less is left to win exactly when the walk is worst. A demo driven off
    the percentage undersells itself at the hour that matters.
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
