"""Ratti-Richens shadow sweep over a DSM. Vectorised numpy, no polygon ops.

Buildings and trees are not the same solid. A building is opaque from roofline to
pavement, so a height-field horizon test is exact for it; a tree is a crown on a stick,
and np.maximum(dsm_b, dsm_c) would extrude every crown to the ground and block the low
beam at knee height. So shadow_mask() marches the opaque building field and
canopy_mask() marches a crown SLAB between the crown-base and crown-top rasters.
Passing a base raster of zeros reproduces the old crown-to-pavement behaviour exactly.

Both masks march from z = 0, i.e. "is the GROUND in shadow" -- the right receiver for
the surface energy balance these rasters drive. point_shade() raises the receiver for
anything you walk on top of.
"""
import numpy as np
import pandas as pd
import pvlib

from ..config import TAU_LEAF

# Radial step along the ray, in cells. A full 1.0 lets a diagonal ray tunnel between two
# diagonally-adjacent wall cells: at 14:00 it misses 4.2 pp of shadow. Repeated integer
# offsets are deduped, so a quarter-cell step costs nothing measurable.
RAY_STEP = 0.25

# Which distance the beam height is evaluated at: "hypot" = the offset actually sampled
# (shipped), "step" = the requested radial distance (biases every shadow long). A knob
# only so tools/bench_shade_ladder.py can rebuild the older rungs from these same lines.
BEAM = "hypot"


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


def _beam_h(dr, dc, k, cell, tan_el, beam=BEAM):
    """Height of the beam, m, over the cell actually sampled at offset (dr, dc).

    The walk steps by a fractional number of cells k but can only sample at the rounded
    integer offset, so taking the beam height from k evaluates the ray at one distance
    and the terrain at another. Use the offset that was really sampled.
    """
    d = np.hypot(dr, dc) if beam == "hypot" else k
    return np.float32(cell * d * tan_el)


def shadow_mask(dsm, cell, az_deg, el_deg, max_h=None, step=RAY_STEP, beam=BEAM):
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
        cand = _shift(dsm, dr, dc) - _beam_h(dr, dc, k, cell, tan_el, beam)
        np.maximum(acc, cand, out=acc)
    return acc > 0.05


def canopy_mask(top_c, base_c, cell, az_deg, el_deg, step=RAY_STEP, beam=BEAM):
    """True where a crown SLAB intercepts the beam. Same walk as shadow_mask().

    At radial distance d the beam sits at d*tan(el) above the receiving cell, so the
    crown there intercepts it iff base(d) <= d*tan(el) < top(d). With base_c all zeros
    the lower test is vacuous and this degenerates to a horizon test on the crown tops.
    """
    out = np.zeros(top_c.shape, dtype=bool)
    if el_deg < 5.0:
        return out
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
        rh = _beam_h(dr, dc, k, cell, tan_el, beam)
        hit = (_shift(top_c, dr, dc) - rh > 0.05) & (_shift(base_c, dr, dc) - rh <= 0.0)
        out |= hit
    return out


def shade_factor(dsm_b, dsm_c, dsm_c_base, cell, az, el, tau_leaf=TAU_LEAF,
                 step=RAY_STEP, beam=BEAM):
    """Float shade in [0,1], 1 = fully shaded. Buildings are opaque, crowns transmit.

    dsm_c_base is required and positional on purpose: it is the difference between a
    tree and a solid block. Pass np.zeros_like(dsm_c) for the old crown-to-pavement model.
    """
    m_b = shadow_mask(dsm_b, cell, az, el, step=step, beam=beam)
    m_c = canopy_mask(dsm_c, dsm_c_base, cell, az, el, step=step, beam=beam)
    out = np.zeros(dsm_b.shape, dtype=np.float32)
    out[m_c] = 1.0 - tau_leaf      # crown intercepts, leaves transmit tau of the beam
    out[m_b] = 1.0                 # building shadow is opaque and overrides
    return out


def point_shade(dsm_b, dsm_c, dsm_c_base, cell, az_deg, el_deg, rows, cols, z0,
                tau_leaf=TAU_LEAF, step=RAY_STEP, beam=BEAM):
    """Shade in [0,1] for POINTS sitting z0 metres above their own ground cell.

    A 2.5D height field stores one number per cell, so a bridge deck IS the terrain
    there and the full-raster march hands back the shadow the deck casts on the ground
    beneath it -- at 14:00 the outdoor bridge ways read 0.803 mean shade against 0.251
    for street-level footpath. Raising the receiver is one term: a blocker of height h at
    distance d intercepts iff h > z0 + d*tan(el). Self-shadowing is then impossible when
    z0 comes from the DSM, since the deck's own cells have h == z0.

    Pointwise because z0 differs per edge; vectorised over the points, so the cost is one
    gather per radial step rather than one march per distinct deck height.

    rows, cols  int arrays, receiver cells (row 0 = north, col 0 = west)
    z0          float array, metres above the receiver's own ground cell
    """
    rows = np.asarray(rows, dtype=np.int64)
    cols = np.asarray(cols, dtype=np.int64)
    z0 = np.asarray(z0, dtype=np.float32)
    H, W = dsm_b.shape
    if el_deg < 5.0:
        return np.ones(rows.shape, dtype=np.float32)

    az, el = np.radians(az_deg), np.radians(el_deg)
    tan_el = np.tan(el)
    max_h = max(float(dsm_b.max()), float(dsm_c.max()))
    n = int(min(max_h / (tan_el * cell * step), 8000))

    hit_b = np.zeros(rows.shape, dtype=bool)
    hit_c = np.zeros(rows.shape, dtype=bool)
    seen = set()
    for i in range(1, n + 1):
        k = i * step
        dr = int(round(-k * np.cos(az)))
        dc = int(round( k * np.sin(az)))
        if (dr, dc) in seen:
            continue
        seen.add((dr, dc))
        r, c = rows + dr, cols + dc
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)     # off-grid = open sky
        if not ok.any():
            continue
        rr, cc = np.clip(r, 0, H - 1), np.clip(c, 0, W - 1)
        rh = _beam_h(dr, dc, k, cell, tan_el, beam) + z0
        hit_b |= ok & (dsm_b[rr, cc] - rh > 0.05)
        hit_c |= ok & (dsm_c[rr, cc] - rh > 0.05) & (dsm_c_base[rr, cc] - rh <= 0.0)

    out = np.zeros(rows.shape, dtype=np.float32)
    out[hit_c] = 1.0 - tau_leaf
    out[hit_b] = 1.0
    return out
