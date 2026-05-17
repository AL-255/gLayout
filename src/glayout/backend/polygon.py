"""Backend `Polygon` — native after iteration-17 cutover.

Glayout's only use of `Polygon` is in `pdk/sky130_mapped/sky130_add_npc.py`
where it constructs `Polygon(points, layer=(L,D))` and appends to a list
that's then handed to `Component.add_polygon`. `_NativePolygon`
satisfies that surface via `gdstk.Polygon`.
"""
from __future__ import annotations

import gdstk

from gdsfactory.polygon import Polygon as _GFPolygon


class _NativePolygon(gdstk.Polygon):
    """Native Polygon = `gdstk.Polygon` with a `Polygon(points, layer=(L,D))`
    constructor and a `bbox` property — matches the gdsfactory surface
    glayout uses without dragging in PHIDL/shapely. Subclasses
    `gdstk.Polygon` so it flows through `gdstk.boolean`/`Cell.add`
    unmodified."""

    def __init__(self, points, layer):
        gds_layer, gds_datatype = layer
        super().__init__(list(points), gds_layer, gds_datatype)

    @property
    def bbox(self):
        return self.bounding_box()

    @property
    def center(self):
        """Center of bounding box, gdsfactory convention used by
        sky130_add_npc.py for npc-polygon proximity checks."""
        (x0, y0), (x1, y1) = self.bounding_box()
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    # Edge accessors (gdsfactory.Polygon exposes these via _GeometryHelper).
    @property
    def xmin(self) -> float: return float(self.bounding_box()[0][0])
    @property
    def ymin(self) -> float: return float(self.bounding_box()[0][1])
    @property
    def xmax(self) -> float: return float(self.bounding_box()[1][0])
    @property
    def ymax(self) -> float: return float(self.bounding_box()[1][1])


# Active export — CUTOVER iter-21. The 2-cell sky130 regression that
# blocked iter-19's cutover was traced to an unrelated bug in
# `backend.snap` (fixed in iter-20); iter-21 reattempts cleanly.
# `_NativePolygon` subclasses gdstk.Polygon, so it flows through
# gdsfactory.Component.add_polygon (which accepts gdstk.Polygon
# natively) unchanged. The only call site is sky130_add_npc.py.
Polygon = _NativePolygon


__all__ = ["Polygon", "_NativePolygon"]
