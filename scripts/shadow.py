"""Ratti-Richens shadow sweep over a DSM. Vectorised numpy, no polygon ops."""
import numpy as np
import pandas as pd
import pvlib


def sun_position(when, lat=-37.8136, lon=144.9631):
    """when: tz-aware pandas Timestamp. Returns (azimuth_deg, elevation_deg)."""
    sp = pvlib.solarposition.get_solarposition(pd.DatetimeIndex([when]), lat, lon)
    return float(sp["azimuth"].iloc[0]), float(sp["apparent_elevation"].iloc[0])


def _shift(a, dr, dc):
    """out[r,c] = a[r+dr, c+dc], zero-padded (no wraparound)."""
    H, W = a.shape
    out = np.zeros_like(a)
    r0, r1 = max(0, -dr), min(H, H - dr)
    c0, c1 = max(0, -dc), min(W, W - dc)
    if r0 < r1 and c0 < c1:
        out[r0:r1, c0:c1] = a[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
    return out


# Radial step along the ray, in cells. A full 1.0 lets a diagonal ray TUNNEL between two
# diagonally-adjacent wall cells and land on ground that is really in shadow: at 14:00 on
# the demo day the 1-cell walk misses 4.2 pp of shadow (255k cells wrongly sunlit). A
# quarter-cell step closes it at no measurable cost, because repeated integer offsets are
# deduped -- the extra iterations collapse onto shifts already computed.
RAY_STEP = 0.25


def shadow_mask(dsm, cell, az_deg, el_deg, max_h=None, step=RAY_STEP):
    """True where ground is shadowed. Grid: row 0 = north, col 0 = west."""
    if el_deg < 5.0:
        return np.ones(dsm.shape, dtype=bool)      # sun too low: all shadow
    az, el = np.radians(az_deg), np.radians(el_deg)
    tan_el = np.tan(el)
    max_h = float(dsm.max()) if max_h is None else max_h
    n = int(min(max_h / (tan_el * cell * step), 8000))

    acc = np.zeros(dsm.shape, dtype=np.float32)
    seen = set()
    for i in range(1, n + 1):
        k = i * step
        dr = int(round(-k * np.cos(az)))    # north is -row
        dc = int(round( k * np.sin(az)))    # east  is +col
        if (dr, dc) in seen:                # same cell offset as a previous sub-step
            continue
        seen.add((dr, dc))
        cand = _shift(dsm, dr, dc) - np.float32(k * cell * tan_el)
        np.maximum(acc, cand, out=acc)
    return acc > 0.05


def shade_factor(dsm_b, dsm_c, cell, az, el, canopy_block=0.7):
    """Float shade in [0,1]: buildings block fully, canopy dapples."""
    m_b = shadow_mask(dsm_b, cell, az, el)
    m_all = shadow_mask(np.maximum(dsm_b, dsm_c), cell, az, el)
    out = np.zeros(dsm_b.shape, dtype=np.float32)
    out[m_all] = canopy_block      # shadowed by anything
    out[m_b] = 1.0                 # building shadow overrides
    return out
