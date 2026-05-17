"""Backend `ComponentReference` — re-exports gdsfactory's for now; the
native class lives in `glayout.backend.component._NativeComponentReference`
(same file as Component since they construct each other) and is
re-exported here as `_NativeComponentReference`. The cutover iteration
will flip the `ComponentReference = ...` line below to point at the
native class."""
from __future__ import annotations

from gdsfactory.component_reference import ComponentReference as _GFComponentReference

from glayout.backend.component import _NativeComponentReference

# Active export — gdsfactory (pending coordinated Component cutover).
ComponentReference = _GFComponentReference


__all__ = ["ComponentReference", "_NativeComponentReference"]
