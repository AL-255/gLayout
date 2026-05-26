"""Layout-generation tests for every cell defined under src/glayout/cells.

Each test builds one (cell, backend) combination in an isolated subprocess
and asserts:
  1. The generator returns a layout that yields a non-empty bounding box.
  2. A GDS is written and has non-zero size.
Plus the wall-clock build time is captured for both backends so we can track
the speedup from the gdstk migration.

Known-broken cells (pre-existing bugs, not migration regressions) are marked
`xfail` with the failing traceback summary. Heavyweight cells that take
minutes to build are marked `slow` and skipped unless `--runslow` is passed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest


BACKENDS = ("gdstk", "gdsfactory")

# Per-run timing table. Populated by each test and printed by the
# pytest_terminal_summary hook in conftest.py.
TIMINGS: list[dict] = []


# ---------------------------------------------------------------------------
# Case table
# ---------------------------------------------------------------------------


@dataclass
class CellCase:
    test_id: str
    module: str
    func: str
    # Static kwargs. `pdk` is injected inside the subprocess so the object
    # doesn't need to cross process boundaries.
    kwargs: dict
    xfail: Optional[str] = None
    slow: bool = False


CASES: list[CellCase] = [
    # --- elementary ---
    CellCase("fvf",
             "glayout.cells.elementary.FVF.fvf",
             "flipped_voltage_follower",
             {}),
    CellCase("current_mirror",
             "glayout.cells.elementary.current_mirror.current_mirror",
             "current_mirror",
             {}),
    CellCase("diff_pair",
             "glayout.cells.elementary.diff_pair.diff_pair",
             "diff_pair",
             {}),
    CellCase("transmission_gate",
             "glayout.cells.elementary.transmission_gate.transmission_gate",
             "transmission_gate",
             {}),

    # --- composite ---
    CellCase("differential_to_single_ended_converter",
             "glayout.cells.composite.differential_to_single_ended_converter.differential_to_single_ended_converter",
             "differential_to_single_ended_converter",
             {"rmult": 1, "half_pload": [0.5, 0.18, 4], "via_xlocation": 10}),
    CellCase("diff_pair_ibias",
             "glayout.cells.composite.diffpair_cmirror_bias.diff_pair_cmirrorbias",
             "diff_pair_ibias",
             {"half_diffpair_params": [4, 2, 1],
              "diffpair_bias": [3, 0.15, 1],
              "rmult": 1,
              "with_antenna_diode_on_diffinputs": 0},
             xfail="pre-existing: current_mirror_interdigitized_netlist() missing 'fingers' arg"),
    CellCase("low_voltage_cmirror_fvf",
             "glayout.cells.composite.fvf_based_ota.low_voltage_cmirror",
             "low_voltage_cmirror",
             {},
             xfail="pre-existing netlist bug: 'str' object has no attribute 'nodes'"),
    CellCase("n_block",
             "glayout.cells.composite.fvf_based_ota.n_block",
             "n_block",
             {},
             xfail="pre-existing netlist bug: 'str' object has no attribute 'nodes'",
             slow=True),
    CellCase("super_class_AB_OTA",
             "glayout.cells.composite.fvf_based_ota.ota",
             "super_class_AB_OTA",
             {},
             xfail="pre-existing: builds on n_block which has a netlist bug",
             slow=True),
    CellCase("p_block",
             "glayout.cells.composite.fvf_based_ota.p_block",
             "p_block",
             {},
             xfail="pre-existing: UnboundLocalError on substrate_tap_ref"),
    CellCase("low_voltage_cmirror_standalone",
             "glayout.cells.composite.low_voltage_cmirror.low_voltage_cmirror",
             "low_voltage_cmirror",
             {},
             xfail="pre-existing: KeyError on ref.info['netlist']"),
    CellCase("diff_pair_stackedcmirror",
             "glayout.cells.composite.opamp.diff_pair_stackedcmirror",
             "diff_pair_stackedcmirror",
             {"half_diffpair_params": [4, 2, 8],
              "diffpair_bias": [6, 2, 3],
              "half_common_source_nbias": [2, 1, 5, 4],
              "rmult": 2,
              "with_antenna_diode_on_diffinputs": 7},
             xfail="pre-existing: current_mirror_interdigitized_netlist() missing 'fingers' arg"),
    CellCase("opamp",
             "glayout.cells.composite.opamp.opamp",
             "opamp",
             {},
             xfail="pre-existing: current_mirror_interdigitized_netlist() missing 'fingers' arg",
             slow=True),
    CellCase("opamp_twostage",
             "glayout.cells.composite.opamp.opamp_twostage",
             "opamp_twostage",
             {},
             xfail="pre-existing: current_mirror_interdigitized_netlist() missing 'fingers' arg",
             slow=True),
    CellCase("stacked_nfet_current_mirror",
             "glayout.cells.composite.stacked_current_mirror.stacked_current_mirror",
             "stacked_nfet_current_mirror",
             {"half_common_source_nbias": [4, 2, 4, 4],
              "rmult": 2,
              "sd_route_left": True}),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backend_available(name: str) -> bool:
    if name == "gdstk":
        try:
            import gdstk  # noqa: F401
            return True
        except ImportError:
            return False
    if name == "gdsfactory":
        try:
            import gdsfactory  # noqa: F401
            return True
        except ImportError:
            return False
    return False


_SRC = str(Path(__file__).resolve().parents[1] / "src")
_WORKER = str(Path(__file__).resolve().parent / "_cell_worker.py")


def _run_worker(case: CellCase, backend: str, timeout_s: int = 600) -> dict:
    """Invoke the cell in an isolated subprocess with the chosen backend.
    Returns the parsed JSON result (or an error dict on timeout / bad exit)."""
    env = os.environ.copy()
    env["GLAYOUT_BACKEND"] = backend
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PDK_ROOT", "/tmp")

    try:
        proc = subprocess.run(
            [sys.executable, _WORKER, case.module, case.func, json.dumps(case.kwargs)],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "elapsed_s": timeout_s,
                "error": f"timed out after {timeout_s}s"}

    # Workers print a single JSON line on stdout. Tolerate trailing output.
    payload: Optional[dict] = None
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    if payload is None:
        return {
            "status": "error",
            "elapsed_s": 0.0,
            "error": f"worker produced no JSON; exit={proc.returncode}",
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }
    return payload


# ---------------------------------------------------------------------------
# Parametrized test
# ---------------------------------------------------------------------------


def _params() -> list:
    out = []
    for case in CASES:
        for backend in BACKENDS:
            marks = []
            if case.slow:
                marks.append(pytest.mark.slow)
            if case.xfail:
                marks.append(pytest.mark.xfail(reason=case.xfail, strict=False))
            out.append(pytest.param(case, backend,
                                    id=f"{case.test_id}-{backend}",
                                    marks=marks))
    return out


@pytest.mark.parametrize("case,backend", _params())
def test_cell_builds_layout(case: CellCase, backend: str):
    """Build the cell with the selected backend and verify the layout."""
    if not _backend_available(backend):
        pytest.skip(f"{backend} backend not installed")

    result = _run_worker(case, backend, timeout_s=900 if case.slow else 180)

    # Always record timing so skipped / xfailed cells still show up in the
    # summary — useful for tracking speedups even on failing cells.
    TIMINGS.append({
        "test_id": case.test_id,
        "backend": backend,
        "status": result.get("status"),
        "elapsed_s": result.get("elapsed_s"),
        "gds_bytes": result.get("gds_bytes"),
        "error": result.get("error"),
    })

    if result["status"] != "ok":
        pytest.fail(
            f"{case.test_id}/{backend} failed: {result.get('error')}\n"
            f"{result.get('traceback', '')}"
        )

    bbox = result["bbox"]
    (x0, y0), (x1, y1) = bbox
    assert (x1 - x0) > 0 and (y1 - y0) > 0, (
        f"{case.test_id}/{backend}: empty bbox {bbox}"
    )
    assert result["gds_bytes"] > 0, f"{case.test_id}/{backend}: empty GDS"
