"""Native snap-to-grid for glayout.

**Critical**: the grid size is *PDK-conditional*. When sky130's mapped
PDK is active, `gdsfactory.pdk.get_grid_size()` returns 0.005 µm (5 nm),
not the 1 nm default. Iter 20 traced a 2-cell DRC regression
(`low_voltage_cmirror` + `opamp`, m1.2 spacing + ct.1) to this exact
issue when an earlier version of this module hard-coded 1 nm —
geometry ended up at sub-grid precision that violated rules tuned
for 5 nm.

Until `glayout.backend.pdk` owns the active grid value (a post-cutover
step), we read it via `gdsfactory.pdk.get_grid_size()` so the rounding
matches whatever PDK is active. This is the only gdsfactory call left
in this file; everything else is local NumPy.

Glayout's call sites pass scalars (sometimes `grid_factor=2`, audited
in `routing/c_route.py` and `util/comp_utils.py`). Tuples / numpy
arrays are also supported for compatibility with the gdsfactory surface.
"""
from __future__ import annotations

from typing import Union

import numpy as np

from glayout.backend._active import get_grid_size_um as _get_grid_size_um

Scalar = Union[int, float, np.floating]


def get_grid_size_um() -> float:
    """Active grid in microns. Reads from glayout's active-PDK registry
    (`glayout.backend._active`), with a gdsfactory fallback if no PDK
    has been registered. Default is 0.001 µm (1 nm); sky130's mapped
    PDK overrides to 0.005 µm (5 nm)."""
    return _get_grid_size_um()


def snap_to_grid(
    x: Union[Scalar, tuple, np.ndarray], grid_factor: int = 1
) -> Union[float, tuple, np.ndarray]:
    """Snap a coordinate (or tuple/array of coordinates) to a multiple of the
    fab grid. `grid_factor=2` snaps to a 2× grid (used for centered
    placement so both halves land on-grid)."""
    nm = int(get_grid_size_um() * 1000 * grid_factor)
    y = nm * np.round(np.asarray(x, dtype=float) * 1e3 / nm) / 1e3

    if isinstance(x, tuple):
        return tuple(y)
    if isinstance(x, (int, float, np.floating)):
        return float(y)
    return y


__all__ = ["snap_to_grid", "get_grid_size_um"]
