"""MRT (SOLWEIG lineage) + UTCI (Brode et al. 2012 polynomial). Pure functions, no I/O.

MRT follows Lindberg, Holmer & Thorsson 2008 (Int. J. Biometeorol. 52(7) 697-713) in the
six-direction "standing person" form used by SOLWEIG:

    S_str = absK * sum_i(K_i F_i)  +  absL * sum_i(L_i F_i)
    MRT   = (S_str / (absL * SIGMA))**0.25 - 273.15

UTCI is the 210-coefficient 6th-order polynomial of Brode et al. 2012 (Int. J.
Biometeorol. 56 481-494); see UTCI_COEF for how the coefficients were obtained.
Everything is vectorised: scalars, arrays and broadcast mixes all work.
"""
import numpy as np

SIGMA = 5.670374419e-8            # Stefan-Boltzmann, W m-2 K-4 (CODATA 2018)
T0 = 273.15

# ---------------------------------------------------------------- human body
# SOLWEIG defaults (UMEP solweig_algorithm.py, SOLWEIG 1.0 paper).
ABS_K = 0.70                      # shortwave absorptivity of clothed body
ABS_L = 0.97                      # longwave absorptivity == emissivity of clothing/skin

# View factors, standing person, SOLWEIG + VDI 3787 Part 2: four sides 0.22, up and down
# 0.06, summing to 1.00. Each side face is half above the horizon and half below, so the
# hemispheres are 0.50/0.50 exactly -- which is what makes the isothermal-enclosure
# identity in tests/test_mrt.py (c)(i) close.
F_SIDE, F_UP, F_DOWN = 0.22, 0.06, 0.06
F_UPPER = F_UP + 2.0 * F_SIDE     # 0.50
F_LOWER = F_DOWN + 2.0 * F_SIDE   # 0.50

# The direct beam arrives from ONE direction, so it is not decomposed over the six F_i;
# it gets f_p(elev), the fraction of body area projected normal to the beam. Taken from
# SOLWEIG's own decomposition (Kside_veg_v2022a.py Fcyl = 0.28, Fup = 0.06) rather than
# the Jendritzky/VDI closed form we could not source verbatim:
#   f_p(b) = F_CYL*cos(b) + F_UP*sin(b),  running 0.28 at the horizon to 0.06 at zenith,
# which brackets the published 0.308 -> 0.08 curve. A flat 0.5 is used nowhere here.
F_CYL = 0.28


def f_p(elev_deg):
    """Projected-area factor of a standing person for a beam at elevation `elev_deg`."""
    b = np.radians(np.asarray(elev_deg, dtype=float))
    return np.where(b > 0.0, F_CYL * np.cos(b) + F_UP * np.sin(b), 0.0)


# ------------------------------------------------------------------ UTCI
def svp_hpa(ta):
    """Saturation vapour pressure over water, hPa, from air temperature in degC.

    Hardy (1998) ITS-90, as used by the official UTCI program and ECMWF thermofeel.
    pythermalcomfort 3.3.0 writes log1p(T) where all three of those use log(T); we
    follow log(T). The difference is <0.02 degC in UTCI -- tests/test_mrt.py (a) reports
    both.
    """
    tk = np.asarray(ta, dtype=float) + T0
    g = (-2.8365744e3, -6.028076559e3, 1.954263612e1, -2.737830188e-2,
         1.6261698e-5, 7.0229056e-10, -1.8680009e-13)
    es = 2.7150305 * np.log(tk)
    for i, gi in enumerate(g):
        es = es + gi * tk ** (i - 2)
    return np.exp(es) * 0.01


def vapour_pressure(ta, rh):
    """Actual vapour pressure in hPa from air temperature (degC) and RH (%)."""
    return svp_hpa(ta) * np.asarray(rh, dtype=float) / 100.0


# Documented validity range of the polynomial fit (Brode et al. 2012 Table 1).
UTCI_TA = (-50.0, 50.0)           # degC
UTCI_DT = (-30.0, 70.0)           # K, mrt - ta
UTCI_VA = (0.5, 17.0)             # m/s at 10 m

# Published stress categories (Brode et al. 2012 Table 4), each the UPPER bound in degC.
# The 9..26 band is "no thermal stress" and is what stress() measures distance from.
UTCI_CATEGORIES = ((-40.0, "extreme cold stress"), (-27.0, "very strong cold stress"),
                   (-13.0, "strong cold stress"), (0.0, "moderate cold stress"),
                   (9.0, "slight cold stress"), (26.0, "no thermal stress"),
                   (32.0, "moderate heat stress"), (38.0, "strong heat stress"),
                   (46.0, "very strong heat stress"), (np.inf, "extreme heat stress"))
NO_STRESS = (9.0, 26.0)

# 6th-order polynomial of Brode et al. 2012, as (coefficient, e_ta, e_va, e_dmrt, e_pa),
# each term being coefficient * ta**e_ta * va**e_va * (mrt-ta)**e_dmrt * pa**e_pa with pa
# the vapour pressure in kPa.
#
# NOT TYPED BY HAND: parsed by script from pythermalcomfort's _utci_optimized and,
# independently, from ECMWF thermofeel's calculate_utci_polynomial (the implementation
# behind ERA5-HEAT). Both parses gave the SAME 210 monomials agreeing to better than
# 1e-8 relative. 211 rows below = those 210 plus the leading bare `ta`.
UTCI_COEF = (
    (            1.0,1,0,0,0), (    0.607562052,0,0,0,0), (  -0.0227712343,1,0,0,0),
    ( 8.06470249e-04,2,0,0,0), (-1.54271372e-04,3,0,0,0), (-3.24651735e-06,4,0,0,0),
    ( 7.32602852e-08,5,0,0,0), ( 1.35959073e-09,6,0,0,0), (    -2.25836520,0,1,0,0),
    (   0.0880326035,1,1,0,0), (  0.00216844454,2,1,0,0), (-1.53347087e-05,3,1,0,0),
    (-5.72983704e-07,4,1,0,0), (-2.55090145e-09,5,1,0,0), (   -0.751269505,0,2,0,0),
    ( -0.00408350271,1,2,0,0), (-5.21670675e-05,2,2,0,0), ( 1.94544667e-06,3,2,0,0),
    ( 1.14099531e-08,4,2,0,0), (    0.158137256,0,3,0,0), (-6.57263143e-05,1,3,0,0),
    ( 2.22697524e-07,2,3,0,0), (-4.16117031e-08,3,3,0,0), (  -0.0127762753,0,4,0,0),
    ( 9.66891875e-06,1,4,0,0), ( 2.52785852e-09,2,4,0,0), ( 4.56306672e-04,0,5,0,0),
    (-1.74202546e-07,1,5,0,0), (-5.91491269e-06,0,6,0,0), (    0.398374029,0,0,1,0),
    ( 1.83945314e-04,1,0,1,0), (-1.73754510e-04,2,0,1,0), (-7.60781159e-07,3,0,1,0),
    ( 3.77830287e-08,4,0,1,0), ( 5.43079673e-10,5,0,1,0), (  -0.0200518269,0,1,1,0),
    ( 8.92859837e-04,1,1,1,0), ( 3.45433048e-06,2,1,1,0), (-3.77925774e-07,3,1,1,0),
    (-1.69699377e-09,4,1,1,0), ( 1.69992415e-04,0,2,1,0), (-4.99204314e-05,1,2,1,0),
    ( 2.47417178e-07,2,2,1,0), ( 1.07596466e-08,3,2,1,0), ( 8.49242932e-05,0,3,1,0),
    ( 1.35191328e-06,1,3,1,0), (-6.21531254e-09,2,3,1,0), (-4.99410301e-06,0,4,1,0),
    (-1.89489258e-08,1,4,1,0), ( 8.15300114e-08,0,5,1,0), ( 7.55043090e-04,0,0,2,0),
    (-5.65095215e-05,1,0,2,0), (-4.52166564e-07,2,0,2,0), ( 2.46688878e-08,3,0,2,0),
    ( 2.42674348e-10,4,0,2,0), ( 1.54547250e-04,0,1,2,0), ( 5.24110970e-06,1,1,2,0),
    (-8.75874982e-08,2,1,2,0), (-1.50743064e-09,3,1,2,0), (-1.56236307e-05,0,2,2,0),
    (-1.33895614e-07,1,2,2,0), ( 2.49709824e-09,2,2,2,0), ( 6.51711721e-07,0,3,2,0),
    ( 1.94960053e-09,1,3,2,0), (-1.00361113e-08,0,4,2,0), (-1.21206673e-05,0,0,3,0),
    (-2.18203660e-07,1,0,3,0), ( 7.51269482e-09,2,0,3,0), ( 9.79063848e-11,3,0,3,0),
    ( 1.25006734e-06,0,1,3,0), (-1.81584736e-09,1,1,3,0), (-3.52197671e-10,2,1,3,0),
    (-3.36514630e-08,0,2,3,0), ( 1.35908359e-10,1,2,3,0), ( 4.17032620e-10,0,3,3,0),
    (-1.30369025e-09,0,0,4,0), ( 4.13908461e-10,1,0,4,0), ( 9.22652254e-12,2,0,4,0),
    (-5.08220384e-09,0,1,4,0), (-2.24730961e-11,1,1,4,0), ( 1.17139133e-10,0,2,4,0),
    ( 6.62154879e-10,0,0,5,0), ( 4.03863260e-13,1,0,5,0), ( 1.95087203e-12,0,1,5,0),
    (-4.73602469e-12,0,0,6,0), (     5.12733497,0,0,0,1), (   -0.312788561,1,0,0,1),
    (  -0.0196701861,2,0,0,1), ( 9.99690870e-04,3,0,0,1), ( 9.51738512e-06,4,0,0,1),
    (-4.66426341e-07,5,0,0,1), (    0.548050612,0,1,0,1), ( -0.00330552823,1,1,0,1),
    ( -0.00164119440,2,1,0,1), (-5.16670694e-06,3,1,0,1), ( 9.52692432e-07,4,1,0,1),
    (  -0.0429223622,0,2,0,1), (  0.00500845667,1,2,0,1), ( 1.00601257e-06,2,2,0,1),
    (-1.81748644e-06,3,2,0,1), (-1.25813502e-03,0,3,0,1), (-1.79330391e-04,1,3,0,1),
    ( 2.34994441e-06,2,3,0,1), ( 1.29735808e-04,0,4,0,1), ( 1.29064870e-06,1,4,0,1),
    (-2.28558686e-06,0,5,0,1), (  -0.0369476348,0,0,1,1), (  0.00162325322,1,0,1,1),
    (-3.14279680e-05,2,0,1,1), ( 2.59835559e-06,3,0,1,1), (-4.77136523e-08,4,0,1,1),
    ( 8.64203390e-03,0,1,1,1), (-6.87405181e-04,1,1,1,1), (-9.13863872e-06,2,1,1,1),
    ( 5.15916806e-07,3,1,1,1), (-3.59217476e-05,0,2,1,1), ( 3.28696511e-05,1,2,1,1),
    (-7.10542454e-07,2,2,1,1), (-1.24382300e-05,0,3,1,1), (-7.38584400e-09,1,3,1,1),
    ( 2.20609296e-07,0,4,1,1), (-7.32469180e-04,0,0,2,1), (-1.87381964e-05,1,0,2,1),
    ( 4.80925239e-06,2,0,2,1), (-8.75492040e-08,3,0,2,1), ( 2.77862930e-05,0,1,2,1),
    (-5.06004592e-06,1,1,2,1), ( 1.14325367e-07,2,1,2,1), ( 2.53016723e-06,0,2,2,1),
    (-1.72857035e-08,1,2,2,1), (-3.95079398e-08,0,3,2,1), (-3.59413173e-07,0,0,3,1),
    ( 7.04388046e-07,1,0,3,1), (-1.89309167e-08,2,0,3,1), (-4.79768731e-07,0,1,3,1),
    ( 7.96079978e-09,1,1,3,1), ( 1.62897058e-09,0,2,3,1), ( 3.94367674e-08,0,0,4,1),
    (-1.18566247e-09,1,0,4,1), ( 3.34678041e-10,0,1,4,1), (-1.15606447e-10,0,0,5,1),
    (    -2.80626406,0,0,0,2), (    0.548712484,1,0,0,2), ( -0.00399428410,2,0,0,2),
    (-9.54009191e-04,3,0,0,2), ( 1.93090978e-05,4,0,0,2), (   -0.308806365,0,1,0,2),
    (   0.0116952364,1,1,0,2), ( 4.95271903e-04,2,1,0,2), (-1.90710882e-05,3,1,0,2),
    (  0.00210787756,0,2,0,2), (-6.98445738e-04,1,2,0,2), ( 2.30109073e-05,2,2,0,2),
    ( 4.17856590e-04,0,3,0,2), (-1.27043871e-05,1,3,0,2), (-3.04620472e-06,0,4,0,2),
    (   0.0514507424,0,0,1,2), ( -0.00432510997,1,0,1,2), ( 8.99281156e-05,2,0,1,2),
    (-7.14663943e-07,3,0,1,2), (-2.66016305e-04,0,1,1,2), ( 2.63789586e-04,1,1,1,2),
    (-7.01199003e-06,2,1,1,2), (-1.06823306e-04,0,2,1,2), ( 3.61341136e-06,1,2,1,2),
    ( 2.29748967e-07,0,3,1,2), ( 3.04788893e-04,0,0,2,2), (-6.42070836e-05,1,0,2,2),
    ( 1.16257971e-06,2,0,2,2), ( 7.68023384e-06,0,1,2,2), (-5.47446896e-07,1,1,2,2),
    (-3.59937910e-08,0,2,2,2), (-4.36497725e-06,0,0,3,2), ( 1.68737969e-07,1,0,3,2),
    ( 2.67489271e-08,0,1,3,2), ( 3.23926897e-09,0,0,4,2), (  -0.0353874123,0,0,0,3),
    (   -0.221201190,1,0,0,3), (   0.0155126038,2,0,0,3), (-2.63917279e-04,3,0,0,3),
    (   0.0453433455,0,1,0,3), ( -0.00432943862,1,1,0,3), ( 1.45389826e-04,2,1,0,3),
    ( 2.17508610e-04,0,2,0,3), (-6.66724702e-05,1,2,0,3), ( 3.33217140e-05,0,3,0,3),
    ( -0.00226921615,0,0,1,3), ( 3.80261982e-04,1,0,1,3), (-5.45314314e-09,2,0,1,3),
    (-7.96355448e-04,0,1,1,3), ( 2.53458034e-05,1,1,1,3), (-6.31223658e-06,0,2,1,3),
    ( 3.02122035e-04,0,0,2,3), (-4.77403547e-06,1,0,2,3), ( 1.73825715e-06,0,1,2,3),
    (-4.09087898e-07,0,0,3,3), (    0.614155345,0,0,0,4), (  -0.0616755931,1,0,0,4),
    (  0.00133374846,2,0,0,4), (  0.00355375387,0,1,0,4), (-5.13027851e-04,1,1,0,4),
    ( 1.02449757e-04,0,2,0,4), ( -0.00148526421,0,0,1,4), (-4.11469183e-05,1,0,1,4),
    (-6.80434415e-06,0,1,1,4), (-9.77675906e-06,0,0,2,4), (   0.0882773108,0,0,0,5),
    ( -0.00301859306,1,0,0,5), (  0.00104452989,0,1,0,5), ( 2.47090539e-04,0,0,1,5),
    (  0.00148348065,0,0,0,6),
)

_C = np.array([r[0] for r in UTCI_COEF], dtype=float)
_E = np.array([r[1:] for r in UTCI_COEF], dtype=np.intp)
_CHUNK = 1 << 19                  # keeps the power cache bounded on 6M-cell rasters


def _poly(ta, va, dmrt, pa):
    """Brode polynomial on flat float64 arrays. pa in kPa. No clamping."""
    n = ta.shape[0]
    out = np.empty(n, dtype=float)
    for s in range(0, n, _CHUNK):
        e = min(s + _CHUNK, n)
        pw = []
        for v in (ta[s:e], va[s:e], dmrt[s:e], pa[s:e]):
            p = [None] * 7                       # p[0] == 1 is implicit, never built
            p[1] = v
            for k in range(2, 7):
                p[k] = p[k - 1] * v
            pw.append(p)
        acc = np.zeros(e - s, dtype=float)
        for c, ex in zip(_C, _E):
            t = None
            for vi in np.nonzero(ex)[0]:
                p = pw[vi][ex[vi]]
                t = p.copy() if t is None else t * p
            acc += c if t is None else t * c
        out[s:e] = acc
    return out


def utci(ta, tmrt, va10, vp_hpa=None, rh=None, clamp=True):
    """UTCI in degC. Vectorised; returns (utci, clamped) with `clamped` a bool mask.

    ta      air temperature, degC
    tmrt    mean radiant temperature, degC (from mrt() below)
    va10    wind speed at 10 m, m/s (weather.wind_speed_10m is km/h -- divide by 3.6)
    vp_hpa  vapour pressure in hPa; converted to the kPa the polynomial wants
            internally. Pass `rh` instead to derive it from temperature.

    `clamped` flags inputs outside the documented validity range, which are clipped onto
    it unless clamp=False (then the polynomial is extrapolated and the flag still fires).
    """
    ta = np.asarray(ta, dtype=float)
    tmrt = np.asarray(tmrt, dtype=float)
    va10 = np.asarray(va10, dtype=float)
    if vp_hpa is None:
        if rh is None:
            raise ValueError("give vp_hpa (hPa) or rh (%)")
        vp_hpa = vapour_pressure(ta, rh)
    vp_hpa = np.asarray(vp_hpa, dtype=float)

    ta, tmrt, va10, vp_hpa = np.broadcast_arrays(ta, tmrt, va10, vp_hpa)
    shape = ta.shape
    ta = np.asarray(ta, dtype=float).ravel()
    dmrt = np.asarray(tmrt, dtype=float).ravel() - ta
    va = np.asarray(va10, dtype=float).ravel()
    pa = np.asarray(vp_hpa, dtype=float).ravel() / 10.0     # hPa -> kPa

    bad = ((ta < UTCI_TA[0]) | (ta > UTCI_TA[1]) | (dmrt < UTCI_DT[0]) |
           (dmrt > UTCI_DT[1]) | (va < UTCI_VA[0]) | (va > UTCI_VA[1]))
    if clamp:
        ta = np.clip(ta, *UTCI_TA)
        dmrt = np.clip(dmrt, *UTCI_DT)
        va = np.clip(va, *UTCI_VA)
    return _poly(ta, va, dmrt, pa).reshape(shape), bad.reshape(shape)


def stress(u):
    """Degrees outside the 9..26 degC no-stress band. 0 inside, >0 both sides.

    Continuous and exactly 0 at both edges, so cost = L * (1 + K * stress(utci)) needs
    no branch.
    """
    u = np.asarray(u, dtype=float)
    return np.maximum(NO_STRESS[0] - u, 0.0) + np.maximum(u - NO_STRESS[1], 0.0)


def category(u):
    """Published UTCI stress-category label(s) for the UI. Returns a str ndarray."""
    u = np.asarray(u, dtype=float)
    bounds = np.array([b for b, _ in UTCI_CATEGORIES[:-1]])
    names = np.array([n for _, n in UTCI_CATEGORIES])
    return names[np.searchsorted(bounds, u, side="right")]


# ------------------------------------------------------------------ sky / MRT
def sky_emissivity(ta, rh=50.0, cloud=0.0):
    """Prata (1996) clear-sky emissivity, blended to 1.0 with cloud fraction.

        w = 46.5 e_hPa / T_K,  eps_clr = 1 - (1+w) exp(-(1.2 + 3w)**0.5)
        eps = eps_clr + (1 - eps_clr) * cloud     (overcast radiates as a black body)
    """
    ta = np.asarray(ta, dtype=float)
    tk = ta + T0
    w = 46.5 * (svp_hpa(ta) * np.asarray(rh, dtype=float) / 100.0) / tk
    eps_clr = 1.0 - (1.0 + w) * np.exp(-np.sqrt(1.2 + 3.0 * w))
    c = np.clip(np.asarray(cloud, dtype=float), 0.0, 1.0)
    return eps_clr + (1.0 - eps_clr) * c


def mrt(ta, svf, shade, i_dir_h, i_dif, elev_deg, tsurf_c,
        t_wall_c=None, albedo_g=0.15, albedo_w=0.20,
        eps_g=0.95, eps_w=0.90, eps_sky=None, rh=50.0, cloud=0.0,
        wall_sunlit=0.5):
    """Mean radiant temperature (degC) for a standing pedestrian. Vectorised.

    ta        air temperature, degC
    svf       sky view factor at pedestrian height, [0,1]
    shade     SHADED fraction of the direct beam, [0,1] (1 = fully shaded)
    i_dir_h   direct irradiance on the HORIZONTAL, W/m2 (Open-Meteo direct_radiation)
    i_dif     diffuse irradiance on the horizontal, W/m2
    elev_deg  solar elevation, degrees. <=0 disables all shortwave.
    tsurf_c   ground surface temperature, degC. The `_c` is load-bearing: the rasters
              and engine.E["tsurf_k"] are KELVIN, so the conversion happens at this call.
    t_wall_c  wall surface temperature, degC (engine.E["twall_c"] already is). Defaults
              to `ta`, which is the pre-wall-balance bias.
    eps_sky   sky emissivity; from Prata via (ta, rh, cloud) if None.
    """
    ta = np.asarray(ta, dtype=float)
    svf = np.clip(np.asarray(svf, dtype=float), 0.0, 1.0)
    sunlit = 1.0 - np.clip(np.asarray(shade, dtype=float), 0.0, 1.0)
    i_dir_h = np.asarray(i_dir_h, dtype=float)
    i_dif = np.asarray(i_dif, dtype=float)
    elev = np.asarray(elev_deg, dtype=float)
    tsurf_c = np.asarray(tsurf_c, dtype=float)
    t_wall_c = ta if t_wall_c is None else np.asarray(t_wall_c, dtype=float)
    if eps_sky is None:
        eps_sky = sky_emissivity(ta, rh, cloud)

    # --- shortwave -------------------------------------------------------
    day = elev > 0.0
    sinb = np.where(day, np.sin(np.radians(elev)), 1.0)
    # beam-normal from horizontal; guarded below ~2 deg where 1/sin blows up
    i_dn = np.where(day, i_dir_h / np.maximum(sinb, np.sin(np.radians(2.0))), 0.0)
    beam_h = np.where(day, i_dir_h, 0.0) * sunlit          # beam reaching the ground
    k_dir = f_p(elev) * i_dn * sunlit                      # onto the body, no F_i
    k_dif = F_UPPER * svf * i_dif                          # isotropic sky diffuse
    # ground global -> reflected up into the body's lower hemisphere
    g_gnd = beam_h + i_dif * svf
    k_ref_g = F_LOWER * albedo_g * g_gnd
    # wall global -> reflected into the (1-svf) upper hemisphere. SOLWEIG's Kside_veg
    # form; `wall_sunlit` is the share of visible facades facing the sun. Crudest and
    # smallest term in the model.
    g_wall = wall_sunlit * i_dn * np.cos(np.radians(np.clip(elev, 0.0, 90.0))) * sunlit \
        + 0.5 * i_dif * svf
    k_ref_w = F_UPPER * (1.0 - svf) * albedo_w * g_wall

    # --- longwave --------------------------------------------------------
    # SOLWEIG structure: sky through svf, wall through (1-svf), plus sky reflected
    l_sky = eps_sky * SIGMA * (ta + T0) ** 4
    l_wall = eps_w * SIGMA * (t_wall_c + T0) ** 4
    l_gnd = eps_g * SIGMA * (tsurf_c + T0) ** 4
    l_down = svf * l_sky + (1.0 - svf) * l_wall + (1.0 - svf) * (1.0 - eps_w) * l_sky
    l_up = l_gnd + (1.0 - eps_g) * l_down                  # ground reflects the down flux

    s_str = ABS_K * (k_dir + k_dif + k_ref_g + k_ref_w) \
        + ABS_L * (F_UPPER * l_down + F_LOWER * l_up)
    return (s_str / (ABS_L * SIGMA)) ** 0.25 - T0
