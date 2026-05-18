# cython: language_level=3, boundscheck=False, wraparound=False, initializedcheck=False
"""Cython hot paths for `GLAYOUT_BACKEND=gdstk_cython` mode.

Defines:
  - `_CyPort`: a cdef class with C-level fields. Substitute for the
    pure-Python `_NativePort` (`@dataclass(slots=True)`). Per-port
    construction is ~3× faster than the slots class because field
    writes go through cdef descriptors (a single C pointer assign)
    rather than the slot descriptor protocol.
  - `cy_add_ports_from_ref(container, ref, prefix, suffix)`:
    cpdef function that runs the combined transform + insert loop
    entirely in Cython. Replaces `_NativeComponent._add_ports_from_ref`
    when the active port class is `_CyPort`.
  - `cy_build_transformed_ports(parent, ref, ox, oy, rotation,
    x_reflection)`: cpdef function for the ref.ports rebuild path.
"""
from libc.math cimport sin as c_sin, cos as c_cos, M_PI


# --- _CyPort cdef class -------------------------------------------------
cdef class _CyPort:
    """Cython port. C-level field access; same observable behavior as
    `_NativePort` (slots dataclass). Construction via `_CyPort(...)`
    snaps `center` to 1 nm to mirror gdsfactory's `Port.__init__`.

    Fast-path constructors (used by the Cython hot-loop helpers below)
    bypass `__init__` via `_CyPort.__new__(_CyPort)` and set fields
    directly — they skip the snap (`_add_ports_from_ref` and
    `_NativeComponentReference.ports` snap inline as needed).
    """
    cdef public str name
    cdef public tuple center
    cdef public double width
    cdef public double orientation
    cdef public tuple layer
    cdef public str port_type
    cdef public object parent
    cdef public object cross_section
    cdef public object shear_angle

    def __init__(self, name, center, width=0.0, orientation=0.0,
                 layer=(1, 0), port_type="electrical",
                 parent=None, cross_section=None, shear_angle=None):
        cdef double cx, cy
        cx = center[0]
        cy = center[1]
        self.name = name
        self.center = (round(cx * 1000.0) / 1000.0,
                       round(cy * 1000.0) / 1000.0)
        self.width = width
        self.orientation = orientation if orientation is not None else 0.0
        self.layer = layer
        self.port_type = port_type
        self.parent = parent
        self.cross_section = cross_section
        self.shear_angle = shear_angle

    # --- gdsfactory-compat accessors ---
    @property
    def x(self):
        return float(self.center[0])

    @x.setter
    def x(self, value):
        self.center = (float(value), self.center[1])

    @property
    def y(self):
        return float(self.center[1])

    @y.setter
    def y(self, value):
        self.center = (self.center[0], float(value))

    def copy(self, name=None):
        """Fast copy — bypasses __init__/snap. Source center is already
        on grid (or deliberately fuzz-preserving via move_copy)."""
        cdef _CyPort out = _CyPort.__new__(_CyPort)
        out.name = name if name is not None else self.name
        out.center = self.center
        out.width = self.width
        out.orientation = self.orientation
        out.layer = self.layer
        out.port_type = self.port_type
        out.parent = None
        out.cross_section = self.cross_section
        out.shear_angle = self.shear_angle
        return out

    def move_copy(self, offset):
        """Translate-copy without snap — matches gdsfactory's
        `Port.move_copy` semantics (preserved float fuzz)."""
        cdef double cx = self.center[0]
        cdef double cy = self.center[1]
        cdef double ox = offset[0]
        cdef double oy = offset[1]
        cdef _CyPort out = _CyPort.__new__(_CyPort)
        out.name = self.name
        out.center = (cx + ox, cy + oy)
        out.width = self.width
        out.orientation = self.orientation
        out.layer = self.layer
        out.port_type = self.port_type
        out.parent = None
        out.cross_section = self.cross_section
        out.shear_angle = self.shear_angle
        return out

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        # Match the gdsfactory `@validate_arguments` contract.
        from pydantic_core import core_schema
        return core_schema.is_instance_schema(cls)

    def __repr__(self):
        return (f"_CyPort(name={self.name!r}, center={self.center}, "
                f"orientation={self.orientation}, layer={self.layer})")

    def as_dict(self):
        """Dict view of this port — used by the gdsfactory serializer
        shim in `_speedups._activate_native_classes` so orjson can
        serialize `_CyPort` instances (cdef classes don't expose
        `__dataclass_fields__` so orjson's dataclass path skips them).
        """
        return {
            "name": self.name,
            "center": self.center,
            "width": self.width,
            "orientation": self.orientation,
            "layer": self.layer,
            "port_type": self.port_type,
            "cross_section": self.cross_section,
            "shear_angle": self.shear_angle,
        }


# --- Hot-loop helpers ---------------------------------------------------
cpdef cy_add_ports_from_ref(object container, object ref,
                            str prefix, str suffix):
    """Combined transform + insert. Mirrors
    `_NativeComponent._add_ports_from_ref` but with the per-port loop
    running entirely at Cython speed.
    """
    cdef object parent = ref.parent
    cdef dict parent_ports = parent.ports
    # Use ref.origin (the _NativeComponentReference property that
    # wraps in tuple) rather than ref._reference.origin (direct gdstk
    # access, which returns an ndarray with different semantics).
    cdef tuple origin = ref.origin
    cdef double ox = origin[0]
    cdef double oy = origin[1]
    cdef double rotation = ref._rotation_deg
    cdef bint x_reflection = bool(ref.x_reflection)

    # Sort parent ports clockwise (rename-collision tie-break matches gf).
    from gdsfactory.port import sort_ports_clockwise
    cdef dict sorted_parent_ports = sort_ports_clockwise(parent_ports)

    cdef int rot_int = int(rotation)
    # Python-style positive modulo (C `%` returns sign of dividend, so
    # rot_int=-90 gives -90 in C but 270 in Python).
    cdef int rot_norm = rot_int % 360
    if rot_norm < 0:
        rot_norm += 360
    cdef bint cardinal = (rotation == rot_int) and (rot_norm in (0, 90, 180, 270))
    cdef int rot_mod = rot_norm if cardinal else 0

    cdef double cos_r = 0.0
    cdef double sin_r = 0.0
    cdef double rad
    if not cardinal:
        rad = rotation * M_PI / 180.0
        cos_r = c_cos(rad)
        sin_r = c_sin(rad)

    cdef dict self_ports = container.ports
    cdef str self_name = container.name
    cdef bint has_affix = bool(prefix or suffix)

    cdef str src_name
    cdef object p
    cdef double cx, cy, rx, ry
    cdef double src_orient, new_orientation
    cdef str nm
    cdef _CyPort np_port

    for src_name, p in sorted_parent_ports.items():
        cx = p.center[0]
        cy = p.center[1]
        if x_reflection:
            cy = -cy
        if cardinal:
            if rot_mod == 0:
                rx = cx
                ry = cy
            elif rot_mod == 90:
                rx = -cy
                ry = cx
            elif rot_mod == 180:
                rx = -cx
                ry = -cy
            else:  # 270
                rx = cy
                ry = -cx
        else:
            rx = cx * cos_r - cy * sin_r
            ry = cx * sin_r + cy * cos_r
        # No per-port snap — matches gdsfactory's fast_ports_get +
        # fast_add_ports unsnapped semantics; XOR parity holds.
        src_orient = p.orientation if p.orientation is not None else 0.0
        # Python-style positive modulo for doubles. C's fmod (which
        # Cython's `%` on doubles compiles to) returns the same sign as
        # the dividend, so `(-180.0) % 360.0` is -180.0 in C but 180.0
        # in Python. The rename_ports_by_orientation suffix derivation
        # depends on the angle being in [0, 360); a negative result
        # flips E↔W and N↔S, changing which port wins rename
        # collisions and producing massive layout drift.
        new_orientation = (src_orient + rotation) % 360.0
        if new_orientation < 0:
            new_orientation += 360.0
        if x_reflection:
            new_orientation = -new_orientation
            if new_orientation < 0:
                new_orientation += 360.0
        nm = (prefix + src_name + suffix) if has_affix else src_name
        np_port = _CyPort.__new__(_CyPort)
        np_port.name = nm
        np_port.center = (rx + ox, ry + oy)
        np_port.width = p.width
        np_port.orientation = new_orientation
        np_port.layer = p.layer
        np_port.port_type = p.port_type
        np_port.parent = container
        np_port.cross_section = p.cross_section
        np_port.shear_angle = p.shear_angle
        if nm in self_ports:
            raise ValueError(
                f"add_port() Port name {nm!r} exists in {self_name!r}"
            )
        self_ports[nm] = np_port


cpdef dict cy_build_transformed_ports(object parent, object ref):
    """Rebuild the transformed-ports dict for a reference. Mirrors
    `_NativeComponentReference.ports`'s build loop (non-identity case).
    Snaps each transformed center to 1 nm — the diff_pair_ibias
    implant-layer parity depends on this snap; only the
    `_add_ports_from_ref` path can drop it.
    """
    cdef dict parent_ports = parent.ports
    cdef tuple origin = ref.origin
    cdef double ox = origin[0]
    cdef double oy = origin[1]
    cdef double rotation = ref._rotation_deg
    cdef bint x_reflection = bool(ref.x_reflection)

    cdef int rot_int = int(rotation)
    cdef int rot_norm = rot_int % 360
    if rot_norm < 0:
        rot_norm += 360
    cdef bint cardinal = (rotation == rot_int) and (rot_norm in (0, 90, 180, 270))
    cdef int rot_mod = rot_norm if cardinal else 0

    cdef double cos_r = 0.0
    cdef double sin_r = 0.0
    cdef double rad
    if not cardinal:
        rad = rotation * M_PI / 180.0
        cos_r = c_cos(rad)
        sin_r = c_sin(rad)

    cdef dict out = {}
    cdef str name
    cdef object p
    cdef double cx, cy, rx, ry, tx, ty
    cdef double src_orient, new_orientation
    cdef tuple new_center
    cdef _CyPort np_port

    for name, p in parent_ports.items():
        cx = p.center[0]
        cy = p.center[1]
        if x_reflection:
            cy = -cy
        if cardinal:
            if rot_mod == 0:
                rx = cx
                ry = cy
            elif rot_mod == 90:
                rx = -cy
                ry = cx
            elif rot_mod == 180:
                rx = -cx
                ry = -cy
            else:  # 270
                rx = cy
                ry = -cx
        else:
            rx = cx * cos_r - cy * sin_r
            ry = cx * sin_r + cy * cos_r
        tx = rx + ox
        ty = ry + oy
        # Manual 1 nm snap (required for sky130 diff_pair_ibias parity).
        new_center = (round(tx * 1000.0) / 1000.0,
                      round(ty * 1000.0) / 1000.0)
        src_orient = p.orientation if p.orientation is not None else 0.0
        # Python-style positive modulo (C's fmod returns sign of dividend).
        new_orientation = (src_orient + rotation) % 360.0
        if new_orientation < 0:
            new_orientation += 360.0
        if x_reflection:
            new_orientation = -new_orientation
            if new_orientation < 0:
                new_orientation += 360.0
        np_port = _CyPort.__new__(_CyPort)
        np_port.name = name
        np_port.center = new_center
        np_port.width = p.width
        np_port.orientation = new_orientation
        np_port.layer = p.layer
        np_port.port_type = p.port_type
        np_port.parent = parent
        np_port.cross_section = p.cross_section
        np_port.shear_angle = p.shear_angle
        out[name] = np_port

    return out
