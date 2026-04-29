"""Summarize a klayout lyrdb into rule -> count and a few sample bboxes.

Usage:
    python tests/drc/diagnose.py path/to/cell.lyrdb [more.lyrdb ...]
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import List, Tuple


def _strip_polygon(text: str) -> Tuple[float, float, float, float] | None:
    """Pull a (x1,y1,x2,y2) bbox out of a klayout polygon/edge value string."""
    pts: List[Tuple[float, float]] = []
    for chunk in text.replace("(", " ").replace(")", " ").split(";"):
        bits = [b for b in chunk.replace(",", " ").split() if b]
        nums: List[float] = []
        for b in bits:
            try:
                nums.append(float(b))
            except ValueError:
                pass
        for i in range(0, len(nums) - 1, 2):
            pts.append((nums[i], nums[i + 1]))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def summarize(report: Path) -> None:
    if not report.exists():
        print(f"missing: {report}")
        return
    tree = ET.parse(report)
    root = tree.getroot()
    items = None
    cats: dict[str, str] = {}
    for child in root:
        tag = child.tag.split("}")[-1]
        if tag == "items":
            items = child
        elif tag == "categories":
            for cat in child:
                cname = ""
                cdesc = ""
                for sub in cat:
                    stag = sub.tag.split("}")[-1]
                    if stag == "name":
                        cname = (sub.text or "").strip()
                    elif stag == "description":
                        cdesc = (sub.text or "").strip()
                if cname:
                    cats[cname] = cdesc or cname
    if items is None:
        print(f"{report.name}: no items element")
        return
    counts: Counter[str] = Counter()
    samples: dict[str, list] = {}
    for item in items:
        cat = ""
        bbox = None
        for sub in item:
            stag = sub.tag.split("}")[-1]
            if stag == "category":
                cat = (sub.text or "").strip().strip("'")
            elif stag == "values":
                for val in sub:
                    text = (val.text or "")
                    bb = _strip_polygon(text)
                    if bb:
                        bbox = bb
                        break
        counts[cat] += 1
        if cat not in samples:
            samples[cat] = []
        if bbox is not None and len(samples[cat]) < 2:
            samples[cat].append(bbox)

    print(f"\n=== {report.name} : {sum(counts.values())} violations across {len(counts)} rules ===")
    for cat, n in counts.most_common():
        desc = cats.get(cat, "")
        head = f"  {n:>4d}  {cat}"
        if desc and desc != cat:
            head += f"  — {desc[:80]}"
        print(head)
        for bb in samples.get(cat, []):
            print(f"        sample bbox um: ({bb[0]:.3f},{bb[1]:.3f})-({bb[2]:.3f},{bb[3]:.3f})")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for arg in sys.argv[1:]:
        summarize(Path(arg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
