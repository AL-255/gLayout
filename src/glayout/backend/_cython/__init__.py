"""Cython-accelerated hot paths for `GLAYOUT_BACKEND=gdstk_cython` mode.

The extension module `_hotpaths` defines `_CyPort` (a cdef class
substitute for `_NativePort`) and `cy_add_ports_from_ref` /
`cy_build_transformed_ports` (cdef-typed loops for the dominant
port-construction hot paths).

`_speedups._activate_native_classes()` swaps `backend.Port` and
the active port class used by `_NativeComponent` /
`_NativeComponentReference` to `_CyPort` when this backend is
selected. The Python class layout (`_NativeComponent`, etc.) is
shared — only the per-port work is C-level.
"""
from ._hotpaths import (
    _CyPort,
    cy_add_ports_from_ref,
    cy_build_transformed_ports,
)


__all__ = ["_CyPort", "cy_add_ports_from_ref", "cy_build_transformed_ports"]
