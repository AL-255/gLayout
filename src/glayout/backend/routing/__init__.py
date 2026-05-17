"""Backend routing helpers.

`route_quad` is native below (`_native_route_quad`) — small enough to
own; glayout calls it ~30 times across composite cells, all in the
default mode (no `manhattan_target_step`).

`route_sharp` stays a re-export: it's imported by `diff_pair.py` but
never actually called (verified by grep). The gdsfactory implementation
pulls in Path/transition machinery that's not worth porting until we
have a real caller.
"""
from __future__ import annotations

from typing import Optional, Tuple

import math
import gdstk
import numpy as np

from gdsfactory.routing.route_quad import route_quad as _gf_route_quad
from gdsfactory.routing.route_sharp import route_sharp as _gf_route_sharp

from glayout.backend.component import _NativeComponent, _NativePort


_LayerTuple = Tuple[int, int]


def _get_rotated_basis(theta_deg: float):
    """Basis vectors rotated CCW by `theta_deg` — copied from
    gdsfactory.routing.route_quad so we don't import that module."""
    theta = math.radians(theta_deg)
    e1 = np.array([math.cos(theta), math.sin(theta)])
    e2 = np.array([-math.sin(theta), math.cos(theta)])
    return e1, e2


def _native_route_quad(
    port1: _NativePort,
    port2: _NativePort,
    width1: Optional[float] = None,
    width2: Optional[float] = None,
    layer: _LayerTuple = (1, 0),
) -> _NativeComponent:
    """Routes a convex quad between two ports (no Manhattan/intermediate
    waypoints — glayout's default mode). Output Component carries two
    ports `e1`/`e2` flipped 180° from the input port orientations to
    match gdsfactory's contract for chained routes."""
    if width1 is None: width1 = port1.width
    if width2 is None: width2 = port2.width

    def edges(port: _NativePort, w: float):
        # The perpendicular ("e2") basis points along the port face.
        _, perp = _get_rotated_basis(port.orientation)
        c = np.array(port.center)
        return c + perp * (w / 2.0), c - perp * (w / 2.0)

    pts = np.array(edges(port1, width1) + edges(port2, width2))
    center = pts.mean(axis=0)
    # Sort by angle around the centroid → convex quad.
    angles = np.arctan2(pts[:, 0] - center[0], pts[:, 1] - center[1])
    vertices = [v for _, v in sorted(zip(angles, pts), key=lambda x: x[0])]

    comp = _NativeComponent(name="route_quad")
    comp.add_polygon([tuple(v) for v in vertices], layer=layer)
    comp.add_port(
        name="e1", center=tuple(port1.center),
        orientation=(port1.orientation + 180) % 360, width=width1, layer=layer,
    )
    comp.add_port(
        name="e2", center=tuple(port2.center),
        orientation=(port2.orientation + 180) % 360, width=width2, layer=layer,
    )
    return comp


def _native_route_sharp(
    port1: _NativePort,
    port2: _NativePort,
    width: Optional[float] = None,
    layer: _LayerTuple = (1, 0),
    cross_section=None,  # gdsfactory-compat, ignored
) -> _NativeComponent:
    """Native route_sharp: builds a single-bend Manhattan path between
    two ports. Glayout imports this in `diff_pair.py` but never calls
    it (verified by grep), so this implementation exists for surface
    completeness; if a real caller appears, the math here may need
    refinement for waveguide-style routes. For the simple electrical
    use case it draws a width-wide polygon along the L-shape between
    port centers.

    Approach: a single 90° bend at the intersection of the port axes.
    """
    if width is None:
        width = (port1.width + port2.width) / 2.0

    p1 = np.array(port1.center, dtype=float)
    p2 = np.array(port2.center, dtype=float)

    delta = p2 - p1
    # Decide bend direction by which axis the deltas dominate.
    # Path1: horizontal then vertical, path2: vertical then horizontal.
    # Pick the one with shortest L1 distance (they're equal — just pick H-first).
    waypoints = [tuple(p1), (float(p2[0]), float(p1[1])), tuple(p2)]

    # Build the path as a thick polyline.
    path = gdstk.FlexPath(waypoints, width=float(width),
                          layer=layer[0], datatype=layer[1])

    comp = _NativeComponent(name="route_sharp")
    for poly in path.to_polygons():
        comp._cell.add(poly)
    comp.add_port(
        name="e1", center=tuple(port1.center),
        orientation=(port1.orientation + 180) % 360,
        width=float(width), layer=layer,
    )
    comp.add_port(
        name="e2", center=tuple(port2.center),
        orientation=(port2.orientation + 180) % 360,
        width=float(width), layer=layer,
    )
    return comp


# Active exports — gdsfactory (pending coordinated Component cutover).
route_quad = _gf_route_quad
route_sharp = _gf_route_sharp


__all__ = ["route_quad", "route_sharp", "_native_route_quad", "_native_route_sharp"]
