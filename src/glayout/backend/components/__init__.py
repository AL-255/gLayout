"""Backend stock components.

`rectangle` and `rectangular_ring` are still gdsfactory's exports for now —
the native versions live below as `_native_rectangle` /
`_native_rectangular_ring` and become the active export at the cutover.

`text_freetype` stays as a gdsfactory re-export for the time being: the
default DEPLOF font is just a polygon table in `gdsfactory.constants`,
and bundling that data into glayout is a separate cleanup task post-
cutover. The audited usage in glayout is for label-only annotation
(opamp etc. label nets), and any font that draws closed polygons on the
target layer satisfies LVS/DRC, so this isn't a behavioral risk.
"""
from __future__ import annotations

from typing import Optional, Tuple

from gdsfactory.components import (
    text_freetype as _gf_text_freetype,
    rectangle as _gf_rectangle,
    rectangular_ring as _gf_rectangular_ring,
)

from glayout.backend.component import _NativeComponent
from glayout.backend.geometry import boolean as _native_boolean


_LayerTuple = Tuple[int, int]


def _native_rectangle(
    size: Tuple[float, float] = (4.0, 2.0),
    layer: _LayerTuple = (1, 0),
    centered: bool = False,
    port_type: Optional[str] = "electrical",
    port_orientations=(180, 90, 0, -90),
) -> _NativeComponent:
    """Native rectangle: a single polygon plus 4 ports on the perimeter
    (N/E/S/W) at the midpoints of their respective edges. Mirrors
    gdsfactory's rectangle(centered, port_orientations) surface so a
    swap is invisible to call sites in glayout.primitives.* and
    glayout.routing.*.
    """
    w, h = size
    c = _NativeComponent(name="rectangle")
    if centered:
        x0, y0 = -w / 2.0, -h / 2.0
    else:
        x0, y0 = 0.0, 0.0
    x1, y1 = x0 + w, y0 + h
    c.add_polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer=layer)
    if port_type:
        # Match gdsfactory.components.compass port naming + orientation:
        #   e1=west(180), e2=north(90), e3=east(0), e4=south(270 — NOT -90)
        edges = {
            180: ("e1", (x0, (y0 + y1) / 2.0), h),
            90:  ("e2", ((x0 + x1) / 2.0, y1), w),
            0:   ("e3", (x1, (y0 + y1) / 2.0), h),
            -90: ("e4", ((x0 + x1) / 2.0, y0), w, 270),  # canonicalize to 270
            270: ("e4", ((x0 + x1) / 2.0, y0), w, 270),
        }
        for orient in port_orientations or ():
            if orient in edges:
                entry = edges[orient]
                name, center, width = entry[0], entry[1], entry[2]
                canonical_orient = entry[3] if len(entry) > 3 else orient
                if name not in c.ports:
                    c.add_port(
                        name=name, center=center, width=width,
                        orientation=canonical_orient, layer=layer, port_type=port_type,
                    )
    return c


def _native_rectangular_ring(
    enclosed_size: Tuple[float, float] = (4.0, 2.0),
    width: float = 0.5,
    layer: _LayerTuple = (1, 0),
    centered: bool = False,
) -> _NativeComponent:
    """Native rectangular_ring: outer rectangle minus inner rectangle via
    the native boolean. Mirrors gdsfactory's signature."""
    w, h = enclosed_size
    rect_in = _native_rectangle(size=(w, h), centered=centered, layer=layer)
    rect_out = _native_rectangle(
        size=(w + 2 * width, h + 2 * width), centered=centered, layer=layer,
    )
    if not centered:
        # gdsfactory shifts inner ref by (width, width); we equivalently
        # build the inner at its own origin and shift via a reference.
        rect_in_ref = rect_in.ref(position=(width, width))
        return _native_boolean(A=rect_out, B=rect_in_ref, operation="A-B", layer=layer)
    return _native_boolean(A=rect_out, B=rect_in, operation="A-B", layer=layer)


# Active exports — gdsfactory (pending coordinated Component cutover).
text_freetype = _gf_text_freetype
rectangle = _gf_rectangle
rectangular_ring = _gf_rectangular_ring


__all__ = [
    "text_freetype",
    "rectangle",
    "rectangular_ring",
    "_native_rectangle",
    "_native_rectangular_ring",
]
