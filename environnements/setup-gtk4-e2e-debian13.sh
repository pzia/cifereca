#!/usr/bin/env bash
# Debian 13 (trixie): provision the system Python GTK4/X11 test environment.
# This script is deliberately local; it is not part of GitHub Actions.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Execute with sudo: sudo bash environnements/setup-gtk4-e2e-debian13.sh" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -o APT::Update::Error-Mode=any
apt-get install -y --no-install-recommends \
  python3-gi python3-gi-cairo python3-pil \
  gir1.2-gtk-4.0 gir1.2-adw-1 \
  xvfb xauth dbus-x11 libxtst6 libx11-6 \
  python3-pytest

/usr/bin/python3 - <<'PY'
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw
from PIL import ImageGrab
print("OK PyGObject:", gi.__file__)
print("OK GTK:", Gtk.get_major_version(), Gtk.get_minor_version())
print("OK libadwaita:", Adw.get_major_version(), Adw.get_minor_version())
PY

xvfb-run -a -s "-screen 0 1366x900x24" /usr/bin/python3 - <<'PY'
from gi.repository import Gdk
display = Gdk.Display.get_default()
assert display is not None, "X11/GDK display unavailable under Xvfb"
print("OK Xvfb backend:", display.get_name())
PY

echo "Ready: /usr/bin/python3 e2e/run.py --python /usr/bin/python3 --scenario diagnostics-1366"
