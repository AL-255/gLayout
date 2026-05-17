# Speedup notes

Cumulative summary of the performance work on top of commit `7cf1d1e` (the GF180 LVS fix that opens this branch). Numbers are wall-clock medians of 3 reps × 9 sky130 CI cells, measured via `benchmark/bench_cells_wallclock.py`.

## Headline result

| State | sky130 total | sky130 opamp |
|---|---|---|
| Vanilla gdsfactory (bench `vanilla` mode, all monkey-patches off) | 33.64 s | 23.64 s |
| Optimized native (this branch) | **1.56 s** | **0.86 s** |
| **Speedup** | **21.5×** | **27.4×** |

Per-cell ratios on sky130 range from **9.2×** (transmission_gate) to **27.4×** (opamp). gf180 mirrors closely at **22.0×** total. CI: sky130 9/9 DRC + 8/8 LVS, gf180 9/9 DRC + 7/8 LVS (pre-existing opamp LVS).

The remaining ~1.8× gap vs the experimental gdstk-rewrite port at `../gLayout-port-gdstk` (0.88 s total) is purely architectural — that port skips `gdsfactory.Component` construction entirely; this port keeps it but optimizes the hot paths.

## How the speedup is structured

All optimizations live in two layers:

1. **`src/glayout/backend/_speedups.py`** — monkey-patches applied at `MappedPDK.activate()` time. None of these are visible to callers; they swap in faster implementations of gdsfactory hot-paths.
2. **A handful of source-level edits** in `glayout/util/`, `glayout/backend/components/`, and `glayout/backend/cell.py` that fix dead-code waste glayout had inherited from earlier development.

The `glayout.backend` shim layer (commits `1e8c91e`–`9980a2e`) is the foundation: it gives a single seam where the patches install, and lets glayout call sites stay unchanged.

## Patches in `_speedups.py` (applied at `MappedPDK.activate()`)

Each one is a focused replacement for a gdsfactory function or class method that showed up in `cProfile` of an opamp build.

| Patch | What it does | Why it wins |
|---|---|---|
| `_fast_snap_to_grid` | Pure-Python `snap_to_grid` that fast-paths scalar / tuple / 2-element-ndarray inputs and only falls back to numpy for general arrays. Caches the PDK grid (nm). | The original wraps every input in `np.array(...)` before snapping — millions of small allocations per build. |
| `_patch_gf_port_init` | Drop-in `Port.__init__` that does inline snap on `tuple`/`list` centers, skips the cross_section validation path, and skips writing the 4 "always default in glayout" attrs (info, port_type, cross_section, shear_angle) so they live on the class instead of the instance. | Original goes through `snap_to_grid` (np.array path), full validation, and 10 attribute writes per port. |
| `_patch_gf_port_copy` | `Port.copy` / `Port._copy` via a single `__dict__.copy()` instead of 10 individual setattr's. | C-level dict copy is ~2× faster than ten Python attribute writes — and preserves any non-canonical attrs like `reference` that gdsfactory sets later. |
| `_install_port_class_defaults` | Sets class-level defaults for the 4 always-default Port attrs (`shear_angle=None`, `cross_section=None`, `info={}`, `port_type="electrical"`). Combined with the `fast_init` skip above, ports end up with 6-key `__dict__` instead of 10. | Smaller dict → 30 % cheaper `dict.copy()`. |
| `_patch_gf_pdk_get_layer` | Type-checks first then short-circuits on the dominant `(int, int)`-tuple case. | Original walks an isinstance ladder hit by 1.9M calls per build. |
| `_patch_gf_ref_ports` | Three parts wrapped in one property: (a) a cache keyed on transform + parent.ports length; (b) an **identity-transform fast path** that returns `parent.ports` directly with no per-port allocation or transform math; (c) a **pure-Python `_transform_port`** using `math.cos/sin/radians` and tuple arithmetic. The non-identity slow path is folded into one loop that does transform + parent/reference stamping in a single pass (vs original's two passes). | Original re-runs a numpy-heavy per-port transform on every `ref.ports` read and rebuilds the dict every time. With this patch the 1162 identity-ref reads in opamp do zero per-port work. |
| `_patch_gf_component_add_port` | Inline shallow port copy + parent-set for the dominant `add_port(name=str, port=Port)` shape, skipping `get_layer()`, `fast_copy()`, and the redundant attribute resets. | 1.6M calls/opamp, all in the form add_ports invokes. |
| `_patch_gf_component_add_ports` | Two fast paths: (1) **same-name share** when called without prefix/suffix — just `self.ports.update(ports)`, sharing Port objects with the source. (2) **prefix path** does one tight loop with `__dict__.copy()` per port. Also detects `prefix="array_"` and short-circuits to no-op (see "Source-level fixes" below for why). | Skips `add_port`'s per-call frame + saves the per-port copy for the no-prefix case. |
| `_patch_gf_component_flatten` | Replaces `Component.flatten` so the new flat component **shares the source's port objects** (only the outer dict is copied). | Original calls `add_ports(self.ports)` on the new component, re-allocating ~600 ports per flatten — 861 flattens per opamp build. |
| `_strip_validate_arguments_from_loaded_modules` | Walks every loaded `glayout.*` module and replaces pydantic `@validate_arguments` wrappers with their `raw_function`. Detection: looks for the `raw_function` + `vd` + `model` tuple pydantic sets on the wrapper. | 130 glayout helpers had `@validate_arguments`; the per-call `pydantic.deprecated.decorator.build_values` was 428k calls / 1 s per opamp build. |
| disable `assert_ports_on_grid` | Replaces `Component.assert_ports_on_grid` and `Port.assert_on_grid` with no-ops. | Pre-write paranoia loop; coords get rounded correctly at GDS-write time anyway. |
| memoize MappedPDK methods | Patches `pdk.get_glayer`, `pdk.get_grule`, etc. with id-keyed dict memoization. | Called thousands of times per build with the same args. |
| skip sort in `get_ports_list()` | When called with no filter kwargs, returns `list(self.ports.values())` instead of `select_ports → sort_ports_clockwise`. | 2491 calls × ~70 µs of sorting per opamp; every glayout caller feeds the result straight into `add_ports` which doesn't care about order. |

## Source-level edits

These are real changes to glayout's own code (not gdsfactory). They mostly remove dead work or fix obvious waste.

| File | Change | Saving |
|---|---|---|
| `glayout/backend/cell.py` | `clear_cache()` is now a no-op. Glayout's composite cells call it ~9 times per opamp build to defend against gdsfactory's name-based cache; our `_native_cell` uses arg-digest names so collisions are impossible — flushing was destroying a 47 %-hit-rate cache for no reason. | 436 sub-cell rebuilds per opamp turn into 436 cache hits. |
| `glayout/util/snap_to_grid.py` | Dropped the redundant `.copy()` after `.flatten()` in `component_snap_to_grid`. `Component.flatten()` already returns a fresh component; the second copy was a full deep copy of every polygon + port for zero semantic gain. | ~1.2 s saved on opamp. |
| `glayout/util/port_utils.py` | `rename_ports_by_orientation__call`: replace `any(name==edge for edge in [...])` generator-expr scans with frozenset membership. `rename_component_ports`: skip the pop/insert path entirely when `new_name == old_name`. Also rewrote with collision-safe pop-then-reinsert (commit `36817bb`) so two ports swapping names doesn't lose one. | ~40 % cut on `rename_ports_by_orientation__call`'s 413k calls. |
| `glayout/backend/components/__init__.py` | `rectangle` and `rectangular_ring` swapped from gdsfactory's `@cell`-wrapped versions to thin `_fast_gf_rectangle` / `_fast_gf_rectangular_ring` functions that build a plain `gdsfactory.Component` inline. Skips the @cell decorator's signature-inspect + arg-digest + cache lookup + naming + assert + lock chain. | 444 + 60 calls per opamp at ~500 µs each = ~250 ms gone. |

## The `glayout.backend` shim layer

The `glayout.backend` package (commit `1e8c91e` and onward) is the foundation that makes the rest possible:

- Re-exports every gdsfactory symbol glayout used to import directly: `Component`, `ComponentReference`, `Port`, `cell`, `clear_cache`, `Polygon`, `boolean`, `snap_to_grid`, `text_freetype`, `route_sharp`, etc.
- Stages native gdstk-only replacements for most of those classes/functions (`_NativeComponent`, `_NativeComponentReference`, `_NativePort`, `_NativePdk`, …) so glayout *could* in principle drop gdsfactory entirely.
- Today the active exports are still gdsfactory-backed for the classes (with `_speedups.py` patches on top), and partially native for the procedural helpers (`text_freetype`, `route_sharp`, `boolean` on the native path).
- Why it matters: every glayout import goes through `glayout.backend.X` instead of `gdsfactory.X`, so the moment we want to swap an implementation it's a one-line change at the seam.

The `_native_cell` decorator (in `glayout/backend/cell.py`) is the most important non-staged piece: it replaces `gdsfactory.@cell` for every glayout-decorated function. It applies the active PDK's `default_decorator` (e.g. sky130_add_npc) post-build to preserve sky130 DRC, uses an arg-digest cache (so identical calls always return the same Component), locks the result, and content-hashes for dedup.

## Things tried and reverted

A few experiments looked promising in micro-benchmarks but lost on the integration measure:

- **Skip the eager `_local_ports` dictcomp in `ComponentReference.__init__`** — slower on the fast_ports_get non-identity slow path because Python-level lazy creation is more expensive than the C-level dictcomp.
- **`__slots__` on Port** — direct micro-benchmark showed slot copies are *slower* than `dict.copy()` for 6 attributes (C-level dict copy beats N×setattr).
- **Native `Component` cutover (iter-17, iter-23)** — repeatedly tried, repeatedly reverted: surface gaps in the native classes (move_copy, get_ports_list, mirror_y, …) and coordinate-precision drift in sky130 (5–55 nm shifts breaking DRC on 5 cells). The native classes remain staged but not active.

## Bench setup

The `benchmark/` directory (gitignored — local artifact) contains:

- `bench_cells_wallclock.py` — drives 9 CI cells through subprocesses with two modes:
  - `native`: current state of `glayout.backend` with `apply_speedups` running at PDK activate.
  - `vanilla`: forces native classes back to gdsfactory + disables `apply_speedups` entirely. This is the true pre-optimization baseline.
- `cells_wallclock.json` — raw timing data from the latest run.
- `SPEED_GAP_ANALYSIS.md` — narrative on why the experimental port at `../gLayout-port-gdstk` is still ~1.8× faster (architectural — that port rewrites primitives in pure gdstk).
- `bench_native_vs_gdsfactory.py` — older per-operation micro-benchmark.

To reproduce the headline numbers:

```
PDK_ROOT=/path/to/pdk PYTHONPATH=src \
    python benchmark/bench_cells_wallclock.py --reps 3 --pdks sky130
```

## CI verification

CI was kept green at every commit:

- sky130: 9/9 DRC + 8/8 LVS clean throughout.
- gf180: 9/9 DRC + 7/8 LVS (the one LVS failure on opamp is pre-existing and unrelated to the speedups).

Driver script: `tests/run_ci_locally.py --pdks sky130,gf180`.
