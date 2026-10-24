"""Custom interdigitated M3 MOM capacitor with M2 terminal bars.

Capacitance is a nominal edge-length model, NOT a characterized ASAP7 model.
The 100/10 recognition marker is gLayout metadata, not a fabrication mask.
"""

import math
from glayout.backend import Component
from glayout.spice import Netlist
from glayout.primitives.finfet import _rect, _ports

MOM_MARKER = (100, 10)
MOM_PITCH = 0.072
MOM_WIDTH = 0.028
MOM_END_GAP = 0.054
MOM_FF_PER_UM = 0.1


def momcap(pdk, size=(1.0, 1.0), fingers=None):
    if pdk.finfet is None:
        raise ValueError("MOM demo requires a FinFET PDK")
    if any(not math.isfinite(v) or v <= 0 for v in size):
        raise ValueError("MOM dimensions must be finite and positive")
    count = (
        max(2, math.floor((size[0] - MOM_WIDTH) / MOM_PITCH) + 1)
        if fingers is None
        else fingers
    )
    if isinstance(count, bool) or not isinstance(count, int) or count < 2:
        raise ValueError("MOM capacitor requires at least two fingers")
    length = max(0.216, pdk.snap_to_2xgrid(size[1]))
    pdk.activate()
    comp = Component()
    xmax = (count - 1) * MOM_PITCH
    for i in range(count):
        x = i * MOM_PITCH
        y0 = -0.014 if i % 2 == 0 else MOM_END_GAP
        y1 = length - MOM_END_GAP if i % 2 == 0 else length + 0.014
        _rect(comp, pdk.get_glayer("met3"), x - 0.014, y0, x + 0.014, y1)
        y = 0 if i % 2 == 0 else length
        _rect(comp, pdk.get_glayer("via2"), x - 0.009, y - 0.009, x + 0.009, y + 0.009)
    for name, y in [("V1", 0), ("V2", length)]:
        bounds = (-0.024, y - 0.014, xmax + 0.024, y + 0.014)
        _rect(comp, pdk.get_glayer("met2"), *bounds)
        _ports(comp, name + "_", bounds, pdk.get_glayer("met2"))
        comp.add_label(name, position=(0, y), layer=pdk.get_glayer("met2_label"))
    _rect(comp, MOM_MARKER, -0.024, -0.014, xmax + 0.024, length + 0.014)
    value = (count - 1) * (length - 2 * MOM_END_GAP) * MOM_FF_PER_UM * 1e-15
    comp.info["momcap"] = {"fingers": count, "length": length, "capacitance": value}
    comp.info["netlist"] = Netlist(
        circuit_name="MOMCAP",
        nodes=["V1", "V2"],
        source_netlist=".subckt {circuit_name} {nodes}\nC0 V1 V2 "
        + f"{value:.12g}"
        + "\n.ends {circuit_name}",
    )
    from dataclasses import asdict

    comp.info["finfet_technology"] = asdict(pdk.finfet)
    pdk.finfet.validate(comp)
    return comp
