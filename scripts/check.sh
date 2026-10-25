#!/usr/bin/env bash
set -euo pipefail
python -m pytest -q
python -m outdoorgraph.cli demo --scene configs/demo_valley.json --out runs/demo_valley
