"""Is Himawari's observed radiation better than the ERA5 radiation the engine uses today?

WHY THIS NEEDS AN INDIRECT TEST. Melbourne's open data has no pyranometer -- see
validate_sensors.WHAT THIS CANNOT VALIDATE -- so neither source can be scored against
ground truth the way air temperature was. What CAN be done is ask which one is more
physically self-consistent, using two arbiters that neither source can see:

  1. CLEAR-SKY. On hours with zero cloud and a high sun, radiation must sit near the
     clear-sky ceiling and its diffuse fraction near the clear-sky value (~12%).
     pvlib's Ineichen model with the Linke turbidity climatology supplies both.
  2. ERBS. Across ALL hours, real sky follows a tight empirical relationship between
     clearness index kt and diffuse fraction. A source far off that curve is suspect.
     Caveat: a source whose own retrieval uses an Erbs-like decomposition scores well
     here by construction, so this test can only condemn, not crown.

THE NUMBER THE APP ACTUALLY CARES ABOUT is neither -- it is what the difference does to
a pedestrian standing in shade, where diffuse IS the shortwave budget. shade_load()
converts it to the W/m2 and the UTCI degrees the router would price differently.
"""
import os, sys, json, gzip
import numpy as np, pandas as pd, pvlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RAD = os.path.join(ROOT, "data", "radiation")
LAT, LON, TZ, ELEV = -37.8136, 144.9631, "Australia/Melbourne", 31.0
LOC = pvlib.location.Location(LAT, LON, TZ, ELEV)


def load(name):
    with gzip.open(os.path.join(RAD, f"{name}.json.gz"), "rt") as f:
        h = json.load(f)["hourly"]
    d = pd.DataFrame(h)
    d["t"] = pd.to_datetime(d.pop("time")).dt.tz_localize(TZ, ambiguous="NaT",
                                                          nonexistent="NaT")
    return d.dropna(subset=["t"]).set_index("t")


def frame():
    """One table: both sources, solar geometry, clear-sky ceiling, Erbs expectation.

    Open-Meteo stamps radiation as the MEAN OVER THE PRECEDING HOUR (see the UNITS note
    in surface_temp.py), so solar position is evaluated at the half-hour, not on the
    hour. Getting this wrong tilts the morning against the afternoon.
    """
    s, e = load("satellite"), load("era5")
    d = pd.DataFrame(index=s.index.intersection(e.index))
    for src, f in (("sat", s), ("era", e)):
        d[f"{src}_ghi"] = f.shortwave_radiation
        d[f"{src}_dir"] = f.direct_radiation          # on the HORIZONTAL plane
        d[f"{src}_dif"] = f.diffuse_radiation
    d["cloud"] = e.cloud_cover
    mid = d.index - pd.Timedelta(minutes=30)
    sp = LOC.get_solarposition(mid)
    d["elev"] = sp.apparent_elevation.to_numpy()
    d["zen"] = sp.apparent_zenith.to_numpy()
    cs = LOC.get_clearsky(mid, model="ineichen")
    d["cs_ghi"] = cs.ghi.to_numpy()
    d["cs_dif"] = cs.dhi.to_numpy()
    e0 = pvlib.irradiance.get_extra_radiation(mid)
    d["ghi_et"] = (e0.to_numpy() * np.cos(np.radians(d.zen))).clip(0)
    d["ta"] = e.temperature_2m
    return d.dropna(subset=["sat_ghi", "era_ghi"])


def selfconsistency(d):
    """Does direct + diffuse equal the reported global? A source that fails this is broken."""
    rows = []
    for src in ("sat", "era"):
        m = d.elev > 5
        r = (d[f"{src}_dir"] + d[f"{src}_dif"] - d[f"{src}_ghi"])[m]
        rows.append(dict(source=src, n=int(m.sum()), mean_gap=r.mean(),
                         max_abs_gap=r.abs().max()))
    return pd.DataFrame(rows)


def clearsky_test(d, min_elev=25.0):
    """Zero cloud, high sun: both the level and the diffuse fraction are pinned by physics."""
    m = (d.cloud == 0) & (d.elev >= min_elev)
    k = d[m]
    rows = [dict(source="clear-sky theory", n=int(m.sum()), ghi=k.cs_ghi.mean(),
                 diffuse=k.cs_dif.mean(), diffuse_frac=100 * (k.cs_dif / k.cs_ghi).mean(),
                 ghi_vs_theory=0.0)]
    for src, lab in (("sat", "satellite (Himawari)"), ("era", "ERA5 (in use today)")):
        g, f = k[f"{src}_ghi"], k[f"{src}_dif"]
        rows.append(dict(source=lab, n=int(m.sum()), ghi=g.mean(), diffuse=f.mean(),
                         diffuse_frac=100 * (f / g.where(g > 0)).mean(),
                         ghi_vs_theory=100 * (g.mean() / k.cs_ghi.mean() - 1)))
    return pd.DataFrame(rows)


def erbs_test(d, min_elev=15.0):
    """Distance from the empirical kt -> diffuse-fraction curve, over all daylight hours."""
    k = d[(d.elev >= min_elev) & (d.ghi_et > 0)].copy()
    rows = []
    for src, lab in (("sat", "satellite (Himawari)"), ("era", "ERA5 (in use today)")):
        kt = (k[f"{src}_ghi"] / k.ghi_et).clip(0, 1.2)
        pred = pvlib.irradiance.erbs(k[f"{src}_ghi"], k.zen, k.index)["dhi"]
        obs = k[f"{src}_dif"]
        rows.append(dict(source=lab, n=len(k), mean_kt=kt.mean(),
                         mean_diffuse_frac=100 * (obs / k[f"{src}_ghi"].where(lambda x: x > 0)).mean(),
                         rmse_vs_erbs=float(np.sqrt(((obs - pred) ** 2).mean())),
                         bias_vs_erbs=float((obs - pred).mean())))
    return pd.DataFrame(rows)


def clearsky_selfselected(d, min_elev=25.0, tol=0.05):
    """The clear-sky test again, but with each source picking its OWN clear hours.

    clearsky_test() selects on ERA5's cloud field, which quietly favours ERA5: if ERA5
    calls an hour cloudless when thin cirrus is present, the real diffuse fraction is
    genuinely higher and the satellite is right. Here each source is asked only about
    hours where ITS OWN global agrees with the clear-sky ceiling to `tol`. A source that
    still reports a high diffuse fraction on hours it itself calls cloudless is
    internally inconsistent, and no selection bias can explain it away.
    """
    rows = []
    for src, lab in (("sat", "satellite (Himawari)"), ("era", "ERA5 (in use today)")):
        g, f = d[f"{src}_ghi"], d[f"{src}_dif"]
        m = (d.elev >= min_elev) & (g > 0) & ((g / d.cs_ghi - 1).abs() <= tol)
        rows.append(dict(source=lab, n=int(m.sum()),
                         diffuse_frac=100 * (f[m] / g[m]).mean(),
                         theory_diffuse_frac=100 * (d.cs_dif[m] / d.cs_ghi[m]).mean()))
    return pd.DataFrame(rows)


def engine_impact(d, svf=0.35, hot_ta=30.0):
    """The difference pushed through the SHIPPED physics: scripts/mrt.py.

    tsurf is held identical between the two runs on purpose. Surface temperature is
    itself driven by radiation, so letting it respond would grow the gap -- holding it
    fixed isolates the direct radiative term and makes this a LOWER BOUND on the change,
    not an estimate of it.
    """
    import mrt as M
    k = d[(d.ta >= hot_ta) & (d.elev > 5)].copy()
    if len(k) < 50:
        return pd.DataFrame([dict(note="too few hot hours", n=len(k))])
    rows = []
    for shade, lab in ((1.0, "full shade"), (0.0, "full sun")):
        u = {}
        for src in ("sat", "era"):
            tm = M.mrt(k.ta.to_numpy(), svf, shade, k[f"{src}_dir"].to_numpy(),
                       k[f"{src}_dif"].to_numpy(), k.elev.to_numpy(),
                       tsurf_c=k.ta.to_numpy() + (8.0 if shade else 20.0),
                       rh=45.0, cloud=(k.cloud / 100.0).to_numpy())
            uu, _ = M.utci(k.ta.to_numpy(), tm, 2.0, rh=45.0)
            u[src] = (tm, uu)
        rows.append(dict(case=lab, n=len(k), svf=svf,
                         era_mrt=u["era"][0].mean(), sat_mrt=u["sat"][0].mean(),
                         d_mrt=(u["sat"][0] - u["era"][0]).mean(),
                         era_utci=u["era"][1].mean(), sat_utci=u["sat"][1].mean(),
                         d_utci=(u["sat"][1] - u["era"][1]).mean(),
                         p90_d_utci=float(np.quantile(u["sat"][1] - u["era"][1], 0.90))))
    r = pd.DataFrame(rows)
    sh, su = r[r.case == "full shade"].iloc[0], r[r.case == "full sun"].iloc[0]
    r.attrs["benefit"] = dict(era_sun_minus_shade=su.era_utci - sh.era_utci,
                              sat_sun_minus_shade=su.sat_utci - sh.sat_utci)
    return r


def shade_load(d, svf=0.35, hot_elev=30.0):
    """What the disagreement costs a pedestrian IN SHADE -- the app's actual question.

    In shade the beam is gone by definition, so the shortwave a body absorbs is the sky
    diffuse it can see (diffuse x SVF) plus ground-reflected. svf=0.35 is a typical CBD
    canyon value; ALBEDO 0.20 matches surface_temp.ALBEDO_ENV, ABS_K 0.70 matches
    mrt.ABS_K. The UTCI figure is the crude first-order conversion, not the engine's --
    it says how big the term is, not what a route would price.
    """
    ALBEDO, ABS_K = 0.20, 0.70
    m = d.elev >= hot_elev                        # high sun == when shade is sought
    k = d[m]
    out = {}
    for src in ("sat", "era"):
        dif, ghi = k[f"{src}_dif"], k[f"{src}_ghi"]
        out[src] = ABS_K * (dif * svf + ALBEDO * ghi * (1 - svf))
    gap = out["sat"] - out["era"]
    return pd.DataFrame([dict(
        hours=int(m.sum()), svf=svf,
        era_absorbed_wm2=out["era"].mean(), sat_absorbed_wm2=out["sat"].mean(),
        extra_wm2=gap.mean(), extra_pct=100 * (out["sat"].mean() / out["era"].mean() - 1),
        p90_extra_wm2=float(gap.quantile(0.90)))])


def main():
    d = frame()
    fmt = lambda x: x.to_string(index=False, float_format=lambda v: f"{v:8.2f}")
    print(f"hours: {len(d):,}   {d.index.min().date()} .. {d.index.max().date()}")
    print("\n=== SELF-CONSISTENCY  (direct + diffuse - global, W/m2)")
    print(fmt(selfconsistency(d)))
    print("\n=== CLEAR-SKY TEST  (cloud_cover == 0, sun above 25 deg)")
    print(fmt(clearsky_test(d)))
    print("\n=== ERBS TEST  (all hours, sun above 15 deg)")
    print(fmt(erbs_test(d)))
    print("\n=== CLEAR-SKY, EACH SOURCE PICKING ITS OWN CLEAR HOURS")
    print(fmt(clearsky_selfselected(d)))
    print("\n=== WHAT IT COSTS IN SHADE  (sun above 30 deg)")
    for svf in (0.25, 0.35, 0.50):
        print(fmt(shade_load(d, svf=svf)))
    print("\n=== THROUGH THE SHIPPED PHYSICS  (mrt.py, hours with Ta >= 30 C)")
    r = engine_impact(d)
    print(fmt(r))
    b = r.attrs.get("benefit")
    if b:
        print(f"    UTCI benefit of shade:  ERA5 {b['era_sun_minus_shade']:+.2f} C   "
              f"satellite {b['sat_sun_minus_shade']:+.2f} C")


if __name__ == "__main__":
    main()
