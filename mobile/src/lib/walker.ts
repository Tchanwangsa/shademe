import { Directory, File, Paths } from 'expo-file-system';

/**
 * Who is doing the walking, as two independent yes/no answers.
 *
 * `unacclimatised` is recent HISTORY -- has this body been in heat like this over the
 * past week or two? Acclimatisation takes 1-2 weeks of repeated exposure, so a visitor,
 * a recent arrival and a lifelong local in the season's first hot week are all equally
 * unadapted.
 *
 * `vulnerable` is CAPACITY -- 65+, pregnant, or a heart or kidney condition. It does not
 * expire and acclimatising does not cancel it, which is exactly why it is a second
 * question rather than a stronger setting of the first.
 *
 * NOT A DIAGNOSIS AND NOT A SEVERITY SCALE. One flag stands in for three quite different
 * physiologies because that is as fine as the number behind it can honestly be; see
 * `cost.k_multiplier` on the server for what the 1.8x per flag is and is not.
 */
export interface Walker {
  unacclimatised: boolean;
  vulnerable: boolean;
}

export const NO_WALKER: Walker = { unacclimatised: false, vulnerable: false };

/** True when nothing has been declared, so the request can leave the flags off entirely
 *  and the response can be read as the unpersonalised one. */
export const isDefault = (w: Walker) => !w.unacclimatised && !w.vulnerable;

/**
 * ON DEVICE, NOT ON THE SERVER -- the same rule `recents.ts` follows, and it matters more
 * here. These two answers are the closest thing to health information this app touches,
 * and the way to keep them safe is to have nowhere to leak them from: they are a JSON
 * file in the app's own document directory, they ride on the query string of a request
 * that is logged like any other, and `/routes` reads them and forgets them. There is no
 * account, no id, and nothing to migrate or authenticate.
 *
 * expo-file-system for the same reason recents uses it: it ships inside `expo`, so its
 * native side is already in the dev-client build.
 */
const FILE = 'shademe-walker.json';

function handle() {
  return new File(new Directory(Paths.document), FILE);
}

/**
 * The saved answers, or both false.
 *
 * NEVER THROWS, and an unreadable file reads as "nothing declared" rather than as an
 * error. The failure to avoid is the other direction -- a corrupt file must not silently
 * turn a declared flag OFF while the UI still shows it on, so the switches are rendered
 * from what this returns rather than from what was last written.
 */
export async function loadWalker(): Promise<Walker> {
  try {
    const f = handle();
    if (!f.exists) return NO_WALKER;
    const p = JSON.parse(await f.text());
    return {
      unacclimatised: p?.unacclimatised === true,
      vulnerable: p?.vulnerable === true,
    };
  } catch {
    return NO_WALKER;
  }
}

/** Write the answers. A device that cannot write still gets a working dial for this
 *  session; it just does not remember it next time, which is not worth an alert. */
export async function saveWalker(w: Walker): Promise<void> {
  try {
    const f = handle();
    if (!f.exists) f.create();
    f.write(JSON.stringify({ unacclimatised: w.unacclimatised, vulnerable: w.vulnerable }));
  } catch {}
}
