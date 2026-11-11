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
    # GitHub's Azure apt mirror can be unreachable while Ubuntu upstream is
    # available. Use signed Ubuntu archives for the single bounded fallback,
    # never disable apt-secure or retry indefinitely.
    . /etc/os-release
    if [[ "${ID:-}:${VERSION_CODENAME:-}" != "ubuntu:noble" ]]; then
      echo "::error::GTK E2E APT fallback supports only ubuntu-24.04"
      exit 1
    fi
    fallback_sources="$(mktemp)"
    trap 'rm -f "$fallback_sources"' EXIT
    cat > "$fallback_sources" <<'APT_SOURCES'
deb https://archive.ubuntu.com/ubuntu noble main restricted universe multiverse
deb https://archive.ubuntu.com/ubuntu noble-updates main restricted universe multiverse
deb https://security.ubuntu.com/ubuntu noble-security main restricted universe multiverse
APT_SOURCES
    fallback_options=(
      -o "Dir::Etc::sourcelist=$fallback_sources"
      -o Dir::Etc::sourceparts=-
    )
    echo "::warning::GTK Azure mirror unavailable; switching to signed Ubuntu archives."
    echo "::group::GTK E2E Ubuntu archive update (max 120s)"
    timeout --signal=TERM --kill-after=10s 120s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" "${fallback_options[@]}" update
    echo "::endgroup::"
    echo "::group::GTK E2E Ubuntu archive install (max 240s)"
    timeout --signal=TERM --kill-after=10s 240s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" "${fallback_options[@]}" install -y --no-install-recommends "${missing[@]}"
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
