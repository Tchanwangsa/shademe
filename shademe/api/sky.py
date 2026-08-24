"""Sky state from the radiation that was measured, not from a cloud percentage.

WHAT THIS REPLACES. The glyph over the map used to be chosen by a rule that fell back to
cloud cover whenever there was no beam to read:

    if direct + diffuse < 20:                    # dusk: no beam to read
        return "cloudy" if cloud_cover >= 60 else "sunny"

At 23:53 on 23 August that hit the fallback with 2% cloud and drew a SUN over Melbourne
at midnight. The rule cannot tell a clear night from a clear dawn, because cloud cover
carries no information about whether the sun exists.

WHAT IT DOES INSTEAD. Two questions, answered from two different things:

  * IS THE SUN UP? -- from the sun's position. Not cloud, not radiation. Below
    shadow.SUN_MIN_DEG there is no beam, so the glyph is `night`, and it is the SAME
    constant the shadow sweep uses to decide every cell is shaded. The icon and the
    router cannot disagree about whether there is sun to walk out of.
  * IS THE BEAM LANDING? -- from the beam TRANSMISSION, direct radiation over what a
    clear sky would deliver at that solar elevation. A ratio rather than a threshold in
    W/m2, because 100 W/m2 of beam is a heavily clouded noon and a perfectly clear 8am,
    and the old absolute cut-offs called the second one "partly cloudy".

Cloud cover survives in one place only, clearly labelled in `condition_source`: the hour
in which the sun is up by the wall clock but the feed row it came with covers a window in
which the sun was too low to deliver a readable beam. That is one edge hour a day.
"""
import math

# --- clear-sky reference ------------------------------------------------------------
# Haurwitz (1946), "Insolation in relation to cloudiness and cloud density", J. Meteor. 3:
#     GHI_clear = 1098 sin(h) exp(-0.059 / sin(h))
# One parameter, no turbidity, no ozone -- and it does not need any, because it is only
# ever used as a DENOMINATOR here. Checked against Open-Meteo's own clear hours (cloud
# cover <= 5%, sun above 20 deg) over two seasons of Melbourne archive:
#     summer 2025-12-01..2026-02-15   n=229   mean kt 1.010  sd 0.055
#     winter 2026-06-01..2026-08-20   n= 42   mean kt 0.946  sd 0.063
# i.e. it reproduces the feed's clear-sky global to a few percent in both seasons, which
# is far tighter than the 0.15/0.50 thresholds below care about.
HAURWITZ_C = 1098.0

# Beam-normal clear sky: Meinel & Meinel (1976), I = I0 * 0.7^(AM^0.678), with the
# Kasten-Young (1989) air mass so it does not blow up near the horizon. Projected onto
# the horizontal to match `direct_radiation`, which Open-Meteo already reports on the
# horizontal plane (the beam-normal variable is `direct_normal_irradiance`).
SOLAR_CONSTANT = 1367.0

# Beam transmission bands. `kb` is the fraction of the clear-sky beam that arrived, so
# these read directly: half the available beam is landing, or a sixth of it is.
KB_SUNNY = 0.50
KB_PARTLY = 0.15

# Below this much clear-sky beam the ratio is unreadable: the feed reports radiation in
# whole W/m2, so a denominator of a few W/m2 turns rounding into a sky state. Reached
# only when the sun is within ~25 minutes of the horizon somewhere in the row's window.
BEAM_FLOOR = 10.0                       # W/m2 on the horizontal

# The precipitation split, unchanged: rain overrides whatever the sky is doing.
RAIN_MM, DRIZZLE_MM = 0.5, 0.0


def air_mass(elev_deg):
    """Relative optical air mass. Kasten & Young (1989), Applied Optics 28, 4735."""
    e = float(elev_deg)
    return 1.0 / (math.sin(math.radians(e)) + 0.50572 * (e + 6.07995) ** -1.6364)


def ghi_clear(elev_deg):
    """Clear-sky GLOBAL horizontal irradiance, W/m2. 0 with the sun down."""
    if elev_deg is None or elev_deg <= 0:
        return 0.0
    s = math.sin(math.radians(float(elev_deg)))
    return HAURWITZ_C * s * math.exp(-0.059 / s)


def beam_clear(elev_deg):
    """Clear-sky DIRECT irradiance on the HORIZONTAL plane, W/m2. 0 with the sun down.

    Horizontal, not beam-normal, because that is the plane `direct_radiation` is on and
    the plane the shade rasters gate.
    """
    if elev_deg is None or elev_deg <= 0:
        return 0.0
    dni = SOLAR_CONSTANT * 0.7 ** (air_mass(elev_deg) ** 0.678)
    return dni * math.sin(math.radians(float(elev_deg)))


def beam_fraction(direct, elev_deg):
    """`direct` as a fraction of the clear-sky beam at this elevation, or None.

    None means the question is unanswerable rather than the answer being zero: with the
    sun on the horizon a clear sky delivers less beam than the feed's own resolution.
    """
    b = beam_clear(elev_deg)
    if b < BEAM_FLOOR:
        return None
    return max(0.0, float(direct)) / b


def condition(direct, diffuse, cloud_pct, precip_mm, elev_now, elev_row=None):
    """(code, why) for the sky glyph. See the module docstring for the two questions.

    elev_now  solar elevation at the moment being priced -- answers "is the sun up".
    elev_row  solar elevation over the window the RADIATION was averaged over, which is
              not the same instant (see weather.row_elevation). Defaults to elev_now.

    `why` is returned beside the code for the same reason `uv_source` is: a sky state
    that cannot be checked against what the person can see out the window is not worth
    showing. It names the quantity that decided, and its value.
    """
    p = 0.0 if precip_mm is None else float(precip_mm)
    if p >= RAIN_MM:
        return "rain", f"precipitation {p:.1f} mm"
    if p > DRIZZLE_MM:
        return "drizzle", f"precipitation {p:.1f} mm"

    from ..physics.shadow import SUN_MIN_DEG
    if elev_now is None:
        # pvlib missing. The one branch that has nothing better than cloud cover, and it
        # cannot even tell day from night -- say so rather than drawing a confident sun.
        return _by_cloud(cloud_pct), f"cloud cover {float(cloud_pct):.0f}% (no sun position)"
    if elev_now <= SUN_MIN_DEG:
        return "night", f"sun {elev_now:.1f} deg -- below SUN_MIN_DEG {SUN_MIN_DEG:.0f}"

    kb = beam_fraction(direct, elev_now if elev_row is None else elev_row)
    if kb is None:
        return (_by_cloud(cloud_pct),
                f"cloud cover {float(cloud_pct):.0f}% (sun too low to read the beam)")
    if kb >= KB_SUNNY:
        return "sunny", f"beam {kb:.2f} of clear-sky"
    if kb >= KB_PARTLY:
        return "partly_cloudy", f"beam {kb:.2f} of clear-sky"
    return "cloudy", f"beam {kb:.2f} of clear-sky"


def _by_cloud(cloud_pct):
    """The last-resort branch. Never reached with a readable beam; always labelled."""
    return "cloudy" if float(cloud_pct or 0.0) >= 60 else "partly_cloudy"
