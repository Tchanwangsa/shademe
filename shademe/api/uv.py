"""Ultraviolet: the live index, and how much of it reaches one edge of the graph.

Separate from `weather` because UV is not a thermal quantity and must not be inferred
from one. Two things this module exists to stop:

  * INVENTING AN INDEX FROM RADIATION. The old `(direct + diffuse) / 100` fallback read
    UV 6 in Melbourne in late winter against 3.6 measured. Broadband W/m2 and erythemal
    irradiance are different quantities and no constant converts one to the other. There
    is no estimate here; an absent index is None.
  * TREATING CLEAR-SKY UV AS ACTUAL UV. Open-Meteo returns `uv_index` and
    `uv_index_clear_sky` equal at every hour for this cell, cloud cover included, so
    whatever the field is meant to be, what arrives is the clear-sky value. It is
    attenuated here rather than trusted.
"""
import os, time, json
import xml.etree.ElementTree as ET

import requests

from ..paths import DATA

# --- live measurement ---------------------------------------------------------
# ARPANSA runs the Australian UV monitoring network and publishes each station's current
# index about once a minute. This is a MEASUREMENT from a spectroradiometer, and it is the
# same number the BOM app shows -- which is what the user will check us against.
ARPANSA_XML = "https://uvdata.arpansa.gov.au/xml/uvvalues.xml"
ARPANSA_SITE = "Melbourne"
TTL = 600                     # the index moves slowly; one fetch per 10 min is plenty
CACHE = os.path.join(DATA, "uv_cache.json")

# --- cloud attenuation, for the hours the measurement cannot cover -------------
# Josefsson & Landelius (2000) erythemal transmission: tau = 1 - A C^p, C the cloud
# fraction. Cloud cuts UV far less than broadband, because so much of the UV reaching the
# ground is already diffuse; the exponent encodes that, so broken cloud barely registers
# and only near-overcast bites. Checked rather than assumed: clear-sky 4.8 at 13:00 under
# 78% cloud gives 3.64, against 3.6 measured at 13:41.
CLOUD_A, CLOUD_P = 0.56, 3.4

# --- the exposure model -------------------------------------------------------
# What fraction of the open-sky index reaches a walker on an edge:
#
#     f = f_dir * (1 - shade) + f_dif * svf
#
# The two terms are the two ways UV arrives, and a shade router has to keep them apart: a
# building blocks the beam and the sky, a street tree blocks the beam and leaves most of
# the sky, an awning blocks the sky while the beam still comes in sideways. Only the
# sky-view term tells those apart, which is why SVF is in here.
#
# UV_DIFFUSE_FLOOR is the diffuse share of erythemal UV under a CLEAR sky. Rayleigh
# scattering goes as lambda^-4, so at 300 nm the sky scatters an order of magnitude harder
# than in the visible: measured clear-sky diffuse fractions sit around 0.45-0.55, against
# ~0.15 broadband. This is the most important number in the file -- it is why standing in
# the shade of a pole does almost nothing and an arcade does everything.
UV_DIFFUSE_FLOOR = 0.45

# 1 UV index unit = 25 mW/m2 erythemally weighted. One SED (standard erythemal dose) is
# 100 J/m2. So one index-minute = 25e-3 * 60 / 100 = 0.015 SED.
SED_PER_INDEX_MINUTE = 0.015

_mem = {}


def _parse(xml_bytes, site=ARPANSA_SITE):
    root = ET.fromstring(xml_bytes.decode("utf-8-sig"))
    for loc in root.findall("location"):
        if (loc.get("id") or "").strip().lower() != site.lower():
            continue
        if (loc.findtext("status") or "").strip().lower() != "ok":
            return None, f"ARPANSA {site} reports status {loc.findtext('status')!r}"
        return float(loc.findtext("index")), (loc.findtext("time") or "").strip()
    return None, f"ARPANSA feed has no station {site!r}"


def measured():
    """(index, stamp) from the live network, or (None, reason).

    Disk-cached so a dead network degrades to a stale reading rather than a guess, and so
    the demo does not hammer a government feed once per conditions poll.
    """
    now = time.time()
    c = _mem.get("arpansa")
    if c is None:
        try:
            c = json.load(open(CACHE))
            _mem["arpansa"] = c
        except Exception:
            c = None
    if c and now - c.get("ts", 0) < TTL:
        return c.get("index"), c.get("stamp")
    try:
        r = requests.get(ARPANSA_XML, timeout=8,
                         headers={"User-Agent": "ShadeMe/1.0 (+melbourne cool routes)"})
        r.raise_for_status()
        idx, stamp = _parse(r.content)
        if idx is None:
            raise ValueError(stamp)
        c = {"index": round(idx, 1), "stamp": stamp, "ts": now}
        os.makedirs(DATA, exist_ok=True)
        json.dump(c, open(CACHE, "w"))
        _mem["arpansa"] = c
        return c["index"], c["stamp"]
    except Exception as e:
        print(f"[uv] ARPANSA fetch failed ({e})")
        if c:                                  # stale measurement beats a model
            return c.get("index"), f"{c.get('stamp')} (stale)"
        return None, str(e)


def attenuate(uv_clear, cloud_pct):
    """Clear-sky index -> index under this much cloud. See CLOUD_A / CLOUD_P."""
    if uv_clear is None:
        return None
    c = max(0.0, min(1.0, float(cloud_pct) / 100.0))
    return max(0.0, float(uv_clear) * (1.0 - CLOUD_A * c ** CLOUD_P))


def index_for(hour, now_hour, uv_feed, cloud_pct, elev_deg=None):
    """(index, source) for one hour. Measurement where there is one, model elsewhere.

    ARPANSA publishes the CURRENT value only, so it can answer for the hour being walked
    and nothing else. Every request this API prices is the current hour, so in practice
    this returns the measurement; the modelled branch covers the clamped early and late
    hours and is labelled differently so the two can never be confused.
    """
    if int(hour) == int(now_hour):
        m, stamp = measured()
        if m is not None:
            return m, f"ARPANSA {ARPANSA_SITE} measured {stamp}"
    if uv_feed is not None:
        return round(attenuate(uv_feed, cloud_pct), 1), \
            f"open-meteo clear-sky x cloud ({cloud_pct:.0f}%)"
    if elev_deg is not None:
        return round(attenuate(clear_sky(elev_deg), cloud_pct), 1), \
            f"modelled clear-sky at {elev_deg:.0f} deg elevation x cloud ({cloud_pct:.0f}%)"
    return None, "no UV in the feed and no measurement"


# --- last resort: clear-sky UV from the sun's height --------------------------
# The standard single-parameter relation UVI = 12.5 sin(elevation)^2.42, calibrated for
# ~305 DU ozone at sea level. It exists for the ARCHIVE path only: Open-Meteo's archive
# endpoint carries no UV field, so a pinned January demo day would otherwise show none in
# the month UV matters most. Checked at elevation 41.4 deg: 4.5 here against the feed's
# 4.8 clear-sky, ~7% low, inside the ozone spread this ignores. Always labelled
# "modelled" and never preferred to the measurement or the feed.
CLEAR_C, CLEAR_P = 12.5, 2.42


def clear_sky(elev_deg):
    """Clear-sky UV index for a solar elevation in degrees. 0 below the horizon."""
    import math
    if elev_deg is None or elev_deg <= 0:
        return 0.0
    return CLEAR_C * math.sin(math.radians(float(elev_deg))) ** CLEAR_P


def split(direct_fraction):
    """(beam share, diffuse share) of erythemal UV. Sums to 1.

    `direct_fraction` is the BROADBAND beam share the weather block already computes, used
    as the sky-clarity signal and scaled down by the floor: under a clear sky the beam
    still carries only ~55% of the UV, and under total cloud there is no beam in either
    band. One variable drives both, so they cannot disagree.
    """
    f_dir = (1.0 - UV_DIFFUSE_FLOOR) * max(0.0, min(1.0, float(direct_fraction)))
    return f_dir, 1.0 - f_dir


def exposure(shade, svf, direct_fraction):
    """Fraction of the open-sky UV index reaching this edge, in [0,1]. Scalar or array."""
    f_dir, f_dif = split(direct_fraction)
    return f_dir * (1.0 - shade) + f_dif * svf


def sed(index_minutes):
    """UV index-minutes -> standard erythemal doses. ~2 SED reddens untanned fair skin."""
    return index_minutes * SED_PER_INDEX_MINUTE
