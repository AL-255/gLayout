"""Glayout-owned active-PDK registry.

`snap.snap_to_grid`, `cell._native_cell`, and `component.write_gds` need
to fetch PDK-dependent settings (grid_size, default_decorator,
gds_write_settings) on every call. Previously they reached into
`gdsfactory.pdk.get_active_pdk()`; this module replaces that with a
local registry that MappedPDK.activate() pushes into.

The PDK object stored here is the glayout MappedPDK instance (which
still inherits from gdsfactory.Pdk for now). When the Pdk cutover
lands, this registry stays unchanged — only the PDK base class swap
needs to happen.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# The currently-active PDK. Set by MappedPDK.activate(). Falls back to
# gdsfactory.pdk.get_active_pdk() if unset (e.g. before any explicit
# activation) so legacy code paths still work.
_active: Any = None


def set_active_pdk(pdk: Any) -> None:
    """Called by MappedPDK.activate() — registers `pdk` as the current
    active PDK for subsequent grid/decorator/settings lookups."""
    global _active
    _active = pdk


def get_active_pdk() -> Any:
    """Return the registered active PDK, falling back to gdsfactory's
    global if no registration has happened yet."""
    if _active is not None:
        return _active
    try:
        from gdsfactory.pdk import get_active_pdk as _gf
        return _gf()
    except Exception:
        return None


def get_grid_size_um() -> float:
    """Active grid in microns (PDK's gds_write_settings.precision relative
    to its unit). Defaults to 1 nm if no PDK is active."""
    pdk = get_active_pdk()
    if pdk is None:
        return 1e-3
    try:
        ws = pdk.gds_write_settings
        return float(ws.precision / ws.unit)
    except Exception:
        try:
            return float(pdk.grid_size)
        except Exception:
            return 1e-3


def get_default_decorator() -> Optional[Callable]:
    """Active PDK's `default_decorator` (e.g. sky130_add_npc) or None."""
    pdk = get_active_pdk()
    if pdk is None:
        return None
    return getattr(pdk, "default_decorator", None)


def get_gds_write_unit_precision() -> tuple[float, float]:
    """Return `(unit, precision)` from the active PDK's gds_write_settings.
    Defaults to (1e-6, 1e-9) matching gdsfactory's defaults."""
    pdk = get_active_pdk()
    if pdk is None:
        return (1e-6, 1e-9)
    try:
        ws = pdk.gds_write_settings
        return (float(ws.unit), float(ws.precision))
    except Exception:
        return (1e-6, 1e-9)


__all__ = [
    "set_active_pdk", "get_active_pdk",
    "get_grid_size_um", "get_default_decorator",
    "get_gds_write_unit_precision",
]
