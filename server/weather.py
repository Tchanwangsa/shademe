"""Open-Meteo weather -> cost weights. No API key. Disk-cached so the demo survives dead wifi."""
import os, json, time, requests

LAT, LON = -37.8136, 144.9631
TZ = "Australia/Melbourne"
# Hero demo day. 26 Jan 2026 flips its own advice across the hour slider: 09:00 is 20.0C
# and half-diffuse (W_heat 0 -> shortest route), 14:00 is 32.0C and clear (W_heat 2.1 ->
# +18% detour, -77% sun), 18:00 is still 30.5C but the low sun already shades the direct
# route, so it declines to detour again. Override with SHADEME_SUMMER_DATE=YYYY-MM-DD.
# Hotter alternatives, both live in the archive: 2026-01-24 (35.9C), 2026-01-27 (42.8C).
SUMMER_DATE = os.environ.get("SHADEME_SUMMER_DATE", "2026-01-26")
VARS = ["temperature_2m", "apparent_temperature", "direct_radiation", "diffuse_radiation",
        "cloud_cover", "precipitation", "wind_speed_10m", "uv_index",
        "relative_humidity_2m"]   # humidity: needed by MRT longwave + UTCI, added in the v2 engine
# Open-Meteo defaults wind to km/h. That default was previously implicit, which is a trap:
# UTCI and the convective term both want m/s. Pin the unit explicitly and expose BOTH,
# so the legacy W_wet (tuned on km/h) and the physics (m/s) can never diverge silently.
WIND_UNIT = "kmh"
CACHE_V = 2                          # bump when VARS changes so old caches are refetched
TTL = 600                            # re-fetch only if cache older than 10 min
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"

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
    "relative_humidity_2m":[88]*7 + [86,84,80,76,72,70,68,68,70,74,78,82,85] + [87]*4,
}

_mem = {}


def clamp(lo, hi, v): return max(lo, min(hi, v))


def _cache_path(mode): return os.path.abspath(os.path.join(DATA, f"weather_cache_{mode}.json"))


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


def _fetch(mode):
    """Returns (payload, source). Raises only if every route fails."""
    hourly = ",".join(VARS)
    if mode == "summer":
        try:
            j = _get(ARCHIVE, dict(latitude=LAT, longitude=LON, start_date=SUMMER_DATE,
                                   end_date=SUMMER_DATE, hourly=hourly, timezone=TZ))
            hrs = _by_hour(j["hourly"])
            if any(hrs[h]["direct_radiation"] for h in hrs):
                return {"hours": hrs, "date": SUMMER_DATE}, f"open-meteo archive {SUMMER_DATE}"
        except Exception as e:
            print(f"[weather] archive failed: {e}")
        try:                                   # forecast API can serve recent past dates
            j = _get(FORECAST, dict(latitude=LAT, longitude=LON, start_date=SUMMER_DATE,
                                    end_date=SUMMER_DATE, hourly=hourly, timezone=TZ))
            hrs = _by_hour(j["hourly"])
            if any(hrs[h]["direct_radiation"] for h in hrs):
                return {"hours": hrs, "date": SUMMER_DATE}, f"open-meteo forecast-past {SUMMER_DATE}"
        except Exception as e:
            print(f"[weather] forecast-past failed: {e}")
        raise RuntimeError("no live summer data")
    j = _get(FORECAST, dict(latitude=LAT, longitude=LON, hourly=hourly, timezone=TZ,
                            forecast_days=1))
    date = j["hourly"]["time"][0][:10]
    return {"hours": _by_hour(j["hourly"]), "date": date}, f"open-meteo forecast {date}"


def get(mode="summer"):
    """Cached hourly payload for a mode. {'hours':{h:{...}}, 'date':..., 'source':...}"""
    mode = "summer" if mode == "summer" else "winter"
    now = time.time()
    c = _mem.get(mode)
    if c is None:
        try:
            c = json.load(open(_cache_path(mode)))
            c["hours"] = {int(k): v for k, v in c["hours"].items()}
            _mem[mode] = c
        except Exception:
            c = None
    fresh = c and now - c.get("ts", 0) < TTL and c.get("cache_v") == CACHE_V
    if mode == "summer" and c and c.get("date") != SUMMER_DATE:
        fresh = False                           # demo day changed under us
    if fresh:
        return c
    try:
        payload, source = _fetch(mode)
        payload.update(ts=now, source=source, cache_v=CACHE_V)
        os.makedirs(os.path.abspath(DATA), exist_ok=True)
        json.dump(payload, open(_cache_path(mode), "w"))
        _mem[mode] = payload
        return payload
    except Exception as e:
        print(f"[weather] fetch failed ({e})")
        if c:                                   # stale cache beats a lie
            c["source"] = c.get("source", "cache") + " (stale cache, offline)"
            return c
        tbl = _TABLE if mode == "summer" else _WINTER_TABLE
        p = _table(tbl, SUMMER_DATE if mode == "summer" else "today")
        p.update(ts=now, source="fallback-table (hardcoded, offline - NOT live)")
        _mem[mode] = p
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


def block(hour, mode="summer"):
    """The contract `weather` block for one hour."""
    p = get(mode)
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
    wind_ms = wind / 3.6                       # WIND_UNIT is pinned to km/h upstream
    w_heat = clamp(0.0, 3.0, (app - 20.0) / 5.0) * direct_fraction
    w_wet = clamp(0.0, 3.0, precip * 2.0 + wind / 15.0)
    uv = r.get("uv_index")
    uv = round(tot / 100.0, 1) if uv is None else round(float(uv), 1)   # estimate if absent
    if mode == "shortest":
        w_heat = w_wet = 0.0
    elif mode != "winter":
        w_wet = 0.0                              # W_wet is a winter-mode weight
    return {
        "temperature": round(_f(r["temperature_2m"]), 1),
        "apparent_temperature": round(app, 1),
        "direct_radiation": round(direct, 1),
        "diffuse_radiation": round(diffuse, 1),
        "cloud_cover": round(_f(r["cloud_cover"]), 1),
        "uv_index": uv,
        "precipitation": round(precip, 2),
        "wind_speed": round(wind, 1),                    # km/h, legacy field, do not repurpose
        "wind_speed_ms": round(wind_ms, 2),              # m/s at 10 m -- use this for physics
        "relative_humidity": round(rh, 1),
        "rh_is_fallback": rh_raw is None,
        "vapour_pressure_hpa": round(vapour_pressure(temp, rh), 2),
        "direct_fraction": round(direct_fraction, 3),
        "w_heat": round(w_heat, 3),
        "w_wet": round(w_wet, 3),
        "source": f"{p['source']} | {p['date']} {h:02d}:00 {TZ}",
    }
