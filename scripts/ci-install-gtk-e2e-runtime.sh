#!/usr/bin/env bash
set -euo pipefail

required_packages=(
  xauth
  x11-utils
  libxtst6
  python3-gi
  python3-gi-cairo
  python3-cairo
  python3-pil
  gir1.2-gtk-4.0
  gir1.2-adw-1
  python3-httpx
  python3-venv
  python3-packaging
)

missing=()
for package in "${required_packages[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx 'install ok installed'; then
    missing+=("$package")
  fi
done

command -v xvfb-run >/dev/null 2>&1 || missing+=(xvfb)
command -v dbus-run-session >/dev/null 2>&1 || missing+=(dbus-x11)

if (("${#missing[@]}" > 0)); then
  echo "GTK E2E runtime missing: ${missing[*]}"
  if ! sudo apt-get install -y --no-install-recommends "${missing[@]}"; then
    echo "Cached APT metadata could not satisfy GTK E2E dependencies; refreshing once."
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends "${missing[@]}"
  fi
else
  echo "GTK E2E runtime already available."
fi

command -v xvfb-run >/dev/null
command -v dbus-run-session >/dev/null

/usr/bin/python3 - <<'PY'
import gi
import httpx
import packaging
from PIL import Image

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402,F401

assert Image is not None
assert httpx is not None
assert packaging is not None
PY
