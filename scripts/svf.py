"""Sky view factor by multi-azimuth horizon sweep over a DSM.

Cosine-weighted radiative SVF for a horizontal surface at pedestrian height
(ENGINE_CONTRACT.md "SVF definition"):

    SVF = 1 - mean_over_azimuths( sin^2(theta) ),   sin^2 = t^2/(1+t^2)

with t = tan(theta) the horizon tangent along that azimuth, taken as the running
max of (dsm_shifted - z_ped) / distance over march steps k = 1..K.

Run directly to write out/svf_bldg.npy and out/svf_all.npy.
  python scripts/svf.py [canopy_dsm.npy] [--all-only]

CAVEAT for whoever consumes svf_all (mrt.py): the horizon-max definition is exact
for buildings, which really are solid from ground to roof, but a tree is not. The
canopy DSM stores only crown-top height, so taking theta = max elevation per
azimuth implicitly extrudes every crown down to the ground and fills in the trunk
gap. Cosine-weighted, a crown of radius a at height h overhead truly blocks
sin^2(atan(a/h)) of the sky -- 5.5% for a 3 m crown at 12.4 m -- whereas
horizon-max scores it as 94.5% blocked. So `svf_bldg - svf_all` is an UPPER BOUND
on canopy sky-blocking, roughly an order of magnitude too large for isolated
crowns and closer to right under continuous canopy.

RESOLVED: scripts/canopy_svf.py now computes the true cosine-weighted canopy
blocking analytically and writes out/svf_veg.npy, which is what the engine reads.
svf_all is kept as the upper bound canopy_svf.py clips against, and is still the
fallback if svf_veg has not been generated. Do NOT treat svf_all as a literal sky
fraction under trees -- measured on the real raster, the single transmissivity
that would repair it varies from 0.00 to 0.72 across cells (p10-p90).
"""
import os, sys, json, time
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from config import CELL

OUT = os.path.join(os.path.dirname(__file__), "..", "out")

# Production settings -- from the studies in test_svf.py, measured on the REAL DSM.
# The analytic canyon plateaus by n_az=16, but the city does not: mean|dSVF| vs a
# 256-azimuth reference is .0175/.0074/.0039/.0017/.0010 at n_az 8/16/32/64/128
# (p99 .090/.041/.0195/.0088/.0049). 64 puts the worst 1% of cells under 0.01 SVF
# ~= 0.2 K of MRT, below every other error in the chain; 128 halves that for 2x.
N_AZ    = 64
# Radius: mean|dSVF| per doubling is .0114 (50->100), .0061 (100->200), .0025
# (200->400), .00083 (400->800). 400 m is also the largest radius the DATA supports
# -- config.BUFFER_M is 500 m, so beyond that a cell on the CBD bbox edge would be
# reading zero-pad as open sky. 800 m would be less accurate there, not more.
MAX_R_M = 400.0
# Radial march increment, in cells. 1.0 (shadow.py's step) lets diagonal rays
# tunnel between adjacent wall cells; 0.25 is converged. See test_svf.py part (g).
RAY_STEP = 0.25
Z_PED    = 1.1     # globe-thermometer / MRT reference height


def _shift(a, dr, dc):
    """out[r,c] = a[r+dr, c+dc], zero-padded. Same as shadow.py's _shift.

    Kept for reference/parity; svf_raster uses the equivalent windowed form
    below, which skips the zero pad because a padded 0 gives a *negative*
    tangent (0 - z_ped) and so can never beat the running max, which starts
    at 0. Border cells therefore see "no obstruction beyond the raster" --
    SVF is over-estimated within MAX_R_M of the edge. The 500 m config.BUFFER_M
    around the CBD bbox makes that a throwaway margin.
    """
    H, W = a.shape
    out = np.zeros_like(a)
    r0, r1 = max(0, -dr), min(H, H - dr)
    c0, c1 = max(0, -dc), min(W, W - dc)
    if r0 < r1 and c0 < c1:
        out[r0:r1, c0:c1] = a[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
    return out


def _rays(n_az, max_r_m, cell, step=RAY_STEP):
    """[(dr, dc, 1/dist_m)] per azimuth: integer cell offsets along each ray.

    Marched in sub-cell increments (`step` in cells) and de-duplicated, so the
    ray visits every cell it actually crosses instead of skipping diagonally
    between them. shadow.py's step of exactly 1 cell lets a ray tunnel through
    a 2 m wall on diagonal azimuths; that biases SVF *up* (missed obstruction)
    by ~0.015 mean on the real DSM -- an order more than n_az>64 buys.

    Distance is the obstructing cell's CENTRE, hypot(dr,dc)*cell. That is
    deliberate: build_dsm.py rasterises with all_touched=False (cell-centre
    rule), so a boundary cell's centre sits on the true polygon edge and the
    centre distance is the unbiased estimate of the real wall position. Using
    the distance at which the ray *enters* the cell instead would shift every
    obstacle half a cell nearer and shrink a canyon by that much -- see
    test_svf.py part (g), which measures both and brackets the truth.
    """
    K = max(1, int(round(max_r_m / (cell * step))))
    out = []
    for i in range(n_az):
        az = 2.0 * np.pi * i / n_az
        steps, seen = [], set()
        for k in range(1, K + 1):
            kk = k * step
            dr = int(round(-kk * np.cos(az)))    # north is -row
            dc = int(round(kk * np.sin(az)))     # east  is +col
            if (dr, dc) in seen or (dr == 0 and dc == 0):
                continue
            seen.add((dr, dc))
            steps.append((dr, dc, 1.0 / (cell * np.hypot(dr, dc))))
        out.append(steps)
    return out


def svf_raster(dsm, cell=CELL, n_az=N_AZ, max_r_m=MAX_R_M, z_ped=Z_PED,
               step=RAY_STEP, verbose=False):
    """float32 (H,W) sky view factor in [0,1]. Vectorised; one azimuth at a time."""
    dsm = np.asarray(dsm, dtype=np.float32)
    H, W = dsm.shape
    acc = np.zeros((H, W), dtype=np.float32)      # running sum of sin^2(theta)
    tan = np.empty((H, W), dtype=np.float32)      # horizon tangent, this azimuth
    buf = np.empty((H, W), dtype=np.float32)      # scratch for the candidate

    for i, steps in enumerate(_rays(n_az, max_r_m, cell, step)):
        tan.fill(0.0)                              # clamp theta >= 0
        for dr, dc, inv_d in steps:
            r0, r1 = max(0, -dr), min(H, H - dr)
            c0, c1 = max(0, -dc), min(W, W - dc)
            if r0 >= r1 or c0 >= c1:
                break                              # ray fully off the raster
            src = dsm[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
            cand = buf[r0:r1, c0:c1]
            np.subtract(src, np.float32(z_ped), out=cand)
            cand *= np.float32(inv_d)
            np.maximum(tan[r0:r1, c0:c1], cand, out=tan[r0:r1, c0:c1])
        t2 = tan * tan
        acc += t2 / (1.0 + t2)                     # sin^2(theta)
        if verbose and (i + 1) % 8 == 0:
            print(f"    az {i+1}/{n_az}", flush=True)

    return np.clip(1.0 - acc / n_az, 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------- edge sampling
def attach_svf(G, grid, path=None, n=8, indoor_svf=0.0):
    """Fill edge['svf'] with the mean SVF along the edge (n points).

    Defaults to out/svf_veg.npy (canopy-corrected) and falls back to out/svf_all.npy.

    Same gather as build_graph.sample_hourly(): r/c from MGA55, clip + inside mask.

    indoor_svf = 0.0, NOT 1.0. An indoor or arcaded pedestrian sees no sky:
    every solid angle above them is ceiling at roughly air temperature. SVF is
    consumed downstream as (a) the diffuse-shortwave multiplier and (b) the
    sky/wall split of the longwave hemisphere, so 0.0 gives no diffuse sun and
    an all-surface longwave hemisphere -> MRT ~ Tair, which is the whole point
    of routing people indoors. SVF=1.0 would say the ceiling is open sky and
    hand indoor edges the *coldest* possible sky longwave -- a free bonus for
    the wrong reason, and it would break in winter where a clear sky is a heat
    sink. NB ENGINE_CONTRACT.md line 87 says "1.0 for indoor/covered"; that is
    the `shade` convention (1 = fully shaded) leaking into a different
    quantity. Flagged, not silently followed -- override via indoor_svf= if the
    integration wave decides otherwise.
    """
    if path is None:                    # prefer the canopy-corrected raster, see canopy_svf.py
        veg = os.path.join(OUT, "svf_veg.npy")
        path = veg if os.path.exists(veg) else os.path.join(OUT, "svf_all.npy")
    minx, miny, maxx, maxy = grid["bounds"]
    H, W, cell = grid["h"], grid["w"], grid["cell"]
    sun = []
    for u, v, d in G.edges(data=True):
        if d["indoor"] or d["covered"]:
            d["svf"] = float(indoor_svf)
        else:
            sun.append((u, v))
    if not sun:
        return

    xy = np.array([[G.nodes[u]["xy"], G.nodes[v]["xy"]] for u, v in sun])   # (E,2,2)
    f = ((np.arange(n) + 0.5) / n)[None, :, None]
    pts = xy[:, 0][:, None, :] + (xy[:, 1] - xy[:, 0])[:, None, :] * f      # (E,n,2)
    r = ((maxy - pts[..., 1]) / cell).astype(np.int32)
    c = ((pts[..., 0] - minx) / cell).astype(np.int32)
    inside = (r >= 0) & (r < H) & (c >= 0) & (c < W)
    flat = (np.clip(r, 0, H - 1) * W + np.clip(c, 0, W - 1))
    cnt = inside.sum(1)
    cnt[cnt == 0] = 1

    g = np.load(path, mmap_mode="r").ravel()
    vals = (np.asarray(g[flat]) * inside).sum(1) / cnt
    for (u, v), s in zip(sun, vals):
        G[u][v]["svf"] = float(s)


def _stats(name, a):
    p = np.percentile(a, [5, 25, 50, 75, 95])
    print(f"  {name}: mean {a.mean():.4f}  median {p[2]:.4f}  "
          f"p5 {p[0]:.4f}  p25 {p[1]:.4f}  p75 {p[3]:.4f}  p95 {p[4]:.4f}  "
          f"min {a.min():.4f}  max {a.max():.4f}")


def _canopy_default():
    """Prefer the allometric canopy DSM; fall back to the flat-8 m original."""
    v2 = f"{OUT}/dsm_canopy_v2.npy"
    return v2 if os.path.exists(v2) else f"{OUT}/dsm_canopy.npy"


if __name__ == "__main__":
    # usage: svf.py [canopy_dsm.npy] [--all-only]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    all_only = "--all-only" in sys.argv          # svf_bldg is canopy-independent
    canopy = args[0] if args else _canopy_default()

    t0 = time.time()
    grid = json.load(open(f"{OUT}/grid.json"))
    cell = grid["cell"]
    b = np.load(f"{OUT}/dsm_buildings.npy")
    c = np.load(canopy)
    cov = c > 0
    print(f"  dsm {b.shape} cell {cell}m  n_az {N_AZ}  max_r {MAX_R_M}m  "
          f"step {RAY_STEP} cells  z_ped {Z_PED}m")
    print(f"  canopy {os.path.basename(canopy)}: {cov.mean()*100:.1f}% cover, "
          f"mean {c[cov].mean():.2f} m over covered cells, max {c.max():.1f} m")

    if all_only:
        svf_b = np.load(f"{OUT}/svf_bldg.npy")
        print("  svf_bldg reused (buildings only, canopy cannot change it)")
    else:
        t = time.time()
        svf_b = svf_raster(b, cell, verbose=True)
        np.save(f"{OUT}/svf_bldg.npy", svf_b)
        print(f"  svf_bldg in {time.time()-t:.1f}s")

    t = time.time()
    svf_a = svf_raster(np.maximum(b, c), cell, verbose=True)
    print(f"  svf_all  in {time.time()-t:.1f}s")

    bad = int((svf_b < svf_a - 1e-6).sum())
    assert bad == 0, f"svf_bldg < svf_all at {bad} cells -- canopy made the sky bigger"
    print(f"  monotonicity OK (svf_bldg >= svf_all everywhere); canopy blocks "
          f"{float((svf_b - svf_a).mean()):.4f} of sky on average, "
          f"{float((svf_b - svf_a)[cov].mean()):.4f} under canopy")

    prev = np.load(f"{OUT}/svf_all.npy") if os.path.exists(f"{OUT}/svf_all.npy") else None
    np.save(f"{OUT}/svf_all.npy", svf_a)
    _stats("svf_bldg", svf_b)
    _stats("svf_all ", svf_a)
    if prev is not None and prev.shape == svf_a.shape:
        d = svf_a - prev
        print(f"  vs previous svf_all: mean {d.mean():+.4f}  mean|d| {np.abs(d).mean():.4f}  "
              f"p99|d| {np.percentile(np.abs(d),99):.4f}  max|d| {np.abs(d).max():.4f}")
        print(f"    under canopy  mean {d[cov].mean():+.4f} (n={cov.sum()/1e6:.2f}M)   "
              f"elsewhere mean {d[~cov].mean():+.4f}")
    print(f"  wrote out/svf_all.npy in {time.time()-t0:.1f}s total")
