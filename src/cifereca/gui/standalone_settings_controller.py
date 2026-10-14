"""Standalone Settings/Diagnostics window; the host owns page lifetime and widgets."""
from __future__ import annotations


def open_standalone_settings(self, *, Gtk, Adw, Gdk) -> None:
    """Build the existing nonmodal window and release only its current page."""
    if getattr(self, "_closed", False):
        return
    win = Adw.Window(transient_for=self, modal=False)
    win.set_title("Diagnostic / Configuration")
    win.set_default_size(900, 650)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    header = Adw.HeaderBar()
    header.set_title_widget(Gtk.Label(label="Configuration et diagnostic"))
    close = Gtk.Button.new_from_icon_name("window-close-symbolic")
    close.set_tooltip_text("Fermer")
    close.connect("clicked", lambda _b: win.close())
    header.pack_end(close)
    content.append(header)
    page = self._build_settings_page(standalone=True)
    page.set_vexpand(True)
    content.append(page)
    win.set_content(content)

    keys = Gtk.EventControllerKey()
    def on_key(_controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape:
            win.close()
            return True
        return False
    keys.connect("key-pressed", on_key)
    win.add_controller(keys)
    # Garder une référence explicite pendant toute la vie de la fenêtre.
    self._standalone_settings_window = win
    def standalone_closed(_window):
        if getattr(self, "_standalone_settings_window", None) is win:
            standalone_lifetime = self._settings_standalone_lifetime
            if standalone_lifetime is not None:
                standalone_lifetime.dispose()
                self._settings_standalone_lifetime = None
            self._standalone_settings_window = None
        return False
    win.connect("close-request", standalone_closed)
    win.present()
