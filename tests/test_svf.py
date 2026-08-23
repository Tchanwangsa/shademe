"""Validation for shademe/physics/svf.py: analytic cases, convergence, radius, real-DSM sanity.

Run: python tests/test_svf.py [a|b|c|d|e|f]   (default: all)
  a analytic canyon + flat plane      e independent-oracle cross-check
  b n_az convergence (synthetic)      f cell refinement + wall-quantisation limit
  c search-radius study (real DSM)    g obstacle-distance convention (resolution floor)
  d real-DSM stats and spot checks

Analytic reference (the README): infinite symmetric canyon, wall height H,
half-width d -> SVF = d/sqrt(d^2+H^2). Derivation: horizon distance along azimuth
phi off the canyon axis is d/|sin phi|, so t = H|sin phi|/d and
mean sin^2 = 1 - 1/sqrt(1+(H/d)^2), giving SVF = d/sqrt(d^2+H^2).
The sweep evaluates at z_ped, so the wall the model sees is (H - z_ped) tall:
every "expected" below uses h_eff = H - 1.1.
"""
import os, sys, json, time
import numpy as np

from shademe.physics.svf import svf_raster, _rays, N_AZ, MAX_R_M, Z_PED

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
SIZE = 421          # synthetic raster, cell = 1 m
TCELL = 1.0
TR = 200.0          # search radius for synthetic tests (< SIZE/2 cells)


def canyon(H, d, rot_deg=0.0, size=SIZE, cell=TCELL):
    """Infinite symmetric canyon: solid half-planes of height H at perpendicular
    distance >= d from the centre line. Returns (dsm, centre_mask)."""
    cc = rc = size // 2
    r, c = np.mgrid[0:size, 0:size]
    a = np.radians(rot_deg)
    u = ((c - cc) * np.cos(a) + (r - rc) * np.sin(a)) * cell   # perpendicular offset
    dsm = np.where(np.abs(u) >= d, np.float32(H), np.float32(0.0)).astype(np.float32)
    # centre line, kept away from the raster border in the axis direction
    keep = (np.abs(r - rc) < size // 3) & (np.abs(c - cc) < size // 3)
    return dsm, (np.abs(u) < 0.5 * cell) & keep


def exact(H, d, z=Z_PED):
    h = max(H - z, 0.0)
    return d / np.hypot(d, h)


# ---------------------------------------------------------------- (a) analytic
def part_a(n_az=N_AZ):
    print(f"\n=== (a) analytic cases   n_az={n_az}  max_r={TR}m  cell={TCELL}m  z_ped={Z_PED} ===")
    flat = np.zeros((200, 200), dtype=np.float32)
    s = svf_raster(flat, TCELL, n_az=n_az, max_r_m=TR)
    print(f"  flat plane        expected 1.000000  got {s.mean():.6f}  "
          f"max|err| {np.abs(s - 1.0).max():.2e}  (exact everywhere, incl. border)")
    assert np.abs(s - 1.0).max() < 1e-6

    print(f"  {'case':<22}{'expected':>10}{'got':>10}{'err':>10}{'maxerr':>10}"
          f"{'contract(no z)':>16}")
    rows = []
    for H, d, rot in [(20, 10, 0), (40, 10, 0), (10, 20, 0),
                      (20, 10, 30), (40, 10, 30), (10, 20, 30)]:
        dsm, m = canyon(H, d, rot)
        s = svf_raster(dsm, TCELL, n_az=n_az, max_r_m=TR)
        got, e = s[m].mean(), exact(H, d)
        mx = np.abs(s[m] - e).max()
        name = f"H={H:<3}d={d:<3}rot={rot:>2}"
        print(f"  {name:<22}{e:>10.4f}{got:>10.4f}{got-e:>+10.4f}{mx:>10.4f}"
              f"{exact(H, d, 0.0):>16.4f}")
        rows.append((name, e, float(got)))
    return rows


# ------------------------------------------------------------- (b) convergence
def part_b():
    print(f"\n=== (b) n_az convergence (canyon H=20 d=10, max_r={TR}m) ===")
    ref = 512
    for rot in (0, 30):
        dsm, m = canyon(20, 10, rot)
        e = exact(20, 10)
        vals = {}
        for n in (8, 16, 32, 64, 128, 256, ref):
            t = time.time()
            vals[n] = float(svf_raster(dsm, TCELL, n_az=n, max_r_m=TR)[m].mean())
            vals[(n, "t")] = time.time() - t
        print(f"  rot={rot:>2} deg   analytic {e:.4f}")
        print(f"    {'n_az':>6}{'SVF':>10}{'err vs analytic':>18}{'err vs n=512':>14}{'s':>8}")
        for n in (8, 16, 32, 64, 128, 256, ref):
            print(f"    {n:>6}{vals[n]:>10.4f}{vals[n]-e:>+18.4f}"
                  f"{vals[n]-vals[ref]:>+14.4f}{vals[(n,'t')]:>8.1f}")


# ---------------------------------------------------------- (c) search radius
def part_c(n_az=32, radii=(50, 100, 200, 400, 800)):
    print(f"\n=== (c) search radius on the real DSM (n_az={n_az}) ===")
    grid = json.load(open(f"{OUT}/grid.json"))
    dsm = np.maximum(np.load(f"{OUT}/dsm_buildings.npy"), np.load(f"{OUT}/dsm_canopy.npy"))
    b = int(max(radii) / grid["cell"])          # trim the zero-pad-affected margin
    sl = (slice(b, -b), slice(b, -b))
    print(f"  comparing over interior {dsm[sl].shape} (border {b} cells dropped)")
    print(f"    {'max_r':>8}{'mean SVF':>11}{'mean|d|':>11}{'p99|d|':>11}{'max|d|':>11}{'s':>8}")
    prev = None
    for R in radii:
        t = time.time()
        s = svf_raster(dsm, grid["cell"], n_az=n_az, max_r_m=float(R))[sl]
        dt = time.time() - t
        if prev is None:
            print(f"    {R:>8}{s.mean():>11.5f}{'-':>11}{'-':>11}{'-':>11}{dt:>8.1f}")
        else:
            d = np.abs(s - prev)
            print(f"    {R:>8}{s.mean():>11.5f}{d.mean():>11.5f}"
                  f"{np.percentile(d, 99):>11.5f}{d.max():>11.5f}{dt:>8.1f}")
        prev = s


# ------------------------------------------------------------ (d) real DSM
SPOTS = [
    # (name, lat, lon, expectation)
    ("Yarra @ Princes Bridge",  -37.8195, 144.9680, "open"),
    ("Yarra @ Southbank ftbrg", -37.8205, 144.9645, "open"),
    ("Flagstaff Gardens",       -37.8110, 144.9540, "open"),
    ("Carlton Gardens S",       -37.8055, 144.9710, "open"),
    ("Birrarung Marr",          -37.8180, 144.9720, "open"),
    ("Hardware Lane",           -37.8130, 144.9615, "laneway"),
    ("Degraves St",             -37.8168, 144.9655, "laneway"),
    ("Centre Place",            -37.8163, 144.9652, "laneway"),
    ("Niagara Lane",            -37.8143, 144.9585, "laneway"),
    ("Bourke St Mall",          -37.8135, 144.9645, "street"),
]


def part_d(path=None):
    # Default to the raster the ENGINE actually reads (svf_veg, canopy-corrected by
    # shademe/physics/canopy_svf.py), falling back to svf_all. Pinning svf_all here would audit
    # a raster nothing consumes any more. Pass a path explicitly to check a specific one.
    if path is None:
        veg = f"{OUT}/svf_veg.npy"
        path = veg if os.path.exists(veg) else f"{OUT}/svf_all.npy"
    print(f"\n=== (d) real-DSM sanity ({os.path.basename(path)}) ===")
    if not os.path.exists(path):
        print(f"  ! {path} missing -- run `python shademe/physics/svf.py` first"); return
    from pyproj import Transformer
    from shademe.config import WGS84, MGA55
    grid = json.load(open(f"{OUT}/grid.json"))
    minx, miny, maxx, maxy = grid["bounds"]
    cell, H, W = grid["cell"], grid["h"], grid["w"]
    s = np.load(path)
    dsm = np.maximum(np.load(f"{OUT}/dsm_buildings.npy"), np.load(f"{OUT}/dsm_canopy.npy"))
    tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)

    p = np.percentile(s, [1, 5, 25, 50, 75, 95, 99])
    print(f"  percentiles  p1 {p[0]:.3f}  p5 {p[1]:.3f}  p25 {p[2]:.3f}  p50 {p[3]:.3f}  "
          f"p75 {p[4]:.3f}  p95 {p[5]:.3f}  p99 {p[6]:.3f}   mean {s.mean():.3f}")
    hist, edges = np.histogram(s, bins=10, range=(0, 1))
    for i in range(10):
        print(f"    {edges[i]:.1f}-{edges[i+1]:.1f}  {hist[i]/s.size*100:5.1f}%  "
              + "#" * int(hist[i] / s.size * 100))
    open_air = s[dsm == 0]
    print(f"  cells with nothing on them (n={open_air.size/1e6:.2f}M): "
          f"mean {open_air.mean():.3f}  p5 {open_air.min():.3f}/{np.percentile(open_air,5):.3f}")

    # Hand-typed lat/lon is only good to ~30 m, and at 2 m cells that regularly
    # lands inside a building footprint -- where SVF is correctly ~0 and the check
    # is meaningless. Snap each probe to the nearest cell with nothing on it.
    print(f"  {'spot':<26}{'expect':>9}{'SVF':>8}{'snap m':>8}{'open 5x5':>10}"
          f"{'tallest within 30m':>20}")
    for name, lat, lon, kind in SPOTS:
        x, y = tf.transform(lon, lat)
        r, c = int((maxy - y) / cell), int((x - minx) / cell)
        if not (0 <= r < H and 0 <= c < W):
            print(f"  {name:<26} outside grid"); continue
        k = 20
        r0, c0 = max(0, r - k), max(0, c - k)
        sub = dsm[r0:r + k + 1, c0:c + k + 1]
        rr, cc = np.nonzero(sub == 0)
        if len(rr) == 0:
            print(f"  {name:<26}{kind:>9}  no open ground within {k*cell:.0f} m"); continue
        j = np.argmin((rr + r0 - r) ** 2 + (cc + c0 - c) ** 2)
        snap = cell * float(np.hypot(rr[j] + r0 - r, cc[j] + c0 - c))
        r, c = int(rr[j] + r0), int(cc[j] + c0)
        w = s[max(0, r-2):r+3, max(0, c-2):c+3]
        nb = dsm[max(0, r-15):r+16, max(0, c-15):c+16]
        print(f"  {name:<26}{kind:>9}{s[r, c]:>8.3f}{snap:>8.0f}"
              f"{w.mean():>10.3f}{nb.max():>20.1f}")

    # data-driven: where are the extremes?
    open_frac = float((s > 0.95).mean()) * 100
    dark_frac = float((s < 0.40).mean()) * 100
    print(f"  {open_frac:.1f}% of cells SVF>0.95 (open sky), {dark_frac:.1f}% SVF<0.40 "
          f"(deep canyon / inside footprints)")
    ground = s[dsm == 0]
    print(f"  of open-ground cells only: {float((ground<0.40).mean())*100:.1f}% below 0.40 "
          f"-- these are the real laneways")
    _edges(grid, path)


def _edges(grid, path):
    """attach_svf smoke test on the real graph. Read-only: never re-pickles."""
    import pickle
    from shademe.physics.svf import attach_svf
    p = f"{OUT}/graph.pkl"
    if not os.path.exists(p):
        print("  (no graph.pkl, skipping attach_svf check)"); return
    G = pickle.load(open(p, "rb"))
    t = time.time()
    attach_svf(G, grid, path)
    v = np.array([d["svf"] for _, _, d in G.edges(data=True)])
    ind = np.array([d["indoor"] or d["covered"] for _, _, d in G.edges(data=True)])
    q = np.percentile(v[~ind], [5, 25, 50, 75, 95])
    print(f"  attach_svf: {len(v)} edges in {time.time()-t:.1f}s, "
          f"{ind.sum()} indoor/covered forced to {v[ind][0] if ind.any() else float('nan'):.1f}")
    print(f"    outdoor edges  p5 {q[0]:.3f}  p25 {q[1]:.3f}  median {q[2]:.3f}  "
          f"p75 {q[3]:.3f}  p95 {q[4]:.3f}  mean {v[~ind].mean():.3f}")
    assert np.isfinite(v).all() and (v >= 0).all() and (v <= 1).all()


# --------------------------------------------------- (e) independent oracle
# A second implementation sharing no code with svf.py: per-cell python loop,
# bilinear DSM sampling, 0.25 m radial steps, 720 azimuths. Lives outside the
# repo; set SVF_ORACLE to its path. Agreement is evidence, not proof, but the
# two methods discretise the ray completely differently.
ORACLE = os.environ.get("SVF_ORACLE", "")


def _oracle():
    if not ORACLE or not os.path.exists(ORACLE):
        print("  ! oracle not found (set SVF_ORACLE=/path/to/svf_oracle.py); skipping")
        return None
    sys.path.insert(0, os.path.dirname(ORACLE))
    import svf_oracle
    return svf_oracle


def part_e(n_probe=200, n_az=N_AZ, seed=0):
    print("\n=== (e) cross-check vs independent oracle ===")
    o = _oracle()
    if o is None:
        return
    R = 100.0
    print("  same synthetic array, both methods (analytic 0.4677, H=20 d=10, cell=2m)")
    print(f"    {'fixture':<34}{'d_eff':>7}{'oracle':>9}{'svf.py':>9}{'diff':>9}")
    fixtures = [("oracle's  wall where |x| >  d", o.canyon_dsm(20, 10, cell=2.0, n=111)[0]),
                ("ours      wall where |u| >= d", canyon(20, 10.0, 0.0, 111, 2.0)[0])]
    for label, arr in fixtures:
        mid = 55
        row = np.nonzero(arr[mid])[0]
        deff = (row[row > mid].min() - mid) * 2.0
        a = o.svf_point(arr, 2.0, mid, mid, max_r_m=R, step_m=0.25)
        b = float(svf_raster(arr, 2.0, n_az=n_az, max_r_m=R)[mid, mid])
        print(f"    {label:<34}{deff:>7.1f}{a:>9.4f}{b:>9.4f}{b-a:>+9.4f}")

    # real DSM: scattered probe cells
    grid = json.load(open(f"{OUT}/grid.json"))
    dsm = np.maximum(np.load(f"{OUT}/dsm_buildings.npy"), np.load(f"{OUT}/dsm_canopy.npy"))
    H, W = dsm.shape
    b = int(MAX_R_M / grid["cell"])
    rng = np.random.default_rng(seed)
    rr = rng.integers(b, H - b, n_probe)
    cc = rng.integers(b, W - b, n_probe)
    mine = svf_raster(dsm, grid["cell"], n_az=n_az, max_r_m=MAX_R_M)[rr, cc]
    t = time.time()
    orc = np.array([o.svf_point(dsm, grid["cell"], int(r), int(c),
                                max_r_m=MAX_R_M, step_m=0.25) for r, c in zip(rr, cc)])
    d = mine - orc
    print(f"  real DSM, {n_probe} scattered cells, n_az={n_az} max_r={MAX_R_M:g}m "
          f"(oracle took {time.time()-t:.0f}s)")
    print(f"    mean diff {d.mean():+.4f}   mean|d| {np.abs(d).mean():.4f}   "
          f"p95|d| {np.percentile(np.abs(d),95):.4f}   max|d| {np.abs(d).max():.4f}")
    print(f"    corr {np.corrcoef(mine, orc)[0,1]:.5f}   "
          f"oracle mean {orc.mean():.4f} vs svf.py mean {mine.mean():.4f}")
    # split by openness: the disagreement should concentrate in tight geometry
    for lo, hi in [(0.0, 0.4), (0.4, 0.7), (0.7, 0.95), (0.95, 1.01)]:
        m = (orc >= lo) & (orc < hi)
        if m.sum():
            print(f"    oracle SVF {lo:.2f}-{hi:.2f}  n={m.sum():>4}  "
                  f"mean diff {d[m].mean():+.4f}  mean|d| {np.abs(d[m]).mean():.4f}")


# ------------------------------------- (f) cell refinement + raster-quantisation
def part_f():
    print("\n=== (f) cell refinement and the wall-position accuracy limit ===")
    o = _oracle()
    R, EXT, e = 100.0, 110.0, exact(20, 10)
    print(f"  H=20 d=10, analytic {e:.4f}, max_r={R:g}m. Two fixtures: a strict '>'\n"
          f"  test puts the wall one cell too far out; '>=' lands it on the true face.")
    print(f"    {'cell':>6}{'|x|>d d_eff':>12}{'oracle':>9}{'svf.py':>9}"
          f"{'|u|>=d d_eff':>14}{'oracle':>9}{'svf.py':>9}")
    for cell in (2.0, 1.0, 0.5, 0.25):
        n = int(2 * EXT / cell) // 2 * 2 + 1
        mid = n // 2
        gm = canyon(20, 10.0, 0.0, n, cell)[0]
        de = lambda a: (np.nonzero(a[mid])[0][np.nonzero(a[mid])[0] > mid].min() - mid) * cell
        m2 = float(svf_raster(gm, cell, n_az=64, max_r_m=R)[mid, mid])
        if o is None:
            print(f"    {cell:>6}{'-':>12}{'-':>9}{'-':>9}{de(gm):>14.2f}{'-':>9}{m2:>9.4f}")
            continue
        g = o.canyon_dsm(20, 10, cell=cell, n=n)[0]
        m1 = float(svf_raster(g, cell, n_az=64, max_r_m=R)[mid, mid])
        o1 = o.svf_point(g, cell, mid, mid, max_r_m=R, step_m=0.25)
        o2 = o.svf_point(gm, cell, mid, mid, max_r_m=R, step_m=0.25)
        print(f"    {cell:>6}{de(g):>12.2f}{o1:>9.4f}{m1:>9.4f}"
              f"{de(gm):>14.2f}{o2:>9.4f}{m2:>9.4f}")
    print("  NB the residual +0.003 at |u|>=d is max_r truncation: an *infinite*\n"
          "  canyon needs infinite radius, near-axis rays never find the wall.")

    print("\n  Wall position on a 2 m grid is a cell-centre test (rasterio all_touched=\n"
          "  False in build_dsm.py), so a wall face is within +-1 m of truth, zero-mean.\n"
          "  Analytic SVF cost of a 1 m half-width error, by street width:")
    f = lambda d, h: d / np.hypot(d, h)
    print(f"    {'street width':>13}{'H=20 SVF':>10}{'rel err':>9}"
          f"{'H=30 SVF':>10}{'rel err':>9}{'H=50 SVF':>10}{'rel err':>9}")
    for d in (3., 5., 7.5, 10., 15., 25.):
        row = f"    {2*d:>13.0f}"
        for Hw in (20., 30., 50.):
            h = Hw - Z_PED
            a, b = f(d, h), f(d + 1, h)
            row += f"{a:>10.4f}{(b-a)/a*100:>8.1f}%"
        print(row)


# ------------------------------------------- (g) which distance to an obstacle?
def _svf_pt(dsm, cell, r, c, n_az, R, mode, step=0.25, z=Z_PED):
    """Scalar reference walk. mode='centre' uses the obstructing cell's centre
    distance (what svf.py does); mode='face' uses centre - cell/2, i.e. roughly
    where the ray enters the cell (what a fine-stepped continuous walk does)."""
    H, W = dsm.shape
    tot = 0.0
    for steps in _rays(n_az, R, cell, step):
        t = 0.0
        for dr, dc, inv_d in steps:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < H and 0 <= cc < W):
                break
            d = 1.0 / inv_d
            if mode == "face":
                d = max(d - cell / 2, cell / 4)
            v = (dsm[rr, cc] - z) / d
            if v > t: t = v
        tot += t * t / (1 + t * t)
    return 1.0 - tot / n_az


def part_g(n_probe=60, seed=0):
    print("\n=== (g) obstacle distance convention, and the 2 m resolution floor ===")
    print("  A DSM cell is a 2 m square of solid. Measuring to its CENTRE vs to the\n"
          "  face the ray enters gives different horizons; the gap IS the resolution\n"
          "  limit. svf.py uses the centre, because build_dsm.py rasterises with\n"
          "  all_touched=False -- a boundary cell's centre lies on the true polygon\n"
          "  edge, so centre distance is the unbiased estimate of the real wall.")
    g = canyon(20, 10.0, 0.0, 111, 2.0)[0]      # wall cell centres at the true face
    print(f"    canyon H=20 d=10 cell=2m, analytic (continuum d=10)  {exact(20,10):>7.4f}")
    print(f"      centre distance (svf.py)                          "
          f"{_svf_pt(g,2.0,55,55,64,100.0,'centre'):>7.4f}")
    print(f"      near-face distance                                "
          f"{_svf_pt(g,2.0,55,55,64,100.0,'face'):>7.4f}")
    print(f"    ... and the analytic value for a wall at d=9 is      {exact(20,9):>7.4f}"
          f"  <- what near-face targets")
    print("    So near-face shrinks the canyon by half a cell. The conventions\n"
          "    bracket the truth and centre is the unbiased one.")

    grid = json.load(open(f"{OUT}/grid.json"))
    dsm = np.maximum(np.load(f"{OUT}/dsm_buildings.npy"), np.load(f"{OUT}/dsm_canopy.npy"))
    H, W = dsm.shape
    rng = np.random.default_rng(seed)
    b = int(MAX_R_M / grid["cell"])
    rr = rng.integers(b, H - b, n_probe); cc = rng.integers(b, W - b, n_probe)
    a = np.array([_svf_pt(dsm, grid["cell"], int(r), int(c), N_AZ, MAX_R_M, "centre")
                  for r, c in zip(rr, cc)])
    f = np.array([_svf_pt(dsm, grid["cell"], int(r), int(c), N_AZ, MAX_R_M, "face")
                  for r, c in zip(rr, cc)])
    d = np.abs(a - f)
    print(f"  real DSM, {n_probe} cells: centre {a.mean():.4f}  near-face {f.mean():.4f}")
    print(f"    resolution band |centre-face|: mean {d.mean():.4f}  "
          f"p95 {np.percentile(d,95):.4f}  max {d.max():.4f}")
    for lo, hi, lbl in [(0.0, 0.3, "enclosed"), (0.3, 0.6, "laneway-ish"),
                        (0.6, 0.9, "street"), (0.9, 1.01, "open")]:
        m = (a >= lo) & (a < hi)
        if m.sum():
            print(f"    SVF {lo:.1f}-{hi:.1f} {lbl:<12} n={m.sum():>3}  band {d[m].mean():.4f}")
    print("  This band, not the analytic canyon error, is the honest uncertainty\n"
          "  on a 2 m grid: worst in tight geometry, which is what routing cares about.")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "abcdefg"
    for k, fn in [("a", part_a), ("b", part_b), ("c", part_c), ("d", part_d),
                  ("e", part_e), ("f", part_f), ("g", part_g)]:
        if k in which: fn()
    # (d) again on the legacy raster, so the canopy correction stays visible side by side
    if "d" in which and os.path.exists(f"{OUT}/svf_veg.npy"):
        part_d(f"{OUT}/svf_all.npy")
