"""Surface-material raster + thermal property table for the energy balance.

Burns City of Melbourne road/footpath surface polygons onto the shared 2m grid.
Writes out/material_id.npy (uint8, 0 = default) and out/material_props.json.

    python -m shademe.pipeline.materials      # build + validate
"""
import os, sys, json, math, pickle, urllib.request, numpy as np
from pyproj import Transformer
from shapely.geometry import shape, Point
import shapely
from rasterio.features import rasterize
from rasterio.transform import from_origin

from ..config import CELL, WGS84, MGA55
from ..paths import DATA, OUT
DATASET = "road-segments-with-surface-type"
URL = ("https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
       f"{DATASET}/exports/geojson")

_tf = Transformer.from_crs(WGS84, MGA55, always_xy=True)

OMEGA = 2 * math.pi / 86400.0              # diurnal angular frequency, rad/s
OMEGA_ANN = 2 * math.pi / (365.25 * 86400.0)   # annual, for the deep reservoir


# ---------------------------------------------------------------- properties
# rho  kg m-3, c  J kg-1 K-1, k  W m-1 K-1.  d is derived (see props()).
# d_cap: physical layer thickness, m -- only for surfaces that sit on a
# thermally-decoupled substrate (mulch over soil, turf canopy over soil,
# timber decking over air). None => semi-infinite, use the damping depth.
MATERIALS = {
    0: dict(name="Default / unmapped", albedo=0.15, emissivity=0.95,
            rho=2000, c=900, k=1.20, d_cap=None,
            source="Oke (1987) BLC bulk-urban albedo 0.10-0.27, eps 0.85-0.96 "
                   "(via Mills 2004, IAUC Teaching Resources Table 2); "
                   "rho c k = mid of the asphalt/concrete pair below."),
    1: dict(name="Bluestone (basalt)", albedo=0.15, emissivity=0.95,
            rho=2650, c=900, k=2.50, d_cap=None,
            source="Zeb et al. (2020) Adv. Mater. Sci. Eng. 4756806: basalt "
                   "rho 2566-2712 kg/m3, k 2.00-3.06 W/mK, C 2.34-2.68 MJ/m3K, "
                   "alpha 0.83-1.25 mm2/s. Albedo/eps: dark grey stone, in the "
                   "Oke (1987) urban 0.10-0.27 band, eps as for rock."),
    2: dict(name="Asphalt (HMA)", albedo=0.12, emissivity=0.95,
            rho=2110, c=920, k=0.75, d_cap=None,
            source="Oke (1987) BLC via Mills (2004) Table 2: asphalt rho 2110, "
                   "C 1.94 MJ/m3K, k 0.75, eps 0.95. Albedo: weathered HMA "
                   "0.10-0.15, Levinson & Akbari (2001) LBNL via ACPA wikipave."),
    3: dict(name="Concrete", albedo=0.27, emissivity=0.90,
            rho=2400, c=879, k=1.51, d_cap=None,
            source="Oke (1987) via Mills (2004) Table 2: dense concrete rho 2400, "
                   "C 2.11 MJ/m3K, k 1.51, albedo 0.10-0.35, eps 0.71-0.91. "
                   "Albedo 0.27 = mid of weathered grey PCC 0.20-0.30 (ACPA "
                   "wikipave / Levinson & Akbari 2001); fresh PCC is 0.35-0.40."),
    4: dict(name="Turf / vegetation", albedo=0.21, emissivity=0.94,
            rho=1600, c=1250, k=0.80, d_cap=None,
            source="Oke (1987) via Mills (2004): grass albedo 0.16-0.26, "
                   "eps 0.90-0.95. Substrate = moist soil, C 2.0 MJ/m3K "
                   "(within the 1.3-2.8 MJ/m3K unconsolidated-ground band). "
                   "REVISED: d was capped at 0.03 m on a canopy+thatch argument; "
                   "uncapped to the full semi-infinite damping depth. Oke's own "
                   "treatment gives a grass surface the admittance of the moist "
                   "soil under it, not of a thin mat, and for IRRIGATED turf the "
                   "thatch is wet and conductive rather than an insulating "
                   "litter layer. The cap only holds for dry litter."),
    5: dict(name="Granitic gravel", albedo=0.20, emissivity=0.92,
            rho=1800, c=800, k=1.00, d_cap=None,
            source="Unconsolidated granular ground C 1.3-2.8 MJ/m3K; mineral "
                   "grains k~3 W/mK diluted by air-filled pores (Dalla Santa "
                   "survey via Pivot GES 5(3) 0007; Rieksts et al. 2017 crushed "
                   "rock). Albedo: buff granitic sand, dry-soil end of Oke's "
                   "0.05-0.40 soil range."),
    6: dict(name="Mulch", albedo=0.20, emissivity=0.95,
            rho=250, c=1600, k=0.15, d_cap=0.075,
            source="Wood chip/bark: k 0.15 W/mK (Sjostrom & Blomqvist 2012, "
                   "Fuel, wood pellets 0.146-0.192; mulch surveys 0.1-0.6), "
                   "bulk rho ~250 kg/m3, c ~1600 J/kgK (dry wood). d = 75 mm "
                   "landscape mulch layer, thinner than its damping depth. "
                   "REVISED albedo 0.13 -> 0.20: field pyranometer measurements "
                   "put wood chips near 0.25 (greenhouse mulch radiation study, "
                   "Agric. For. Meteorol. 2020); 0.20 allows for CoM's aged "
                   "dark hardwood chip being darker than pale fresh chip. "
                   "0.13 was a guess at 'dark bark' and was too low."),
    7: dict(name="Brick", albedo=0.24, emissivity=0.93,
            rho=1800, c=840, k=0.90, d_cap=None,
            source="Fired clay brick rho 1700-1900, c ~800-840 J/kgK "
                   "(material-properties.org / ASHRAE Fundamentals ch.26 "
                   "masonry). k 0.9 = mid of the 0.7-1.31 W/mK spread between "
                   "CIBSE Guide A (~0.77) and material-properties.org (1.31). "
                   "Albedo 0.20-0.30 for red clay pavers."),
    8: dict(name="Granite / stone paver", albedo=0.25, emissivity=0.95,
            rho=2650, c=790, k=3.00, d_cap=None,
            source="Granite rho 2650 kg/m3, k 3.0 W/mK, c 790 J/kgK "
                   "(material-properties.org; METER Group soil/rock mix note). "
                   "Lighter than basalt -> higher albedo."),
    9: dict(name="Timber decking", albedo=0.20, emissivity=0.90,
            rho=600, c=1600, k=0.15, d_cap=0.04,
            source="Softwood rho ~500-700, c ~1600 J/kgK, k 0.12-0.18 W/mK "
                   "(Wood Thermal Properties, IntechOpen ch.52890). d = 40 mm "
                   "deck board over air."),
    10: dict(name="Metal (steel / wrought iron)", albedo=0.35, emissivity=0.35,
             rho=7850, c=490, k=50.0, d_cap=0.01,
             source="Structural steel rho 7850, c 490 J/kgK, k ~50 W/mK "
                    "(ASHRAE Fundamentals ch.26). Low eps: bare/galvanised "
                    "metal 0.2-0.5. d = 10 mm plate; grates/bridge decks only."),
    11: dict(name="Water", albedo=0.07, emissivity=0.97,
             rho=1000, c=4180, k=100.0, d_cap=None,
             source="Water rho 1000, c 4180 J/kgK. k is an EFFECTIVE turbulent "
                    "diffusivity, not the molecular 0.6 W/mK: a river/lake mixes, "
                    "so the diurnal wave penetrates ~0.8 m rather than 6 cm. "
                    "Albedo 0.05-0.10 for high sun angles, eps 0.96-0.99 "
                    "(Oke 1987 BLC water surface). Added for the Yarra and the "
                    "park lakes, which were previously default sealed ground."),
}

# ------------------------------------------------- latent + deep conduction
# Additive hooks read by shademe/physics/surface_temp.py. Both default to 0, which
# leaves the march exactly the plain energy balance.
#
#   beta    evaporative fraction of NET radiation, QE/Q*  [-]
#   k_deep  conductance to a deep reservoir            [W m-2 K-1]  (derived)
#   t_deep  that reservoir's temperature               [K]
#
# MOISTURE ASSUMPTION (states the whole table): a hot, dry summer afternoon,
# >48 h since rain, with CoM's parkland irrigation running. Sealed surfaces are
# dry; turf is irrigated; mulch and gravel hold residual moisture at depth only.
# `beta_dry` is the same surface after a fortnight without rain or watering --
# documented for sensitivity runs, NOT read by the default march.
#
# beta is converted from the Bowen ratio B = QH/QE via the daytime energy
# balance Q* = QH + QE + dQs:   beta = QE/Q* = (1 - dQs/Q*) / (1 + B).
# dQs/Q* ~ 0.10 over grass and ~ 0.28 over impervious urban fabric
# (Grimmond & Oke 1999, J. Appl. Meteorol. 38, 922, heat-storage ratios).
BOWEN_SRC = ("Bowen-ratio scale: 0.2 irrigated crops, 0.5 grassland, 5 semi-arid, "
             "10 desert (Bowen ratio, Encyclopedia of World Climatology, Springer "
             "2005, doi 10.1007/0-387-30749-4_30); urban cores routinely B > 2 and "
             "often > 5. Converted to QE/Q* with dQs/Q* from Grimmond & Oke (1999) "
             "J. Appl. Meteorol. 38, 922. Cross-checked against Priestley-Taylor: "
             "alpha 1.0-1.26 and s/(s+gamma)=0.72 at 25 C give QE/Q* 0.65-0.82 for "
             "well-watered short grass (Grimmond & Oke 1991, WRR 27, 1739).")

# Substrate below the surface node. Moist mineral soil: k 0.8 W/mK,
# rho*c 2.0 MJ/m3K, the same substrate already used for class 4.
SOIL = dict(k=0.80, rho_c=2.0e6)

# Two reservoirs, because the model marches on a diurnal timescale but the only
# depth at which a truly CONSTANT temperature exists is the annual one:
#
#   "annual"  z = annual damping depth of soil (~2.0 m), T = annual mean air.
#             Correct for a node that is already the full diurnal slab -- the
#             next constant thing below it is the seasonal reservoir. Small.
#   "diurnal" z = diurnal damping depth of soil (~0.10 m), T = summer daily-mean
#             air. Correct for a node CAPPED thinner than the diurnal slab
#             (mulch): the soil right underneath is the reservoir that matters,
#             and it sits near the daily mean. An order of magnitude larger.
#   "vent"    ventilated air cavity under decking/plate: a convective
#             conductance to air, not a conduction path into ground.
#
# T_ANNUAL = annual mean 2 m air temperature at the CBD grid point
# (-37.8136, 144.9631), 14.78 C over 2015-2024, Open-Meteo ERA5 archive.
# T_SUMMER = the same series restricted to DJF, 19.73 C over 903 summer days.
# Soil at the diurnal damping depth tracks the daily-mean SURFACE temperature;
# approximating that by daily-mean air is standard and slightly conservative
# (real sunlit soil runs a little warmer, which would warm mulch marginally).
T_DEEP = 273.15 + 14.78          # annual;  kept as the name surface_temp.py sees
T_SUMMER = 273.15 + 19.73

# h_under for a ventilated cavity: ASHRAE Fundamentals ch.26 still-air surface
# conductance for a horizontal surface with heat flow DOWNWARD, non-reflective
# (eps 0.90) = 6.13 W/m2 K, convection plus radiation to the ground below. A
# real deck cavity is open at the edges so the true value is a little higher;
# the still-air figure is the one that is citable, so that is what is used.
H_UNDER = 6.13

HYDRO = {
    0: dict(beta=0.00, beta_dry=0.00, res=None,
            note="Unmapped ground, dominated by sealed plaza/private paving. "
                 "Held at 0 so the default class reproduces the bare contract "
                 "equation."),
    1: dict(beta=0.00, beta_dry=0.00, res=None, note="Sealed stone, dry."),
    2: dict(beta=0.00, beta_dry=0.00, res=None, note="Sealed asphalt, dry."),
    3: dict(beta=0.00, beta_dry=0.00, res=None, note="Sealed concrete, dry."),
    4: dict(beta=0.65, beta_dry=0.15, res="annual",
            note="Irrigated parkland/nature-strip turf, REVISED 0.55 -> 0.65. "
                 "Priestley-Taylor at 30 C: s/(s+gamma) = 0.785 (FAO-56 Allen "
                 "et al. 1998 eq. 13), so a fully wet grass surface reaches "
                 "QE/Rn = alpha*0.785*(1 - G/Rn) = 0.89 at alpha = 1.26. "
                 "0.65 corresponds to alpha ~ 0.92, i.e. well-irrigated but "
                 "short of potential -- comfortably inside the envelope. "
                 "Dry spell: stomata close, B ~ 5 -> beta ~ 0.15."),
    5: dict(beta=0.05, beta_dry=0.00, res=None,
            note="Compacted granitic gravel: permeable, but the surface skin "
                 "is dry by midday on a hot day. B ~ 15 -> beta ~ 0.05."),
    6: dict(beta=0.15, beta_dry=0.03, res="diurnal",
            note="Mulch over irrigated bed: chips dry fast, moisture persists "
                 "beneath. B ~ 5 -> beta = 0.90/6 = 0.15. Mulch is SUPPOSED to "
                 "run hot -- that is how it shields the soil -- so beta stays "
                 "low and the correction comes from the conduction path."),
    7: dict(beta=0.00, beta_dry=0.00, res=None, note="Sealed brick, dry."),
    8: dict(beta=0.00, beta_dry=0.00, res=None, note="Sealed stone, dry."),
    9: dict(beta=0.00, beta_dry=0.00, res="vent",
            note="Decking over ventilated air; no moisture. Measured hardwood "
                 "decking runs 58-60 C at ~30 C ambient (Paul's Decking field "
                 "test; ASTM C1055 puts the 5 s contact-burn threshold at "
                 "60 C), so timber genuinely IS hot -- the only thing missing "
                 "was convection off the underside."),
    10: dict(beta=0.00, beta_dry=0.00, res="vent",
             note="Bare metal grate/bridge deck over air; no moisture."),
    11: dict(beta=0.80, beta_dry=0.80, res=None,
             note="Open water evaporates at close to the potential rate and "
                  "never dries out, so beta_dry = beta. Priestley-Taylor "
                  "alpha 1.26 with s/(s+gamma) = 0.785 at 30 C gives QE/Rn ~ "
                  "0.99 for a free water surface; 0.80 allows for the share "
                  "going into the water column rather than the air."),
}

# CoM `material` string -> class id. Anything not listed falls to 0.
MAP = {
    "Dressed Bluestone": 1, "Bluestone Pitcher": 1, "Bluestone Paver (Other)": 1,
    "Bluestone Paver (1000x500x40)": 1, "Bluestone Triangle Paver": 1,
    "Sawn Bluestone Block (100x100x100)": 1,
    "HMA": 2, "HMA Footpath (CBD)": 2, "HMA Footpath (Non CBD)": 2, "HMA-Porous": 2,
    "Concrete": 3, "Cast in Situ Off Form": 3, "Cast in Situ Exposed Agg": 3,
    "Cast-In-Situ Concrete": 3, "Pre Cast Off Form": 3, "Pre Cast Exposed Agg": 3,
    "Precast Concrete": 3, "Concrete Paver": 3, "Terrazzo Paver": 3,
    "Turf": 4, "Grass": 4, "Plants": 4, "Synthetic Turf": 4,
    "Granitic Gravel": 5, "Crushed Rock": 5, "Pebble Paving": 5, "Sand": 5,
    "Mulch": 6,
    "Brick": 7, "Brick Paver": 7,
    "Granite Paver": 8, "Other Paver": 8, "Crazy Paving": 8, "Masonry": 8,
    "Timber": 9, "Woodpaving": 9,
    "Steel": 10, "Wrought Iron": 10,
}
# "N Row Bluestone Pitcher" for N = 1..15
MAP.update({f"{n} Row Bluestone Pitcher": 1 for n in range(1, 16)})

# Burn order: later wins on overlap. Footways last -- a pedestrian standing on
# the kerb line is on the footpath, not in the gutter.
TYPE_ORDER = {"Carriageway": 0, "Tramway": 0, "Bridge": 1, "Median": 2,
              "Road Channel": 3, "Road Kerb": 4, "Footway": 5}

# ------------------------------------------------------- open space (OSM)
# City of Melbourne publishes NO open-space / parks / landcover polygon layer
# (checked all 239 datasets in the catalogue -- see report). The nearest things
# are 27 polygons of 1956 surface geology, a garden-bed inventory with no
# geometry at all, and 37 park POINTS inside the landmarks dataset. So the
# parks come from OpenStreetMap instead, which already supplies this project's
# entire routing graph. Burned UNDERNEATH the road layer, so CoM road/footpath
# polygons still win wherever the two overlap.
OSM_LC = "https://overpass-api.de/api/interpreter"
LC_KEYS = ("leisure", "landuse", "natural", "waterway")

# tag value -> (class id, burn priority). Higher priority burns later = wins.
# The park ENVELOPE goes down first, then the more specific covers inside it.
LC_MAP = {
    # --- park envelopes (coarse) ---
    "leisure=park": (4, 0), "leisure=garden": (4, 0), "leisure=common": (4, 0),
    "leisure=recreation_ground": (4, 0), "leisure=golf_course": (4, 0),
    "leisure=dog_park": (4, 0), "landuse=recreation_ground": (4, 0),
    "landuse=village_green": (4, 0), "landuse=cemetery": (4, 0),
    "landuse=greenfield": (4, 0),
    # --- specific vegetated cover (finer, burns over the envelope) ---
    "landuse=grass": (4, 1), "landuse=meadow": (4, 1),
    "natural=grassland": (4, 1), "natural=scrub": (4, 1),
    "landuse=forest": (4, 1), "natural=wood": (4, 1),
    "leisure=pitch": (4, 1),
    # --- garden beds are mulched ---
    "landuse=flowerbed": (6, 2),
    # --- mineral ---
    "natural=sand": (5, 2), "natural=shingle": (5, 2),
    # --- water, last: a lake inside a park is water, not lawn ---
    "natural=water": (11, 3), "natural=wetland": (11, 3),
    "waterway=riverbank": (11, 3), "landuse=reservoir": (11, 3),
}
LC_QUERY = """[out:json][timeout:300];
(
  way["leisure"~"^(park|garden|common|pitch|recreation_ground|dog_park|golf_course)$"]({b});
  rel["leisure"~"^(park|garden|common|pitch|recreation_ground|dog_park|golf_course)$"]({b});
  way["landuse"~"^(grass|recreation_ground|forest|meadow|village_green|cemetery|greenfield|flowerbed)$"]({b});
  rel["landuse"~"^(grass|recreation_ground|forest|meadow|village_green|cemetery|greenfield|flowerbed)$"]({b});
  way["natural"~"^(water|grassland|wood|scrub|sand|bare_rock|wetland|shingle)$"]({b});
  rel["natural"~"^(water|grassland|wood|scrub|sand|bare_rock|wetland|shingle)$"]({b});
  way["waterway"="riverbank"]({b});
  rel["waterway"="riverbank"]({b});
);
out geom;"""


def props():
    """Fill in alpha, damping depth and rho_c_d for every class.

    Diurnal damping depth of a semi-infinite solid, d = sqrt(2*alpha/omega)
    with alpha = k/(rho*c) and omega = 2*pi/86400 s^-1. That is the e-folding
    depth of the daily temperature wave, i.e. the slab that actually swings
    with the surface -- the right thickness for a single-node energy balance.
    Layered surfaces (mulch, turf, decking, plate) are capped at their real
    thickness because the substrate below is thermally decoupled.

    Equivalent to the thermal-admittance route, mu*sqrt(2/omega) with
    mu = sqrt(k*rho*c): both reduce to sqrt(2*k*rho*c/omega) for the uncapped
    classes. The capped four are the deliberate departures.

    k_deep couples the node to a reservoir at the ANNUAL damping depth, which
    is where a constant t_deep is actually true. Only classes whose `d` was
    capped AND which sit on real ground get one -- for the uncapped classes the
    node already IS the diurnal slab, so there is nothing left to conduct into.
    """
    out = {}
    for i, m in MATERIALS.items():
        rho_c = m["rho"] * m["c"]
        alpha = m["k"] / rho_c
        d_damp = math.sqrt(2 * alpha / OMEGA)
        d = d_damp if m["d_cap"] is None else min(d_damp, m["d_cap"])
        hy = HYDRO[i]
        res = hy["res"]
        a_sub = SOIL["k"] / SOIL["rho_c"]
        z_ann = math.sqrt(2 * a_sub / OMEGA_ANN)      # ~2.0 m
        z_diu = math.sqrt(2 * a_sub / OMEGA)          # ~0.10 m
        if res == "annual":
            k_deep, t_deep, z_deep = SOIL["k"] / (z_ann - d), T_DEEP, z_ann
            why = ("Node is already the full diurnal slab, so the next constant "
                   f"reservoir is the annual one at {z_ann:.2f} m; "
                   "k_deep = k_soil/(z_ann - d). t_deep = annual mean air.")
        elif res == "diurnal":
            # series: half the capped layer itself, then soil to the diurnal depth
            r = (d / 2) / m["k"] + z_diu / SOIL["k"]
            k_deep, t_deep, z_deep = 1.0 / r, T_SUMMER, d + z_diu
            why = ("Node is capped THINNER than the diurnal slab, so the soil "
                   f"immediately below (to {z_diu:.3f} m) is the reservoir that "
                   "matters; k_deep = 1/((d/2)/k_self + z_diurnal/k_soil). "
                   "t_deep = summer daily-mean air.")
        elif res == "vent":
            k_deep, t_deep, z_deep = H_UNDER, T_SUMMER, None
            why = ("Ventilated cavity underneath: k_deep is a convective+radiant "
                   f"surface conductance ({H_UNDER} W/m2 K), not a ground path. "
                   "t_deep = summer daily-mean air (the cavity air).")
        else:
            k_deep, t_deep, z_deep = 0.0, T_DEEP, None
            why = ("0: sealed surface on subgrade of its own kind, or open "
                   "water. Empirically these classes already march to realistic "
                   "Ts without a deep sink, so none is imposed.")
        out[i] = dict(name=m["name"], albedo=m["albedo"], emissivity=m["emissivity"],
                      rho=m["rho"], c=m["c"], k=m["k"],
                      rho_c=rho_c, alpha=alpha, d_damping=d_damp, d=d,
                      rho_c_d=rho_c * d, source=m["source"],
                      beta=hy["beta"], beta_dry=hy["beta_dry"],
                      k_deep=k_deep, t_deep=t_deep, z_deep=z_deep,
                      reservoir=res or "none",
                      beta_source=hy["note"] + " " + BOWEN_SRC,
                      deep_source=why)
    return out


def check_props(P):
    """Sanity gate from the spec. Raises on anything indefensible."""
    bad = []
    for i, p in P.items():
        if not 0.0 <= p["albedo"] <= 1.0: bad.append(f"{p['name']}: albedo {p['albedo']}")
        if not 0.0 < p["emissivity"] <= 1.0: bad.append(f"{p['name']}: eps {p['emissivity']}")
        if p["rho_c_d"] <= 0: bad.append(f"{p['name']}: rho_c_d {p['rho_c_d']}")
    a = {i: P[i]["albedo"] for i in P}
    if not a[3] > a[7] > a[1] > a[2]:
        bad.append("albedo order concrete > brick > bluestone > asphalt violated")
    low = max(P[4]["rho_c_d"], P[6]["rho_c_d"])
    high = min(P[1]["rho_c_d"], P[3]["rho_c_d"])
    if low >= high:
        bad.append(f"inertia order violated: turf/mulch {low:.0f} >= stone/concrete {high:.0f}")
    for i in (1, 2, 3, 4, 5, 6, 7):
        if not 0.88 <= P[i]["emissivity"] <= 0.99:
            bad.append(f"{P[i]['name']}: eps {P[i]['emissivity']} outside 0.88-0.99")
    # additive: latent + deep conduction
    for i, p in P.items():
        if not 0.0 <= p["beta"] <= 1.0: bad.append(f"{p['name']}: beta {p['beta']}")
        if not 0.0 <= p["beta_dry"] <= p["beta"] + 1e-12:
            bad.append(f"{p['name']}: beta_dry {p['beta_dry']} > beta {p['beta']}")
        if p["k_deep"] < 0: bad.append(f"{p['name']}: k_deep {p['k_deep']}")
        if p["k_deep"] > 0 and not 270 < p["t_deep"] < 310:
            bad.append(f"{p['name']}: t_deep {p['t_deep']} K implausible")
    for i in (1, 2, 3, 7, 8, 10):          # sealed + metal must be dry
        if P[i]["beta"] != 0.0: bad.append(f"{P[i]['name']}: sealed but beta {P[i]['beta']}")
    if not P[11]["beta"] > P[4]["beta"] > P[6]["beta"] > P[5]["beta"] > P[2]["beta"]:
        bad.append("beta order water > turf > mulch > gravel > asphalt violated")
    if not P[11]["rho_c_d"] > P[1]["rho_c_d"]:
        bad.append("water must have the highest thermal inertia of all classes")
    # admittance cross-check: rho_c_d must equal sqrt(2*k*rho*c/omega) uncapped
    for i, p in P.items():
        if MATERIALS[i]["d_cap"] is None:
            adm = math.sqrt(2 * p["k"] * p["rho_c"] / OMEGA)
            if abs(adm - p["rho_c_d"]) / adm > 1e-9:
                bad.append(f"{p['name']}: rho_c_d != admittance route ({adm:.6e})")
    return bad


# ------------------------------------------------------------------ fetch
def fetch(path=None):
    path = path or os.path.join(DATA, "road_surface.geojson")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print(f"  cached  road_surface.geojson ({os.path.getsize(path)/1e6:.1f} MB)")
        return path
    print("  fetching road_surface ...")
    urllib.request.urlretrieve(URL, path)
    print(f"  done    ({os.path.getsize(path)/1e6:.1f} MB)")
    return path


def fetch_landcover(path=None):
    """Overpass -> data/osm_landcover.json. Never re-downloads."""
    import urllib.parse
    from ..config import BBOX
    path = path or os.path.join(DATA, "osm_landcover.json")
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        print(f"  cached  osm_landcover.json ({os.path.getsize(path)/1e6:.1f} MB)")
        return path
    b = (f'{BBOX["min_lat"]},{BBOX["min_lon"]},'
         f'{BBOX["max_lat"]},{BBOX["max_lon"]}')
    print("  querying overpass for landcover ...")
    req = urllib.request.Request(
        OSM_LC, data=urllib.parse.urlencode({"data": LC_QUERY.format(b=b)}).encode(),
        headers={"User-Agent": "shademe-melbhack/0.1 (hackathon project)"})
    with urllib.request.urlopen(req, timeout=600) as r:
        open(path, "wb").write(r.read())
    print(f"  done    ({os.path.getsize(path)/1e6:.1f} MB)")
    return path


def _ring(geom):
    return [(p["lon"], p["lat"]) for p in geom]


def _poly(el):
    """Shapely polygon(s) in lon/lat for one OSM way or multipolygon relation."""
    from shapely.geometry import Polygon, MultiPolygon
    if el["type"] == "way":
        r = _ring(el.get("geometry", []))
        return Polygon(r) if len(r) >= 4 else None
    outers, inners = [], []
    for mem in el.get("members", []):
        if "geometry" not in mem or len(mem["geometry"]) < 4:
            continue
        (outers if mem.get("role") != "inner" else inners).append(
            Polygon(_ring(mem["geometry"])))
    if not outers:
        return None
    out = []
    for o in outers:
        if not o.is_valid:
            o = o.buffer(0)
        holes = [h for h in inners if o.contains(h.representative_point())]
        for h in holes:
            o = o.difference(h)
        out.append(o)
    g = out[0] if len(out) == 1 else MultiPolygon([p for p in out if not p.is_empty])
    return g


def load_landcover(path, bounds):
    """Reproject OSM landcover polys intersecting the grid -> (geom, id, prio)."""
    els = json.load(open(path))["elements"]
    minx, miny, maxx, maxy = bounds
    (w, s), (e, n) = (_tf.transform(minx, miny, direction="INVERSE"),
                      _tf.transform(maxx, maxy, direction="INVERSE"))
    keep, skip, tally = [], 0, {}
    for el in els:
        t = el.get("tags", {})
        hit = next(((f"{k}={t[k]}") for k in LC_KEYS
                    if k in t and f"{k}={t[k]}" in LC_MAP), None)
        if hit is None:
            skip += 1; continue
        try:
            g = _poly(el)
        except Exception:
            skip += 1; continue
        if g is None or g.is_empty:
            skip += 1; continue
        a, b, c, d = g.bounds
        if c < w or a > e or d < s or b > n:
            skip += 1; continue
        cid, prio = LC_MAP[hit]
        if not g.is_valid:
            g = g.buffer(0)
        keep.append((to_mga(g), cid, prio))
        tally[hit] = tally.get(hit, 0) + 1
    return keep, skip, tally


def to_mga(geom):
    return shapely.transform(geom, lambda c: np.column_stack(_tf.transform(c[:, 0], c[:, 1])))


def ll_bbox(coords):
    """Cheap lon/lat bbox straight off nested geojson coordinate lists."""
    stack = [coords]
    xs, ys = [], []
    while stack:
        o = stack.pop()
        if isinstance(o[0], (int, float)):
            xs.append(o[0]); ys.append(o[1])
        else:
            stack.extend(o)
    return min(xs), min(ys), max(xs), max(ys)


def load(path, bounds):
    """Reproject features that intersect the grid. Returns (geom, id, props)."""
    feats = json.load(open(path))["features"]
    minx, miny, maxx, maxy = bounds
    (w, s), (e, n) = (_tf.transform(minx, miny, direction="INVERSE"),
                      _tf.transform(maxx, maxy, direction="INVERSE"))
    keep, out_of_box, no_geom = [], 0, 0
    for f in feats:
        g = f.get("geometry")
        if not g or not g.get("coordinates"):
            no_geom += 1; continue
        a, b, c, d = ll_bbox(g["coordinates"])
        if c < w or a > e or d < s or b > n:
            out_of_box += 1; continue
        p = f["properties"]
        keep.append((to_mga(shape(g)), MAP.get(p.get("material"), 0), p))
    return keep, len(feats), out_of_box, no_geom


def breakdown(path):
    """Full material + type census over the whole dataset (not just the grid)."""
    feats = json.load(open(path))["features"]
    mat, typ = {}, {}
    for f in feats:
        p = f["properties"]
        a = p.get("area_sqm") or 0.0
        for d, k in ((mat, p.get("material")), (typ, p.get("type"))):
            e = d.setdefault(k, [0, 0.0]); e[0] += 1; e[1] += a
    return mat, typ, len(feats)


# ------------------------------------------------------------------ build
def build(grid, feats, lc=()):
    """Landcover first, roads second -- rasterize is last-wins, so CoM road and
    footpath polygons always beat the OSM park envelope underneath them."""
    minx, miny, maxx, maxy = grid["bounds"]
    h, w = grid["h"], grid["w"]
    transform = from_origin(minx, maxy, CELL, CELL)
    shapes = [(g, i) for g, i, prio in sorted(lc, key=lambda t: t[2])]
    shapes += [(g, i) for g, i, p in
               sorted(feats, key=lambda t: TYPE_ORDER.get(t[2].get("type"), 0))]
    return rasterize(shapes, out_shape=(h, w), transform=transform,
                     dtype="uint8", fill=0), transform


def rc(grid, x, y):
    return int((grid["bounds"][3] - y) / CELL), int((x - grid["bounds"][0]) / CELL)


# ------------------------------------------------------------------ validate
def edge_coverage(mid, grid, n=8):
    """Fraction of pedestrian-graph edges landing on mapped material.

    Mirrors sample_hourly() in build_graph.py: n points per edge, nearest cell.
    Loads graph.pkl read-only.
    """
    p = os.path.join(OUT, "graph.pkl")
    if not os.path.exists(p):
        print("  ! no graph.pkl, skipping edge coverage"); return None
    G = pickle.load(open(p, "rb"))
    minx, maxy = grid["bounds"][0], grid["bounds"][3]
    H, W = grid["h"], grid["w"]
    out = {}
    for label, sel in (("all", lambda d: True),
                       ("outdoor", lambda d: not (d.get("indoor") or d.get("covered")))):
        E = [(u, v) for u, v, d in G.edges(data=True) if sel(d)]
        if not E:
            out[label] = None; continue
        xy = np.array([[G.nodes[u]["xy"], G.nodes[v]["xy"]] for u, v in E])
        f = ((np.arange(n) + 0.5) / n)[None, :, None]
        pts = xy[:, 0][:, None, :] + (xy[:, 1] - xy[:, 0])[:, None, :] * f
        r = ((maxy - pts[..., 1]) / CELL).astype(np.int32)
        c = ((pts[..., 0] - minx) / CELL).astype(np.int32)
        inside = (r >= 0) & (r < H) & (c >= 0) & (c < W)
        vals = mid[np.clip(r, 0, H - 1), np.clip(c, 0, W - 1)] * inside
        hit = (vals > 0)
        maj = hit.mean(1) >= 0.5
        d = dict(n_edges=len(E), pt_hit=float(hit.mean()),
                 any_hit=float(hit.any(1).mean()), most_hit=float(maj.mean()),
                 modal=_modal(vals))
        if label == "outdoor":
            ll = np.array([[G.nodes[u]["ll"], G.nodes[v]["ll"]] for u, v in E]).mean(1)
            core = ((ll[:, 0] > 144.955) & (ll[:, 0] < 144.975) &
                    (ll[:, 1] > -37.822) & (ll[:, 1] < -37.805))
            d["core"] = (int(core.sum()), float(maj[core].mean()))
            d["rest"] = (int((~core).sum()), float(maj[~core].mean()))
        out[label] = d
    return out


def dilate(mid, k):
    """Nearest-mapped-class fill within k cells. Diagnostic only -- shows how
    much of an edge miss is OSM-centreline-vs-polygon registration slop rather
    than genuinely unmapped ground. Never written to disk."""
    from scipy.ndimage import distance_transform_edt
    d, (ri, ci) = distance_transform_edt(mid == 0, return_distances=True,
                                         return_indices=True)
    return np.where(d <= k, mid[ri, ci], 0).astype(np.uint8)


def _modal(vals):
    """Dominant class per edge -> histogram of edge counts by class."""
    dom = np.zeros(len(vals), dtype=np.uint8)
    for i, row in enumerate(vals):
        r = row[row > 0]
        if len(r): dom[i] = np.bincount(r).argmax()
    return {int(k): int(v) for k, v in zip(*np.unique(dom, return_counts=True))}


# Coordinates lifted from named ways in data/osm_walk.json, with OSM's own
# `surface` tag as an independent second opinion. `expect` = classes that would
# be defensible; None = no strong prior.
# ------------------------------------------------- energy-balance diagnostic
SIGMA = 5.670374419e-8


def demo_weather(date="2026-01-26", path=None):
    """Cache the demo day's hourly forcing. Only used by --check."""
    path = path or os.path.join(DATA, f"weather_eb_{date}.json")
    if os.path.exists(path) and os.path.getsize(path) > 500:
        return json.load(open(path))
    import urllib.parse
    q = urllib.parse.urlencode(dict(
        latitude=-37.8136, longitude=144.9631, start_date=date, end_date=date,
        hourly="temperature_2m,shortwave_radiation,relative_humidity_2m,wind_speed_10m",
        timezone="Australia/Melbourne", wind_speed_unit="ms"))
    with urllib.request.urlopen(
            f"https://archive-api.open-meteo.com/v1/archive?{q}", timeout=120) as r:
        d = json.load(r)
    json.dump(d, open(path, "w"))
    return d


def march(p, wx, h_conv, days=4, dt=60.0, shade=0.0):
    """Explicit-Euler diurnal march of the contract equation, full sun by default.

    rho_c_d dTs/dt = (1-a)S + eps*L_sky - eps*sigma*Ts^4
                     - h(Ts-Ta) - beta*Qnet - k_deep*(Ts-t_deep)

    Looped over the same day until diurnal steady state. Returns Ts by hour, C.
    """
    H = wx["hourly"]
    Ta = np.array(H["temperature_2m"], dtype=float) + 273.15
    S = np.array(H["shortwave_radiation"], dtype=float) * (1.0 - shade)
    RH = np.array(H["relative_humidity_2m"], dtype=float)
    # Brutsaert (1975) clear-sky emissivity from screen-level vapour pressure
    es = 6.112 * np.exp(17.67 * (Ta - 273.15) / (Ta - 29.65))
    e = RH / 100.0 * es
    eps_sky = 1.24 * (e / Ta) ** (1 / 7)
    L = eps_sky * SIGMA * Ta ** 4
    a, eps, C = p["albedo"], p["emissivity"], p["rho_c_d"]
    beta, kd, td = p["beta"], p["k_deep"], p["t_deep"]
    Ts = Ta[0]
    n = int(3600 / dt)
    for _ in range(days):
        for hr in range(24):
            for _ in range(n):
                qn = (1 - a) * S[hr] + eps * L[hr] - eps * SIGMA * Ts ** 4
                Ts += dt / C * (qn - h_conv * (Ts - Ta[hr])
                                - beta * qn - kd * (Ts - td))
        if _ == days - 1:
            break
    out = {}
    for hr in range(24):
        for _ in range(n):
            qn = (1 - a) * S[hr] + eps * L[hr] - eps * SIGMA * Ts ** 4
            Ts += dt / C * (qn - h_conv * (Ts - Ta[hr]) - beta * qn - kd * (Ts - td))
        out[hr] = Ts - 273.15
    return out


def calibrate_h(P, wx, target=64.6, hour=14, cls=2):
    """Pick h so full-sun ASPHALT hits the coordinator's validated p99 at 14:00.

    h is surface_temp.py's constant, not mine -- this only makes my numbers
    comparable to theirs. Nothing about the material table is fitted.
    """
    lo, hi = 2.0, 60.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if march(P[cls], wx, mid)[hour] > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


SPOTS = [
    ("Hardware Lane",      144.96128, -37.81372, "paving_stones", (1, 8, 3)),
    ("Degraves Street",    144.96560, -37.81691, "paving_stones", (1, 8)),
    ("Centre Place",       144.96552, -37.81658, "paving_stones", (1, 8)),
    ("Block Place",        144.96425, -37.81508, "paving_stones", (1, 8, 3)),
    ("Hosier Lane",        144.96903, -37.81625, "sett",          (1,)),
    ("Elizabeth St",       144.96360, -37.81501, "asphalt",       (2,)),
    ("Swanston St",        144.96592, -37.81414, "asphalt",       (2, 1)),
    ("Collins St",         144.96488, -37.81590, "asphalt",       (2,)),
]


PARKS = ["Flagstaff Gardens", "Carlton Gardens", "Fitzroy Gardens",
         "Treasury Gardens", "Alexandra Gardens", "Birrarung Marr"]


def park_checks(mid, grid, lcpath, P, win=40):
    """Class mix over a +-80 m window at each named park's representative point.

    The point itself often lands on an internal path, so the MIX is the check:
    a park that is mostly turf has been burned correctly, and the asphalt share
    is the road layer correctly winning on top of it.
    """
    els = json.load(open(lcpath))["elements"]
    rows = []
    for name in PARKS:
        for el in els:
            t = el.get("tags", {})
            if t.get("name") != name or t.get("leisure") not in ("park", "garden"):
                continue
            try:
                g = _poly(el)
            except Exception:
                g = None
            if g is None or g.is_empty:
                continue
            c = to_mga(g).representative_point()
            r, cc = rc(grid, c.x, c.y)
            w = mid[max(0, r - win):r + win, max(0, cc - win):cc + win]
            if not w.size:
                continue
            u, ct = np.unique(w, return_counts=True)
            mix = {P[int(a)]["name"]: round(b / w.size * 100)
                   for a, b in zip(u, ct) if b / w.size > 0.02}
            rows.append((name, r, cc, mix))
            break
    return rows


def spot_checks(mid, grid, feats):
    """Raster value vs the source polygon that actually covers the CELL CENTRE.

    rasterize() paints a cell when its centre falls inside a polygon, so the
    containment test has to be run at that same centre -- otherwise a correct
    raster looks wrong near edges. Independent of the rasteriser, so a flipped
    axis or an off-by-one transform shows up as a disagreement.
    """
    minx, maxy = grid["bounds"][0], grid["bounds"][3]
    rows = []
    for name, lon, lat, osm, expect in SPOTS:
        x, y = _tf.transform(lon, lat)
        r, c = rc(grid, x, y)
        inb = 0 <= r < grid["h"] and 0 <= c < grid["w"]
        got = int(mid[r, c]) if inb else -1
        # centre of cell (r,c), back in MGA55
        cx, cy = minx + (c + 0.5) * CELL, maxy - (r + 0.5) * CELL
        pt = Point(cx, cy)
        src = [(p.get("material"), p.get("type"), MAP.get(p.get("material"), 0))
               for g, i, p in feats if g.covers(pt)]
        ids = [i for _, _, i in src]
        if src:
            ok = "OK" if got in ids else "MISMATCH"          # rasteriser agrees?
        else:
            ok = "OK (bare)" if got == 0 else "MISMATCH (painted, no polygon)"
        rows.append(dict(name=name, lon=lon, lat=lat, r=r, c=c, osm=osm,
                         raster=got, expect=expect, src=src, ok=ok,
                         plausible=(got in expect) if expect else None))
    return rows


# ------------------------------------------------------------------- main
def run_check(P):
    """python shademe/pipeline/materials.py --check -- full-sun diurnal march per class."""
    wx = demo_weather()
    H = wx["hourly"]
    h_conv = calibrate_h(P, wx)
    print(f"energy-balance check: demo day 2026-01-26, FULL SUN (shade=0), "
          f"Ta@14 {H['temperature_2m'][14]:.1f} C, "
          f"S@14 {H['shortwave_radiation'][14]:.0f} W/m2 global horizontal")
    print(f"  h calibrated to {h_conv:.1f} W/m2K so asphalt hits 64.6 C at 14:00")
    print(f"  {'class':<28}{'Ts@14':>8}{'vs Ta':>8}{'Ts max':>8}{'@h':>4}")
    for i, p in P.items():
        t = march(p, wx, h_conv)
        pk = max(t.values()); ph = max(t, key=t.get)
        print(f"  {i:>2} {p['name']:<25}{t[14]:>8.1f}{t[14]-H['temperature_2m'][14]:>+8.1f}"
              f"{pk:>8.1f}{ph:>4}")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    P = props()
    if "--check" in sys.argv:
        run_check(P); sys.exit(0)
    bad = check_props(P)
    print("property table:")
    print(f"  {'class':<28}{'alb':>6}{'eps':>6}{'k':>7}{'rho*c':>10}"
          f"{'d_damp':>8}{'d':>7}{'rho_c_d':>11}{'beta':>7}{'(dry)':>7}{'k_deep':>8}")
    for i, p in P.items():
        print(f"  {i:>2} {p['name']:<25}{p['albedo']:>6.2f}{p['emissivity']:>6.2f}"
              f"{p['k']:>7.2f}{p['rho_c']/1e6:>10.2f}{p['d_damping']:>8.3f}"
              f"{p['d']:>7.3f}{p['rho_c_d']:>11.3e}{p['beta']:>7.2f}"
              f"{p['beta_dry']:>7.2f}{p['k_deep']:>8.3f}")
    print(f"  t_deep {T_DEEP:.2f} K ({T_DEEP-273.15:.2f} C), reservoir depth "
          f"{P[4]['z_deep']:.2f} m; k_deep = 0 means the term is inert")
    print("  sanity:", "OK" if not bad else "FAILED")
    for b in bad: print("   !", b)

    path = fetch()
    print("census (whole dataset):")
    mat, typ, nfeat = breakdown(path)
    print(f"  {nfeat} features")
    print(f"  {'type':<16}{'n':>8}{'area_sqm':>14}")
    for k, (n, a) in sorted(typ.items(), key=lambda t: -t[1][1]):
        print(f"  {str(k):<16}{n:>8}{a:>14,.0f}")
    print(f"  {'material':<38}{'n':>8}{'area_sqm':>14}{'cls':>5}")
    for k, (n, a) in sorted(mat.items(), key=lambda t: -t[1][1]):
        print(f"  {str(k):<38}{n:>8}{a:>14,.0f}{MAP.get(k, 0):>5}")
    # per-class rollup
    roll = {}
    for k, (n, a) in mat.items():
        e = roll.setdefault(MAP.get(k, 0), [0, 0.0]); e[0] += n; e[1] += a
    print(f"  {'-> class':<38}{'n':>8}{'area_sqm':>14}")
    for i, (n, a) in sorted(roll.items(), key=lambda t: -t[1][1]):
        print(f"  {P[i]['name']:<38}{n:>8}{a:>14,.0f}")

    grid = json.load(open(os.path.join(OUT, "grid.json")))
    print(f"grid {grid['w']} x {grid['h']} @ {grid['cell']}m")
    print("loading + reprojecting ...")
    feats, ntot, oob, nog = load(path, grid["bounds"])
    print(f"  {len(feats)} features on grid ({oob} outside, {nog} no geometry, {ntot} total)")

    lcpath = fetch_landcover()
    lc, lcskip, tally = load_landcover(lcpath, grid["bounds"])
    print(f"  {len(lc)} open-space polys on grid ({lcskip} skipped)")
    for k, v in sorted(tally.items(), key=lambda t: -t[1]):
        print(f"     {k:<28} {v:>5}  -> {P[LC_MAP[k][0]]['name']}")

    print("rasterising (landcover under roads) ...")
    mid, _ = build(grid, feats, lc)
    np.save(os.path.join(OUT, "material_id.npy"), mid)
    json.dump({str(i): p for i, p in P.items()},
              open(os.path.join(OUT, "material_props.json"), "w"), indent=1)
    print("  saved out/material_id.npy, out/material_props.json")

    tot = mid.size
    print("coverage:")
    print(f"  mapped {(mid>0).sum()/1e6:.2f}M / {tot/1e6:.2f}M cells "
          f"= {(mid>0).mean()*100:.2f}%  (default {(mid==0).mean()*100:.2f}%)")
    for i, n in zip(*np.unique(mid, return_counts=True)):
        print(f"   {int(i):>2} {P[int(i)]['name']:<28}{n:>10}  {n/tot*100:>6.2f}%")

    cov = edge_coverage(mid, grid)
    if cov:
        for label, d in cov.items():
            if d is None: continue
            print(f"  graph edges [{label}]: {d['n_edges']} edges  "
                  f"points {d['pt_hit']*100:.1f}%  any {d['any_hit']*100:.1f}%  "
                  f"majority {d['most_hit']*100:.1f}%")
            print("    dominant class:", {P[k]['name']: v for k, v in d['modal'].items()})
            if "core" in d:
                print(f"    CBD core (144.955-144.975 x -37.822..-37.805): "
                      f"{d['core'][0]} edges, {d['core'][1]*100:.1f}% majority-mapped; "
                      f"rest {d['rest'][0]} edges, {d['rest'][1]*100:.1f}%")

    print("registration diagnostic (nearest-class fill, NOT saved):")
    for k in (1, 2, 3):
        c2 = edge_coverage(dilate(mid, k), grid)
        if c2 and c2["outdoor"]:
            print(f"  fill {k*CELL:.0f}m: cells {(dilate(mid,k)>0).mean()*100:5.2f}%   "
                  f"outdoor edge points {c2['outdoor']['pt_hit']*100:.1f}%  "
                  f"majority {c2['outdoor']['most_hit']*100:.1f}%")

    print("park checks (class mix in a +-80 m window; turf-dominated = correct):")
    for name, r, c, mix in park_checks(mid, grid, lcpath, P):
        print(f"  {name:<20} r={r:<5} c={c:<5} {mix}")

    print("spot checks (raster vs source polygon covering the same cell centre):")
    for s in spot_checks(mid, grid, feats):
        src = "; ".join(f"{m}/{t}->{i}" for m, t, i in s["src"]) or "(no polygon)"
        pl = "" if s["plausible"] is None else ("  plausible" if s["plausible"]
                                               else "  <- NOT the expected class")
        print(f"  {s['name']:<16} {s['lon']:.5f},{s['lat']:.5f} r={s['r']:<5} c={s['c']:<5}"
              f" osm={s['osm']:<14} raster={s['raster']} {P[s['raster']]['name']:<22}"
              f" {s['ok']}{pl}")
        print(f"     source @ cell centre: {src}")
