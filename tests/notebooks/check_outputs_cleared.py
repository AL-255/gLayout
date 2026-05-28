"""Fail if any tutorial notebook ships with cell outputs or execution counts.

Run as a CI precheck before ``run_tutorial_notebooks.py``. Stdlib-only so it
can run before any venv is set up — fails in a fraction of a second when a
contributor forgets to strip outputs, instead of after the multi-minute
nbconvert pass.

Usage:
    python tests/notebooks/check_outputs_cleared.py
    python tests/notebooks/check_outputs_cleared.py --fix     # strip in-place

A code cell counts as dirty if either:
  * ``execution_count`` is not None (i.e. the cell has been run), or
  * ``outputs`` is non-empty.

Markdown / raw cells are ignored. Cell metadata (e.g. ``metadata.execution``,
``metadata.widgets``) is left alone — we only care about renderable outputs.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
TUTORIAL_DIR = REPO_ROOT / "tutorial"


def _scan(nb_path: Path) -> List[Tuple[int, str]]:
    """Return [(cell_index, reason), ...] for every dirty code cell."""
    try:
        nb = json.loads(nb_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return [(-1, f"unreadable notebook: {e}")]
    dirty: List[Tuple[int, str]] = []
    for i, c in enumerate(nb.get("cells", [])):
        if c.get("cell_type") != "code":
            continue
        if c.get("execution_count") is not None:
            dirty.append((i, f"execution_count={c['execution_count']!r}"))
        if c.get("outputs"):
            dirty.append((i, f"{len(c['outputs'])} output(s)"))
    return dirty


def _clear(nb_path: Path) -> bool:
    """Strip outputs in-place. Returns True if the file changed."""
    nb = json.loads(nb_path.read_text())
    changed = False
    for c in nb.get("cells", []):
        if c.get("cell_type") != "code":
            continue
        if c.get("execution_count") is not None:
            c["execution_count"] = None
            changed = True
        if c.get("outputs"):
            c["outputs"] = []
            changed = True
    if changed:
        nb_path.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
    return changed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fix", action="store_true",
                   help="Strip outputs in-place instead of just reporting.")
    args = p.parse_args()

    nbs = sorted(
        nb for nb in TUTORIAL_DIR.rglob("*.ipynb")
        if ".ipynb_checkpoints" not in nb.parts
    )
    if not nbs:
        print("no tutorial notebooks found", file=sys.stderr)
        return 2

    bad: List[Tuple[Path, List[Tuple[int, str]]]] = []
    for nb in nbs:
        if args.fix:
            if _clear(nb):
                print(f"[FIXED] {nb.relative_to(REPO_ROOT)}")
            continue
        dirty = _scan(nb)
        if dirty:
            bad.append((nb, dirty))

    if args.fix:
        return 0

    if not bad:
        print(f"OK: all {len(nbs)} tutorial notebook(s) have cleared outputs.")
        return 0

    print("FAIL: the following notebooks ship with non-cleared outputs:\n", file=sys.stderr)
    for nb, dirty in bad:
        rel = nb.relative_to(REPO_ROOT)
        # Cap the noise — one line per offending cell, first 10 cells per nb.
        shown = dirty[:10]
        more = len(dirty) - len(shown)
        for i, why in shown:
            print(f"  {rel}: cell {i}: {why}", file=sys.stderr)
        if more > 0:
            print(f"  {rel}: ... and {more} more", file=sys.stderr)
    print(
        "\nFix:\n"
        "  jupyter nbconvert --clear-output --inplace tutorial/**/*.ipynb tutorial/**/**/*.ipynb\n"
        "or:\n"
        "  python tests/notebooks/check_outputs_cleared.py --fix",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
