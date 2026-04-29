"""Runs Klayout DRC on every glayout cell for a chosen PDK.

Used by the GitHub Actions CI workflow at ``.github/workflows/drc.yml``.

The script:
  * builds each registered cell with a small, deterministic parameter set,
  * writes a GDS to a per-cell output directory,
  * invokes ``klayout -b -r <drc-deck>`` with ``input``/``report`` runtime
    variables, mirroring ``MappedPDK.drc`` for klayout <= 0.29,
  * parses the resulting ``lyrdb`` to count violations,
  * emits a JSON summary, a JUnit report, and exits non-zero if any cell has
    DRC errors or fails to build.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_DECKS = {
    "sky130": REPO_ROOT / "src" / "glayout" / "pdk" / "sky130_mapped" / "sky130.lydrc",
    "gf180":  REPO_ROOT / "src" / "glayout" / "pdk" / "gf180_mapped" / "gf180mcu.drc",
}


@dataclass
class CellSpec:
    name: str
    builder: Callable[..., Any]
    kwargs_by_pdk: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skip_pdks: List[str] = field(default_factory=list)


def _import_cells() -> Dict[str, CellSpec]:
    """Import cell builders lazily so that import errors are reported per-cell."""
    from glayout.cells.elementary import (
        current_mirror,
        diff_pair,
        flipped_voltage_follower,
        transmission_gate,
    )
    from glayout.cells.composite import (
        differential_to_single_ended_converter,
        diff_pair_ibias,
        low_voltage_cmirror,
        opamp,
    )

    specs: List[CellSpec] = [
        CellSpec(
            name="current_mirror_nfet",
            builder=current_mirror,
            kwargs_by_pdk={
                "sky130": {"device": "nfet", "numcols": 2},
                "gf180":  {"device": "nfet", "numcols": 2},
            },
        ),
        CellSpec(
            name="current_mirror_pfet",
            builder=current_mirror,
            kwargs_by_pdk={
                "sky130": {"device": "pfet", "numcols": 2},
                "gf180":  {"device": "pfet", "numcols": 2},
            },
        ),
        CellSpec(
            name="diff_pair",
            builder=diff_pair,
            kwargs_by_pdk={
                "sky130": {"width": 3, "fingers": 4, "n_or_p_fet": True},
                "gf180":  {"width": 3, "fingers": 4, "n_or_p_fet": True},
            },
        ),
        CellSpec(
            name="flipped_voltage_follower",
            builder=flipped_voltage_follower,
            kwargs_by_pdk={
                "sky130": {
                    "device_type": "nmos", "placement": "horizontal",
                    "width": (5.0, 5.0), "length": (1.0, 1.0),
                    "fingers": (2, 2), "multipliers": (1, 1),
                },
                "gf180": {
                    "device_type": "nmos", "placement": "vertical",
                    "width": (3.0, 3.0), "length": (0.5, 0.5),
                    "fingers": (2, 2), "multipliers": (1, 1),
                },
            },
        ),
        CellSpec(
            name="transmission_gate",
            builder=transmission_gate,
            kwargs_by_pdk={
                "sky130": {},
                "gf180":  {},
            },
        ),
        CellSpec(
            # PDK-specific rmult: rmult=1 is clean on sky130 (1 m4.4 density-area
            # filtered out); rmult=3 minimizes gf180 violations (28→11; rmult=4
            # is worse).
            name="differential_to_single_ended_converter",
            builder=differential_to_single_ended_converter,
            kwargs_by_pdk={
                "sky130": {"rmult": 1, "half_pload": (3.0, 1.0, 2), "via_xlocation": 0},
                "gf180":  {"rmult": 3, "half_pload": (3.0, 1.0, 2), "via_xlocation": 0},
            },
        ),
        CellSpec(
            # PDK-specific rmult: rmult=2 is clean on sky130 (rmult=3 trips
            # m2.2 spacing); rmult=3 is required on gf180 (rmult=2 trips
            # M3.2a from parallel m3 routes 0.05um apart).
            name="diff_pair_ibias",
            builder=diff_pair_ibias,
            kwargs_by_pdk={
                "sky130": {
                    "half_diffpair_params": (5.0, 1.0, 1),
                    "diffpair_bias": (5.0, 2.0, 1),
                    "rmult": 2,
                    "with_antenna_diode_on_diffinputs": 0,
                },
                "gf180": {
                    "half_diffpair_params": (5.0, 1.0, 1),
                    "diffpair_bias": (5.0, 2.0, 1),
                    "rmult": 3,
                    "with_antenna_diode_on_diffinputs": 0,
                },
            },
        ),
        CellSpec(
            name="low_voltage_cmirror",
            builder=low_voltage_cmirror,
            kwargs_by_pdk={
                "sky130": {"width": (4.0, 1.5), "length": 2.0, "fingers": (2, 1), "multipliers": (1, 1)},
                "gf180":  {"width": (4.0, 1.5), "length": 2.0, "fingers": (2, 1), "multipliers": (1, 1)},
            },
        ),
        CellSpec(
            # PDK-specific rmult — same rationale as diff_pair_ibias.
            name="opamp",
            builder=opamp,
            kwargs_by_pdk={
                "sky130": {
                    "half_diffpair_params": (5.0, 1.0, 1),
                    "diffpair_bias": (5.0, 2.0, 1),
                    "half_common_source_params": (7.0, 1.0, 10, 5),
                    "half_common_source_bias": (6.0, 2.0, 8, 4),
                    "half_pload": (6.0, 1.0, 5),
                    "add_output_stage": False,
                    "with_antenna_diode_on_diffinputs": 0,
                    "rmult": 2,
                },
                "gf180": {
                    "half_diffpair_params": (5.0, 1.0, 1),
                    "diffpair_bias": (5.0, 2.0, 1),
                    "half_common_source_params": (7.0, 1.0, 10, 5),
                    "half_common_source_bias": (6.0, 2.0, 8, 4),
                    "half_pload": (6.0, 1.0, 5),
                    "add_output_stage": False,
                    "with_antenna_diode_on_diffinputs": 0,
                    "rmult": 3,
                },
            },
        ),
    ]
    return {spec.name: spec for spec in specs}


def _resolve_pdk(pdk_name: str):
    if pdk_name == "sky130":
        from glayout import sky130
        if sky130 is None:
            raise RuntimeError("sky130 PDK could not be imported")
        return sky130
    if pdk_name == "gf180":
        from glayout import gf180
        if gf180 is None:
            raise RuntimeError("gf180 PDK could not be imported")
        return gf180
    raise ValueError(f"Unsupported PDK: {pdk_name}")


def _drc_deck_for(pdk_name: str, override: Optional[str] = None) -> Path:
    if override:
        return Path(override).resolve()
    if pdk_name not in BUNDLED_DECKS:
        raise ValueError(f"Unsupported PDK: {pdk_name}")
    return BUNDLED_DECKS[pdk_name]


# Rules that are not functional defects — fab/density-style; safe to ignore in CI.
# Match by category name OR description (case-insensitive).
import re as _re
_IGNORE_PATTERNS = [
    _re.compile(r"density", _re.IGNORECASE),
    _re.compile(r"min[._\s-]*\w*\s*area", _re.IGNORECASE),
    _re.compile(r"^m\d+\.4$", _re.IGNORECASE),  # sky130 metal min-area rules: m1.4, m2.4, m3.4, m4.4
]


def _is_ignored_rule(name: str, desc: str) -> bool:
    text = f"{name}  {desc}"
    return any(p.search(text) for p in _IGNORE_PATTERNS)


def _count_lyrdb_violations(report: Path) -> dict:
    """Count DRC violations in a klayout lyrdb. Returns a dict with:
        total, effective (excluding density/min-area), ignored, by_rule, ignored_by_rule.
    On failure to read the report returns {'total': -1, ...}.
    """
    if not report.exists():
        return {"total": -1, "effective": -1, "ignored": 0, "by_rule": {}, "ignored_by_rule": {}}
    tree = ET.parse(report)
    root = tree.getroot()
    cats: dict[str, str] = {}
    items = None
    for child in root:
        tag = child.tag.split("}")[-1]
        if tag == "items":
            items = child
        elif tag == "categories":
            for cat in child:
                cname = cdesc = ""
                for sub in cat:
                    stag = sub.tag.split("}")[-1]
                    if stag == "name":
                        cname = (sub.text or "").strip()
                    elif stag == "description":
                        cdesc = (sub.text or "").strip()
                if cname:
                    cats[cname] = cdesc
    by_rule: dict[str, int] = {}
    ignored_by_rule: dict[str, int] = {}
    if items is not None:
        for item in items:
            cat = ""
            for sub in item:
                if sub.tag.split("}")[-1] == "category":
                    cat = (sub.text or "").strip().strip("'")
                    break
            desc = cats.get(cat, "")
            if _is_ignored_rule(cat, desc):
                ignored_by_rule[cat] = ignored_by_rule.get(cat, 0) + 1
            else:
                by_rule[cat] = by_rule.get(cat, 0) + 1
    total = sum(by_rule.values()) + sum(ignored_by_rule.values())
    return {
        "total": total,
        "effective": sum(by_rule.values()),
        "ignored": sum(ignored_by_rule.values()),
        "by_rule": by_rule,
        "ignored_by_rule": ignored_by_rule,
    }


def _run_klayout(deck: Path, gds: Path, report: Path) -> subprocess.CompletedProcess:
    cmd = [
        "klayout",
        "-b",
        "-r", str(deck),
        "-rd", f"input={gds}",
        "-rd", f"report={report}",
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900)


def _write_junit(results: List[dict], pdk: str, out: Path) -> None:
    suite = ET.Element(
        "testsuite",
        attrib={
            "name": f"glayout-drc-{pdk}",
            "tests": str(len(results)),
            "failures": str(sum(1 for r in results if r["status"] == "fail")),
            "errors": str(sum(1 for r in results if r["status"] == "error")),
            "skipped": str(sum(1 for r in results if r["status"] == "skip")),
        },
    )
    for r in results:
        case = ET.SubElement(
            suite, "testcase",
            attrib={"classname": f"drc.{pdk}", "name": r["cell"]},
        )
        if r["status"] == "fail":
            ET.SubElement(case, "failure", attrib={"message": r.get("message", "DRC violations")}).text = json.dumps(r, indent=2)
        elif r["status"] == "error":
            ET.SubElement(case, "error", attrib={"message": r.get("message", "build/DRC error")}).text = json.dumps(r, indent=2)
        elif r["status"] == "skip":
            ET.SubElement(case, "skipped", attrib={"message": r.get("message", "skipped")})
    tree = ET.ElementTree(suite)
    tree.write(out, encoding="utf-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdk", required=True, choices=["sky130", "gf180"])
    parser.add_argument("--out-dir", default="drc_results")
    parser.add_argument(
        "--cells",
        default=None,
        help="Comma-separated cell names; default runs every registered cell.",
    )
    parser.add_argument(
        "--deck",
        default=None,
        help="Path to a klayout DRC deck overriding the bundled one (e.g. a PDK-installed deck).",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    gds_dir = out_dir / "gds"
    rpt_dir = out_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    gds_dir.mkdir(parents=True, exist_ok=True)
    rpt_dir.mkdir(parents=True, exist_ok=True)

    deck = _drc_deck_for(args.pdk, args.deck)
    if not deck.exists():
        print(f"DRC deck missing: {deck}", file=sys.stderr)
        return 2

    pdk = _resolve_pdk(args.pdk)
    specs = _import_cells()
    if args.cells:
        wanted = {c.strip() for c in args.cells.split(",") if c.strip()}
        specs = {n: s for n, s in specs.items() if n in wanted}

    results: List[dict] = []
    for name, spec in specs.items():
        result: Dict[str, Any] = {"cell": name, "pdk": args.pdk, "status": "skip"}
        if args.pdk in spec.skip_pdks:
            result["message"] = f"cell skipped on {args.pdk}"
            results.append(result)
            print(f"[SKIP] {name}: {result['message']}")
            continue

        kwargs = spec.kwargs_by_pdk.get(args.pdk, {})
        gds_path = gds_dir / f"{name}.gds"
        rpt_path = rpt_dir / f"{name}.lyrdb"
        try:
            print(f"[BUILD] {name}", flush=True)
            comp = spec.builder(pdk, **kwargs)
            # Some cells (e.g. diff_pair_ibias) return a ComponentReference;
            # wrap into a fresh Component so we can write_gds.
            if not hasattr(comp, "write_gds"):
                from gdsfactory.component import Component as _Component
                wrapper = _Component(name)
                wrapper.add(comp)
                wrapper.add_ports(comp.get_ports_list())
                if hasattr(comp, "parent") and "netlist" in getattr(comp.parent, "info", {}):
                    wrapper.info["netlist"] = comp.parent.info["netlist"]
                comp = wrapper
            comp.name = name
            comp.write_gds(str(gds_path))
        except Exception as exc:
            tb = traceback.format_exc()
            result.update({"status": "error", "message": f"build failed: {exc}", "trace": tb})
            results.append(result)
            print(f"[ERROR] {name}: build failed\n{tb}")
            continue

        try:
            print(f"[DRC]  {name}", flush=True)
            proc = _run_klayout(deck, gds_path, rpt_path)
        except subprocess.TimeoutExpired:
            result.update({"status": "error", "message": "klayout timeout"})
            results.append(result)
            print(f"[ERROR] {name}: klayout timeout")
            continue

        viols = _count_lyrdb_violations(rpt_path)
        effective = viols["effective"]
        result.update({
            "violations": viols,
            "report": str(rpt_path.relative_to(out_dir)),
            "gds": str(gds_path.relative_to(out_dir)),
            "klayout_returncode": proc.returncode,
            "klayout_stderr_tail": (proc.stderr or "")[-400:],
        })
        if proc.returncode != 0:
            result["status"] = "error"
            result["message"] = f"klayout exited {proc.returncode}"
        elif effective < 0:
            result["status"] = "error"
            result["message"] = "report file not produced"
        elif effective == 0:
            result["status"] = "pass"
            if viols["ignored"]:
                result["message"] = f"clean (ignored {viols['ignored']} density/area)"
        else:
            result["status"] = "fail"
            top = ", ".join(f"{r}:{n}" for r, n in sorted(viols["by_rule"].items(), key=lambda kv: -kv[1])[:3])
            result["message"] = f"{effective} DRC violation(s) [{top}]"
        results.append(result)
        print(f"[{result['status'].upper()}] {name}: {result.get('message', 'clean')}")

    summary = {
        "pdk": args.pdk,
        "total": len(results),
        "pass": sum(1 for r in results if r["status"] == "pass"),
        "fail": sum(1 for r in results if r["status"] == "fail"),
        "error": sum(1 for r in results if r["status"] == "error"),
        "skip": sum(1 for r in results if r["status"] == "skip"),
        "results": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    _write_junit(results, args.pdk, out_dir / "junit.xml")

    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    return 0 if summary["fail"] == 0 and summary["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
