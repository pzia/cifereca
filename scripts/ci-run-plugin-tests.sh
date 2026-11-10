#!/usr/bin/env bash
set -euo pipefail

# Local and release invocations remain sequential. PR/main CI opts into three
# independent pytest processes, with immediate replacement on completion.
jobs="${CIFERECA_PLUGIN_TEST_JOBS:-1}"
if ! [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || (( jobs > 4 )); then
  echo "CIFERECA_PLUGIN_TEST_JOBS must be an integer between 1 and 4" >&2
  exit 2
fi

failed=0
count=0
active=0
if (( $# )); then
  targets=("$@")
else
  shopt -s nullglob
  targets=(src/plugins/*)
  shopt -u nullglob
fi

logdir="$(mktemp -d)"
trap 'rm -rf "$logdir"' EXIT
mkfifo "$logdir/completed"
exec 3<>"$logdir/completed"
pids=()
names=()
logs=()

# A pipe keeps all completions (including children that finished early).
# wait -n can miss already-reaped children on some Bash versions.
finish_one() {
  local completed status child_status
  if ! read -r completed status <&3; then
    echo "Plugin test completion channel closed unexpectedly" >&2
    exit 2
  fi
  if ! [[ "$completed" =~ ^[1-9][0-9]*$ ]] || \
     [[ -z "${pids[completed]:-}" ]] || ! [[ "$status" =~ ^[0-9]+$ ]]; then
    echo "Invalid or duplicate plugin test completion: $completed ($status)" >&2
    exit 2
  fi
  child_status=0
  if ! wait "${pids[completed]}"; then child_status=1; fi
  echo "::group::${names[completed]}"
  cat "${logs[completed]}"
  echo "::endgroup::"
  if (( status != 0 || child_status != 0 )); then failed=1; fi
  unset 'pids[completed]' 'names[completed]' 'logs[completed]'
  active=$((active-1))
}

for p in "${targets[@]}"; do
  if [[ -f "$p/cifereca-plugin.json" && -d "$p/tests" ]]; then
    count=$((count+1))
    active=$((active+1))
    log="$logdir/$count.log"
    names[count]="${p##*/}"
    logs[count]="$log"
    (
      status=0
      # Preserve the historical per-package contract gate after removing
      # duplicate local test modules. Synthetic runner fixtures are not migrated.
      case "${p##*/}" in
        baen|home-page|provider-kobo|epub_optimizer|provider-paris|ebooksgratuits|provider-gallica|provider-numilog|runtime-kobo-adept|provider-gutenberg|provider-bnf-catalogue)
          python3 "$(dirname "${BASH_SOURCE[0]}")/validate_plugin_contract.py" "$p" || status=$?
          ;;
      esac
      if (( status == 0 )); then
        (cd "$p" && python -m pytest -q) || status=$?
      fi
      printf '%s %s\n' "$count" "$status" >&3
      exit "$status"
    ) >"$log" 2>&1 &
    pids[count]=$!
    if (( active >= jobs )); then finish_one; fi
  fi
done
while (( active > 0 )); do finish_one; done
exec 3>&-

echo "Tested $count plugin package(s) (max parallel: $jobs)"
exit "$failed"
