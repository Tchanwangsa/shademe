"""Checks for the half-hour time grid: the sun gate, the clock, and dt invariance.

Run: python tests/test_timegrid.py

The expensive one is (a). There USED to be a 06:00-20:00 window here and this test
re-derived it from pvlib over every hour of a year. The window is gone -- what decides
whether a slot has a raster is now shadow.SUN_MIN_DEG on the day being priced -- so (a)
checks the claim that replaced it, and checks it the same way: over all 17,520 half-hour
slots of 2026, from pvlib, rather than trusting the numbers quoted in the README.

The claim is that widening to 24 h COSTS NOTHING. That is only true because the sun gate
is a strict subset of the window it replaced, and that is a fact about the sun which
nothing in the code enforces -- so it is checked against the sun.
"""
import numpy as np
import pandas as pd
import pvlib

from shademe import timegrid as TG
from shademe.physics import shadow
from shademe.physics.shadow import SUN_MIN_DEG
from shademe.physics import surface_temp as st
from shademe.api import cost
from shademe.api.weather import _interp_hourly as _interp

LAT, LON, TZ = -37.8136, 144.9631, "Australia/Melbourne"
FAIL = []


def check(name, ok, msg=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + msg) if msg else ''}")
    if not ok:
        FAIL.append(name)


print("\n(a) the sun gate is a strict subset of the window it replaced, all year")
OLD_FIRST, OLD_LAST, OLD_N = 6 * 60, 20 * 60, 29     # the window this test used to assert
idx = pd.date_range("2026-01-01", "2026-12-31 23:30", freq=f"{TG.STEP_MIN}min", tz=TZ)
el = pvlib.solarposition.get_solarposition(idx, LAT, LON)["apparent_elevation"].values
minute = idx.hour * 60 + idx.minute
lit = minute[el >= SUN_MIN_DEG]

check("every slot the sweep can resolve is inside the old window",
      lit.min() >= OLD_FIRST and lit.max() <= OLD_LAST,
      f"sunlit slots span {TG.label(lit.min())}..{TG.label(lit.max())}")
# The one that matters for disk: per DAY, never more files than the window wrote.
per_day = pd.Series(el >= SUN_MIN_DEG, index=idx).groupby(idx.date).sum()
check("no day needs more rasters than the old window wrote",
      per_day.max() <= OLD_N, f"worst day {per_day.max()} vs {OLD_N}")
check("and most days need far fewer",
      per_day.mean() < OLD_N - 4, f"mean {per_day.mean():.1f} rasters/day vs {OLD_N}")
# The four days quoted in the README and in pipeline.shade's header.
for day, want in (("2026-06-21", 17), ("2026-08-24", 20),
                  ("2026-01-26", 27), ("2026-12-21", 28)):
    got = int(per_day[pd.Timestamp(day).date()])
    check(f"{day} needs {want} rasters", got == want, f"got {got}")
# The gate is the SWEEP's, not a second copy of it. If shadow.SUN_MIN_DEG moves, the set
# on disk moves with it and this test is the thing that notices.
check("the gate is shadow.SUN_MIN_DEG itself", SUN_MIN_DEG == 5.0, f"{SUN_MIN_DEG}")
check("a slot below the gate is fully shaded by the sweep",
      bool(shadow.shadow_mask(np.zeros((4, 4), np.float32), 2.0, 0.0,
                              SUN_MIN_DEG - 0.1).all()))

print("\n(b) slots, labels and the as_slot ambiguity")
check("48 slots at 30 min -- the whole clock",
      len(TG.SLOTS) == 48 and TG.STEP_MIN == 30 and TG.SLOTS[0] == 0,
      f"{len(TG.SLOTS)} slots, first {TG.label(TG.SLOTS[0])}")
check("SLOTS and the march CLOCK are one list", TG.CLOCK is TG.SLOTS)
check("hhmm sorts lexically in a directory",
      [TG.hhmm(s) for s in TG.SLOTS] == sorted(TG.hhmm(s) for s in TG.SLOTS))
check("as_slot reads an int hour as an hour", TG.as_slot(13) == 780)
check("as_slot passes a slot through", TG.as_slot(810) == 810)
check("as_slot parses HH:MM", TG.as_slot("13:30") == 810)
check("label round-trips", all(TG.as_slot(TG.label(s)) == s for s in TG.SLOTS))

print("\n(c) snapping is to the NEAREST slot, wraps at midnight, and never clamps")
cases = [(13 * 60 + 52, 840), (13 * 60 + 44, 810),
         (1 * 60 + 37, 90),                        # 01:37 -> 01:30. Used to clamp to 06:00.
         (21 * 60 + 30, 1290),                     # 21:30 -> itself. Used to clamp to 20:00.
         (23 * 60 + 50, 0),                        # wraps, rather than running off the end
         (5 * 60 + 50, 360)]
for m, want in cases:
    got = TG.snap(m)
    check(f"{m // 60:02d}:{m % 60:02d} -> {TG.label(want)}", got == want,
          f"got {TG.label(got)}")
check("every snap lands on a real slot", all(TG.snap(m) in TG.SLOTS for m in range(1440)))
check("nearest never rounds more than half a step away",
      max(min(abs(TG.snap(m) - m), 1440 - abs(TG.snap(m) - m))
          for m in range(0, 1440)) <= TG.STEP_MIN // 2)
check("the clamp is gone", not hasattr(TG, "clamp") and not hasattr(TG, "in_window"))

print("\n(d) dt does NOT change when the clock is refined -- the whole point")
# The finer clock is built the way production builds it: weather._interp_hourly, i.e.
# linear between the hourly endpoints. Repeating each hourly row twice instead would be
# a STEP forcing against the hourly clock's trapezoid, which is a different forcing and
# would be testing the interpolation rather than the timestep.
w = {h: {"temperature_2m": 20.0 + 5 * np.sin(h / 24 * 6.28), "direct_radiation": 400.0,
         "diffuse_radiation": 100.0, "cloud_cover": 10.0, "wind_speed_10m": 10.0,
         "relative_humidity_2m": 50.0} for h in range(24)}
svf, mid = np.ones((4, 4), np.float32), np.zeros((4, 4), np.uint8)
props = {"0": {"albedo": 0.15, "emissivity": 0.95, "rho_c_d": 1.5e5}}
d1, d2 = {}, {}
o1 = st.march({}, svf, mid, props, w, hours=[12], diag=d1)
VARS = ["temperature_2m", "direct_radiation", "diffuse_radiation",
        "cloud_cover", "wind_speed_10m", "relative_humidity_2m"]
w2 = {s: {v: _interp(w, s, v) for v in VARS} for s in TG.CLOCK}
o2 = st.march({}, svf, mid, props, w2, hours=[720], diag=d2)
check("hourly clock integrates at SUB_DT", d1["dt"] == st.SUB_DT, f"dt={d1['dt']}")
check("half-hourly clock integrates at the SAME dt", d2["dt"] == d1["dt"],
      f"{d1['dt']} vs {d2['dt']}  (n_sub {d1['n_sub']} vs {d2['n_sub']})")
dk = float(abs(o1[12] - o2[720]).max())
check("and the same forcing gives the same Ts to within 0.05 K", dk < 0.05, f"max|dTs| {dk:.4f} K")

print("\n(e) a wall march clocked either way agrees too")
sun = {h: (180.0, 40.0) for h in range(24)}
a = st.wall_march(w, sun, hours=[12])[12]
b = st.wall_march(w2, {s: (180.0, 40.0) for s in TG.CLOCK}, hours=[720])[720]
check("wall Ts agrees across the two clocks", float(np.abs(a - b).max()) < 0.05,
      f"max|dTs| {float(np.abs(a - b).max()):.4f} K")

print("\n(f) edge_shade puts a slot and an hourly pickle on the same grid")
d = {"shade": {h: h / 100 for h in range(6, 21)}}
check("13:30 reads the 13:00 key, not 20:00", cost.edge_shade(d, 810) == 0.13,
      f"got {cost.edge_shade(d, 810)}")
check("survives a json round-trip",
      cost.edge_shade({"shade": {str(h): h / 100 for h in range(6, 21)}}, 810) == 0.13)
check("an applied _shade still wins", cost.edge_shade({"_shade": 0.42, "shade": {6: 0.1}}, 810) == 0.42)
check("a legacy hour argument still works", cost.edge_shade(d, 13) == 0.13)

print("\n" + "=" * 60)
print(f"FAILURES: {len(FAIL)}" + ("  " + ", ".join(FAIL) if FAIL else ""))
raise SystemExit(1 if FAIL else 0)
