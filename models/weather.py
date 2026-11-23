"""Climate statistics from the Open-Meteo historical archive (ERA5, global).

We reduce ten years of daily data to a monthly climatology: sunshine
fraction (sunshine / daylight duration), wet/rain/snow day shares and mean
global horizontal irradiation. The sunshine fraction is what turns
clear-sky sun hours into weather-adjusted sun hours.

Results are cached on a 0.1 degree grid, so neighbouring regions (and later
a country-wide run) reuse the same request.
"""
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import date

import numpy as np

from config import CACHE_DIR, CLIMATE_YEARS, OPEN_METEO_ARCHIVE, USER_AGENT

_DAILY = "sunshine_duration,daylight_duration,precipitation_sum,rain_sum,snowfall_sum,shortwave_radiation_sum"


def _fetch(lat, lon, y0, y1):
    params = {
        "latitude": f"{lat:.2f}", "longitude": f"{lon:.2f}",
        "start_date": f"{y0}-01-01", "end_date": f"{y1}-12-31",
        "daily": _DAILY, "timezone": "auto",
    }
    url = f"{OPEN_METEO_ARCHIVE}?{urllib.parse.urlencode(params)}"
    last = None
    for wait in (0, 15, 45, 90):     # the free API rate-limits multi-year requests
        time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read().decode("utf-8")
            data = json.loads(body)
            if "daily" in data:
                return data
            last = RuntimeError(data.get("reason", body[:200]))
        except Exception as exc:
            last = exc
    raise RuntimeError(f"Open-Meteo failed for {lat},{lon}: {last}")


def _arr(daily, key):
    return np.array([np.nan if v is None else v for v in daily[key]], dtype=float)


def summarise(raw: dict) -> dict:
    d = raw["daily"]
    months = np.array([date.fromisoformat(t).month - 1 for t in d["time"]])
    sun = _arr(d, "sunshine_duration") / 3600.0
    day = _arr(d, "daylight_duration") / 3600.0
    pr = _arr(d, "precipitation_sum")
    rain = _arr(d, "rain_sum")
    snow = _arr(d, "snowfall_sum")              # cm
    ghi = _arr(d, "shortwave_radiation_sum") / 3.6  # MJ/m2 -> kWh/m2

    def m(fn):
        return [fn(i) for i in range(12)]

    def frac(i):
        sel = months == i
        s, dl = np.nansum(sun[sel]), np.nansum(day[sel])
        return float(s / dl) if dl > 0 else 0.0  # polar night: no daylight, no sun

    def pct(arr, thr):
        return lambda i: float(np.nanmean(arr[months == i] >= thr) * 100)

    monthly = {
        "sunshine_fraction": m(frac),
        "sunshine_hours_day": m(lambda i: float(np.nanmean(sun[months == i]))),
        "daylight_hours_day": m(lambda i: float(np.nanmean(day[months == i]))),
        "wet_day_pct": m(pct(pr, 1.0)),
        "rain_day_pct": m(pct(rain, 1.0)),
        "snow_day_pct": m(pct(snow, 1.0)),
        "precip_mm_month": m(lambda i: float(np.nanmean(pr[months == i]) * [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][i])),
        "ghi_kwh_m2_day": m(lambda i: float(np.nanmean(ghi[months == i]))),
    }
    annual = {
        "sunshine_hours": float(np.nansum(sun) / len(set(t[:4] for t in d["time"]))),
        "sunshine_fraction": float(np.nansum(sun) / max(np.nansum(day), 1e-9)),
        "wet_day_pct": float(np.nanmean(pr >= 1.0) * 100),
        "rain_day_pct": float(np.nanmean(rain >= 1.0) * 100),
        "snow_day_pct": float(np.nanmean(snow >= 1.0) * 100),
        "ghi_kwh_m2": float(np.nanmean(ghi) * 365),
    }
    for k, v in monthly.items():
        monthly[k] = [0.0 if (x is None or math.isnan(x)) else round(x, 4) for x in v]
    return {
        "timezone": raw.get("timezone", "UTC"),
        "lat": raw.get("latitude"), "lon": raw.get("longitude"),
        "elevation": raw.get("elevation"),
        "years": list(CLIMATE_YEARS),
        "source": "Open-Meteo historical archive (ERA5 / ERA5-Land)",
        "monthly": monthly,
        "annual": {k: round(v, 3) for k, v in annual.items()},
    }


def fetch_climate(lat: float, lon: float) -> dict:
    y0, y1 = CLIMATE_YEARS
    key = f"{round(lat, 1):.1f}_{round(lon, 1):.1f}_{y0}_{y1}.json"
    path = CACHE_DIR / "weather" / key
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    clim = summarise(_fetch(round(lat, 1), round(lon, 1), y0, y1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clim, indent=1), encoding="utf-8")
    return clim
