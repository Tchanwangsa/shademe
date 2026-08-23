"""Build every derived artefact the API needs, in dependency order.

    uv run python -m shademe.pipeline.build_all

One command, because the ten stages have a strict order and getting it wrong fails
several stages later with a FileNotFoundError that names a file but not the stage that
was meant to write it. Each stage is skipped when its outputs are already on disk, so a
re-run after a crash resumes rather than restarting; --force ignores that and rebuilds.

Each stage runs as a subprocess so its __main__ -- and its progress output -- is exactly
what running the module by hand gives you.
"""
import argparse, os, subprocess, sys, time

from .shade_legacy import HOURS
from ..paths import DATA, OUT

# (module, [outputs that mean "this stage is done"], one-line what-and-why).
# The markers are chosen to be unique to the stage: dsm and tree_heights both touch
# dsm_canopy.npy, and svf and canopy_svf both touch svf_veg.npy, so neither of those
# names can stand for a stage on its own.
STAGES = [
    ("shademe.pipeline.fetch",
     [f"{DATA}/buildings.geojson", f"{DATA}/canopy.geojson",
      f"{DATA}/canopy_cbd.geojson"],
     "CoM buildings + canopy, clipped to the CBD"),
    ("shademe.pipeline.fetch_osm",
     [f"{DATA}/osm_walk.json"],
     "walkable OSM network"),
    ("shademe.pipeline.dsm",
     [f"{OUT}/dsm_buildings.npy", f"{OUT}/grid.json"],
     "2 m building + canopy height rasters"),
    ("shademe.pipeline.tree_heights",
     [f"{OUT}/dsm_canopy_v2.npy", f"{OUT}/dsm_canopy_base_v2.npy"],
     "allometric crowns (top + trunk base)"),
    ("shademe.pipeline.shade_legacy",
     [f"{OUT}/shade_{h:02d}.npy" for h in HOURS] + [f"{OUT}/shade_means.json"],
     "legacy shade set (bench baseline)"),
    ("shademe.pipeline.shade",
     [f"{OUT}/v2/shade_means_v2.json"],
     "the shipped shade set -> out/v2"),
    ("shademe.physics.svf",
     [f"{OUT}/svf_bldg.npy", f"{OUT}/svf_all.npy"],
     "svf_bldg + svf_all"),
    ("shademe.physics.canopy_svf",
     [f"{OUT}/svf_canopy_block.npy", f"{OUT}/svf_veg.npy"],
     "svf_veg, the one the engine reads"),
    ("shademe.pipeline.materials",
     [f"{OUT}/material_id.npy", f"{OUT}/material_props.json"],
     "surface materials + thermal properties"),
    ("shademe.pipeline.graph",
     [f"{OUT}/graph.pkl"],
     "out/graph.pkl, what the API loads at startup"),
]


def missing(outputs):
    return [p for p in outputs if not os.path.exists(p) or os.path.getsize(p) == 0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="rebuild every stage even if its outputs exist")
    ap.add_argument("--only", metavar="NAME",
                    help="run just the stage whose module ends with NAME (e.g. shade)")
    args = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    stages = STAGES
    if args.only:
        stages = [s for s in STAGES if s[0].split(".")[-1] == args.only]
        if not stages:
            sys.exit(f"no stage named {args.only!r}; have: "
                     + ", ".join(s[0].split('.')[-1] for s in STAGES))

    t_all = time.time()
    for i, (mod, outputs, what) in enumerate(stages, 1):
        short = mod.split(".")[-1]
        gaps = missing(outputs)
        if not gaps and not args.force:
            print(f"[{i}/{len(stages)}] {short:<14} skip -- already built")
            continue
        print(f"\n[{i}/{len(stages)}] {short:<14} {what}")
        if not args.force:
            print(f"     missing {os.path.relpath(gaps[0])}"
                  + (f" (+{len(gaps)-1} more)" if len(gaps) > 1 else ""))
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", mod])
        if r.returncode != 0:
            sys.exit(f"\n{short} failed (exit {r.returncode}). Fix it and re-run "
                     f"build_all -- finished stages are skipped.")
        gaps = missing(outputs)
        if gaps:
            sys.exit(f"\n{short} exited 0 but did not write "
                     f"{os.path.relpath(gaps[0])}.")
        print(f"     {short} done in {time.time()-t0:.0f} s")

    print(f"\nall stages complete in {(time.time()-t_all)/60:.1f} min. Start the API:")
    print("  uv run uvicorn shademe.api.main:app --host 0.0.0.0 --port 8011")


if __name__ == "__main__":
    main()
