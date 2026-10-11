#!/usr/bin/env bash
# Install only verified Debian 13 .deb packages from a GitHub artifact.
# Usage: sudo bash environnements/install-gtk4-from-github-artifact.sh /path/to/gtk4-debian13-debs.tar.gz
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then echo "Requires sudo" >&2; exit 2; fi
source /etc/os-release
if [[ "${ID:-}" != "debian" || "${VERSION_CODENAME:-}" != "trixie" ]]; then
  echo "Refusing non-Debian-13 host; never mix Ubuntu and Debian packages" >&2
  exit 2
fi
archive="${1:-}"
if [[ -z "$archive" || ! -f "$archive" ]]; then
  echo "Usage: sudo bash $0 /path/to/gtk4-debian13-debs.tar.gz" >&2
  exit 2
fi
archive="$(readlink -f "$archive")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# Reject paths outside the expected top-level debs directory.
if tar -tzf "$archive" | grep -vE '^debs(/|$)' | grep -q .; then
  echo "Unexpected paths in archive" >&2
  exit 3
fi
tar -xzf "$archive" -C "$tmp" --no-same-owner
cd "$tmp/debs"
sha256sum --check --strict SHA256SUMS
shopt -s nullglob
packages=(./*.deb)
if [[ "${#packages[@]}" -eq 0 ]]; then echo "No deb packages" >&2; exit 3; fi
# Offline APT install; fail rather than fetching packages from a different distro.
apt-get install -y --no-download "${packages[@]}"
/usr/bin/python3 - <<'PY'
import gi
gi.require_version("Gtk","4.0")
gi.require_version("Adw","1")
from gi.repository import Gtk, Adw
from PIL import ImageGrab
print("GTK:", Gtk.get_major_version(), Gtk.get_minor_version(), "PyGObject:", gi.__file__)
PY
xvfb-run -a -s "-screen 0 1366x900x24" /usr/bin/python3 - <<'PY'
from gi.repository import Gdk
assert Gdk.Display.get_default() is not None
print("Xvfb / GDK OK")
PY
