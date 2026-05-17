"""
Glayout - A PDK-agnostic layout automation framework for analog circuit design
"""

# gdsfactory 7.x post-7.7 tightened `Component.info` validation to only accept
# int/float/str/Sequence values, and tightened cell-name JSON serialization to
# error on unknown types. glayout stores `Netlist` instances under
# `component.info['netlist']` in ~25 cells. Two surgical patches keep that
# pattern working without touching every cell:
#  (1) Replace gdsfactory's `Info` model with a subclass whose validator
#      exempts the `netlist` key (the existing `schematic` exemption uses the
#      same mechanism). Component.__init__ looks Info up by module attribute,
#      so the swap takes effect for every new component without rebuilding the
#      class graph.
#  (2) Wrap `serialization.clean_value_json` so any Netlist instance becomes a
#      stable string. When a parent cell's auto-name walks a child component's
#      `info.model_dump()` it would otherwise trip on the unserializable
#      Netlist object during orjson.dumps.
try:
    from gdsfactory import component_layout as _gf_cl  # type: ignore
    from gdsfactory import component as _gf_component  # type: ignore
    from gdsfactory import serialization as _gf_serialization  # type: ignore
except Exception:
    _gf_cl = None
    _gf_component = None
    _gf_serialization = None

if _gf_cl is not None and not getattr(_gf_cl.Info, "_glayout_netlist_patch", False):
    from pydantic import BaseModel as _PydBaseModel
    from typing import Iterable as _Iterable

    class _GlayoutRelaxedInfo(_PydBaseModel, extra="allow", validate_assignment=True):
        """Drop-in replacement for gdsfactory.component_layout.Info that drops
        the int/float/str/Sequence restriction. glayout stores Netlist objects
        and small dicts under several info keys (netlist, netlist_obj,
        netlist_data) and the serialization patch below handles the cell-name
        JSON encoding for the Netlist type — the value-type guard in the
        upstream Info model is the only thing that stood in the way."""

        def __getitem__(self, key):
            return getattr(self, key)

        def __setitem__(self, key, val):
            if val is not None:
                setattr(self, key, val)

        def __contains__(self, key):
            return hasattr(self, key)

        def get(self, key, default=None):
            return getattr(self, key) if hasattr(self, key) else default

        def update(self, data):
            if isinstance(data, dict):
                for k, v in data.items():
                    self[k] = v
            elif isinstance(data, _GlayoutRelaxedInfo):
                for k, v in data.model_dump().items():
                    self[k] = v
            elif isinstance(data, _Iterable):
                for k, v in data:
                    self[k] = v
            else:
                raise TypeError("Unsupported data type for update")

    _GlayoutRelaxedInfo._glayout_netlist_patch = True
    _gf_cl.Info = _GlayoutRelaxedInfo
    if _gf_component is not None:
        _gf_component.Info = _GlayoutRelaxedInfo

if _gf_serialization is not None and not getattr(_gf_serialization, "_glayout_netlist_patch", False):
    _orig_clean_value_json = _gf_serialization.clean_value_json

    def _glayout_clean_value_json(value, *args, **kwargs):
        try:
            from glayout.spice.netlist import Netlist as _Netlist
        except Exception:
            _Netlist = None
        if _Netlist is not None and isinstance(value, _Netlist):
            return f"<Netlist {getattr(value, 'circuit_name', '') or ''}>"
        return _orig_clean_value_json(value, *args, **kwargs)

    _gf_serialization.clean_value_json = _glayout_clean_value_json
    _gf_serialization._glayout_netlist_patch = True

try:
    from .pdk.mappedpdk import MappedPDK
except Exception as e:
    print(f"[WARN] gdsfactory import failed - switching to a minimal DummyPdk ({e})")
    print("[INFO] Switching to a minimal DummyPdk for limited functionality.")

    class DummyPdk:
        """Minimal fallback to keep flow running if gdsfactory isn't installed."""

        def activate(self):
            print("[INFO] DummyPdk active. Limited functionality only.")

    MappedPDK = DummyPdk()

# Other PDKs
try:
    from .pdk.sky130_mapped import sky130_mapped_pdk as sky130
except Exception:
    sky130 = None
try:
    from .pdk.gf180_mapped import gf180_mapped_pdk as gf180
except Exception:
    gf180 = None
try:
    from .pdk.ihp130_mapped import ihp130_mapped_pdk as ihp130
except Exception:
    ihp130 = None

# Primitive components
from .primitives.via_gen import via_stack, via_array
from .primitives.fet import nmos, pmos, multiplier
from .primitives.guardring import tapring
from .primitives.mimcap import mimcap, mimcap_array
from .primitives.resistor import resistor

# SPICE and utils
from .spice import Netlist

from .util.port_utils import (
    PortTree, parse_direction, proc_angle, ports_inline, ports_parallel,
    rename_component_ports, rename_ports_by_list, rename_ports_by_orientation,
    remove_ports_with_prefix, add_ports_perimeter, get_orientation,
    assert_port_manhattan, assert_ports_perpindicular, set_port_orientation,
    set_port_width, print_ports, create_private_ports, print_port_tree_all_cells
)

from .util.comp_utils import (
    move, movex, movey, align_comp_to_port, evaluate_bbox, center_to_edge_distance,
    to_float, to_decimal, prec_array, prec_center, prec_ref_center,
    get_padding_points_cc, get_primitive_rectangle
)

from .util.snap_to_grid import component_snap_to_grid

# Routing
from .routing.c_route import c_route
from .routing.L_route import L_route
from .routing.straight_route import straight_route
from .routing.smart_route import smart_route

# Placement
from .placement.common_centroid_ab_ba import common_centroid_ab_ba
from .placement.four_transistor_interdigitized import generic_4T_interdigitzed
from .placement.two_transistor_interdigitized import (
    two_transistor_interdigitized, two_pfet_interdigitized,
    two_nfet_interdigitized, macro_two_transistor_interdigitized
)
from .placement.two_transistor_place import two_transistor_place

__version__ = "0.1.1"

__all__ = [
    "Netlist",
    "mimcap",
    "mimcap_array",
    "resistor",
    "evaluate_bbox",
    "center_to_edge_distance",
    "to_float",
    "to_decimal",
    "prec_array",
    "prec_center",
    "prec_ref_center",
    "get_padding_points_cc",
    "get_primitive_rectangle",
    "parse_direction",
    "proc_angle",
    "ports_inline",
    "ports_parallel",
    "rename_component_ports",
    "rename_ports_by_list",
    "remove_ports_with_prefix",
    "add_ports_perimeter",
    "get_orientation",
    "assert_port_manhattan",
    "assert_ports_perpindicular",
    "set_port_orientation",
    "set_port_width",
    "print_ports",
    "component_snap_to_grid",
    "two_transistor_place",
    "two_transistor_interdigitized",
    "two_pfet_interdigitized",
    "two_nfet_interdigitized",
    "macro_two_transistor_interdigitized",
    "generic_4T_interdigitzed",
    "smart_route",
    "c_route",
    "L_route",
    "straight_route",
    "via_stack",
    "via_array",
    "nmos", 
    "pmos", 
    "multiplier",
    "tapring",
    "PortTree",
    "rename_ports_by_orientation",
    "move",
    "movex",
    "movey",
    "align_comp_to_port",
    "sky130",
    "gf180",
]
