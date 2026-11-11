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

# A GitHub Ubuntu runner can advertise an Azure APT mirror that is temporarily
# unreachable. Do not consume the entire 15-minute GTK watchdog retrying every
# package on that mirror. Use an explicit finite timeout and a single
# authenticated Ubuntu archive fallback; failure still fails the E2E gate.
apt_options=(-o Acquire::Retries=0 -o Acquire::http::Timeout=8 -o Acquire::https::Timeout=8)

if (("${#missing[@]}" > 0)); then
  echo "GTK E2E runtime missing: ${missing[*]}"
  if ! sudo timeout --signal=TERM --kill-after=10s 75s \
      apt-get "${apt_options[@]}" install -y --no-install-recommends "${missing[@]}"; then
    echo "APT runner mirror unavailable or stale: retrying once via signed Ubuntu archive."
    # Only the supported GitHub Actions Ubuntu 24.04 runner is in scope.
    . /etc/os-release
    if [[ "${ID:-}" != ubuntu || "${VERSION_CODENAME:-}" != noble ]]; then
      echo "GTK fallback refuses an unrecognized distro release." >&2
      exit 1
    fi
    alternate_sources="${RUNNER_TEMP:-/tmp}/cifereca-gtk-ubuntu.sources.list"
    cat > "$alternate_sources" <<'APT_SOURCES'
deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] http://archive.ubuntu.com/ubuntu noble main restricted universe multiverse
deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] http://archive.ubuntu.com/ubuntu noble-updates main restricted universe multiverse
deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] http://security.ubuntu.com/ubuntu noble-security main restricted universe multiverse
APT_SOURCES
    alternate_options=(
      "${apt_options[@]}"
      -o "Dir::Etc::sourcelist=$alternate_sources"
      -o Dir::Etc::sourceparts=-
      -o APT::Get::List-Cleanup=0
    )
    sudo timeout --signal=TERM --kill-after=10s 90s \
      apt-get "${alternate_options[@]}" update
    sudo timeout --signal=TERM --kill-after=10s 150s \
      apt-get "${alternate_options[@]}" install -y --no-install-recommends "${missing[@]}"
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
