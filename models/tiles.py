"""XYZ (slippy-map) tile math. Tiles are the unit of processing, storage and loading."""
import math

from config import TILES_DIR


def lonlat_to_tile(lon: float, lat: float, z: int):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_bounds(z: int, x: int, y: int):
    """(west, south, east, north) in degrees."""
    n = 2 ** z

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return (x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y))


def tiles_covering(bbox, z: int):
    w, s, e, n = bbox
    x0, y0 = lonlat_to_tile(w, n, z)
    x1, y1 = lonlat_to_tile(e, s, z)
    return [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def tile_dir(z: int, x: int, y: int):
    return TILES_DIR / str(z) / str(x) / str(y)


def tile_key(z: int, x: int, y: int) -> str:
    return f"{z}-{x}-{y}"


def parse_key(key: str):
    z, x, y = (int(v) for v in key.split("-"))
    return z, x, y
