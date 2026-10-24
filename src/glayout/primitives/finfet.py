"""Pitch-based, multifinger FinFET generator using the mapped PDK's technology.

Width is the occupied fin-track height, rounded up to an integer fin count.
Use nfin for unambiguous sizing; it is recorded in layout and SPICE metadata.
Dummy gates are outside ACTIVE and therefore do not add electrical devices.
All dimensions below come from the PDK's FinFETTechnology, not a PDK name.
"""

import math
from dataclasses import asdict
from glayout.backend import Component
from glayout.spice import Netlist


def _rect(comp, layer, x0, y0, x1, y1):
    comp.add_polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer=layer)


def _ports(comp, prefix, bounds, layer):
    x0, y0, x1, y1 = bounds
    for side, center, orientation, width in (
        ("W", (x0, (y0 + y1) / 2), 180, y1 - y0),
        ("N", ((x0 + x1) / 2, y1), 90, x1 - x0),
        ("E", (x1, (y0 + y1) / 2), 0, y1 - y0),
        ("S", ((x0 + x1) / 2, y0), 270, x1 - x0),
    ):
        comp.add_port(
            name=prefix + side,
            center=center,
            orientation=orientation,
            width=width,
            layer=layer,
            port_type="electrical",
        )


def finfet(
    pdk,
    device="nfet",
    width=0.081,
    nfin=None,
    length=None,
    fingers=1,
    multipliers=1,
    with_dummy=True,
    sd_route_topmet="met2",
    gate_route_topmet="met2",
):
    """Generate parallel fingers with shared diffusion and routed D/G/S.

    Gates run vertically and fins horizontally. 90-degree device rotations are
    illegal. The bulk is the implicit substrate/well, as in the ASAP7 models.
    Planar guard rings and deep wells are not generated.
    """
    t = pdk.finfet
    if t is None:
        raise ValueError("PDK must define FinFETTechnology")
    if device not in ("nfet", "pfet"):
        raise ValueError("device must be nfet or pfet")
    for value in (fingers, multipliers):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("fingers and multipliers must be positive integers")
    if length is not None and not math.isclose(length, t.gate_length, abs_tol=1e-10):
        raise ValueError(f"this FinFET PDK requires gate length {t.gate_length} um")
    if sd_route_topmet != "met2" or gate_route_topmet != "met2":
        raise NotImplementedError("FinFET POC terminal routes currently use met2")
    nf = t.fin_count(width, nfin)
    if isinstance(with_dummy, bool):
        dummy = (with_dummy, with_dummy)
    elif len(with_dummy) == 2 and all(isinstance(v, bool) for v in with_dummy):
        dummy = tuple(with_dummy)
    else:
        raise ValueError("with_dummy must be bool or two booleans")
    pdk.activate()
    comp = Component()
    L = pdk.get_glayer
    active_bottom = -t.fin_pitch / 2
    active_top = (nf - 0.5) * t.fin_pitch
    active_left = -t.gate_length / 2 - t.active_gate_extension
    active_right = (
        (fingers - 1) * t.gate_pitch + t.gate_length / 2 + t.active_gate_extension
    )
    half = t.metal_width / 2
    viahalf = t.via_width / 2
    # Rows are separated by whole fin pitches, preserving the global fin phase.
    row_pitch = (
        math.ceil((nf * t.fin_pitch + t.row_separation) / t.fin_pitch) * t.fin_pitch
    )
    first = -1 if dummy[0] else 0
    last = fingers if dummy[1] else fingers - 1
    if fingers == 1 and first == last:
        last += 1
    gate_y0 = active_bottom - t.gate_extension
    gate_y1 = (multipliers - 1) * row_pitch + active_top + t.gate_extension
    for k in range(first, last + 1):
        x = k * t.gate_pitch
        _rect(
            comp,
            L("poly"),
            x - t.gate_length / 2,
            gate_y0,
            x + t.gate_length / 2,
            gate_y1,
        )
    # Continuous gate stripes are cut between rows, rather than drawn as
    # disconnected polygons at the same x coordinate (GATE.AUX.2).
    for row in range(multipliers - 1):
        _rect(
            comp,
            L("gcut"),
            first * t.gate_pitch - t.gate_length / 2 - 0.016,
            row * row_pitch + active_top + t.gate_extension,
            last * t.gate_pitch + t.gate_length / 2 + 0.016,
            (row + 1) * row_pitch + active_bottom - t.gate_extension,
        )
    fin_left = min(active_left, first * t.gate_pitch - t.gate_length / 2)
    fin_right = max(
        active_right,
        last * t.gate_pitch + t.gate_length / 2,
        fin_left + t.fin_min_length,
    )
    for k in range(round((multipliers - 1) * row_pitch / t.fin_pitch) + nf):
        y = k * t.fin_pitch
        _rect(
            comp,
            L("fin"),
            fin_left,
            y - t.fin_width / 2,
            fin_right,
            y + t.fin_width / 2,
        )
    terminals = {"source": [], "drain": [], "gate": []}
    for row in range(multipliers):
        dy = row * row_pitch

        def box(layer, x0, y0, x1, y1):
            _rect(comp, L(layer), x0, y0 + dy, x1, y1 + dy)

        box("active_diff", active_left, active_bottom, active_right, active_top)
        gate_y0 = active_bottom - t.gate_extension
        gate_y1 = active_top + t.gate_extension
        # Extend SELECT and WELL past both ACTIVE and all uncut gates.
        sx0 = min(
            active_left - t.select_enclosure,
            first * t.gate_pitch - t.gate_length / 2 - t.select_enclosure,
        )
        sx1 = max(
            active_right + t.select_enclosure,
            last * t.gate_pitch + t.gate_length / 2 + t.select_enclosure,
        )
        sy0, sy1 = gate_y0 - t.select_enclosure, gate_y1 + t.select_enclosure
        box("n+s/d" if device == "nfet" else "p+s/d", sx0, sy0, sx1, sy1)
        if device == "pfet":
            box("nwell", sx0, sy0, sx1, sy1)
        # SDT is the contact to diffusion; LISD connects it to V0/M1.
        source_y = active_top + t.route_pitch
        drain_y = active_top + 2 * t.route_pitch
        for k in range(fingers + 1):
            x = (k - 0.5) * t.gate_pitch
            for layer in ("sdt", "lisd"):
                box(
                    layer,
                    x - t.sd_width / 2,
                    active_bottom,
                    x + t.sd_width / 2,
                    active_top,
                )
            box("mcon", x - viahalf, -viahalf, x + viahalf, viahalf)
            by = source_y if k % 2 == 0 else drain_y
            box(
                "met1",
                x - half,
                -viahalf - t.manufacturing_grid * 10,
                x + half,
                by + viahalf + t.manufacturing_grid * 10,
            )
            box("via1", x - viahalf, by - viahalf, x + viahalf, by + viahalf)
        for name, parity, y in (("source", 0, source_y), ("drain", 1, drain_y)):
            xs = [
                (k - 0.5) * t.gate_pitch for k in range(fingers + 1) if k % 2 == parity
            ]
            bounds = (
                min(xs) - viahalf - 0.005,
                y - half,
                max(xs) + viahalf + 0.005,
                y + half,
            )
            box("met2", *bounds)
            world = (bounds[0], bounds[1] + dy, bounds[2], bounds[3] + dy)
            _ports(comp, f"multiplier_{row}_{name}_", world, L("met2"))
            terminals[name].append(world)
        # LIG overlaps the gate beyond ACTIVE, avoiding the SDT/LISD contacts.
        gate_y = active_bottom - t.route_pitch
        for k in range(fingers):
            x = k * t.gate_pitch
            box(
                "lig",
                x - t.lig_width / 2,
                gate_y - t.lig_height / 2,
                x + t.lig_width / 2,
                gate_y + t.lig_height / 2,
            )
            box("mcon", x - viahalf, gate_y - viahalf, x + viahalf, gate_y + viahalf)
            box("via1", x - viahalf, gate_y - viahalf, x + viahalf, gate_y + viahalf)
        bounds = (
            -viahalf - 0.005,
            gate_y - half,
            (fingers - 1) * t.gate_pitch + viahalf + 0.005,
            gate_y + half,
        )
        box("met1", *bounds)
        box("met2", *bounds)
        world = (bounds[0], bounds[1] + dy, bounds[2], bounds[3] + dy)
        _ports(comp, f"multiplier_{row}_gate_", world, L("met2"))
        terminals["gate"].append(world)
    # Join multiplier rows on M3; separate x tracks for D/G/S.
    for index, (name, rectangles) in enumerate(terminals.items()):
        if multipliers > 1:
            x = active_left - (index + 2) * t.route_pitch
            ys = [(b[1] + b[3]) / 2 for b in rectangles]
            for bounds, y in zip(rectangles, ys):
                _rect(comp, L("met2"), x - 0.014, y - half, bounds[2], y + half)
                _rect(
                    comp, L("via2"), x - viahalf, y - viahalf, x + viahalf, y + viahalf
                )
            _rect(
                comp, L("met3"), x - 0.014, min(ys) - 0.014, x + 0.014, max(ys) + 0.014
            )
        _ports(comp, name + "_", rectangles[0], L("met2"))
    for name, terminal in [("D", "drain"), ("G", "gate"), ("S", "source")]:
        port = comp.ports[terminal + "_W"]
        comp.add_label(
            name,
            position=(port.center[0] + 0.007, port.center[1]),
            layer=pdk.get_glayer("met2_label"),
        )
    comp.info["finfet_technology"] = asdict(t)
    comp.info["finfet"] = {
        "nfin": nf,
        "fingers": fingers,
        "multipliers": multipliers,
        "length": t.gate_length,
        "width": nf * t.fin_pitch,
    }
    comp.info["netlist"] = finfet_netlist(pdk, device, nf, fingers, multipliers)
    t.validate(comp)
    return comp


def finfet_netlist(pdk, device, nfin, fingers, multipliers):
    model = pdk.models[device]
    # Explicit parallel devices, each with BSIM-CMG nfin (not planar W).
    lines = [".subckt {circuit_name} {nodes}"]
    lines += [
        f"M{i} D G S B {model} l={pdk.finfet.gate_length}u nfin={nfin}"
        for i in range(fingers * multipliers)
    ]
    lines += [".ends {circuit_name}"]
    return Netlist(
        circuit_name="NFET" if device == "nfet" else "PFET",
        nodes=["D", "G", "S", "B", "DUM"],
        source_netlist="\n".join(lines),
        parameters={
            "nfin": nfin,
            "length": pdk.finfet.gate_length,
            "width": nfin * pdk.finfet.fin_pitch,
        },
    )
