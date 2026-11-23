"""Per-cell horizon profiles and view rays over the 1 m surface model.

For each cell and each of AZ_BINS azimuths we march outwards and record
  * the horizon elevation angle (max obstruction angle) -> sun visibility,
    sky view factor, and live shadow tests for any time of year;
  * the first distance where a building rises above the cell (horizontal
    line of sight) -> view openness;
  * how much green / water ground the line of sight passes over.
"""
import numpy as np

from config import AZ_BINS, MAX_RAY_M
from models.raster import LC_GREEN, LC_WATER, Grid


def ray_steps(max_d: float = MAX_RAY_M):
    near = np.arange(1.0, 30.0, 1.0)
    far = np.geomspace(30.0, max_d, 70)
    return np.concatenate([near, far])


def az_centers():
    return (np.arange(AZ_BINS) + 0.5) * (360.0 / AZ_BINS)


def march(px, py, pz, dsm_pad, lc_pad, grid: Grid, chunk: int = 512, progress=None):
    steps = ray_steps()
    seg = np.diff(np.concatenate([[0.0], steps]))
    K = len(steps)
    az = np.radians(az_centers())
    sa, ca = np.sin(az), np.cos(az)
    N = len(px)
    hor = np.zeros((N, AZ_BINS), np.float32)
    first = np.full((N, AZ_BINS), MAX_RAY_M, np.float32)
    green = np.zeros((N, AZ_BINS), np.float32)
    water = np.zeros((N, AZ_BINS), np.float32)
    kidx = np.arange(K)
    for s in range(0, N, chunk):
        e = min(N, s + chunk)
        x = px[s:e, None, None] + steps[None, None, :] * sa[None, :, None]
        y = py[s:e, None, None] + steps[None, None, :] * ca[None, :, None]
        ix = np.clip(np.floor((x - grid.x0) / grid.res).astype(np.int32) + 1, 0, grid.nx + 1)
        iy = np.clip(np.floor((y - grid.y0) / grid.res).astype(np.int32) + 1, 0, grid.ny + 1)
        dz = dsm_pad[iy, ix] - pz[s:e, None, None].astype(np.float32)
        tan_el = np.max(dz / steps[None, None, :], axis=2)
        hor[s:e] = np.degrees(np.arctan(np.maximum(tan_el, 0.0)))
        blocked = dz > 0
        anyb = blocked.any(axis=2)
        fidx = np.argmax(blocked, axis=2)
        first[s:e] = np.where(anyb, steps[fidx], MAX_RAY_M)
        vis = np.where(anyb[..., None], kidx[None, None, :] < fidx[..., None], True)
        lc = lc_pad[iy, ix]
        green[s:e] = ((lc == LC_GREEN) & vis) @ seg
        water[s:e] = ((lc == LC_WATER) & vis) @ seg
        if progress:
            progress(e / N)
    return {"horizon": hor, "first_hit": first, "green_len": green, "water_len": water}


def ray_profile(px: float, py: float, pz: float, dsm_pad, lc_pad, grid: Grid) -> list[dict]:
    """What one cell "sees" along each azimuth, for validating the view metrics.

    Uses the same step sampling as march(), so the numbers match the stored
    metrics exactly. Per azimuth: horizon angle, first-obstruction distance and
    obstacle height, and runs of green / water ground before the obstruction.
    """
    steps = ray_steps()
    edges = np.concatenate([[0.0], steps])
    out = []
    for i, a in enumerate(np.radians(az_centers())):
        x = px + steps * np.sin(a)
        y = py + steps * np.cos(a)
        ix = np.clip(np.floor((x - grid.x0) / grid.res).astype(np.int32) + 1, 0, grid.nx + 1)
        iy = np.clip(np.floor((y - grid.y0) / grid.res).astype(np.int32) + 1, 0, grid.ny + 1)
        h = dsm_pad[iy, ix]
        dz = h - pz
        blocked = np.flatnonzero(dz > 0)
        first = int(blocked[0]) if len(blocked) else len(steps)
        lc = lc_pad[iy, ix][:first]
        runs = []
        k = 0
        while k < len(lc):
            if lc[k] in (LC_GREEN, LC_WATER):
                j = k
                while j + 1 < len(lc) and lc[j + 1] == lc[k]:
                    j += 1
                runs.append([round(float(edges[k]), 1), round(float(edges[j + 1]), 1),
                             "green" if lc[k] == LC_GREEN else "water"])
                k = j + 1
            else:
                k += 1
        out.append({
            "az": round(float(np.degrees(a)), 1),
            "horizon": round(float(np.degrees(np.arctan(max(float(np.max(dz / steps)), 0.0)))), 2),
            "first_hit": round(float(steps[first]), 1) if first < len(steps) else None,
            "hit_h": round(float(h[first]), 1) if first < len(steps) else None,
            "runs": runs,
        })
    return out
