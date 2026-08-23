import { Directory, File, Paths } from 'expo-file-system';

import type { Place } from './api';

/**
 * The places this device has actually picked, newest first.
 *
 * This is what replaced a hardcoded list of fifteen landmarks. That list was the same for
 * everybody, went stale whenever a venue moved, and told a returning user nothing they
 * did not already know. Somewhere you went yesterday is a better suggestion than a
 * landmark you have never chosen.
 *
 * ON DEVICE, NOT ON THE SERVER. The API holds no per-user state at all and this does not
 * change that -- it is a JSON file in the app's document directory, which is also why
 * there is nothing to migrate, expire or authenticate.
 *
 * expo-file-system rather than AsyncStorage deliberately: it already ships inside the
 * `expo` package, so its native side is in the existing dev-client build and adding this
 * needed no rebuild. AsyncStorage would have been a new native module.
 */
const FILE = 'shademe-recents.json';

/** Enough to be useful, few enough to fit above the keyboard without scrolling. */
const MAX = 8;

/** Two rows are the same place if they render the same and sit within ~1 m. */
const keyOf = (p: Place) => `${p.name}|${p.lat.toFixed(5)},${p.lon.toFixed(5)}`;

function handle() {
  return new File(new Directory(Paths.document), FILE);
}

/**
 * Newest first, or [] for anything unreadable.
 *
 * NEVER THROWS. A missing file is the normal first-run case, and a truncated or
 * hand-edited one is not worth taking the picker down for -- both mean "no recents yet".
 */
export async function loadRecents(): Promise<Place[]> {
  try {
    const f = handle();
    if (!f.exists) return [];
    const parsed = JSON.parse(await f.text());
    if (!Array.isArray(parsed)) return [];
    // Coordinates are what routing consumes, so a row without usable numbers is dropped
    // rather than handed to /routes to fail on.
    return parsed
      .filter(
        (p): p is Place =>
          !!p && typeof p.name === 'string' && Number.isFinite(p.lat) && Number.isFinite(p.lon),
      )
      .slice(0, MAX);
  } catch {
    return [];
  }
}

/**
 * Put `place` at the top and return the new list.
 *
 * `open_now` is deliberately NOT persisted, and recents carry NO open/closed badge. It is
 * true or false about one moment, and a badge saying "Open" that was written yesterday
 * evening is worse than no badge. Re-deriving it here would mean a second copy of the
 * OSM opening_hours parser living in the client, drifting from api/osm_hours.py -- one
 * rule evaluated in two places is exactly the bug that made closed venues unroutable in
 * the first place. Live search rows get their badge from the server; recents do not.
 */
export async function addRecent(place: Place): Promise<Place[]> {
  const stripped: Place = { ...place, open_now: null, distance_m: null };
  const next = [stripped, ...(await loadRecents()).filter((p) => keyOf(p) !== keyOf(stripped))].slice(
    0,
    MAX,
  );
  try {
    const f = handle();
    if (!f.exists) f.create();
    f.write(JSON.stringify(next));
  } catch {
    // A device with no writable document directory still gets a working picker; it just
    // does not remember. Not worth surfacing.
  }
  return next;
}

export async function clearRecents(): Promise<void> {
  try {
    const f = handle();
    if (f.exists) f.delete();
  } catch {}
}
