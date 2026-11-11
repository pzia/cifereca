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
  # Bound mirror, dpkg-lock and retry delays. A failed install must fail E2E.
  apt_options=(-o Acquire::Retries=1 -o Acquire::http::Timeout=25
               -o Acquire::https::Timeout=25 -o DPkg::Lock::Timeout=30
               -o Dpkg::Use-Pty=0)
  echo "::group::GTK E2E APT install (max 240s)"
  if timeout --signal=TERM --kill-after=10s 240s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" install -y --no-install-recommends "${missing[@]}"; then
    echo "::endgroup::"
  else
    echo "::endgroup::"
    echo "::warning::GTK dependencies unresolved; refreshing package metadata once."
    echo "::group::GTK E2E APT update (max 120s)"
    timeout --signal=TERM --kill-after=10s 120s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" update
    echo "::endgroup::"
    echo "::group::GTK E2E APT retry (max 240s)"
    timeout --signal=TERM --kill-after=10s 240s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" install -y --no-install-recommends "${missing[@]}"
    echo "::endgroup::"
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
