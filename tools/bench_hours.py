"""What the benefit looks like hour by hour, in both of the units the UI could show.

    python tools/bench_hours.py                 # summer, 40 pairs, hours 07..20
    python tools/bench_hours.py --mode winter
    python tools/bench_hours.py --shade-only    # indoor routing off (door = inf)

WHY THIS EXISTS. `compare_thermal` returns the benefit twice -- `stress_load_avoided`
in degC-minutes and `stress_load_avoided_pct` -- and they do not peak at the same hour.
The percentage is a ratio to a moving denominator: it is highest when the baseline is
mildest and there is little heat to avoid, and it sags in the late afternoon when the
whole city is in long shadow and the shortest path is ALREADY shaded, which is exactly
when the absolute dose is at its worst. A demo driven off the percentage therefore looks
weakest at the hour heat risk is highest, for a reason that has nothing to do with the
engine.

The absolute number is also the one with a meaning: degC-minutes outside the 9..26 degC
no-stress band is a dose, it is additive along the path, it is zero when there is nothing
to avoid, and it is the quantity the cost function minimises. The percentage is a
presentation of it. Lead with the dose; the percentage is the subtitle.

ONE process, ONE weather payload, all hours -- the feed moves between runs. See
tools/bench_indoor.py for why that matters.
"""
import os, sys, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pickle
from shademe.api import engine as E_, weather
from shademe.api import cost as C
from shademe.api.cost import thermal_summary
from shademe.api.routing import shortest, path_length
from bench_indoor import sample_pairs, route

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="summer")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--hours", default="7-20")
    ap.add_argument("--K", type=float, default=None)
    ap.add_argument("--shade-only", action="store_true",
                    help="price the door at 1e9 m, i.e. no indoor routing at all -- the "
                         "arm that isolates what the SHADE model is worth")
    ap.add_argument("--graph", default=os.path.join(ROOT, "out", "graph.pkl"))
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.hours.split("-"))
    K = C.K_DEFAULT if a.K is None else a.K
    door = 1e9 if a.shade_only else C.DOOR_PENALTY_M

    wx = weather.get(a.mode)
    G = pickle.load(open(a.graph, "rb"))
    E = E_.edge_index(G, mode=a.mode)
    E_.attach_tsurf(E, G, wx, mode=a.mode)
    pairs = sample_pairs(G, a.n)

    print(f"\ngraph {os.path.basename(a.graph)}  {G.number_of_edges()} edges  "
          f"{len(pairs)} OD pairs  {a.mode}  K={K}  door={door:g} m")
    print(f"\n{'hour':>5} {'Ta':>6} {'dir':>6} {'base':>8} {'route':>8} "
          f"{'avoided':>9} {'avoided':>8} {'extra':>7} {'ind%':>6}")
    print(f"{'':>5} {'degC':>6} {'W/m2':>6} {'degC-min':>8} {'degC-min':>8} "
          f"{'degC-min':>9} {'pct':>8} {'m':>7} {'':>6}")
    rows = []
    for h in range(lo, hi + 1):
        w = weather.block(h, a.mode)
        E_.apply(G, E, E_.solve(E, w, wx, h))
        base = chosen = av = extra = ind = 0.0
        n = 0
        for s, t in pairs:
            sp = shortest(G, s, t)
            r = route(G, s, t, K, door=door)
            bs = thermal_summary(G, sp); cs = thermal_summary(G, r)
            if not bs or not cs:
                continue
            base += bs["stress_load"]; chosen += cs["stress_load"]
            extra += path_length(G, r) - path_length(G, sp)
            ind += sum(float(G[u][v]["length"]) for u, v in zip(r, r[1:])
                       if G[u][v].get("indoor")) / max(path_length(G, r), 1.0)
            n += 1
        base /= n; chosen /= n; extra /= n; ind = 100.0 * ind / n
        av = base - chosen
        pct = 100.0 * av / base if base > 0.01 else float("nan")
        rows.append((h, base, chosen, av, pct))
        print(f"{h:5d} {w['temperature']:6.1f} {w['direct_radiation']:6.0f} "
              f"{base:8.1f} {chosen:8.1f} {av:9.1f} {pct:8.1f} {extra:7.0f} {ind:6.1f}")

    r = np.array([x for x in rows if np.isfinite(x[4])])
    if len(r):
        hb, hp = int(r[r[:, 3].argmax(), 0]), int(r[r[:, 4].argmax(), 0])
        hh = int(r[r[:, 1].argmax(), 0])
        print(f"\n  hottest baseline at {hh:02d}:00 ({r[:,1].max():.1f} degC-min)")
        print(f"  most degC-min avoided at {hb:02d}:00 ({r[:,3].max():.1f})")
        print(f"  best PERCENTAGE at {hp:02d}:00 ({r[:,4].max():.1f}%)")
        at_hot = r[r[:, 0] == hh][0]
        print(f"  at the hottest hour the percentage reads {at_hot[4]:.1f}% while the dose "
              f"avoided is {at_hot[3]:.1f} degC-min")
        print("  -> quote the dose. The percentage is a ratio to a denominator that is "
              "itself\n     moving, so it understates exactly when the walk is worst.")
    try:
        from shademe import provenance
        print("\n  " + provenance.line(mode=a.mode))
    except Exception as e:
        print(f"\n  (no provenance stamp: {e!r})")


if __name__ == "__main__":
    main()
