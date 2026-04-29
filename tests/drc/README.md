# Cell DRC

Runs Klayout DRC against every glayout cell for a given PDK.

## Local usage (with iic-osic-tools)

The CI workflow uses [`hpretl/iic-osic-tools`](https://github.com/iic-jku/iic-osic-tools),
which ships klayout, magic, netgen and the sky130A / gf180mcuD PDKs at
`/foss/pdks`. The image is Ubuntu 24.04 with only Python 3.12, but glayout pins
`gdsfactory<=7.7.0` / `numpy<=1.24`, so we install Python 3.10 from deadsnakes
and run glayout in a venv.

```bash
docker run --rm -it \
  -v "$PWD":/work -w /work \
  --user root --entrypoint /bin/bash \
  hpretl/iic-osic-tools:latest -lc '
    set -euxo pipefail
    unset PYTHONPATH    # the image sets it to 3.12 paths
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends \
      software-properties-common ca-certificates gnupg curl >/dev/null
    add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
    apt-get update -qq
    apt-get install -y --no-install-recommends python3.10 python3.10-venv >/dev/null
    python3.10 -m venv /tmp/venv
    . /tmp/venv/bin/activate
    python -m pip install --upgrade pip wheel
    python -m pip install -e .
    python tests/drc/run_cell_drc.py --pdk sky130 --out-dir drc_results/sky130
    python tests/drc/run_cell_drc.py --pdk gf180  --out-dir drc_results/gf180
  '
```

## Local usage (host install)

```bash
# Klayout CLI must be installed and on PATH (https://www.klayout.org/).
pip install -e .

# PDK_ROOT must point at a directory; klayout DRC against the bundled deck
# does not need a real PDK install, but the gf180 PDK reads the env var at
# import time.
export PDK_ROOT=$(mktemp -d)

python tests/drc/run_cell_drc.py --pdk sky130 --out-dir drc_results/sky130
python tests/drc/run_cell_drc.py --pdk gf180  --out-dir drc_results/gf180
```

Pass `--deck <path>` to use a PDK-installed DRC deck instead of the bundled one.

A subset of cells can be selected with `--cells`:

```bash
python tests/drc/run_cell_drc.py --pdk sky130 --cells current_mirror_nfet,opamp
```

## Output

For each run the script writes:

- `<out-dir>/gds/<cell>.gds` — generated layout per cell
- `<out-dir>/reports/<cell>.lyrdb` — klayout violation database
- `<out-dir>/summary.json` — machine-readable summary
- `<out-dir>/junit.xml` — JUnit report consumed by the CI workflow

The script exits non-zero if any cell fails to build or has DRC violations.

## CI

`.github/workflows/drc.yml` runs the same script on every push and PR with a
matrix over `sky130` and `gf180`, and uploads the per-PDK output directory as
a build artifact.
