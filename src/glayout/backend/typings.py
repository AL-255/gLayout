"""Native typings — pure aliases, no gdsfactory dependency.

The class names (`Component`, `ComponentReference`, `Port`) reference the
backend's own module exports, so this file stays correct as those
classes are swapped from re-exports to native implementations.

Audited surface (from `src/glayout/`):
  Component, ComponentReference, Port, Layer, PathType, ComponentOrReference
"""
from __future__ import annotations

import pathlib
from typing import Union

from glayout.backend.component import Component
from glayout.backend.component_reference import ComponentReference
from glayout.backend.port import Port


# Matches gdsfactory's definitions verbatim (gdsfactory/typings.py:139-156).
Layer = tuple[int, int]
PathType = Union[str, pathlib.Path]
ComponentOrReference = Union[Component, ComponentReference]


__all__ = [
    "Component",
    "ComponentReference",
    "Port",
    "Layer",
    "PathType",
    "ComponentOrReference",
]
