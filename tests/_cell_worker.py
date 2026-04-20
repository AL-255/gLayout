"""Subprocess worker that builds a single cell and reports the result as JSON.

Run directly:
    python -m tests._cell_worker <module> <func> <kwargs_json>

Prints one line of JSON on stdout with the measured elapsed time and either
the success fields or the error message.

The worker isolates per-(cell, backend) state. `GLAYOUT_BACKEND` must already
be set in the environment when the worker starts — once glayout is imported,
the backend binding is frozen for the process.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import traceback


def _listify_to_tuple(obj):
    """JSON has no tuple type; recursively promote lists back to tuples so
    pydantic-validated signatures (which expect `tuple[...]`) accept them."""
    if isinstance(obj, list):
        return tuple(_listify_to_tuple(x) for x in obj)
    if isinstance(obj, dict):
        return {k: _listify_to_tuple(v) for k, v in obj.items()}
    return obj


def _materialize(result):
    """Wrap a reference / tuple / Component return into a Component."""
    from glayout.backend import Component, ComponentReference
    if isinstance(result, Component):
        return result
    wrapper = Component("test_wrapper")
    if isinstance(result, ComponentReference):
        wrapper.add(result)
        return wrapper
    if isinstance(result, (tuple, list)):
        added = False
        for item in result:
            if isinstance(item, ComponentReference):
                wrapper.add(item)
                added = True
            elif isinstance(item, Component):
                wrapper.add(wrapper << item)
                added = True
        if not added:
            raise AssertionError(
                f"cell returned a container with no Component/Reference: {result!r}"
            )
        return wrapper
    raise AssertionError(f"unexpected return type {type(result).__name__}")


def _bbox_tuple(component) -> list:
    """Normalize bbox to a JSON-serializable nested list. The gdsfactory
    backend returns a numpy array; the gdstk backend returns a tuple."""
    bb = component.bbox
    # numpy array or list-of-lists
    try:
        (x0, y0), (x1, y1) = bb
    except Exception:
        # numpy 2D array
        import numpy as np
        arr = np.asarray(bb)
        x0, y0 = float(arr[0, 0]), float(arr[0, 1])
        x1, y1 = float(arr[1, 0]), float(arr[1, 1])
    return [[float(x0), float(y0)], [float(x1), float(y1)]]


def main() -> int:
    if len(sys.argv) != 4:
        print(json.dumps({
            "status": "error",
            "error": "usage: _cell_worker.py <module> <func> <kwargs_json>",
        }))
        return 2

    module_path, func_name, kwargs_json = sys.argv[1], sys.argv[2], sys.argv[3]
    kwargs = _listify_to_tuple(json.loads(kwargs_json))

    # Build a short env summary for post-hoc debugging.
    backend = os.environ.get("GLAYOUT_BACKEND", "<unset>")

    # Import / construct pdk OUTSIDE the measured window — we want to time
    # cell construction, not module import. (Each subprocess is fresh
    # anyway; we just want apples-to-apples between backends.)
    try:
        from glayout.pdk.sky130_mapped.sky130_mapped import sky130_mapped_pdk as pdk
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "error": f"pdk import failed: {type(e).__name__}: {e}",
            "backend": backend,
        }))
        return 1

    kwargs["pdk"] = pdk

    t0 = time.perf_counter()
    try:
        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        result = func(**kwargs)
        component = _materialize(result)
        bbox = _bbox_tuple(component)

        with tempfile.TemporaryDirectory() as td:
            gds_path = os.path.join(td, "out.gds")
            component.write_gds(gds_path)
            gds_size = os.path.getsize(gds_path)

        elapsed = time.perf_counter() - t0
        print(json.dumps({
            "status": "ok",
            "backend": backend,
            "elapsed_s": elapsed,
            "bbox": bbox,
            "gds_bytes": gds_size,
        }))
        return 0
    except Exception as e:
        elapsed = time.perf_counter() - t0
        summary = traceback.format_exception_only(type(e), e)[-1].strip()
        print(json.dumps({
            "status": "error",
            "backend": backend,
            "elapsed_s": elapsed,
            "error": summary,
            "traceback": traceback.format_exc(),
        }))
        return 1


if __name__ == "__main__":
    sys.exit(main())
