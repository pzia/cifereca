#!/usr/bin/env bash
# GTK4 UX probe dependencies from the Ubuntu archive (Ubuntu 24.04 LTS).
# Never install Ubuntu binary packages into Debian: use an Ubuntu host/container.
set -euo pipefail

. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "noble" ]]; then
  echo "Requires isolated Ubuntu 24.04 (noble), got: ${PRETTY_NAME:-unknown}." >&2
  echo "Do not add Ubuntu APT sources to Debian 13." >&2
  exit 2
fi
if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash environnements/setup-gtk4-e2e-ubuntu2404.sh" >&2
  exit 2
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -o APT::Update::Error-Mode=any
apt-get install -y --no-install-recommends \
  python3-gi python3-gi-cairo python3-pil \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  xvfb xauth dbus-x11 libxtst6 libx11-6 python3-pytest
/usr/bin/python3 - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
from PIL import ImageGrab
print("PyGObject:", gi.__file__, "GTK:", Gtk.get_major_version(), Gtk.get_minor_version())
PY
xvfb-run -a -s "-screen 0 1366x900x24" /usr/bin/python3 - <<'PY'
from gi.repository import Gdk
display = Gdk.Display.get_default()
assert display is not None, "Xvfb/GDK display unavailable"
print("Xvfb:", display.get_name())
PY
echo "Then: /usr/bin/python3 e2e/run.py --python /usr/bin/python3 --scenario diagnostics-1366"
