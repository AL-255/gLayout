"""Brute-force sweep helper to find a DRC-clean parameter set per cell.

Usage:
    python tests/drc/sweep.py --pdk sky130 --cell flipped_voltage_follower

Iterates a small grid of parameters drawn from the project's parameter sweep
sheet and prints the first combination that produces 0 DRC violations under
the bundled klayout deck (same path as ``run_cell_drc.py``).
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent))
from run_cell_drc import _drc_deck_for, _resolve_pdk, _count_lyrdb_violations  # noqa: E402


def _grid_flipped_voltage_follower() -> Iterable[Dict[str, Any]]:
    # Tighter grid: rmult-style sizing first, then placements.
    widths = [(3.0, 3.0), (5.0, 5.0), (8.0, 8.0)]
    lengths = [(0.5, 0.5), (1.0, 1.0), (2.0, 2.0)]
    placements = ["horizontal", "vertical"]
    multipliers = [(2, 2), (1, 1)]
    for w, l, p, m in itertools.product(widths, lengths, placements, multipliers):
        yield {
            "device_type": "nmos",
            "placement": p,
            "width": w,
            "length": l,
            "fingers": (2, 2),
            "multipliers": m,
        }


def _grid_diff_to_single() -> Iterable[Dict[str, Any]]:
    for rmult in (2, 1):
        for w in (5.0, 6.0, 7.0, 3.0):
            for l in (1.0, 1.5):
                for fingers in (2, 4):
                    for via in (0, 1):
                        yield {"rmult": rmult, "half_pload": (w, l, fingers), "via_xlocation": via}


def _grid_diff_pair_ibias() -> Iterable[Dict[str, Any]]:
    # rmult=2 cleaned sky130 immediately; try it first for gf180 too.
    for rmult in (2, 1):
        for hdp in [(5.0, 1.0, 1), (6.0, 1.0, 1), (6.0, 1.5, 2), (7.0, 1.0, 2), (5.0, 0.5, 4)]:
            for db in [(5.0, 2.0, 1), (6.0, 2.0, 1), (6.0, 2.5, 2), (7.0, 2.0, 2)]:
                yield {
                    "half_diffpair_params": hdp,
                    "diffpair_bias": db,
                    "rmult": rmult,
                    "with_antenna_diode_on_diffinputs": 0,
                }


def _grid_low_voltage_cmirror() -> Iterable[Dict[str, Any]]:
    # Smaller, on-grid lengths first; sweep widths/fingers second.
    for length in (2.0, 1.5, 3.0):
        for w in [(4.0, 1.5), (5.0, 2.0), (6.0, 2.0), (3.0, 1.0)]:
            for f in [(2, 1), (3, 1), (2, 2)]:
                for m in [(1, 1), (2, 1)]:
                    yield {
                        "width": w,
                        "length": length,
                        "fingers": f,
                        "multipliers": m,
                    }


def _grid_opamp() -> Iterable[Dict[str, Any]]:
    # Sweep midpoints around the sheet ranges; keep small to bound runtime.
    hdp_set = [(5.0, 1.0, 1), (6.0, 1.0, 1), (6.0, 1.5, 2), (7.0, 1.0, 2)]
    db_set = [(5.0, 2.0, 1), (6.0, 2.0, 1), (7.0, 2.0, 2)]
    cs_p_set = [(6.0, 1.0, 8, 5), (7.0, 1.0, 10, 5), (8.0, 1.5, 12, 5)]
    cs_b_set = [(5.0, 2.0, 7, 4), (6.0, 2.0, 8, 4), (7.0, 2.0, 9, 4)]
    pload_set = [(5.0, 1.0, 4), (6.0, 1.0, 5), (7.0, 1.0, 6)]
    rmult_set = (1, 2)
    for hdp, db, csp, csb, pl, rm in itertools.product(
        hdp_set, db_set, cs_p_set, cs_b_set, pload_set, rmult_set
    ):
        yield {
            "half_diffpair_params": hdp,
            "diffpair_bias": db,
            "half_common_source_params": csp,
            "half_common_source_bias": csb,
            "half_pload": pl,
            "add_output_stage": False,
            "with_antenna_diode_on_diffinputs": 0,
            "rmult": rm,
        }


GRIDS: Dict[str, Tuple[str, Callable[[], Iterable[Dict[str, Any]]]]] = {
    "flipped_voltage_follower":              ("glayout.cells.elementary.flipped_voltage_follower",     _grid_flipped_voltage_follower),
    "differential_to_single_ended_converter":("glayout.cells.composite.differential_to_single_ended_converter", _grid_diff_to_single),
    "diff_pair_ibias":                       ("glayout.cells.composite.diff_pair_ibias",               _grid_diff_pair_ibias),
    "low_voltage_cmirror":                   ("glayout.cells.composite.low_voltage_cmirror",           _grid_low_voltage_cmirror),
    "opamp":                                 ("glayout.cells.composite.opamp.opamp",                   _grid_opamp),
}


def _builder_for(import_path: str) -> Callable[..., Any]:
    parts = import_path.split(".")
    mod = __import__(".".join(parts[:-1]), fromlist=[parts[-1]])
    return getattr(mod, parts[-1])


def _run_klayout(deck: Path, gds: Path, report: Path) -> int:
    cmd = [
        "klayout", "-b",
        "-r", str(deck),
        "-rd", f"input={gds}",
        "-rd", f"report={report}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdk", required=True, choices=["sky130", "gf180"])
    parser.add_argument("--cell", required=True, choices=list(GRIDS.keys()))
    parser.add_argument("--max-trials", type=int, default=200)
    parser.add_argument("--stop-on-first", action="store_true", default=True)
    args = parser.parse_args()

    pdk = _resolve_pdk(args.pdk)
    deck = _drc_deck_for(args.pdk)
    if not deck.exists():
        print(f"DRC deck missing: {deck}", file=sys.stderr)
        return 2
    import_path, grid = GRIDS[args.cell]
    builder = _builder_for(import_path)

    workdir = Path(tempfile.mkdtemp(prefix=f"sweep_{args.cell}_{args.pdk}_"))
    print(f"workdir: {workdir}")
    cleanest = (10**9, None)  # (violations, params)
    tried = 0
    for params in grid():
        if tried >= args.max_trials:
            break
        tried += 1
        gds = workdir / f"trial_{tried}.gds"
        rpt = workdir / f"trial_{tried}.lyrdb"
        t0 = time.time()
        try:
            comp = builder(pdk, **params)
            if not hasattr(comp, "write_gds"):
                from gdsfactory.component import Component as _Component
                wrapper = _Component(args.cell)
                wrapper.add(comp)
                wrapper.add_ports(comp.get_ports_list())
                comp = wrapper
            comp.name = args.cell
            comp.write_gds(str(gds))
        except Exception as exc:
            print(f"[{tried}] BUILD FAIL: {params} -> {exc}")
            continue

        rc = _run_klayout(deck, gds, rpt)
        if rc != 0:
            print(f"[{tried}] klayout rc={rc}: {params}")
            continue
        viols = _count_lyrdb_violations(rpt)
        v = viols["effective"]
        elapsed = time.time() - t0
        print(f"[{tried}] violations={v:<4d} (ignored {viols['ignored']:>2d}) ({elapsed:5.1f}s) params={json.dumps(params, default=str)}")
        if v >= 0 and v < cleanest[0]:
            cleanest = (v, params)
        if v == 0 and args.stop_on_first:
            print(f"\nCLEAN POINT for {args.cell} on {args.pdk}:")
            print(json.dumps(params, indent=2, default=str))
            return 0

    print(f"\nNo clean point in {tried} trials. Cleanest had {cleanest[0]} violations:")
    print(json.dumps(cleanest[1], indent=2, default=str))
    return 1 if cleanest[0] != 0 else 0


if __name__ == "__main__":
    sys.exit(main())
