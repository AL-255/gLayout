"""
glayout.backend - shim wrapper around the layout backend.

Today every symbol re-exports from gdsfactory 7.x so we can move call sites
off direct `gdsfactory.*` imports one file at a time. Once every glayout
module imports from `glayout.backend` instead, we can replace the
re-exports below with a gdstk-native implementation without touching the
call sites.

Layout mirrors `gdsfactory/__init__.py` for the symbols glayout actually
uses (audited from `src/glayout/`). Anything not yet shimmed should be
added here rather than imported from gdsfactory directly.

Import-path discipline (DO NOT BREAK):
  * Symbols with the SAME NAME as their submodule must be imported from
    the submodule, not the package. Example: `from glayout.backend.cell
    import cell` is correct; `from glayout.backend import cell` is NOT —
    Python's import machinery unconditionally re-binds the package's
    attribute to the submodule object on every submodule load, which
    silently shadows the function. (Bit us once during the migration.)
  * The PascalCase symbols below don't collide with their lowercase
    submodule names (`Component` vs `component`, etc.), so the
    package-level re-exports are safe for those.
"""

# PascalCase symbols — no submodule-name collision, safe to re-export.
from glayout.backend.component import Component, copy
from glayout.backend.component_reference import ComponentReference
from glayout.backend.port import Port
from glayout.backend.polygon import Polygon
from glayout.backend.pdk import Pdk

# Subpackage handles (callers should reach into them rather than
# expecting re-exports of names that collide with submodule names).
from glayout.backend import components
from glayout.backend import routing
from glayout.backend import read
from glayout.backend import geometry
from glayout.backend import typings
from glayout.backend import functions
from glayout.backend import add_padding
from glayout.backend import cell as _cell_mod   # noqa: F401  (load submodule eagerly)
from glayout.backend import snap as _snap_mod   # noqa: F401
from glayout.backend import grid as _grid_mod   # noqa: F401

# Backend selector — pick "native" (default, optimized) or "gdsfactory"
# (vanilla, no monkey-patches). Set via `set_backend()` or the
# GLAYOUT_BACKEND env var BEFORE the first MappedPDK.activate().
from glayout.backend.config import set_backend, get_backend, is_native

# `from gdsfactory import ComponentReference as Reference` is used in
# util/geometry.py, so expose the short alias too.
Reference = ComponentReference

__all__ = [
    "Component",
    "ComponentReference",
    "Reference",
    "Port",
    "Polygon",
    "Pdk",
    "copy",
    "components",
    "routing",
    "read",
    "geometry",
    "typings",
    "functions",
    "add_padding",
    "set_backend",
    "get_backend",
    "is_native",
]
