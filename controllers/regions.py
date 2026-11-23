"""Region API: list, metadata, city-wide building layer, sun path for a day."""
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from models import solar, store

router = APIRouter(prefix="/api/regions", tags=["regions"])


def _region(rid: str):
    try:
        return store.region(rid)
    except KeyError:
        raise HTTPException(404, f"unknown region {rid}")


@router.get("")
def list_regions():
    return store.list_regions()


@router.get("/{rid}")
def region_meta(rid: str):
    return _region(rid).meta


@router.get("/{rid}/buildings.geojson")
def buildings(rid: str):
    return Response(_region(rid).geojson, media_type="application/json")


@router.get("/{rid}/sunpath")
def sunpath(rid: str, day: date = Query(None, alias="date"), step: int = Query(5, ge=1, le=60)):
    m = _region(rid).meta
    lat, lon = (m["bbox"][1] + m["bbox"][3]) / 2, (m["bbox"][0] + m["bbox"][2]) / 2
    return solar.day_sun_path(lat, lon, day or date.today(), m["timezone"], step)
