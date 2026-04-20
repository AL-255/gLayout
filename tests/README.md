## Tests

This folder has two categories of tests:

1. **Structural / regression** — import-path canonicalization and repo layout
   (`test_import_paths.py`, `test_repo_layout.py`). These run fast and are
   gated by `tests/run_regression.sh`.

2. **Layout-generation** — exercise the actual layout engine:
   - `test_gdstk_backend.py` — sanity checks for the gdstk-backed
     `Component` / `ComponentReference` / `Port`.
   - `test_cells_layout.py` — builds every cell in `src/glayout/cells/`
     under **both** the `gdstk` and `gdsfactory` backends, verifies each
     build's bounding box is non-empty and writes a GDS, and records the
     wall-clock time. The terminal summary prints a per-cell timing table
     with the gdsfactory-vs-gdstk speedup. Known-broken cells are marked
     `xfail`; heavyweight cells are marked `slow`.
   - `_cell_worker.py` — subprocess entry point. Each (cell, backend) pair
     runs in its own interpreter so the backend binding is isolated.

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

`test_cells_layout.py` **always runs both backends** — each cell is
parametrized as `[<cell>-gdstk, <cell>-gdsfactory]` and dispatched to a
subprocess with `GLAYOUT_BACKEND` set accordingly, so there's no state
leakage between backends. If a backend isn't installed the corresponding
cases skip cleanly.

The other tests (`test_gdstk_backend.py`, structural tests) default to the
gdstk backend (set in `conftest.py`). Override by exporting
`GLAYOUT_BACKEND=gdsfactory` before running pytest.

### Reading the timing table

```
cell                         gdstk (s)  gdsfactory (s)   speedup   status
--------------------------------------------------------------------------
current_mirror                   0.247           6.412    25.93x   ok/ok
diff_pair                        0.376           6.210    16.51x   ok/ok
...
TOTAL (paired ok)                6.253          62.929    10.06x   6 cells
```

Columns:
- `gdstk (s)` / `gdsfactory (s)` — wall-clock build time inside the worker.
- `speedup` — `gdsfactory / gdstk`; blank when either side didn't succeed.
- `status` — `ok` / `error` / `timeout` for each backend.

The `TOTAL (paired ok)` row sums only the cells that succeeded on both
backends, so the aggregate speedup isn't skewed by partial results.

### Expanding the cell matrix

`test_cells_layout.py` is driven by a single `CASES` list of `CellCase`
entries. Add a new cell by appending a row with its module path, function
name, and a lambda that returns the kwargs dict (so the pdk fixture can be
injected lazily). Mark `xfail=` with the short reason when a cell is known
broken, and `slow=True` for cells that take minutes to build.
