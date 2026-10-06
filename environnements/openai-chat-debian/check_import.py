"""Print an actual import result; a display server is not needed for this check."""
import json
import os
import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, GLib, Adw

print(json.dumps({
    "gtk_import": "OK",
    "python_version": sys.version.split()[0],
    "python_executable": sys.executable,
    "pygobject": gi.__version__,
    "gtk": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
    "adw_import": "OK",
    "glib": f"{GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}",
    "gsk_renderer": os.environ.get("GSK_RENDERER"),
    "display": os.environ.get("DISPLAY"),
}, indent=2))
