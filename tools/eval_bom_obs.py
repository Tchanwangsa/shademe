"""Is BOM's free station observation feed worth wiring into shademe/api/weather.py?

THE QUESTION. The live feed (http://www.bom.gov.au/fwo/IDV60901/IDV60901.95936.json,
Melbourne Olympic Park, 2.1 km from the CBD reference point, 30-minutely, rolling 72 h,
no key) gives a real thermometer reading where today we have a ~9 km ECMWF grid cell
corrected by a climatological table. Does the thermometer actually predict CBD air
temperature better, and does that advantage survive to the forecast hours the app's
hour slider actually shows?

WHY THE HISTORICAL PROXY. The live feed only retains 72 h, and the CoM sensor archive
stops 2026-08-18, so the two never overlap and the live feed cannot be scored directly.
NOAA ISD publishes the SAME station (WMO 95936) hourly and QC'd, 2023..2025, which is
what this scores. ISD is the hourly synoptic subset of what BOM broadcasts; treat the
numbers here as a lower bound on the live feed, which is 30-minutely.

SPLIT. NOT the production BIAS_SPLIT. ISD ends 2025-08-24, so a 2025-07-01 split would
hold out eight winter weeks and no summer at all. Everything here -- including the
Open-Meteo correction it is compared against -- is refitted on train and scored on test
below, so every candidate sees identical hours.
"""
import os, sys
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validate_sensors as V

ISD_DIR = os.path.join(V.DATA, "isd")
OP_LAT, OP_LON = -37.8333333, 144.9833333        # ISD coords for WMO 95936
OP_KEY = "_bom_olympicpark"                       # pseudo-site for the co-located archive

TRAIN_END = "2024-11-01"                          # test = 2024-11-01 .. 2025-08-24
SEASONS, SEASON_OF = V.SEASONS, V.SEASON_OF
PARKISH = ("ICTMicroclimate-01", "aws5-0999")


def load_isd(name="olympicpark"):
    """ISD hourly -> DataFrame on tz-naive LOCAL hour, same index convention as open_meteo()."""
    fs = sorted(f for f in os.listdir(ISD_DIR) if f.startswith(name) and f.endswith(".csv"))
    d = pd.concat([pd.read_csv(os.path.join(ISD_DIR, f), low_memory=False) for f in fs],
                  ignore_index=True)
    def scaled(col):
        t = d[col].astype(str).str.split(",", expand=True)
        v, q = pd.to_numeric(t[0], errors="coerce"), t[1]
        return (v.where(v.abs() != 9999) / 10.0).where(q.isin(["0", "1", "4", "5", "9"]))
    ta, td = scaled("TMP"), scaled("DEW")
    # Magnus, same form as shademe/physics/surface_temp.py's vapour pressure -- RH from the
    # dew point the station actually reports, not from a model humidity field.
    e = lambda T: 6.112 * np.exp(17.67 * T / (T + 243.5))
    u = pd.to_datetime(d.DATE, errors="coerce").dt.tz_localize("UTC")
    o = pd.DataFrame({"hh": u.dt.tz_convert(V.TZ).dt.tz_localize(None).dt.floor("h"),
                      "obs": ta, "obs_rh": (100.0 * e(td) / e(ta)).clip(0, 100)})
    return o.dropna(subset=["hh"]).groupby("hh", as_index=False)[["obs", "obs_rh"]].mean()


def build(devices=None):
    """Sensor x Open-Meteo x station-observation, one row per site-hour."""
    V.fetch_openmeteo(OP_KEY, OP_LAT, OP_LON)                 # archive AT the station
    d = V.load_sensors()
    j = V.pooled_hourly(d, devices)                           # sensor + co-located model
    op = V.open_meteo(OP_KEY)[["ltime", "temperature_2m"]].rename(
        columns={"ltime": "hh", "temperature_2m": "om_at_station"})
    j = j.merge(op, on="hh", how="left").merge(load_isd(), on="hh", how="inner")
    j = j.dropna(subset=["airtemperature", "temperature_2m", "obs", "om_at_station"])
    j["split"] = np.where(j.hh < pd.Timestamp(TRAIN_END), "train", "test")
    j["anom"] = j.obs - j.om_at_station                       # live model error at the station
    prev = j.set_index("hh").obs.groupby(level=0).first()
    j["obs_lag1"] = j.hh.map(lambda t: prev.get(t - pd.Timedelta(hours=1), np.nan))
    return j


def _fit(tr, col, truth="airtemperature"):
    """Season-shape (zero-mean over the day) + level, the same form as fit_diurnal_bias()."""
    e = (tr[col] - tr[truth]).dropna()
    tr = tr.loc[e.index]
    raw = pd.DataFrame({"hour": tr.hour, "season": tr.season, "e": e}).pivot_table(
        index="hour", columns="season", values="e", aggfunc="mean").reindex(
        index=range(24), columns=list(SEASONS))
    raw = raw.fillna(e.mean())
    return {"shape": (raw - raw.mean(axis=0)), "level": float(e.mean())}


def _apply(f, sub, col, level=True):
    """Offset-corrected column. NaNs in `col` propagate; score() drops them."""
    sh = np.array([f["shape"][s].to_numpy() for s in SEASONS])
    si = {s: i for i, s in enumerate(SEASONS)}
    off = sh[[si[s] for s in sub.season], sub.hour.to_numpy()]
    return sub[col].to_numpy(float) - off - (f["level"] if level else 0.0)


def candidates(j, leads=(0, 1, 2, 3, 6, 12)):
    """Every predictor of CBD sensor air temperature, all fitted on train only."""
    tr, te = j[j.split == "train"], j[j.split == "test"].copy()
    f_om, f_obs = _fit(tr, "temperature_2m"), _fit(tr, "obs")
    out = {
        "om_raw":            te.temperature_2m.to_numpy(float),
        "om_shape":          _apply(f_om, te, "temperature_2m", level=False),
        "om_shape_level":    _apply(f_om, te, "temperature_2m", level=True),
        "obs_raw":           te.obs.to_numpy(float),
        "obs_shape_level":   _apply(f_obs, te, "obs", level=True),
    }
    # Anchoring: correct the model forecast by the model's OWN error at the station,
    # observed k hours before the target hour. k=0 is the nowcast; k>0 is what the
    # hour slider actually needs, and is the test that decides this.
    lag = j.set_index("hh").anom.groupby(level=0).first()
    for k in leads:
        a = te.hh.map(lambda t: lag.get(t - pd.Timedelta(hours=k), np.nan)).to_numpy(float)
        out[f"anchor_k{k}"] = _apply(f_om, te, "temperature_2m", level=True) + a
    return te, out


def score(te, out, label, mask=None, truth="airtemperature", base="om_shape_level"):
    sub = te if mask is None else te[mask]
    idx = np.flatnonzero(np.asarray(mask)) if mask is not None else slice(None)
    truth = sub[truth].to_numpy(float)
    rows = []
    for name, pred in out.items():
        p = np.asarray(pred)[idx]
        ok = np.isfinite(p) & np.isfinite(truth)
        if ok.sum() < 100:
            continue
        e = p[ok] - truth[ok]
        rows.append(dict(group=label, model=name, n=int(ok.sum()), bias=e.mean(),
                         mae=np.abs(e).mean(), rmse=float(np.sqrt((e ** 2).mean()))))
    df = pd.DataFrame(rows)
    b = df.loc[df.model == base, "rmse"].iloc[0]
    df["vs_prod_pct"] = 100.0 * (1.0 - df.rmse / b)
    return df


def staleness(j):
    """THE QUESTION FOR A PURE-REALTIME APP: how stale may the observation be?

    The live feed is 30-minutely and published ~1 min after the hour it stamps, so a
    request served at any moment uses a reading 0-30 min old. ISD is hourly and cannot
    resolve 30 min directly, so this brackets it: lag 0 is the unattainable best case,
    lag 1 h is strictly worse than anything operational. If lag 1 h still beats the
    production Open-Meteo path, every operational staleness does too, by monotonicity.
    """
    tr, te = j[j.split == "train"], j[j.split == "test"].copy()
    f_om, f0, f1 = (_fit(tr, "temperature_2m"), _fit(tr, "obs"), _fit(tr, "obs_lag1"))
    out = {"om_shape_level": _apply(f_om, te, "temperature_2m"),
           "obs_lag0": _apply(f0, te, "obs"),
           "obs_lag1h": _apply(f1, te, "obs_lag1")}
    groups = [("ALL test", None), ("sensor >= 28C", te.airtemperature >= 28),
              ("sensor >= 32C", te.airtemperature >= 32)]
    return pd.concat([score(te, out, lab, m) for lab, m in groups], ignore_index=True)


def humidity(j):
    """Does the station beat Open-Meteo on RH too? RH drives UTCI and the vapour term.

    Wind is deliberately NOT tested here: CoM anemometer height is undocumented
    (V.WIND_HEIGHT_NOTE), so any wind comparison confounds model error with an unknown
    log-profile correction.
    """
    k = j.dropna(subset=["relativehumidity", "relative_humidity_2m", "obs_rh"])
    tr, te = k[k.split == "train"], k[k.split == "test"].copy()
    f_om = _fit(tr, "relative_humidity_2m", truth="relativehumidity")
    f_ob = _fit(tr, "obs_rh", truth="relativehumidity")
    out = {"om_raw": te.relative_humidity_2m.to_numpy(float),
           "om_corrected": _apply(f_om, te, "relative_humidity_2m"),
           "obs_raw": te.obs_rh.to_numpy(float),
           "obs_corrected": _apply(f_ob, te, "obs_rh")}
    groups = [("ALL test", None), ("sensor >= 28C", te.airtemperature >= 28)]
    return pd.concat([score(te, out, lab, m, truth="relativehumidity", base="om_corrected")
                      for lab, m in groups], ignore_index=True)


def leave_one_site_out(j, hot=28.0):
    """Does a station->CBD offset fitted WITHOUT a site still work AT that site?

    The same guard fit_diurnal_bias() uses per-site. If the win only appears when the
    offset has seen the site's own instrument, it is fitting that unit's calibration and
    will not transfer to a route the app actually prices.
    """
    rows = []
    for dev in sorted(j.device_id.unique()):
        tr = j[(j.split == "train") & (j.device_id != dev)]
        te = j[(j.split == "test") & (j.device_id == dev)]
        te = te[te.airtemperature >= hot]
        if len(tr) < 1000 or len(te) < 100:
            continue
        f_obs, f_om = _fit(tr, "obs"), _fit(tr, "temperature_2m")
        t = te.airtemperature.to_numpy(float)
        r = lambda p: float(np.sqrt(((p - t) ** 2).mean()))
        rows.append(dict(device=dev, exposure=V.EXPOSURE.get(dev, "?"), n=len(te),
                         prod_rmse=r(_apply(f_om, te, "temperature_2m")),
                         obs_rmse=r(_apply(f_obs, te, "obs"))))
    df = pd.DataFrame(rows)
    df["gain_pct"] = 100.0 * (1.0 - df.obs_rmse / df.prod_rmse)
    return df


def main():
    j = build()
    print(f"site-hours joined: {len(j):,}   "
          f"train {j[j.split=='train'].hh.min().date()}..{j[j.split=='train'].hh.max().date()}  "
          f"test {j[j.split=='test'].hh.min().date()}..{j[j.split=='test'].hh.max().date()}")
    te, out = candidates(j)
    groups = [("ALL test", None), ("CBD only", ~te.device_id.isin(PARKISH))]
    groups += [(f"test {s}", te.season == s) for s in SEASONS]
    groups += [("sensor >= 28C", te.airtemperature >= 28), ("sensor >= 32C", te.airtemperature >= 32)]
    tbl = pd.concat([score(te, out, lab, m) for lab, m in groups], ignore_index=True)
    pd.set_option("display.width", 200)
    for lab in tbl.group.unique():
        print(f"\n=== {lab}")
        print(tbl[tbl.group == lab].drop(columns="group").to_string(
            index=False, float_format=lambda x: f"{x:7.3f}"))
    tbl.to_csv(os.path.join(V.ROOT, "out", "eval_bom_obs.csv"), index=False)
    print("\n=== LEAVE-ONE-SITE-OUT, hours where that site read >= 28 C")
    print("    (offset fitted on the other eleven sites only)")
    print(leave_one_site_out(j).to_string(index=False, float_format=lambda x: f"{x:7.3f}"))
    fmt = lambda d: d.to_string(index=False, float_format=lambda x: f"{x:7.3f}")
    print("\n=== STALENESS  (lag 1 h is strictly worse than any operational case)")
    print(fmt(staleness(j)))
    print("\n=== RELATIVE HUMIDITY  (%RH; baseline is corrected Open-Meteo)")
    print(fmt(humidity(j)))


if __name__ == "__main__":
    main()
