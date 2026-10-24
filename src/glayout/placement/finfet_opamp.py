"""ASAP7 placement for the existing two-stage opamp schematic, with MOM caps."""

from glayout.backend import Component
from glayout.spice import Netlist
from glayout.primitives.finfet import finfet_netlist
from glayout.primitives.momcap import momcap
from glayout.placement.finfet_circuit import finfet_circuit


def finfet_opamp(
    pdk,
    half_diffpair_params,
    diffpair_bias,
    half_common_source_params,
    half_common_source_bias,
    half_pload,
    mim_cap_size,
    mim_cap_rows,
    rmult=1,
    with_antenna_diode_on_diffinputs=0,
):
    from glayout.cells.composite.diffpair_cmirror_bias.diff_pair_cmirrorbias import (
        diff_pair_ibias,
    )
    from glayout.cells.composite.differential_to_single_ended_converter.differential_to_single_ended_converter import (
        differential_to_single_ended_converter_netlist,
    )
    from glayout.cells.composite.opamp.row_csamplifier_diff_to_single_ended_converter import (
        row_csamplifier_diff_to_single_ended_converter_netlist,
    )
    from glayout.cells.composite.opamp.opamp_twostage import (
        opamp_gain_stage_netlist,
        opamp_twostage_netlist,
    )

    t = pdk.finfet
    for params in (
        half_diffpair_params,
        diffpair_bias,
        half_common_source_params,
        half_common_source_bias,
        half_pload,
    ):
        if abs(params[1] - t.gate_length) > 1e-10:
            raise ValueError("unsupported FinFET gate length")
    if half_common_source_bias[3] < 2:
        raise ValueError("common-source bias multipliers must be >=2")
    if (
        isinstance(mim_cap_rows, bool)
        or not isinstance(mim_cap_rows, int)
        or mim_cap_rows < 1
    ):
        raise ValueError("capacitor rows must be a positive integer")
    input_stage = diff_pair_ibias(
        pdk,
        half_diffpair_params,
        diffpair_bias,
        rmult,
        with_antenna_diode_on_diffinputs,
    ).info["netlist"]
    input_stage.circuit_name = "INPUT_STAGE"
    dse = Component()
    dse.info["netlist"] = differential_to_single_ended_converter_netlist(
        pdk, half_pload
    )
    diff_cs = row_csamplifier_diff_to_single_ended_converter_netlist(dse)
    pw, pl, pf, pm = half_common_source_params
    leaf = finfet_netlist(pdk, "pfet", t.fin_count(pw), pf, pm)
    for _ in range(2):
        diff_cs.connect_netlist(
            leaf,
            [("D", "VOUT"), ("G", "VIN2"), ("S", "VSS"), ("B", "VSS"), ("DUM", "VSS")],
        )
    nw, nl, nf, nm = half_common_source_bias
    bias = Netlist(circuit_name="CS_BIAS", nodes=["VREF", "VOUT", "VSS", "B"])
    for drain, count in [("VREF", 2 * nf), ("VOUT", 2 * nf * nm)]:
        leaf = finfet_netlist(pdk, "nfet", t.fin_count(nw), count, 1)
        bias.connect_netlist(
            leaf, [("D", drain), ("G", "VREF"), ("S", "VSS"), ("B", "B"), ("DUM", "B")]
        )
    # Keep the original compensation topology and array count. In FinFET
    # mode the historical mim_cap_* arguments size custom MOM capacitors.
    cap = momcap(pdk, size=mim_cap_size)
    caparray = Netlist(circuit_name="MOM_ARRAY", nodes=["V1", "V2"])
    for _ in range(2 * mim_cap_rows):
        caparray.connect_netlist(cap.info["netlist"], [("V1", "V1"), ("V2", "V2")])
    gain = opamp_gain_stage_netlist(caparray, diff_cs, bias)
    netlist = opamp_twostage_netlist(input_stage, gain)
    result = finfet_circuit(pdk, netlist, capacitors=[cap])
    result.info["compensation"] = {
        "type": "MOM",
        "count": 2 * mim_cap_rows,
        "nominal_capacitance": 2 * mim_cap_rows * cap.info["momcap"]["capacitance"],
    }
    return result
