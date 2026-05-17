"""Backend `grid` placement helper.

Active export is gdsfactory's full-featured grid (1D/2D, alignment,
mirror, rotation, edge anchoring, port-prefix renaming) because nothing
in `glayout/` actually calls grid() at the moment — `fet.py` imports it
but never uses it. The native implementation below is the minimum
shape-(N,M) tile layout that satisfies that import contract; if/when a
real caller appears, this is the place to grow the surface.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import numpy as np

from gdsfactory.grid import grid as _gf_grid

from glayout.backend.component import _NativeComponent


def _native_grid(
    components: Sequence["_NativeComponent"],
    spacing: Tuple[float, float] = (5.0, 5.0),
    shape: Optional[Tuple[int, int]] = None,
) -> _NativeComponent:
    """Place each component in a row-major grid with edge-to-edge `spacing`.
    Minimal subset of gdsfactory.grid — only what the audited surface
    needs (everything else gdsfactory provides is unused by glayout)."""
    arr = np.asarray(components, dtype=object)
    if shape is not None:
        arr = arr.reshape(shape)
    elif arr.ndim == 1:
        arr = arr.reshape(1, -1)
    nrows, ncols = arr.shape
    grid_cell = _NativeComponent(name="grid")
    # Compute uniform tile size from the largest component bbox in
    # each row/column (mirrors gdsfactory.grid's "separation" mode,
    # which is the default).
    col_widths = [max(arr[r, c].xsize for r in range(nrows)) for c in range(ncols)]
    row_heights = [max(arr[r, c].ysize for c in range(ncols)) for r in range(nrows)]
    sx, sy = spacing
    y_origin = 0.0
    for r in range(nrows):
        x_origin = 0.0
        for c in range(ncols):
            ref = grid_cell.add_ref(arr[r, c])
            ref.move(destination=(x_origin, y_origin))
            x_origin += col_widths[c] + sx
        y_origin += row_heights[r] + sy
    return grid_cell


# --- Active export — REVERTED iter-17 (see component.py for context).
grid = _gf_grid


__all__ = ["grid", "_native_grid"]
