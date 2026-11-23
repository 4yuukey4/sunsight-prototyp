"""Tiny raster helpers: a 1 m surface model (building heights) and land cover."""
import math
from dataclasses import dataclass

import numpy as np
import shapely

LC_NONE, LC_GREEN, LC_WATER = 0, 1, 2


@dataclass
class Grid:
    x0: float
    y0: float
    res: float
    nx: int
    ny: int

    @classmethod
    def covering(cls, xmin, ymin, xmax, ymax, res):
        return cls(xmin, ymin, res, int(math.ceil((xmax - xmin) / res)), int(math.ceil((ymax - ymin) / res)))


def rasterize(geoms, values, grid: Grid, dtype=np.float32, mode="max"):
    arr = np.zeros((grid.ny, grid.nx), dtype=dtype)
    for g, v in zip(geoms, values):
        minx, miny, maxx, maxy = g.bounds
        i0 = max(0, int((minx - grid.x0) / grid.res))
        i1 = min(grid.nx, int(math.ceil((maxx - grid.x0) / grid.res)) + 1)
        j0 = max(0, int((miny - grid.y0) / grid.res))
        j1 = min(grid.ny, int(math.ceil((maxy - grid.y0) / grid.res)) + 1)
        if i0 >= i1 or j0 >= j1:
            continue
        xs = grid.x0 + (np.arange(i0, i1) + 0.5) * grid.res
        ys = grid.y0 + (np.arange(j0, j1) + 0.5) * grid.res
        X, Y = np.meshgrid(xs, ys)
        shapely.prepare(g)
        m = shapely.contains_xy(g, X, Y)
        sub = arr[j0:j1, i0:i1]
        if mode == "max":
            sub[m] = np.maximum(sub[m], v)
        else:
            sub[m] = v
    return arr


def padded(arr):
    """Add a 1-pixel zero border so out-of-range lookups can be clipped onto it."""
    return np.pad(arr, 1)


def sample(arr_padded, grid: Grid, x, y):
    ix = np.clip(np.floor((x - grid.x0) / grid.res).astype(np.int32) + 1, 0, grid.nx + 1)
    iy = np.clip(np.floor((y - grid.y0) / grid.res).astype(np.int32) + 1, 0, grid.ny + 1)
    return arr_padded[iy, ix]
