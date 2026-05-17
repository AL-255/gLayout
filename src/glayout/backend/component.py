"""
Backend `Component` — currently a re-export of gdsfactory.Component, but
this module also hosts `_NativeComponent`: the in-progress, gdstk-only
implementation that will eventually replace the re-export.

Cutover plan: `_NativeComponent` is built up across several iterations
(one method-group per iteration, each validated for behavioral
equivalence with gdsfactory.Component). When every method glayout uses
is covered, a single iteration renames `_NativeComponent` → `Component`
and updates every factory in `glayout.backend.{components,routing,read}`
to construct the native type. Until then the active `Component` is the
re-export so the call graph stays unbroken.

Audited surface used by glayout (do not break without grepping):
  - `Component(name=...)` construction
  - `add_ref`, `add_polygon`, `add_label`, `add_port`, `add_ports`, `add`
  - `remove_layers`, `remove_ports_with_prefix`
  - `flatten`, `extract`, `copy`, `ref`, `ref_center`
  - `references`, `ports`, `name`, `bbox`, `xmin/xmax/ymin/ymax`,
    `xsize/ysize`, `center`, `info`, `_cell`, `_cell.labels`
  - `write_gds`, `show`
  - Container subscript: `comp["ref_name"]`
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple, Union
from pathlib import Path

import gdstk

from gdsfactory.component import Component as _GFComponent
from gdsfactory.component import copy as _gf_copy

# Placeholder forward declarations — real bindings at bottom of file
# (after `_NativeComponent` and `_native_copy` are defined).
Component = _GFComponent
copy = _gf_copy


def _native_copy(D: "_NativeComponent", name: Optional[str] = None) -> "_NativeComponent":
    """Native equivalent of gdsfactory.component.copy — call this once the
    swap iteration activates `_NativeComponent`. Same signature, but the
    `references/ports/polygons/paths/labels` kwargs aren't needed (glayout
    never passes them; gdsfactory used them only for its bookkeeping)."""
    return D.copy(name=name)


# --- Native Component, in progress -----------------------------------
#
# Iteration 7 (this one): construction, name, info dict, _cell,
# bbox/center/xmin/xmax/ymin/ymax/xsize/ysize, add_polygon, polygons
# accessor, get_polygons, write_gds.
#
# Each subsequent iteration adds more method groups (ports, refs,
# transforms, etc.) and is validated for equivalence with the
# gdsfactory implementation.

_LayerTuple = Tuple[int, int]
_PointSeq = Sequence[Tuple[float, float]]


class MutabilityError(ValueError):
    """Raised when mutating a locked Component. Matches gdsfactory's
    exception for compatibility with any glayout `try/except` that catches it."""


class _NativeComponentReference:
    """Native ComponentReference — wraps a gdstk.Reference and tracks back
    to a `_NativeComponent` parent.

    Forward-declared here (rather than in `backend.component_reference`) so
    Component and Reference can construct each other without a circular
    import. The standalone module will re-export this class.

    Audited surface used by glayout: `origin`, `rotation`, `magnification`,
    `x_reflection`, `columns`, `rows`, `spacing`, `v1`, `v2`, `parent`
    (the referenced Component), `ports` (transformed copies), `center`,
    `move(origin, destination)`, `movex`, `movey`."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.is_instance_schema(cls)

    def __init__(
        self,
        component: "_NativeComponent",
        origin: Tuple[float, float] = (0.0, 0.0),
        rotation: float = 0.0,
        magnification: float = 1.0,
        x_reflection: bool = False,
        columns: int = 1,
        rows: int = 1,
        spacing: Optional[Tuple[float, float]] = None,
        v1: Optional[Tuple[float, float]] = None,
        v2: Optional[Tuple[float, float]] = None,
    ) -> None:
        self.parent = component
        self._reference = gdstk.Reference(
            cell=component._cell,
            origin=origin,
            rotation=rotation * 3.141592653589793 / 180.0,  # gdstk uses radians
            magnification=magnification,
            x_reflection=x_reflection,
            columns=columns,
            rows=rows,
            spacing=spacing or (0.0, 0.0),
        )
        # cache the in-degrees rotation gdsfactory exposes
        self._rotation_deg = float(rotation)
        self.v1 = v1
        self.v2 = v2

    # --- transform-tracking properties (delegate to gdstk.Reference) -
    @property
    def origin(self) -> Tuple[float, float]:
        return tuple(self._reference.origin)
    @origin.setter
    def origin(self, value: Tuple[float, float]) -> None:
        self._reference.origin = tuple(value)

    @property
    def rotation(self) -> float:
        return self._rotation_deg
    @rotation.setter
    def rotation(self, value: float) -> None:
        self._rotation_deg = float(value)
        self._reference.rotation = float(value) * 3.141592653589793 / 180.0

    @property
    def magnification(self) -> float: return self._reference.magnification
    @magnification.setter
    def magnification(self, value: float) -> None: self._reference.magnification = float(value)

    @property
    def x_reflection(self) -> bool: return self._reference.x_reflection
    @x_reflection.setter
    def x_reflection(self, value: bool) -> None: self._reference.x_reflection = bool(value)

    @property
    def columns(self) -> int: return self._reference.repetition.columns or 1
    @property
    def rows(self) -> int: return self._reference.repetition.rows or 1
    @property
    def spacing(self):
        return self._reference.repetition.spacing

    # --- geometry ----------------------------------------------------
    @property
    def bbox(self):
        import numpy as np
        bb = self._reference.bounding_box()
        if bb is None:
            return np.array([[0.0, 0.0], [0.0, 0.0]])
        (x0, y0), (x1, y1) = bb
        return np.array([[x0, y0], [x1, y1]])

    @property
    def center(self) -> Tuple[float, float]:
        bb = self.bbox
        return ((bb[0][0] + bb[1][0]) / 2.0, (bb[0][1] + bb[1][1]) / 2.0)

    @property
    def xmin(self) -> float: return float(self.bbox[0][0])
    @property
    def ymin(self) -> float: return float(self.bbox[0][1])
    @property
    def xmax(self) -> float: return float(self.bbox[1][0])
    @property
    def ymax(self) -> float: return float(self.bbox[1][1])

    # --- compatibility helpers --------------------------------------
    def get_ports_list(self, **kwargs) -> list["_NativePort"]:
        """Return ports as a list (matches gdsfactory ComponentReference.
        get_ports_list which calls `select_ports → sort_ports_clockwise`).
        The clockwise sort matters: downstream `add_ports` chains feed
        through `rename_ports_by_orientation`, and when two ports
        rename to the same new name (e.g. two south-orient gate vias
        both → gate_S), the LAST-inserted wins. Matching gf's sort
        keeps the same port winning in both backends."""
        ports = self.ports
        if kwargs:
            from gdsfactory.port import select_ports
            return list(select_ports(ports, **kwargs).values())
        from gdsfactory.port import sort_ports_clockwise
        return list(sort_ports_clockwise(ports).values())

    # --- polygons via reference (for boolean/extract consumers) -----
    def get_polygons(self, as_array: bool = False) -> list:
        """Return the reference's polygons with this ref's transform
        applied — matches gdsfactory's `ComponentReference.get_polygons`.
        Required by `glayout.backend.geometry.boolean` when a reference
        is passed as an operand."""
        polys = self._reference.get_polygons()
        if as_array:
            return [p.points for p in polys]
        return polys

    # --- transformed ports ------------------------------------------
    @property
    def ports(self) -> dict[str, "_NativePort"]:
        """Returns parent ports transformed by this reference's
        rotation/x_reflection/origin. Centers and orientations are
        recomputed; widths and layers pass through unchanged."""
        import math
        rad = math.radians(self._rotation_deg)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        ox, oy = self._reference.origin
        out: dict[str, _NativePort] = {}
        for name, p in self.parent.ports.items():
            cx, cy = p.center
            if self.x_reflection:
                cy = -cy
            # rotate
            rx = cx * cos_r - cy * sin_r
            ry = cx * sin_r + cy * cos_r
            # translate
            new_center = (rx + ox, ry + oy)
            # Match gdsfactory's order: rotate first, then x_reflect.
            # `_NativeComponentReference.x_reflection` negates the final
            # angle (reflection across the horizontal x-axis).
            new_orientation = (p.orientation + self._rotation_deg) % 360
            if self.x_reflection:
                new_orientation = (-new_orientation) % 360
            out[name] = _NativePort(
                name=p.name,
                center=new_center,
                width=p.width,
                orientation=new_orientation,
                layer=p.layer,
                port_type=p.port_type,
                parent=self.parent,
            )
        return out

    # --- movement helpers (subset of gdsfactory ComponentReference) ---
    def move(
        self,
        origin: Union[Tuple[float, float], str] = (0, 0),
        destination: Optional[Tuple[float, float]] = None,
        axis: Optional[str] = None,
    ) -> "_NativeComponentReference":
        """Mirror gdsfactory's signature: if `destination` is None, treat
        `origin` as the destination (relative move); otherwise translate
        the reference so `origin` lands on `destination`.

        Snaps the resulting origin to the active PDK grid to match
        gdsfactory's implicit snap-on-Port-coord behaviour. Without
        this, downstream `align_comp_to_port` chains accumulate
        sub-grid float fuzz that lands the final polygon edges at a
        different on-grid integer than gdsfactory's snapped coords.
        """
        if destination is None:
            dx, dy = origin
        else:
            ox, oy = origin if isinstance(origin, tuple) else self.parent.ports[origin].center
            dx, dy = (destination[0] - ox, destination[1] - oy)
        if axis == "x": dy = 0
        elif axis == "y": dx = 0
        ox, oy = self.origin
        nx, ny = ox + dx, oy + dy
        from glayout.backend._active import get_grid_size_um
        grid_um = get_grid_size_um()
        if grid_um > 0:
            grid_nm = grid_um * 1000.0
            nx = round(nx * 1000.0 / grid_nm) * grid_nm / 1000.0
            ny = round(ny * 1000.0 / grid_nm) * grid_nm / 1000.0
        self.origin = (nx, ny)
        return self

    def movex(self, dx: float) -> "_NativeComponentReference":
        return self.move(origin=(dx, 0))
    def movey(self, dy: float) -> "_NativeComponentReference":
        return self.move(origin=(0, dy))

    def mirror(self, p1=(0.0, 1.0), p2=(0.0, 0.0)) -> "_NativeComponentReference":
        """Mirror across the line through p1, p2. Direct port of
        gdsfactory.ComponentReference.mirror (component_reference.py:703).
        """
        import math
        import numpy as np
        # Allow ports as p1/p2 (gdsfactory does)
        if hasattr(p1, "center"): p1 = p1.center
        if hasattr(p2, "center"): p2 = p2.center
        p1 = np.array(p1, dtype=float)
        p2 = np.array(p2, dtype=float)

        def _rotate_points(point, angle, center=(0, 0)):
            angle_rad = math.radians(angle)
            cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
            cx, cy = center
            x, y = float(point[0]) - cx, float(point[1]) - cy
            return (cos_a * x - sin_a * y + cx, sin_a * x + cos_a * y + cy)

        # Translate so reflection axis passes through origin
        ox, oy = self.origin
        ox -= p1[0]; oy -= p1[1]
        self.origin = (ox, oy)

        # Rotate so reflection axis aligns with x-axis
        angle = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
        new_origin = _rotate_points(self.origin, angle=-angle, center=(0, 0))
        self.origin = new_origin
        self.rotation = self._rotation_deg - angle

        # Reflect across x-axis
        self.x_reflection = not self.x_reflection
        self.origin = (self.origin[0], -self.origin[1])
        self.rotation = -self._rotation_deg

        # Un-rotate and un-translate
        new_origin = _rotate_points(self.origin, angle=angle, center=(0, 0))
        self.origin = new_origin
        self.rotation = (self._rotation_deg + angle) % 360
        ox, oy = self.origin
        self.origin = (ox + p1[0], oy + p1[1])
        # Grid-snap to avoid OFFGRID violations from floating-point drift
        # during the rotate/reflect/un-rotate chain (sky130 OFFGRID rules
        # are strict on the 5nm grid).
        from glayout.backend.snap import snap_to_grid
        ox, oy = self.origin
        self.origin = (snap_to_grid(ox), snap_to_grid(oy))
        return self

    def mirror_x(self, port_name=None, x0=None) -> "_NativeComponentReference":
        """Mirror across vertical line x=x0 (or port's x). Default x0
        flips around `-self.x` per gdsfactory's behaviour."""
        if port_name is None and x0 is None:
            x0 = -self.center[0]
        if port_name is not None:
            x0 = self.parent.ports[port_name].center[0]
        return self.mirror((x0, 1), (x0, 0))

    def mirror_y(self, port_name=None, y0=None) -> "_NativeComponentReference":
        """Mirror across horizontal line y=y0 (or port's y). Default y0=0."""
        if port_name is None and y0 is None:
            y0 = 0.0
        if port_name is not None:
            y0 = self.parent.ports[port_name].center[1]
        return self.mirror((1, y0), (0, y0))

    @property
    def info(self) -> dict:
        """Pass through to the parent Component's info dict — matches
        gdsfactory's `ComponentReference.info` accessor."""
        return self.parent.info

    def rotate(self, angle: float, center: Tuple[float, float] = (0, 0)) -> "_NativeComponentReference":
        """Rotate (in degrees) around `center` — matches gdsfactory order:
        translate to center → rotate → translate back."""
        import math
        rad = math.radians(angle)
        cos_r, sin_r = math.cos(rad), math.sin(rad)
        ox, oy = self.origin
        rx = (ox - center[0]) * cos_r - (oy - center[1]) * sin_r + center[0]
        ry = (ox - center[0]) * sin_r + (oy - center[1]) * cos_r + center[1]
        self.origin = (rx, ry)
        self.rotation = (self._rotation_deg + angle) % 360
        return self


@dataclass
class _NativePort:
    """Minimal native Port — `add_port` constructs instances of this.

    Stand-alone `glayout.backend.port.Port` will reuse this class once the
    Component is activated (so all Ports flowing through the backend have a
    single type). Surface matches what glayout audits show — `name`,
    `center`, `orientation`, `width`, `layer`, plus `copy(name=...)` /
    `parent`.

    Mirrors gdsfactory.Port's `__init__` snap_to_grid behavior: `center`
    is snapped to the active PDK grid in `__post_init__`. Without this
    snap, downstream code that uses `port.center` for positioning
    propagates raw float values that may land on a different integer
    grid unit at GDS write time than gdsfactory would have produced,
    causing the 5-cell DRC drift the cutover used to fail on.
    """

    name: str
    center: Tuple[float, float]
    width: float
    orientation: float
    layer: _LayerTuple = (1, 0)
    port_type: str = "optical"
    parent: Optional["_NativeComponent"] = None
    # gdsfactory compat fields glayout reads but doesn't really construct:
    cross_section: Any = None
    shear_angle: Optional[float] = None

    def __post_init__(self) -> None:
        # Snap center to the gdsfactory-default 1 nm grid (NOT the PDK
        # 5 nm grid). gdsfactory.Port.__init__ does `snap_to_grid(...,
        # nm=1)` unconditionally; we mirror that so port-center math
        # produces the same on-1nm-grid value here as in gf bench
        # mode. Snapping to a finer 1 nm grid kills only the
        # sub-nanometer float fuzz that propagates from Decimal-
        # converted-back-to-float values — it doesn't change any
        # 5 nm-aligned value that downstream `snap_to_2xgrid` cares
        # about.
        cx, cy = self.center
        snx = round(cx * 1000.0) / 1000.0
        sny = round(cy * 1000.0) / 1000.0
        self.center = (snx, sny)

    @property
    def x(self) -> float:
        return float(self.center[0])

    @x.setter
    def x(self, value: float) -> None:
        self.center = (float(value), self.center[1])

    @property
    def y(self) -> float:
        return float(self.center[1])

    @y.setter
    def y(self, value: float) -> None:
        self.center = (self.center[0], float(value))

    def copy(self, name: Optional[str] = None) -> "_NativePort":
        out = replace(self, parent=None)
        if name is not None:
            out.name = name
        return out

    def move_copy(self, offset: Tuple[float, float]) -> "_NativePort":
        """Return a copy shifted by `offset` (used by util/comp_utils
        when generating offset port arrays).

        Bypasses `dataclasses.replace` so `__post_init__`'s 1 nm snap
        doesn't fire on the moved center — matches gdsfactory's
        `Port.move_copy` which does NOT snap after the move. The
        float fuzz that survives a non-snapped move is observable
        by downstream `pdk.snap_to_2xgrid`'s ROUND_UP decimal-
        rounding, and matching that behaviour keeps the gdstk-mode
        GDS byte-identical to gdsfactory mode.
        """
        cx, cy = self.center
        ox, oy = offset
        out = type(self).__new__(type(self))
        out.__dict__.update(self.__dict__)
        out.center = (cx + ox, cy + oy)
        out.parent = None
        return out

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema
        return core_schema.is_instance_schema(cls)


class _NativeComponent:
    """In-progress native Component, wraps a `gdstk.Cell`.

    Intentionally minimal — only the methods listed in the file-header
    "audited surface" are implemented; anything beyond that is added in
    later iterations as needed. Construction matches gdsfactory:
    `Component(name=...)`, with a default auto-generated name if omitted.
    """

    _name_counter = 0  # auto-name fallback (mirrors gdsfactory's "Unnamed_N")

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        # Glayout's @validate_arguments decorators (in util/geometry.py and
        # elsewhere) try to build a schema for Component-typed parameters.
        # Tell pydantic to accept any instance of us without further
        # validation — equivalent to `arbitrary_types_allowed=True` at the
        # field level.
        from pydantic_core import core_schema
        return core_schema.is_instance_schema(cls)

    def __init__(self, name: Optional[str] = None) -> None:
        # ALWAYS uniquify the cell name. gdstk caches Cell.bounding_box
        # results by name internally — if two sibling references in a
        # parent cell point to Cell objects that share a name (e.g.
        # multiple `_native_rectangle()` cells all named "rectangle"),
        # the parent's bounding_box returns only the FIRST cell's bbox,
        # ignoring the others. Discovered debugging the gdstk-cutover
        # via_stack mode where evaluate_bbox(viastack) returned 0.15
        # instead of 0.43, because all sub-rectangles had cell name
        # "rectangle" and only the first (0.15 via2 patch) was counted.
        # Suffix the user-supplied name with a counter the same way
        # gdsfactory's `$N` convention does — preserves the original
        # name as a prefix so debug names still make sense.
        type(self)._name_counter += 1
        cnt = type(self)._name_counter
        if name is None:
            name = f"Unnamed_{cnt}"
        else:
            name = f"{name}${cnt}"
        self._cell: gdstk.Cell = gdstk.Cell(name)
        self.info: dict = {}
        self.ports: dict[str, _NativePort] = {}
        self._references: list[_NativeComponentReference] = []
        self._named_references: dict[str, _NativeComponentReference] = {}
        # gdsfactory-compat lock: @cell sets this True after build to
        # prevent accidental mutation of cached cells (which would
        # pollute subsequent retrievals from the cache). Glayout calls
        # `.unlock()` before legitimate mutation (e.g. add_df_labels).
        self._locked: bool = False

    def _check_unlocked(self) -> None:
        if self._locked:
            raise MutabilityError(
                f"Component {self.name!r} is locked (already in the cell cache). "
                "Call .unlock() before mutating."
            )

    # --- Identity ----------------------------------------------------
    @property
    def name(self) -> str:
        return self._cell.name

    @name.setter
    def name(self, value: str) -> None:
        self._cell.name = value

    def __repr__(self) -> str:
        return f"Component(name={self.name!r})"

    # --- Geometry queries -------------------------------------------
    @property
    def bbox(self):
        """Returns ((xmin, ymin), (xmax, ymax)) as a 2x2 numpy array.

        Snaps each coord to the active PDK grid. Without this snap,
        accumulated float-fuzz in gdstk's bounding_box can produce a
        1-ULP-above-on-grid value (e.g. 6.095000000000001 instead of
        6.095) that propagates through `evaluate_bbox → tap_encloses`
        in two_nfet_interdigitized and bumps a tapring by one grid
        unit (5 nm in sky130) — the cmirror's final ymax then differs
        from gdsfactory's by 5 nm. Snapping here normalises the bbox
        so any tiny float error gets erased BEFORE it propagates.
        """
        import numpy as np
        bb = self._cell.bounding_box()
        if bb is None:
            return np.array([[0.0, 0.0], [0.0, 0.0]])
        (x0, y0), (x1, y1) = bb
        from glayout.backend._active import get_grid_size_um
        g = get_grid_size_um()
        if g > 0:
            gnm = g * 1000.0
            def _snap(v):
                # Only snap if `v` is within 1-ULP of an on-grid value
                # (i.e. fix accumulated float fuzz, not real off-grid
                # values). Otherwise leave it alone so genuine
                # off-grid placements aren't forced into the wrong
                # grid bin.
                raw = v * 1000.0 / gnm
                nearest = round(raw)
                if abs(raw - nearest) < 1e-6:  # within 1 ULP of integer
                    return nearest * gnm / 1000.0
                return v
            x0, y0, x1, y1 = _snap(x0), _snap(y0), _snap(x1), _snap(y1)
        return np.array([[x0, y0], [x1, y1]])

    @property
    def xmin(self) -> float: return float(self.bbox[0][0])
    @property
    def ymin(self) -> float: return float(self.bbox[0][1])
    @property
    def xmax(self) -> float: return float(self.bbox[1][0])
    @property
    def ymax(self) -> float: return float(self.bbox[1][1])
    @property
    def xsize(self) -> float: return self.xmax - self.xmin
    @property
    def ysize(self) -> float: return self.ymax - self.ymin
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.xmin + self.xmax) / 2.0, (self.ymin + self.ymax) / 2.0)

    # --- Polygons ---------------------------------------------------
    def add_polygon(
        self,
        points: Union[_PointSeq, gdstk.Polygon, Sequence[gdstk.Polygon]],
        layer: Optional[_LayerTuple] = None,
    ) -> Union[gdstk.Polygon, list[gdstk.Polygon]]:
        """Add a polygon (or list of polygons) on `layer = (gds_layer,
        gds_datatype)`. Accepts either a sequence of (x, y) tuples or
        pre-built gdstk.Polygon objects (or a list of them — used by
        `geometry.boolean`).

        Layer semantics matching gdsfactory.Component.add_polygon:
          * If `layer` is provided, it overrides the polygon's layer.
          * If `layer` is None and `points` is a gdstk.Polygon, the
            polygon's existing layer/datatype is preserved. This was
            the iter-23 regression source: `straight_route.py` and
            `get_primitive_rectangle` pass `gdstk.rectangle(...)`
            (which carries the real PDK layer) into add_polygon WITHOUT
            an override; the previous default of `layer=(1,0)`
            overwrote and put 59 polygons on layer (1,0) instead of
            the proper PDK layers, causing massive sky130 DRC fails.
        """
        self._check_unlocked()
        def _resolve(poly: gdstk.Polygon) -> Tuple[int, int]:
            if layer is not None:
                return layer
            return (poly.layer, poly.datatype)

        if isinstance(points, gdstk.Polygon):
            gl, gd = _resolve(points)
            poly = gdstk.Polygon(points.points, layer=gl, datatype=gd)
            self._cell.add(poly)
            return poly
        if isinstance(points, (list, tuple)) and points and isinstance(points[0], gdstk.Polygon):
            polys = []
            for p in points:
                gl, gd = _resolve(p)
                polys.append(gdstk.Polygon(p.points, layer=gl, datatype=gd))
            for p in polys:
                self._cell.add(p)
            return polys
        gl, gd = layer if layer is not None else (1, 0)
        poly = gdstk.Polygon(list(points), layer=gl, datatype=gd)
        self._cell.add(poly)
        return poly

    @property
    def polygons(self) -> list[gdstk.Polygon]:
        return list(self._cell.polygons)

    def get_polygons(self, as_array: bool = True):
        """Mirrors gdsfactory's `Component.get_polygons`: when `as_array=True`
        (default) return points-arrays; otherwise return gdstk.Polygon
        objects so the layer survives."""
        polys = self._cell.get_polygons()
        if as_array:
            return [p.points for p in polys]
        return polys

    # --- Labels -----------------------------------------------------
    def add_label(
        self,
        text: str = "hello",
        position: Tuple[float, float] = (0.0, 0.0),
        magnification: float = 1.0,
        rotation: float = 0.0,
        anchor: str = "o",
        layer: _LayerTuple = (1, 0),
        x_reflection: bool = False,
    ) -> gdstk.Label:
        self._check_unlocked()
        gds_layer, gds_datatype = layer
        label = gdstk.Label(
            text=text,
            origin=position,
            anchor=anchor,
            magnification=magnification,
            rotation=rotation,
            layer=gds_layer,
            texttype=gds_datatype,
            x_reflection=x_reflection,
        )
        self._cell.add(label)
        return label

    @property
    def labels(self) -> list[gdstk.Label]:
        return list(self._cell.labels)

    # --- Ports ------------------------------------------------------
    def add_port(
        self,
        name: Union[str, _NativePort, None] = None,
        center: Optional[Tuple[float, float]] = None,
        width: Optional[float] = None,
        orientation: Optional[float] = None,
        port: Optional[_NativePort] = None,
        layer: Optional[_LayerTuple] = None,
        port_type: Optional[str] = None,
    ) -> _NativePort:
        """Add a port. Three call styles supported (mirrors gdsfactory):
          add_port(name, center=..., width=..., orientation=..., layer=...)
          add_port(port=existing)                  # copy
          add_port(port=existing, name="new_name") # copy with rename
        """
        self._check_unlocked()
        if port is not None:
            p = port.copy()
            if name is not None:
                p.name = name  # type: ignore[assignment]
            if center is not None: p.center = center
            if width is not None: p.width = width
            if orientation is not None: p.orientation = orientation
            if layer is not None: p.layer = layer
            if port_type is not None: p.port_type = port_type
            p.parent = self
        elif isinstance(name, _NativePort):
            p = name.copy()
            p.parent = self
            name = p.name
        elif center is None:
            raise ValueError("Port needs center parameter (x, y) um.")
        else:
            p = _NativePort(
                name=str(name) if name is not None else "",
                center=center,
                width=float(width) if width is not None else 0.0,
                orientation=float(orientation) if orientation is not None else 0.0,
                layer=layer if layer is not None else (1, 0),
                port_type=port_type or "optical",
                parent=self,
            )
        if name is not None and not isinstance(name, _NativePort):
            p.name = str(name)
        if p.name in self.ports:
            raise ValueError(f"add_port() Port name {p.name!r} exists in {self.name!r}")
        self.ports[p.name] = p
        return p

    def add_ports(
        self,
        ports: Union[Iterable[_NativePort], Mapping[str, _NativePort]],
        prefix: str = "",
        suffix: str = "",
    ) -> None:
        self._check_unlocked()
        items = ports.values() if isinstance(ports, Mapping) else ports
        for port in items:
            self.add_port(name=f"{prefix}{port.name}{suffix}", port=port)

    # --- Layer ops --------------------------------------------------
    def remove_layers(
        self,
        layers: Sequence[_LayerTuple],
        include_labels: bool = True,
        invert_selection: bool = False,
        recursive: bool = True,
    ) -> "_NativeComponent":
        """Drop polygons (and optionally labels/paths) on the listed
        (layer, datatype) tuples. Returns self for chaining (gdsfactory
        also returns the same component for the non-recursive case;
        recursive flattens first)."""
        target = self.flatten() if recursive and self._cell.references else self
        target._cell.filter(
            spec=list(layers),
            remove=not invert_selection,
            polygons=True,
            paths=True,
            labels=include_labels,
        )
        return target

    def extract(self, layers: Sequence[_LayerTuple]) -> "_NativeComponent":
        """Return a new Component containing only the polygons on `layers`."""
        if not isinstance(layers, (list, tuple)):
            raise ValueError(f"layers {layers!r} must be list or tuple")
        out = _NativeComponent()
        wanted = set(tuple(l) for l in layers)
        for p in self._cell.get_polygons():
            if (p.layer, p.datatype) in wanted:
                out._cell.add(gdstk.Polygon(p.points, layer=p.layer, datatype=p.datatype))
        for path in self._cell.get_paths():
            if (path.layers[0], path.datatypes[0]) in wanted:
                out._cell.add(path.copy())
        return out

    # --- References -------------------------------------------------
    def add_ref(
        self,
        component: "_NativeComponent",
        alias: Optional[str] = None,
    ) -> _NativeComponentReference:
        """Add a reference to `component` and return the reference object."""
        ref = _NativeComponentReference(component)
        self._cell.add(ref._reference)
        self._references.append(ref)
        if alias is not None:
            self._named_references[alias] = ref
        return ref

    def add(self, element) -> "_NativeComponent":
        """Add a polygon, reference, label, or iterable thereof. Mirrors
        gdsfactory's `Component.add` polymorphism.

        gdsfactory's `Component.add(other_component)` actually
        ITERATES the other component (via __iter__ = polygons + paths
        + labels + references) and adds each item. This flattens the
        other component's contents into this one — labels included.
        Without matching this behaviour, sub-cell pin labels (added
        by e.g. diff_pair_ibias) stay buried in a reference instead
        of landing on the parent's top-level cell, and the opamp's
        `_erase_subcell_pin_labels` finds nothing to erase, leading
        to duplicate pin labels at the opamp top → LVS mismatch.
        """
        self._check_unlocked()
        if isinstance(element, _NativeComponentReference):
            self._cell.add(element._reference)
            self._references.append(element)
        elif isinstance(element, _NativeComponent):
            # Match gdsfactory: iterate the component (polygons,
            # labels, references) and add each — this flattens its
            # contents into us, propagating labels to our top cell.
            import gdstk
            for poly in element._cell.polygons:
                self._cell.add(gdstk.Polygon(poly.points, layer=poly.layer, datatype=poly.datatype))
            for lab in element._cell.labels:
                self._cell.add(gdstk.Label(
                    text=lab.text, origin=lab.origin, layer=lab.layer,
                    texttype=lab.texttype, anchor=getattr(lab, "anchor", "o"),
                    rotation=getattr(lab, "rotation", 0),
                    magnification=getattr(lab, "magnification", 1),
                    x_reflection=getattr(lab, "x_reflection", False),
                ))
            for ref in list(element._cell.references):
                self._cell.add(ref)
        elif isinstance(element, (list, tuple)):
            for e in element:
                self.add(e)
        else:
            self._cell.add(element)
        return self

    def _register_reference(self, ref: _NativeComponentReference, alias: Optional[str] = None) -> None:
        """Used by `import_gds` and friends to attach an already-built ref."""
        if alias is not None:
            self._named_references[alias] = ref

    def __lshift__(self, element: "_NativeComponent") -> _NativeComponentReference:
        """`comp << other` → add a reference and return it (gdsfactory's
        most-used shorthand)."""
        return self.add_ref(element)

    def __getitem__(self, key: str):
        """`comp[name]` → port lookup first, then named reference, matching
        gdsfactory's behaviour."""
        if key in self.ports:
            return self.ports[key]
        if key in self._named_references:
            return self._named_references[key]
        raise KeyError(f"{key!r} not in ports or named references of {self.name!r}")

    @property
    def references(self) -> list[_NativeComponentReference]:
        return list(self._references)

    @property
    def named_references(self) -> dict[str, _NativeComponentReference]:
        return dict(self._named_references)

    # --- Convenience refs ------------------------------------------
    def ref(
        self,
        position: Tuple[float, float] = (0, 0),
        port_id: Optional[str] = None,
        rotation: float = 0.0,
        h_mirror: bool = False,
        v_mirror: bool = False,
    ) -> _NativeComponentReference:
        """Standalone reference (not attached to any parent), positioned
        at `position` with optional rotation/mirrors. Glayout uses this
        via `util.geometry.prec_ref_center` for one-off placements."""
        r = _NativeComponentReference(self)
        if port_id and port_id not in self.ports:
            raise ValueError(f"port {port_id} not in {list(self.ports)}")
        origin = self.ports[port_id].center if port_id else (0, 0)
        if h_mirror:
            r.x_reflection = True  # gdstk x_reflection is a horizontal-axis flip
        if rotation:
            r.rotate(rotation, origin)
        r.move(origin, position)
        return r

    def ref_center(self, position: Tuple[float, float] = (0, 0)) -> _NativeComponentReference:
        """Reference centered at `position`."""
        cx, cy = self.center
        r = _NativeComponentReference(self)
        r.move((cx, cy), position)
        return r

    # --- copy / flatten / show --------------------------------------
    def copy(self, name: Optional[str] = None) -> "_NativeComponent":
        """Deep copy this Component: polygons, labels, ports, refs, info.
        Mirrors gdsfactory's module-level `copy()` (also reachable as
        `Component.copy()`). When `name` is None, gdsfactory generates a
        unique name; we just append `_copy`."""
        out = _NativeComponent(name=name or (self.name + "_copy"))
        out.info = dict(self.info)
        for poly in self._cell.polygons:
            out._cell.add(gdstk.Polygon(poly.points, layer=poly.layer, datatype=poly.datatype))
        for label in self._cell.labels:
            out._cell.add(gdstk.Label(
                text=label.text, origin=label.origin, anchor=label.anchor,
                magnification=label.magnification, rotation=label.rotation,
                layer=label.layer, texttype=label.texttype,
                x_reflection=label.x_reflection,
            ))
        for port in self.ports.values():
            out.add_port(port=port)
        for ref in self._references:
            new_ref = _NativeComponentReference(
                component=ref.parent,
                origin=ref.origin,
                rotation=ref.rotation,
                magnification=ref.magnification,
                x_reflection=ref.x_reflection,
                columns=ref.columns,
                rows=ref.rows,
                spacing=ref.spacing,
            )
            out.add(new_ref)
        return out

    def flatten(self, single_layer: Optional[_LayerTuple] = None) -> "_NativeComponent":
        """Return a new Component with all references resolved into raw
        polygons in the top cell. Labels, ports, and info are preserved."""
        flat = _NativeComponent(name=self.name + "_flat")
        flat.info = dict(self.info)
        # `gdstk.Cell.copy(name, deep_copy=True)` copies the dependency
        # tree, then `.flatten()` resolves all references in place.
        flat._cell = self._cell.copy(flat._cell.name, deep_copy=True)
        flat._cell.flatten()
        if single_layer is not None:
            gl, gd = single_layer
            for poly in flat._cell.polygons:
                poly.layer = gl
                poly.datatype = gd
        # Carry ports forward (they're metadata, not geometry).
        for p in self.ports.values():
            flat.add_port(port=p)
        return flat

    # --- Compatibility helpers --------------------------------------
    def get_ports_list(self, **kwargs) -> list["_NativePort"]:
        """Mirrors gdsfactory.Component.get_ports_list. Sorts clockwise to
        match gdsfactory's default `select_ports → sort_ports_clockwise`
        — necessary for rename_ports_by_orientation collision tie-breaks
        to match gf."""
        ports = self.ports
        if kwargs:
            from gdsfactory.port import select_ports
            return list(select_ports(ports, **kwargs).values())
        from gdsfactory.port import sort_ports_clockwise
        return list(sort_ports_clockwise(ports).values())

    # --- Component-level move/transform passthroughs ---------------
    # Some glayout code calls move/movex/movey/rotate directly on the
    # returned Component (not on a ref). gdsfactory.Component has these
    # via _GeometryHelper. We translate them into a fresh wrapping
    # reference, matching gdsfactory's behaviour.
    def move(self, *args, **kwargs):
        from glayout.backend.functions import move as _move
        return _move(self, *args, **kwargs)

    def movex(self, dx: float) -> "_NativeComponent":
        from glayout.backend.functions import move as _move
        return _move(self, destination=(dx, 0))

    def movey(self, dy: float) -> "_NativeComponent":
        from glayout.backend.functions import move as _move
        return _move(self, destination=(0, dy))

    def add_padding(
        self,
        layers: Sequence[_LayerTuple] = ((1, 0),),
        default: float = 50.0,
        top: Optional[float] = None,
        bottom: Optional[float] = None,
        right: Optional[float] = None,
        left: Optional[float] = None,
        **_ignored,
    ) -> "_NativeComponent":
        """In-place: add padding polygons on each layer in `layers`, expanding
        the existing bbox by the per-side amounts (default 50µm). Mirrors
        gdsfactory.add_padding.add_padding (delegated to via Component method)."""
        from glayout.backend.add_padding import get_padding_points
        points = get_padding_points(
            self, default=default, top=top, bottom=bottom, right=right, left=left,
        )
        for layer in layers:
            self.add_polygon(points, layer=layer)
        return self

    def area(self, layer: Optional[_LayerTuple] = None) -> float:
        """Total polygon area in µm². With `layer=None`, sums all polygons.
        Matches gdsfactory.Component.area for the no-arg case (only usage
        in glayout)."""
        if layer is None:
            return float(sum(p.area() for p in self._cell.polygons))
        gl, gd = layer
        return float(sum(p.area() for p in self._cell.polygons if (p.layer, p.datatype) == (gl, gd)))

    # --- lock/unlock — gdsfactory's @cell locks after build to prevent
    # accidental mutation of cached cells (which would silently pollute
    # subsequent cache retrievals). Glayout calls .unlock() before
    # legitimate mutations (e.g. add_df_labels in diff_pair.py).
    def lock(self) -> "_NativeComponent":
        self._locked = True
        return self
    def unlock(self) -> "_NativeComponent":
        self._locked = False
        return self

    def show(self, *args, **kwargs) -> None:
        """No-op viewer hook. gdsfactory's `show()` pipes to klive; glayout
        only calls this in `transmission_gate.py` for interactive debugging
        and we don't want to depend on klive in CI."""
        return None

    # --- IO ---------------------------------------------------------
    def write_gds(
        self,
        gdspath: Union[str, Path],
        unit: Optional[float] = None,
        precision: Optional[float] = None,
    ) -> Path:
        """Write a single-cell GDS. Unit/precision default to the active
        PDK's `gds_write_settings` (sky130 sets precision=5e-9; defaulting
        to 1e-9 here causes OFFGRID violations because polygon coords
        round to a finer grid than the PDK rules expect)."""
        if unit is None or precision is None:
            from glayout.backend._active import get_gds_write_unit_precision
            _u, _p = get_gds_write_unit_precision()
            if unit is None: unit = _u
            if precision is None: precision = _p
        gdspath = Path(gdspath)
        lib = gdstk.Library(unit=unit, precision=precision)
        lib.add(self._cell, *self._cell.dependencies(True))
        lib.write_gds(str(gdspath))
        return gdspath


# --- Active exports — CUTOVER. ---
# Pick at import time based on GLAYOUT_BACKEND env var so downstream
# `from glayout.backend.component import Component` callers get the
# right class without re-importing. The env var is the ONLY reliable
# switch because by the time `set_backend()` could fire, glayout's
# cell modules have usually already imported Component.
import os as _os
if _os.environ.get("GLAYOUT_BACKEND", "").strip().lower() == "gdstk":
    Component = _NativeComponent
    copy = _native_copy
else:
    Component = _GFComponent
    copy = _gf_copy


__all__ = [
    "Component", "copy",
    "_NativeComponent", "_NativePort", "_NativeComponentReference",
]
