"""Native boolean — uses gdstk.boolean directly.

gdsfactory's boolean was already a thin wrapper around gdstk.boolean; we
strip the LayerSpec/PDK lookup (glayout's two call sites pass `(layer,
datatype)` tuples or rely on the default `(1, 0)`) and the operand-name
normalization, then return a Component carrying the result on the named
layer. Polygons are fractured to gdstk's default max-vertex limit, same
as gdsfactory did.

`Component` is imported from `glayout.backend.component`, so this module
needs no edit when Component itself is later swapped to a native class.
"""
from __future__ import annotations

from typing import Sequence, Union

import gdstk

from glayout.backend.component import Component


_LayerTuple = tuple[int, int]
_Operand = Union["Component", object]  # Component | ComponentReference | gdstk.Polygon


def _to_polygons(items: Union[_Operand, Sequence[_Operand]]) -> list[gdstk.Polygon]:
    items = list(items) if isinstance(items, (list, tuple)) else [items]
    out: list[gdstk.Polygon] = []
    for e in items:
        if hasattr(e, "get_polygons"):
            out.extend(e.get_polygons())
        elif hasattr(e, "polygons"):
            out.extend(e.polygons)
        else:
            out.append(e)
    return out


_OP_ALIASES = {
    "a+b": "or",
    "a-b": "not",
    # b-a: handled by swapping operands; canonical name stays "not"
}
_VALID_OPS = {"not", "and", "or", "xor"}


def boolean(
    A: Union[_Operand, Sequence[_Operand]],
    B: Union[_Operand, Sequence[_Operand]],
    operation: str,
    precision: float = 1e-4,
    layer: _LayerTuple = (1, 0),
) -> Component:
    """Boolean op between two Component/Reference (or list thereof) operands.

    `operation` ∈ {'not', 'and', 'or', 'xor', 'A-B', 'B-A', 'A+B'}.
    Result polygons land on `layer` (gds_layer, gds_datatype)."""
    A_polys = _to_polygons(A)
    B_polys = _to_polygons(B)

    op = operation.lower().replace(" ", "")
    if op == "b-a":
        op = "not"
        A_polys, B_polys = B_polys, A_polys
    else:
        op = _OP_ALIASES.get(op, op)
    if op not in _VALID_OPS:
        raise ValueError(
            f"boolean() operation={operation!r} not recognized — must be one "
            "of 'not', 'and', 'or', 'xor', 'A-B', 'B-A', 'A+B'"
        )

    gds_layer, gds_datatype = layer

    if not A_polys and not B_polys:
        result_polys = None
    elif not B_polys and op in ("not",):
        # A − ∅ = A
        result_polys = A_polys
    elif not A_polys and op == "xor":
        result_polys = B_polys
    elif not B_polys and op == "xor":
        result_polys = A_polys
    elif (not A_polys or not B_polys) and op != "or":
        # and/not with one empty side → empty
        result_polys = None
    else:
        result_polys = gdstk.boolean(
            operand1=A_polys,
            operand2=B_polys,
            operation=op,
            precision=precision,
            layer=gds_layer,
            datatype=gds_datatype,
        )

    out = Component()
    if result_polys:
        added = out.add_polygon(result_polys, layer=layer)
        # gdsfactory fractured each added polygon; preserve that to keep
        # downstream max-vertex behaviour identical.
        if isinstance(added, list):
            for poly in added:
                if hasattr(poly, "fracture"):
                    poly.fracture(precision=precision)
        elif hasattr(added, "fracture"):
            added.fracture(precision=precision)
    return out


__all__ = ["boolean"]
