"""Backend `Pdk` class.

`MappedPDK` inherits from this and adds dozens of pydantic-validated
fields (glayer maps, design rules, models, etc.). The active export
stays `gdsfactory.Pdk` until cutover because:
  1. MappedPDK declares fields with pydantic semantics that depend on
     the gdsfactory.Pdk base layout — swapping to a non-pydantic base
     would force a parallel rewrite of MappedPDK.
  2. The sky130 and gf180 mapped PDK instantiations set
     `cell_decorator_settings.cache=False` and `gds_write_settings.precision=...`
     directly on the instance. The native class needs to support that
     mutation surface.

`_NativePdk` below is a pydantic BaseModel staging the minimum surface
MappedPDK depends on. The cutover iteration will flip the active export
and re-validate the PDK instantiation paths.
"""
from __future__ import annotations

from typing import Optional

from gdsfactory.pdk import Pdk as _GFPdk


# Pydantic is already a glayout dependency (via gdsfactory).
try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError:  # pragma: no cover — pydantic always available in CI
    BaseModel = object  # type: ignore[assignment]
    Field = lambda **k: None  # type: ignore[assignment]


class _NativeCellDecoratorSettings(BaseModel):
    """Mirrors the gdsfactory.cell.CellDecoratorSettings fields glayout
    touches. `include_module` / `cache` are the only ones the mapped PDKs
    actually mutate (`.cache=False` in sky130_mapped.py and gf180_mapped.py)."""
    if BaseModel is not object:
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    cache: bool = True


class _NativeGdsWriteSettings(BaseModel):
    """GDS write tunables — `precision` is mutated by sky130_mapped to
    5e-9. `unit` defaults to 1µm in microns (1e-6 m)."""
    if BaseModel is not object:
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    unit: float = 1e-6
    precision: float = 1e-9
    flatten_invalid_refs: bool = True


class _NativePdk(BaseModel):
    """Minimal native Pdk base, pydantic-compatible so MappedPDK can keep
    declaring fields on top. Only the surface MappedPDK relies on is
    here; gdsfactory's full Pdk has dozens more fields we don't need."""
    if BaseModel is not object:
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    name: str = ""
    layers: dict = Field(default_factory=dict)
    cell_decorator_settings: _NativeCellDecoratorSettings = Field(
        default_factory=_NativeCellDecoratorSettings
    )
    gds_write_settings: _NativeGdsWriteSettings = Field(
        default_factory=_NativeGdsWriteSettings
    )

    def activate(self) -> None:
        """No-op marker for the active-PDK contract gdsfactory exposes.
        Glayout's MappedPDK overrides this with PDK-specific setup."""
        return None

    def get_layer(self, layer):
        """Resolve a layer spec (string name or (int, int) tuple) to a
        `(layer, datatype)` tuple. The mapped PDKs override this with
        their own resolution; this base version just handles tuples and
        named lookups in `self.layers`."""
        if isinstance(layer, tuple):
            return layer
        if isinstance(layer, str):
            entry = self.layers.get(layer)
            if entry is None:
                raise KeyError(f"Layer {layer!r} not in PDK {self.name!r}")
            return entry
        raise TypeError(f"Unsupported layer spec: {layer!r}")


# --- Active export — still gdsfactory until the cutover --------------
Pdk = _GFPdk


__all__ = ["Pdk", "_NativePdk", "_NativeCellDecoratorSettings", "_NativeGdsWriteSettings"]
