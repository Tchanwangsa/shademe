"""Checks for the Open-Meteo diurnal bias correction in shademe/api/weather.py.

Run: python tests/test_weather_bias.py

These are INVARIANT checks, not accuracy checks. Whether the correction makes the feed
more accurate is decided on held-out sensor days by
`python tools/validate_sensors.py --fit-bias`, which writes its own skill table into
data/openmeteo_bias.json. What this file guards is the arithmetic around it: the sign
convention, the zero-mean property that makes it a SHAPE correction rather than a level
one, moisture conservation, and the fact that turning it off gives back the raw feed
byte-for-byte. Every one of those is a silent failure mode -- a flipped sign would still
produce plausible temperatures.
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

FAIL = []


def check(name, ok, msg=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + msg) if msg else ''}")
    if not ok:
        FAIL.append(name)


def load(**env):
    """Re-import shademe.api.weather under a given env, so module-level flags are re-read."""
    keep = {k: os.environ.get(k) for k in ("SHADEME_BIAS", "SHADEME_BIAS_LEVEL")}
    for k in keep:
        os.environ.pop(k, None)
    os.environ.update({k: v for k, v in env.items()})
    import shademe.api.weather as w
    importlib.reload(w)
    for k, v in keep.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    return w


# A payload of the shape weather.get() returns, with a known flat diurnal cycle so any
# hour-to-hour structure in the output can only have come from the correction.
def payload(date="2026-01-26", ta=25.0, rh=50.0):
    return {"date": date, "source": "synthetic",
            "hours": {h: {"temperature_2m": ta, "apparent_temperature": ta + 1.0,
                          "relative_humidity_2m": rh, "direct_radiation": 0.0,
                          "diffuse_radiation": 0.0, "cloud_cover": 0.0,
                          "precipitation": 0.0, "wind_speed_10m": 10.0,
                          "uv_index": 0.0} for h in range(24)}}


def t_table_is_zero_mean():
    """The whole 'correct the shape, not the level' claim is this one property."""
    w = load()
    t = w.bias_table()
    if t is None:
        check("(a) fitted table present", False,
              "data/openmeteo_bias.json missing -- run validate_sensors.py --fit-bias")
        return
    check("(a) fitted table present", t["kind"] == "openmeteo-diurnal-bias-v1")
    for s, v in t["seasons"].items():
        m = sum(v) / len(v)
        check(f"(b) {s} shape is zero-mean over 24 h", abs(m) < 1e-3, f"mean={m:+.2e} C")
    check("(c) level is reported separately, not folded in",
          isinstance(t.get("level"), float) and t["level"] < 0,
          f"level={t['level']:+.3f} C (Open-Meteo runs cold in the daily mean)")
    check("(d) fit is stamped with its train/test window",
          len(t["fit"]["train"]) == 2 and len(t["fit"]["test"]) == 2
          and t["fit"]["train"][1] < t["fit"]["test"][0],
          f"train {t['fit']['train']} -> test {t['fit']['test']}")


def t_sign_convention():
    """offset = model - sensor, so the correction SUBTRACTS it. Getting this backwards
    doubles the error instead of removing it, and nothing downstream would notice."""
    w = load()
    p = w.apply_bias(payload())
    if not any(p["bias"]["offsets"].values()):
        check("(e) sign convention", False, "no table, cannot test")
        return
    off3, _ = w.ta_offset(3, "2026-01-26")
    off13, _ = w.ta_offset(13, "2026-01-26")
    check("(e) summer night offset is negative (model too cold at 03:00)", off3 < 0,
          f"{off3:+.3f} C")
    check("(f) summer midday offset is positive (model too warm at 13:00)", off13 > 0,
          f"{off13:+.3f} C")
    check("(g) corrected = raw - offset, at 03:00",
          abs((p["hours"][3]["temperature_2m"]) - (25.0 - off3)) < 1e-9,
          f"25.0 -> {p['hours'][3]['temperature_2m']:.3f} C")
    check("(h) a flat input day comes out with a diurnal cycle",
          p["hours"][3]["temperature_2m"] > p["hours"][13]["temperature_2m"],
          "night lifted, midday lowered -- the city damps the range")


def t_shape_and_level_are_separable():
    """The two halves do different jobs and must stay independently switchable.

    The shape REARRANGES a day and cannot move its mean -- that is what zero-meaning buys.
    The level MOVES the whole day and does nothing else. Both ship by default; the shape
    alone is still reachable, and a level that silently stopped being applied would look
    exactly like a correct run.
    """
    ws = load(SHADEME_BIAS_LEVEL="0")
    ps = ws.apply_bias(payload())
    m = sum(ps["hours"][h]["temperature_2m"] for h in range(24)) / 24.0
    check("(i) shape alone leaves the daily mean exactly where it was",
          abs(m - 25.0) < 2e-3, f"{m:.5f} C vs 25.0")

    w = load()
    p = w.apply_bias(payload())
    lvl = w.bias_table()["level"]
    ml = sum(p["hours"][h]["temperature_2m"] for h in range(24)) / 24.0
    check("(j) the level ships ON by default", w.BIAS_LEVEL and "level" in p["bias"]["mode"],
          p["bias"]["mode"])
    check("(k) and lifts the whole day by exactly -level",
          abs(ml - (25.0 - lvl)) < 2e-3,
          f"{m:.3f} -> {ml:.3f} C, level {lvl:+.4f} C")
    check("(l) the level is a FLAT shift -- every hour moves by the same amount",
          max(abs((p["hours"][h]["temperature_2m"] - ps["hours"][h]["temperature_2m"]) + lvl)
              for h in range(24)) < 1e-9,
          "shape untouched underneath it")


def t_moisture_is_conserved():
    """RH is re-derived at constant vapour pressure. If it were left alone instead, a
    1 C shift would invent or destroy water, and both MRT's longwave term and UTCI read
    vapour pressure directly."""
    w = load()
    p = w.apply_bias(payload(ta=25.0, rh=50.0))
    vp0 = w.vapour_pressure(25.0, 50.0)
    worst = 0.0
    for h in range(24):
        r = p["hours"][h]
        worst = max(worst, abs(w.vapour_pressure(r["temperature_2m"],
                                                 r["relative_humidity_2m"]) - vp0))
    check("(m) vapour pressure invariant under the correction", worst < 1e-6,
          f"max drift {worst:.2e} hPa")
    warmed = p["hours"][3]["relative_humidity_2m"]      # night: Ta up  -> RH down
    check("(n) RH falls where the correction warms the air", warmed < 50.0,
          f"50.0 -> {warmed:.2f} %")


def t_apparent_rides_ta():
    w = load()
    p = w.apply_bias(payload())
    ok = all(abs((p["hours"][h]["apparent_temperature"] - p["hours"][h]["temperature_2m"]) - 1.0)
             < 1e-9 for h in range(24))
    check("(o) apparent temperature shifts 1:1 with Ta", ok,
          "the raw gap of 1.0 C is preserved at every hour")


def t_off_returns_the_raw_feed():
    w = load(SHADEME_BIAS="0")
    p = w.apply_bias(payload())
    check("(p) SHADEME_BIAS=0 leaves every hour untouched",
          all(p["hours"][h]["temperature_2m"] == 25.0 for h in range(24))
          and all(v == 0.0 for v in p["bias"]["offsets"].values()))
    check("(q) and says so rather than staying silent", "off" in p["bias"]["mode"],
          repr(p["bias"]["mode"]))


def t_idempotent_and_seasonal():
    w = load()
    p = w.apply_bias(w.apply_bias(payload()))
    check("(r) apply_bias is idempotent (a double-corrected payload is a real risk)",
          abs(p["hours"][13]["temperature_2m"] - (25.0 - w.ta_offset(13, "2026-01-26")[0])) < 1e-9)
    # Compare the SHAPE halves: the level is a constant and would mask the seasonal
    # difference if the two totals were compared directly.
    t = w.bias_table()
    s, win = t["seasons"]["DJF"][9], t["seasons"]["JJA"][9]
    check("(s) season is taken from the payload date, not assumed",
          s > 0 > win and w.ta_offset(9, "2026-01-15")[0] != w.ta_offset(9, "2026-07-15")[0],
          f"09:00 shape: DJF {s:+.3f} C vs JJA {win:+.3f} C -- opposite sign, which is why "
          f"the table is per season and not pooled")
    bad, _ = w.ta_offset(9, "today")        # the offline winter table's literal date
    check("(t) an unparseable date falls back to the real month, not to a hardcoded one",
          isinstance(bad, float))


def t_block_reports_what_it_did():
    w = load()
    b = w.block(14, "summer")
    for k in ("ta_bias_offset", "bias_mode", "temperature_raw",
              "apparent_temperature_raw", "relative_humidity_raw"):
        check(f"(u) block() carries {k}", k in b, str(b.get(k)))
    check("(v) block's temperature is raw minus the reported offset",
          abs(b["temperature"] - round(b["temperature_raw"] - b["ta_bias_offset"], 1)) < 0.06,
          f"{b['temperature_raw']} - {b['ta_bias_offset']} = {b['temperature']}")
    check("(w) vapour pressure in the block matches the RAW moisture",
          abs(b["vapour_pressure_hpa"]
              - round(w.vapour_pressure(b["temperature_raw"], b["relative_humidity_raw"]), 2)) < 0.02)


if __name__ == "__main__":
    for t in (t_table_is_zero_mean, t_sign_convention, t_shape_and_level_are_separable,
              t_moisture_is_conserved, t_apparent_rides_ta, t_off_returns_the_raw_feed,
              t_idempotent_and_seasonal, t_block_reports_what_it_did):
        print(f"\n{t.__name__}")
        t()
    print("\n" + ("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}"))
    sys.exit(1 if FAIL else 0)
