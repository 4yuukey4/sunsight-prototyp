"""Analyse one z14 tile end to end and write it to data/tiles/z/x/y/.

    OSM (cached chunks) + climate  ->  buildings + 1 m DSM + land cover
    ->  surfaces & cells  ->  horizon march  ->  sun / view metrics  ->  disk

Tiles are independent (each carries a CONTEXT_BUFFER_M margin of shadow
casters), so a city, a country or the world is the same loop over more
tiles, parallel and resumable.
"""
import json
import struct
import time

import numpy as np
from shapely.geometry import box, mapping

from config import (CELL_SIZE, CONTEXT_BUFFER_M, DSM_RES, FACADE_CELL_HEIGHT, FACADE_OFFSET_M,
                    ROOF_OFFSET_M, TILE_VERSION)
from models import exposure, horizon, osm, raster, solar, weather
from models.buildings import KIND_FACADE, KIND_ROOF, make_buildings, make_cells, subset_cells
from models.geo import LocalFrame, expand_bbox
from models.tiles import tile_bounds, tile_dir, tile_key

# metrics stored per cell / served per slot ("overall" is derived client-side
# from region-wide percentiles, so the same tile can serve several regions)
CELL_METRICS = ["sun_h", "sun_h_clear", "winter_sun_h", "sun_rel", "irr", "svf",
                "view_score", "view_open", "view_green", "view_water"]
BUILDING_METRICS = CELL_METRICS + ["roof_sun_h", "roof_irr"]
EMPTY = -1e30  # "no cell in this slot" marker in value arrays
MESH_MAGIC = 0x53554E31  # "SUN1"


def is_done(z, x, y) -> bool:
    p = tile_dir(z, x, y) / "tile.json"
    if not p.exists():
        return False
    return json.loads(p.read_text(encoding="utf-8")).get("version") == TILE_VERSION


def climate_for(lat, lon):
    clim = weather.fetch_climate(lat, lon)
    # ERA5 sunshine_duration runs ~50% high in Sweden; use the inverse
    # Angstrom-Prescott fraction from irradiance, floored at half the ERA5 ratio
    # where Angstrom breaks down (very low sun, snow, polar winter).
    sf = np.maximum(solar.effective_sunshine_fraction(lat, lon, clim["monthly"]["ghi_kwh_m2_day"]),
                    0.5 * np.asarray(clim["monthly"]["sunshine_fraction"]))
    clim = {**clim, "monthly": {**clim["monthly"], "sunshine_fraction_eff": np.round(sf, 4).tolist()},
            "annual": dict(clim["annual"])}
    return clim, sf


def process_tile(z: int, x: int, y: int, cell_size: float = CELL_SIZE) -> dict:
    t0 = time.time()
    out = tile_dir(z, x, y)
    out.mkdir(parents=True, exist_ok=True)
    bounds = tile_bounds(z, x, y)
    w, s, e, n = bounds
    lat0, lon0 = (s + n) / 2, (w + e) / 2
    frame = LocalFrame(lat0, lon0)
    ctx = expand_bbox(bounds, CONTEXT_BUFFER_M)
    feats = osm.features_in_bbox(ctx)
    clim, sf = climate_for(lat0, lon0)

    cx0, cy0 = frame.to_xy(w, s)
    cx1, cy1 = frame.to_xy(e, n)
    core_poly = box(float(cx0), float(cy0), float(cx1), float(cy1))
    buildings = make_buildings(feats["building"], frame, core_poly)
    gx0, gy0 = frame.to_xy(ctx[0], ctx[1])
    gx1, gy1 = frame.to_xy(ctx[2], ctx[3])
    grid = raster.Grid.covering(float(gx0), float(gy0), float(gx1), float(gy1), DSM_RES)
    dsm = raster.rasterize([b.poly for b in buildings], [b.height for b in buildings], grid)
    green = [frame.geom_to_xy(f["geom"]) for f in feats["green"]]
    water = [frame.geom_to_xy(f["geom"]) for f in feats["water"]]
    lc = raster.rasterize(green, [raster.LC_GREEN] * len(green), grid, np.uint8, mode="set")
    lcw = raster.rasterize(water, [1] * len(water), grid, np.uint8, mode="set")
    lc[lcw > 0] = raster.LC_WATER
    lc[dsm > 0] = raster.LC_NONE
    dsm_pad, lc_pad = raster.padded(dsm), raster.padded(lc)

    cells, surfaces = make_cells(buildings, cell_size, FACADE_CELL_HEIGHT)
    rad = np.radians(cells["naz"].astype(float))
    nvec = np.column_stack([np.sin(rad), np.cos(rad)])
    is_f = cells["kind"] == KIND_FACADE
    # drop facade cells covered by an adjoining building (party walls)
    probe = cells["center"][:, :2] + nvec * 1.2
    neigh_h = raster.sample(dsm_pad, grid, probe[:, 0], probe[:, 1])
    keep = ~(is_f & (neigh_h >= cells["center"][:, 2]))
    cells = subset_cells(cells, keep)
    nvec, is_f = nvec[keep], is_f[keep]

    origin = ray_origins(cells)
    rays = horizon.march(origin[:, 0], origin[:, 1], origin[:, 2], dsm_pad, lc_pad, grid)
    sunpath = solar.sun_path_histogram(lat0, lon0, sf)
    clim["annual"]["sunshine_hours_eff"] = round(float(sunpath["hours_wx"].sum()), 1)
    sm = exposure.solar_metrics(cells["kind"], cells["naz"], rays["horizon"], sunpath, clim)
    vm = exposure.view_metrics(cells["kind"], cells["naz"], rays["first_hit"], rays["green_len"], rays["water_len"])
    metrics = {**{k: v for k, v in sm.items() if k != "sun_h_month"}, **vm}
    bstats = aggregate_buildings(buildings, cells, metrics, sm["sun_h_month"])

    n_slots = len(surfaces["slot_kind"])
    slot_cell = np.full(n_slots, -1, np.int32)
    slot_cell[cells["slot"]] = np.arange(len(cells["slot"]), dtype=np.int32)

    # ---- write ------------------------------------------------------------
    np.savez_compressed(out / "cells.npz", **cells, slot_cell=slot_cell,
                        horizon=np.clip(np.round(rays["horizon"] * 2), 0, 180).astype(np.uint8),
                        sun_h_month=sm["sun_h_month"].astype(np.float32),
                        **{f"m_{k}": np.asarray(metrics[k], np.float32) for k in CELL_METRICS})
    for k in CELL_METRICS:
        v = np.full(n_slots, EMPTY, np.float32)
        v[cells["slot"]] = metrics[k]
        (out / f"v_{k}.f32").write_bytes(v.tobytes())
    (out / "mesh.bin").write_bytes(encode_mesh(frame, surfaces, cell_size))
    np.savez_compressed(out / "raster.npz", dsm_dm=np.round(dsm * 10).astype(np.uint16), lc=lc,
                        grid=np.array([grid.x0, grid.y0, grid.res, grid.nx, grid.ny]))
    _write_buildings(out, frame, buildings, bstats)
    _write_landcover(out, core_poly, green, water)
    src = [b.height_source for b in buildings if b.core]
    meta = {
        "version": TILE_VERSION, "key": tile_key(z, x, y), "z": z, "x": x, "y": y,
        "bounds": list(bounds), "context_bbox": list(ctx), "frame": frame.to_dict(), "cell_size": cell_size,
        "timezone": clim["timezone"], "climate": clim,
        "counts": {"buildings": int(sum(b.core for b in buildings)), "context_buildings": len(buildings),
                   "cells": int(len(cells["kind"])), "slots": int(n_slots),
                   "roof_cells": int((cells["kind"] == KIND_ROOF).sum()),
                   "facade_cells": int((cells["kind"] == KIND_FACADE).sum())},
        "height_sources": {k: src.count(k) for k in ("height", "levels", "default")},
        "elapsed_s": round(time.time() - t0, 1),
    }
    (out / "tile.json").write_text(json.dumps(meta), encoding="utf-8")
    return {"key": meta["key"], **meta["counts"], "elapsed_s": meta["elapsed_s"]}


def ray_origins(cells):
    rad = np.radians(cells["naz"].astype(float))
    nvec = np.column_stack([np.sin(rad), np.cos(rad)])
    is_f = cells["kind"] == KIND_FACADE
    origin = np.array(cells["center"], dtype=np.float64, copy=True)
    origin[is_f, :2] += nvec[is_f] * FACADE_OFFSET_M
    origin[~is_f, 2] += ROOF_OFFSET_M
    return origin


def aggregate_buildings(buildings, cells, metrics, sun_month):
    nb = len(buildings)
    bid, area, kind = cells["bid"], cells["area"].astype(float), cells["kind"]

    def wmean(values, mask):
        wsum = np.bincount(bid[mask], area[mask], minlength=nb)
        vsum = np.bincount(bid[mask], area[mask] * values[mask], minlength=nb)
        return np.where(wsum > 0, vsum / np.maximum(wsum, 1e-9), np.nan)

    fac, roof = kind == KIND_FACADE, kind == KIND_ROOF
    st = {k: wmean(np.asarray(metrics[k], float), fac) for k in CELL_METRICS}
    st["roof_sun_h"] = wmean(np.asarray(metrics["sun_h"], float), roof)
    st["roof_irr"] = wmean(np.asarray(metrics["irr"], float), roof)
    st["sun_h_month"] = np.stack([wmean(sun_month[:, i].astype(float), fac) for i in range(12)], 1)
    st["facade_area"] = np.bincount(bid[fac], area[fac], minlength=nb)
    st["roof_area"] = np.bincount(bid[roof], area[roof], minlength=nb)
    st["n_cells"] = np.bincount(bid, minlength=nb)
    return st


def _r(v, nd=3):
    v = float(v)
    return None if np.isnan(v) else round(v, nd)


def _write_buildings(out, frame, buildings, st):
    rows = []
    for b in buildings:
        analysed = bool(b.core and st["n_cells"][b.bid] > 0)
        p = {"bid": b.bid, "osm_id": b.osm_id, "core": analysed, "h": round(b.height, 2),
             "height_source": b.height_source, "type": b.btype, "name": b.name, "address": b.address,
             "c": [round(b.poly.centroid.x, 2), round(b.poly.centroid.y, 2)],
             "rings": [np.round(np.asarray(r.coords)[:-1], 2).tolist() for r in [b.poly.exterior, *b.poly.interiors]]}
        if analysed:
            p["m"] = {k: _r(st[k][b.bid]) for k in BUILDING_METRICS}
            p["facade_area"] = _r(st["facade_area"][b.bid], 1)
            p["roof_area"] = _r(st["roof_area"][b.bid], 1)
            p["sun_h_month"] = [_r(v, 1) for v in st["sun_h_month"][b.bid]]
            p["lonlat"] = mapping(frame.geom_to_lonlat(b.poly))
        rows.append(p)
    (out / "buildings.json").write_text(json.dumps(rows), encoding="utf-8")


def _write_landcover(out, core_poly, green, water):
    feats = []
    for cls, geoms in (("green", green), ("water", water)):
        for g in geoms:
            g = g.intersection(core_poly)
            if g.is_empty:
                continue
            for p in getattr(g, "geoms", [g]):
                if p.geom_type == "Polygon" and p.area > 1.0:
                    feats.append({"cls": cls, "rings": [np.round(np.asarray(r.coords)[:-1], 1).tolist()
                                                        for r in [p.exterior, *p.interiors]]})
    (out / "landcover.json").write_text(json.dumps(feats), encoding="utf-8")


def encode_mesh(frame, surfaces, cell_size) -> bytes:
    """Binary surface description the browser turns into one mesh per tile.

    float64 lat0, lon0 | int32 magic, nWalls, nRoofs, nRoofFloats, nSlots, cell_mm
    float32 walls[nWalls*11]   bid, x0, y0, x1, y1, z0, h, ncols, nrows, slotBase, normalAz
    float32 roofs[nRoofs*11]   bid, h, ang, cx, cy, ox, oy, nx, ny, slotBase, nRings
    float32 roofCoords         per roof, per ring: nPts, x0, y0, x1, y1, ...
    uint8   slotKind[nSlots]   0 roof, 1 facade
    """
    walls = surfaces["walls"].astype(np.float32)
    rmeta, rcoords = [], []
    for r in surfaces["roofs"]:
        rmeta.append([r["bid"], r["h"], r["ang"], r["cx"], r["cy"], r["ox"], r["oy"], r["nx"], r["ny"],
                      r["base"], len(r["rings"])])
        for ring in r["rings"]:
            rcoords.append(len(ring))
            rcoords.extend(np.asarray(ring, np.float64).ravel().tolist())
    rmeta = np.asarray(rmeta, np.float32).reshape(-1, 11)
    rcoords = np.asarray(rcoords, np.float32)
    sk = surfaces["slot_kind"]
    head = struct.pack("<dd6i", frame.lat0, frame.lon0, MESH_MAGIC, len(walls), len(rmeta), len(rcoords),
                       len(sk), int(round(cell_size * 1000)))
    return head + walls.tobytes() + rmeta.tobytes() + rcoords.tobytes() + sk.tobytes()
