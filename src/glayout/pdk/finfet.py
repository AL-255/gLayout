"""FinFET dimensions and placement lattice, in micrometres.

A manufacturing grid is not a placement grid: rounding every vertex cannot
repair a displaced fin or gate without changing the transistor. Validate those
layers and snap reference origins instead. No foundry rules are implied here.
"""

from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from contextvars import ContextVar
import math
import inspect
import numpy as np


@dataclass(frozen=True)
class FinFETTechnology:
    fin_pitch: float
    fin_width: float
    gate_pitch: float
    gate_length: float
    manufacturing_grid: float = 0.0005
    fin_min_length: float = 0.108
    active_fin_enclosure: float = 0.010
    active_gate_extension: float = 0.025
    gate_extension: float = 0.072
    select_enclosure: float = 0.054
    well_enclosure: float = 0.054
    sd_width: float = 0.024
    lig_width: float = 0.022
    lig_height: float = 0.016
    via_width: float = 0.018
    metal_width: float = 0.018
    route_pitch: float = 0.054
    row_separation: float = 0.324
    fin_layer: tuple[int, int] = (2, 0)
    gate_layer: tuple[int, int] = (7, 0)

    def __post_init__(self):
        for key, value in vars(self).items():
            if not key.endswith("_layer") and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{key} must be finite and positive")
        if self.fin_width >= self.fin_pitch or self.gate_length >= self.gate_pitch:
            raise ValueError("fin/gate width must be smaller than pitch")
        for dim in (
            self.fin_pitch,
            self.gate_pitch,
            self.fin_width / 2,
            self.gate_length / 2,
        ):
            if not _on_grid(dim, self.manufacturing_grid):
                raise ValueError("FinFET dimensions must fit the manufacturing grid")

    def fin_count(
        self, width: Optional[float] = None, nfin: Optional[int] = None
    ) -> int:
        if nfin is not None:
            if isinstance(nfin, bool) or not isinstance(nfin, int) or nfin < 1:
                raise ValueError("nfin must be a positive integer")
            return nfin
        if width is None or not math.isfinite(width) or width <= 0:
            raise ValueError("width must be finite and positive when nfin is omitted")
        return max(
            1,
            math.ceil(
                float(Decimal(str(width)) / Decimal(str(self.fin_pitch))) - 1e-10
            ),
        )

    def snap_placement(self, xy):
        return tuple(
            float(
                (Decimal(str(v)) / Decimal(str(p))).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
                * Decimal(str(p))
            )
            for v, p in zip(xy, (self.gate_pitch, self.fin_pitch))
        )

    def place(self, reference, origin=(0, 0)):
        """Move a reference origin to the nearest legal site (not its bbox centre)."""
        reference.origin = self.snap_placement(origin)
        return reference

    def validate(self, component):
        """Check flattened world coordinates, including transformed hierarchy."""
        polys = polygons_by_layer(component)
        for layer, polygons in polys.items():
            for points in polygons:
                pts = np.asarray(points, dtype=float)
                if not np.isfinite(pts).all() or not np.allclose(
                    pts / self.manufacturing_grid,
                    np.round(pts / self.manufacturing_grid),
                    atol=1e-6,
                    rtol=0,
                ):
                    raise ValueError(f"off manufacturing grid on layer {layer}")
                if layer not in (self.fin_layer, self.gate_layer):
                    continue
                lo, hi = pts.min(axis=0), pts.max(axis=0)
                # Fins and gates are unbent rectangles, with a fixed orientation.
                if not np.all(
                    np.logical_or(
                        np.isclose(pts, lo, atol=1e-9), np.isclose(pts, hi, atol=1e-9)
                    )
                ):
                    raise ValueError(f"nonrectangular fin/gate on layer {layer}")
                axis = 1 if layer == self.fin_layer else 0
                pitch = self.fin_pitch if axis == 1 else self.gate_pitch
                width = self.fin_width if axis == 1 else self.gate_length
                if not math.isclose(hi[axis] - lo[axis], width, abs_tol=1e-9):
                    raise ValueError(
                        f"incorrect fin/gate width or rotation on layer {layer}"
                    )
                if not _on_grid((hi[axis] + lo[axis]) / 2, pitch):
                    raise ValueError(f"off fin/gate placement grid on layer {layer}")
        for port in component.ports.values():
            if not all(_on_grid(v, self.manufacturing_grid) for v in port.center):
                raise ValueError(f"off manufacturing grid port {port.name}")
        return component


def _on_grid(value, pitch):
    return math.isfinite(value) and math.isclose(
        value / pitch, round(value / pitch), abs_tol=1e-6, rel_tol=0
    )


def polygons_by_layer(component):
    if hasattr(component, "_cell"):  # native gdstk
        result = {}
        for poly in component._cell.get_polygons():
            result.setdefault((poly.layer, poly.datatype), []).append(poly.points)
        return result
    return component.get_polygons(by_spec=True)


_active_technology = ContextVar("glayout_finfet_technology", default=None)


def activate_grid(technology):
    _active_technology.set(technology)


def component_technology(component):
    configs = []
    seen = set()

    def visit(c):
        if id(c) in seen:
            return
        seen.add(id(c))
        config = c.info.get("finfet_technology")
        if config is not None:
            configs.append(FinFETTechnology(**config))
        for ref in c.references:
            visit(ref.parent)

    visit(component)
    if configs and any(t != configs[0] for t in configs[1:]):
        raise ValueError("cannot mix different FinFET placement lattices")
    return configs[0] if configs else _active_technology.get()


def install_export_guard(component_class):
    """Install once for either geometry backend. Planar PDKs retain defaults.

    Persist the lattice through flattening and PDK changes: a generated FinFET
    must not lose enforcement because a planar PDK was activated afterwards.
    """
    original = component_class.write_gds
    if getattr(original, "_finfet_guard", False):
        return
    original_flatten = component_class.flatten

    def flatten(self, *args, **kwargs):
        technology = component_technology(self)
        result = original_flatten(self, *args, **kwargs)
        if technology is not None:
            result.info["finfet_technology"] = asdict(technology)
        return result

    def write_gds(self, *args, **kwargs):
        technology = component_technology(self)
        if technology is not None:
            technology.validate(self)
            bound = inspect.signature(original).bind_partial(self, *args, **kwargs)
            unit = kwargs.get("unit", bound.arguments.get("unit")) or 1e-6
            precision = technology.manufacturing_grid * unit
            given = kwargs.get("precision", bound.arguments.get("precision"))
            if given is not None and given > precision * (1 + 1e-9):
                raise ValueError(
                    "GDS precision would lose the FinFET manufacturing grid"
                )
            if given is None:
                kwargs["precision"] = precision
        return original(self, *args, **kwargs)

    write_gds._finfet_guard = True
    component_class.write_gds = write_gds
    component_class.flatten = flatten
