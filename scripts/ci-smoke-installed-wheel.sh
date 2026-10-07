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

venv_dir="$(mktemp -d "${TMPDIR:-/tmp}/cifereca-installed.XXXXXX")"
trap 'rm -rf "$venv_dir"' EXIT

python -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --disable-pip-version-check "$wheel"

(
  cd "${TMPDIR:-/tmp}"
  "$venv_dir/bin/python" -c "import cifereca; print(cifereca.__file__)"
  "$venv_dir/bin/cifereca-cli" --help >/dev/null
  "$venv_dir/bin/cifereca-update" --help >/dev/null
  "$venv_dir/bin/cifereca-update-dev" --help >/dev/null
  "$venv_dir/bin/cifereca-plugin-test" --help >/dev/null
)
