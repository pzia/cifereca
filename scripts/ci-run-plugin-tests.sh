#!/usr/bin/env bash
set -euo pipefail

failed=0
count=0

if (( $# )); then
  targets=("$@")
else
  shopt -s nullglob
  targets=(src/plugins/*)
  shopt -u nullglob
fi

for p in "${targets[@]}"; do
  if [ -f "$p/cifereca-plugin.json" ] && [ -d "$p/tests" ]; then
    count=$((count + 1))
    echo "::group::$(basename "$p")"
    if ! (cd "$p" && python -m pytest -q); then
      failed=1
    fi
    echo "::endgroup::"
  fi
done

echo "Tested $count plugin package(s)"
exit "$failed"
