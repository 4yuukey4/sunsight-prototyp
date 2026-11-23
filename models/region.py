"""Regions: named sets of tiles (a city now; a country or the world later).

build_region() makes sure every tile is processed (in parallel, resumable),
then aggregate_region() computes region-wide rankings and colour domains.
"""
import json
import multiprocessing as mp
import os
from datetime import datetime, timezone

import numpy as np

from config import (AZ_BINS, CELL_SIZE, CONTEXT_BUFFER_M, MAX_RAY_M, REGIONS_DIR, TILE_Z, VIEW_WEIGHTS)
from models import exposure, osm
from models.buildings import KIND_FACADE, KIND_ROOF
from models.geo import expand_bbox
from models.tile_pipeline import BUILDING_METRICS, CELL_METRICS, climate_for, is_done, process_tile
from models.tiles import tile_bounds, tile_dir, tile_key, tiles_covering


def _work(args):
    z, x, y = args
    try:
        return process_tile(z, x, y)
    except Exception as exc:  # keep the pool alive; report and move on
        import traceback
        return {"key": tile_key(z, x, y), "error": f"{exc}\n{traceback.format_exc()}"}


def build_region(rid: str, name: str, bbox, workers: int | None = None, force: bool = False, log=print):
    tiles = tiles_covering(bbox, TILE_Z)
    log(f"{name}: {len(tiles)} z{TILE_Z} tiles")

    # Network first, sequentially and politely: OSM chunks, then climate cells.
    chunks = osm.chunks_for_bbox(expand_bbox(bbox, CONTEXT_BUFFER_M + 50))
    for i, (cx, cy) in enumerate(chunks, 1):
        log(f"  OSM chunk {i}/{len(chunks)} ({cx},{cy})")
        osm.ensure_chunk(cx, cy)
    cells01 = {(round((b[1] + b[3]) / 2, 1), round((b[0] + b[2]) / 2, 1))
               for b in (tile_bounds(*t) for t in tiles)}
    for lat, lon in sorted(cells01):
        climate_for(lat, lon)
    log(f"  climate cells: {len(cells01)}")

    todo = [t for t in tiles if force or not is_done(*t)]
    log(f"  tiles to process: {len(todo)} (skipping {len(tiles) - len(todo)} done)")
    if todo:
        workers = workers or max(1, min(len(todo), (os.cpu_count() or 2) - 2))
        with mp.get_context("spawn").Pool(workers) as pool:
            for i, res in enumerate(pool.imap_unordered(_work, todo), 1):
                if "error" in res:
                    log(f"  [{i}/{len(todo)}] {res['key']} FAILED: {res['error'].splitlines()[0]}")
                else:
                    log(f"  [{i}/{len(todo)}] {res['key']}: {res['buildings']} buildings, "
                        f"{res['cells']:,} cells, {res['elapsed_s']} s")
    return aggregate_region(rid, name, bbox, tiles, log)


def _quantiles(x, q=101):
    x = x[~np.isnan(x)]
    return np.round(np.quantile(x, np.linspace(0, 1, q)), 4).tolist() if len(x) else None


def _domain(x):
    x = x[~np.isnan(x)]
    if not len(x):
        return None
    lo, hi = np.percentile(x, [2, 98])
    return [float(lo), float(hi if hi > lo else lo + 1e-6)]


def aggregate_region(rid, name, bbox, tiles, log=print):
    out = REGIONS_DIR / rid
    out.mkdir(parents=True, exist_ok=True)
    rows, keys, tile_metas = [], [], []
    cell_vals = {kd: {k: [] for k in CELL_METRICS} for kd in (KIND_ROOF, KIND_FACADE)}
    for t in tiles:
        d = tile_dir(*t)
        if not (d / "tile.json").exists():
            continue
        tm = json.loads((d / "tile.json").read_text(encoding="utf-8"))
        key = tm["key"]
        keys.append(key)
        tile_metas.append(tm)
        for b in json.loads((d / "buildings.json").read_text(encoding="utf-8")):
            if b["core"]:
                rows.append((key, b))
        if tm["counts"]["cells"]:
            z = np.load(d / "cells.npz")
            kind = z["kind"]
            for kd in (KIND_ROOF, KIND_FACADE):
                m = kind == kd
                for k in CELL_METRICS:
                    cell_vals[kd][k].append(z[f"m_{k}"][m])
    cv = {kd: {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in d_.items()} for kd, d_ in cell_vals.items()}

    bm = {k: np.array([np.nan if b["m"][k] is None else b["m"][k] for _, b in rows], float) for k in BUILDING_METRICS}
    pct = {k: exposure.percentile_rank(bm[k]) for k in ("sun_h", "view_score", "winter_sun_h")}
    overall = 0.5 * pct["sun_h"] + 0.5 * pct["view_score"]
    feats = []
    for i, (key, b) in enumerate(rows):
        props = {"id": f"{key}:{b['bid']}", "tile": key, "bid": b["bid"], "osm_id": b["osm_id"],
                 "height": b["h"], "height_source": b["height_source"], "type": b["type"],
                 "name": b["name"], "address": b["address"], **b["m"],
                 "pct_sun_h": _r(pct["sun_h"][i]), "pct_view_score": _r(pct["view_score"][i]),
                 "pct_winter_sun_h": _r(pct["winter_sun_h"][i]), "overall": _r(overall[i])}
        feats.append({"type": "Feature", "properties": props, "geometry": _round_geom(b["lonlat"])})
    (out / "buildings.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}),
                                           encoding="utf-8")

    kinds = {"roof": KIND_ROOF, "facade": KIND_FACADE}
    domains = {kn: {k: _domain(cv[kd][k]) for k in CELL_METRICS} for kn, kd in kinds.items()}
    domains["building"] = {k: _domain(bm[k]) for k in BUILDING_METRICS}
    domains["building"]["overall"] = [0.0, 1.0]
    for kn in kinds:
        domains[kn]["overall"] = [0.0, 1.0]
    cdf = {kn: {k: _quantiles(cv[kd][k]) for k in ("sun_h", "view_score")} for kn, kd in kinds.items()}

    hs = {k: sum(tm["height_sources"][k] for tm in tile_metas) for k in ("height", "levels", "default")}
    center = min(tile_metas, key=lambda tm: abs((tm["bounds"][1] + tm["bounds"][3]) / 2 - (bbox[1] + bbox[3]) / 2)
                 + abs((tm["bounds"][0] + tm["bounds"][2]) / 2 - (bbox[0] + bbox[2]) / 2))
    meta = {
        "id": rid, "name": name, "bbox": list(bbox), "tile_z": TILE_Z, "tiles": keys,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "timezone": center["timezone"], "climate": center["climate"],
        "params": {"cell_size": CELL_SIZE, "az_bins": AZ_BINS, "max_ray_m": MAX_RAY_M,
                   "context_buffer_m": CONTEXT_BUFFER_M, "view_weights": VIEW_WEIGHTS},
        "counts": {"tiles": len(keys), "buildings": len(rows),
                   "cells": int(sum(tm["counts"]["cells"] for tm in tile_metas)),
                   "slots": int(sum(tm["counts"]["slots"] for tm in tile_metas))},
        "height_sources": hs,
        "summary": {k: _r(np.nanmedian(bm[k])) if len(rows) else None for k in BUILDING_METRICS},
        "domains": domains, "cdf": cdf,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    log(f"  region {rid}: {meta['counts']}")
    return meta


def _r(v, nd=3):
    v = float(v)
    return None if np.isnan(v) else round(v, nd)


def _round_geom(g):
    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], 7), round(c[1], 7)]
    return {"type": g["type"], "coordinates": rnd(g["coordinates"])}
