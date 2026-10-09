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
import shutil
import sys
from pathlib import Path
from cifereca.updater import install_source

source = Path.cwd()
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

read -r package_id package_version <<<"$(python - "$archive" <<'PY'
import json
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as z:
    matches = [n for n in z.namelist() if n.endswith("/cifereca-plugins-only.json")]
    if len(matches) != 1:
        raise SystemExit("plugins-only index missing or ambiguous")
    index = json.loads(z.read(matches[0]))
if not index.get("packages"):
    raise SystemExit("plugins-only package selection is empty")
row = index["packages"][0]
print(row["package_id"], row["version"])
PY
)"

package_root="$XDG_DATA_HOME/cifereca/plugins/packages/$package_id"
version_root="$package_root/$package_version"
test -d "$version_root"

# Preserve an installed-but-inactive package across a real strict replacement.
python - "$package_root/current.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
payload["active"] = None
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

printf 'must disappear on successful replace\n' > "$version_root/.p0-success-sentinel"
mkdir -p "$XDG_DATA_HOME/cifereca"
printf 'p0-user-data-integrity\n' > "$XDG_DATA_HOME/cifereca/.p0-user-data"
data_before="$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')"

"$runtime_update_dev" "$archive"   --require-plugins-only   --expected-sha256 "$digest"   --dry-run   --json > "$base/dry-run.json"

"$runtime_update_dev" "$archive"   --require-plugins-only   --expected-sha256 "$digest"   --json > "$base/success.json"

test ! -e "$version_root/.p0-success-sentinel"
python - "$package_root/current.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("active") is not None:
    raise SystemExit(f"inactive package was unexpectedly activated: {payload}")
PY
test "$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')" = "$data_before"

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
before_incompatible="$(tree_digest "$package_root")"
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
test "$(tree_digest "$package_root")" = "$before_incompatible"
test "$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')" = "$data_before"

# Force only the post-install inventory smoke to fail. The real package install
# runs first; automatic rollback must restore the exact pre-attempt package tree.
printf 'must return after rollback\n' > "$version_root/.p0-rollback-sentinel"
before_rollback="$(tree_digest "$package_root")"
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
test -f "$version_root/.p0-rollback-sentinel"
test "$(tree_digest "$package_root")" = "$before_rollback"
test "$(sha256sum "$XDG_DATA_HOME/cifereca/.p0-user-data" | awk '{print $1}')" = "$data_before"

echo "plugins-only runtime gate passed for $package_id $package_version"
