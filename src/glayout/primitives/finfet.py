"""FinFET device geometry behind the existing planar primitive interface.

Generic 'poly' ports are local gate interconnect (LIG); physical gates are
separate fixed-width stripes. Everything above fet.__gen_fingers_macro keeps
its original placement, finger routing, and public port names.
"""

from dataclasses import asdict
import math
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


def fingers(
    pdk, rmult, fingers, length, width, poly_height, sdlayer, inter_finger_topmet
):
    from glayout.primitives.via_gen import via_stack

    t = pdk.finfet
    flavor = pdk.get_finfet_flavor()
    if length is not None and not math.isclose(length, t.gate_length, abs_tol=1e-10):
        raise ValueError("unsupported FinFET gate length")
    nf = t.fin_count(width)
    height = nf * t.fin_pitch
    # Preserve the global fin/gate phase, including even finger/fin counts.
    xoff = t.gate_pitch / 2 if fingers % 2 == 0 else 0
    yoff = t.fin_pitch / 2 if nf % 2 == 0 else 0
    contact_size = max(
        0.028,
        pdk.get_grule("met1")["min_width"],
        pdk.get_grule(inter_finger_topmet)["min_width"],
    )
    pitch = t.gate_pitch * max(
        1,
        math.ceil(
            (rmult * contact_size + pdk.get_grule("met1")["min_separation"])
            / t.gate_pitch
        ),
    )
    xs = [(i - fingers / 2) * pitch + xoff for i in range(fingers + 1)]
    gx = [(xs[i] + xs[i + 1]) / 2 for i in range(fingers)]
    # Quantize routing multiplier pitch to an odd gate-pitch multiple so
    # contact midpoints and gates remain on the same fixed gate lattice.
    if any(abs(x / t.gate_pitch - round(x / t.gate_pitch)) > 1e-7 for x in gx):
        pitch += t.gate_pitch
        xs = [(i - fingers / 2) * pitch + xoff for i in range(fingers + 1)]
        gx = [(xs[i] + xs[i + 1]) / 2 for i in range(fingers)]
    left, right = xs[0] - 0.012, xs[-1] + 0.012
    bottom, top = -height / 2 + yoff, height / 2 + yoff
    c = Component()
    _rect(c, pdk.get_glayer("active_diff"), left, bottom, right, top)
    _ports(c, "diff_", (left, bottom, right, top), pdk.get_glayer("active_diff"))
    gate_bottom = bottom - t.gate_extension
    gate_top = top + t.gate_extension
    gate_contact_y = gate_bottom + 0.016
    for i, x in enumerate(gx):
        _rect(
            c,
            t.gate_layer,
            x - t.gate_length / 2,
            gate_bottom,
            x + t.gate_length / 2,
            gate_top,
        )
        lig = (x - 0.011, gate_contact_y - 0.011, x + 0.011, gate_contact_y + 0.011)
        # Keep ports inside the landing so half-grid alignment retains overlap.
        overlap = 2 * t.manufacturing_grid
        _rect(
            c,
            pdk.get_glayer("poly"),
            lig[0] - overlap,
            lig[1] - overlap,
            lig[2] + overlap,
            lig[3] + overlap,
        )
        _ports(c, f"row0_col{i}_gate_", lig, pdk.get_glayer("poly"))
    fin_left = min(left, right - t.fin_min_length)
    for i in range(nf):
        y = (i - (nf - 1) / 2) * t.fin_pitch + yoff
        _rect(c, t.fin_layer, fin_left, y - t.fin_width / 2, right, y + t.fin_width / 2)
    select = (
        left - t.select_enclosure,
        gate_bottom - t.select_enclosure,
        right + t.select_enclosure,
        gate_top + t.select_enclosure,
    )
    _rect(c, pdk.get_glayer(sdlayer), *select)
    if flavor.marker is not None:
        _rect(c, pdk.get_glayer(flavor.marker), *select)
    if sdlayer == "p+s/d":
        enc = t.well_enclosure
        _rect(
            c,
            pdk.get_glayer("nwell"),
            select[0] - enc,
            select[1] - enc,
            select[2] + enc,
            select[3] + enc,
        )
    _ports(
        c,
        "plusdoped_",
        (
            left - t.select_enclosure,
            bottom - t.select_enclosure,
            right + t.select_enclosure,
            top + t.select_enclosure,
        ),
        pdk.get_glayer(sdlayer),
    )
    for i, x in enumerate(xs):
        _rect(c, pdk.get_glayer("sdt"), x - 0.012, bottom, x + 0.012, top)
        _rect(c, pdk.get_glayer("lisd"), x - 0.012, bottom, x + 0.012, top)
        _rect(
            c, pdk.get_glayer("mcon"), x - 0.009, yoff - 0.009, x + 0.009, yoff + 0.009
        )
        pad = (
            x - contact_size / 2,
            min(bottom, yoff - contact_size / 2),
            x + contact_size / 2,
            max(top, yoff + contact_size / 2),
        )
        _rect(c, pdk.get_glayer("met1"), *pad)
        prefix = "leftsd_" if i == 0 else f"row0_col{i-1}_rightsd_"
        _ports(c, prefix + "bottom_met_", pad, pdk.get_glayer("met1"))
        _ports(c, prefix + "bottom_layer_", pad, pdk.get_glayer("met1"))
        if inter_finger_topmet != "met1":
            stack = via_stack(pdk, "met1", inter_finger_topmet, fulltop=True)
            ref = c << stack
            ref.move((x, yoff))
            _rect(c, pdk.get_glayer(inter_finger_topmet), *pad)
        _ports(c, prefix + "top_met_", pad, pdk.get_glayer("met1"))
    # A virtual device envelope preserves bbox-centering assumptions without
    # shifting physical fin/gate masks off their manufacturing lattices.
    envelope = t.well_enclosure if sdlayer == "p+s/d" else 0
    bx = (
        math.ceil((max(abs(select[0]), abs(select[2])) + envelope) / t.gate_pitch)
        * t.gate_pitch
    )
    by = (
        math.ceil((max(abs(select[1]), abs(select[3])) + envelope) / t.fin_pitch)
        * t.fin_pitch
    )
    _rect(c, (100, 0), -bx, -by, bx, by)
    c.info["finfet_technology"] = asdict(t)
    c.info["finfet"] = {
        "nfin": nf,
        "fingers": fingers,
        "multipliers": 1,
        "width": height,
        "length": t.gate_length,
        "vt": pdk.finfet_flavor,
        "model": pdk.models["pfet" if sdlayer == "p+s/d" else "nfet"],
    }
    t.validate(c)
    return c


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
    vt=None,
    **kwargs,
):
    from glayout.primitives.fet import nmos, pmos

    fn = nmos if device == "nfet" else pmos if device == "pfet" else None
    if fn is None:
        raise ValueError("device must be nfet or pfet")
    return fn(
        pdk,
        width=width,
        nfin=nfin,
        length=length,
        fingers=fingers,
        multipliers=multipliers,
        with_dummy=with_dummy,
        sd_route_topmet=sd_route_topmet,
        gate_route_topmet=gate_route_topmet,
        vt=vt,
        **kwargs,
    )


def finfet_netlist(
    pdk,
    device,
    nfin,
    fingers,
    multipliers,
    with_dummy=False,
    dummies_tied_to_bulk=False,
):
    pdk.get_finfet_flavor()
    model = pdk.models[device]
    dummy = (with_dummy, with_dummy) if isinstance(with_dummy, bool) else with_dummy
    lines = [".subckt {circuit_name} {nodes}"]
    lines += [
        f"M{i} D G S B {model} l={pdk.finfet.gate_length}u nfin={nfin}"
        for i in range(fingers * multipliers)
    ]
    dummy_node = "B" if dummies_tied_to_bulk else "DUM"
    lines += [
        f"MD{i} {dummy_node} {dummy_node} {dummy_node} B {model} l={pdk.finfet.gate_length}u nfin={nfin}"
        for i in range(sum(dummy) * multipliers)
    ]
    lines += [".ends {circuit_name}"]
    return Netlist(
        circuit_name=("NFET" if device == "nfet" else "PFET")
        + ("" if pdk.finfet_flavor == "rvt" else "_" + pdk.finfet_flavor.upper()),
        nodes=["D", "G", "S", "B", "DUM"],
        source_netlist="\n".join(lines),
        parameters={
            "nfin": nfin,
            "length": pdk.finfet.gate_length,
            "width": nfin * pdk.finfet.fin_pitch,
        },
    )
