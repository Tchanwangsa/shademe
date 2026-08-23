"""Ground-truth validation: City of Melbourne sensors vs Open-Meteo vs (later) the engine.

WHAT THIS CAN VALIDATE
  * Open-Meteo's skill at the *air* variables the engine consumes: air temperature,
    relative humidity, wind speed. Bias / RMSE / correlation, per site and overall.
    That is the error the engine INHERITS from its weather input, before any physics.
  * ATTEMPTED, and the attempt is the answer: whether a point was sunlit or shaded at a
    given hour -- shadow geometry only -- via the 2015 nine-board light dataset.
    `--shade-lux` runs it. The light channel saturates through all of daylight (0 of 9587
    beam-hour samples ever read shaded), so the contingency table has no shaded class and
    the overcast control shows no beam-specific separation for either model. See
    LUX_FINDING. This bounds what the published data CAN say; it does not validate the
    shadow model, and nothing may claim it does.

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
import os, json, gzip, time
import numpy as np, pandas as pd, requests, pvlib

from shademe.config import BBOX, WGS84, MGA55

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DATA = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "out")     # DSMs + grid.json, for the shade/lux check
TZ = "Australia/Melbourne"
ODS = "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/{}/exports/csv"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

SENSOR_CSV = os.path.join(DATA, "microclimate_sensors.csv")     # ~976k rows, ';' delimited
LUX_CSV = os.path.join(DATA, "lux_sensors_2015.csv")            # 56.5k rows, ';' delimited
OM_DIR = os.path.join(DATA, "openmeteo_sites")
BIAS_JSON = os.path.join(DATA, "openmeteo_bias.json")  # fitted diurnal correction, read by shademe/api/weather.py

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


# ------------------------------------------------- diurnal bias fit (stage 01)

# Split is CHRONOLOGICAL, not random. Random hours would leak: the hour either side of a
# held-out hour carries almost the same synoptic state, so a random split measures
# interpolation, not forecast skill. Train is the first ~25 months, test the last ~14 --
# a full annual cycle, so every season is held out at least once.
BIAS_SPLIT = "2025-07-01"
SEASON_OF = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
             6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
SEASONS = ("DJF", "MAM", "JJA", "SON")

BIAS_FINDING = (
    "Open-Meteo's Melbourne CBD air temperature carries TWO separable errors, and they "
    "are not equally trustworthy.\n"
    "  SHAPE (diurnal): the model runs ~1.2 C colder relative to its own daily mean at "
    "night than at midday. The sign is the same at 10 of 12 sites and at every exposure "
    "class -- park, street and rooftop alike -- so no sensor drift explains it: drift is "
    "not synchronised to the clock across a dozen independent instruments. It is the "
    "nocturnal urban heat island, which a ~9 km grid cell cannot resolve. Correcting it "
    "is safe.\n"
    "  LEVEL (daily mean): the model runs ~1.0 C cold overall. This is the bigger error "
    "by far, but it is the CONFOUNDED one -- see PAIR_FINDING, where two units 48 m "
    "apart disagree by 1.0 C. fit_diurnal_bias() therefore returns the level SEPARATELY "
    "and zero-means the shape, so applying the shape alone cannot move the daily mean.\n"
    "  What the held-out numbers say about that choice is in evaluate_bias_correction(); "
    "read it before deciding which of the two to ship.")


def pooled_hourly(d, devices=None):
    """Every site, every hour of the whole archive, joined to its co-located Open-Meteo.

    join_hourly() is the per-date harness and is the right thing for a report table; it
    is the wrong thing for fitting, which wants all ~243k site-hours at once. Same join,
    same timezone handling (sensor UTC -> local, Open-Meteo already local), vectorised.
    """
    devices = sorted(d.device_id.dropna().unique()) if devices is None else devices
    dd = d.copy()
    dd["hh"] = dd["ltime"].dt.tz_localize(None).dt.floor("h")
    g = dd.groupby(["device_id", "hh"])
    s = g[list(VARS)].mean()
    s["n_s"] = g.size()
    s = s.reset_index()
    ms = []
    for dev in devices:
        if not os.path.exists(_om_path(dev)):
            print(f"  [bias] no open-meteo archive for {dev}, skipped")
            continue
        m = open_meteo(dev).copy()
        m["hh"] = m.pop("ltime")
        m["device_id"] = dev
        ms.append(m[["device_id", "hh"] + OM_VARS])
    j = s.merge(pd.concat(ms, ignore_index=True), on=["device_id", "hh"], how="inner")
    j["hour"] = j.hh.dt.hour
    j["season"] = j.hh.dt.month.map(SEASON_OF)
    j["split"] = np.where(j.hh < pd.Timestamp(BIAS_SPLIT), "train", "test")
    for k, v in VARS.items():
        j[k + "_bias"] = j[v] - j[k]
    return j


def fit_diurnal_bias(j, var="airtemperature"):
    """Hour-of-day bias tables for one variable, fitted on the TRAIN split only.

    Returns per-season 24-vectors that are ZERO-MEAN over the day (the shape), the same
    thing pooled over all seasons, and the daily-mean bias (the level) as a separate
    scalar. `corrected = model - offset`, with offset = shape[season][hour] and the level
    added only if the caller opts in.

    Why per season and not one pooled shape: the winter shape is nearly the inverse of
    the summer one in the morning (JJA 09:00 -0.23 C vs DJF 09:00 +0.81 C). A single
    pooled table is +6% RMSE in summer and -3% in WINTER -- worse than no correction at
    all -- and the app is served year round. Per-season is never harmful in any season.

    Why not per site: see PAIR_FINDING. Two units at one address differ by 1.0 C, so a
    per-site table fits that instrument's calibration as readily as the model's error.
    Averaging the shape over all sites is what makes it a claim about the model.
    """
    tr = j[(j.split == "train")].dropna(subset=[var + "_bias"])
    raw = tr.pivot_table(index="hour", columns="season", values=var + "_bias", aggfunc="mean")
    raw = raw.reindex(index=range(24), columns=list(SEASONS))
    raw = raw.fillna(pd.Series(tr.groupby("hour")[var + "_bias"].mean()).reindex(range(24)).mean())
    shape = raw - raw.mean(axis=0)                       # zero-mean over the 24 hours
    pooled = tr.groupby("hour")[var + "_bias"].mean().reindex(range(24))
    return {
        "kind": "openmeteo-diurnal-bias-v1",
        "variable": var,
        "convention": "offset = mean(model - sensor); corrected = model - offset",
        "shape_is_zero_mean_per_season": True,
        "seasons": {s: [round(float(x), 4) for x in shape[s]] for s in SEASONS},
        "pooled_shape": [round(float(x), 4) for x in (pooled - pooled.mean())],
        "level": round(float(tr[var + "_bias"].mean()), 4),
        "fit": {
            "split_date": BIAS_SPLIT,
            "train": [str(j[j.split == "train"].hh.min().date()),
                      str(j[j.split == "train"].hh.max().date())],
            "test": [str(j[j.split == "test"].hh.min().date()),
                     str(j[j.split == "test"].hh.max().date())],
            "n_train": int(len(tr)),
            "n_test": int(j[(j.split == "test")].dropna(subset=[var + "_bias"]).shape[0]),
            "devices": sorted(tr.device_id.unique().tolist()),
        },
    }


def _offsets(fit, sub, mode):
    """The per-row offset a given correction mode would apply to `sub`."""
    if mode == "none":
        return np.zeros(len(sub))
    if mode == "pooled":
        return np.asarray(fit["pooled_shape"], float)[sub.hour.to_numpy()]
    sh = np.array([fit["seasons"][s] for s in SEASONS])   # (4,24)
    si = {s: i for i, s in enumerate(SEASONS)}
    o = sh[[si[s] for s in sub.season], sub.hour.to_numpy()]
    return o + (fit["level"] if mode == "season+level" else 0.0)


def evaluate_bias_correction(j, fit, var="airtemperature", parkish=("ICTMicroclimate-01", "aws5-0999")):
    """Held-out skill of each correction mode, overall / per season / CBD-only.

    THE RESULT THIS TABLE EXISTS TO MAKE UNMISSABLE: the shape is the defensible half and
    the small half. On held-out CBD site-hours the shape buys ~3% RMSE; adding the level
    buys ~27%. The level is ten times the win and it is the one that cannot be told apart
    from instrument calibration at a single site -- so it is off by default in
    shademe/api/weather.py and this table is the argument for revisiting that, not a footnote.

    `parkish` are the two large-parkland sites (Birrarung Marr, Royal Park). They are not
    CBD air and they are the sites where the level does NOT transfer, which is itself the
    strongest evidence that the level is urban heat island rather than model drift.
    """
    te = j[(j.split == "test")].dropna(subset=[var + "_bias"])
    modes = ("none", "pooled", "season", "season+level")
    rows = []
    groups = [("ALL", te)] + [(f"test {s}", te[te.season == s]) for s in SEASONS]
    groups.append(("CBD only", te[~te.device_id.isin(parkish)]))
    groups.append(("parks only", te[te.device_id.isin(parkish)]))
    for label, sub in groups:
        if len(sub) < 100:
            continue
        b = sub[var + "_bias"].to_numpy(float)
        base = float(np.sqrt((b ** 2).mean()))
        for m in modes:
            e = b - _offsets(fit, sub, m)
            r = float(np.sqrt((e ** 2).mean()))
            rows.append(dict(group=label, mode=m, n=len(sub), bias=float(e.mean()),
                             rmse=r, gain_pct=100.0 * (1.0 - r / base)))
    return pd.DataFrame(rows)


def leave_one_site_out(j, var="airtemperature"):
    """Does a correction fitted WITHOUT a site transfer TO that site?

    This is the test that separates 'model error' from 'this unit's calibration'. A
    per-site table would score perfectly here by construction and tells you nothing; a
    pooled table only wins if the thing it captured is common to the network.
    """
    rows = []
    for dev in sorted(j.device_id.unique()):
        sub = j[(j.split == "test") & (j.device_id == dev)].dropna(subset=[var + "_bias"])
        if len(sub) < 200:
            continue
        f = fit_diurnal_bias(j[j.device_id != dev], var)
        b = sub[var + "_bias"].to_numpy(float)
        r = {m: float(np.sqrt(((b - _offsets(f, sub, m)) ** 2).mean()))
             for m in ("none", "season", "season+level")}
        rows.append(dict(device=dev, exposure=EXPOSURE.get(dev, "?"), n=len(sub),
                         site_level=float(b.mean()), fitted_level=f["level"],
                         rmse_none=r["none"], rmse_shape=r["season"],
                         rmse_shape_level=r["season+level"],
                         gain_shape_pct=100 * (1 - r["season"] / r["none"]),
                         gain_level_pct=100 * (1 - r["season+level"] / r["none"])))
    return pd.DataFrame(rows)


def write_bias(fit, ev, path=BIAS_JSON):
    """Stamp the fit with its held-out skill and write it where the server reads it."""
    out = dict(fit)
    out["held_out_skill"] = [
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
        for r in ev.to_dict("records")]
    out["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        from provenance import stamp
        out["provenance"] = stamp().get("git", {})
    except Exception:
        pass
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"  wrote {path}")
    return path


def fit_bias_main():
    """`python tools/validate_sensors.py --fit-bias` -- fit, evaluate, write."""
    fetch_all()
    print("\nloading sensors ...")
    d = load_sensors()
    s = sites(d)
    for dev in s.index:
        fetch_openmeteo(dev, s.lat[dev], s.lon[dev])
    print("pooling hourly ...")
    j = pooled_hourly(d)
    print(f"  {len(j)} site-hours  {j.hh.min()} .. {j.hh.max()}  "
          f"train={int((j.split=='train').sum())} test={int((j.split=='test').sum())}")
    pd.set_option("display.width", 200, "display.max_columns", 40)

    fit = fit_diurnal_bias(j)
    print("\n=== FITTED DIURNAL SHAPE  (degC, model - sensor, zero-mean per season)")
    t = pd.DataFrame({s_: fit["seasons"][s_] for s_ in SEASONS})
    t.insert(0, "pooled", fit["pooled_shape"])
    t.index.name = "hour"
    print(t.round(3).to_string())
    print(f"\n  level (daily-mean bias, NOT applied by default): {fit['level']:+.3f} C")

    ev = evaluate_bias_correction(j, fit)
    print("\n=== HELD-OUT SKILL  (fitted on <= {}, tested on > {})".format(
        fit["fit"]["train"][1], fit["fit"]["train"][1]))
    print(ev.pivot(index="group", columns="mode", values="rmse")[
        ["none", "pooled", "season", "season+level"]].round(4).to_string())
    print("\n  RMSE gain vs no correction, %")
    print(ev.pivot(index="group", columns="mode", values="gain_pct")[
        ["pooled", "season", "season+level"]].round(2).to_string())
    print("\n  residual bias after correction, degC")
    print(ev.pivot(index="group", columns="mode", values="bias")[
        ["none", "season", "season+level"]].round(3).to_string())

    print("\n=== LEAVE-ONE-SITE-OUT  (fit excludes the site it is tested on)")
    lo = leave_one_site_out(j)
    print(lo.round(3).to_string(index=False))
    print(f"  shape transfers to {int((lo.gain_shape_pct>0).sum())}/{len(lo)} held-out sites, "
          f"level to {int((lo.gain_level_pct>0).sum())}/{len(lo)}")

    print("\n=== READ THIS BEFORE QUOTING ANY OF IT\n  " + BIAS_FINDING.replace("\n", "\n  "))
    write_bias(fit, ev)


# ------------------------------------------- shade / lux contingency (stage 02)
#
# The ONLY external check on the shadow model. Everything above this line validates the
# weather INPUT; this validates the geometry the engine adds to it. It is deliberately a
# 2x2 contingency table and not a regression: the model emits a mask, the sensor emits a
# level, and the honest common ground is "sunlit or not", scored the way a forecast of a
# binary event is scored.
#
# WHAT IT CAN PROVE  that sun position + occlusion put the shadow edge in the right place,
#                    at that point, at that hour.
# WHAT IT CANNOT     anything about longwave, surface temperature, SVF-weighted diffuse or
#                    MRT. A correct shadow mask says nothing about how hot the shade is.

LUX_START, LUX_END = "2014-12-15", "2015-06-05"
LUX_OM = os.path.join(OM_DIR, "lux_fitzroy_2015.csv")
LUX_OM_SITE = (-37.8140, 144.9800)          # Fitzroy Gardens, centroid of the five boards

# The two models scored against each other. Same shipped code path in shademe/physics/shadow.py,
# different arguments -- exactly the rungs of tools/bench_shade_ladder.py, so a number
# here and a number there refer to the same thing. "legacy" is the bottom rung (crown
# extruded to the pavement, nominal 8 m canopy, 1-cell ray step, beam height from the
# requested distance); "slab" is what ships.
TAU_LEGACY = 0.30                            # the hand-picked CANOPY_BLOCK, now retired
LUX_MODELS = {
    "legacy": dict(canopy="v1", base="zeros",   tau=TAU_LEGACY, step=1.0, beam="step"),
    "slab":   dict(canopy="v2", base="base_v2", tau=None,       step=None, beam="hypot"),
}

LUX_BRIGHT = 50.0        # light_avg above this = "the sensor says sunlit"
LUX_SHADED = 0.5         # modelled shade above this = "the model says shaded"
LUX_EL_MIN = 5.0         # shadow.shadow_mask returns all-shadow below this by construction
LUX_DNI_BEAM = 400.0     # W/m2 beam-normal: there is a beam to block
LUX_DNI_NONE = 50.0      # W/m2: there is no beam, so the model's label is meaningless
LUX_LIVE = 90.0          # a live light channel must reach this with the sun above 20 deg

LUX_FINDING = (
    "THE TABLE IS NOT THE RESULT. Read the base rate first.\n"
    "  `light_avg` is a saturating 0-100 channel and it is saturated through all of the "
    "daylight this test uses: of 9587 samples with the sun above 5 deg and a beam in the "
    "sky, ZERO read below 50, and the dimmest of them is 67.5. Diffuse light under a 10 m "
    "crown still pins it. So the observed class is 100% 'sunlit', there is no shaded class "
    "to be wrong about, the false-alarm rate is undefined by division, and the surviving "
    "'hit rate' is arithmetically nothing but the model's own sunlit fraction -- 0.50 for "
    "legacy, 0.46 for the slab, which are facts about the two models and not about "
    "Melbourne. The table is reported because it was asked for and because its degeneracy "
    "IS the finding.\n"
    "  There IS a residual signal -- modelled-sunlit samples read a couple of points "
    "brighter than modelled-shaded ones at the same board and the same solar elevation. "
    "The question is whether that is the beam. The control answers it: repeat the same "
    "comparison on OVERCAST hours (DNI < 50 W/m2), where there is no beam for a crown to "
    "block and the model's label therefore cannot mean anything. If the separation "
    "survives the control it is not shadow geometry -- it is canopy density leaking in "
    "through the diffuse term, i.e. the sensor telling us it is under a tree, which is "
    "not what the mask claims. Read the beam-minus-control column, not the beam column.\n"
    "  COVERAGE. Five of the eight usable boards are in a park with no building within "
    "reach, so the building-shadow path -- the one the CBD product actually rides on -- is "
    "exercised only by the Docklands boards. The canopy layer is 2021 and the measurement "
    "is 2015: seven years of growth are attributed to the model as over-shading. Building "
    "stock IS contemporaneous (building-outlines-2015).")


def load_lux():
    """The 2015 five-minute light archive, tz-aware, with solar position per sample."""
    l = pd.read_csv(LUX_CSV, sep=";", low_memory=False)
    l["t"] = pd.to_datetime(l["timestamp"], utc=True)
    l["ltime"] = l["t"].dt.tz_convert(TZ)
    l["date"] = l["ltime"].dt.date
    l = l.rename(columns={"boardid": "board", "location": "site"})
    l = l.dropna(subset=["light_avg", "latitude", "longitude"])
    u = pd.DatetimeIndex(sorted(l["t"].unique()))
    sp = pvlib.solarposition.get_solarposition(u, *LUX_OM_SITE)
    l = l.merge(pd.DataFrame({"t": u, "az": sp["azimuth"].values,
                              "el": sp["apparent_elevation"].values}), on="t")
    l["bright"] = l["light_avg"] >= LUX_BRIGHT
    return l


def lux_weather(force=False):
    """Hourly Open-Meteo archive over the 2015 campaign. Cached; ERA5 goes back to 1940.

    Needed for one thing only: whether there was a BEAM. 'Sunlit vs shaded' is undefined
    under cloud, and scoring a shadow mask against an overcast hour measures the weather.
    """
    if os.path.exists(LUX_OM) and not force:
        w = pd.read_csv(LUX_OM)
    else:
        os.makedirs(OM_DIR, exist_ok=True)
        r = requests.get(ARCHIVE, params=dict(
            latitude=LUX_OM_SITE[0], longitude=LUX_OM_SITE[1],
            start_date=LUX_START, end_date=LUX_END,
            hourly=",".join(OM_VARS + ["direct_normal_irradiance"]), timezone=TZ),
            timeout=300)
        r.raise_for_status()
        w = pd.DataFrame(r.json()["hourly"])
        w.to_csv(LUX_OM, index=False)
    w["hh"] = pd.to_datetime(w["time"])
    return w


def lux_boards(l, w=None):
    """One row per board, with the four measurements the screen is made on.

    Every exclusion below is a MEASUREMENT on the board, not a judgement about the site.
    The published note for this dataset says the Docklands units 'are almost certainly
    indoors'; the temperature column here says they are not, so it is not used.

    night_bright  fraction of samples with the sun below -6 deg that still read bright.
                  Artificial light near the board. High at Docklands, ~0.05 everywhere
                  (that residual is the summer twilight tail, not lighting).
    t_range_ratio the board's median daily temperature range over Open-Meteo's. An
                  air-conditioned interior DAMPS the range; a sun-exposed housing
                  AMPLIFIES it. Docklands reads 1.0-1.7, Fitzroy 0.54-0.83 -- so the
                  Docklands boards are outdoors and less shaded, not indoors.
    live_days     board-days on which the light channel reached LUX_LIVE with the sun
                  above 20 deg. Every board's channel dies in May 2015 -- all nine read
                  under 10 at local noon -- and three Docklands boards go earlier.
    in_footprint  the board's grid cell falls inside a 2015 building outline. The model
                  is then being asked about a point inside a solid, and cannot answer.
    """
    from pyproj import Transformer
    g = json.load(open(os.path.join(OUT_DIR, "grid.json")))
    dsm_b = np.load(os.path.join(OUT_DIR, "dsm_buildings.npy"))
    tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)

    b = l.groupby("board").agg(site=("site", "first"), n=("light_avg", "size"),
                               lat=("latitude", "median"), lon=("longitude", "median"))
    x, y = tf.transform(b["lon"].values, b["lat"].values)
    b["col"] = ((x - g["bounds"][0]) / g["cell"]).astype(int)
    b["row"] = ((g["bounds"][3] - y) / g["cell"]).astype(int)
    b["bldg_m"] = [float(dsm_b[r, c]) for r, c in zip(b["row"], b["col"])]
    b["in_footprint"] = b["bldg_m"] > 0.5

    night = l[l.el < -6.0]
    b["night_bright"] = night.groupby("board")["bright"].mean()

    w = lux_weather() if w is None else w
    om = w.assign(date=w.hh.dt.date).groupby("date")["temperature_2m"].agg(["min", "max"])
    om = (om["max"] - om["min"]).rename("om_range")
    day = l.groupby(["board", "date"])["temp_avg"].agg(["min", "max"])
    day = (day["max"] - day["min"]).rename("rng").reset_index().join(om, on="date")
    b["t_range_ratio"] = (day.groupby("board")["rng"].median()
                          / day.groupby("board")["om_range"].median())

    live = lux_live_days(l)
    b["live_days"] = live.groupby("board")["live"].sum()
    b["days"] = live.groupby("board")["live"].size()

    b["excluded"] = np.where(b["in_footprint"], "cell inside a 2015 building footprint", "")
    return b


def lux_live_days(l):
    """(board, date, live). A light channel that never saturates with the sun above 20 deg
    is not reporting light. Not circular with the shade test: no outdoor board in Melbourne
    spends a whole day under something that keeps a saturating channel below 90 -- and the
    boards that fail do so for a whole MONTH, all nine of them, from 2015-05 on."""
    hi = l[l.el > 20.0]
    m = hi.groupby(["board", "date"])["light_avg"].max().rename("daymax").reset_index()
    m["live"] = m["daymax"] >= LUX_LIVE
    return m


def _lux_shade(l, boards, models=("legacy", "slab")):
    """{model: DataFrame(timestamp x board)} of modelled shade in [0,1].

    Marched with shadow.point_shade, NOT read out of out/shade_HH.npy: those rasters are
    for one 2026 demo day and this is 2015, so the sun is in a different place at the same
    clock hour. point_shade is the same walk as shadow_mask with the receiver height term,
    vectorised over the nine points, so a whole campaign costs one march per timestamp
    instead of one raster per timestamp.

    z0 = 0. Mount height is not published for these boards, and the rasters the engine
    ships answer the z0 = 0 question too, so this scores what the engine actually uses.
    """
    from shademe.physics.shadow import point_shade, RAY_STEP
    from shademe.config import CELL, TAU_LEAF
    d = {"b": np.load(os.path.join(OUT_DIR, "dsm_buildings.npy")),
         "v1": np.load(os.path.join(OUT_DIR, "dsm_canopy.npy")),
         "v2": np.load(os.path.join(OUT_DIR, "dsm_canopy_v2.npy")),
         "base_v2": np.load(os.path.join(OUT_DIR, "dsm_canopy_base_v2.npy"))}
    d["zeros"] = np.zeros_like(d["v1"])
    rows = boards["row"].values.astype(np.int64)
    cols = boards["col"].values.astype(np.int64)
    z0 = np.zeros(len(boards), dtype=np.float32)

    sun = l.groupby("t").agg(az=("az", "first"), el=("el", "first"))
    out = {}
    for name in models:
        c = dict(LUX_MODELS[name])
        c["tau"] = TAU_LEAF if c["tau"] is None else c["tau"]
        c["step"] = RAY_STEP if c["step"] is None else c["step"]
        t0 = time.time()
        a = np.empty((len(sun), len(boards)), dtype=np.float32)
        for i, (az, el) in enumerate(zip(sun["az"].values, sun["el"].values)):
            a[i] = point_shade(d["b"], d[c["canopy"]], d[c["base"]], CELL, az, el,
                               rows, cols, z0, tau_leaf=c["tau"], step=c["step"],
                               beam=c["beam"])
        out[name] = pd.DataFrame(a, index=sun.index, columns=list(boards.index))
        print(f"  [lux] {name:6} marched {len(sun)} sun positions in {time.time()-t0:.0f}s")
    return out


def contingency(pred_sunlit, obs_sunlit):
    """2x2 forecast verification. `pred` is the model, `obs` is the sensor.

    hit_rate (POD)  a/(a+c)  of the hours that WERE sunlit, the fraction called sunlit
    false_alarm (F) b/(b+d)  of the hours that were SHADED, the fraction called sunlit
    pss                      Peirce skill score, POD - F. 0 = no skill over always-yes.
    base_rate                fraction of observations that are sunlit. Read this FIRST:
                             when it is near 1 there is no shaded class to be wrong about
                             and both rates are pinned near 1 for free.
    """
    p = np.asarray(pred_sunlit, dtype=bool)
    o = np.asarray(obs_sunlit, dtype=bool)
    a = int(( p &  o).sum()); b = int(( p & ~o).sum())
    c = int((~p &  o).sum()); d = int((~p & ~o).sum())
    pod = a / (a + c) if a + c else float("nan")
    far_rate = b / (b + d) if b + d else float("nan")
    return {"n": a + b + c + d, "hit": a, "false_alarm": b, "miss": c, "correct_neg": d,
            "hit_rate": pod, "false_alarm_rate": far_rate,
            "pss": pod - far_rate if a + c and b + d else float("nan"),
            "base_rate": (a + c) / (a + b + c + d) if a + b + c + d else float("nan"),
            "bias": (a + b) / (a + c) if a + c else float("nan")}


def lux_samples(l=None, w=None, verbose=True):
    """Every gate applied, both models marched, one row per (board, 5-min sample).

    The gates, in order, and why each one is not a way of flattering the model:
      1. board screen      -- a cell inside a building footprint is a question the model
                              cannot be asked (lux_boards).
      2. live board-day    -- the light channel has to be reporting light (lux_live_days).
      3. el >= LUX_EL_MIN  -- below it shadow_mask returns all-shadow as a hardcoded floor,
                              so scoring there scores the floor and not the geometry.
      4. beam class        -- DNI splits the samples into 'there is a beam to block' and
                              the control, 'there is not'. Nothing is dropped; the control
                              is the point.
    """
    l = load_lux() if l is None else l
    w = lux_weather() if w is None else w
    b = lux_boards(l, w)
    keep = b[b["excluded"] == ""]
    if verbose:
        print("\n=== BOARD SCREEN  (light dataset, 2014-12-15 .. 2015-06-05)")
        print(b[["site", "n", "bldg_m", "night_bright", "t_range_ratio",
                 "live_days", "days", "excluded"]].round(3).to_string())
        print("  night_bright: artificial light near the board (Docklands is a lit "
              "precinct).\n  t_range_ratio: board daily temperature range / Open-Meteo's. "
              ">1 is a sun-baked\n    housing, <1 is shade. It REFUTES the published "
              "'almost certainly indoors' reading\n    of the Docklands units, so they are "
              "kept and reported separately.")

    live = lux_live_days(l)
    s = l[l.board.isin(keep.index)].merge(live, on=["board", "date"], how="left")
    s = s[(s["live"] == True) & (s.el >= LUX_EL_MIN)].copy()      # noqa: E712 -- NaN is not live

    sh = _lux_shade(s, keep)
    for name, m in sh.items():
        s["shade_" + name] = m.values[
            m.index.get_indexer(s["t"]), [m.columns.get_loc(x) for x in s["board"]]]
        s["sunlit_" + name] = s["shade_" + name] < LUX_SHADED

    s["hh"] = s["ltime"].dt.tz_localize(None).dt.floor("h")
    s = s.merge(w[["hh", "direct_normal_irradiance", "cloud_cover"]], on="hh", how="left")
    s["dni"] = s["direct_normal_irradiance"]
    s["beam"] = np.where(s.dni >= LUX_DNI_BEAM, "beam",
                         np.where(s.dni < LUX_DNI_NONE, "control", "mixed"))
    s["elb"] = pd.cut(s.el, [5, 15, 25, 35, 45, 90])
    return s, b


def lux_power(s, model, by=("board", "elb")):
    """The control test. Does the separation survive the removal of the beam?

    Inside one board and one solar-elevation band, compare `light_avg` for samples the
    model calls sunlit against samples it calls shaded, as a median difference. Do it
    twice: on beam hours, and on the overcast control where the model's label cannot
    carry information about a beam because there is no beam.

    Stratifying by board is not optional. Five boards are five instruments, and the board
    that is modelled shaded most often (505, under a 10 m crown) is also the board that
    reads lowest -- a pooled comparison would score that calibration offset as skill.
    Stratifying by elevation removes the other confound: shade and low sun co-occur, and
    low sun is darker for reasons that have nothing to do with occlusion.
    """
    rows = []
    for key, g in s.groupby(list(by), observed=True):
        r = {k: v for k, v in zip(by, key if isinstance(key, tuple) else (key,))}
        for cls in ("beam", "control"):
            q = g[g.beam == cls]
            hi = q.light_avg[q["sunlit_" + model]]
            lo = q.light_avg[~q["sunlit_" + model]]
            r[cls + "_n"] = min(len(hi), len(lo))
            r[cls] = (hi.median() - lo.median()) if len(hi) >= 10 and len(lo) >= 10 else np.nan
        rows.append(r)
    d = pd.DataFrame(rows)
    d["beam_minus_control"] = d["beam"] - d["control"]
    return d


def lux_power_ci(p, n_boot=4000, seed=0):
    """Stratified bootstrap on the pooled beam-minus-control median. Strata are the
    resampling unit, not samples: two rows from the same board are not independent."""
    v = p["beam_minus_control"].dropna().values
    if len(v) < 3:
        return {"n": len(v), "median": float("nan"), "lo": float("nan"),
                "hi": float("nan"), "p_sign": float("nan")}
    rng = np.random.default_rng(seed)
    bs = np.median(rng.choice(v, size=(n_boot, len(v)), replace=True), axis=1)
    k = int((v > 0).sum())
    # two-sided sign test against 'the beam changes nothing'
    from math import comb
    tail = sum(comb(len(v), i) for i in range(max(k, len(v) - k), len(v) + 1)) / 2 ** len(v)
    return {"n": len(v), "median": float(np.median(v)),
            "lo": float(np.percentile(bs, 2.5)), "hi": float(np.percentile(bs, 97.5)),
            "pos": k, "p_sign": min(1.0, 2 * tail)}


def compare_shade_lux(l=None, w=None, models=("legacy", "slab"), verbose=True):
    """Score the shadow geometry against the 2015 light boards. Returns a dict of tables.

    Replaces the stub. `date`/`mac` are gone on purpose: one board on one day is a
    thousand correlated samples of one shadow, and the thing worth reporting is the whole
    campaign under one set of gates.

    READ LUX_FINDING BEFORE QUOTING ANY NUMBER THIS RETURNS.
    """
    s, screen = lux_samples(l, w, verbose=verbose)
    beam = s[s.beam == "beam"]
    out = {"screen": screen, "n_samples": len(s), "n_beam": len(beam), "tables": {}}

    if verbose:
        print("\n=== SAMPLE BUDGET")
        print(f"  {len(s)} board-samples survive the gates, el >= {LUX_EL_MIN:.0f} deg")
        print(s.groupby(["site", "beam"]).size().unstack(fill_value=0).to_string())
        h, edges = np.histogram(s[s.beam == "beam"].light_avg, bins=np.arange(0, 101, 10))
        print("\n=== IS THE CHANNEL INFORMATIVE?  light_avg histogram, beam hours, "
              "bins of 10")
        print("  " + "  ".join(f"{int(e):>3}-{int(e)+9:<3}" for e in edges[:-1]))
        print("  " + "  ".join(f"{v:>7d}" for v in h))
        print("  A sensor that could see shade would be bimodal. This one is a spike at "
              "the top of\n  its range: light_max == light_min == light_avg in every row "
              "of the file, so there is\n  no within-window variance to fall back on "
              "either -- it is one saturated spot reading.")
        print(f"\n=== CONTINGENCY  beam hours only (DNI >= {LUX_DNI_BEAM:.0f} W/m2), "
              f"observed sunlit = light_avg >= {LUX_BRIGHT:.0f}")

    for m in models:
        rows = []
        for tag, q in [("ALL", beam)] + [(f"el {int(e.left)}-{int(e.right)}", beam[beam.elb == e])
                                         for e in beam.elb.cat.categories if (beam.elb == e).any()] \
                    + [(f"site {k}", v) for k, v in beam.groupby("site")]:
            if not len(q):
                continue
            rows.append(dict(subset=tag, **contingency(q["sunlit_" + m], q["bright"])))
        t = pd.DataFrame(rows)
        out["tables"][m] = t
        if verbose:
            print(f"\n-- {m}")
            print(t[["subset", "n", "hit", "false_alarm", "miss", "correct_neg",
                     "hit_rate", "false_alarm_rate", "pss", "base_rate", "bias"]]
                  .round(3).to_string(index=False))

    if verbose:
        print("\n=== THRESHOLD SENSITIVITY  (the observed class is a choice; here is what "
              "it buys)")
        rows = []
        for th in (10, 30, 50, 70, 85, 90, 93, 95):
            r = {"light_avg >=": th, "base_rate": float((beam.light_avg >= th).mean())}
            for m in models:
                c = contingency(beam["sunlit_" + m], beam.light_avg >= th)
                r[m + "_hit"] = c["hit_rate"]; r[m + "_fa"] = c["false_alarm_rate"]
                r[m + "_pss"] = c["pss"]
            rows.append(r)
        print(pd.DataFrame(rows).round(3).to_string(index=False))

    out["power"] = {}
    out["power_ci"] = {}
    if verbose:
        print("\n=== CONTROL TEST  median light_avg (model sunlit) - (model shaded), "
              "degrees of the\n    0-100 channel, within board and solar-elevation band. "
              "`control` is overcast\n    (DNI < %.0f), where the model's label cannot be "
              "about a beam." % LUX_DNI_NONE)
    for m in models:
        p = lux_power(s, m)
        out["power"][m] = p
        ok = p.dropna(subset=["beam", "control"])
        if verbose:
            print(f"\n-- {m}   ({len(p.dropna(subset=['beam']))} strata with a beam "
                  f"comparison, {len(ok)} with both)")
            print(p.round(2).to_string(index=False))
            if len(ok):
                ci = lux_power_ci(p)
                out["power_ci"][m] = ci
                print(f"   pooled over {ci['n']} strata: beam {ok['beam'].median():+.2f}, "
                      f"control {ok['control'].median():+.2f}, "
                      f"BEAM-MINUS-CONTROL {ci['median']:+.2f} "
                      f"[95% CI {ci['lo']:+.2f}, {ci['hi']:+.2f}], "
                      f"positive in {ci['pos']}/{ci['n']} strata (sign test p={ci['p_sign']:.2f})")

    if verbose:
        lux_verdict(out, beam, models)
        try:
            from shademe import provenance
            print("\n  " + provenance.line())
        except Exception as e:
            print(f"\n  (no provenance stamp: {e!r})")
        print("\n=== READ THIS BEFORE QUOTING ANY OF IT\n  " + LUX_FINDING.replace("\n", "\n  "))
    return out


def lux_verdict(out, beam, models):
    """The one paragraph anyone is allowed to quote, built from the run's own numbers."""
    lo = float(beam.light_avg.min())
    dark = int((beam.light_avg < LUX_BRIGHT).sum())
    print("\n=== VERDICT")
    print("  The instrument has no power here, and that is the result.")
    print(f"  * {dark} of {len(beam)} beam-hour samples read below {LUX_BRIGHT:.0f} on the "
          f"0-100 light channel\n    (dimmest sample of the whole set: {lo:.1f}). With no "
          f"observed SHADED class the 2x2 table\n    has no false-alarm rate to report, "
          f"and its 'hit rate' is arithmetically just the\n    model's own sunlit "
          f"fraction -- for both models, on the same boards and hours.")
    for m in models:
        c = out["power_ci"].get(m, {})
        if c:
            print(f"  * {m:6}: beam-minus-control {c['median']:+.2f} points "
                  f"[95% CI {c['lo']:+.2f}, {c['hi']:+.2f}], sign test p={c['p_sign']:.2f} "
                  f"over {c['n']} strata.")
    print("    Both intervals span zero. Neither model is supported and neither is "
          "refuted:\n    the beam-specific separation this dataset can resolve is "
          "smaller than its noise.")
    print("  * Do NOT report the raw beam column as a win for the slab model. It is the "
          "same size\n    on overcast hours, where there is no beam to block, so it is "
          "the sensor detecting\n    canopy overhead through the DIFFUSE term -- a "
          "different claim from the one the\n    shadow mask makes.")
    print("  What would give power: a light channel that does not saturate, or a "
          "pyranometer.\n  No City of Melbourne open dataset carries either, so this is "
          "the ceiling of what the\n  published data can say about the shadow geometry. "
          "The slab model's evidence stays\n  what it was -- internal consistency plus "
          "the ASU/MaRTy shade RANKING -- and the\n  canopy work must not be quoted as "
          "externally validated.")


# ---------------------------------------------------------------- stubs

def compare_mrt(d, date, device, mrt_dir="out"):
    """STUB -- MRT/UTCI comparison. NOT IMPLEMENTED, and only PARTLY possible ever.

    Plugs in: `out/mrt_HH.npy` (float32 (h,w), degC) + `out/tsurf_HH.npy` + `out/grid.json`
    per the README. Sample the raster at the site's projected (x,y), pair with
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
    print("\n=== TIMEZONE ALIGNMENT")
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
    print("  That floor is PARTLY REMOVABLE and partly removed: see --fit-bias, which")
    print("  fits the diurnal shape of this error on held-out days. What it does NOT")
    print("  remove by default is the ~1 C level, which is the larger half. The numbers")
    print("  above are the RAW feed; shademe/api/weather.py ships the corrected one.")

    print("\n=== WIND HEIGHT\n  " + WIND_HEIGHT_NOTE)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shade-lux", action="store_true",
                    help="score the shadow geometry against the 2015 light boards "
                         "(stage 02): 2x2 contingency table for the slab model and the "
                         "legacy model on the same boards and hours, plus the overcast "
                         "control that says whether the table means anything")
    ap.add_argument("--fit-bias", action="store_true",
                    help="fit the Open-Meteo diurnal bias correction on the train split, "
                         "score it on held-out days, and write data/openmeteo_bias.json")
    a = ap.parse_args()
    if a.shade_lux:
        compare_shade_lux()
    elif a.fit_bias:
        fit_bias_main()
    else:
        main()
