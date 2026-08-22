"""Physics checks for scripts/surface_temp.py. Synthetic inputs only -- touches no out/.

Run: python scripts/test_surface_temp.py
"""
import os
import sys
import numpy as np
from scipy.optimize import brentq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import surface_temp as st

FAIL = []


def check(name, ok, msg=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + msg) if msg else ''}")
    if not ok:
        FAIL.append(name)


# --- synthetic material table (shape of out/material_props.json) --------------
# rho_c_d is the AREAL heat capacity of the diurnally-active layer, so it must be
# built from the thermal admittance mu = sqrt(k rho c), not from a guessed slab
# thickness:   rho_c_d = mu * sqrt(2/omega) = 165.8 * mu,  omega = 2pi/86400.
# (mu in J m-2 K-1 s-1/2; Oke 1987 Table 2.1.) Getting this wrong is the single
# biggest lever on peak Ts -- see the note in the report to materials.py.
PROPS = {
    0: dict(name="default",  albedo=0.20, emissivity=0.95, rho_c_d=2.5e5),
    1: dict(name="asphalt",  albedo=0.12, emissivity=0.95, rho_c_d=2.6e5),  # mu~1590
    2: dict(name="stone",    albedo=0.35, emissivity=0.92, rho_c_d=2.9e5),  # mu~1780
    3: dict(name="mulch",    albedo=0.15, emissivity=0.95, rho_c_d=2.0e4),  # stiffness
    4: dict(name="turf",     albedo=0.22, emissivity=0.97, rho_c_d=1.5e5),  # mu~900
    5: dict(name="stone_dk", albedo=0.15, emissivity=0.95, rho_c_d=2.9e5),  # d) twin
}
WIND_MAX_KMH = 40.0             # worst case for stiffness


def row(t_c, dirr, diff, cloud, wind_kmh, rh=45.0):
    return dict(temperature_2m=t_c, direct_radiation=dirr, diffuse_radiation=diff,
                cloud_cover=cloud, wind_speed_10m=wind_kmh, relative_humidity_2m=rh)


def flat_day(**kw):
    """24 identical hours."""
    r = row(**kw)
    return {h: dict(r) for h in range(24)}


def summer_day(wind_kmh=12.0, cloud=5.0):
    """Clear Melbourne January day: Ta 18->34 C, clear-sky-ish horizontal irradiance."""
    d = {}
    for h in range(24):
        s = max(0.0, np.sin(np.pi * (h - 6.0) / 14.0)) if 6 <= h <= 20 else 0.0
        d[h] = row(26.0 - 8.0 * np.cos(2 * np.pi * (h - 5) / 24.0),
                   820.0 * s ** 1.3, 130.0 * s ** 0.6, cloud, wind_kmh)
    return d


def shade_day(shade_map, hours=range(6, 21)):
    """{hour: array} from one constant (h,w) shade array. Hours outside `hours` fall
    back to DEFAULT_SHADE=1.0, which is only harmless when the sun is actually down --
    a constant-forcing test must therefore pass hours=range(24)."""
    return {h: np.asarray(shade_map, np.float32) for h in hours}


def grid(ids, svf=1.0):
    a = np.atleast_2d(np.asarray(ids, np.uint8))
    return a, np.full(a.shape, svf, np.float32)


# =============================================================================
def t_a_steady_state():
    print("\n(a) STEADY STATE -- marched answer vs brentq root of the same expression")
    w = flat_day(t_c=30.0, dirr=700.0, diff=120.0, cloud=0.0, wind_kmh=15.0)
    mid, svf = grid([[1, 2, 3, 4]], svf=0.8)
    sh = shade_day([[0.0, 0.0, 0.0, 0.0]], hours=range(24))
    d = {}
    out = st.march(sh, svf, mid, PROPS, w, hours=[12], spin_loops=40, n_sub=24, diag=d)
    ts = out[12].ravel()

    f = d["forcing"][12]
    P = st.props_arrays(PROPS)
    worst_r, worst_e = 0.0, 0.0
    for i in range(4):
        mi = int(mid[0, i])
        alb, eps = float(P["albedo"][mi]), float(P["emissivity"][mi])
        q = float(st.absorbed(f, 0.0, 0.8, alb, eps))
        r = float(st.net_flux(ts[i], q, eps, f["h"], f["ta_k"]))
        root = brentq(lambda T: st.net_flux(T, q, eps, f["h"], f["ta_k"]),
                      200.0, 500.0, xtol=1e-10)
        print(f"      {PROPS[mi]['name']:9s} Ts={ts[i]-273.15:6.2f}C  root={root-273.15:6.2f}C"
              f"  |dT|={abs(ts[i]-root)*1e3:7.3f} mK  residual={r:+.3e} W/m2")
        worst_r, worst_e = max(worst_r, abs(r)), max(worst_e, abs(ts[i] - root))
    check("residual ~ 0", worst_r < 5e-2, f"max |R| = {worst_r:.2e} W/m2")
    # 5 mK is the float32 state floor, not a physics error: the update
    # dt R / (C + dt b) underflows float32 once R ~ 0.03 W/m2, i.e. R/b ~ 1.5 mK.
    check("marched == brentq root", worst_e < 5e-3, f"max |dT| = {worst_e*1e3:.3f} mK")


def t_b_night():
    print("\n(b) NIGHT / RADIATIVE COOLING -- clear, calm, no shortwave")
    for cloud, tag in ((0.0, "clear"), (100.0, "overcast")):
        w = flat_day(t_c=18.0, dirr=0.0, diff=0.0, cloud=cloud, wind_kmh=3.0, rh=60.0)
        mid, svf = grid([[1, 4]], svf=1.0)
        d = {}
        out = st.march(shade_day([[1.0, 1.0]]), svf, mid, PROPS, w,
                       hours=[3], spin_loops=30, n_sub=24, diag=d)
        f = d["forcing"][3]
        dt = out[3].ravel() - f["ta_k"]
        print(f"      {tag:9s} eps_sky={st.sky_emissivity(f['ta_k'], f['e_hpa'], cloud/100):.3f}"
              f" L_sky={f['l_sky']:5.1f} W/m2  h={f['h']:.2f}  "
              f"Ts-Ta: asphalt {dt[0]:+.2f} K, turf {dt[1]:+.2f} K")
        if tag == "clear":
            check("clear sky cools below air", dt.max() < -1.0,
                  f"warmest is {dt.max():+.2f} K")
            check("cooling is a few K, not tens", dt.min() > -12.0,
                  f"coldest is {dt.min():+.2f} K")
        else:
            check("overcast cools much less than clear", dt.max() > -2.0,
                  f"warmest is {dt.max():+.2f} K")

    # shaded-by-buildings cell: low SVF replaces cold sky with warm wall
    w = flat_day(t_c=18.0, dirr=0.0, diff=0.0, cloud=0.0, wind_kmh=3.0, rh=60.0)
    mid, svf = grid([[1]], svf=0.30)
    o = st.march(shade_day([[1.0]]), svf, mid, PROPS, w, hours=[3], spin_loops=30, n_sub=24)
    print(f"      canyon SVF=0.30 clear night: Ts-Ta = {float(o[3])-291.15:+.2f} K"
          "   (canyons cool less -- the observed UHI signature)")


def t_c_step_size():
    print("\n(c) STEP-SIZE CONVERGENCE -- lowest inertia (mulch) at highest wind")
    w = summer_day(wind_kmh=WIND_MAX_KMH)
    mid, svf = grid([[3]], svf=1.0)
    P = st.props_arrays(PROPS)
    f = st.forcing(w[14])
    cap, eps = float(P["rho_c_d"][3]), float(P["emissivity"][3])
    tau = cap / (f["h"] + 4 * eps * st.SIGMA * 330.0 ** 3)
    dtmax = float(st.stability_dt(cap, eps, f["h"], 330.0))
    print(f"      u10={WIND_MAX_KMH} km/h -> u({st.Z_PED}m)={f['u_surf']:.2f} m/s, "
          f"h={f['h']:.2f} W/m2K; C={cap:.1e}; tau={tau:.0f} s; "
          f"explicit-Euler limit 2C/b = {dtmax:.0f} s")
    check("dt=3600 would be explicitly UNSTABLE", dtmax < 3600.0,
          f"{dtmax:.0f} s < 3600 s -- forward Euler at 1 h would oscillate")

    ref = None
    prev = None
    print("      n_sub    dt(s)   max|Ts - Ts(n_sub=192)|   max|Ts - Ts(prev)|")
    for n in (1, 3, 6, 12, 24, 48, 96, 192):
        o = st.march(shade_day([[0.0]]), svf, mid, PROPS, w, hours=range(24),
                     spin_loops=6, n_sub=n)
        v = np.array([float(o[h]) for h in range(24)])
        if n == 192:
            ref = v
    for n in (1, 3, 6, 12, 24, 48, 96):
        o = st.march(shade_day([[0.0]]), svf, mid, PROPS, w, hours=range(24),
                     spin_loops=6, n_sub=n)
        v = np.array([float(o[h]) for h in range(24)])
        e = np.max(np.abs(v - ref))
        h_ = "" if prev is None else f"{np.max(np.abs(v - prev)):.4f} K"
        print(f"      {n:5d} {3600.0/n:8.1f}   {e:18.4f} K   {h_:>18s}")
        if n == 12:
            e12 = e
        if n == 24:
            e24 = e
        prev = v
    check("halving dt halves the error (1st order, converging)", e12 / max(e24, 1e-9) > 1.7,
          f"err(dt=300s)={e12:.4f} K, err(dt=150s)={e24:.4f} K, ratio {e12/e24:.2f}")
    check("default n_sub=12 is accurate to <0.05 K", e12 < 0.05, f"{e12:.4f} K")


def t_d_inertia():
    print("\n(d) INERTIA ORDERING -- same albedo/emissivity, rho_c_d 2e4 vs 3e5")
    w = summer_day()
    mid, svf = grid([[3, 5]], svf=1.0)        # mulch 2.0e4 vs stone_dk 3.0e5
    o = st.march(shade_day([[0.0, 0.0]]), svf, mid, PROPS, w, hours=range(24),
                 spin_loops=6, n_sub=24)
    ta = np.array([w[h]["temperature_2m"] for h in range(24)])
    res = []
    for i, nm in ((0, "mulch  C=2.0e4"), (1, "stone  C=3.0e5")):
        v = np.array([float(o[h].ravel()[i]) for h in range(24)]) - 273.15
        k = int(np.argmax(v))
        a, b, c = v[(k - 1) % 24], v[k], v[(k + 1) % 24]     # parabolic peak
        off = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
        pk = b - 0.25 * (a - c) * off
        res.append((k + off, pk, v.max() - v.min()))
        print(f"      {nm}: peak {pk:5.2f} C at {k + off:5.2f} h  "
              f"(diurnal range {v.max()-v.min():5.2f} K)")
    print(f"      air:            peak {ta.max():5.2f} C at {int(np.argmax(ta)):5.2f} h  "
          f"(diurnal range {ta.max()-ta.min():5.2f} K)")
    check("high inertia peaks LATER", res[1][0] > res[0][0] + 0.2,
          f"{res[1][0]:.2f} h vs {res[0][0]:.2f} h  (+{res[1][0]-res[0][0]:.2f} h lag)")
    check("high inertia peaks LOWER", res[1][1] < res[0][1] - 0.5,
          f"{res[1][1]:.2f} C vs {res[0][1]:.2f} C")
    check("high inertia damps the swing", res[1][2] < res[0][2],
          f"{res[1][2]:.2f} K vs {res[0][2]:.2f} K")


def t_e_shade():
    print("\n(e) SHADE RESPONSE -- asphalt, sunlit vs fully shaded")
    w = summer_day()
    #   cell 0 open sun, 1 tree-shaded (open sky), 2 building-shaded (canyon SVF)
    mid = np.array([[1, 1, 1]], np.uint8)
    svf = np.array([[1.0, 1.0, 0.35]], np.float32)
    sh = {h: np.array([[0.0, 1.0, 1.0]], np.float32) for h in range(6, 21)}
    o = st.march(sh, svf, mid, PROPS, w, hours=range(24), spin_loops=6, n_sub=24)
    ta = np.array([w[h]["temperature_2m"] for h in range(24)])
    v = np.array([o[h].ravel() for h in range(24)]) - 273.15
    hot = int(np.argmax(v[:, 0]))
    print(f"      hottest hour = {hot:02d}:00, Ta = {ta[hot]:.1f} C")
    for i, nm in ((0, "sunlit  shade=0.0 svf=1.00"), (1, "tree    shade=1.0 svf=1.00"),
                  (2, "canyon  shade=1.0 svf=0.35")):
        print(f"      {nm}: Ts = {v[hot, i]:5.1f} C   Ts-Ta = {v[hot, i]-ta[hot]:+5.1f} K")
    d_tree = v[hot, 0] - v[hot, 1]
    d_can = v[hot, 0] - v[hot, 2]
    print(f"      delta at hottest hour: tree shade {d_tree:.1f} K, canyon {d_can:.1f} K")
    check("shaded is markedly cooler", d_tree > 8.0, f"{d_tree:.1f} K")
    excess = v[hot, 0] - ta[hot]
    check("sunlit asphalt 12..30 K over air", 12.0 < excess < 30.0, f"{excess:+.1f} K")
    check("shaded asphalt stays within a few K of air",
          abs(v[hot, 1] - ta[hot]) < 6.0, f"{v[hot,1]-ta[hot]:+.1f} K")


def t_f_energy():
    print("\n(f) ENERGY CONSERVATION -- integrated flux vs stored heat")
    w = summer_day()
    P = st.props_arrays(PROPS)
    for mi in (1, 2, 3):
        alb, eps = float(P["albedo"][mi]), float(P["emissivity"][mi])
        cap = float(P["rho_c_d"][mi])
        errs = []
        for n_sub in (12, 48):
            dt = 3600.0 / n_sub
            t0 = w[0]["temperature_2m"] + 273.15
            ts, acc, thru = t0, 0.0, 0.0
            for _ in range(4):                      # 4 diurnal loops
                for hh in range(24):
                    f = st._mean_forcing(st.forcing(w[hh]), st.forcing(w[(hh + 1) % 24]))
                    q = float(st.absorbed(f, 1.0 if not 6 <= hh <= 20 else 0.0, 1.0, alb, eps))
                    for _ in range(n_sub):
                        ts = float(st.substep(ts, q, eps, cap, f["h"], f["ta_k"], dt))
                        r = dt * float(st.net_flux(ts, q, eps, f["h"], f["ta_k"]))
                        acc += r
                        thru += abs(r)              # gross energy through the node
            store = cap * (ts - t0)
            errs.append(abs(acc - store) / thru)
            print(f"      {PROPS[mi]['name']:8s} dt={dt:5.1f}s  int(R)dt = {acc:12.1f} J/m2"
                  f"   C dT = {store:12.1f} J/m2   gap/throughput = {errs[-1]:.2e}")
        check(f"conservation ({PROPS[mi]['name']})", errs[1] < 1e-4,
              f"{errs[1]:.2e} at dt=75 s (was {errs[0]:.2e} at dt=300 s)")


def t_g_spinup():
    print("\n(g) SPIN-UP CONVERGENCE -- max |Ts(loop n) - Ts(loop n-1)| at midnight")
    w = summer_day()
    mid, svf = grid([[1, 2, 3, 4]], svf=0.7)
    d = {}
    st.march(shade_day([[0.0, 0.3, 0.7, 1.0]]), svf, mid, PROPS, w,
             hours=[12], spin_loops=8, n_sub=12, diag=d)
    for i, v in enumerate(d["spin"]):
        print(f"      loop {i + 1}: max |dTs| = {v:8.4f} K")
    check("2 loops already < 0.5 K", d["spin"][1] < 0.5, f"{d['spin'][1]:.4f} K")
    check("3 loops < 0.05 K (default spin_loops=3 is enough)", d["spin"][2] < 0.05,
          f"{d['spin'][2]:.4f} K")


def t_h_shape_and_io():
    print("\n(h) SHAPES, STREAMING, save_hourly")
    w = summer_day()
    rng = np.random.default_rng(0)
    mid = rng.integers(0, 5, (40, 60)).astype(np.uint8)
    svf = rng.uniform(0.2, 1.0, (40, 60)).astype(np.float32)
    sh = {h: rng.uniform(0, 1, (40, 60)).astype(np.float32) for h in range(6, 21)}
    seen = []
    out = st.march(sh, svf, mid, PROPS, w, spin_loops=3, n_sub=12, keep=False,
                   on_hour=lambda h, t: seen.append((h, t.shape, t.dtype)))
    check("keep=False holds nothing", out == {})
    check("streamed 15 hours 06..20", [s[0] for s in seen] == list(range(6, 21)))
    check("float32 (h,w) per contract", all(s[1] == (40, 60) and s[2] == np.float32
                                            for s in seen))
    # lazy loader form (what the real 6 M-cell grid will use)
    out2 = st.march(lambda h: sh.get(h), svf, mid, PROPS, w, hours=[14],
                    spin_loops=3, n_sub=12)
    check("callable shade source matches dict", np.allclose(
        out2[14], st.march(sh, svf, mid, PROPS, w, hours=[14], spin_loops=3,
                           n_sub=12)[14]))
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out")
    p = st.save_hourly(out2[14], 14, tmp, prefix="_tmp_tsurf_test")
    ok = np.load(p).dtype == np.float32 and np.load(p).shape == (40, 60)
    os.remove(p)
    check("save_hourly writes float32 (h,w) then cleaned up", ok, os.path.basename(p))


def t_i_bench():
    print("\n(i) SCALE -- real grid 2485x2438 (6.06 M cells), one hour timed")
    import time
    import resource
    H, W = 2485, 2438
    n = 8                                    # 8 rows only; extrapolate
    w = summer_day()
    mid = np.zeros((n, W), np.uint8)
    svf = np.full((n, W), 0.7, np.float32)
    sh = {h: np.full((n, W), 0.4, np.float32) for h in range(6, 21)}
    t0 = time.time()
    st.march(sh, svf, mid, PROPS, w, spin_loops=3, n_sub=12, keep=False)
    per_cell = (time.time() - t0) / (n * W)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
    print(f"      {per_cell*1e9:.1f} ns/cell for the full 3-loop march "
          f"-> ~{per_cell*H*W:.1f} s for 6.06 M cells")
    print(f"      (this process peak RSS so far: {rss:.0f} MB)")
    print("      Measured on the real 2485x2438 grid, spin_loops=3 n_sub=12 keep=False:")
    print("        33.0 s wall, 741 MB peak RSS -- but 504 MB of that was the caller")
    print("        holding all 15 shade rasters resident. march() itself adds ~240 MB")
    print("        (svf, mat_id, alb/eps/cap maps, shade, q, ts, ~2 scratch).")
    print("        Pass a callable shade loader + keep=False and the whole step fits")
    print("        in ~270 MB; keep=True would add 15 x 24.2 = 363 MB on top.")


if __name__ == "__main__":
    for t in (t_a_steady_state, t_b_night, t_c_step_size, t_d_inertia, t_e_shade,
              t_f_energy, t_g_spinup, t_h_shape_and_io, t_i_bench):
        t()
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    sys.exit(1 if FAIL else 0)
