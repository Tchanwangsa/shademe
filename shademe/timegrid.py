"""The time grid everything is priced on: half-hour slots, as minutes since midnight.

ONE UNIT, EVERYWHERE. A slot is an int -- minutes since local midnight -- so 06:00 is
360, 06:30 is 390, 20:00 is 1200. Not a float hour (6.5 hashes equal to no filename and
survives a json round-trip as "6.5"), not a (h, m) pair, and not an index into a list
that every caller has to hold the same copy of.

THE WHOLE CLOCK, AND NO WINDOW. This was 06:00-20:00, justified as "exactly the set of
hours that can carry a beam somewhere in the year", and api.main clamped the wall clock
into it. The window was the wrong shape of answer to the right question. What actually
decides whether a slot has a raster is whether the sun is up ON THE DAY BEING PRICED,
which is `shadow.SUN_MIN_DEG` -- the same 5 deg the shadow sweep itself uses to give up
and call every cell shaded. Below it there is no beam to route around, the raster would
be the constant 1.0 in all 6.1 M cells, so pipeline.shade writes no file and
api.engine._shade_path reads the absence as full shade.

That gate is a STRICT SUBSET of the old window on every day of the year, so covering the
whole clock costs nothing and saves in every season -- measured at this latitude:

    day           sunlit half-hour slots    the old 06:00-20:00 window
    2026-06-21     17  (08:30..16:30)        29
    2026-08-24     20  (07:30..17:00)        29
    2026-01-26     27  (07:00..20:00)        29
    2026-12-21     28  (06:30..20:00)        29

A winter set is now smaller than the hourly set that preceded it. What the window cost
was not disk, it was correctness at the edges: 23:53 was priced on 20:00's sun, with an
8pm temperature, an 8pm sky glyph and an arcade gate that thought Melbourne Central was
open at midnight. There is no clamp here any more because there is nothing to clamp to.

WHY 30 MINUTES. At solar noon in January the solar azimuth swings 23.4 deg in half an
hour, so an hourly grid is up to 30 minutes stale at its worst point. Against a rebuilt
13:30 raster, nearest-hour sampling put 2.9% (Jan) to 3.8% (Aug) of cells on the wrong
side of a shadow edge; halving the step halves that. 15 minutes was measured too and
does not pay for itself: it quadruples the raster set for a further fraction of a
percent. The radiation series IS sampled at 15 minutes, because that costs no disk at
all -- see api.weather.

The march in physics.surface_temp derives its timestep from the spacing of the clock it
is handed, so moving to half-hour slots keeps dt at 300 s and does not change the
accumulation integral. The Ts numbers are comparable across that change; the shade ones
are not, because the rasters are new.

A Ts raster is 24.2 MB on the real grid and the march has to produce one for EVERY slot,
dark ones included -- the accumulation integral is the only part of the model with
memory, so the night is what the morning is warm from. 48 of them held at once is 1.16
GB, which is why api.engine.attach_tsurf streams them onto the edges instead of
accumulating: it is what makes this grid fit the box at all.
"""
STEP_MIN = 30

# Every slot of the day. The march clock and the priced grid are now the same list --
# they were two lists while the window existed, and the march always walked the full 24 h
# anyway to accumulate, so the window only ever decided what got emitted from it.
SLOTS = list(range(0, 24 * 60, STEP_MIN))
CLOCK = SLOTS                    # the old name for the march's clock; they are one grid

# 15 minutes, for the radiation series only. Open-Meteo's minutely_15 carries real
# sub-hourly structure in direct/diffuse radiation (a cloud crossing reads 267 -> 208 ->
# 503 W/m2 inside one hour that the hourly series flattens to 280, 210) but interpolates
# temperature_2m linearly from the hourly endpoints, so only radiation is taken from it.
RAD_STEP_MIN = 15


def hhmm(slot):
    """390 -> '0630'. The raster filename stem, so it sorts lexically in a directory."""
    return f"{int(slot) // 60:02d}{int(slot) % 60:02d}"


def label(slot):
    """390 -> '06:30'. For humans and for API responses."""
    return f"{int(slot) // 60:02d}:{int(slot) % 60:02d}"


def hour_of(slot):
    """The clock hour containing a slot. Use only where an hourly table is the input."""
    return int(slot) // 60


def is_hour(slot):
    """True when a slot lands on the hour, i.e. a legacy shade_HH.npy may serve it."""
    return int(slot) % 60 == 0


def of(when):
    """A datetime -> the minute-of-day it falls on. Not snapped."""
    return when.hour * 60 + when.minute


def snap(minute, step=STEP_MIN):
    """Round a minute-of-day to the NEAREST slot, wrapping at midnight.

    13:52 is closer to 14:00's sun than to 13:30's, so nearest and not floor. 23:50 wraps
    to 0 rather than running off the end of SLOTS -- which is the whole point of there
    being no window: 23:50 is a real time to ask for a walk.
    """
    return int(round(float(minute) / step) * step) % (24 * 60)


def nearest(slot, have):
    """The closest slot in `have`, for serving a request off a coarser set."""
    have = list(have)
    return min(have, key=lambda s: abs(int(s) - int(slot))) if have else None


def as_slot(t):
    """Accept a slot, an int hour, or 'HH:MM' and return a slot.

    An int below 24 is read as an HOUR, because every legacy caller and every bench
    script passes one. 6 -> 360, 360 -> 360. The ambiguity is real and it is now a
    genuine one -- minute-of-day 0..23 is 00:00-00:23, which IS priced now that the
    window is gone -- so pass a slot, or 'HH:MM', from anything new.
    """
    if isinstance(t, str):
        if ":" in t:
            h, m = t.split(":")[:2]
            return int(h) * 60 + int(m)
        t = float(t)
    t = int(t)
    return t * 60 if 0 <= t < 24 else t
