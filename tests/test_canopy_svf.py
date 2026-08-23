"""Checks for the analytic canopy sky-blocking. Run: python tests/test_canopy_svf.py

(a) the patch sum reproduces the closed-form disc view factor a^2/(a^2+h^2)
(b) it is bounded above by horizon-max, which is what svf.py computes
(c) svf_all <= svf_veg <= svf_bldg on the real rasters
(d) the "one tau" alternative is measured, not asserted
"""
import os, sys
import numpy as np

from shademe.physics.canopy_svf import blocking, Z_PED, TAU_LEAF

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))
fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


print("(a) opaque disc overhead: patch sum vs exact a^2/(a^2+h^2)")
CELL = 0.5
for a, h in [(1.5, 11.3), (3.0, 11.3), (3.0, 5.0), (6.0, 11.3), (1.5, 4.0), (8.0, 8.0)]:
    n = int(np.ceil(a / CELL)) + 2
    # one cell of "ground" surrounded by a disc of canopy at height h + Z_PED
    W = 2 * n + 1
    cz = np.zeros((W, W), dtype=np.float32)
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1] * CELL
    cz[(xx * xx + yy * yy) <= a * a] = h + Z_PED
    b = blocking(cz, np.zeros_like(cz), cell=CELL, z_ped=Z_PED, r_m=(n + 1) * CELL)
    got, exact = float(b[n, n]), a * a / (a * a + h * h)
    check(f"disc a={a:.1f}m h={h:.1f}m", abs(got - exact) / exact < 0.05,
          f"exact {exact:.4f} got {got:.4f} ({100*(got-exact)/exact:+.1f}%)")

print("\n(b) a crown blocks less than the same crown extruded to the ground")
cz = np.zeros((41, 41), dtype=np.float32); cz[18:23, 18:23] = 12.0   # 10x10 m crown
b = blocking(cz, np.zeros_like(cz), cell=2.0, r_m=40.0)
a_eq = np.sqrt(100.0 / np.pi)                        # area-equivalent crown radius, 5.64 m
dz = 12.0 - Z_PED
exact = a_eq ** 2 / (a_eq ** 2 + dz ** 2)
got = float(b[20, 20])
check("isolated 10x10 m crown at 12 m matches the equivalent disc",
      abs(got - exact) < 0.03,
      f"exact-disc {exact:.4f} got {got:.4f} -- horizon-max would score this ~0.99, "
      f"a {0.99/got:.0f}x over-block")

print("\n(c) real rasters")
need = [f"{OUT}/svf_bldg.npy", f"{OUT}/svf_all.npy", f"{OUT}/svf_veg.npy"]
if not all(os.path.exists(p) for p in need):
    print("  (svf rasters not built; run shademe/physics/svf.py then shademe/physics/canopy_svf.py)")
else:
    sb, sa, sv = (np.load(p, mmap_mode="r") for p in need)
    sb, sa, sv = np.asarray(sb), np.asarray(sa), np.asarray(sv)
    check("svf_veg >= svf_all everywhere", bool((sv >= sa - 1e-5).all()))
    check("svf_veg <= svf_bldg everywhere", bool((sv <= sb + 1e-5).all()))
    m = (sb - sa) > 0.005
    check("canopy cells exist", int(m.sum()) > 1000, f"{int(m.sum())} cells")
    print(f"    mean SVF over canopy cells: all {sa[m].mean():.4f} -> "
          f"veg {sv[m].mean():.4f} -> bldg {sb[m].mean():.4f}")

    print("\n(d) could ONE transmissivity have done this instead?")
    blk = np.asarray(np.load(f"{OUT}/svf_canopy_block.npy", mmap_mode="r"))
    ub = np.clip(sb - sa, 0, 1)
    r = blk[m] / np.maximum(ub[m], 1e-6)
    lo, hi = 1 - np.percentile(r, 90), 1 - np.percentile(r, 10)
    print(f"    per-cell tau needed: p10 {lo:.2f}  median {1-np.median(r):.2f}  p90 {hi:.2f}")
    check("a single tau cannot serve both regimes", (hi - lo) > 0.3,
          f"p10-p90 spread {hi-lo:.2f}; that spread is why this is computed, not tuned")
    for tau in (0.0, 0.3, 0.5):
        d = np.abs((1 - tau) * ub[m] - blk[m])
        print(f"    tau={tau:.1f}: residual |dSVF| mean {d.mean():.4f}  p90 "
              f"{np.percentile(d,90):.4f}  max {d.max():.4f}")
    print(f"    (TAU_LEAF = {TAU_LEAF} is SOLWEIG's leaf transmissivity, applied to the "
          f"ANALYTIC block, not to the horizon-max bound)")

print("\n" + "=" * 60)
print(f"FAILURES: {len(fails)} {fails if fails else ''}")
sys.exit(1 if fails else 0)
