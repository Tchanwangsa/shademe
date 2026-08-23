"""The time grid everything is priced on: half-hour slots, as minutes since midnight.

ONE UNIT, EVERYWHERE. A slot is an int -- minutes since local midnight -- so 06:00 is
360, 06:30 is 390, 20:00 is 1200. Not a float hour (6.5 hashes equal to no filename and
survives a json round-trip as "6.5"), not a (h, m) pair, and not an index into a list
that every caller has to hold the same copy of.

WHY 06:00-20:00 AND NOT WIDER. Measured over all 8760 hours of 2026 at -37.8136,
144.9631: the sun's apparent elevation never exceeds -9.20 deg at 05:00 or -3.26 deg at
21:00, and it is above the horizon at some point of the year in every hour from 06 to 20.
So this window is exactly the set of hours that can carry a beam, not a cautious guess.
Outside it there is no direct radiation to route around and api.main zeroes the beam
rather than borrowing the last daylight hour's.

WHY 30 MINUTES. At solar noon in January the solar azimuth swings 23.4 deg in half an
hour, so an hourly grid is up to 30 minutes stale at its worst point. Against a rebuilt
13:30 raster, nearest-hour sampling put 2.9% (Jan) to 3.8% (Aug) of cells on the wrong
side of a shadow edge; halving the step halves that. 15 minutes was measured too and
does not pay for itself: it quadruples the raster set to 1.3 GB per day for a further
fraction of a percent. The radiation series IS sampled at 15 minutes, because that costs
no disk at all -- see api.weather.

The march in physics.surface_temp derives its timestep from the spacing of the clock it
is handed, so moving to half-hour slots keeps dt at 300 s and does not change the
accumulation integral. The Ts numbers are comparable across this change; the shade ones
are not, because the rasters are new.
"""
STEP_MIN = 30
FIRST_MIN, LAST_MIN = 6 * 60, 20 * 60        # 06:00, 20:00 -- see the docstring
SLOTS = list(range(FIRST_MIN, LAST_MIN + 1, STEP_MIN))

# The full 24 h clock the energy-balance march walks, at the same step. The march needs
# the whole diurnal cycle to accumulate; SLOTS is only what gets emitted from it.
CLOCK = list(range(0, 24 * 60, STEP_MIN))

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
    """A datetime -> the minute-of-day it falls on. Not snapped, not clamped."""
    return when.hour * 60 + when.minute


def snap(minute, step=STEP_MIN):
    """Round a minute-of-day to the NEAREST slot. Unclamped: 1290 -> 1290 (21:30)."""
    return int(round(float(minute) / step) * step)


def clamp(minute):
    """Hold a minute-of-day inside the daylight window."""
    return max(FIRST_MIN, min(LAST_MIN, int(minute)))


def in_window(minute):
    """Is there any sun to route around at this minute-of-day? See the docstring."""
    return FIRST_MIN <= int(minute) <= LAST_MIN


def nearest(slot, have):
    """The closest slot in `have`, for serving a request off a coarser set."""
    have = list(have)
    return min(have, key=lambda s: abs(int(s) - int(slot))) if have else None


def as_slot(t):
    """Accept a slot, an int hour, or 'HH:MM' and return a slot.

    An int below 24 is read as an HOUR, because every legacy caller and every bench
    script passes one. 6 -> 360, 360 -> 360. The ambiguity is real but it is bounded:
    minute-of-day 0..23 is 00:00-00:23, which is outside the window and never priced.
    """
    if isinstance(t, str):
        if ":" in t:
            h, m = t.split(":")[:2]
            return int(h) * 60 + int(m)
        t = float(t)
    t = int(t)
    return t * 60 if 0 <= t < 24 else t
