#!/usr/bin/env bash
# P0 #787: reject a strict plugins-only ZIP on an ACTUAL historic installed
# Core, rather than faking a version mismatch by editing the current Core.
set -euo pipefail
if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 <plugins-only-snapshot.zip>" >&2
  exit 2
fi
archive="$(readlink -f "$1")"
test -s "$archive"
digest="$(sha256sum "$archive" | awk '{print $1}')"
historic_tag="v0.9.44+dev.10"
historic_version="0.9.44+dev.10"
root="${CIFERECA_P0_GATE_ROOT:-${RUNNER_TEMP:-/tmp}/cifereca-p0-gate}-historical"
rm -rf "$root"
mkdir -p "$root/source" "$root/bin" "$root/data" "$root/config" "$root/state" "$root/cache"
export CIFERECA_INSTALL_ROOT="$root/runtime"
export CIFERECA_BIN_DIR="$root/bin"
export XDG_DATA_HOME="$root/data"
export XDG_CONFIG_HOME="$root/config"
export XDG_STATE_HOME="$root/state"
export XDG_CACHE_HOME="$root/cache"

# A fixed historic release tag, available from fetch-depth: 0 on both
# the PR validation workflow and the explicit release workflow.
git rev-parse --verify "refs/tags/${historic_tag}^{commit}" >/dev/null
git archive "$historic_tag" | tar -x -C "$root/source"
test "$(tr -d '[:space:]' < "$root/source/VERSION")" = "$historic_version"
test "$(tr -d '[:space:]' < VERSION)" != "$historic_version"
# Build a real historic Core-only installation with THAT tag's installer.
# v0.9.44+dev.10 predates attested offline wheelhouse enforcement and still
# supports source fallback. Using today's updater here incorrectly fails its
# provenance guard, before testing historic-version incompatibility at all.
# This is a disposable CI-only XDG tree; the CURRENT strict updater is invoked
# separately below and must still reject the installed historic runtime.
rm -f "$root/source/src/cifereca/plugin-release.json"
PYTHONPATH="$root/source/src" python - "$root/source" "$historic_version" <<'PY'
import sys
from pathlib import Path
from cifereca.updater import install_source, install_root, _metadata

source = Path(sys.argv[1])
expected = sys.argv[2]
result = install_source(source, keep=3)
assert result.get("ok") and result.get("current"), result
target = (install_root() / "current").resolve(strict=True)
assert _metadata(target)[1] == expected, (_metadata(target), expected)
assert (target / ".venv/bin/cifereca-update-dev").is_file(), target
print(f"Historic Core installed: {expected} ({target.name})", flush=True)
PY

runtime="$(readlink -f "$CIFERECA_INSTALL_ROOT/current")"
marker="$XDG_DATA_HOME/cifereca/.p0-preserve-data"
mkdir -p "$(dirname "$marker")" "$XDG_DATA_HOME/cifereca/plugins/packages"
printf 'historic-core-preserve-user-data\n' > "$marker"
printf 'historic-core-preserve-package\n' > "$XDG_DATA_HOME/cifereca/plugins/packages/.p0-existing-plugin"
checksum="$(sha256sum "$marker" | awk '{print $1}')"
package_marker="$XDG_DATA_HOME/cifereca/plugins/packages/.p0-existing-plugin"
package_checksum="$(sha256sum "$package_marker" | awk '{print $1}')"

# Old installed CLI does not implement the newer strict protocol: it must
# refuse these flags, not silently install the ZIP as a generic snapshot.
historic_cli="$runtime/.venv/bin/cifereca-update-dev"
if "$historic_cli" --help | grep -q -- '--require-plugins-only'; then
  echo "Pinned historic Core unexpectedly supports the strict protocol" >&2
  exit 1
fi
set +e
"$historic_cli" "$archive" --require-plugins-only --expected-sha256 "$digest" \
  --json > "$root/historic-cli.json" 2>&1
historic_rc=$?
set -e
if [[ "$historic_rc" -eq 0 ]]; then
  echo "Historic CLI unexpectedly accepted strict plugins-only ZIP" >&2
  exit 1
fi
grep -q -- '--require-plugins-only' "$root/historic-cli.json"

# Also test today's strict updater against that genuinely installed older
# Core. This specifically proves the runtime detects a mismatched target
# BEFORE backups, package replacement or Core installation.
set +e
PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" python -m cifereca.dev_updater \
  "$archive" --require-plugins-only --expected-sha256 "$digest" \
  --json > "$root/new-cli-old-target.json" 2>&1
strict_rc=$?
set -e
if [[ "$strict_rc" -eq 0 ]]; then
  echo "Strict updater unexpectedly accepted an older installed Core" >&2
  exit 1
fi
grep -q 'Core cible' "$root/new-cli-old-target.json"

test "$(readlink -f "$CIFERECA_INSTALL_ROOT/current")" = "$runtime"
test "$(sha256sum "$marker" | awk '{print $1}')" = "$checksum"
test "$(sha256sum "$package_marker" | awk '{print $1}')" = "$package_checksum"
test "$(tr -d '[:space:]' < "$runtime/VERSION")" = "$historic_version"
test ! -d "$XDG_STATE_HOME/cifereca/dev-updates"
echo "historical Core gate passed: $historic_tag refused; no Core, plugin or user-data mutation"
