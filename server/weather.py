"""Open-Meteo weather -> cost weights. No API key. Disk-cached so the demo survives dead wifi."""
import os, json, time, requests

from . import uv as UV

LAT, LON = -37.8136, 144.9631
TZ = "Australia/Melbourne"

# THIS MODULE SERVES TODAY. There is no summer mode and no winter mode: `mode` used to
# name a season, the season chose a demo day, and the live API had to reach in and
# reassign the constant at import to stop itself pricing an August walk on January
# weather. A date is the only thing any caller actually meant, so a date is what the
# functions take -- `get()` / `block()` accept "YYYY-MM-DD", or None for today.
#
# Only scripts/ still want a fixed day, so that benchmarks reproduce; they get it by
# passing one explicitly, or by setting SHADEME_DATE. Nothing pins itself by default.
# Hotter archive days worth naming: 2026-01-26 (32 C), 2026-01-24 (35.9), 2026-01-27 (42.8).
PINNED_DATE = os.environ.get("SHADEME_DATE") or os.environ.get("SHADEME_SUMMER_DATE")

# Legacy season names, for the two bench scripts that still pass one. They resolve to a
# date like everything else; they are not a second code path.
LEGACY_DAYS = {"summer": "2026-01-26", "winter": None, "cold": None}


def today():
    return time.strftime("%Y-%m-%d", time.localtime())


def resolve_day(day=None):
    """Whatever a caller passed -> a YYYY-MM-DD string. None/'' means today."""
    if day in LEGACY_DAYS:
        day = LEGACY_DAYS[day]
    return str(day) if day else (PINNED_DATE or today())


# uv_index_clear_sky rides along because Open-Meteo returns it EQUAL to `uv_index` for
# this cell at every hour of the day, cloud cover included -- so `uv_index` is not the
# all-sky value its name promises. server/uv.py attenuates it. Both are carried so the
# day the feed starts distinguishing them, the difference is visible rather than assumed.
VARS = ["temperature_2m", "apparent_temperature", "direct_radiation", "diffuse_radiation",
        "cloud_cover", "precipitation", "wind_speed_10m", "uv_index", "uv_index_clear_sky",
        "relative_humidity_2m"]   # humidity: needed by MRT longwave + UTCI, added in the v2 engine
# Open-Meteo defaults wind to km/h. That default was previously implicit, which is a trap:
# UTCI and the convective term both want m/s. Pin the unit explicitly and expose BOTH,
# so the legacy W_wet (tuned on km/h) and the physics (m/s) can never diverge silently.
WIND_UNIT = "kmh"
CACHE_V = 3                          # bump when VARS changes so old caches are refetched
TTL = 600                            # re-fetch only if cache older than 10 min
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# --- Open-Meteo diurnal bias correction (stage 01) ------------------------------------
# Fitted by `python scripts/validate_sensors.py --fit-bias` against twelve City of
# Melbourne microclimate sensors, ~243k site-hours, and written to data/openmeteo_bias.json
# with its own held-out skill table. Read that file's `held_out_skill` before quoting any
# temperature this module returns; the docstring on fit_diurnal_bias() carries the why.
#
# WHAT IS CORRECTED: the SHAPE of the daily cycle only. The table is zero-mean over each
# season's 24 hours, so it cannot move the daily mean temperature -- it redistributes it.
# Open-Meteo's ~9 km cell resolves the synoptic day but not the city, and the city damps
# the diurnal range: nights are held up by released heat, so the model reads ~1.2 C too
# cold at 03:00 relative to its own daily mean and ~1.2 C too warm at 13:00. Ten of the
# twelve sites agree on the sign at every hour, across park, street and rooftop mounts.
# Nothing about a dozen independent instruments drifting would be synchronised to the clock.
#
# ALSO CORRECTED: the LEVEL, a flat +1.00 C on every hour. Open-Meteo reads 1.00 C colder
# than the sensor network in the daily mean, and this is the bigger half of the error by
# far -- 28.0% held-out RMSE against CBD sites, against 3.6% for the shape. A 9 km cell
# averages the dense centre together with the parkland and suburbs around it and reports
# the mean; the centre really is warmer than that mean. The model is not wrong about the
# weather, it is wrong about where it is reporting the weather for.
#
# This was OFF at first, on the reasoning that the co-located pair at 1 Treasury Place
# disagree with EACH OTHER by 1.0 C (validate_sensors.PAIR_FINDING), so a level that size
# could be one instrument's calibration rather than a model error. That reasoning holds
# for a PER-SITE level and does not survive the pooled test: fit the level on eleven sites
# and score it on the twelfth, and it transfers to 8 of 11. All three failures are
# explainable -- Royal Park (+0.14 C) and Birrarung Marr (-0.51 C) are large parkland, and
# the third is the unit already known to be miscalibrated. Parkland NOT showing the offset
# is the evidence for a heat island, not against it; instrument drift does not sort itself
# by land cover.
#
# The level is the all-twelve-sites figure (1.0004 C). Restricting the fit to the nine
# dense-centre sites gives 1.179 C and scores identically on held-out centre readings, so
# the pooled number is used: same accuracy, no judgement call about which sites to drop.
# SHADEME_BIAS_LEVEL=0 turns the level off and leaves the shape correction running.
BIAS_PATH = os.path.join(DATA, "openmeteo_bias.json")
BIAS_ON = os.environ.get("SHADEME_BIAS", "1") != "0"        # SHADEME_BIAS=0 -> raw feed
BIAS_LEVEL = os.environ.get("SHADEME_BIAS_LEVEL", "1") != "0"   # =0 -> shape only
SEASON_OF = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
             6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}

# Last-resort offline table: real observed 2026-01-14 Melbourne values (Open-Meteo archive),
# incl. the two anchor hours documented in TECHNICAL_PLAN.md (10:00 88/345/100, 16:00 558/160/3).
# Only used when there is no network AND no cache. Reported as source "fallback-table".
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
_WINTER_TABLE = {                    # generic cold+wet Melbourne day, only if offline with no cache
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
    """Returns (payload, source) for one YYYY-MM-DD. Raises only if every route fails.

    FORECAST FIRST, ARCHIVE SECOND, and that order is the fix for a real bug rather than
    a preference. The archive endpoint answers for today -- it does not "miss and fall
    through" the way the API layer used to assume -- and it returns the same temperature,
    cloud and radiation as the forecast endpoint but with **uv_index absent**. Absent UV
    was then filled in by an estimate off broadband radiation, which read UV 6 in
    Melbourne in late August against 3.6 measured. So the endpoint that actually carries
    the field is asked first, and the archive is the fallback for old dates it alone has.
    """
    hourly = ",".join(VARS)
    f = (FORECAST, dict(latitude=LAT, longitude=LON, start_date=day, end_date=day,
                        hourly=hourly, timezone=TZ), f"open-meteo forecast {day}")
    a = (ARCHIVE, dict(latitude=LAT, longitude=LON, start_date=day, end_date=day,
                       hourly=hourly, timezone=TZ), f"open-meteo archive {day}")
    # The forecast endpoint serves ~92 days back and refuses older dates with a 400.
    # Asking it anyway and letting it fail works, but it prints an alarming traceback on
    # every start-up of a pinned demo day, so ask the one that can answer first.
    tries = [f, a] if (_days_ago(day) or 0) <= 90 else [a, f]
    err = None
    for url, params, label in tries:
        try:
            j = _get(url, params)
            hrs = _by_hour(j["hourly"])
            if hrs and any(hrs[h].get("direct_radiation") is not None for h in hrs):
                return {"hours": hrs, "date": day}, label
        except Exception as e:
            err = e
            print(f"[weather] {label} failed: {e}")
    raise RuntimeError(f"no data for {day} ({err})")


def _fallback_table(day):
    """The offline table that fits this DAY, chosen by its month rather than by a mode.

    Used only when there is no network AND no cache, so it is a last resort in every
    sense; picking it by month is simply the one thing available that is not a guess.
    Southern-hemisphere warm season is Nov-Mar.
    """
    try:
        month = int(str(day)[5:7])
    except (ValueError, IndexError):
        month = time.localtime().tm_mon
    return _TABLE if month in (11, 12, 1, 2, 3) else _WINTER_TABLE


def get(day=None):
    """Cached hourly payload for one day. {'hours':{h:{...}}, 'date':..., 'source':...}

    `day` is a YYYY-MM-DD string, or None for today. A legacy season name still resolves
    (see LEGACY_DAYS) so the bench scripts keep running, but it is turned into a date
    here and nothing downstream sees a mode.
    """
    day = resolve_day(day)
    now = time.time()
    c = _mem.get(day)
    if c is None:
        try:
            c = json.load(open(_cache_path(day)))
            c["hours"] = {int(k): v for k, v in c["hours"].items()}
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
    coefficients as given in the WMO CIMO Guide. Accurate to ~0.1% over -40..50 C."""
    import math
    return 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))


def vapour_pressure(t_c, rh_pct):
    """Actual vapour pressure, hPa. UTCI's polynomial and Prata's L_sky both want this."""
    return sat_vapour_pressure(t_c) * max(0.0, min(100.0, rh_pct)) / 100.0


_bias_mem = {}


def bias_table():
    """The fitted correction, or None if it has not been fitted on this checkout.

    Absent file is not an error: the engine runs on the raw feed and says so in the
    block's `bias_mode`. A silently-applied correction and a silently-skipped one are the
    same bug, so both states are reported rather than assumed.
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
    """Degrees C to SUBTRACT from Open-Meteo's air temperature. (offset = model - sensor.)

    Returns (offset, mode) where mode names exactly what was applied, so the caller can
    put it in the response instead of the reader having to infer it from an env var.
    """
    t = bias_table()
    if not BIAS_ON:
        return 0.0, "off (SHADEME_BIAS=0)"
    if t is None:
        return 0.0, "unavailable (run: python scripts/validate_sensors.py --fit-bias)"
    # The offline winter table carries the literal date "today", so parse defensively and
    # fall back to the real month rather than to a hardcoded season -- a winter request
    # silently corrected with the summer shape would be worse than no correction.
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

    The correction is applied once, here, to the hourly rows -- not inside block(). The
    engine does not only read block(): attach_tsurf() marches the surface and facade
    energy balance straight off `wx["hours"]`, so correcting block() alone would leave
    one solve running two different air temperatures, one in the convective/UTCI terms
    and another in the longwave ones. One Ta, one place.

    The disk cache stays RAW on purpose. The cache is the observation; this is a derived
    transform of it, and a cache that already had the correction baked in could not be
    re-corrected when the fit is re-run.

    Shifted together, and why:
      temperature_2m       the fitted quantity.
      apparent_temperature rides Ta 1:1. An ASSUMPTION, not a fit -- Open-Meteo's
                           apparent temperature has no ground truth in the CoM archive so
                           it cannot be corrected on its own, but leaving it at the raw
                           value while Ta moves would make the pair inconsistent, and the
                           legacy W_heat is computed from it.
      relative_humidity_2m re-derived at CONSTANT vapour pressure. A temperature-bias
                           correction does not add or remove water; it says the air was
                           always this warm, so the absolute moisture carries over and RH
                           follows from the new saturation point. Not free-riding: it
                           independently improves RH RMSE against the same held-out
                           sensors (8.95 -> 8.80 %), a small check that the sign is right.
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


def solar_elevation(day, hour):
    """Solar elevation in degrees, or None if the astronomy helper is not importable.

    Only the archive path needs it (server/uv.clear_sky), so an ImportError here must
    degrade to "no UV" rather than take the weather block down with it.
    """
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
        import pandas as pd
        from shadow import sun_position
        return float(sun_position(pd.Timestamp(f"{day} {int(hour):02d}:00", tz=TZ))[1])
    except Exception:
        return None


def block(hour, day=None, now_hour=None, wet=False):
    """The contract `weather` block for one hour of one day.

    `now_hour` is the wall-clock hour, and it is what lets the UV index be a MEASUREMENT
    rather than a model: the live ARPANSA reading can only answer for right now, so it is
    used when `hour` is that hour and the modelled value is used otherwise. Passing None
    means "do not claim anything is live" and gives the modelled branch throughout.

    `wet` restores the legacy precipitation/wind weight, which only the old `edge_cost`
    path reads. The v2 engine does not use w_heat or w_wet at all.
    """
    p = apply_bias(get(day))
    hrs = p["hours"]
    h = int(hour) if int(hour) in hrs else min(hrs, key=lambda k: abs(k - int(hour)))
    r = hrs[h]
    direct, diffuse = _f(r["direct_radiation"]), _f(r["diffuse_radiation"])
    tot = direct + diffuse
    direct_fraction = (direct / tot) if tot > 0 else 0.0
    app = _f(r["apparent_temperature"])
    precip, wind = _f(r.get("precipitation")), _f(r.get("wind_speed_10m"))
    temp = _f(r.get("temperature_2m"))
    # RH may be absent in a pre-v2 cache or the offline table -> fall back, but SAY so.
    rh_raw = r.get("relative_humidity_2m")
    rh = 50.0 if rh_raw is None else float(rh_raw)

    raw = (p.get("hours_raw") or {}).get(h, r)
    temp_raw, app_raw, rh_uncorr = (_f(raw.get("temperature_2m")),
                                    _f(raw.get("apparent_temperature")),
                                    50.0 if raw.get("relative_humidity_2m") is None
                                    else float(raw["relative_humidity_2m"]))
    off = float((p.get("bias") or {}).get("offsets", {}).get(h, 0.0))
    bias_mode = (p.get("bias") or {}).get("mode", "off")
    vp = vapour_pressure(temp, rh)
    wind_ms = wind / 3.6                       # WIND_UNIT is pinned to km/h upstream
    w_heat = clamp(0.0, 3.0, (app - 20.0) / 5.0) * direct_fraction
    w_wet = clamp(0.0, 3.0, precip * 2.0 + wind / 15.0)
    # UV IS NEVER ESTIMATED FROM RADIATION. See server/uv.py -- the previous
    # `(direct + diffuse) / 100` fallback read UV 6 against 3.6 measured. If neither the
    # live network nor the feed can answer, this is None and the client shows nothing.
    uv_feed = r.get("uv_index")
    if uv_feed is None:
        uv_feed = r.get("uv_index_clear_sky")
    cloud = _f(r.get("cloud_cover"))
    uv, uv_source = UV.index_for(h, now_hour if now_hour is not None else -1,
                                 None if uv_feed is None else float(uv_feed), cloud,
                                 elev_deg=solar_elevation(p.get("date"), h))
    if not wet:
        w_wet = 0.0                              # legacy weight; off unless asked for
    return {
        "date": p.get("date"),
        "hour": h,
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
        "source": f"{p['source']} | {p['date']} {h:02d}:00 {TZ}",
    }
