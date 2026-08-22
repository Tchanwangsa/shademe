"""Checks for the crown-slab shadow path. Run: python scripts/test_shadow.py

(a) base = 0 degenerates EXACTLY to the legacy horizon test on the crown tops
(b) analytic single-tree geometry: the crown shadow runs base/tan(el) .. top/tan(el)
(c) raising the crown base can only REMOVE canopy shadow, never add it (monotone)
(d) shade_factor levels: buildings 1.0, crown 1-TAU_LEAF, elsewhere 0.0
(e) the building path is untouched -- a tower still casts h/tan(el)
(f) the real rasters: how much crown shadow the trunk gap actually opens
"""
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from config import CELL, TAU_LEAF
from shadow import sun_position, shadow_mask, canopy_mask, shade_factor

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out"))
fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


print("(a) base=0 reproduces the legacy np.maximum() horizon test bit for bit")
rng = np.random.default_rng(0)
top = np.zeros((300, 300), np.float32)
for _ in range(40):
    r, c = rng.integers(20, 280, 2)
    top[r - 2:r + 3, c - 2:c + 3] = rng.uniform(6.0, 20.0)
zero = np.zeros_like(top)
bld = np.zeros_like(top)
for az, el in [(70, 15), (0, 45), (300, 25), (110, 5.4)]:
    legacy = shadow_mask(np.maximum(bld, top), CELL, az, el)
    slab = canopy_mask(top, zero, CELL, az, el)
    check(f"az={az} el={el}", np.array_equal(legacy, slab),
          f"{legacy.sum()} cells both" if np.array_equal(legacy, slab)
          else f"legacy {legacy.sum()} vs slab {slab.sum()}")

print("\n(b) one crown, sun due east: the mask matches base <= d*tan(el) < top exactly")
# az=90 is due east, so the shadow falls due WEST = decreasing column index. A
# single-column crown makes the distance to the occluder unambiguous, so the expected
# mask is closed-form and the comparison can be cell-for-cell rather than a tolerance.
N = 400
for h_top, h_base, el in [(14.0, 3.5, 20.0), (14.0, 3.5, 45.0), (10.0, 0.0, 30.0),
                          (20.0, 6.0, 15.0), (9.0, 2.0, 60.0)]:
    t = np.zeros((5, N), np.float32); b = np.zeros((5, N), np.float32)
    t[:, N - 1] = h_top; b[:, N - 1] = h_base
    got = canopy_mask(t, b, CELL, 90.0, el)[2, :N - 1]
    d = (N - 1 - np.arange(N - 1)) * CELL                  # distance to the crown, m
    rh = d * np.tan(np.radians(el))                        # beam height there
    want = (h_top - rh > 0.05) & (h_base - rh <= 0.0)
    check(f"top={h_top:.0f} base={h_base:.1f} el={el:.0f}", np.array_equal(got, want),
          f"{got.sum()} cells, shadow {d[want].min() if want.any() else 0:.0f}.."
          f"{d[want].max() if want.any() else 0:.0f} m")

print("\n(c) monotone: a higher crown base never adds shadow")
prev = None
for hb in [0.0, 2.0, 4.0, 6.0, 9.0]:
    b = np.where(top > 0, np.float32(hb), np.float32(0.0))
    b = np.minimum(b, top * 0.9)
    m = canopy_mask(top, b, CELL, 70.0, 15.0)
    if prev is not None:
        check(f"base {hb:.0f} m subset of the lower base", bool((m & ~prev).sum() == 0),
              f"{m.sum()} cells ({m.sum() - prev.sum():+d})")
    prev = m

print("\n(d) shade_factor levels")
bld2 = np.zeros_like(top); bld2[140:160, 140:160] = 40.0
s = shade_factor(bld2, top, np.full_like(top, 3.5) * (top > 0), CELL, 70.0, 25.0)
vals = np.unique(s)                                    # float32: compare with isclose
allowed = np.array([0.0, 1.0 - TAU_LEAF, 1.0])
check("only {0, 1-tau, 1} appear",
      bool(np.isclose(vals[:, None], allowed[None, :], atol=1e-6).any(axis=1).all()),
      f"levels {[round(float(v), 4) for v in vals]}")
check("bounded [0,1]", bool(s.min() >= 0.0 and s.max() <= 1.0),
      f"min {s.min():.3f} max {s.max():.3f}")
check("canopy level is 1-TAU_LEAF, not the old 0.7",
      bool(np.isclose(1 - TAU_LEAF, 0.97)), f"1-tau = {1 - TAU_LEAF:.3f}")

print("\n(e) building path: a tower casts h/tan(el), cell for cell")
# Same closed-form comparison as (b). A solid column shadows every cell closer than
# (h - 0.05)/tan(el); the final, partially-shadowed cell is correctly left sunlit, so
# the cast length lands within one cell BELOW the continuous answer, never above it.
M = 900
for h, el in [(100.0, 30.0), (60.0, 45.0), (297.0, 20.0)]:
    t = np.zeros((5, M), np.float32)
    t[:, M - 1] = h
    got = shadow_mask(t, CELL, 90.0, el)[2, :M - 1]
    d = (M - 1 - np.arange(M - 1)) * CELL
    want = (h - d * np.tan(np.radians(el))) > 0.05
    cast = d[got].max() if got.any() else 0.0
    exact = h / np.tan(np.radians(el))
    check(f"h={h:.0f} el={el:.0f}", np.array_equal(got, want),
          f"cast {cast:.0f} m, exact {exact:.1f} m, err {cast - exact:+.1f} m")

print("\n(f) real rasters: crown shadow removed by the trunk gap")
try:
    B = np.load(f"{OUT}/dsm_buildings.npy")
    T = np.load(f"{OUT}/dsm_canopy_v2.npy")
    BS = np.load(f"{OUT}/dsm_canopy_base_v2.npy")
except OSError as e:
    print(f"  SKIP  rasters not built ({e.filename})")
else:
    day = os.environ.get("SHADEME_SUMMER_DATE", "2026-01-26")
    for hh in (7, 12, 18):
        az, el = sun_position(pd.Timestamp(f"{day} {hh:02d}:00", tz="Australia/Melbourne"))
        mb = shadow_mask(B, CELL, az, el)
        m0 = canopy_mask(T, np.zeros_like(T), CELL, az, el) & ~mb
        ms = canopy_mask(T, BS, CELL, az, el) & ~mb
        check(f"{hh:02d}:00 slab is a subset of the extruded crown",
              bool((ms & ~m0).sum() == 0),
              f"{m0.sum()} -> {ms.sum()} cells ({(ms.sum()/max(m0.sum(),1)-1)*100:+.1f}%)")

print("\n" + "=" * 60)
print(f"FAILURES: {len(fails)} {fails if fails else ''}")
