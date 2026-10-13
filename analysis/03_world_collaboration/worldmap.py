"""A small world basemap for matplotlib: Natural Earth outlines in the Equal Earth projection.

The outlines (geo/ne_110m_admin_0_countries.geojson, public domain, naturalearthdata.com) are
presentation geometry only; every number drawn on the map still comes from the database.

Why not Plotly's built-in map: its country shapes merge Taiwan into China, so Taiwan's papers
could not be shown as their own area.

The 1:110m outlines leave out very small territories (Singapore, Hong Kong, Macao, Bahrain,
Malta ...). `draw_base` returns the country codes it did draw, so the caller can mark the rest
with a dot at their coordinates.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from matplotlib.collections import PolyCollection

GEOJSON = Path(__file__).resolve().parent / "geo" / "ne_110m_admin_0_countries.geojson"


def project(lon, lat):
    """Equal Earth projection (Savric, Patterson & Jenny 2018): equal-area, so a country's
    painted area matches its real area. Degrees in, unitless map coordinates out."""
    a1, a2, a3, a4 = 1.340264, -0.081106, 0.000893, 0.003796
    lam, phi = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    theta = np.arcsin(np.sqrt(3) / 2 * np.sin(phi))
    t2 = theta ** 2
    t6 = t2 ** 3
    x = 2 * np.sqrt(3) * lam * np.cos(theta) / (3 * (9 * a4 * t6 * t2 + 7 * a3 * t6 + 3 * a2 * t2 + a1))
    y = theta * (a1 + a2 * t2 + t6 * (a3 + a4 * t2))
    return x, y


def _outlines() -> list[tuple[str, np.ndarray]]:
    """(ISO-2 code, projected outer ring) for every polygon, Antarctica left out."""
    features = json.loads(GEOJSON.read_text(encoding="utf-8"))["features"]
    rings = []
    for feature in features:
        code = feature["properties"]["ISO_A2_EH"]            # the *_EH field has FR, NO and TW right
        if code == "AQ":
            continue
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            lon, lat = np.asarray(polygon[0], float).T        # outer ring; enclaves are own features
            rings.append((code, np.column_stack(project(lon, lat))))
    return rings


def _area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))) / 2


def draw_base(ax, fill: dict[str, object], default: str, edge: str = "white") -> set[str]:
    """Paint the countries: `fill` maps ISO-2 code -> colour, everything else gets `default`.

    Returns the ISO-2 codes present in the basemap.
    """
    rings = sorted(_outlines(), key=lambda item: -_area(item[1]))   # big first, so enclaves stay on top
    ax.add_collection(PolyCollection([ring for _, ring in rings],
                                     facecolors=[fill.get(code, default) for code, _ in rings],
                                     edgecolors=edge, linewidths=0.4, zorder=1))
    x_edge, _ = project([-180, 180], [0, 0])
    _, y_edge = project([0, 0], [-57, 84])
    ax.set_xlim(x_edge[0] * 0.96, x_edge[1] * 0.99)
    ax.set_ylim(y_edge[0], y_edge[1])
    ax.set_aspect("equal")
    ax.axis("off")
    return {code for code, _ in rings}
