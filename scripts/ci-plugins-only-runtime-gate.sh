#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 <plugins-only-snapshot.zip>" >&2
  exit 2
fi

archive="$1"
if [[ ! -s "$archive" ]]; then
  echo "plugins-only snapshot not found: $archive" >&2
  exit 2
fi
archive="$(readlink -f "$archive")"
digest="$(sha256sum "$archive" | awk '{print $1}')"
base="${CIFERECA_P0_GATE_ROOT:-${RUNNER_TEMP:-${TMPDIR:-/tmp}}/cifereca-p0-gate}"

rm -rf "$base"
export CIFERECA_INSTALL_ROOT="$base/runtime"
export CIFERECA_BIN_DIR="$base/bin"
export XDG_DATA_HOME="$base/data"
export XDG_CONFIG_HOME="$base/config"
export XDG_STATE_HOME="$base/state"
export XDG_CACHE_HOME="$base/cache"
mkdir -p "$CIFERECA_BIN_DIR" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" "$XDG_STATE_HOME" "$XDG_CACHE_HOME"

# Stage a genuine Core-only source install. The full-release manifest would
# install every optional plugin and would make this targeted gate dependent on
# unrelated provider visibility; full-release smoke belongs to its own gate.
PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}" python - "$base/core-only-source" <<'PY'
import os
import shutil
import sys
from pathlib import Path
from cifereca.updater import install_source

# Release job pins the installation source to the GitHub-published Core tag.
# PR validation intentionally uses its checkout while no Core release exists.
source = Path(os.environ.get("CIFERECA_P0_CORE_SOURCE") or Path.cwd()).resolve()
if not source.is_dir() or not (source / "VERSION").is_file():
    raise SystemExit(f"Core source absent from runtime gate: {source}")
target = Path(sys.argv[1])
ignore = shutil.ignore_patterns(
    ".git", ".venv", ".updates", "release", "dist", "build",
    ".pytest_cache", "__pycache__", "*.pyc", "*.pyo", "plugin-release.json",
)
shutil.copytree(source, target, ignore=ignore)
assert not (target / "src/cifereca/plugin-release.json").exists()
result = install_source(target, keep=3)
if not result.get("ok") or not result.get("current"):
    raise SystemExit(f"isolated Core-only install failed: {result}")
PY

runtime_update_dev="$CIFERECA_BIN_DIR/cifereca-update-dev"
if ! "$runtime_update_dev" --help | grep -q -- '--require-plugins-only'; then
  echo "strict plugins-only updater support is missing from installed runtime" >&2
  exit 1
fi
# Seed exactly the selected package through the real strict updater, so the
# subsequent update can verify inactive state preservation and rollback.
"$runtime_update_dev" "$archive" --require-plugins-only --expected-sha256 "$digest" --json > "$base/seed.json"

# Track all selected packages instead of silently testing the first one.
selected_file="$base/selected-packages.tsv"
python - "$archive" "$selected_file" <<'PY'
import json
import sys
import zipfile
from pathlib import Path
with zipfile.ZipFile(sys.argv[1]) as z:
    matches = [name for name in z.namelist() if name.endswith("/cifereca-plugins-only.json")]
    if len(matches) != 1:
        raise SystemExit("plugins-only index missing or ambiguous")
    index = json.loads(z.read(matches[0]))
rows = index.get("packages")
if not isinstance(rows, list) or not rows:
    raise SystemExit("plugins-only package selection is empty")
selected = []
for row in rows:
    package_id, version = str(row["package_id"]), str(row["version"])
    if not package_id or not version or any(c in package_id + version for c in "\t\r\n/\\"):
        raise SystemExit("unsafe selected package id/version")
    selected.append((package_id, version))
if len({package_id for package_id, _ in selected}) != len(selected):
    raise SystemExit("duplicate selected packages")
Path(sys.argv[2]).write_text("".join(f"{pid}\t{version}\n" for pid, version in selected), encoding="utf-8")
PY

store_root="$XDG_DATA_HOME/cifereca/plugins/packages"
inactive_package="$(head -n 1 "$selected_file" | cut -f 1)"
while IFS=$'\t' read -r package_id package_version; do
  test -d "$store_root/$package_id/$package_version"
done < "$selected_file"

# First selected package is intentionally inactive; all others stay active.
# This exercises mixed active/inactive state with one multi-package ZIP.
while IFS=$'\t' read -r package_id package_version; do
  package_root="$store_root/$package_id"
  version_root="$package_root/$package_version"
  if [[ "$package_id" == "$inactive_package" ]]; then
    python - "$package_root/current.json" <<'PY'
import json
import sys
from pathlib import Path
path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["active"] = None
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  fi
  printf 'must disappear on successful replace\n' > "$version_root/.p0-success-sentinel"
done < "$selected_file"

mkdir -p "$XDG_DATA_HOME/cifereca"
printf 'p0-user-data-integrity\n' > "$XDG_DATA_HOME/cifereca/.p0-user-data"
data_before="$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')"

"$runtime_update_dev" "$archive"   --require-plugins-only   --expected-sha256 "$digest"   --dry-run   --json > "$base/dry-run.json"

"$runtime_update_dev" "$archive"   --require-plugins-only   --expected-sha256 "$digest"   --json > "$base/success.json"

while IFS=$'\t' read -r package_id package_version; do
  package_root="$store_root/$package_id"
  version_root="$package_root/$package_version"
  test ! -e "$version_root/.p0-success-sentinel"
  expected_active="$package_version"
  if [[ "$package_id" == "$inactive_package" ]]; then expected_active=""; fi
  python - "$package_root/current.json" "$expected_active" <<'PY'
import json
import sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = sys.argv[2] or None
if payload.get("active") != expected:
    raise SystemExit(f"active state changed: {payload!r}; expected {expected!r}")
PY
done < "$selected_file"

test "$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')" = "$data_before"

# #577: attestation against the actually installed plugin store, NOT merely
# the updater's exit code or the source manifest. Validate every selected
# package's receipt, exact nested ZIP digest, tag, Core and absent signature
# claim. This runs in both PR review and signed-release gates.
python -m scripts.ci_plugins_only_receipt_gate \
  --archive "$archive" --store "$store_root" --snapshot-sha256 "$digest"

tree_digest() {
  python - "$1" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
h = hashlib.sha256()
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    h.update(path.relative_to(root).as_posix().encode())
    h.update(b"\0")
    h.update(path.read_bytes())
    h.update(b"\0")
print(h.hexdigest())
PY
}

# A coherent but incompatible installed Core must fail before package mutation.
runtime="$(readlink -f "$CIFERECA_INSTALL_ROOT/current")"
cp "$runtime/pyproject.toml" "$runtime/pyproject.toml.p0"
cp "$runtime/VERSION" "$runtime/VERSION.p0"
sed -i 's/^version = ".*"/version = "0.0.1"/' "$runtime/pyproject.toml"
printf '0.0.1\n' > "$runtime/VERSION"
before_incompatible="$(tree_digest "$store_root")"
set +e
"$runtime_update_dev" "$archive"   --require-plugins-only   --expected-sha256 "$digest"   --json > "$base/incompatible.json" 2>&1
incompatible_rc=$?
set -e
mv "$runtime/pyproject.toml.p0" "$runtime/pyproject.toml"
mv "$runtime/VERSION.p0" "$runtime/VERSION"
if [[ "$incompatible_rc" -eq 0 ]]; then
  echo "incompatible installed Core unexpectedly accepted" >&2
  exit 1
fi
grep -q "Core cible" "$base/incompatible.json"
test "$(tree_digest "$store_root")" = "$before_incompatible"
test "$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')" = "$data_before"

# Force only the post-install inventory smoke to fail. The real package install
# runs first; automatic rollback must restore the exact pre-attempt package tree.
while IFS=$'\t' read -r package_id package_version; do
  printf 'must return after rollback\n' > "$store_root/$package_id/$package_version/.p0-rollback-sentinel"
done < "$selected_file"
before_rollback="$(tree_digest "$store_root")"
cli="$runtime/.venv/bin/cifereca-cli"
real_cli="$cli.p0-real"
mv "$cli" "$real_cli"
cat > "$cli" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "plugin" && "\${2:-}" == "list" ]]; then
  echo "forced P0 post-install smoke failure" >&2
  exit 86
fi
exec "$real_cli" "\$@"
EOF
chmod +x "$cli"

set +e
"$runtime_update_dev" "$archive"   --require-plugins-only   --expected-sha256 "$digest"   --json > "$base/rollback.json" 2>&1
rollback_rc=$?
set -e
mv "$real_cli" "$cli"
if [[ "$rollback_rc" -eq 0 ]]; then
  echo "forced post-install smoke failure unexpectedly succeeded" >&2
  exit 1
fi
grep -q "smoke plugins" "$base/rollback.json"
while IFS=$'\t' read -r package_id package_version; do
  test -f "$store_root/$package_id/$package_version/.p0-rollback-sentinel"
done < "$selected_file"
test "$(tree_digest "$store_root")" = "$before_rollback"
test "$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')" = "$data_before"

# The failed third attempt must not have altered the successful receipts
# or any installed package bytes; tree_digest above checks both.
python -m scripts.ci_plugins_only_receipt_gate \
  --archive "$archive" --store "$store_root" --snapshot-sha256 "$digest"

echo "plugins-only runtime gate passed for $(wc -l < "$selected_file") selected packages; $inactive_package inactive"
