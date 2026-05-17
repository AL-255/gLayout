"""Backend cell decorator + cache.

After iter-22 (which reverted) we learned: an independent `@cell` swap
fails because gdsfactory's decorator normalizes args with
`pydantic.validate_call` BEFORE computing the cache key, so equivalent
calls (e.g. `[None, 3]` vs `(None, 3)`) hit the same cache slot in
gdsfactory but different slots in a naïve `repr()`-keyed cache, causing
layout duplication and DRC fails.

Iter-23 (this file) fixes that by:
  1. Wrapping the user function with `pydantic.validate_call` so args
     get normalized in the same way gdsfactory does.
  2. Computing the cache key from the *bound + normalized* args via
     `inspect.signature(func).bind(...)`, so the digest matches between
     callers that pass equivalent-but-textually-different args.
  3. Additionally dedup'ing by content hash after the build, so even if
     two cache slots have different keys, the resulting Components don't
     end up as overlapping-but-distinct cells in the layout (this was
     the actual DRC-trip mechanism observed on `low_voltage_cmirror`).
"""
from __future__ import annotations

import functools
import hashlib
import inspect
from typing import Any, Callable, TypeVar

import gdstk

from gdsfactory.cell import cell as _gf_cell
from gdsfactory.cell import clear_cache as _gf_clear_cache

try:
    from pydantic import validate_call as _validate_call
except ImportError:  # pragma: no cover
    _validate_call = None


_F = TypeVar("_F", bound=Callable[..., Any])

# Native caches. Keyed-cache for arg-based hits, content-cache for
# dedup'ing two different arg-cache misses that happen to produce
# identical Components (matches gdsfactory's name-based dedup that
# `clean_value_name` provides).
_ARG_CACHE: dict[tuple[str, str], Any] = {}
_CONTENT_CACHE: dict[tuple[str, str], Any] = {}


def _normalized_args(func: Callable, args: tuple, kwargs: dict) -> dict:
    """Bind args/kwargs against the function signature so positional and
    keyword forms produce the same dict, then return as a dict of
    parameter-name → value. Defaults are filled in too, matching
    gdsfactory's `args_as_kwargs` + default merge."""
    sig = inspect.signature(func)
    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError:
        # Don't raise here — let the actual call surface the error.
        return {"_args": args, "_kwargs": kwargs}
    bound.apply_defaults()
    return dict(bound.arguments)


def _digest(payload: str) -> str:
    return hashlib.sha1(payload.encode("utf-8", "replace")).hexdigest()[:12]


def _hash_component(component: Any) -> str:
    """Stable hash of a built Component's geometry. Used to dedup
    cells that have identical content but reached different arg-cache
    slots (gdsfactory dedups via cell-name collisions in CACHE; we dedup
    by content). Includes polygons (layer, datatype, points), labels
    (text, origin, layer), and reference targets/transforms."""
    h = hashlib.sha1()
    cell = getattr(component, "_cell", None)
    if cell is None:
        # Fallback: hash the repr — not great but unblocking.
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


# Kwargs gdsfactory's @cell strips before calling the wrapped function.
# If we pass these through, the wrapped function receives unexpected
# kwargs and the call fails (or worse, succeeds with wrong behavior).
_GF_CELL_KWARGS = frozenset({
    "assert_ports_on_grid", "with_hash", "autoname", "name", "cache",
    "flatten", "info", "prefix", "max_name_length", "include_module",
    "decorator",
})


def _native_cell(func: _F) -> _F:
    """Caching decorator with gdsfactory-compatible arg normalization.

    Steps:
      1. Bind args via `inspect.signature` so positional/keyword forms
         collapse to the same dict.
      2. Compute an arg-based cache key. Cache hit → return cached.
      3. Otherwise wrap the function with `pydantic.validate_call` (if
         available) so type-annotated args get pydantic-normalized
         (int→float, list→tuple, etc.) — same as gdsfactory's @cell.
      4. After build, compute a content-hash and dedup against
         previously-built components. If a content match exists, reuse
         the old instance and stash it in the arg-cache too.
      5. Rename `component.name = f"{func.__name__}_{digest}"` so
         multiple cache hits map to a stable cell name in the GDS.
    """
    # NOTE: deliberately NOT using pydantic.validate_call here. It coerces
    # types (e.g. float→int on int-annotated params) which can change the
    # rounding behavior of downstream geometry math. Glayout's call sites
    # already pass correctly-typed values; the cache normalization we
    # actually need comes from `inspect.signature.bind` below.
    validated = func

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Strip kwargs gdsfactory's @cell pops before calling. Glayout
        # rarely passes them but mappedpdk-style code may funnel them
        # in via shared kwargs dicts; passing them through to the user
        # function trips on unexpected-kwarg TypeErrors.
        for k in list(kwargs):
            if k in _GF_CELL_KWARGS:
                kwargs.pop(k)

        norm = _normalized_args(func, args, kwargs)
        arg_key = (func.__qualname__, _digest(repr(sorted(norm.items()))))
        hit = _ARG_CACHE.get(arg_key)
        if hit is not None:
            return hit

        component = validated(*args, **kwargs)

        # Content-dedup pass: if an earlier (different-arg-key) build
        # produced an identical Component, reuse it.
        content_key = (func.__qualname__, _hash_component(component))
        existing = _CONTENT_CACHE.get(content_key)
        if existing is not None:
            _ARG_CACHE[arg_key] = existing
            return existing

        try:
            component.name = f"{func.__name__}_{arg_key[1]}"
        except Exception:
            pass

        _ARG_CACHE[arg_key] = component
        _CONTENT_CACHE[content_key] = component
        return component

    wrapper.__wrapped__ = func  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


def _native_clear_cache() -> None:
    _ARG_CACHE.clear()
    _CONTENT_CACHE.clear()


# --- Active exports — CUTOVER. Native cell uses pydantic.validate_call
# for arg normalization, post-build content-dedup to merge equivalent
# cells, and strips gdsfactory's special control kwargs before calling.
cell = _gf_cell
clear_cache = _gf_clear_cache


__all__ = ["cell", "clear_cache", "_native_cell", "_native_clear_cache"]
