#!/usr/bin/env bash
set -euo pipefail

# Keep local and release invocations sequential. CI workflows opt into a
# conservative parallelism explicitly; packages use distinct pytest processes.
jobs="${CIFERECA_PLUGIN_TEST_JOBS:-1}"
if ! [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || (( jobs > 4 )); then
  echo "CIFERECA_PLUGIN_TEST_JOBS must be an integer between 1 and 4" >&2
  exit 2
fi

failed=0
count=0

if (( $# )); then
  targets=("$@")
else
  shopt -s nullglob
  targets=(src/plugins/*)
  shopt -u nullglob
fi

logdir="$(mktemp -d)"
trap 'rm -rf "$logdir"' EXIT
pids=()
names=()
logs=()

finish_batch() {
  local index
  for index in "${!pids[@]}"; do
    if ! wait "${pids[index]}"; then
      failed=1
    fi
    echo "::group::${names[index]}"
    cat "${logs[index]}"
    echo "::endgroup::"
  done
  pids=()
  names=()
  logs=()
}

for p in "${targets[@]}"; do
  if [ -f "$p/cifereca-plugin.json" ] && [ -d "$p/tests" ]; then
    count=$((count + 1))
    log="$logdir/$count.log"
    (cd "$p" && python -m pytest -q) > "$log" 2>&1 &
    pids+=("$!")
    names+=("$(basename "$p")")
    logs+=("$log")
    if (( ${#pids[@]} >= jobs )); then
      finish_batch
    fi
  fi
done
if (( ${#pids[@]} )); then
  finish_batch
fi

echo "Tested $count plugin package(s) (max parallel: $jobs)"
exit "$failed"
