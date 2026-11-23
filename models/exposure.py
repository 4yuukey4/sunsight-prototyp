"""Turn horizon profiles + the sun-path histogram into sun and view metrics."""
import numpy as np

from config import (AZ_BINS, EL_BIN_DEG, MAX_RAY_M, SUN_YEAR, VIEW_FEATURE_MIN_M,
                    VIEW_FRONT_HALF_ANGLE, VIEW_WEIGHTS, WINTER_MONTHS)
from models.buildings import KIND_ROOF
from models.horizon import az_centers

DAYS_IN_MONTH = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], float)


def _rel_cos(naz):
    return np.cos(np.radians(az_centers()[None, :] - naz[:, None]))


def sky_view_factor(kind, naz, hor_deg):
    """Isotropic sky view factor: 1 for an open roof, 0.5 for an open wall."""
    h = np.radians(hor_deg)
    roof = np.mean(np.cos(h) ** 2, axis=1)
    c = _rel_cos(naz)
    inner = (np.pi / 2 - h) / 2 - np.sin(2 * h) / 4
    facade = np.sum(np.where(c > 0, c * inner, 0.0), axis=1) * (2 * np.pi / AZ_BINS) / np.pi
    return np.where(kind == KIND_ROOF, roof, facade)


def _top_cumsum(H):
    """[12, A, E] -> [12, A, E+1]; entry k = sum of bins with elevation index >= k."""
    C = np.zeros(H.shape[:2] + (H.shape[2] + 1,))
    C[:, :, :-1] = np.cumsum(H[:, :, ::-1], axis=2)[:, :, ::-1]
    return C


def solar_metrics(kind, naz, hor_deg, sunpath: dict, climate: dict, chunk: int = 4096) -> dict:
    n_el = sunpath["hours_clear"].shape[2]
    k = np.clip(np.ceil(hor_deg / EL_BIN_DEG).astype(np.int32), 0, n_el)
    C = {key: _top_cumsum(v) for key, v in sunpath.items()}
    is_roof = (kind == KIND_ROOF)[:, None]
    cosr = _rel_cos(naz)
    w_h = np.where(is_roof, 1.0, (cosr > 0).astype(float))
    w_e = np.where(is_roof, 1.0, np.clip(cosr, 0.0, None))
    a_idx = np.arange(AZ_BINS)[None, :]
    N = len(kind)
    sun_m = np.zeros((N, 12), np.float32)
    sun_clear = np.zeros(N, np.float32)
    pot_clear = np.zeros(N, np.float32)
    beam = np.zeros(N, np.float32)
    for s in range(0, N, chunk):
        e = min(N, s + chunk)
        kk, wh, we, roof = k[s:e], w_h[s:e], w_e[s:e], is_roof[s:e]
        sun_m[s:e] = (C["hours_wx"][:, a_idx, kk] * wh[None]).sum(-1).T
        sun_clear[s:e] = (C["hours_clear"][:, a_idx, kk] * wh[None]).sum(-1).sum(0)
        pot_clear[s:e] = (C["hours_clear"][:, :, 0].sum(0)[None, :] * wh).sum(-1)
        bh = (C["beam_h"][:, a_idx, kk] * we[None]).sum(-1).sum(0)
        bv = (C["beam_v"][:, a_idx, kk] * we[None]).sum(-1).sum(0)
        beam[s:e] = np.where(roof[:, 0], bh, bv)

    # Diffuse: measured monthly GHI minus our (weather-adjusted) beam on an open
    # horizontal plane, spread isotropically and scaled by the sky view factor.
    ghi_month = np.asarray(climate["monthly"]["ghi_kwh_m2_day"]) * DAYS_IN_MONTH
    beam_open = sunpath["beam_h"].sum(axis=(1, 2))
    diffuse_h = np.maximum(ghi_month - beam_open, 0.2 * ghi_month).sum()
    svf = sky_view_factor(kind, naz, hor_deg)
    sun_h = sun_m.sum(1)
    return {
        "sun_h": sun_h,
        "sun_h_clear": sun_clear,
        "winter_sun_h": sun_m[:, list(WINTER_MONTHS)].sum(1),
        "sun_rel": np.where(pot_clear > 0, sun_clear / np.maximum(pot_clear, 1e-9), 0.0),
        "irr": beam + svf * diffuse_h,
        "svf": svf,
        "sun_h_month": sun_m,
    }


def view_metrics(kind, naz, first_hit, green_len, water_len) -> dict:
    front = np.where((kind == KIND_ROOF)[:, None], True,
                     _rel_cos(naz) > np.cos(np.radians(VIEW_FRONT_HALF_ANGLE)))
    nf = np.maximum(front.sum(1), 1)

    def fmean(x):
        return np.where(front, x, 0.0).sum(1) / nf

    openness = fmean(np.log1p(first_hit) / np.log1p(MAX_RAY_M))
    green = fmean((green_len >= VIEW_FEATURE_MIN_M).astype(float))
    water = fmean((water_len >= VIEW_FEATURE_MIN_M).astype(float))
    w = VIEW_WEIGHTS
    return {
        "view_open": openness, "view_green": green, "view_water": water,
        "view_score": w["open"] * openness + w["green"] * green + w["water"] * water,
    }


def percentile_rank(x):
    x = np.asarray(x, float)
    out = np.full(len(x), np.nan)
    ok = ~np.isnan(x)
    if ok.sum() == 0:
        return out
    order = np.argsort(np.argsort(x[ok]))
    out[ok] = order / max(ok.sum() - 1, 1)
    return out
