"""Checks for the half-hour time grid: the window, the clamp, and dt invariance.

Run: python tests/test_timegrid.py

The expensive one is (a): it re-derives the 06-20 window from pvlib over every hour of a
year rather than trusting the constant, because that window is an assertion about the sun
and the only way it stays true is by being checked against the sun.
"""
import numpy as np
import pandas as pd
import pvlib

from shademe import timegrid as TG
from shademe.physics import surface_temp as st
from shademe.api import cost
from shademe.api.weather import _interp_hourly as _interp

LAT, LON, TZ = -37.8136, 144.9631, "Australia/Melbourne"
FAIL = []


def check(name, ok, msg=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + msg) if msg else ''}")
    if not ok:
        FAIL.append(name)


print("\n(a) the window is exactly the hours the sun can be up, over a whole year")
idx = pd.date_range("2026-01-01", "2026-12-31 23:00", freq="h", tz=TZ)
el = pvlib.solarposition.get_solarposition(idx, LAT, LON)["apparent_elevation"].values
lit = sorted({h for h, e in zip(idx.hour, el) if e > 0})
check("every hour with sun is inside the window",
      all(TG.FIRST_MIN <= h * 60 <= TG.LAST_MIN for h in lit), f"lit hours {lit[0]}..{lit[-1]}")
check("no hour inside the window is dark all year",
      set(lit) == set(range(TG.FIRST_MIN // 60, TG.LAST_MIN // 60 + 1)),
      f"{len(lit)} lit hours vs {TG.LAST_MIN // 60 - TG.FIRST_MIN // 60 + 1} in window")
for h in (TG.FIRST_MIN // 60 - 1, TG.LAST_MIN // 60 + 1):
    hi = max(e for hh, e in zip(idx.hour, el) if hh == h)
    check(f"{h:02d}:00 is below the horizon on all 365 days", hi <= 0.0,
          f"max elevation {hi:.2f} deg")

print("\n(b) slots, labels and the as_slot ambiguity")
check("29 slots at 30 min", len(TG.SLOTS) == 29 and TG.STEP_MIN == 30)
check("hhmm sorts lexically in a directory",
      [TG.hhmm(s) for s in TG.SLOTS] == sorted(TG.hhmm(s) for s in TG.SLOTS))
check("as_slot reads an int hour as an hour", TG.as_slot(13) == 780)
check("as_slot passes a slot through", TG.as_slot(810) == 810)
check("as_slot parses HH:MM", TG.as_slot("13:30") == 810)
check("label round-trips", all(TG.as_slot(TG.label(s)) == s for s in TG.SLOTS))

print("\n(c) the clamp snaps to the NEAREST slot and reports the window honestly")
cases = [(13 * 60 + 52, 840, True), (13 * 60 + 44, 810, True),
         (1 * 60 + 37, TG.FIRST_MIN, False), (21 * 60 + 30, TG.LAST_MIN, False),
         (20 * 60 + 10, TG.LAST_MIN, True), (5 * 60 + 50, TG.FIRST_MIN, True)]
for minute, want, lit_want in cases:
    raw = TG.snap(minute)
    got, in_win = TG.clamp(raw), TG.in_window(raw)
    check(f"{minute // 60:02d}:{minute % 60:02d} -> {TG.label(got)}, sun up={in_win}",
          got == want and in_win == lit_want,
          f"got {TG.label(got)} lit={in_win}")
check("nearest never rounds more than half a step away",
      max(abs(TG.snap(m) - m) for m in range(0, 1440)) <= TG.STEP_MIN // 2)

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
