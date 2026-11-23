"""Precompute regions: process every z14 tile they cover, then aggregate.

    python tools/precompute.py vaxjo                 # a preset (see config.REGION_PRESETS)
    python tools/precompute.py --all
    python tools/precompute.py my-area --name "My area" --bbox 14.80,56.87,14.81,56.88
    python tools/precompute.py vaxjo --force --workers 12

Tiles are shared between regions and skipped when already processed with the
current TILE_VERSION, so re-runs and overlapping regions are cheap.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import REGION_PRESETS  # noqa: E402
from models.region import build_region  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("region", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--name")
    ap.add_argument("--bbox", help="west,south,east,north")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--force", action="store_true", help="reprocess tiles even if up to date")
    a = ap.parse_args()
    ids = list(REGION_PRESETS) if a.all else [a.region]
    if ids == [None]:
        ap.error("give a region id or --all")
    for rid in ids:
        if a.bbox:
            bbox, name = tuple(float(v) for v in a.bbox.split(",")), a.name or rid
        elif rid in REGION_PRESETS:
            bbox, name = REGION_PRESETS[rid]["bbox"], REGION_PRESETS[rid]["name"]
        else:
            ap.error(f"unknown preset {rid!r}; pass --bbox")
        t0 = time.time()
        build_region(rid, name, bbox, workers=a.workers, force=a.force, log=lambda m: print(m, flush=True))
        print(f"== {rid} done in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
