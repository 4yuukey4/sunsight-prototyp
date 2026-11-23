"""Local metric frame around a region origin.

A local east/north tangent plane is accurate to well under a metre over the
few-kilometre tiles we analyse, and keeps the whole pipeline free of a
projection library. Country-scale runs are processed tile by tile, each
with its own frame.
"""
import math

import numpy as np
import shapely

EARTH_R = 6378137.0


class LocalFrame:
    def __init__(self, lat0: float, lon0: float):
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        self.kx = math.radians(1.0) * EARTH_R * math.cos(math.radians(lat0))
        self.ky = math.radians(1.0) * EARTH_R

    def to_xy(self, lon, lat):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky

    def to_lonlat(self, x, y):
        return self.lon0 + np.asarray(x) / self.kx, self.lat0 + np.asarray(y) / self.ky

    def geom_to_xy(self, geom):
        def fn(c):
            x, y = self.to_xy(c[:, 0], c[:, 1])
            return np.column_stack([x, y])
        return shapely.transform(geom, fn)

    def geom_to_lonlat(self, geom):
        def fn(c):
            lon, lat = self.to_lonlat(c[:, 0], c[:, 1])
            return np.column_stack([lon, lat])
        return shapely.transform(geom, fn)

    def to_dict(self):
        return {"lat0": self.lat0, "lon0": self.lon0}

    @classmethod
    def from_dict(cls, d):
        return cls(d["lat0"], d["lon0"])


def bbox_around(lat: float, lon: float, radius_m: float):
    dlat = radius_m / (math.radians(1.0) * EARTH_R)
    dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def expand_bbox(bbox, meters: float):
    w, s, e, n = bbox
    lat = (s + n) / 2
    dlat = meters / (math.radians(1.0) * EARTH_R)
    dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
    return (w - dlon, s - dlat, e + dlon, n + dlat)
