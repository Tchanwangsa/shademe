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

# Which distance the beam height is evaluated at: "hypot" = the offset actually sampled
# (correct, shipped), "step" = the requested radial distance k (the pre-fix convention,
# which biases every shadow long by up to RAY_STEP*cell*tan(el)). A knob only so the
# ladder in scripts/bench_shade_ladder.py is data rather than a fork of this file.
BEAM = "hypot"


def _beam_h(dr, dc, k, cell, tan_el, beam=BEAM):
    """Height of the beam, m, over the cell actually sampled at offset (dr, dc).

    The walk steps by a fractional number of cells k but can only SAMPLE the DSM at the
    rounded integer offset, and those two distances are not the same -- k is up to half a
    step away from hypot(dr, dc). Taking the beam height from k therefore evaluates the
    ray at one distance and the terrain at another, which biases every shadow long by up
    to RAY_STEP*cell*tan(el). Use the offset that was really sampled.

    beam="step" is the pre-fix convention (height from k, the requested distance). It is
    kept as an ARGUMENT, not a second code path, so scripts/bench_shade_ladder.py can
    rebuild the older rungs of the ladder from these same lines. Nothing in the live
    pipeline passes it.
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
        rh = _beam_h(dr, dc, k, cell, tan_el, beam)   # beam height over the cell sampled
        hit = (_shift(top_c, dr, dc) - rh > 0.05) & (_shift(base_c, dr, dc) - rh <= 0.0)
        out |= hit
    return out


def shade_factor(dsm_b, dsm_c, dsm_c_base, cell, az, el, tau_leaf=TAU_LEAF,
                 step=RAY_STEP, beam=BEAM):
    """Float shade in [0,1], 1 = fully shaded. Buildings are opaque, crowns transmit.

    dsm_c_base is REQUIRED and positional on purpose: it is the difference between a
    tree and a solid block, and the call sites should have to say which they mean.
    Pass np.zeros_like(dsm_c) to get the old crown-to-pavement model back.

    A crown-shadowed cell keeps `tau_leaf` of the beam and so blocks 1 - tau_leaf.
    That constant is SOLWEIG's published leaf-on transmissivity, shared with the SVF
    path via config.TAU_LEAF; it replaced a hand-picked 0.7 that came from no paper.
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

    shadow_mask() and canopy_mask() march a whole raster with the receiver pinned at
    z = 0, which is the right question for the surface energy balance but the wrong one
    for anything you walk on top of. A 2.5D height field stores one number per cell, so
    a bridge deck IS the terrain there: the full-raster march asks "is the ground under
    the bridge shadowed", answers yes, and hands that back as the shade on the deck. At
    14:00 the 186 outdoor bridge ways in this graph read 0.803 mean shade against 0.251
    for ground-level footpath -- they are standing in their own shadow.

    Raising the receiver is one term. A blocker of height h at radial distance d
    intercepts iff h > z0 + d*tan(el), so this is shadow_mask's test with z0 added to
    the beam. Two consequences worth stating:

      - Self-exclusion is automatic when z0 comes from the DSM. The deck's own cells
        have h == z0, and h > z0 + (positive beam height) is false at every step, so the
        structure you are standing on can never shadow you. That is why build_graph
        reads z0 off dsm_b rather than off the level tag wherever it can.
      - Crowns are handled by the same slab test as canopy_mask, shifted by z0. A deck
        above the canopy sees no leaves; a low deck still does.

    Pointwise rather than per-raster because z0 differs per edge -- a raster per distinct
    deck height would be dozens of full marches to serve ~2.9 km of walkway. Vectorised
    over the points, so the cost is one gather per radial step, not one per point.

    rows, cols  int arrays, receiver cells (row 0 = north, col 0 = west)
    z0          float array, metres above the receiver's own ground cell
    """
    rows = np.asarray(rows, dtype=np.int64)
    cols = np.asarray(cols, dtype=np.int64)
    z0 = np.asarray(z0, dtype=np.float32)
    H, W = dsm_b.shape
    if el_deg < 5.0:
        return np.ones(rows.shape, dtype=np.float32)     # sun too low; see shadow_mask

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
        ok = (r >= 0) & (r < H) & (c >= 0) & (c < W)     # off-grid = open sky, not blocked
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
