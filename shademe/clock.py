"""What time the API thinks it is. ONE clock, and one place to pin it.

Everything the engine prices -- the weather row, the shade raster, the sun's position,
the opening-hours gate, the arrival times on the cards -- hangs off a single instant.
That instant is the wall clock in Australia/Melbourne, and this module is the only place
that reads it, so that pinning it for a demo is one change rather than six.

WHY A PIN EXISTS AT ALL. api.main says "real time, not a scrubber", and that stands for
the shipped product: an API with an `hour` parameter is an API whose numbers can be
dialled to their best hour, which is not evidence. A pinned clock is a different thing.
It moves the WHOLE WORLD to one instant -- the weather is that day's real archive, the
shadows are that day's real sun, the shops keep that day's real hours -- and it is a
process-wide setting the person launching the server chose, not a request parameter a
client can pass. Nothing can be cherry-picked per request, and the response says which
instant it was priced at either way (`conditions.clock`).

A demo on a late-August Monday is the reason. Melbourne in August tops out around 16 C
with UTCI inside the no-stress band, so every rung of the thermal ladder collapses to the
same walk and the product has nothing to show. 27 January 2026 reached 43.4 C at 16:00
under a clear sky, and that is a real archived day, not an invented one.

    shademe-api --date 2026-01-27 --time 16:00

TWO KNOBS, EITHER ALONE. The date decides which day is priced; the time decides where in
that day we stand. Pin one and the other stays real -- `--date` alone prices 27 January
at the actual current time of day, which is what the bench scripts have always done.

THE PIN IS FROZEN. `now()` returns the same instant for as long as the process runs, so a
demo that takes ten minutes does not drift into the next half-hour slot and re-march the
surface temperatures halfway through. Nothing here is a per-request cache; it is simply
that a pinned instant is a constant.

READ LAZILY, EVERY CALL. The environment is the storage, and it is read on each call
rather than snapshotted at import, because the CLI in api.main sets it AFTER this module
is imported. Anything that caches `os.environ.get("SHADEME_DATE")` at module level cannot
see a flag passed on the command line.
"""
import os, time

TZ = "Australia/Melbourne"

ENV_DATE = "SHADEME_DATE"      # YYYY-MM-DD, the day being priced
ENV_TIME = "SHADEME_TIME"      # HH:MM, where in that day the clock stands


def real_today():
    """The actual date, whatever the pin says. The one thing `is_today` may compare to."""
    return time.strftime("%Y-%m-%d", time.localtime())


def _env(name):
    v = (os.environ.get(name) or "").strip()
    return v or None


def parse_date(s):
    """'2026-01-27' -> '2026-01-27'. Anything else raises with the form spelled out.

    ISO ONLY, and deliberately: 27-01-26 is the day this was built for and is read as
    27 January 2026 by an Australian and as some other day by a date library. A demo
    priced on the wrong day looks exactly like a demo priced on the right one, so the
    ambiguous forms are refused rather than guessed at -- with the ISO spelling of what
    was probably meant, so the fix is a copy-paste.
    """
    s = str(s).strip()
    try:
        time.strptime(s, "%Y-%m-%d")
        return s
    except ValueError:
        pass
    hint = ""
    parts = s.replace("/", "-").split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts) and len(parts[0]) <= 2:
        d, m, y = parts
        y = y if len(y) == 4 else f"20{y[-2:]}"
        hint = f" Did you mean --date {y}-{int(m):02d}-{int(d):02d}?"
    raise ValueError(f"date must be YYYY-MM-DD, got {s!r}.{hint}")


def parse_time(s):
    """'16:00' / '1600' / '16' -> '16:00'. Minutes, not slots: `as_of` is the real
    instant asked for and api.main.now_slot() does the snapping to the half hour."""
    s = str(s).strip()
    if ":" in s:
        h, _, m = s.partition(":")
    elif s.isdigit() and len(s) == 4:
        h, m = s[:2], s[2:]
    else:
        h, m = s, "0"
    try:
        h, m = int(h), int(m)
    except ValueError:
        raise ValueError(f"time must be HH:MM, got {s!r}")
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"time must be HH:MM inside one day, got {s!r}")
    return f"{h:02d}:{m:02d}"


def pinned_date():
    """The pinned day, or None. Invalid values are refused loudly rather than ignored --
    a typo that silently prices today is the failure this whole module exists to avoid."""
    v = _env(ENV_DATE)
    return None if v is None else parse_date(v)


def pinned_time():
    v = _env(ENV_TIME)
    return None if v is None else parse_time(v)


def is_pinned():
    return _env(ENV_DATE) is not None or _env(ENV_TIME) is not None


def is_live():
    """May a LIVE measurement answer for the instant being priced?

    Only when nothing is pinned. ARPANSA publishes the current UV index and no other, so
    on a pinned January day the reading on the wire is this afternoon's late-winter 1.4 --
    a real measurement of the wrong day, which is worse than the modelled clear-sky value
    that at least belongs to the day being walked. See api.uv.index_for.
    """
    return not is_pinned()


def date():
    """The day being priced: the pin, or today."""
    return pinned_date() or real_today()


def now():
    """The instant being priced, as a tz-aware pandas Timestamp in TZ.

    Unpinned this is the wall clock. Pinned it is the pinned day and/or pinned time, with
    whichever half was not pinned taken from the wall clock.
    """
    import pandas as pd
    d, t = pinned_date(), pinned_time()
    if d is None and t is None:
        return pd.Timestamp.now(tz=TZ)
    wall = pd.Timestamp.now(tz=TZ)
    d = d or str(wall.date())
    t = t if t is not None else f"{wall.hour:02d}:{wall.minute:02d}"
    return pd.Timestamp(f"{d} {t}", tz=TZ)


def pin(day=None, at=None):
    """Set the pin from the CLI. Validates BEFORE writing, so a bad flag never half-lands.

    Returns the resolved (date, time) so the caller can print what it actually pinned.
    """
    day = None if day is None else parse_date(day)
    at = None if at is None else parse_time(at)
    if day is not None:
        os.environ[ENV_DATE] = day
    if at is not None:
        os.environ[ENV_TIME] = at
    return pinned_date(), pinned_time()


def unpin():
    """Back to the wall clock. For tests, which must not leak a pin into each other."""
    os.environ.pop(ENV_DATE, None)
    os.environ.pop(ENV_TIME, None)


def describe():
    """What rode on every response, so a figure can be read against the clock that made
    it. Same contract as `meta.provenance`: no number without its config."""
    return {
        "pinned": is_pinned(),
        "date": date(),
        "date_pinned": pinned_date() is not None,
        "time": pinned_time(),
        "real_today": real_today(),
        "source": ("wall clock" if not is_pinned() else
                   " + ".join(filter(None, [
                       f"{ENV_DATE}={pinned_date()}" if pinned_date() else None,
                       f"{ENV_TIME}={pinned_time()}" if pinned_time() else None]))),
    }
