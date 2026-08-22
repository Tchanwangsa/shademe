"""How hard does the router go indoors and upstairs, and what does that buy?

    python scripts/bench_indoor.py                      # door-penalty sweep, 14:00
    python scripts/bench_indoor.py --sweep rise,door,K  # all three, ONE weather payload
    python scripts/bench_indoor.py --sweep K --hour 9 --n 60
    python scripts/bench_indoor.py --key protected      # the other keying arm

Sweeps are comma-separated and run in ONE process on purpose: the numbers only compare
if the weather behind them is the same payload (see below), and the three parameters
interact -- 3.1 km of the stepped network is protected, so pricing the climb moves the
indoor share on its own, before any door is charged.

Exists because every indoor figure quoted so far has been measured on ONE OD pair --
Melbourne Central to Federation Square -- which runs the length of the arcade spine and
is the most indoor-favourable route in the city. It is not the engine's behaviour, it is
that pair's. This samples OD pairs across the protected network's own footprint instead,
and reports the same quantity the cost function minimises (degC-minutes outside the
no-thermal-stress band) rather than mean UTCI, which averages an air-conditioned arcade
with a sunlit footpath and describes neither.

Both arms of any comparison run in ONE process against ONE weather payload. The
Open-Meteo feed moves between runs -- Ta for the same 2026-01-26 14:00 block moved 1.1 C
during a single session -- so before/after measured in separate runs is not attributable.
"""
import os, sys, math, pickle, random, argparse
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import numpy as np
import networkx as nx
from server import engine as E_, weather
from server import cost as C
from server.cost import thermal_summary, protected, utci_cost, door_state
from server.routing import shortest, path_length, _astar_state

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def sample_pairs(G, n=40, lo=600.0, hi=2000.0, radius=1400.0, seed=7):
    """OD pairs drawn around the protected network's centroid.

    Uniform over the whole graph would mostly draw pairs with no arcade within reach,
    which measures the sampler rather than the router. Around the centroid is the
    population that could plausibly go indoors -- the hard case for the shade model."""
    main = list(G.graph.get("main") or max(nx.connected_components(G), key=len))
    XY = {x: G.nodes[x]["xy"] for x in main}
    prot = [x for u, v, d in G.edges(data=True) if protected(d) for x in (u, v) if x in XY]
    cx = np.mean([XY[x][0] for x in prot]); cy = np.mean([XY[x][1] for x in prot])
    near = sorted(x for x in main if math.dist(XY[x], (cx, cy)) < radius)
    rng = random.Random(seed)
    out = []
    while len(out) < n:
        a, b = rng.sample(near, 2)
        if lo < math.dist(XY[a], XY[b]) < hi:
            out.append((a, b))
    return out


def route(G, s, t, K, door=0.0, rise=None, level=0.0, key=None, block=()):
    """One route under the real router's search. See routing._astar_state.

    This used to be a private Dijkstra living in this file, which is how a bench ends up
    measuring something the server does not do. It calls the shipped search now, and the
    only thing it varies is the parameters under test.
    """
    return _astar_state(G, s, t, lambda d: utci_cost(d, K, rise),
                        door=door, level_jump=level, closed=block, key=key)


def _stepped(e):
    return bool(e.get("rise_m") or 0.0) or bool(e.get("conveying"))


def summarise_path(G, path, base_m):
    es = [G[u][v] for u, v in zip(path, path[1:])]
    L = sum(e["length"] for e in es)
    f = lambda pred: sum(e["length"] for e in es if pred(e)) / L * 100.0
    return {"indoor_pct": f(lambda e: e.get("indoor")),
            "covered_pct": f(lambda e: e.get("covered") and not e.get("indoor")),
            "ratio": L / base_m,
            # climb is the rise you actually walk -- escalators excluded, they carry you
            "rise_m": sum(e.get("rise_m", 0.0) for e in es if not e.get("conveying")),
            "doors": sum(1 for a, b in zip(es, es[1:])
                         if bool(a.get("indoor")) != bool(b.get("indoor"))),
            # storey changes with no staircase either side: the climbs the graph forgot
            "jumps": sum(abs(int(a.get("level") or 0) - int(b.get("level") or 0))
                         for a, b in zip(es, es[1:])
                         if not (_stepped(a) or _stepped(b))),
            "stress": thermal_summary(G, path)["stress_load"],
            # The mean indoor SHARE cannot answer the question the door penalty is asked:
            # is this an arcade used as a leg of the walk, or a 40 m duck through a foyer?
            # Both look identical in a percentage. The runs do not.
            "runs": indoor_runs(es)}


def indoor_runs(es):
    """Lengths of the consecutive indoor stretches on a path. One entry per door pair."""
    out, cur = [], 0.0
    for e in es:
        if door_state(e):
            cur += float(e["length"])
        elif cur:
            out.append(cur); cur = 0.0
    if cur:
        out.append(cur)
    return out


SWEEPS = {                       # what to vary, and over what
    "door":  [0, 10, 20, 30, 36, 45, 55, 69, 70, 90, 120, 200],
    "K":     [0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60],
    "rise":  [0.0, 1.5, 3.0, 6.0, 12.0],
    "level": [0.0, 6.0, 12.0, 24.0],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hour", type=int, default=14)
    ap.add_argument("--mode", default="summer")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--graph", default=os.path.join(ROOT, "out", "graph.pkl"))
    ap.add_argument("--sweep", default="door",
                    help="comma-separated: " + ",".join(SWEEPS) + " -- all in one process")
    ap.add_argument("--K", type=float, default=None, help="held fixed while not swept")
    ap.add_argument("--door", type=float, default=None)
    ap.add_argument("--rise", type=float, default=None)
    ap.add_argument("--level", type=float, default=None)
    ap.add_argument("--key", choices=("protected", "indoor"), default="indoor",
                    help="what counts as a door: any protected edge, or indoor only")
    ap.add_argument("--values", default=None,
                    help="comma-separated sweep points, overriding the default list. "
                         "Used to find where a plateau actually starts and stops -- a "
                         "value picked from a coarse grid is a value picked at an edge.")
    a = ap.parse_args()

    # Defaults come from the shipped constants, so a bench with no flags measures what
    # the server actually does. Only the parameter under test departs from them.
    fixed = {"K": C.K_DEFAULT if a.K is None else a.K,
             "door": C.DOOR_PENALTY_M if a.door is None else a.door,
             "rise": C.RISE_M_PER_M if a.rise is None else a.rise,
             "level": C.LEVEL_JUMP_M if a.level is None else a.level}

    wx = weather.get(a.mode)
    w = weather.block(a.hour, a.mode)
    G = pickle.load(open(a.graph, "rb"))
    E = E_.edge_index(G, mode=a.mode)
    E_.attach_tsurf(E, G, wx, mode=a.mode)
    E_.apply(G, E, E_.solve(E, w, wx, a.hour))

    key = protected if a.key == "protected" else door_state
    pairs = sample_pairs(G, a.n)
    sp = {p: shortest(G, *p) for p in pairs}
    base = {p: path_length(G, sp[p]) for p in pairs}
    bs = float(np.mean([thermal_summary(G, sp[p])["stress_load"] for p in pairs]))

    print(f"\ngraph {os.path.basename(a.graph)}  {G.number_of_edges()} edges")
    print(f"{a.mode} {a.hour:02d}:00  Ta {w['temperature']:.1f} C  "
          f"direct {w['direct_radiation']:.0f} W/m2  ({w['source']})")
    print(f"{len(pairs)} OD pairs  door keyed on {a.key}  "
          f"shortest-path baseline {bs:.1f} degC-min")
    print("held: " + "  ".join(f"{k}={v}" for k, v in fixed.items()))

    for name in [x.strip() for x in a.sweep.split(",") if x.strip()]:
        if name not in SWEEPS:
            raise SystemExit(f"unknown sweep {name!r}; pick from {sorted(SWEEPS)}")
        print(f"\n  sweeping {name}  (others held)")
        print(f"  {name:>7} {'ind%':>6} {'cov%':>6} {'ratio':>6} {'doors':>6} {'climb':>6} "
              f"{'jumps':>6} {'stress':>7} {'avoided':>8} {'med run':>8} {'dodge':>6} "
              f"{'leg':>5}  routes")
        seen = {}                       # route-set signature -> first value that produced it
        vals = ([float(v) for v in a.values.split(",")] if a.values else list(SWEEPS[name]))
        for x in vals + ([float("inf")] if name == "door" and not a.values else []):
            kw = dict(fixed); kw[name] = 1e9 if x == float("inf") else x
            rows, sig = [], []
            for p in pairs:
                path = route(G, *p, kw["K"], kw["door"], kw["rise"], kw["level"], key)
                rows.append(summarise_path(G, path, base[p]))
                sig.append(tuple(path))
            m = lambda k: float(np.mean([r[k] for r in rows]))
            runs = [L for r in rows for L in r["runs"]]
            dodge = sum(1 for L in runs if L < 50.0) / len(rows)
            leg = sum(1 for L in runs if L >= 150.0) / len(rows)
            runm = float(np.median(runs)) if runs else 0.0
            sig = hash(tuple(sig))
            # "Plateau" has to mean the SAME 40 routes, not two means that round alike.
            # Without this the midpoint of a plateau is guesswork.
            tag = "=" if sig in seen else "new"
            seen.setdefault(sig, x)
            lab = "inf" if x == float("inf") else (f"{x:.2f}" if x < 1 else f"{x:g}")
            print(f"  {lab:>7} {m('indoor_pct'):>6.1f} {m('covered_pct'):>6.1f} "
                  f"{m('ratio'):>6.3f} {m('doors'):>6.1f} {m('rise_m'):>6.1f} "
                  f"{m('jumps'):>6.2f} {m('stress'):>7.1f} "
                  f"{(bs - m('stress')) / bs * 100:>7.1f}% {runm:>8.0f} {dodge:>6.2f} "
                  f"{leg:>5.2f}  {tag}")
        # med run: median indoor stretch in metres. dodge/leg: indoor stretches per route
        # under 50 m / over 150 m -- the duck through a foyer against the arcade leg.
        if name == "door":
            # inf is not "indoor removed": a route may start or end inside, and no
            # transition is charged for that. The residual share is those legs.
            print("  (inf = no transition is ever worth paying for, not indoor removed)")

    try:
        import provenance
        print(f"\n  {provenance.line()}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
