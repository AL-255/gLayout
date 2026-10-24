# ASAP7 / FinFET proof of concept

Use Python 3.10 and either geometry engine (GLAYOUT_BACKEND=gdstk or
GLAYOUT_BACKEND=gdsfactory). Install with pip install -e . pytest.
Set PDK_ROOT=/tmp when no planar PDK is installed (the package also imports
its legacy planar modules). No external PDK is needed for the demonstration. The mapping and dimensions
come from ASAP7 r1p7's layer map and design-rule manual. Layout uses native
micrometres, not the scaled APR libraries.

## Transistors and placement

    from glayout import asap7, nmos, pmos, momcap
    device = nmos(asap7, nfin=3, fingers=4, multipliers=2)
    device.write_gds('nfet.gds')
    print(device.info['netlist'].generate_netlist())

nfin is the explicit fin count. When omitted, width means occupied fin-track
height in micrometres and rounds UP to complete tracks. It is not planar
channel width or the effective BSIM-CMG perimeter width. For ASAP7 each track
is 27 nm. SPICE uses nfin and a 20 nm gate length. Nonphysical gate lengths
are rejected. Models are nmos_rvt and pmos_rvt.

FinFETTechnology describes a node's dimensions and layer identities; the
transistor generator dispatches using pdk.finfet rather than a PDK name.
ASAP7 uses horizontal 7 nm fins, vertical 20 nm gates, 27/54 nm fin/gate
pitches, and a 0.5 nm manufacturing grid. S/D contacts use SDT, LISD and V0;
gate contacts use LIG and V0. Dummy gates lie outside ACTIVE. Multiplier
rows use continuous gate stripes with GCUT and complete fin lattices.

    top = Component()
    ref = top << device
    asap7.finfet.place(ref, origin=(0.541, 0.271))

Import Component from glayout.backend for the placement example. Reference
origins snap to whole gate/fin pitches; arbitrary bbox centering or 90-degree
rotation is illegal. Export checks world coordinates through the hierarchy
and after flattening, rejects off-grid geometry and prevents lossy GDS
precision. The lattice metadata survives changing the active PDK.
The gdsfactory compatibility adapter preserves fractional-nanometre ports.

## Circuits and MOM capacitors

All eleven cells in tests/parameters/ci_drc_asap7.csv use the same CI builders
as SKY130/GF180. Explicit ASAP7 dimensions replace planar sizes and lengths.
The FinFET path reuses their electrical schematics and uses a simple,
spacious channel router: M2 escapes, vertical M3 columns, horizontal M4 nets.
It does not retain planar guard rings, interdigitated placement or common
centroid matching. Bulk terminals are implicit substrate/well pins. Multiple
isolated NMOS substrate biases are unsupported.

The opamp retains its two stages and compensation connections. As requested,
its historical mim_cap_size/mim_cap_rows options create custom MOM capacitors
in FinFET mode (two columns, the specified number of rows). M3 fingers connect
to M2 terminal bars through V2. Call momcap(asap7, size=(0.4, 0.5)) directly
for a standalone capacitor. Layer 100/10 is a gLayout recognition marker,
not a fabrication mask. The nominal model is 0.1 fF per micrometre of facing
finger edge at 44 nm spacing, inversely scaled with gap. This is an
uncalibrated demonstration model, not a characterized ASAP7 capacitance.

The optional opamp output buffer and antenna-diode options are not implemented
in this POC. The CI opamp uses its normal two-stage configuration with those
options disabled. Primitive terminal routes currently use met2.

## Verification

    python tests/drc/run_cell_drc.py --pdk asap7 --jobs 2 --out-dir asap7_results/drc
    python tests/lvs/run_cell_lvs.py --pdk asap7 --jobs 2 --inputs-dir asap7_results/drc --out-dir asap7_results/lvs
    pytest -q tests/test_finfet.py

ASAP7 DRC is an OPEN KLAYOUT POC SUBSET, not the official Calibre signoff deck.
It checks manufacturing/fin/gate lattices, fin dimensions and channel fin
counts, ACTIVE track height, selected width/spacing/area rules for ACTIVE,
SELECT, WELL, LISD, LIG, SDT, M1-M5 and V0-V4, contact containment, and selected
cross-layer separations. It does not implement the entire DRM: in particular
all tip/corner spacing, exact pitch adjacency, enclosure, GCUT, density and
upper-metal routing-track rules are not comprehensively checked. No area or
density exclusions are used for ASAP7's implemented rules.

LVS independently reads GDS, derives MOS channels, extracts SDT/LISD/LIG/
V0/metal connectivity and compares against external SPICE. NFIN reference
values convert to occupied-track W for KLayout's generic MOS extractor;
DRC independently checks actual FIN/channel intersections. Top-level pins
must match, and only declared bulk names have implicit connection semantics.
The MOM extractor measures neighbouring finger overlaps and gaps and probes
their actual nets; it does not infer a capacitor merely from its marker.
Negative tests cover missing contacts, shorts, wrong fin counts, missing MOM
fingers, illegal transforms and insufficient GDS precision.

The ASAP7 workflow runs both geometry engines, preserves every existing CI
cell name, and uploads GDS, reference/extracted SPICE, extraction databases,
JSON summaries and JUnit results. Existing planar CI flows remain in place.

## Validation recorded for this change

- Python 3.10 / pinned KLayout 0.28.17: 46 FinFET tests pass on each geometry engine.
- Python 3.13 / KLayout 0.30.12: the same 46 tests pass on gdstk.
- ASAP7: all 11 CSV examples pass POC DRC and extracted LVS on both engines,
  with no skipped examples, including the compensated opamp.
- Existing planar CI DRC: SKY130 11/11 and GF180 11/11 pass on gdsfactory.
- Existing planar CI LVS: GF180 8/8 pass after correcting its MIM option to B
  and quoting include paths; SKY130 7/8 pass. The SKY130 opamp has one existing
  LVS net mismatch. An independent checkout of baseline commit 3e129ed reproduces
  that failure; its opamp geometry has an empty per-layer XOR against this
  branch and its reference schematic is identical. This is not an ASAP7 result.
- The full existing Python suite completed with 80 passed, 8 skipped,
  5 expected failures and 5 unexpected passes before the final three new tests;
  the final fast CI suite and all final FinFET tests were rerun successfully.
