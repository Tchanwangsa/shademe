/**
 * maplibre-gl v6 resolves its web-worker URL from `import.meta.url` of its own
 * bundle. Under Turbopack that points at a hashed chunk path where the worker
 * file does not exist, so the worker 404s, GeoJSON sources are never tiled and
 * no route lines render. We instead serve the worker from /public and point
 * maplibre at it with `setWorkerUrl` (see MapView.tsx).
 *
 * The worker imports ./maplibre-gl-shared.mjs relatively, so both files must
 * land in the same directory.
 */
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const from = join(root, "node_modules", "maplibre-gl", "dist");
const to = join(root, "public", "maplibre");

mkdirSync(to, { recursive: true });
for (const f of ["maplibre-gl-worker.mjs", "maplibre-gl-shared.mjs"]) {
  copyFileSync(join(from, f), join(to, f));
}
console.log("copied maplibre worker -> public/maplibre");
