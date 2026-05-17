"""Native `transformed` and `move` — no gdsfactory.functions dependency.

Both helpers wrap a Component / ComponentReference in a fresh Component
so the caller has a transformed, owned copy. The gdsfactory originals
were `@cell`-decorated (caching + name hashing); glayout uses both inside
hot loops where caching collisions caused bugs in the past, so dropping
`@cell` here is intentional. We also drop `copy_child_info` (used by
gdsfactory only to propagate settings between cached cells — unused
downstream in glayout).

Component / ComponentReference are imported from `glayout.backend.*` so
this module needs no edit when those classes are swapped to native
implementations.
"""
from __future__ import annotations

from typing import Optional, Tuple

from glayout.backend.component import Component
from glayout.backend.component_reference import ComponentReference


def _clone_reference(ref: ComponentReference) -> ComponentReference:
    """Return a new ComponentReference with the same parent and transform
    as `ref`. Mirrors gdsfactory.component.copy_reference for the fields
    we actually carry (no `name` propagation — glayout doesn't name
    references at this layer)."""
    return ComponentReference(
        component=ref.parent,
        columns=ref.columns,
        rows=ref.rows,
        spacing=ref.spacing,
        origin=ref.origin,
        rotation=ref.rotation,
        magnification=ref.magnification,
        x_reflection=ref.x_reflection,
        v1=ref.v1,
        v2=ref.v2,
    )


def move(
    component: Component,
    origin: Tuple[float, float] = (0, 0),
    destination: Optional[Tuple[float, float]] = None,
    axis: Optional[str] = None,
) -> Component:
    """Return a new Component that wraps a moved reference to `component`.
    Mirrors gdsfactory.functions.move (without the @cell decorator)."""
    container = Component()
    ref = container.add_ref(component)
    ref.move(origin=origin, destination=destination, axis=axis)
    container.add_ports(ref.ports)
    return container


def transformed(ref: ComponentReference) -> Component:
    """Return a new flattened Component with `ref`'s transformation baked in.

    Used by glayout placement code to materialize a transformed copy that
    can then be re-placed without inheriting the original ref's
    transform. Mirrors gdsfactory.functions.transformed but without the
    @cell decorator and without `info["transformed_cell"]` (unused
    downstream)."""
    container = Component()
    container.add(_clone_reference(ref))
    flat = container.flatten()
    flat.add_ports(ref.ports)
    return flat


__all__ = ["move", "transformed"]
