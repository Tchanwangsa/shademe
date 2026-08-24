"""Open-Meteo weather, hourly plus a 15-minute radiation series. No API key. Disk-cached
so the demo survives dead wifi.

THIS MODULE SERVES TODAY. There is no summer mode and no winter mode -- a date is the
only thing any caller ever meant, so a date is what the functions take. `get()` and
`block()` accept "YYYY-MM-DD", or None for today. Only tools/ wants a fixed day, so that
benchmarks reproduce; they pass one explicitly or set SHADEME_DATE.

TWO SERIES, AND THEY ARE NOT THE SAME NUMBERS. Open-Meteo also serves `minutely_15`, and
for THIS cell it splits cleanly in two: temperature_2m at 15 minutes is the exact linear
interpolation of the hourly endpoints (13.4 -> 13.8, 14.1, 14.4 -> 14.7), so it carries
no information, while direct/diffuse radiation carry real sub-hourly structure the hourly
series flattens -- a cloud crossing read 267, 232, 208, 229, 305, 404, 483, 503 W/m2
inside two hours the hourly series reported as 280 and 210. Radiation is the variable
this whole product turns on, so it is taken from minutely_15 and nothing else is.

The two series DISAGREE at shared timestamps (11:00 reads 305 in minutely_15 against 210
hourly): they are different model outputs, not aggregations of each other. So they must
never be mixed inside one solve. slot_rows() below rebuilds the whole clock off the
15-minute series, and block() reports from the same rows the engine marched, so the
radiation quoted in a response is always the radiation that was priced.
"""
import os, json, time, requests

from . import sky as SKY
from . import uv as UV
from ..paths import DATA
from .. import timegrid as TG
from ..timegrid import RAD_STEP_MIN
from .. import clock as CLOCK

LAT, LON = -37.8136, 144.9631
TZ = CLOCK.TZ

# Hotter archive days worth naming: 2026-01-26 (32 C), 2026-01-24 (35.9), 2026-01-27 (43.4).
# Which day is served when a caller passes none is `clock`'s question, not this module's,
# and it is asked on every call rather than snapshotted at import -- `shademe-api --date`
# sets the pin AFTER this module has been imported, so a module-level read cannot see it.

# Legacy season names, for the bench scripts that still pass one. They resolve to a date
# like everything else; they are not a second code path.
LEGACY_DAYS = {"summer": "2026-01-26", "winter": None, "cold": None}


def today():
    return CLOCK.real_today()


def resolve_day(day=None):
    """Whatever a caller passed -> a YYYY-MM-DD string. None/'' means the day the clock
    is standing on: today, or the pinned demo day. See shademe.clock."""
    if day in LEGACY_DAYS:
        day = LEGACY_DAYS[day]
    return str(day) if day else CLOCK.date()


# uv_index_clear_sky rides along because Open-Meteo returns it EQUAL to `uv_index` for
# this cell at every hour, cloud included -- so `uv_index` is not the all-sky value its
# name promises (uv.py attenuates it). Carrying both makes the day they diverge visible.
VARS = ["temperature_2m", "apparent_temperature", "direct_radiation", "diffuse_radiation",
        "cloud_cover", "precipitation", "wind_speed_10m", "uv_index", "uv_index_clear_sky",
        "relative_humidity_2m"]   # humidity: needed by MRT longwave + UTCI, added in the v2 engine
# Open-Meteo defaults wind to km/h; UTCI and the convective term want m/s. Pin the unit
# explicitly and expose BOTH, so the legacy km/h weight and the physics cannot diverge.
# Only radiation. See the module docstring: the rest of minutely_15 is interpolation of
# the hourly series, and asking for it would buy bytes, not information.
MIN15_VARS = ["direct_radiation", "diffuse_radiation"]
WIND_UNIT = "kmh"
CACHE_V = 4                          # bump when VARS changes so old caches are refetched
                                     # v4: minutely_15 radiation
TTL = 600                            # re-fetch only if cache older than 10 min
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# --- Open-Meteo diurnal bias correction ---------------------------------------
# Fitted by `python tools/validate_sensors.py --fit-bias` against twelve City of Melbourne
# microclimate sensors, ~243k site-hours, and written to data/openmeteo_bias.json with its
# own held-out skill table. Read that file's `held_out_skill` before quoting any
# temperature this module returns.
#
# SHAPE. The table is zero-mean over each season's 24 hours, so it cannot move the daily
# mean -- it redistributes it. Open-Meteo's ~9 km cell resolves the synoptic day but not
# the city, and the city damps the diurnal range: the model reads ~1.2 C too cold at 03:00
# and ~1.2 C too warm at 13:00 relative to its own daily mean. Ten of the twelve sites
# agree on the sign at every hour, across park, street and rooftop mounts.
#
# LEVEL. A flat +1.00 C on every hour, and the bigger half of the error by far: 28.0%
# held-out RMSE improvement against CBD sites, against 3.6% for the shape. A 9 km cell
# averages the dense centre with the parkland around it; the centre really is warmer than
# that mean. This was off at first, because the co-located pair at 1 Treasury Place
# disagree with EACH OTHER by 1.0 C -- but that argument is about a PER-SITE level and
# does not survive the pooled test: fit on eleven sites, score on the twelfth, and it
# transfers to 8 of 11. All three failures are explainable (two are large parkland, one is
# the unit already known to be miscalibrated), and parkland NOT showing the offset is
# evidence FOR a heat island, not against it.
#
# SHADEME_BIAS_LEVEL=0 turns the level off and leaves the shape correction running.
BIAS_PATH = os.path.join(DATA, "openmeteo_bias.json")
BIAS_ON = os.environ.get("SHADEME_BIAS", "1") != "0"        # SHADEME_BIAS=0 -> raw feed
BIAS_LEVEL = os.environ.get("SHADEME_BIAS_LEVEL", "1") != "0"   # =0 -> shape only
SEASON_OF = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
             6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}

# Last-resort offline table: real observed 2026-01-14 Melbourne values. Used only when
# there is no network AND no cache; reported as source "fallback-table".
_TABLE = {
    "temperature_2m":      [16.8,16.6,16.3,16.1,16.3,16.1,16.2,16.8,17.8,19.3,20.3,21.7,
                            23.0,23.9,24.1,24.5,25.1,25.1,24.0,22.6,21.7,20.8,20.4,19.5],
    "apparent_temperature":[16.0,16.0,15.9,15.7,16.0,15.9,15.8,16.0,16.9,18.5,19.6,20.8,
                            22.2,23.5,24.3,24.3,24.6,23.8,22.9,22.2,21.3,20.0,19.7,19.2],
    "direct_radiation":    [0,0,0,0,0,0,5,44,80,138,88,124,134,233,511,544,558,466,265,94,8,0,0,0],
    "diffuse_radiation":   [0,0,0,0,0,0,14,71,160,218,345,391,470,465,327,261,160,106,95,63,13,0,0,0],
    "cloud_cover":         [69,30,98,97,99,98,99,100,99,99,100,100,99,97,42,13,3,11,26,10,11,0,0,1],
    "precipitation":       [0.0]*8 + [0.1,0.1,0.1] + [0.0]*13,
    "wind_speed_10m":      [14.2,13.2,12.0,11.9,11.2,10.9,12.0,13.9,14.2,13.2,12.5,13.5,
                            14.4,15.7,17.1,18.6,17.2,16.9,15.8,13.0,12.2,13.8,13.0,11.3],
    "uv_index":            [None]*24,
    "uv_index_clear_sky":  [None]*24,
    # observed 2026-01-14 Melbourne RH%, paired with the temps above
    "relative_humidity_2m":[71,72,74,75,74,75,75,73,69,63,59,55,
                            51,48,47,46,44,45,49,55,58,62,64,67],
}
_WINTER_TABLE = {                    # generic cold+wet day, same last resort
    "temperature_2m":      [10.0]*6 + [10.5,11.0,11.5,12.0,12.5,13.0,13.5,14.0,14.0,13.5,
                                       13.0,12.0,11.5,11.0,11.0] + [10.5]*3,
    "apparent_temperature":[7.5]*6 + [8.0,8.5,9.0,9.5,10.0,10.5,11.0,11.5,11.5,11.0,
                                      10.5,9.5,9.0,8.5,8.5] + [8.0]*3,
    "direct_radiation":    [0,0,0,0,0,0,0,20,40,70,60,50,120,110,90,80,60,20,0,0,0,0,0,0],
    "diffuse_radiation":   [0,0,0,0,0,0,0,20,50,90,110,130,220,220,200,160,110,60,10,0,0,0,0,0],
    "cloud_cover":         [95]*24,
    "precipitation":       [0.2]*24,
    "wind_speed_10m":      [18.0]*24,
    "uv_index":            [0,0,0,0,0,0,0,0.3,1.0,1.5,2.0,2.5,3.0,3.0,2.5,2.0,1.2,0.4,0,0,0,0,0,0],
    "uv_index_clear_sky":  [0,0,0,0,0,0,0,0.3,1.0,1.5,2.0,2.5,3.0,3.0,2.5,2.0,1.2,0.4,0,0,0,0,0,0],
    "relative_humidity_2m":[88]*7 + [86,84,80,76,72,70,68,68,70,74,78,82,85] + [87]*4,
}

_mem = {}


def clamp(lo, hi, v): return max(lo, min(hi, v))


def _cache_path(day): return os.path.abspath(os.path.join(DATA, f"weather_cache_{day}.json"))


def _by_hour(hourly):
    """Open-Meteo hourly arrays -> {hour_of_day: {var: value}} for the first 24h."""
    out = {}
    for i, t in enumerate(hourly.get("time", [])[:24]):
        h = int(t[11:13])
        out[h] = {v: (hourly.get(v) or [None] * 24)[i] for v in VARS}
    return out


def _by_min15(m15):
    """minutely_15 arrays -> {minute_of_day: {var: value}} for the first 24 h.

    Keyed in minutes since midnight, the same unit as timegrid, so a slot indexes it
    directly. 96 rows a day; absent or short is normal (the archive endpoint does not
    serve it) and slot_rows() falls back to the hourly series when it is.
    """
    out = {}
    for i, t in enumerate((m15 or {}).get("time", [])[:96]):
        out[int(t[11:13]) * 60 + int(t[14:16])] = {
            v: ((m15.get(v) or [None] * 96)[i]) for v in MIN15_VARS}
    return out


def _table(tbl, date):
    return {"hours": {h: {v: tbl[v][h] for v in VARS} for h in range(24)}, "date": date}


def _get(url, params):
    params = dict(params, wind_speed_unit=WIND_UNIT)
    r = requests.get(url, params=params, timeout=12)
    r.raise_for_status()
    return r.json()


def _days_ago(day):
    """Whole days between `day` and today, or None if it will not parse."""
    try:
        t = time.mktime(time.strptime(str(day), "%Y-%m-%d"))
        return int((time.time() - t) // 86400)
    except (ValueError, TypeError):
        return None


def _fetch(day):
    """(payload, source) for one YYYY-MM-DD. Raises only if every route fails.

    FORECAST FIRST, ARCHIVE SECOND, and that order fixes a real bug. The archive endpoint
    answers for today -- it does not miss and fall through -- and returns the same
    temperature, cloud and radiation but with uv_index ABSENT. Absent UV was then filled
    in by an estimate off broadband radiation that read UV 6 against 3.6 measured. So the
    endpoint that carries the field is asked first, and the archive covers old dates.
    """
    hourly = ",".join(VARS)
    # minutely_15 only on the forecast endpoint -- the archive 400s on the parameter, so
    # asking it there would cost the whole payload, not just the finer radiation.
    f = (FORECAST, dict(latitude=LAT, longitude=LON, start_date=day, end_date=day,
                        hourly=hourly, minutely_15=",".join(MIN15_VARS), timezone=TZ),
         f"open-meteo forecast {day}")
    a = (ARCHIVE, dict(latitude=LAT, longitude=LON, start_date=day, end_date=day,
                       hourly=hourly, timezone=TZ), f"open-meteo archive {day}")
    # The forecast endpoint serves ~92 days back and 400s on older dates, so ask the
    # one that can answer first rather than printing a traceback on every start-up.
    tries = [f, a] if (_days_ago(day) or 0) <= 90 else [a, f]
    err = None
    for url, params, label in tries:
        try:
            j = _get(url, params)
            hrs = _by_hour(j["hourly"])
            if hrs and any(hrs[h].get("direct_radiation") is not None for h in hrs):
                m15 = _by_min15(j.get("minutely_15"))
                return {"hours": hrs, "min15": m15, "date": day}, label
        except Exception as e:
            err = e
            print(f"[weather] {label} failed: {e}")
    raise RuntimeError(f"no data for {day} ({err})")


def _fallback_table(day):
    """The offline table that fits this DAY, chosen by month. Southern warm season is
    Nov-Mar. Only reachable with no network and no cache, so it is a last resort in every
    sense; the month is simply the one thing available that is not a guess.
    """
    try:
        month = int(str(day)[5:7])
    except (ValueError, IndexError):
        month = time.localtime().tm_mon
    return _TABLE if month in (11, 12, 1, 2, 3) else _WINTER_TABLE


def get(day=None):
    """Cached hourly payload for one day. {'hours':{h:{...}}, 'date':..., 'source':...}

    `day` is YYYY-MM-DD or None for today. A legacy season name still resolves so the
    bench scripts keep running, but it becomes a date here and nothing downstream sees it.
    """
    day = resolve_day(day)
    now = time.time()
    c = _mem.get(day)
    if c is None:
        try:
            c = json.load(open(_cache_path(day)))
            c["hours"] = {int(k): v for k, v in c["hours"].items()}
            c["min15"] = {int(k): v for k, v in (c.get("min15") or {}).items()}
            _mem[day] = c
        except Exception:
            c = None
    if c and c.get("date") != day:
        c = None                                # cache file is for some other day
    if c and now - c.get("ts", 0) < TTL and c.get("cache_v") == CACHE_V:
        return c
    try:
        payload, source = _fetch(day)
        payload.update(ts=now, source=source, cache_v=CACHE_V)
        os.makedirs(os.path.abspath(DATA), exist_ok=True)
        json.dump(payload, open(_cache_path(day), "w"))
        _mem[day] = payload
        return payload
    except Exception as e:
        print(f"[weather] fetch failed ({e})")
        if c:                                   # stale cache beats a lie
            c["source"] = c.get("source", "cache") + " (stale cache, offline)"
            return c
        p = _table(_fallback_table(day), day)
        p.update(ts=now, source="fallback-table (hardcoded, offline - NOT live)")
        _mem[day] = p
        return p


def _f(v, default=0.0):
    return default if v is None else float(v)


def sat_vapour_pressure(t_c):
    """Saturation vapour pressure over water, hPa. Magnus-Tetens with the Sonntag (1990)
    coefficients from the WMO CIMO Guide; ~0.1% over -40..50 C."""
    import math
    return 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))


def vapour_pressure(t_c, rh_pct):
    """Actual vapour pressure, hPa. UTCI's polynomial and Prata's L_sky both want this."""
    return sat_vapour_pressure(t_c) * max(0.0, min(100.0, rh_pct)) / 100.0


_bias_mem = {}


def bias_table():
    """The fitted correction, or None if it has not been fitted on this checkout.

    An absent file is not an error: the engine runs on the raw feed and says so in the
    block's `bias_mode`. A silently-applied correction and a silently-skipped one are the
    same bug, so both states are reported.
    """
    if "t" not in _bias_mem:
        try:
            t = json.load(open(BIAS_PATH))
            if t.get("kind") != "openmeteo-diurnal-bias-v1":
                raise ValueError(f"unexpected bias table kind {t.get('kind')!r}")
            _bias_mem["t"] = t
        except Exception as e:
            print(f"[weather] no diurnal bias correction ({e}); using the raw feed")
            _bias_mem["t"] = None
    return _bias_mem["t"]


def ta_offset(hour, date):
    """Degrees C to SUBTRACT from Open-Meteo's air temperature (offset = model - sensor).

    Returns (offset, mode), where mode names exactly what was applied so the caller can
    put it in the response rather than have the reader infer it from an env var.
    """
    t = bias_table()
    if not BIAS_ON:
        return 0.0, "off (SHADEME_BIAS=0)"
    if t is None:
        return 0.0, "unavailable (run: python tools/validate_sensors.py --fit-bias)"
    # Parse defensively and fall back to the real month: a winter request silently
    # corrected with the summer shape would be worse than no correction.
    try:
        month = int(str(date)[5:7])
        assert 1 <= month <= 12
    except Exception:
        month = time.localtime().tm_mon
    season = SEASON_OF[month]
    off = float(t["seasons"][season][int(hour) % 24])
    if BIAS_LEVEL:
        return off + float(t["level"]), f"season-shape[{season}]+level"
    return off, f"season-shape[{season}]"


def apply_bias(p):
    """A corrected COPY of a payload, with the raw hours kept alongside.

    Applied once, HERE, to the hourly rows -- not inside block(). attach_tsurf() marches
    the surface and facade energy balance straight off `wx["hours"]`, so correcting
    block() alone would leave one solve running two different air temperatures. One Ta,
    one place. The disk cache stays RAW: it is the observation, and a cache with the
    correction baked in could not be re-corrected when the fit is re-run.

    Shifted together: apparent_temperature rides Ta 1:1 (an assumption -- it has no ground
    truth in the CoM archive, but leaving it raw while Ta moves makes the pair
    inconsistent), and relative_humidity is re-derived at CONSTANT vapour pressure, since
    a temperature correction does not add or remove water. That last one independently
    improves RH RMSE against the same held-out sensors (8.95 -> 8.80%), which is a small
    check that the sign is right.
    """
    if p.get("bias") is not None:                    # already corrected, idempotent
        return p
    date = p.get("date", "")
    offs, mode = {}, "off"
    for h in p.get("hours", {}):
        offs[int(h)], mode = ta_offset(int(h), date)
    if not any(offs.values()):
        q = dict(p)
        q["bias"] = {"mode": mode, "offsets": {h: 0.0 for h in offs}, "level": BIAS_LEVEL}
        q["hours_raw"] = p.get("hours", {})
        return q
    hours, raw = {}, p["hours"]
    for h, row in raw.items():
        h = int(h)
        o = offs[h]
        n = dict(row)
        t0 = _f(row.get("temperature_2m"))
        rh0 = row.get("relative_humidity_2m")
        n["temperature_2m"] = t0 - o
        if row.get("apparent_temperature") is not None:
            n["apparent_temperature"] = _f(row["apparent_temperature"]) - o
        if rh0 is not None:
            vp0 = vapour_pressure(t0, float(rh0))
            n["relative_humidity_2m"] = clamp(
                0.0, 100.0, 100.0 * vp0 / sat_vapour_pressure(t0 - o))
        hours[h] = n
    q = dict(p)
    q["hours"], q["hours_raw"] = hours, raw
    q["bias"] = {"mode": mode, "offsets": offs, "level": BIAS_LEVEL,
                 "table": os.path.basename(BIAS_PATH)}
    return q


# --- the half-hour clock the engine marches ------------------------------------

def _interp_hourly(hours, minute, var):
    """Linearly interpolate one hourly variable to a minute-of-day, wrapping midnight.

    NOT an invention: Open-Meteo's own minutely_15 temperature_2m is exactly this
    interpolation of its hourly endpoints, so a half-hour Ta is the number the provider
    would have served anyway. Radiation does NOT come through here -- see slot_rows.

    Caveat, stated because the field exists: precipitation is an hourly ACCUMULATION, so
    interpolating it gives a rate-like number, not a half-hour total. Only the legacy
    w_wet weight reads it and that is off by default; do not start using it as a total.
    """
    h0 = int(minute) // 60 % 24
    h1 = (h0 + 1) % 24
    a, b = (hours.get(h0) or {}).get(var), (hours.get(h1) or {}).get(var)
    if a is None:
        return b
    if b is None:
        return a
    f = (int(minute) % 60) / 60.0
    return float(a) + (float(b) - float(a)) * f


def _rad_over(m15, slot, step):
    """Mean direct/diffuse over [slot, slot+step) from the 15-minute series, or None.

    A MEAN over the slot, not the instantaneous value at its start: the slot is an
    interval the walker spends time in, and the march holds one forcing constant across
    it. Returns None when the series does not cover the interval, which is the signal to
    fall back to the hourly numbers.
    """
    keys = [k for k in range(int(slot), int(slot) + int(step), RAD_STEP_MIN) if k in m15]
    if not keys:
        return None
    out = {}
    for v in MIN15_VARS:
        vals = [m15[k][v] for k in keys if m15[k].get(v) is not None]
        if not vals:
            return None
        out[v] = float(sum(vals)) / len(vals)
    return out


def slot_rows(p, step=None):
    """{slot: row} over the WHOLE 24 h clock at timegrid's step, cached on the payload.

    This is what physics.surface_temp.march() clocks on and what solve() prices from, so
    it is the one place the two series are joined. Radiation comes from minutely_15 where
    it reaches, everything else is interpolated from the hourly rows. `rad_source` on each
    row records which, because a slot silently served hourly radiation and one served
    15-minute radiation are not the same measurement and the response says which it was.

    Pass a payload that has already been through apply_bias(): the correction is a
    temperature one and belongs on the hourly rows it was fitted against, so interpolating
    a corrected Ta is right and correcting an interpolated one would refit nothing.
    """
    step = TG.STEP_MIN if step is None else int(step)
    ck = f"_slots_{step}"
    if p.get(ck):
        return p[ck]
    hours = {int(k): v for k, v in (p.get("hours") or {}).items()}
    m15 = {int(k): v for k, v in (p.get("min15") or {}).items()}
    out = {}
    for slot in range(0, 24 * 60, step):
        row = {v: _interp_hourly(hours, slot, v) for v in VARS}
        rad = _rad_over(m15, slot, step)
        if rad:
            row.update(rad)
        row["rad_source"] = "minutely_15" if rad else "hourly (interpolated)"
        out[slot] = row
    p[ck] = out
    return out


def rad_source(p):
    """One label for how the day's radiation was resolved, for the provenance stamp."""
    rows = slot_rows(p)
    n = sum(1 for r in rows.values() if r["rad_source"] == "minutely_15")
    if n == len(rows):
        return "minutely_15"
    if n == 0:
        return "hourly (interpolated -- no minutely_15)"
    return f"minutely_15 on {n}/{len(rows)} slots, hourly elsewhere"


def _slot_offset(p, slot):
    """The fitted Ta offset at a slot, interpolated between the two bracketing hours.

    The table is a smooth zero-mean diurnal SHAPE fitted per season, so interpolating it
    is reading it at the resolution it was always meant to describe. Stepping it on the
    hour would put a discontinuity in Ta at every :30 that nothing physical produces.
    """
    offs = {int(k): float(v) for k, v in ((p.get("bias") or {}).get("offsets") or {}).items()}
    if not offs:
        return 0.0
    h0, h1 = int(slot) // 60 % 24, (int(slot) // 60 + 1) % 24
    a, b = offs.get(h0), offs.get(h1)
    if a is None or b is None:
        return float(a if a is not None else (b or 0.0))
    return a + (b - a) * ((int(slot) % 60) / 60.0)


def solar_elevation(day, when, minutes=0, tz=TZ):
    """Solar elevation in degrees at `day when`, offset by `minutes`, or None.


    An ImportError must degrade to "no UV" and "no sky state" rather than take the
    weather block down with it, so every caller has to handle None.
    """
    try:
        import pandas as pd
        from ..physics.shadow import sun_position
        slot = TG.as_slot(when)
        t = pd.Timestamp(f"{day} {TG.label(slot)}", tz=tz) + pd.Timedelta(minutes=minutes)
        return float(sun_position(t)[1])
    except Exception:
        return None


# WHICH INSTANT A RADIATION NUMBER STANDS FOR. Open-Meteo labels its hourly rows in a
# FIXED +10:00 for Melbourne -- it returns `utc_offset_seconds: 36000` for a January range
# as well as an August one, so its summer timestamps do not carry AEDT -- and each row is
# the MEAN OVER THE PRECEDING HOUR. So the radiation in row `hh` was collected over
# [hh-1, hh] in +10:00, and the instant that represents it is half an hour before the
# label, in that fixed frame.
#
# Measured rather than assumed. Sweeping the offset and scoring the spread of the
# clear-sky index over clear hours (cloud <= 5%, sun above 20 deg) of Melbourne archive:
#
#     offset      summer (AEDT)        winter (AEST)
#     -60 min     mean 0.95 sd 0.38    mean 0.92 sd 0.12
#     -30 min     mean 0.95 sd 0.30    mean 0.95 sd 0.06   <- winner, AEST frame
#       0 min     mean 1.00 sd 0.14    mean 0.96 sd 0.13
#     +30 min     mean 1.01 sd 0.06    mean 0.94 sd 0.22   <- winner, AEDT frame
#
# The two seasons want offsets exactly one hour apart in a DST-aware frame, and the SAME
# -30 min in the fixed +10:00 frame (sd 0.055 summer, 0.063 winter). That is the DST gap,
# and it is the proof of the fixed frame.
#
# CAVEAT, AND IT IS BIGGER THAN THIS FUNCTION. Everything else in this project reads a
# slot's row as "local time = that slot" -- the shade raster for a slot is cast at that
# local time, and the surface march forces the slot with its own row. Under the frame
# above that is right to within half an hour in winter and an hour out in summer. Fixing
# it moves every temperature, radiation and UTCI figure the project reports and wants its
# own validation, so it has NOT been done here. These two functions exist so the sky glyph
# at least divides by the right clear-sky number; they do not repair the alignment
# underneath it.
FEED_TZ = "Etc/GMT-10"          # fixed +10:00 -- Open-Meteo's frame, DST or not
FEED_ROW_MINUTES = -30          # hourly rows are the mean over the preceding hour


def row_elevation(day, slot, source="hourly", step=None):
    """Solar elevation over the window the radiation on row `slot` was averaged over.

    Two cases, because slot_rows joins two series and they do not cover the same window:

      HOURLY (INTERPOLATED). Each hourly row is centred half an hour before its label in
      FEED_TZ (measured, above); a linear interpolation between two such rows is centred
      half an hour before the interpolation point. So `slot` - 30 min, in FEED_TZ. This is
      the generalisation of the measurement, not a new assumption.

      MINUTELY_15. _rad_over averages the 15-minute rows labelled `slot` and `slot`+15,
      which under the same preceding-window convention together cover [slot-15, slot+15).
      The centre is `slot` itself, and the FEED_ROW_MINUTES shift cancels. Stated as what
      it is: the row-labelling convention was MEASURED for the hourly series and is only
      ASSUMED to carry over to minutely_15, so this branch is good to +/-15 min. It is
      tolerable here because elev_row is only ever a DENOMINATOR for the sky glyph -- it
      does not enter the routing, the march, or any reported figure.
    """
    step = TG.STEP_MIN if step is None else int(step)
    if str(source).startswith("minutely_15"):
        return solar_elevation(day, slot, 0, FEED_TZ)
    return solar_elevation(day, slot, FEED_ROW_MINUTES, FEED_TZ)


def block(when, day=None, now_min=None, wet=False):
    """The weather block for one SLOT of one day. `when` is a slot, an hour, or 'HH:MM'.

    Read off slot_rows(), not off the hourly rows, so the radiation reported here is the
    radiation the engine marched -- see the module docstring on the two series.

    `now_min` is the wall-clock minute-of-day, and it is what lets the UV index be a
    MEASUREMENT rather than a model: the live ARPANSA reading can only answer for right
    now. None means "do not claim anything is live" and gives the modelled branch.

    There is no `beam` flag any more. It zeroed the radiation outside a 06:00-20:00
    window, because the clamp served a 21:30 walk off the 20:00 slot and priced it on a
    beam that had set at 20:44. With the clamp gone the row for 21:30 is 21:30's own, and
    its direct radiation is zero because the sun is down -- the feed says so and does not
    have to be corrected into saying it. What the sun's POSITION still asserts here is the
    UV floor below the horizon; see the `elev` gate below.

    `wet` restores the legacy precipitation/wind weight, which only the old `edge_cost`
    path reads. The physical engine uses neither w_heat nor w_wet.
    """
    p = apply_bias(get(day))
    rows = slot_rows(p)
    slot = TG.as_slot(when)
    h = slot if slot in rows else TG.nearest(slot, rows)
    r = rows[h]
    direct, diffuse = _f(r["direct_radiation"]), _f(r["diffuse_radiation"])
    tot = direct + diffuse
    direct_fraction = (direct / tot) if tot > 0 else 0.0
    app = _f(r["apparent_temperature"])
    precip, wind = _f(r.get("precipitation")), _f(r.get("wind_speed_10m"))
    temp = _f(r.get("temperature_2m"))
    # RH may be absent in a pre-v2 cache or the offline table -> fall back, but SAY so.
    rh_raw = r.get("relative_humidity_2m")
    rh = 50.0 if rh_raw is None else float(rh_raw)

    # The uncorrected comparison, interpolated to the same slot so the pair is like for
    # like: quoting a corrected 13:30 against a raw 13:00 would read as bias.
    raw_hours = {int(k): v for k, v in (p.get("hours_raw") or p.get("hours") or {}).items()}
    temp_raw = _f(_interp_hourly(raw_hours, h, "temperature_2m"))
    app_raw = _f(_interp_hourly(raw_hours, h, "apparent_temperature"))
    _rh_raw = _interp_hourly(raw_hours, h, "relative_humidity_2m")
    rh_uncorr = 50.0 if _rh_raw is None else float(_rh_raw)
    off = _slot_offset(p, h)
    bias_mode = (p.get("bias") or {}).get("mode", "off")
    vp = vapour_pressure(temp, rh)
    wind_ms = wind / 3.6                       # WIND_UNIT is pinned to km/h upstream
    w_heat = clamp(0.0, 3.0, (app - 20.0) / 5.0) * direct_fraction
    w_wet = clamp(0.0, 3.0, precip * 2.0 + wind / 15.0)
    # UV IS NEVER ESTIMATED FROM RADIATION -- see uv.py. If neither the live network
    # nor the feed can answer, this is None and the client shows nothing.
    uv_feed = r.get("uv_index")
    if uv_feed is None:
        uv_feed = r.get("uv_index_clear_sky")
    cloud = _f(r.get("cloud_cover"))
    # Two solar elevations, because two different questions are asked of the sun.
    # `elev` is the instant this slot is priced at -- the same instant the shade raster
    # for it was cast at -- and answers "is the sun up". `elev_row` is the window the
    # radiation above was averaged over, and is the only elevation the beam may be
    # divided by. See row_elevation for why they differ. Both are None without pvlib.
    elev = solar_elevation(p.get("date"), h)
    elev_row = row_elevation(p.get("date"), h, r.get("rad_source", "hourly"))
    uv, uv_source = UV.index_for(h, now_min if now_min is not None else -1,
                                 None if uv_feed is None else float(uv_feed), cloud,
                                 elev_deg=elev)
    # THE SUN SETS THE UV TO ZERO, and it says so. The feed reports 0 UV at night and the
    # ARPANSA reading is ~0, so this is belt-and-braces -- but it used to be asserted by
    # the 06:00-20:00 window, and with the window gone it has to be asserted by the sun or
    # not at all. The HORIZON, not SUN_MIN_DEG: there is real erythemal UV at 3 deg of
    # elevation, which is above the horizon and below the shadow sweep's give-up angle.
    if elev is not None and elev <= 0.0:
        uv, uv_source = 0.0, f"sun {elev:.1f} deg -- below the horizon"
    cond, cond_why = SKY.condition(direct, diffuse, cloud, precip, elev, elev_row)
    if not wet:
        w_wet = 0.0                              # legacy weight; off unless asked for
    return {
        "date": p.get("date"),
        "slot": h,
        "time": TG.label(h),
        "hour": TG.hour_of(h),                           # legacy field, whole hours only
        "rad_source": r.get("rad_source", "hourly"),
        # The sky glyph and the number that chose it. NOT derived from cloud cover except
        # in the branch `condition_source` names as such -- see api/sky.py.
        "condition": cond,
        "condition_source": cond_why,
        "solar_elevation": None if elev is None else round(elev, 1),
        "solar_elevation_row": None if elev_row is None else round(elev_row, 1),
        "beam_fraction": (lambda k: None if k is None else round(k, 3))(
            SKY.beam_fraction(direct, elev_row)),
        "beam_clear": round(SKY.beam_clear(elev_row), 1),
        "temperature": round(temp, 1),
        "apparent_temperature": round(app, 1),
        "direct_radiation": round(direct, 1),
        "diffuse_radiation": round(diffuse, 1),
        "cloud_cover": round(cloud, 1),
        "uv_index": uv,
        "uv_index_feed": None if uv_feed is None else round(float(uv_feed), 1),
        "uv_source": uv_source,
        "precipitation": round(precip, 2),
        "wind_speed": round(wind, 1),                    # km/h, legacy field, do not repurpose
        "wind_speed_ms": round(wind_ms, 2),              # m/s at 10 m -- use this for physics
        "relative_humidity": round(rh, 1),
        "rh_is_fallback": rh_raw is None,
        "vapour_pressure_hpa": round(vp, 2),             # invariant under the correction
        "ta_bias_offset": round(off, 3),                 # degC SUBTRACTED from the feed
        "bias_mode": bias_mode,
        "temperature_raw": round(temp_raw, 1),           # what Open-Meteo actually said
        "apparent_temperature_raw": round(app_raw, 1),
        "relative_humidity_raw": round(rh_uncorr, 1),
        "direct_fraction": round(direct_fraction, 3),
        "w_heat": round(w_heat, 3),
        "w_wet": round(w_wet, 3),
        "source": f"{p['source']} | {p['date']} {TG.label(h)} {TZ} | rad {r.get('rad_source')}",
    }
