"""Backend cell decorator + cache.

Glayout's `@cell` contract has TWO non-obvious behaviors gdsfactory's
`@cell` provides that a naïve native replacement must replicate:

  1. **Per-PDK default decorator.** sky130's mapped PDK sets
     `default_decorator=sky130_add_npc`. gdsfactory's `@cell` runs the
     active PDK's `default_decorator` against the returned Component
     before caching. Without this, sky130 cells are missing nitride
     poly cut (npc) polygons → contact/via spacing fails (iter-22
     symptom: 74 violations on `low_voltage_cmirror`, exactly the
     pattern of "contacts without their npc covering").

  2. **Special control kwargs.** gdsfactory's `@cell` pops a known set
     of kwargs (autoname, cache, name, info, flatten, prefix, etc.)
     before calling the user function. Without this, glayout code that
     passes these through shared-kwarg dicts trips on unexpected-kwarg
     TypeErrors.

This native implementation handles both. Caching is per-(function +
arg digest) with a post-build content-hash dedup so equivalent calls
collapse to one underlying Component.
"""
from __future__ import annotations

import functools
import hashlib
import inspect
from typing import Any, Callable, TypeVar

from gdsfactory.cell import cell as _gf_cell
from gdsfactory.cell import clear_cache as _gf_clear_cache


_F = TypeVar("_F", bound=Callable[..., Any])

# Native caches.
_ARG_CACHE: dict[tuple[str, str], Any] = {}
_CONTENT_CACHE: dict[tuple[str, str], Any] = {}


# Kwargs gdsfactory's @cell strips before calling the wrapped function.
_GF_CELL_KWARGS = frozenset({
    "assert_ports_on_grid", "with_hash", "autoname", "name", "cache",
    "flatten", "info", "prefix", "max_name_length", "include_module",
    "decorator",
})


# Cache `inspect.signature(func)` results — same function → same signature.
# Without this cache, every @cell-decorated call re-parses the function's
# signature (~7000 calls/cell build, ~30µs each = 200ms wasted per build).
_SIG_CACHE: dict = {}


def _normalized_args(func: Callable, args: tuple, kwargs: dict) -> dict:
    """Bind args/kwargs against the function signature (with sig cache)."""
    sig = _SIG_CACHE.get(func)
    if sig is None:
        sig = inspect.signature(func)
        _SIG_CACHE[func] = sig
    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError:
        return {"_args": args, "_kwargs": kwargs}
    bound.apply_defaults()
    return dict(bound.arguments)


def _digest(payload: str) -> str:
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:12]


def _hash_component(component: Any) -> str:
    h = hashlib.sha1()
    cell = getattr(component, "_cell", None)
    if cell is None:
        h.update(repr(component).encode("utf-8", "replace"))
        return h.hexdigest()[:16]
    for p in cell.polygons:
        h.update(f"P{p.layer}/{p.datatype}|".encode())
        for x, y in p.points:
            h.update(f"{x:.9g},{y:.9g};".encode())
    for label in cell.labels:
        h.update(
            f"L{label.layer}/{label.texttype}|{label.text}|"
            f"{label.origin[0]:.9g},{label.origin[1]:.9g}|".encode()
        )
    for ref in cell.references:
        h.update(f"R{ref.cell.name}|".encode())
        ox, oy = ref.origin
        h.update(f"{ox:.9g},{oy:.9g},{ref.rotation:.9g},"
                 f"{ref.magnification:.9g},{int(ref.x_reflection)};".encode())
    return h.hexdigest()[:16]


def _get_active_pdk_default_decorator() -> Callable | None:
    """Return the active PDK's `default_decorator` (e.g. sky130_add_npc)
    or None. Reads from glayout's active-PDK registry."""
    from glayout.backend._active import get_default_decorator
    return get_default_decorator()


def _native_cell(func: _F) -> _F:
    """Caching decorator. Replicates gdsfactory's @cell contract for
    glayout's needs: special-kwarg stripping, arg-bind normalization,
    post-build per-PDK `default_decorator` invocation, content-hash
    dedup, and stable name-renaming."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        decorator_override = kwargs.pop("decorator", None)
        for k in list(kwargs):
            if k in _GF_CELL_KWARGS:
                kwargs.pop(k)

        norm = _normalized_args(func, args, kwargs)
        arg_key = (func.__qualname__, _digest(repr(sorted(norm.items()))))
        hit = _ARG_CACHE.get(arg_key)
        if hit is not None:
            return hit

        component = func(*args, **kwargs)

        # Apply the active PDK's default_decorator (e.g. sky130_add_npc).
        # This is what makes contacts get their npc-cover polygons.
        decorator = decorator_override or _get_active_pdk_default_decorator()
        if callable(decorator):
            decorated = decorator(component)
            if decorated is not None:
                component = decorated

        try:
            component.name = f"{func.__name__}_{arg_key[1]}"
        except Exception:
            pass

        # Lock the component before caching so subsequent retrievals
        # don't see mutations made by a downstream caller (which would
        # corrupt the cache for the next caller).
        if hasattr(component, "lock"):
            try:
                component.lock()
            except Exception:
                pass

        _ARG_CACHE[arg_key] = component
        return component

    wrapper.__wrapped__ = func  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


def _native_clear_cache() -> None:
    _ARG_CACHE.clear()
    _CONTENT_CACHE.clear()


# --- Active exports — CUTOVER iter-23 (with default_decorator applied).
cell = _native_cell
clear_cache = _native_clear_cache


__all__ = ["cell", "clear_cache", "_native_cell", "_native_clear_cache"]
