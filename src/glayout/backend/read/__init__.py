"""Native `import_gds` — uses gdstk.read_gds directly.

The gdsfactory version was already a thin wrapper around gdstk.read_gds;
its complexity came from the `@cell` cache decorator, OmegaConf YAML
metadata loading, and the `Settings`/`get_name_short` integrations —
glayout uses none of those (the two call sites in
`util/component_array_create.py` and
`cells/composite/fvf_based_ota/sky130_ota_tapeout.py` pass a single
positional path and consume the returned Component directly).

We drop those features and keep only what glayout uses: load the file,
rebuild the cell hierarchy as Components + ComponentReferences, return
the top cell.

`Component` and `ComponentReference` are imported from
`glayout.backend.*`, so this module needs no edit when those classes are
later swapped to native implementations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import gdstk

from glayout.backend.component import Component
from glayout.backend.component_reference import ComponentReference


def import_gds(
    gdspath: Union[str, Path],
    cellname: Optional[str] = None,
    gdsdir: Optional[Union[str, Path]] = None,
    **kwargs,
) -> Component:
    """Load a GDS (or OAS) file and return its top cell as a Component.

    Extra kwargs are merged into `component.info` to preserve the
    metadata-passing surface the gdsfactory version exposed (e.g.
    `polarization="te"`)."""
    gdspath = Path(gdsdir) / Path(gdspath) if gdsdir else Path(gdspath)
    if not gdspath.exists():
        raise FileNotFoundError(f"No file {str(gdspath)!r} found")

    suffix = gdspath.suffix.lower()
    if suffix == ".gds":
        lib = gdstk.read_gds(str(gdspath))
    elif suffix == ".oas":
        lib = gdstk.read_oas(str(gdspath))
    else:
        raise ValueError(f"gdspath.suffix {gdspath.suffix!r} not .gds or .oas")

    top = lib.top_level()
    top_names = [c.name for c in top]
    if not top_names:
        raise ValueError(f"no top cells found in {str(gdspath)!r}")

    # Wrap every gdstk Cell in a Component and stash the gdstk Cell on
    # `_cell` (gdsfactory's convention) so references and downstream code
    # can reach into the underlying geometry.
    cell_to_comp: dict[gdstk.Cell, Component] = {}
    name_to_comp: dict[str, Component] = {}
    for c in lib.cells:
        D = Component(name=c.name)
        D._cell = c
        cell_to_comp[c] = D
        name_to_comp[c.name] = D

    if cellname is not None:
        if cellname not in name_to_comp:
            raise ValueError(
                f"cell {cellname!r} not in {gdspath} "
                f"(available: {list(name_to_comp)})"
            )
    elif len(top) == 1:
        cellname = top[0].name
    else:
        raise ValueError(
            f"import_gds() multiple top-level cells in {str(gdspath)!r}; "
            f"specify `cellname` from {top_names}"
        )

    # Recreate the reference graph using ComponentReference so callers can
    # treat the loaded hierarchy the same as any glayout-built one.
    for c, D in cell_to_comp.items():
        for e in c.references:
            ref_device = cell_to_comp[e.cell]
            ref = ComponentReference(
                component=ref_device,
                origin=e.origin,
                rotation=e.rotation,
                magnification=e.magnification,
                x_reflection=e.x_reflection,
                columns=e.repetition.columns or 1,
                rows=e.repetition.rows or 1,
                spacing=e.repetition.spacing,
                v1=e.repetition.v1,
                v2=e.repetition.v2,
            )
            D._register_reference(ref)
            D._references.append(ref)
            ref._reference = e

    component = name_to_comp[cellname]
    if kwargs:
        component.info.update(**kwargs)
    component.imported_gds = True
    return component


__all__ = ["import_gds"]
