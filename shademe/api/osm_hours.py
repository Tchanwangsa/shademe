"""Is this OSM place open right now?

Reads the `opening_hours` tag OpenStreetMap carries on a POI. That tag has a full grammar
(<https://wiki.openstreetmap.org/wiki/Key:opening_hours>) with holidays, week numbers,
sunset offsets and comments; this parses the SUBSET that Melbourne CBD places actually
use, measured against Nominatim:

    Melbourne Central   Su-We 10:00-19:00; Th-Fr 10:00-21:00; Sa 10:00-19:00
    State Library       Mo-Su 10:00-18:00
    QV Market           Tu 06:00-14:00; Th 06:00-14:00; Fr 06:00-18:00; ...
    Degraves Street     (no tag -- a street has no hours, and that is not a gap)

THE UNPARSEABLE CASE RETURNS None, NEVER False. "We could not read the hours" and "it is
shut" are different answers, and only one of them should put a Closed badge on a place
that is actually open. Everything this does not understand -- `sunrise-sunset`, `week 1-3`,
holiday rules, open-ended `Mo-Fr 09:00+` -- falls through to unknown rather than guessing.

This is a DIFFERENT MECHANISM to data/indoor_hours.json, which gates the walking graph's
indoor edges by editorial estimate. This one is OSM's own claim about one POI, is only
ever shown to the user, and never removes an edge or changes a route.
"""
import re

# Monday-based, matching datetime.weekday().
DAYS = {"mo": 0, "tu": 1, "we": 2, "th": 3, "fr": 4, "sa": 5, "su": 6}

# A rule's day part: Mo-Fr, Sa, Mo,We,Fr, Su-We. Anything else (PH, SH, week 2, Jan-Mar)
# is not a weekday selector we understand.
_DAY_TOKEN = re.compile(r"^(mo|tu|we|th|fr|sa|su)(?:-(mo|tu|we|th|fr|sa|su))?$")
_TIME_SPAN = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


class Unparseable(Exception):
    """The rule uses a part of the grammar this does not implement."""


def _days(spec):
    """"Su-We" -> {6,0,1,2}. Ranges wrap through Sunday, which is why Melbourne Central
    (Su-We) needs the modulo rather than a plain range()."""
    out = set()
    for tok in spec.split(","):
        m = _DAY_TOKEN.match(tok.strip())
        if not m:
            raise Unparseable(tok)
        a = DAYS[m.group(1)]
        b = DAYS[m.group(2)] if m.group(2) else a
        n = (b - a) % 7
        out.update((a + i) % 7 for i in range(n + 1))
    return out


def _spans(spec):
    """"10:00-19:00,20:00-22:00" -> [(600, 1140), (1200, 1320)] in minutes from midnight.

    A span ending at or before it starts crosses midnight (`22:00-02:00`) and is split in
    two, so the caller only ever compares against same-day minute ranges.
    """
    out = []
    for tok in spec.split(","):
        m = _TIME_SPAN.match(tok.strip())
        if not m:
            raise Unparseable(tok)
        h1, m1, h2, m2 = (int(x) for x in m.groups())
        a, b = h1 * 60 + m1, h2 * 60 + m2
        if b == a:
            raise Unparseable(tok)
        out.append((a, b) if b > a else (a, 24 * 60))
        if b < a:
            out.append((0, b))      # the part that lands after midnight
    return out


def _rules(text):
    """[(days, spans_or_None)] -- spans None means the rule explicitly CLOSES those days."""
    rules = []
    for raw in text.split(";"):
        r = raw.strip().lower()
        if not r:
            continue
        # Comments in quotes carry no machine meaning, and "off"/"closed" is a closure.
        r = re.sub(r'"[^"]*"', " ", r).strip()
        if not r:
            continue
        closed = bool(re.search(r"\b(off|closed)\b", r))
        r = re.sub(r"\b(off|closed)\b", " ", r).strip()
        if r and re.match(r"^(mo|tu|we|th|fr|sa|su)[-,]", r + ","):
            parts = r.split(None, 1)
            days, rest = _days(parts[0]), (parts[1] if len(parts) > 1 else "")
        elif r and _DAY_TOKEN.match(r.split()[0]):
            parts = r.split(None, 1)
            days, rest = _days(parts[0]), (parts[1] if len(parts) > 1 else "")
        else:
            days, rest = set(range(7)), r      # no day selector: every day
        if closed and not rest:
            rules.append((days, None))
            continue
        if not rest:
            raise Unparseable(raw)
        rules.append((days, _spans(rest)))
    if not rules:
        raise Unparseable(text)
    return rules


def is_open(text, when):
    """True / False / None for an OSM `opening_hours` string at aware datetime `when`.

    None means "not known" -- no tag, or a grammar this does not implement. Later rules
    win over earlier ones for the same day, which is what the spec says and what makes
    `Mo-Fr 09:00-17:00; We off` read correctly.
    """
    if not text or not isinstance(text, str):
        return None
    t = text.strip().lower()
    if not t:
        return None
    if t in ("24/7", "mo-su 00:00-24:00", "00:00-24:00"):
        return True
    try:
        rules = _rules(t)
    except Unparseable:
        return None
    dow, mins = when.weekday(), when.hour * 60 + when.minute
    state = None
    for days, spans in rules:
        if dow not in days:
            continue
        state = False if spans is None else any(a <= mins < b for a, b in spans)
    # Parsed cleanly, but no rule names today: in this grammar a day nothing mentions is
    # SHUT, not unknown. Queen Victoria Market lists Tu/Th/Fr/Sa/Su and really is closed
    # on a Monday -- reporting that as "hours unknown" would hide a true closure.
    return False if state is None else state


def describe(text, when):
    """The `hours` / `open_now` kwargs main._place takes, ready to splat.

    The raw string rides along so the client can show the hours themselves; a place with
    no tag gets both fields null and the UI shows no badge at all.
    """
    return {"open_now": is_open(text, when),
            "hours": text if isinstance(text, str) and text.strip() else None}
