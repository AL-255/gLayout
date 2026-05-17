"""Backend stock components.

Native implementations exist for every component glayout uses.
`rectangle` and `rectangular_ring` are activated alongside the
Component cutover; `text_freetype` is a thin wrapper around
`gdstk.text` (gdstk's built-in font produces closed polygons on the
named layer — sufficient for the labeling use case in glayout, even
though the glyph shapes differ from gdsfactory's DEPLOF font; DRC/LVS
only care that the polygons exist on the right layer).
"""
from __future__ import annotations

from typing import Optional, Tuple

import gdstk

from gdsfactory.components import (
    text_freetype as _gf_text_freetype,
    rectangle as _gf_rectangle,
    rectangular_ring as _gf_rectangular_ring,
)

from glayout.backend.component import _NativeComponent
from glayout.backend.geometry import boolean as _native_boolean


_LayerTuple = Tuple[int, int]


def _native_text_freetype(
    text: str = "abcd",
    size: float = 10,
    justify: str = "left",
    layer: _LayerTuple = (1, 0),
    font: str = "DEPLOF",
) -> _NativeComponent:
    """Native text rendering using gdstk.text. The `font` arg is accepted
    for gdsfactory-compat but ignored — gdstk uses its built-in single
    stroke font. Glayout's text_freetype usage is for net-label
    annotation only (e.g. opamp pin labels), so glyph fidelity isn't
    important; what matters is that polygons land on the right layer
    so klayout/magic extract the labels correctly.

    `justify` is honored ('left', 'right', 'center') via post-rendering
    shift of the polygon bbox.
    """
    gds_layer, gds_datatype = layer
    c = _NativeComponent(name="text_freetype")
    polys = gdstk.text(text, size=float(size), position=(0.0, 0.0),
                       layer=gds_layer, datatype=gds_datatype)
    if polys:
        # Compute bbox for justify shift.
        xs = []
        for p in polys:
            for x, _ in p.points:
                xs.append(float(x))
        if xs:
            xmin, xmax = min(xs), max(xs)
            if justify == "right":
                dx = -xmax
            elif justify == "center":
                dx = -(xmin + xmax) / 2.0
            else:
                dx = -xmin
        else:
            dx = 0.0
        for p in polys:
            if dx:
                p.translate(dx, 0.0)
            c._cell.add(p)
    return c


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


def _fast_gf_rectangle(
    size=(4.0, 2.0),
    layer=(1, 0),
    centered=False,
    port_type="electrical",
    port_orientations=(180, 90, 0, -90),
):
    """Drop-in for gdsfactory.components.rectangle returning a
    gdsfactory.Component but skipping the @cell wrapper overhead
    (cache lookup, signature inspect, decorator chain — adds ~500 µs
    per call). 444 calls per opamp build → ~200 ms savings.

    Equivalent layout: one polygon plus N/E/S/W ports following the
    gdsfactory.components.compass naming convention (e1..e4).
    """
    import gdsfactory as _gf
    w, h = size
    c = _gf.Component(name="rectangle")
    if centered:
        x0, y0 = -w / 2.0, -h / 2.0
    else:
        x0, y0 = 0.0, 0.0
    x1, y1 = x0 + w, y0 + h
    c.add_polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer=layer)
    if port_type:
        # Match gdsfactory.components.compass port naming + orientation:
        #   e1=west(180), e2=north(90), e3=east(0), e4=south(270 NOT -90)
        edges = {
            180: ("e1", (x0, (y0 + y1) / 2.0), h, 180),
            90:  ("e2", ((x0 + x1) / 2.0, y1), w, 90),
            0:   ("e3", (x1, (y0 + y1) / 2.0), h, 0),
            -90: ("e4", ((x0 + x1) / 2.0, y0), w, 270),
            270: ("e4", ((x0 + x1) / 2.0, y0), w, 270),
        }
        for orient in port_orientations or ():
            if orient in edges:
                name, center, width, canonical = edges[orient]
                if name not in c.ports:
                    c.add_port(
                        name=name, center=center, width=width,
                        orientation=canonical, layer=layer,
                        port_type=port_type,
                    )
    return c


rectangle = _fast_gf_rectangle
rectangular_ring = _gf_rectangular_ring


__all__ = [
    "text_freetype",
    "rectangle",
    "rectangular_ring",
    "_native_rectangle",
    "_native_rectangular_ring",
    "_native_text_freetype",
]
