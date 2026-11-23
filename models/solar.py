"""Sun position and the annual sun-path histogram.

Sun position uses the NOAA/Astronomical Almanac low-precision formulas
(~0.01 deg), computed in UTC from latitude/longitude, so daylight is correct
anywhere on Earth including polar day/night. Local time zones only matter
for *displaying* times and are handled with zoneinfo.

The key trick for annual analysis: instead of shadow-testing every cell
against ~26k sun positions, we bin the year's sun positions into an
(month, azimuth, elevation) histogram once per region. A cell's horizon
profile then says which elevation bins it can see per azimuth, and a
reversed cumulative sum turns that into sun hours / irradiation with one
gather per cell.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from config import AZ_BINS, EL_BIN_DEG, SUN_STEP_MIN, SUN_YEAR

SOLAR_CONSTANT = 1361.0


def sun_position(t_utc: np.ndarray, lat: float, lon: float):
    """t_utc: datetime64 array (UTC). Returns (azimuth deg from N clockwise, elevation deg)."""
    t = np.asarray(t_utc, dtype="datetime64[s]").astype(np.float64)
    n = t / 86400.0 + 2440587.5 - 2451545.0
    L = np.mod(280.460 + 0.9856474 * n, 360.0)
    g = np.radians(np.mod(357.528 + 0.9856003 * n, 360.0))
    lam = np.radians(L + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g))
    eps = np.radians(23.439 - 4e-7 * n)
    ra = np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))
    dec = np.arcsin(np.sin(eps) * np.sin(lam))
    gmst_h = np.mod(18.697374558 + 24.06570982441908 * n, 24.0)
    ha = np.radians(gmst_h * 15.0 + lon) - ra
    phi = np.radians(lat)
    el = np.arcsin(np.sin(phi) * np.sin(dec) + np.cos(phi) * np.cos(dec) * np.cos(ha))
    az = np.arctan2(-np.sin(ha), np.tan(dec) * np.cos(phi) - np.sin(phi) * np.cos(ha))
    el_deg = np.degrees(el)
    # atmospheric refraction (Bennett), relevant for low sun at high latitudes
    refr = 1.02 / np.tan(np.radians(el_deg + 10.3 / (el_deg + 5.11))) / 60.0
    el_deg = np.where(el_deg > -1.0, el_deg + refr, el_deg)
    return np.mod(np.degrees(az), 360.0), el_deg


def clear_sky_dni(el_deg):
    """Meinel clear-sky direct normal irradiance (W/m2) with Kasten-Young air mass."""
    el = np.clip(el_deg, 0.01, 90.0)
    am = 1.0 / (np.sin(np.radians(el)) + 0.50572 * (el + 6.07995) ** -1.6364)
    return np.where(el_deg > 0, SOLAR_CONSTANT * 0.7 ** (am ** 0.678), 0.0)


def year_times(year: int = SUN_YEAR, step_min: int = SUN_STEP_MIN):
    return np.arange(np.datetime64(f"{year}-01-01T00:00"), np.datetime64(f"{year + 1}-01-01T00:00"),
                     np.timedelta64(step_min, "m"))


def sun_path_histogram(lat: float, lon: float, sunshine_fraction) -> dict:
    """Histograms indexed [month, az_bin, el_bin].

    hours_clear  geometric sun hours (sun above horizon, no clouds)
    hours_wx     weather-adjusted sun hours (x monthly sunshine fraction)
    beam_h       weather-adjusted beam irradiation on a horizontal plane, kWh/m2
    beam_v       weather-adjusted DNI*cos(el), kWh/m2 (vertical plane facing the sun's azimuth)
    """
    t = year_times()
    az, el = sun_position(t, lat, lon)
    up = el > 0
    t, az, el = t[up], az[up], el[up]
    month = t.astype("datetime64[M]").astype(int) % 12
    a = (az / (360.0 / AZ_BINS)).astype(int) % AZ_BINS
    n_el = int(round(90.0 / EL_BIN_DEG))
    e = np.clip((el / EL_BIN_DEG).astype(int), 0, n_el - 1)
    dt_h = SUN_STEP_MIN / 60.0
    sf = np.asarray(sunshine_fraction, dtype=float)[month]
    dni = clear_sky_dni(el)
    shape = (12, AZ_BINS, n_el)
    out = {k: np.zeros(shape) for k in ("hours_clear", "hours_wx", "beam_h", "beam_v")}
    idx = (month, a, e)
    np.add.at(out["hours_clear"], idx, dt_h)
    np.add.at(out["hours_wx"], idx, dt_h * sf)
    np.add.at(out["beam_h"], idx, dni * np.sin(np.radians(el)) * dt_h / 1000.0 * sf)
    np.add.at(out["beam_v"], idx, dni * np.cos(np.radians(el)) * dt_h / 1000.0 * sf)
    return out


def extraterrestrial_monthly(lat: float, lon: float):
    """Top-of-atmosphere irradiation on a horizontal plane per month, kWh/m2."""
    t = year_times()
    _, el = sun_position(t, lat, lon)
    month = t.astype("datetime64[M]").astype(int) % 12
    e = SOLAR_CONSTANT * np.clip(np.sin(np.radians(el)), 0, None) * (SUN_STEP_MIN / 60.0) / 1000.0
    return np.bincount(month, e, minlength=12)


def effective_sunshine_fraction(lat: float, lon: float, ghi_kwh_m2_day, a: float = 0.25, b: float = 0.50):
    """Inverse Angstrom-Prescott: sunshine fraction from the clearness index.

    ERA5's sunshine_duration overestimates in cloudy climates (Växjö: ~2650 h
    vs ~1750 h measured by SMHI), while ERA5 irradiation is much better, so we
    derive the fraction from irradiance instead.
    """
    days = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31], float)
    h0 = extraterrestrial_monthly(lat, lon)
    ghi = np.asarray(ghi_kwh_m2_day, float) * days
    kt = np.where(h0 > 0.5, ghi / np.maximum(h0, 1e-9), 0.0)
    return np.clip((kt - a) / b, 0.0, 1.0)


def day_sun_path(lat: float, lon: float, day: date, tz_name: str, step_min: int = 5) -> dict:
    """Sun azimuth/elevation over one local calendar day (for the live view)."""
    tz = ZoneInfo(tz_name)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    n = 24 * 60 // step_min + 1
    local = [start + timedelta(minutes=step_min * i) for i in range(n)]
    utc = np.array([d.astimezone(timezone.utc).replace(tzinfo=None) for d in local], dtype="datetime64[s]")
    az, el = sun_position(utc, lat, lon)
    above = el > 0
    rise = set_ = None
    for i in range(1, n):
        if above[i] and not above[i - 1] and rise is None:
            rise = local[i].strftime("%H:%M")
        if above[i - 1] and not above[i]:
            set_ = local[i].strftime("%H:%M")
    polar = None
    if above.all():
        polar = "midnight_sun"
    elif not above.any():
        polar = "polar_night"
    return {
        "date": day.isoformat(), "timezone": tz_name, "step_min": step_min,
        "minutes": [step_min * i for i in range(n)],
        "az": np.round(az, 2).tolist(), "el": np.round(el, 2).tolist(),
        "sunrise": rise, "sunset": set_, "polar": polar,
        "daylight_h": round(float(above[:-1].sum()) * step_min / 60.0, 2),
        "max_el": round(float(el.max()), 2),
    }
