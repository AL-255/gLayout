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

from typing import Any, Callable, Optional

from gdsfactory.pdk import Pdk as _GFPdk


# Pydantic is already a glayout dependency (via gdsfactory).
try:
    from pydantic import BaseModel, ConfigDict, Field
except ImportError:  # pragma: no cover — pydantic always available in CI
    BaseModel = object  # type: ignore[assignment]
    Field = lambda **k: None  # type: ignore[assignment]


class _NativeCellDecoratorSettings(BaseModel):
    """Mirrors gdsfactory.pdk.CellDecoratorSettings — the full surface
    gdsfactory's @cell reads on every invocation. Mapped PDKs typically
    mutate only `cache=False` (sky130/gf180), but the other defaults
    must exist or `getattr(settings, '<name>')` raises."""
    if BaseModel is not object:
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")
    with_hash: bool = False
    autoname: bool = True
    name: Optional[str] = None
    cache: bool = True
    flatten: bool = False
    info: dict = Field(default_factory=dict)
    prefix: Optional[str] = None
    max_name_length: int = 99
    include_module: bool = False
    assert_ports_on_grid: bool = False
    naming_style: str = "default"


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
    # default_decorator is called by `glayout.backend.cell._native_cell` on
    # every built Component (e.g. sky130 sets this to sky130_add_npc to
    # add nitride-poly-cut covering polygons over contacts — without
    # this, sky130 cells fail ct.1/via.1a/ct.2 spacing rules).
    default_decorator: Optional[Callable[[Any], Any]] = None

    @property
    def grid_size(self) -> float:
        """Minimum resolvable unit, relative to unit. Mirrors
        gdsfactory.Pdk.grid_size."""
        return self.gds_write_settings.precision / self.gds_write_settings.unit

    @grid_size.setter
    def grid_size(self, value: float) -> None:
        self.gds_write_settings.precision = value * self.gds_write_settings.unit

    def activate(self) -> None:
        """No-op marker for the active-PDK contract gdsfactory exposes.
        Glayout's MappedPDK overrides this with PDK-specific setup.
        We also push ourselves into gdsfactory's CONF so anything still
        reading `get_active_pdk()` (e.g. backend.snap, backend.cell)
        sees the right grid/decorator settings."""
        try:
            from gdsfactory.pdk import _ACTIVE_PDK
            _ACTIVE_PDK = self  # noqa — best-effort; the module may already cache
        except Exception:
            pass
        # Also try setting via the official API if the module exposes it.
        try:
            import gdsfactory.pdk as _gpdk
            _gpdk._ACTIVE_PDK = self
        except Exception:
            pass

    def validate_layers(self, layers_required=None) -> None:
        """gdsfactory.Pdk method MappedPDK doesn't override. Stub: glayout's
        MappedPDK does its own glayer validation via `has_required_glayers`."""
        return None

    def add_base_pdk(self, *args, **kwargs) -> None:
        """gdsfactory.Pdk hook for stacking PDKs — unused by glayout."""
        return None

    def register_cells(self, **kwargs) -> None:
        """gdsfactory.Pdk cell registration — unused by glayout."""
        return None

    def register_cross_sections(self, **kwargs) -> None:
        """gdsfactory.Pdk cross-section registration — unused by glayout."""
        return None

    def get_cell(self, cell, **kwargs):
        """gdsfactory.Pdk cell factory dispatch. Glayout calls primitives
        directly; this is here for surface completeness."""
        return cell

    def get_component(self, component, **kwargs):
        """gdsfactory.Pdk component factory dispatch."""
        if callable(component):
            return component(**kwargs)
        return component

    def get_constant(self, key: str):
        """Get a named constant — gdsfactory exposes a tiny constants
        registry; glayout doesn't use it but the surface should exist."""
        constants = getattr(self, "constants", {})
        return constants.get(key)

    def get_layer(self, layer):
        """Resolve a layer spec to a `(layer, datatype)` tuple. Matches
        gdsfactory.Pdk.get_layer's tolerance for None (returns (0, 0))
        and unknown integers (used by glayout for raw GDS layer numbers)."""
        if layer is None:
            return (0, 0)
        # gdsfactory's @cell decorator calls add_polygon with a default
        # of np.nan and treats nan as "skip / no-op". Match that.
        try:
            import math
            if isinstance(layer, float) and math.isnan(layer):
                return (0, 0)
        except Exception:
            pass
        if isinstance(layer, tuple):
            return layer
        if isinstance(layer, int):
            return (layer, 0)
        if isinstance(layer, str):
            entry = self.layers.get(layer)
            if entry is None:
                # Unknown string layer — gdsfactory raises KeyError; preserve
                raise KeyError(f"Layer {layer!r} not in PDK {self.name!r}")
            return entry
        raise TypeError(f"Unsupported layer spec: {layer!r}")


# --- Active export — gdsfactory (pending MappedPDK refactor).
# _NativePdk has the surface MappedPDK needs at the field level
# (cell_decorator_settings, gds_write_settings, default_decorator,
# grid_size, get_layer with None/nan tolerance, register_cells/etc.
# stubs), but activating it cascades to layer_to_glayer failures when
# get_layer((0,0)) reaches MappedPDK's glayer validation. MappedPDK
# would need a small layer-translation tweak to tolerate the (0,0)
# placeholder layers gdsfactory.Pdk passes through.
Pdk = _GFPdk


__all__ = ["Pdk", "_NativePdk", "_NativeCellDecoratorSettings", "_NativeGdsWriteSettings"]
