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

# Refill a free slot immediately rather than waiting for the slowest pytest
# in a fixed batch. wait -n requires Bash >= 5.1 (Ubuntu 24.04 provides 5.2).
finish_one() {
  local finished='' status=0 index
  if wait -n -p finished "${pids[@]}"; then
    status=0
  else
    status=$?
  fi
  if [[ -z "${finished:-}" ]]; then
    # Fallback if every child completed before wait -n was called.
    finished="${pids[0]}"
    if wait "$finished"; then status=0; else status=$?; fi
  fi
  for index in "${!pids[@]}"; do
    if [[ "${pids[index]}" == "$finished" ]]; then
      echo "::group::${names[index]}"
      cat "${logs[index]}"
      echo "::endgroup::"
      unset 'pids[index]' 'names[index]' 'logs[index]'
      pids=("${pids[@]}")
      names=("${names[@]}")
      logs=("${logs[@]}")
      if (( status != 0 )); then failed=1; fi
      return 0
    fi
  done
  echo "Plugin test worker status could not be matched to a child PID" >&2
  exit 2
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
      finish_one
    fi
  fi
done
while (( ${#pids[@]} )); do
  finish_one
done

echo "Tested $count plugin package(s) (max parallel: $jobs)"
exit "$failed"
