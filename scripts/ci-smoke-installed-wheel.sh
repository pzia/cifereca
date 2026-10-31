#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <wheel>" >&2
  exit 2
fi

wheel="$1"
if [ ! -f "$wheel" ]; then
  echo "wheel not found: $wheel" >&2
  exit 2
fi

if [ -n "${CIFERECA_SMOKE_VENV:-}" ]; then
  venv_dir="$CIFERECA_SMOKE_VENV"
  rm -rf "$venv_dir"
  cleanup=false
else
  venv_dir="$(mktemp -d "${TMPDIR:-/tmp}/cifereca-installed.XXXXXX")"
  cleanup=true
fi

if [ "$cleanup" = true ]; then
  trap 'rm -rf "$venv_dir"' EXIT
fi

python -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --disable-pip-version-check "$wheel"

(
  cd "${TMPDIR:-/tmp}"
  "$venv_dir/bin/python" -c "import cifereca; print(cifereca.__file__)"
  # Validate the installed wheel, outside the source checkout. An expected
  # CI SHA must be identical to the immutable receipt, not merely to HEAD
  # observed in the producer workspace after installation.
  "$venv_dir/bin/python" - <<'PY'
import json
import os
from pathlib import Path
import cifereca
from cifereca.startup_revision import build_identity

receipt = Path(cifereca.__file__).with_name("_build_identity.json")
assert receipt.is_file(), "missing packaged build receipt"
data = json.loads(receipt.read_text(encoding="utf-8"))
assert data["schema_version"] == 1
assert data["build_version"] == cifereca.__version__
identity = build_identity()
expected = os.environ.get("CIFERECA_EXPECTED_BUILD_SHA")
if expected:
    assert identity["status"] == "known", identity
    assert identity["commit_sha"] == expected.lower(), (identity, expected)
    assert data["commit_sha"] == expected.lower(), data
else:
    assert identity["status"] in ("known", "unavailable")
print("installed build_identity", identity["status"], identity["commit_sha"])
PY
  "$venv_dir/bin/cifereca-cli" --help >/dev/null
  "$venv_dir/bin/cifereca-update" --help >/dev/null
  "$venv_dir/bin/cifereca-update-dev" --help >/dev/null
  "$venv_dir/bin/cifereca-plugin-test" --help >/dev/null
)
