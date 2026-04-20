"""gdsfactory passthrough backend.

Re-exports the gdsfactory symbols the rest of glayout depends on, under the
same names the gdstk backend uses. This keeps `from glayout.backend import X`
working identically regardless of which backend is active.
"""
from gdsfactory.component import Component, copy
from gdsfactory.component_reference import ComponentReference
from gdsfactory.port import Port
from gdsfactory.polygon import Polygon
from gdsfactory.cell import cell, clear_cache
from gdsfactory.components.rectangle import rectangle
from gdsfactory.components.rectangular_ring import rectangular_ring
from gdsfactory.snap import snap_to_grid
from gdsfactory.functions import transformed
from gdsfactory.geometry.boolean import boolean
from gdsfactory.read.import_gds import import_gds
from gdsfactory.routing.route_quad import route_quad
from gdsfactory.typings import Layer, ComponentOrReference, PathType

# Pdk lives in a separate module in newer gdsfactory
try:
    from gdsfactory.pdk import Pdk
except Exception:  # pragma: no cover
    from typing import Any as Pdk  # type: ignore


__all__ = [
    "Component",
    "ComponentReference",
    "Port",
    "Polygon",
    "Layer",
    "PathType",
    "Pdk",
    "ComponentOrReference",
    "snap_to_grid",
    "cell",
    "clear_cache",
    "copy",
    "rectangle",
    "rectangular_ring",
    "boolean",
    "import_gds",
    "transformed",
    "route_quad",
]
