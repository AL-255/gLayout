"""Native `get_padding_points` — no gdsfactory dependency.

Builds the 4-point outline of `component` expanded by per-side padding.
Glayout's own `util.comp_utils.get_padding_points_cc` is a superset that
handles ComponentReference and raw bbox tuples; this remains here only to
preserve the public surface a future external caller might use.

Pure geometry — works against anything that exposes
`xmin / xmax / ymin / ymax` floats.
"""
from __future__ import annotations

from typing import Optional, Protocol


class _HasBoundingBox(Protocol):
    xmin: float
    xmax: float
    ymin: float
    ymax: float


def get_padding_points(
    component: _HasBoundingBox,
    default: float = 50.0,
    top: Optional[float] = None,
    bottom: Optional[float] = None,
    right: Optional[float] = None,
    left: Optional[float] = None,
) -> list[list[float]]:
    """Return [SW, SE, NE, NW] corners of `component`'s bbox expanded by
    the given per-side padding (microns). Any side left as `None` falls
    back to `default`."""
    top = default if top is None else top
    bottom = default if bottom is None else bottom
    right = default if right is None else right
    left = default if left is None else left
    return [
        [component.xmin - left, component.ymin - bottom],
        [component.xmax + right, component.ymin - bottom],
        [component.xmax + right, component.ymax + top],
        [component.xmin - left, component.ymax + top],
    ]


__all__ = ["get_padding_points"]
