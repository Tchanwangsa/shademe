"""Ratti-Richens shadow sweep over a DSM. Vectorised numpy, no polygon ops.

Buildings and trees are NOT the same solid. A building really is opaque from its
roofline to the pavement, so a height-field horizon test is exact for it. A tree is a
crown on a stick, and a height field has no way to say "empty below 3.4 m" -- taking
np.maximum(dsm_b, dsm_c) and ray-marching it extrudes every crown to the ground and
blocks the low morning and evening beam at knee height, in trees' favour.

So the two are marched separately: shadow_mask() for the opaque building height field,
canopy_mask() for a crown SLAB between dsm_canopy_base_v2 and dsm_canopy_v2. The slab
test is the same radial walk with two shifts instead of one -- the beam is intercepted
where it passes BELOW the crown top and ABOVE the crown base.

Passing a base raster of zeros reproduces the old crown-to-pavement behaviour exactly,
so the legacy model is representable as data rather than as a second code path.

RECEIVER HEIGHT. Both masks march from z = 0, i.e. they answer "is the GROUND in
shadow". That is the right receiver for the surface energy balance these rasters
primarily drive (scripts/surface_temp.py). mrt() then reuses the same raster to gate
the beam onto a body at 1.1 m, which is a small inconsistency inherited from the
building path and left alone here rather than silently changed.
"""
import numpy as np
import pandas as pd
import pvlib

from config import TAU_LEAF


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


def _beam_h(dr, dc, cell, tan_el):
    """Height of the beam, m, over the cell actually sampled at offset (dr, dc).

    The walk steps by a fractional number of cells k but can only SAMPLE the DSM at the
    rounded integer offset, and those two distances are not the same -- k is up to half a
    step away from hypot(dr, dc). Taking the beam height from k therefore evaluates the
    ray at one distance and the terrain at another, which biases every shadow long by up
    to RAY_STEP*cell*tan(el). Use the offset that was really sampled.
    """
    return np.float32(cell * np.hypot(dr, dc) * tan_el)


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
        cand = _shift(dsm, dr, dc) - _beam_h(dr, dc, cell, tan_el)
        np.maximum(acc, cand, out=acc)
    return acc > 0.05


def canopy_mask(top_c, base_c, cell, az_deg, el_deg, step=RAY_STEP):
    """True where a crown SLAB intercepts the beam. Same walk as shadow_mask().

    top_c, base_c  crown top and crown-base height rasters, m above ground, 0 = no
                   canopy (scripts/tree_heights.py writes both).

    At radial distance d along the sun azimuth the beam sits at height d*tan(el) above
    the receiving cell, so the crown there intercepts it iff

        base(d) <= d*tan(el) < top(d)

    which is two shifted comparisons per step instead of shadow_mask's running max.
    Where there is no canopy top == base == 0, the upper test fails and the cell is a
    miss, so bare ground costs nothing. With base_c all zeros the lower test is
    vacuously true and this degenerates EXACTLY to the horizon test on the crown tops
    -- i.e. the pre-slab behaviour.
    """
    out = np.zeros(top_c.shape, dtype=bool)
    if el_deg < 5.0:
        return out                          # sun below the mask floor; see shadow_mask
    az, el = np.radians(az_deg), np.radians(el_deg)
    tan_el = np.tan(el)
    n = int(min(float(top_c.max()) / (tan_el * cell * step), 8000))

    seen = set()
    for i in range(1, n + 1):
        k = i * step
        dr = int(round(-k * np.cos(az)))
        dc = int(round( k * np.sin(az)))
        if (dr, dc) in seen:
            continue
        seen.add((dr, dc))
        rh = _beam_h(dr, dc, cell, tan_el)  # beam height over the cell sampled
        hit = (_shift(top_c, dr, dc) - rh > 0.05) & (_shift(base_c, dr, dc) - rh <= 0.0)
        out |= hit
    return out


def shade_factor(dsm_b, dsm_c, dsm_c_base, cell, az, el, tau_leaf=TAU_LEAF):
    """Float shade in [0,1], 1 = fully shaded. Buildings are opaque, crowns transmit.

    dsm_c_base is REQUIRED and positional on purpose: it is the difference between a
    tree and a solid block, and the call sites should have to say which they mean.
    Pass np.zeros_like(dsm_c) to get the old crown-to-pavement model back.

    A crown-shadowed cell keeps `tau_leaf` of the beam and so blocks 1 - tau_leaf.
    That constant is SOLWEIG's published leaf-on transmissivity, shared with the SVF
    path via config.TAU_LEAF; it replaced a hand-picked 0.7 that came from no paper.
    """
    m_b = shadow_mask(dsm_b, cell, az, el)
    m_c = canopy_mask(dsm_c, dsm_c_base, cell, az, el)
    out = np.zeros(dsm_b.shape, dtype=np.float32)
    out[m_c] = 1.0 - tau_leaf      # crown intercepts, leaves transmit tau of the beam
    out[m_b] = 1.0                 # building shadow is opaque and overrides
    return out
