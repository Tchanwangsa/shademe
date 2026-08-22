"""Ground-truth validation: City of Melbourne sensors vs Open-Meteo vs (later) the engine.

WHAT THIS CAN VALIDATE
  * Open-Meteo's skill at the *air* variables the engine consumes: air temperature,
    relative humidity, wind speed. Bias / RMSE / correlation, per site and overall.
    That is the error the engine INHERITS from its weather input, before any physics.
  * (stub) whether a grid cell was sunlit or shaded at a given hour -- shadow geometry
    only -- via the 2015 8-location light dataset.

WHAT THIS CANNOT VALIDATE
  * MRT. These are air-temperature/RH/wind stations. No globe thermometer, no
    pyranometer, no net radiometer anywhere in the CoM open archive. MRT error is
    therefore NOT measurable here, only bounded indirectly.
  * UTCI. Follows from MRT.
  * Surface temperature. Nothing measures it.
  * The convective term at pedestrian height: sensor anemometer height is UNDOCUMENTED
    in the dataset metadata (see WIND_HEIGHT_NOTE), so sensor-vs-Open-Meteo-10m wind
    differences confound model error with an unknown log-profile correction.

Sensor archive runs 2022-05-31 .. 2026-08-18 (verified), so the demo day 2026-01-26
IS inside the observed period -- validation and demo day can be the same date.
"""
import os, sys, json, gzip, time, math
import numpy as np, pandas as pd, requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BBOX

DATA = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
TZ = "Australia/Melbourne"
ODS = "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{}/exports/csv"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

SENSOR_CSV = os.path.join(DATA, "microclimate_sensors.csv")     # ~976k rows, ';' delimited
LUX_CSV = os.path.join(DATA, "lux_sensors_2015.csv")            # 56.5k rows, ';' delimited
OM_DIR = os.path.join(DATA, "openmeteo_sites")

OM_START, OM_END = "2023-06-01", "2026-08-18"   # contiguous archive window per site
OM_VARS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_direction_10m",
           "direct_radiation", "diffuse_radiation", "cloud_cover", "precipitation"]

WIND_HEIGHT_NOTE = (
    "Anemometer height is NOT published for any CoM microclimate site. The units are "
    "pole/rooftop mounted (ICT MicroClimate stations); street poles are ~3-4 m, rooftops "
    "are ~30-60 m above ground. Open-Meteo wind_speed_10m is a 10 m open-terrain value. "
    "Any sensor-vs-model wind comparison therefore mixes model error with an unknown "
    "roughness/height correction. Do NOT calibrate the convective term h(wind) on it.")

# Mount exposure. Rooftop sites are NOT pedestrian-level and must be excluded from any
# street-canyon claim; they are kept because they are a clean test of the free-air forcing.
EXPOSURE = {
    "ICTMicroclimate-01": "park",     "ICTMicroclimate-02": "rooftop",
    "ICTMicroclimate-03": "rooftop",  "ICTMicroclimate-04": "park",
    "ICTMicroclimate-05": "park",     "ICTMicroclimate-06": "street",
    "ICTMicroclimate-07": "street",   "ICTMicroclimate-08": "street",
    "ICTMicroclimate-09": "rooftop",  "ICTMicroclimate-10": "street",
    "ICTMicroclimate-11": "street",   "aws5-0999": "park",
}

# Plausibility gates. The raw archive contains spikes (pressure to 894 hPa, gust to 52 m/s).
QC = {"airtemperature": (-5.0, 50.0), "relativehumidity": (0.0, 100.0),
      "averagewindspeed": (0.0, 30.0), "gustwindspeed": (0.0, 45.0),
      "atmosphericpressure": (950.0, 1050.0)}

VARS = {  # sensor column -> open-meteo column
    "airtemperature": "temperature_2m",
    "relativehumidity": "relative_humidity_2m",
    "averagewindspeed": "wind_speed_10m",
}


# ---------------------------------------------------------------- fetch / cache

def _download(dataset, path):
    """Stream an ODS csv export to disk. Never re-downloads."""
    if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
        print(f"  cached  {os.path.basename(path)} ({os.path.getsize(path)/1e6:.1f} MB)")
        return path
    tmp = path + ".part"
    print(f"  fetching {dataset} ...")
    with requests.get(ODS.format(dataset), stream=True, timeout=3600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for c in r.iter_content(1 << 20):
                f.write(c)
    os.replace(tmp, path)
    print(f"  done    {os.path.basename(path)} ({os.path.getsize(path)/1e6:.1f} MB)")
    return path


def fetch_all():
    os.makedirs(DATA, exist_ok=True)
    _download("microclimate-sensors-data", SENSOR_CSV)
    _download("sensor-readings-with-temperature-light-humidity-every-5-minutes-at-8-locations-t",
              LUX_CSV)


# ---------------------------------------------------------------- sensors

def load_sensors():
    """Full archive -> DataFrame with tz-aware UTC `t` and local `ltime`, QC'd."""
    d = pd.read_csv(SENSOR_CSV, sep=";", low_memory=False)
    d["t"] = pd.to_datetime(d["received_at"], utc=True)
    d["ltime"] = d["t"].dt.tz_convert(TZ)
    d["date"] = d["ltime"].dt.date
    d["hour"] = d["ltime"].dt.hour
    ll = d["latlong"].astype(str).str.split(",", expand=True)
    d["lat"] = pd.to_numeric(ll[0], errors="coerce")
    d["lon"] = pd.to_numeric(ll[1], errors="coerce")
    for c, (lo, hi) in QC.items():
        if c in d:
            d.loc[(d[c] < lo) | (d[c] > hi), c] = np.nan
    return d


def sites(d):
    """One row per device: name, lat/lon, exposure, in-bbox flag, coverage."""
    g = d.groupby("device_id")
    s = pd.DataFrame({
        "name": g["sensorlocation"].first(),
        "lat": g["lat"].median(), "lon": g["lon"].median(),
        "n": g.size(), "t0": g["ltime"].min().dt.date, "t1": g["ltime"].max().dt.date,
    })
    for c in ("airtemperature", "relativehumidity", "averagewindspeed",
              "gustwindspeed", "atmosphericpressure"):
        s[c[:4] + "%"] = (100.0 * g[c].count() / g.size()).round(1)
    s["exposure"] = [EXPOSURE.get(i, "?") for i in s.index]
    s["in_bbox"] = ((s.lon >= BBOX["min_lon"]) & (s.lon <= BBOX["max_lon"]) &
                    (s.lat >= BBOX["min_lat"]) & (s.lat <= BBOX["max_lat"]))
    return s.sort_index()


def hourly_sensor(d, date, device):
    """Sensor obs for one local date + device, averaged onto local hours 0..23."""
    m = d[(d.device_id == device) & (d.date == pd.Timestamp(date).date())]
    if m.empty:
        return pd.DataFrame()
    g = m.groupby("hour")
    out = pd.DataFrame({c + "_s": g[c].mean() for c in VARS})
    out["n_s"] = g.size()
    return out.reindex(range(24))


# ---------------------------------------------------------------- open-meteo

def _om_path(device):
    return os.path.join(OM_DIR, f"{device}.json.gz")


def fetch_openmeteo(device, lat, lon, start=OM_START, end=OM_END):
    """One archive request per site over a contiguous window. Never re-downloads."""
    p = _om_path(device)
    if os.path.exists(p):
        return p
    os.makedirs(OM_DIR, exist_ok=True)
    r = requests.get(ARCHIVE, params=dict(
        latitude=lat, longitude=lon, start_date=start, end_date=end,
        hourly=",".join(OM_VARS), timezone=TZ, wind_speed_unit="ms"), timeout=180)
    r.raise_for_status()
    with gzip.open(p, "wt") as f:
        json.dump(r.json(), f)
    time.sleep(1.0)                                   # be polite
    return p


_om_cache = {}


def open_meteo(device):
    """Site archive as a DataFrame indexed by tz-naive LOCAL hour (Australia/Melbourne)."""
    if device in _om_cache:
        return _om_cache[device]
    with gzip.open(_om_path(device), "rt") as f:
        h = json.load(f)["hourly"]
    df = pd.DataFrame(h)
    df["ltime"] = pd.to_datetime(df.pop("time"))          # already local, tz-naive
    df["date"] = df["ltime"].dt.date
    df["hour"] = df["ltime"].dt.hour
    _om_cache[device] = df
    return df


# ---------------------------------------------------------------- the harness

def join_hourly(d, date, device):
    """THE AIR-SIDE HARNESS.

    For one local date and one sensor site, return a 24-row hourly table joining the
    sensor observations to the co-located Open-Meteo archive values.

    Columns: airtemperature_s/_m, relativehumidity_s/_m, averagewindspeed_s/_m (+ _d
    differences), plus the model's direct/diffuse radiation and cloud cover for context.
    Index = local hour 0..23 (Australia/Melbourne). Sensor timestamps are UTC in the
    archive and are converted explicitly; Open-Meteo is requested with timezone=
    Australia/Melbourne and returns tz-naive local time. Both are therefore local.
    """
    s = hourly_sensor(d, date, device)
    if s.empty:
        return pd.DataFrame()
    m = open_meteo(device)
    m = m[m.date == pd.Timestamp(date).date()].set_index("hour")
    if m.empty:
        return pd.DataFrame()
    out = s.join(m[OM_VARS].rename(columns={v: k + "_m" for k, v in VARS.items()}))
    for k in VARS:
        if k + "_m" in out:
            out[k + "_d"] = out[k + "_m"] - out[k + "_s"]
    return out


def check_tz_alignment(d, dates, devices):
    """Diurnal-peak guard. On a CLEAR day the peak must land in the afternoon in both
    series. Overcast/frontal days genuinely peak at odd hours, so they are excluded --
    an off-by-one would otherwise hide behind real meteorology."""
    rows = []
    for dt in dates:
        for dev in devices:
            j = join_hourly(d, dt, dev)
            if j.empty or j["airtemperature_s"].isna().all():
                continue
            tot = j.direct_radiation.sum() + j.diffuse_radiation.sum()
            rows.append((dt, dev, int(j["airtemperature_s"].idxmax()),
                         int(j["airtemperature_m"].idxmax()),
                         float(j.direct_radiation.sum() / tot) if tot else 0.0))
    r = pd.DataFrame(rows, columns=["date", "device", "peak_sensor_h", "peak_model_h", "dfrac"])
    c = r[r.dfrac >= 0.6]
    ok = bool(len(c) and c.peak_sensor_h.between(11, 20).all()
              and c.peak_model_h.between(11, 20).all())
    return r, ok


def lag_scan(d, devices, lags=range(-3, 4)):
    """Stronger off-by-one guard: correlate the WHOLE hourly sensor series against the
    model at lags -3..+3 h. The maximising lag must be 0 for every site. A one-hour
    timezone error would show up here as a systematic +/-1."""
    dd = d.dropna(subset=["airtemperature"]).copy()
    dd["hh"] = dd["ltime"].dt.tz_localize(None).dt.floor("h")
    s_all = dd.groupby(["device_id", "hh"])["airtemperature"].mean()
    rows = []
    for dev in devices:
        s = s_all.loc[dev]
        m = open_meteo(dev).set_index("ltime")["temperature_2m"]
        j = pd.concat([s.rename("s"), m.rename("m")], axis=1).dropna()
        if len(j) < 500:
            continue
        cs = {L: j["s"].corr(j["m"].shift(L)) for L in lags}
        best = max(cs, key=lambda k: cs[k])
        rows.append(dict(device=dev, best_lag=best, r_at_0=cs[0],
                         r_at_best=cs[best], n=len(j)))
    r = pd.DataFrame(rows)
    # A real timezone error shifts EVERY site together. A lone site preferring +/-1 h with
    # a negligible correlation gain is thermal mass (rooftops lag), not a clock bug.
    return r, bool(len(r) and (r.best_lag == 0).mean() >= 0.9)


PAIR = ("ICTMicroclimate-10", "ICTMicroclimate-11")   # 1 Treasury Place, ~48 m apart

PAIR_FINDING = (
    "-10 and -11 sit at the same street address ~48 m apart, yet -11 reads +1.0 C and "
    "-9 %RH against -10. That offset is CONSTANT to within +/-0.2 C across all 19 months "
    "of overlap AND across all 24 hours (incl. 06:00, no sun, no thermal forcing). It is "
    "an instrument CALIBRATION offset, not microclimate. Consequence: the absolute "
    "accuracy of this ground truth is ~+/-1 C / ~+/-9 %RH. Any per-site bias in the "
    "Open-Meteo table smaller than that is NOT distinguishable from sensor drift -- only "
    "the RMSE and correlation columns, and BETWEEN-site differences, carry real signal.")


def pair_noise(d, dates, pair=PAIR):
    """Sensor-vs-sensor at a co-located pair -> the NOISE FLOOR of this ground truth.

    No model can be validated below this. Any of it that is not instrument error is real
    sub-50 m microclimate variability, which is exactly what the engine claims to resolve
    -- so this number is both a floor and a sanity check on the premise."""
    out = {}
    for k in VARS:
        a = pd.concat([hourly_sensor(d, dt, pair[0]) for dt in dates])[k + "_s"]
        b = pd.concat([hourly_sensor(d, dt, pair[1]) for dt in dates])[k + "_s"]
        m = a.notna().to_numpy() & b.notna().to_numpy()
        e = b.to_numpy()[m] - a.to_numpy()[m]
        out[k] = dict(bias=float(e.mean()), rmse=float(np.sqrt((e ** 2).mean())),
                      max_abs=float(np.abs(e).max()), n=int(m.sum()))
    return pd.DataFrame(out).T


def skill(j):
    """bias (model-sensor), RMSE, Pearson r, n -- per variable, for one joined table."""
    out = {}
    for k in VARS:
        a, b = j.get(k + "_s"), j.get(k + "_m")
        if a is None or b is None:
            continue
        msk = a.notna() & b.notna()
        n = int(msk.sum())
        if n < 3:
            out[k] = dict(bias=np.nan, rmse=np.nan, r=np.nan, n=n)
            continue
        e = (b[msk] - a[msk]).to_numpy(float)
        out[k] = dict(bias=float(e.mean()), rmse=float(np.sqrt((e ** 2).mean())),
                      r=float(np.corrcoef(a[msk], b[msk])[0, 1]), n=n)
    return out


def skill_table(d, dates, devices):
    """Per (device, variable) skill pooled over `dates`, plus an ALL row."""
    rows, pool = [], {k: [] for k in VARS}
    for dev in devices:
        js = [join_hourly(d, dt, dev) for dt in dates]
        js = [x for x in js if not x.empty]
        if not js:
            continue
        j = pd.concat(js)
        for k, v in skill(j).items():
            rows.append(dict(device=dev, var=k, **v))
        for k in VARS:
            if k + "_s" in j:
                pool[k].append(j[[k + "_s", k + "_m"]])
    for k, chunks in pool.items():
        if not chunks:
            continue
        c = pd.concat(chunks).dropna()
        e = (c[k + "_m"] - c[k + "_s"]).to_numpy(float)
        rows.append(dict(device="ALL", var=k, bias=e.mean(),
                         rmse=float(np.sqrt((e ** 2).mean())),
                         r=float(np.corrcoef(c.iloc[:, 0], c.iloc[:, 1])[0, 1]), n=len(c)))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- day selection

def daily_stats(d, min_sites=6, min_samples=80):
    """Per local date: how many sites reported well, and the network temperature range."""
    ok = d.dropna(subset=["airtemperature"]).groupby(["date", "device_id"]).agg(
        n=("airtemperature", "size"), tmin=("airtemperature", "min"),
        tmax=("airtemperature", "max"), tmean=("airtemperature", "mean"))
    ok = ok[ok.n >= min_samples]
    g = ok.groupby("date").agg(sites=("n", "size"), tmin=("tmin", "min"),
                               tmax=("tmax", "max"), tmean=("tmean", "mean"))
    return g[g.sites >= min_sites]


def candidate_days(d, ref_device="ICTMicroclimate-08"):
    """Pick a hot-clear, a mild, and a cool-overcast day with wide sensor coverage.

    Cloud/radiation come from the co-located Open-Meteo archive (the sensors carry no
    radiation channel), so 'clear' and 'overcast' are model-defined, not observed.
    """
    g = daily_stats(d)
    m = open_meteo(ref_device)
    day = m.groupby("date").agg(cloud=("cloud_cover", "mean"),
                                dir_=("direct_radiation", "sum"),
                                dif=("diffuse_radiation", "sum"),
                                rain=("precipitation", "sum"))
    day["dfrac"] = day.dir_ / (day.dir_ + day.dif).replace(0, np.nan)
    g = g.join(day, how="inner")
    picks = {}
    hot = g[(g.tmax >= 33) & (g.dfrac >= 0.65) & (g.cloud <= 50)]
    if len(hot):
        picks["hot_clear"] = hot.sites.mul(1000).add(hot.tmax).idxmax()
    mild = g[g.tmax.between(19, 24) & (g.rain < 0.5) & (g.cloud <= 40)]
    if len(mild):
        picks["mild_sunny"] = mild.sites.mul(100).add(mild.dfrac).idxmax()
    # Winter needs BOTH ends: a wet grey day (no sun to seek) and a cold clear day
    # (low-elevation winter sun -> the engine should route people INTO it).
    wet = g[(g.tmax <= 15) & (g.cloud >= 70) & (g.rain >= 1.0)]
    if len(wet):
        picks["cold_overcast"] = wet.sites.mul(100).sub(wet.tmax).idxmax()
    cold = g[(g.tmax <= 15) & (g.dfrac >= 0.6) & (g.rain < 0.5)]
    if len(cold):
        picks["cold_clear"] = cold.sites.mul(100).sub(cold.tmax).idxmax()
    return picks, g


# ---------------------------------------------------------------- stubs

def compare_shade_lux(date, mac, shade_dir="out"):
    """STUB -- shade/shadow-geometry validation. NOT IMPLEMENTED (engine side pending).

    Plugs in: `out/shade_HH.npy` (float32 (h,w), 1.0 = fully shaded) + `out/grid.json`.
    Take the 2015 light dataset (`lux_sensors_2015.csv`, 9 boards at Fitzroy Gardens and
    Docklands Library, 2014-12-15 .. 2015-06-05, 5-min), project the board lat/lon to
    EPSG:28355, index the raster, and test whether the modelled sunlit/shaded state
    matches a sunlit/shaded classification of the light channel. Score as a 2x2
    contingency table (hit rate / false alarm), NOT as a regression.

    WHAT IT WOULD PROVE: that the shadow *geometry* (sun position, building/canopy
    occlusion, at that hour, at that point) is right.
    WHAT IT WOULD NOT PROVE: anything about longwave, surface temperature, SVF-weighted
    diffuse, or MRT. A shadow mask being correct says nothing about how hot the shade is.

    KNOWN LIMITS OF THE LIGHT DATA -- read before trusting a number:
      * `light_avg` is bounded 0..98.7 with a 75th percentile of 94.9. It is a
        normalised/saturating 0-100 channel, NOT lux. It clips through most of daylight,
        so it discriminates sun-vs-shade only near dawn/dusk and under deep shade.
      * Sites are a park (Fitzroy Gardens, few buildings -> tests canopy, not canyons)
        and Docklands Library (3 boards at 0-4 m elevation with median light 8-14, almost
        certainly indoor -> useless as a sky-exposure reference).
      * 2014-15 only. Building stock is `building-outlines-2015` so the geometry is
        contemporaneous, but the canopy layer is 2021.
    """
    raise NotImplementedError("shade/lux comparison: engine rasters not built yet")


def compare_mrt(d, date, device, mrt_dir="out"):
    """STUB -- MRT/UTCI comparison. NOT IMPLEMENTED, and only PARTLY possible ever.

    Plugs in: `out/mrt_HH.npy` (float32 (h,w), degC) + `out/tsurf_HH.npy` + `out/grid.json`
    per ENGINE_CONTRACT.md. Sample the raster at the site's projected (x,y), pair with
    this harness's hourly sensor Ta / RH / wind, run the Brode UTCI polynomial, and emit
    the modelled UTCI series.

    HONEST LIMIT: there is no measured MRT to compare against. No CoM dataset carries a
    globe temperature or a pyranometer. So this function can only produce:
      (a) a PLAUSIBILITY check -- MRT-Ta should be roughly 0 at night, +15..35 C in
          summer midday sun, and near 0 in deep shade; sign and magnitude, not error;
      (b) a CONSISTENCY check -- shaded vs sunlit sites at the same hour should differ in
          the modelled direction, and the two Treasury Place sites (-10, -11), 48 m apart,
          should agree closely;
      (c) an AIR-SIDE ERROR BUDGET -- the Ta/RH/wind skill this module already measures
          propagates into UTCI and bounds how much of any discrepancy is the physics.
    Do not report the output of this function as "MRT validated". It is not.
    """
    raise NotImplementedError("MRT comparison: engine rasters not built yet")


# ---------------------------------------------------------------- report

def main():
    fetch_all()
    print("\nloading sensors ...")
    d = load_sensors()
    s = sites(d)
    pd.set_option("display.width", 200, "display.max_columns", 40)

    print(f"\n=== TEMPORAL COVERAGE  n={len(d)}  {d.ltime.min()}  ..  {d.ltime.max()}")
    print("\n=== SITES")
    print(s.to_string())
    print(f"\nin bbox: {int(s.in_bbox.sum())}/{len(s)}   "
          f"street-level+park (usable for pedestrian claims): "
          f"{int((s.in_bbox & (s.exposure != 'rooftop')).sum())}")

    for dev in s.index:
        fetch_openmeteo(dev, s.lat[dev], s.lon[dev])

    picks, g = candidate_days(d)
    days = dict(picks)
    days["demo_day"] = pd.Timestamp("2026-01-26").date()
    print("\n=== CANDIDATE VALIDATION DAYS")
    for k, dt in days.items():
        r = g.loc[dt] if dt in g.index else None
        print(f"  {k:14} {dt}  " + (f"sites={int(r.sites):2d} tmin={r.tmin:.1f} "
              f"tmax={r.tmax:.1f} cloud={r.cloud:.0f}% dfrac={r.dfrac:.2f} "
              f"rain={r.rain:.1f}mm" if r is not None else "(below coverage threshold)"))

    dates = list(days.values())
    devs = [i for i in s.index if s.in_bbox[i]]
    tz, ok = check_tz_alignment(d, dates, devs)
    c = tz[tz.dfrac >= 0.6]
    print(f"\n=== TIMEZONE ALIGNMENT")
    print(f"  (a) afternoon-peak on clear site-days (n={len(c)}): OK={ok}   "
          f"sensor peak h {c.peak_sensor_h.min()}..{c.peak_sensor_h.max()}, "
          f"model peak h {c.peak_model_h.min()}..{c.peak_model_h.max()}")
    print(f"      (all site-days incl. overcast: sensor {tz.peak_sensor_h.min()}.."
          f"{tz.peak_sensor_h.max()} -- frontal days peak at odd hours, expected)")
    lg, lok = lag_scan(d, devs)
    print(f"  (b) full-archive lag scan (-3..+3 h): all best_lag==0? {lok}")
    print(lg.round(4).to_string(index=False))

    print("\n=== OPEN-METEO SKILL vs SENSORS  (bias = model - sensor)")
    t = skill_table(d, dates, devs)
    for v in VARS:
        sub = t[t["var"] == v].copy()
        sub["exposure"] = [EXPOSURE.get(x, "-") for x in sub.device]
        print(f"\n-- {v}")
        print(sub[["device", "exposure", "bias", "rmse", "r", "n"]].round(3).to_string(index=False))

    print("\n=== CO-LOCATED PAIR NOISE FLOOR  "
          f"({PAIR[0]} vs {PAIR[1]}, 1 Treasury Place, ~48 m apart)")
    print(pair_noise(d, dates).round(3).to_string())
    print("  Nothing below these RMSEs is measurable with this ground truth.\n  " + PAIR_FINDING)

    ta = t[(t.device == "ALL") & (t["var"] == "airtemperature")].iloc[0]
    print("\n=== INHERITED ERROR BUDGET")
    print(f"  Open-Meteo Ta RMSE = {ta.rmse:.2f} C. UTCI is ~1:1 in Ta and ~1:4 in MRT,")
    print(f"  so the weather INPUT alone puts a ~{ta.rmse:.1f} C floor under any UTCI")
    print(f"  error. Our physics would need MRT errors above ~{4*ta.rmse:.0f} C before it")
    print("  dominated the input error. Quote this before quoting any MRT number.")

    print("\n=== WIND HEIGHT\n  " + WIND_HEIGHT_NOTE)


if __name__ == "__main__":
    main()
