## Tests

This folder has two categories of tests:

1. **Structural / regression** — import-path canonicalization and repo layout
   (`test_import_paths.py`, `test_repo_layout.py`). These run fast and are
   gated by `tests/run_regression.sh`.

2. **Layout-generation** — exercise the actual layout engine:
   - `test_gdstk_backend.py` — sanity checks for the gdstk-backed
     `Component` / `ComponentReference` / `Port`.
   - `test_cells_layout.py` — builds every cell in `src/glayout/cells/`,
     verifies the bounding box is non-empty, writes a GDS, and round-trips
     it through `gdstk.read_gds`. Known-broken cells are marked `xfail` with
     the failing error summary; heavyweight cells are marked `slow`.

### Running

From the repo root:

```bash
# fast tests only
PYTHONPATH=src pytest tests/

# include slow/heavyweight cells (adds several minutes)
PYTHONPATH=src pytest tests/ --runslow

# run a single parametrized case
PYTHONPATH=src pytest tests/test_cells_layout.py -k diff_pair
```

### Backend selection

Layout tests default to the gdstk backend (`GLAYOUT_BACKEND=gdstk`), set in
`conftest.py`. Override by exporting a different value before pytest runs if
you want to cross-check against gdsfactory:

```bash
GLAYOUT_BACKEND=gdsfactory PYTHONPATH=src pytest tests/
```

### Expanding the cell matrix

`test_cells_layout.py` is driven by a single `CASES` list of `CellCase`
entries. Add a new cell by appending a row with its module path, function
name, and a lambda that returns the kwargs dict (so the pdk fixture can be
injected lazily). Mark `xfail=` with the short reason when a cell is known
broken, and `slow=True` for cells that take minutes to build.
