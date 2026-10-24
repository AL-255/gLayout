"""Electrical and placement checks for the FinFET proof of concept."""

from dataclasses import replace
import math
import pytest
import gdstk
from glayout import asap7, nmos, pmos
from glayout.backend import Component
from glayout.pdk.finfet import FinFETTechnology
from glayout.pdk.asap7_mapped.verification import drc, lvs


@pytest.mark.parametrize("builder", [nmos, pmos])
@pytest.mark.parametrize("nfin", [1, 3, 8])
@pytest.mark.parametrize("fingers", [1, 2, 4])
@pytest.mark.parametrize("multipliers", [1, 2])
def test_finfet_electrical_matrix(tmp_path, builder, nfin, fingers, multipliers):
    c = builder(asap7, nfin=nfin, fingers=fingers, multipliers=multipliers)
    gds = tmp_path / "fet.gds"
    spice = tmp_path / "fet.spice"
    c.write_gds(gds)
    spice.write_text(c.info["netlist"].generate_netlist())
    assert c.info["finfet"]["nfin"] == nfin
    assert gdstk.read_gds(gds).precision == pytest.approx(0.5e-9)
    assert drc(gds) == {}
    assert lvs(gds, spice, tmp_path / "lvs")["match"]


def test_placement_guard_catches_hierarchy_and_rotation(tmp_path):
    c = nmos(asap7, nfin=3)
    parent = Component()
    ref = parent << c
    ref.movex(0.001)
    with pytest.raises(ValueError, match="placement grid"):
        parent.write_gds(tmp_path / "off.gds")
    asap7.finfet.place(ref, (0.0541, -0.0271))
    parent.write_gds(tmp_path / "on.gds")
    ref.rotate(90)
    with pytest.raises(ValueError, match="width or rotation"):
        parent.write_gds(tmp_path / "rotated.gds")


def test_precision_must_preserve_half_nanometre(tmp_path):
    c = nmos(asap7, nfin=3)
    with pytest.raises(ValueError, match="precision"):
        c.write_gds(tmp_path / "rounded.gds", precision=1e-9)


def test_width_quantization_and_bad_sizing():
    c = nmos(asap7, width=0.082)
    assert c.info["finfet"]["nfin"] == 4
    for kwargs in (
        {"nfin": 0},
        {"nfin": 1.5},
        {"width": float("nan")},
        {"fingers": 0},
        {"multipliers": -1},
        {"length": 0.021},
    ):
        with pytest.raises(ValueError):
            nmos(asap7, **kwargs)


def test_technology_is_not_selected_by_pdk_name(tmp_path):
    pdk = asap7.model_copy(deep=True)
    pdk.name = "another-finfet"
    pdk.finfet = replace(asap7.finfet, fin_pitch=0.030, gate_pitch=0.060)
    c = nmos(pdk, nfin=2, fingers=3)
    pdk.finfet.validate(c)
    assert c.info["finfet"]["width"] == pytest.approx(0.060)
    c.write_gds(tmp_path / "another.gds")


def test_lvs_detects_wrong_fin_count_and_broken_contact(tmp_path):
    c = nmos(asap7, nfin=3)
    gds = tmp_path / "fet.gds"
    spice = tmp_path / "fet.spice"
    c.write_gds(gds)
    text = c.info["netlist"].generate_netlist()
    spice.write_text(text.replace("nfin=3", "nfin=4"))
    assert not lvs(gds, spice, tmp_path / "wrong_nfin")["match"]
    spice.write_text(text)
    lib = gdstk.read_gds(gds)
    for cell in lib.cells:
        for p in list(cell.polygons):
            if p.layer == 18:
                cell.remove(p)
    lib.write_gds(tmp_path / "broken.gds")
    assert not lvs(tmp_path / "broken.gds", spice, tmp_path / "broken")["match"]


def test_lvs_detects_short(tmp_path):
    c = nmos(asap7, nfin=3)
    spice = tmp_path / "fet.spice"
    gds = tmp_path / "short.gds"
    spice.write_text(c.info["netlist"].generate_netlist())
    s, d = c.ports["source_E"], c.ports["drain_E"]
    c.add_polygon(
        [(-0.041, s.y), (0.041, s.y), (0.041, d.y), (-0.041, d.y)],
        layer=asap7.get_glayer("met2"),
    )
    c.write_gds(gds)
    assert not lvs(gds, spice, tmp_path / "lvs")["match"]


def test_planar_activation_clears_finfet_export_guard(tmp_path):
    nmos(asap7, nfin=1)
    from glayout.pdk.mappedpdk import MappedPDK

    planar = MappedPDK(
        name="planar", glayers={}, grules={}, pdk_files={}, valid_bjt_sizes={}
    )
    planar.activate()
    c = Component()
    c.add_polygon([(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)], layer=(7, 0))
    c.write_gds(tmp_path / "planar.gds")


def test_lattice_survives_flatten_and_planar_activation(tmp_path):
    device = nmos(asap7, nfin=3)
    parent = Component()
    ref = parent << device
    ref.movex(0.001)
    parent = parent.flatten()
    from glayout.pdk.mappedpdk import MappedPDK

    MappedPDK(
        name="planar", glayers={}, grules={}, pdk_files={}, valid_bjt_sizes={}
    ).activate()
    with pytest.raises(ValueError, match="placement grid"):
        parent.write_gds(tmp_path / "bad.gds")


def test_mom_capacitor_and_broken_finger(tmp_path):
    from glayout.primitives.momcap import momcap

    c = momcap(asap7, size=(0.4, 0.5))
    gds = tmp_path / "mom.gds"
    spice = tmp_path / "mom.spice"
    c.write_gds(gds)
    spice.write_text(c.info["netlist"].generate_netlist())
    assert drc(gds) == {}
    assert lvs(gds, spice, tmp_path / "good")["match"]
    lib = gdstk.read_gds(gds)
    for cell in lib.cells:
        fingers = [p for p in cell.polygons if p.layer == 30]
        if fingers:
            cell.remove(fingers[0])
    lib.write_gds(tmp_path / "broken.gds")
    assert not lvs(tmp_path / "broken.gds", spice, tmp_path / "bad")["match"]


def test_asap7_ci_covers_existing_examples():
    import csv
    from pathlib import Path

    params = Path(__file__).parent / "parameters"

    def names(pdk):
        with (params / f"ci_drc_{pdk}.csv").open() as f:
            return {row["cell"] for row in csv.DictReader(f)}

    assert names("asap7") == names("sky130") == names("gf180")
