"""Checks for the OSM opening_hours reader. Run: python tests/test_osm_hours.py

(a) the strings Melbourne CBD places actually carry, read at real moments
(b) a day no rule mentions is CLOSED, not unknown -- QV Market on a Monday
(c) anything outside the implemented subset returns None, and NEVER False

(c) is the one that matters most. `open_now` drives a Closed badge in the picker, so a
grammar this cannot read has to come back "unknown" -- guessing False would mark open
places shut, which is worse than showing no badge at all.
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from shademe.api.osm_hours import is_open, describe

TZ = ZoneInfo("Australia/Melbourne")
fails = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
    if not ok:
        fails.append(name)


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def case(hours, when, want, why=""):
    got = is_open(hours, at(when))
    check(f"{str(hours)[:34]:36} @ {when[5:]} -> {str(got):5}",
          got is want, f"want {want}{'  ' + why if why else ''}")


print("(a) real tags from Nominatim, read at real moments")
MC = "Su-We 10:00-19:00; Th-Fr 10:00-21:00; Sa 10:00-19:00"
case(MC, "2026-08-23 14:00", True, "Sunday afternoon")
case(MC, "2026-08-23 19:16", False, "Sunday, just shut -- the 422 that started this")
case(MC, "2026-08-27 20:30", True, "Thursday trades to 21:00")
case("Mo-Su 10:00-18:00", "2026-08-23 11:00", True, "State Library")
case("Mo-Su 10:00-18:00", "2026-08-23 19:16", False, "State Library, after close")

print("\n(b) a day with no rule is shut, not unknown")
QV = "Tu 06:00-14:00; Th 06:00-14:00; Fr 06:00-18:00; Sa 06:00-15:00; Su 09:00-16:00"
case(QV, "2026-08-23 10:00", True, "Sunday, open")
case(QV, "2026-08-24 10:00", False, "Monday: QV Market really is closed")
case("Mo-Fr 09:00-17:00; We off", "2026-08-26 12:00", False, "explicit 'off' wins")
case("Mo-Fr 09:00-17:00; We off", "2026-08-25 12:00", True, "Tuesday unaffected")

print("\n(c) boundaries and shapes")
case("24/7", "2026-08-23 03:00", True)
case("Mo-Su 22:00-02:00", "2026-08-24 01:00", True, "crosses midnight")
case("Mo-Su 22:00-02:00", "2026-08-24 12:00", False)
case("Mo-Su 10:00-18:00", "2026-08-24 10:00", True, "open at the opening minute")
case("Mo-Su 10:00-18:00", "2026-08-24 18:00", False, "shut at the closing minute")
case("Tu,Th 06:00-14:00", "2026-08-25 07:00", True, "comma day list")

print("\n(d) unknown must never read as closed")
for bad in (None, "", "   ", "sunrise-sunset", "Mo-Fr 09:00+", "Jan-Mar Mo-Fr 09:00-17:00",
            "Mo-Fr 09:00-17:00 || bicycle", "week 1-3 Mo 09:00-17:00", "nonsense"):
    got = is_open(bad, at("2026-08-24 12:00"))
    check(f"unparseable {str(bad)[:30]:32} -> {got}", got is None,
          "must be None, never False")

print("\n(e) describe() hands main._place its kwargs")
d = describe(MC, at("2026-08-23 19:16"))
check("describe keys are hours/open_now", set(d) == {"hours", "open_now"}, str(set(d)))
check("describe reports closed", d["open_now"] is False)
check("describe carries the raw string", d["hours"] == MC)
d2 = describe(None, at("2026-08-23 19:16"))
check("no tag -> both null", d2["hours"] is None and d2["open_now"] is None)

print("\n" + "=" * 60)
print(f"FAILURES: {len(fails)} {fails if fails else ''}")
sys.exit(1 if fails else 0)
