"""Backend `Port` — re-exports gdsfactory.Port for now; the native dataclass
lives in `glayout.backend.component._NativePort` and is re-exported here as
`_NativePort` so the eventual Component swap can flip a single line per
file (here and in `__init__.py`) to activate the native type."""
from __future__ import annotations

from gdsfactory.port import Port as _GFPort

# Live in `backend.component` because Component and Port mutually
# reference each other; importing the native class here lets future
# call sites do `from glayout.backend.port import _NativePort` without
# leaking the implementation location.
from glayout.backend.component import _NativePort

# Active export — REVERTED iter-17 (see component.py header for context).
Port = _GFPort


__all__ = ["Port", "_NativePort"]
