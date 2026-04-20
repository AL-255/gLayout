"""Shared pytest configuration and fixtures.

Sets the gdstk backend by default since it doesn't require gdsfactory to be
installed. Callers can override with `GLAYOUT_BACKEND=gdsfactory` if gdsfactory
is available in the environment.
"""
import os

import pytest

os.environ.setdefault("GLAYOUT_BACKEND", "gdstk")
# MappedPDK requires PDK_ROOT to be set (used to locate DRC/LVS files). The
# tests here only build layouts, so any existing path is fine.
os.environ.setdefault("PDK_ROOT", "/tmp")


def pytest_addoption(parser):
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="Also run tests marked @pytest.mark.slow (heavyweight cells that "
             "take minutes to build).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="--runslow not set")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(scope="session")
def sky130_pdk():
    """The sky130 MappedPDK instance used for layout tests."""
    from glayout.pdk.sky130_mapped.sky130_mapped import sky130_mapped_pdk
    return sky130_mapped_pdk
