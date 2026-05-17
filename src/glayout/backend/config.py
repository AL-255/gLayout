"""Backend configuration — switchable between optimized "native" mode
(monkey-patches active) and "gdsfactory" mode (vanilla, no patches).

Selection precedence (highest first):
  1. Explicit `set_backend("...")` call.
  2. `GLAYOUT_BACKEND` environment variable.
  3. Default: "native".

The setting is read by `MappedPDK.activate()` to decide whether to
install the hot-path monkey-patches in `glayout.backend._speedups`.

  - `native` (default): `apply_speedups` runs as usual, ~20× faster
    cell builds, but a handful of gdsfactory hot functions get
    replaced (`Port.copy`, `Port.__init__`, `ComponentReference.ports`,
    `Component.add_port` / `add_ports` / `flatten`, etc.). Glayout's
    `@validate_arguments` wrappers are stripped at activate time.
  - `gdsfactory`: no patches, no strip. Behaves exactly like vanilla
    gdsfactory + glayout. Useful for debugging, for measuring
    baselines, or when you need bit-for-bit gdsfactory semantics.

Usage:
    # Code:
    from glayout.backend.config import set_backend
    set_backend("gdsfactory")  # do this BEFORE any pdk.activate()
    pdk.activate()

    # Shell:
    GLAYOUT_BACKEND=gdsfactory python -c "..."
"""
from __future__ import annotations

import os
from typing import Literal

Backend = Literal["native", "gdsfactory"]

_VALID = ("native", "gdsfactory")

# Tri-state: None = not explicitly set, fall through to env var or default.
_explicit: str | None = None


def set_backend(backend: Backend) -> None:
    """Set the active backend. Must be called BEFORE the first
    `MappedPDK.activate()` to take effect — once monkey-patches are
    installed (or not), they persist for the process."""
    if backend not in _VALID:
        raise ValueError(
            f"backend must be one of {_VALID!r}, got {backend!r}"
        )
    global _explicit
    _explicit = backend


def get_backend() -> Backend:
    """Return the currently-selected backend (resolving env var and
    default if no explicit setting)."""
    if _explicit is not None:
        return _explicit  # type: ignore[return-value]
    env = os.environ.get("GLAYOUT_BACKEND", "").strip().lower()
    if env in _VALID:
        return env  # type: ignore[return-value]
    return "native"


def is_native() -> bool:
    """Convenience: True when the optimized native backend is active."""
    return get_backend() == "native"


__all__ = ["set_backend", "get_backend", "is_native", "Backend"]
