"""Simple channel router for transistor-only FinFET proof-of-concept circuits.

Keep schematics independent of planar placement. Devices are placed on legal
sites, M2 escapes to dedicated M3 columns, and M4 carries one horizontal track
per net. Deliberately spacious, deterministic, and intended for small demos.
"""

import math
from dataclasses import asdict
import re
import tempfile
from pathlib import Path
import klayout.db as k
from glayout.backend import Component
from glayout.primitives.finfet import finfet, _rect, _ports


def finfet_circuit(pdk, netlist, capacitors=()):
    t = pdk.finfet
    if t is None:
        raise ValueError("FinFETTechnology is required")
    pdk.activate()
    text = netlist.generate_netlist()
    normalized = re.sub(
        r"\bnfin=(\d+)\b", lambda m: f"w={int(m[1])*t.fin_pitch:.9g}u", text, flags=re.I
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "reference.spice"
        path.write_text(normalized)
        nl = k.Netlist()
        nl.read(str(path), k.NetlistSpiceReader())
    nl.flatten()
    nl.purge()
    circuit = next(nl.each_circuit_top_down(), None)
    if circuit is None:
        raise ValueError("empty circuit")
    models = {
        value.upper(): key
        for key, value in pdk.models.items()
        if key in ("nfet", "pfet")
    }
    comp = Component()
    comp.name = netlist.circuit_name
    cursor = 0.0
    terminals = {}
    top_y = 0.0
    substrate = None
    for index, dev in enumerate(circuit.each_device()):
        cls = dev.device_class()
        is_cap = isinstance(cls, k.DeviceClassCapacitor)
        if is_cap:
            candidates = [
                c
                for c in capacitors
                if math.isclose(
                    c.info["momcap"]["capacitance"],
                    dev.parameter("C"),
                    rel_tol=1e-9,
                    abs_tol=1e-30,
                )
            ]
            if not candidates:
                raise ValueError("no MOM geometry matching reference capacitance")
            c = candidates[0].copy()
            device = None
            term_map = (("A", "V1"), ("B", "V2"))
        else:
            if cls.name.upper() not in models:
                raise NotImplementedError(
                    f"FinFET circuit router cannot place {cls.name}"
                )
            device = models[cls.name.upper()]
            nfin = round(dev.parameter("W") / t.fin_pitch)
            c = finfet(pdk, device=device, nfin=nfin, length=dev.parameter("L"))
            term_map = (("D", "drain"), ("G", "gate"), ("S", "source"))
        # Child D/G/S labels are standalone pin names, not parent net names.
        if hasattr(c, "_cell"):
            for label in list(c._cell.labels):
                c._cell.remove(label)
        else:
            c.remove_labels()
        origin = t.snap_placement((cursor - c.xmin + 0.540, 0))
        ref = comp << c
        ref.move(origin)
        comp.add_ports(ref.get_ports_list(), prefix=f"M{index}_")
        if not is_cap:
            bulk = dev.net_for_terminal("B").name
            if device == "nfet":
                if substrate is not None and substrate != bulk:
                    raise ValueError(
                        "isolated NMOS bulks need an unsupported deep-well process"
                    )
                substrate = bulk
            # ASAP7 bulk pins are implicit well/substrate pins, not metal contacts.
            comp.add_label(
                bulk,
                position=(origin[0], origin[1]),
                layer=(1 if device == "pfet" else 3, 251),
            )
        for ti, (term, name) in enumerate(term_map):
            net = dev.net_for_terminal(term).name
            port = ref.ports[name + "_W"]
            x = origin[0] + c.xmin - (ti + 1) * 0.108
            y = port.center[1]
            # Horizontal M2 escape and an M2/V2/M3 landing pad.
            _rect(
                comp,
                pdk.get_glayer("met2"),
                x - 0.014,
                y - 0.009,
                port.center[0] + 0.014,
                y + 0.009,
            )
            _rect(
                comp, pdk.get_glayer("met2"), x - 0.009, y - 0.014, x + 0.009, y + 0.014
            )
            _rect(
                comp, pdk.get_glayer("via2"), x - 0.009, y - 0.009, x + 0.009, y + 0.009
            )
            terminals.setdefault(net, []).append((x, y))
        cursor = origin[0] + c.xmax + 0.216
        top_y = max(top_y, ref.ymax)
    base = math.ceil((top_y + 0.192) / 0.048) * 0.048
    pins = {p.name().upper() for p in circuit.each_pin()}
    for index, (net, points) in enumerate(terminals.items()):
        y = base + index * 0.192
        for x, start_y in points:
            # V3 has the full M4 track width (24 nm).
            _rect(
                comp,
                pdk.get_glayer("met3"),
                x - 0.014,
                start_y - 0.014,
                x + 0.014,
                y + 0.017,
            )
            _rect(
                comp, pdk.get_glayer("via3"), x - 0.009, y - 0.012, x + 0.009, y + 0.012
            )
        xs = [p[0] for p in points]
        bounds = (min(xs) - 0.024, y - 0.012, max(xs) + 0.024, y + 0.012)
        _rect(comp, pdk.get_glayer("met4"), *bounds)
        if net.upper() in pins:
            comp.add_label(
                net, position=(min(xs), y), layer=pdk.get_glayer("met4_label")
            )
            _ports(comp, net + "_", bounds, pdk.get_glayer("met4"))
    comp.info["finfet_technology"] = asdict(t)
    comp.info["netlist"] = netlist
    t.validate(comp)
    return comp
