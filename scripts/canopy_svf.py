"""Analytic canopy sky-blocking -> out/svf_canopy_block.npy, out/svf_veg.npy.

WHY THIS EXISTS
---------------
svf.py computes the horizon by taking, per azimuth, the running max of
(dsm - z_ped)/distance. That is exact for a building, which really is solid from
its roofline down to the ground, and wrong for a tree, which is a crown floating
on a stick. Horizon-max silently extrudes every crown to the pavement and fills
in the trunk gap, so `svf_bldg - svf_all` over-states canopy sky-blocking by
roughly 2x in the mean and by an order of magnitude under isolated street trees
(svf.py's own docstring says so).

ENGINE_CONTRACT.md line 35 says the MRT step "weights [canopy sky-blocking] by a
canopy transmissivity rather than treating it as opaque". That knob was never
implemented -- engine.py read svf_all raw and svf_bldg was dead code. It is also
the WRONG fix: measured on the real raster, the transmissivity that would repair
horizon-max varies from 0.07 to 0.78 across cells (p10-p90), because continuous
canopy and an isolated crown need different values. One constant cannot do it.

So compute the geometry instead of fitting it. A mapped crown polygon at height z
is, seen from below, an opaque horizontal patch. The exact cosine-weighted view
factor from a horizontal receiver to a horizontal patch of area dA at height
dz and horizontal distance rho is

    dF = dz^2 * dA / (pi * R^4),        R^2 = rho^2 + dz^2

which integrates over a disc of radius a directly overhead to a^2/(a^2+h^2) --
i.e. exactly sin^2(atan(a/h)), the quantity svf.py's docstring names as the truth.
Summing dF over the canopy DSM inside a 40 m neighbourhood therefore gives the
canopy's true cosine-weighted sky blocking, with NO free parameter.

The one physical constant left is leaf transmissivity: crowns are not fully
opaque. SOLWEIG's published default is 3% (UMEP "Transmissivity of light through
vegetation", and its svfbuveg = svf - (1 - svfveg)*(1 - trans)). We use the same
value and the same blend. That is a published constant, not a fit -- and the
whole plausible range 0.03..0.20 moves SVF by under 0.05, versus the unbounded
error it replaces.

Run:  python scripts/canopy_svf.py
"""
import os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from config import CELL, TAU_LEAF     # TAU_LEAF is shared with shadow.py -- see config.py

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))

Z_PED = 1.1        # same reference height as svf.py -- these two must not drift apart
R_M = 40.0         # neighbourhood radius. dF falls as 1/R^4: a full 2 m ring of canopy
                   # at 40 m contributes < 0.008, and beyond 60 m < 0.002. See _tail().
                   # TAU_LEAF (SOLWEIG leaf-on transmissivity, 3%) now lives in
                   # config.py so shadow.py uses the SAME constant. Imported above.


def blocking(canopy, buildings, cell=CELL, z_ped=Z_PED, r_m=R_M, verbose=False):
    """Cosine-weighted sky fraction blocked by canopy, treating each canopy cell as an
    opaque horizontal patch at its crown-top height. float32 (H,W) in [0,1].

    Canopy is counted only where the crown is ABOVE both the pedestrian and the local
    building DSM -- a crown at or below roof level is already inside the building horizon
    and would be double counted.
    """
    cz = np.asarray(canopy, dtype=np.float32)
    bz = np.asarray(buildings, dtype=np.float32)
    dz = np.where((cz > z_ped) & (cz > bz), cz - z_ped, 0.0).astype(np.float32)
    H, W = dz.shape
    acc = np.zeros((H, W), dtype=np.float32)
    K = int(r_m / cell)
    dA = cell * cell
    t0 = time.time()
    for dr in range(-K, K + 1):
        for dc in range(-K, K + 1):
            rho2 = (dr * dr + dc * dc) * cell * cell
            if rho2 > r_m * r_m:
                continue
            r1, r2 = max(0, -dr), min(H, H - dr)
            c1, c2 = max(0, -dc), min(W, W - dc)
            if r1 >= r2 or c1 >= c2:
                continue
            src = dz[r1 + dr:r2 + dr, c1 + dc:c2 + dc]
            z2 = src * src
            np.add(acc[r1:r2, c1:c2],
                   np.where(src > 0, z2 * dA / (np.pi * np.maximum((rho2 + z2) ** 2, 1e-6)), 0.0),
                   out=acc[r1:r2, c1:c2], casting="unsafe")
        if verbose and (dr + K) % 10 == 0:
            print(f"    row offset {dr:+d}/{K}  {time.time()-t0:.1f}s", flush=True)
    return np.clip(acc, 0.0, 1.0)


def _tail(cell=CELL, z=10.0):
    """Contribution of a fully-canopied 2 m ring at radius rho -- justifies R_M."""
    for rho in (20.0, 40.0, 60.0, 100.0):
        n = 2 * np.pi * rho / cell                      # cells in the ring
        print(f"    full canopy ring at {rho:5.0f} m contributes "
              f"{n * z*z * cell*cell / (np.pi * (rho*rho + z*z)**2):.4f}")


if __name__ == "__main__":
    t0 = time.time()
    b = np.load(f"{OUT}/dsm_buildings.npy")
    cpath = (f"{OUT}/dsm_canopy_v2.npy" if os.path.exists(f"{OUT}/dsm_canopy_v2.npy")
             else f"{OUT}/dsm_canopy.npy")
    c = np.load(cpath)
    svf_b = np.load(f"{OUT}/svf_bldg.npy")
    svf_a = np.load(f"{OUT}/svf_all.npy")
    print(f"  dsm {b.shape} cell {CELL}m  canopy {os.path.basename(cpath)}  "
          f"radius {R_M}m  z_ped {Z_PED}m  tau_leaf {TAU_LEAF}")
    _tail()

    blk_raw = blocking(c, b, verbose=True)
    # horizon-max IS a valid upper bound: extruding a crown to the ground can only block
    # more of each azimuth than the floating crown does. Anywhere the patch sum exceeds
    # it, the excess is double counting between overlapping crowns -- clip it away.
    ub = np.clip(svf_b - svf_a, 0.0, 1.0)
    over = float((blk_raw > ub + 1e-6).mean())
    blk = np.minimum(blk_raw, ub)
    np.save(f"{OUT}/svf_canopy_block.npy", blk.astype(np.float32))

    svf_v = np.clip(svf_b - (1.0 - TAU_LEAF) * blk, 0.0, 1.0).astype(np.float32)
    np.save(f"{OUT}/svf_veg.npy", svf_v)

    m = ub > 0.005
    n = int(m.sum())
    print(f"\n  canopy-affected cells: {n} ({m.mean()*100:.1f}% of grid); "
          f"patch sum exceeded the horizon-max bound at {over*100:.1f}% (clipped)")
    for name, a in (("horizon-max blocking (today)", ub[m]),
                    ("analytic blocking (this)   ", blk[m])):
        print(f"    {name}: mean {a.mean():.4f}  p50 {np.median(a):.4f}  "
              f"p90 {np.percentile(a, 90):.4f}  max {a.max():.4f}")
    r = blk[m] / np.maximum(ub[m], 1e-6)
    print(f"    ratio analytic/horizon: mean {r.mean():.3f}  p10 {np.percentile(r,10):.3f}  "
          f"p50 {np.median(r):.3f}  p90 {np.percentile(r,90):.3f}")
    print(f"    -> the single tau that would match the mean is {1-r.mean():.2f}, but the "
          f"p10-p90 spread of the tau each cell needs is {1-np.percentile(r,90):.2f}"
          f"..{1-np.percentile(r,10):.2f}. That is why this is computed, not tuned.")
    for name, a in (("svf_all  (today) ", svf_a[m]), ("svf_veg  (this)  ", svf_v[m]),
                    ("svf_bldg (no veg)", svf_b[m])):
        print(f"    {name}: mean {a.mean():.4f}  p50 {np.median(a):.4f}")
    assert (svf_v >= svf_a - 1e-5).all(), "svf_veg must never be below svf_all"
    assert (svf_v <= svf_b + 1e-5).all(), "svf_veg must never exceed svf_bldg"
    print(f"  bounds OK (svf_all <= svf_veg <= svf_bldg everywhere)")
    print(f"  wrote out/svf_canopy_block.npy and out/svf_veg.npy in {time.time()-t0:.1f}s")
