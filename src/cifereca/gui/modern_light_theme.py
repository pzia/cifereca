"""#757 — Modern & clear GTK4 V2, scoped to Cifereca's light appearance.

The selected 08/10 prototype supplied the palette; this is an adaptation,
not a wholesale import of its global GTK element overrides. Plugin pages
inherit the same neutral class, without provider-specific rules.
"""
from __future__ import annotations

from weakref import ref


MODERN_LIGHT_CLASS = "cifereca-modern-light"

# GTK4 CSS uses a private namespace for the V2 colours, avoiding overrides
# of Adwaita's global accent and error/success theme tokens.
MODERN_LIGHT_CSS = b"""
@define-color cifereca_light_accent #1d5fcb;
@define-color cifereca_light_window #f7faff;
@define-color cifereca_light_ink #152641;
@define-color cifereca_light_sidebar #e9f1fb;
@define-color cifereca_light_card #ffffff;
@define-color cifereca_light_border #bfd0e8;
@define-color cifereca_light_hover #d8e7fd;
@define-color cifereca_light_muted #4e6482;

/* Every visual rule is rooted at this window's explicit light-mode class.
   No selector can repaint the rest of the system or an active dark window. */
window.cifereca-modern-light {
  background-color: @cifereca_light_window;
  color: @cifereca_light_ink;
}
window.cifereca-modern-light headerbar,
window.cifereca-modern-light .titlebar {
  background-color: @cifereca_light_window;
  color: @cifereca_light_ink;
  border-color: @cifereca_light_border;
}
window.cifereca-modern-light .sidebar,
window.cifereca-modern-light stacksidebar {
  background-color: @cifereca_light_sidebar;
  color: @cifereca_light_ink;
}
window.cifereca-modern-light .sidebar row {
  border-radius: 9px;
}
window.cifereca-modern-light .sidebar row:selected,
window.cifereca-modern-light .sidebar row:checked {
  background-color: @cifereca_light_accent;
  color: white;
}
window.cifereca-modern-light .login-card,
window.cifereca-modern-light .sync-source-panel {
  background-color: @cifereca_light_card;
  border: 1px solid @cifereca_light_border;
  border-radius: 14px;
}
window.cifereca-modern-light .book-row {
  border-radius: 10px;
}
window.cifereca-modern-light .book-row:hover,
window.cifereca-modern-light .facet-value:hover {
  background-color: @cifereca_light_hover;
}
window.cifereca-modern-light .book-tag {
  border: 1px solid transparent;
  border-radius: 7px;
  background-color: #e5eefc;
  color: #12315f;
  padding: 2px 6px;
}
window.cifereca-modern-light .book-tag.tag-content {
  background-color: #edf6f1;
}
window.cifereca-modern-light .book-title {
  color: @cifereca_light_ink;
  font-weight: 700;
}
window.cifereca-modern-light .page-title,
window.cifereca-modern-light .detail-title {
  color: @cifereca_light_ink;
}
window.cifereca-modern-light .dim-label {
  color: @cifereca_light_muted;
}
window.cifereca-modern-light button {
  border-radius: 10px;
}
window.cifereca-modern-light button.flat {
  background-color: transparent;
  border-color: transparent;
  box-shadow: none;
  padding: 3px 6px;
}
window.cifereca-modern-light button.flat:hover {
  background-color: @cifereca_light_hover;
}
window.cifereca-modern-light button.suggested-action {
  background-color: @cifereca_light_accent;
  color: white;
}
window.cifereca-modern-light switch:checked {
  background-color: @cifereca_light_accent;
}
window.cifereca-modern-light progressbar progress,
window.cifereca-modern-light scale highlight {
  background-color: @cifereca_light_accent;
}
window.cifereca-modern-light button:focus-visible,
window.cifereca-modern-light entry:focus-visible,
window.cifereca-modern-light row:focus-visible,
window.cifereca-modern-light .facet-value:focus {
  outline: 2px solid @cifereca_light_accent;
  outline-offset: 2px;
}
/* Existing Adwaita error/warning/success tokens and disabled opacity are
   deliberately untouched: danger actions must never look like safe CTAs. */
"""


def modern_light_enabled(style_manager: object) -> bool:
    """Unknown appearance means no extra palette; never force a light mode."""
    reader = getattr(style_manager, "get_dark", None)
    if not callable(reader):
        return False
    try:
        value = reader()
    except Exception:
        return False
    return type(value) is bool and not value


def update_light_class(window: object, style_manager: object) -> None:
    if getattr(window, "_closed", False):
        return
    add = getattr(window, "add_css_class", None)
    remove = getattr(window, "remove_css_class", None)
    if not callable(add) or not callable(remove):
        return
    if modern_light_enabled(style_manager):
        add(MODERN_LIGHT_CLASS)
    else:
        remove(MODERN_LIGHT_CLASS)


def install_modern_light(window, *, Gtk, Gdk, Adw) -> None:
    """Register one scoped overlay and follow Adwaita light/dark transitions."""
    if getattr(window, "_closed", False):
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    manager_factory = getattr(Adw, "StyleManager", None)
    get_default = getattr(manager_factory, "get_default", None)
    if not callable(get_default):
        return
    try:
        manager = get_default()
    except Exception:
        return
    if manager is None or not callable(getattr(manager, "connect", None)):
        return

    # Register only once for a given live window. A repeated install_css()
    # must not multiply overlay providers or dark-mode signal subscriptions.
    if getattr(window, "_modern_light_css_installed", False):
        update_light_class(window, manager)
        return
    css = Gtk.CssProvider()
    css.load_from_data(MODERN_LIGHT_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    try:
        reference = ref(window)
    except TypeError:
        return

    def changed(style_manager, *_args):
        current = reference()
        if current is None:
            return
        update_light_class(current, style_manager)

    try:
        handler = manager.connect("notify::dark", changed)
    except Exception:
        # The overlay is inert until root class is deliberately installed.
        return
    window._modern_light_css_installed = True
    window._modern_light_css_handler = (manager, handler)
    update_light_class(window, manager)
