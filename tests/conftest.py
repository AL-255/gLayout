"""Shared pytest configuration.

Layout tests run each (cell, backend) combination in an isolated subprocess,
so the parent process's backend never matters. We still default
`GLAYOUT_BACKEND=gdstk` so structural/regression tests that import glayout
directly don't require gdsfactory to be installed.
"""
import os

import pytest

os.environ.setdefault("GLAYOUT_BACKEND", "gdstk")
os.environ.setdefault("PDK_ROOT", "/tmp")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: marks tests that can take several minutes (--runslow to run)"
    )


def pytest_addoption(parser):
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="Also run tests marked @pytest.mark.slow (heavyweight cells that "
             "can take several minutes to build).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="--runslow not set")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print a per-(cell, backend) timing table, plus a gdsfactory-vs-gdstk
    speedup column computed from matched pairs."""
    # The test module lives next to this conftest. Load via the file path
    # directly so we don't need `tests/` to be a package.
    import importlib.util, pathlib
    test_file = pathlib.Path(__file__).parent / "test_cells_layout.py"
    spec = importlib.util.spec_from_file_location("_tests_cells_layout", test_file)
    if spec is None or spec.loader is None:
        return
    # The module is already imported by pytest — use the imported instance so
    # we see the populated TIMINGS list.
    import sys
    mod = None
    for m in sys.modules.values():
        if getattr(m, "__file__", None) == str(test_file):
            mod = m
            break
    if mod is None or not hasattr(mod, "TIMINGS"):
        return
    TIMINGS = mod.TIMINGS
    if not TIMINGS:
        return

    # group by test_id -> {backend: row}
    by_id: dict[str, dict[str, dict]] = {}
    for row in TIMINGS:
        by_id.setdefault(row["test_id"], {})[row["backend"]] = row

    tw = terminalreporter
    tw.write_sep("=", "cell layout build times")
    header = f"{'cell':<42} {'gdstk (s)':>10} {'gdsfactory (s)':>15} {'speedup':>10} {'status':>20}"
    tw.write_line(header)
    tw.write_line("-" * len(header))

    def _fmt(val):
        return f"{val:.3f}" if isinstance(val, (int, float)) else "—"

    gdstk_total = 0.0
    gf_total = 0.0
    paired = 0
    for tid in sorted(by_id):
        rows = by_id[tid]
        gdstk = rows.get("gdstk")
        gf = rows.get("gdsfactory")
        t_gdstk = gdstk["elapsed_s"] if gdstk else None
        t_gf = gf["elapsed_s"] if gf else None
        if isinstance(t_gdstk, (int, float)) and isinstance(t_gf, (int, float)) and t_gdstk > 0 and gdstk["status"] == "ok" and gf["status"] == "ok":
            speedup = f"{t_gf / t_gdstk:.2f}x"
            gdstk_total += t_gdstk
            gf_total += t_gf
            paired += 1
        else:
            speedup = "—"
        status_parts = []
        for name, r in (("gdstk", gdstk), ("gdsfactory", gf)):
            if r is None:
                status_parts.append(f"{name}:skip")
            else:
                status_parts.append(f"{name}:{r['status']}")
        tw.write_line(
            f"{tid:<42} {_fmt(t_gdstk):>10} {_fmt(t_gf):>15} {speedup:>10}"
            f"   {'/'.join(status_parts)}"
        )

    if paired:
        tw.write_line("-" * len(header))
        tw.write_line(
            f"{'TOTAL (paired ok)':<42} {_fmt(gdstk_total):>10} "
            f"{_fmt(gf_total):>15} {(gf_total / gdstk_total):>9.2f}x   "
            f"{paired} cells"
        )
