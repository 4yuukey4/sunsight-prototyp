"""Tile API: binary surface meshes and per-slot values (what the map draws),
plus per-building / per-cell detail for the panel, inspector and validation."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from models import store
from models.tile_pipeline import CELL_METRICS

router = APIRouter(prefix="/api/tiles", tags=["tiles"])
_BIN = "application/octet-stream"
_CACHE = {"Cache-Control": "public, max-age=3600"}


def _tile(key: str):
    try:
        return store.tile(key)
    except (KeyError, ValueError):
        raise HTTPException(404, f"unknown tile {key}")


@router.get("/{key}/mesh.bin")
def mesh(key: str):
    return FileResponse(_tile(key).dir / "mesh.bin", media_type=_BIN, headers=_CACHE)


@router.get("/{key}/values/{metric}.bin")
def values(key: str, metric: str):
    if metric not in CELL_METRICS:
        raise HTTPException(404, f"unknown metric {metric}")
    return FileResponse(_tile(key).dir / f"v_{metric}.f32", media_type=_BIN, headers=_CACHE)


@router.get("/{key}/buildings/{bid}")
def building(key: str, bid: int, region: str | None = None):
    t = _tile(key)
    if not 0 <= bid < len(t.buildings):
        raise HTTPException(404, "unknown building")
    return store.building_detail(key, bid, region)


@router.get("/{key}/slots/{slot}")
def slot(key: str, slot: int):
    t = _tile(key)
    ci = t.cell_of_slot(slot)
    if ci < 0:
        raise HTTPException(404, "no cell in this slot (party wall or outside the roof)")
    return t.cell_info(ci)


@router.get("/{key}/cells/{ci}/rays")
def rays(key: str, ci: int):
    t = _tile(key)
    if not 0 <= ci < len(t.cells["kind"]):
        raise HTTPException(404, "unknown cell")
    return t.rays(ci)


@router.get("/{key}/scene/{bid}")
def scene(key: str, bid: int, ctx_radius: float = Query(300, le=600), cell_radius: float = Query(90, le=250)):
    t = _tile(key)
    if not 0 <= bid < len(t.buildings) or not t.buildings[bid]["core"]:
        raise HTTPException(404, "not an analysed building")
    return store.scene(key, bid, ctx_radius, cell_radius)
