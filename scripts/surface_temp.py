"""Single-node surface energy balance, marched hourly over the shade rasters.

    rho_c_d dTs/dt = (1-a) S_down + eps L_in - eps sigma Ts^4 - h(u) (Ts - Ta)

Vectorised over cells; the only loop is over time. The Ts^4 term is linearised and
the step is taken semi-implicitly, so the march is UNCONDITIONALLY STABLE -- the
sub-stepping below buys accuracy, not stability (see stability_dt() for what an
explicit Euler step would have needed).

Everything takes arrays/dicts as arguments; nothing here reads out/ except
save_hourly(), which only writes.
"""
import os
import numpy as np

# --- physical constants -------------------------------------------------------
SIGMA = 5.670374419e-8      # Stefan-Boltzmann, W m-2 K-4 (CODATA 2018, exact)
KARMAN_Z0 = 0.03            # aerodynamic roughness length, m. Wieringa (1992) class
                            # "open" -- the terrain an NWP 10 m wind is defined over.
Z_PED = 1.1                 # reference height, m. Globe-thermometer / UTCI height,
                            # same z_ped the SVF in ENGINE_CONTRACT.md uses.
U_MIN = 0.5                 # m/s floor: UTCI validity lower bound, and a stand-in for
                            # the free convection that never actually stops.

# --- urban environment defaults (override per call) ---------------------------
ALBEDO_ENV = 0.20           # bulk albedo of surrounding walls. Oke (1987) Table 1.1,
                            # urban walls/concrete 0.10-0.35.
REFL_F = 0.5                # walls are vertical: they intercept roughly half the
                            # global HORIZONTAL irradiance we use as their driver.
EPS_WALL = 0.90             # longwave emissivity of brick/concrete walls (Oke 1987).
WALL_DT = 0.0               # wall temperature = Ta + WALL_DT. See module note below.
DEFAULT_SHADE = 1.0         # for hours with no shade raster (sun is down anyway).
DEFAULT_RH = 50.0           # %, only used if the weather row carries no humidity.

# NOTE ON WALL TEMPERATURE. We have no wall energy balance yet, so walls radiate at
# air temperature (WALL_DT = 0). That is the standard first-order closure and it is
# conservative: real sunlit walls run 5-15 K hot, so canyon cells come out slightly
# COOL here. Raise WALL_DT once mrt.py has a facade temperature to hand back.

# --- material property defaults (material_props.json overrides per id) ---------
DEF_PROPS = dict(albedo=0.20, emissivity=0.95, rho_c_d=1.5e5, beta=0.0, k_deep=0.0)
# beta   : evaporative fraction of net radiation (Bowen-ratio closure). 0 = dry.
# k_deep : W m-2 K-1 conduction to a deep reservoir at t_deep. 0 = contract equation.
# Both are OFF by default so the default march is exactly ENGINE_CONTRACT.md step 4.
#
# rho_c_d IS THE MODEL'S BIGGEST LEVER ON PEAK Ts -- materials.py, please read this.
# It is the areal heat capacity of the layer that actually participates in the diurnal
# cycle, so it must come from the thermal admittance mu = sqrt(k rho c), not from a
# guessed slab thickness:
#     d = sqrt(2 alpha / omega),  omega = 2 pi / 86400 s-1
#     rho_c_d = rho c d = mu sqrt(2/omega) = 165.8 * mu        [J m-2 K-1]
# Oke (1987) Table 2.1: asphalt mu ~ 1590, concrete ~ 1780, short grass ~ 900,
# dry bark mulch ~ 220 -> rho_c_d ~ 2.6e5, 2.9e5, 1.5e5, 3.7e4. Halving rho_c_d for
# asphalt moves its 15:00 surface temperature by roughly +8 K.


# ==============================================================================
# atmosphere
# ==============================================================================
def es_hpa(t_c):
    """Saturation vapour pressure over water, hPa. Magnus/Tetens, WMO-No.8 form."""
    return 6.112 * np.exp(17.62 * t_c / (243.12 + t_c))


def vapour_pressure(row, rh_default=DEFAULT_RH):
    """Screen-level vapour pressure, hPa, from whatever the Open-Meteo row carries.

    server/weather.py's VARS has NO humidity variable, so this usually falls back to
    rh_default. Add "relative_humidity_2m" (or "dew_point_2m") to VARS and this
    starts using the real thing with no other change.
    """
    for k in ("vapour_pressure", "vapor_pressure"):
        if row.get(k) is not None:
            return float(row[k])
    for k in ("dew_point_2m", "dewpoint_2m"):
        if row.get(k) is not None:
            return float(es_hpa(float(row[k])))
    t = _f(row.get("temperature_2m"))
    rh = row.get("relative_humidity_2m")
    rh = rh_default if rh is None else float(rh)
    return float(es_hpa(t) * rh / 100.0)


def sky_emissivity(ta_k, e_hpa, cloud_frac):
    """All-sky effective emissivity of the atmosphere, [0,1].

    Clear sky: Prata, A.J. (1996) "A new long-wave formula for estimating downward
      clear-sky radiation at the surface", Q.J.R. Meteorol. Soc. 122, 1127-1151.
          eps_clear = 1 - (1 + w) exp( -(1.2 + 3 w)^0.5 )
          w = 46.5 e/Ta   precipitable water, g cm-2, e in hPa, Ta in K
    Cloud: Crawford, T.M. & Duchon, C.E. (1999) "An improved parameterization for
      estimating effective atmospheric emissivity...", J. Appl. Meteorol. 38, 474-480.
          eps_all = clf + (1 - clf) eps_clear      (clouds radiate as black bodies)
    Prata is chosen over Brutsaert (1975) because it uses precipitable water rather
    than raw vapour pressure and holds up better in dry, hot air -- i.e. a Melbourne
    January afternoon.
    """
    w = 46.5 * np.asarray(e_hpa, float) / np.asarray(ta_k, float)
    eps_clear = 1.0 - (1.0 + w) * np.exp(-np.sqrt(1.2 + 3.0 * w))
    clf = np.clip(cloud_frac, 0.0, 1.0)
    return clf + (1.0 - clf) * eps_clear


def l_sky(ta_k, e_hpa, cloud_frac):
    """Downwelling longwave from an unobstructed sky, W m-2."""
    return sky_emissivity(ta_k, e_hpa, cloud_frac) * SIGMA * np.asarray(ta_k, float) ** 4


def wind_at(u10, z=Z_PED, z0=KARMAN_Z0):
    """10 m wind (m/s) -> wind at height z by the neutral log profile.

    u(z) = u10 ln(z/z0) / ln(10/z0).  z0 = 0.03 m gives u(1.1)/u(10) = 0.62, which is
    the same order as the ~0.68 factor the UTCI literature uses to move between 1.1 m
    and 10 m. Caveat, stated loudly: inside a real urban canopy the log law needs a
    displacement height and this profile does not have one -- it treats the 10 m wind
    as the open-terrain value it is defined as and does NOT model canyon sheltering.
    """
    return np.maximum(U_MIN, np.asarray(u10, float) * np.log(z / z0) / np.log(10.0 / z0))


def h_conv(u):
    """Convective heat transfer coefficient, W m-2 K-1, from wind speed u (m/s).

    Watmuff, J.H., Charters, W.W.S. & Proctor, D. (1977) "Solar and wind induced
    external coefficients for solar collectors", Comples 2, 56:
        h = 2.8 + 3.0 u
    Preferred over McAdams / Duffie-Beckman h = 5.7 + 3.8 u because that one folds a
    radiative component into its intercept and we compute radiation explicitly --
    using it would double-count longwave. Watmuff is the radiation-stripped version.
    """
    return 2.8 + 3.0 * np.asarray(u, float)


# ==============================================================================
# forcing
# ==============================================================================
def _f(v, d=0.0):
    return d if v is None else float(v)


def forcing(row, wind_unit="kmh", rh_default=DEFAULT_RH, z_ped=Z_PED, z0=KARMAN_Z0):
    """One Open-Meteo hourly row -> scalar forcing dict in SI/Kelvin.

    GEOMETRY, the thing that scales the whole model: Open-Meteo's `direct_radiation`
    is documented as "direct solar radiation as average of the preceding hour ON THE
    HORIZONTAL PLANE" -- it is NOT beam-normal (that variable is
    `direct_normal_irradiance`). So for our horizontal ground cells it is already the
    right projection: no cos(zenith) factor, just gate it by the shade raster.
    UNITS: Open-Meteo's default wind unit is km/h and server/weather.py never asks for
    anything else, hence wind_unit="kmh".
    """
    ta = _f(row.get("temperature_2m"))
    u10 = _f(row.get("wind_speed_10m")) * (1.0 / 3.6 if wind_unit == "kmh" else 1.0)
    ta_k = ta + 273.15
    e = vapour_pressure(row, rh_default)
    u = float(wind_at(u10, z_ped, z0))
    return dict(ta_k=ta_k, e_hpa=e, u_surf=u, h=float(h_conv(u)),
                s_dir=max(0.0, _f(row.get("direct_radiation"))),
                s_dif=max(0.0, _f(row.get("diffuse_radiation"))),
                l_sky=float(l_sky(ta_k, e, _f(row.get("cloud_cover")) / 100.0)))


def _mean_forcing(a, b):
    """Midpoint of two hourly forcings -> the value held constant over the interval.

    Open-Meteo radiation at hour H is the mean over [H-1,H) while Ta/wind are instant
    at H, so the mean of rows H and H+1 is the best constant for [H,H+1) either way,
    and it makes the march second-order in the forcing instead of first.
    """
    return {k: 0.5 * (a[k] + b[k]) for k in a}


# ==============================================================================
# per-cell material properties
# ==============================================================================
def props_arrays(props, n=256):
    """{id: {albedo, emissivity, rho_c_d, [beta], [k_deep]}} -> 256-long lookup vectors."""
    out = {k: np.full(n, v, np.float32) for k, v in DEF_PROPS.items()}
    for k, p in (props or {}).items():
        i = int(k)
        if not 0 <= i < n:
            raise ValueError(f"material id {i} out of range 0..{n-1}")
        for f in DEF_PROPS:
            if p.get(f) is not None:
                out[f][i] = float(p[f])
    return out


# ==============================================================================
# the energy balance
# ==============================================================================
def absorbed(f, shade, svf, alb, eps, albedo_env=ALBEDO_ENV, refl_f=REFL_F,
             eps_wall=EPS_WALL, wall_dt=WALL_DT):
    """Ts-independent half of the balance: absorbed shortwave + absorbed longwave, W m-2.

    S_down = direct (already horizontal, gated by shade) + diffuse (scaled by SVF)
             + reflected off the (1-SVF) of the hemisphere that is wall.
    L_in   = SVF * L_sky + (1-SVF) * eps_wall sigma (Ta + wall_dt)^4.
    """
    g = f["s_dir"] + f["s_dif"]
    sw = f["s_dir"] * (1.0 - shade) + f["s_dif"] * svf + albedo_env * refl_f * (1.0 - svf) * g
    l_in = svf * f["l_sky"] + (1.0 - svf) * eps_wall * SIGMA * (f["ta_k"] + wall_dt) ** 4
    return (1.0 - alb) * sw + eps * l_in


def net_flux(ts, q, eps, h, ta_k, k_deep=0.0, t_deep=0.0, beta=0.0):
    """RHS of the balance in W m-2, i.e. rho_c_d dTs/dt. Arrays or scalars."""
    rn = q - eps * SIGMA * ts ** 4                      # net all-wave radiation
    r = rn - h * (ts - ta_k)
    if np.any(k_deep):
        r = r - k_deep * (ts - t_deep)
    if np.any(beta):
        r = r - beta * np.maximum(rn, 0.0)              # evaporation, Bowen closure
    return r


def stability_dt(rho_c_d, eps, h, ts, k_deep=0.0):
    """Largest dt an EXPLICIT forward Euler step could take, s:  2 C / (h + 4 eps sig T^3).

    We do not use this -- the scheme below is unconditionally stable -- but it is what
    a naive dt=3600 s explicit march would be violating, so tests report it.
    """
    return 2.0 * rho_c_d / (h + k_deep + 4.0 * eps * SIGMA * np.asarray(ts, float) ** 3)


def substep(ts, q, eps, rho_c_d, h, ta_k, dt, k_deep=0.0, t_deep=0.0, beta=0.0):
    """One semi-implicit Euler step (Ts^4 linearised about Ts_n). Returns new Ts, K.

        C (T' - T)/dt = R(T) - b (T' - T),   b = (1-beta) 4 eps sig T^3 + h + k_deep
    =>  T' = T + dt R(T) / (C + dt b)

    b > 0 always, so the update is a contraction for any dt: no stability limit, and
    dt -> inf degenerates into a Newton step straight at the steady state.
    """
    r = net_flux(ts, q, eps, h, ta_k, k_deep, t_deep, beta)
    b = (1.0 - beta) * 4.0 * eps * SIGMA * ts ** 3 + h + k_deep
    return ts + dt * r / (rho_c_d + dt * b)


# ==============================================================================
# the march
# ==============================================================================
def _shade_at(src, hour, shape, default=DEFAULT_SHADE):
    """shade_by_hour may be a dict, a callable(hour)->array|None, or None."""
    a = src(hour) if callable(src) else (src or {}).get(hour)
    if a is None:
        return np.float32(default)
    return np.asarray(a, np.float32)


def march(shade_by_hour, svf, mat_id, props, weather_hours, hours=range(6, 21),
          spin_loops=3, n_sub=12, keep=True, on_hour=None, wind_unit="kmh",
          rh_default=DEFAULT_RH, t_deep=None, diag=None, **cfg):
    """March the energy balance around the 24 h clock. -> {hour: Ts float32 (h,w) in K}.

    shade_by_hour  dict{hour: (h,w) float32 in [0,1], 1 = fully shaded} or callable(hour)
                   -> array|None. Hours with no raster get DEFAULT_SHADE (sun is down).
    svf            (h,w) float32 [0,1], sky view factor (use svf_all).
    mat_id         (h,w) uint8 material class ids.
    props          {id: {albedo, emissivity, rho_c_d, ...}} from material_props.json.
    weather_hours  {0..23: Open-Meteo row}, exactly server.weather.get()["hours"].
    hours          which hours to emit. spin_loops  diurnal repeats before emitting.
    n_sub          sub-steps per hour (dt = 3600/n_sub). Accuracy only, not stability.
    keep           hold results in the returned dict. False + on_hour = streaming.
    on_hour        callback(hour, ts) fired as each requested hour is reached.
    diag           optional dict; filled with "spin" (per-loop max |dTs|) and "dt".
    cfg            albedo_env, refl_f, eps_wall, wall_dt, z_ped, z0, default_shade.

    Peak RAM is ~11 rasters (svf, mat_id, 3-5 property maps, shade, q, ts, scratch).
    On the real 2485x2438 grid that is ~0.3 GB with keep=False; keep=True adds
    15 x 24.2 MB = 363 MB more, so stream on the real grid.
    """
    default_shade = cfg.pop("default_shade", DEFAULT_SHADE)
    z_ped, z0 = cfg.pop("z_ped", Z_PED), cfg.pop("z0", KARMAN_Z0)
    svf = np.asarray(svf, np.float32)
    mat_id = np.asarray(mat_id)
    if svf.shape != mat_id.shape:
        raise ValueError(f"svf {svf.shape} != mat_id {mat_id.shape}")

    P = props_arrays(props)
    alb, eps = P["albedo"][mat_id], P["emissivity"][mat_id]
    cap = P["rho_c_d"][mat_id]
    beta = P["beta"][mat_id]
    kdp = P["k_deep"][mat_id]
    beta = np.float32(0.0) if not beta.any() else beta      # scalars save 24 MB each
    kdp = np.float32(0.0) if not kdp.any() else kdp

    F = {h: forcing(weather_hours[h], wind_unit, rh_default, z_ped, z0)
         for h in sorted(weather_hours)}
    clock = sorted(F)                                       # normally 0..23
    Fi = {h: _mean_forcing(F[h], F[clock[(i + 1) % len(clock)]])
          for i, h in enumerate(clock)}
    if t_deep is None:
        t_deep = float(np.mean([F[h]["ta_k"] for h in clock]))

    dt = 3600.0 / n_sub
    ts = np.full(svf.shape, F[clock[0]]["ta_k"], np.float32)
    want = set(hours)
    out = {}
    spin = []
    for loop in range(max(1, spin_loops)):
        last = loop == max(1, spin_loops) - 1
        prev = ts.copy()
        for hh in clock:
            if last and hh in want:                         # state AT hh:00
                v = ts.copy()
                if on_hour:
                    on_hour(hh, v)
                if keep:
                    out[hh] = v
            f = Fi[hh]
            q = absorbed(f, _shade_at(shade_by_hour, hh, svf.shape, default_shade),
                         svf, alb, eps, **cfg)
            for _ in range(n_sub):
                ts = substep(ts, q, eps, cap, f["h"], f["ta_k"], dt, kdp, t_deep, beta)
            ts = ts.astype(np.float32, copy=False)
        spin.append(float(np.max(np.abs(ts - prev))))
    if diag is not None:
        diag.update(spin=spin, dt=dt, t_deep=t_deep,
                    forcing={h: Fi[h] for h in clock})
    return out


def save_hourly(ts, hour, outdir="out", prefix="tsurf"):
    """Write out/tsurf_HH.npy, float32 Kelvin, per ENGINE_CONTRACT.md. -> path."""
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, f"{prefix}_{int(hour):02d}.npy")
    np.save(p, np.asarray(ts, np.float32))
    return p
