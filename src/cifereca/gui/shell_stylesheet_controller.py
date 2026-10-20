"""CoreUI P0 #1051: centralized shell stylesheet provider, without a GTK import.

The MainWindow facade injects Gtk/Gdk and remains the lifetime owner.
All selectors, colors, breakpoints and precedence remain verbatim.
"""
from __future__ import annotations


def install_css(window, *, Gtk, Gdk) -> None:
    if getattr(window, '_closed', False):
        return
    css = b"""
    .login-card { background: @card_bg_color; border-radius: 16px; padding: 28px; }
    .sidebar { background: @sidebar_bg_color; border-right: 1px solid @borders; }
    .sidebar row { border-radius: 8px; margin: 2px 8px; padding: 4px; }
    .sidebar row:selected { background: @accent_bg_color; color: @accent_fg_color; }
    .book-row { border-radius: 10px; }
    .book-row:hover { background: alpha(currentColor, 0.055); }
    .book-title { font-weight: 650; font-size: 1.05em; }
    .page-title { font-weight: 750; font-size: 1.55em; }
    .detail-title { font-weight: 800; font-size: 1.7em; }
    .detail-subtitle { font-size: 1.08em; }
    .badge { border-radius: 999px; padding: 3px 8px; background: alpha(currentColor, 0.10); font-size: 0.88em; }
    .book-tag { border-radius: 10px; padding: 2px 6px; font-size: 0.88em; }
    .tag-content { background: alpha(@accent_color, 0.14); font-weight: 600; }
    .tag-form { background: alpha(@success_color, 0.14); }
    .book-tag:focus { outline: 2px solid @accent_color; }
    .facet-value { border-radius: 8px; padding: 2px 6px; background: alpha(@accent_color, 0.08); }
    .facet-value:hover { background: alpha(@accent_color, 0.16); }
    .facet-value:focus { outline: 2px solid @accent_color; }
    .success-badge { background: alpha(@success_color, 0.20); color: @success_color; }
    .cover-frame { border-radius: 7px; background: alpha(currentColor, 0.06); }
    .busy-bar { background: @headerbar_bg_color; border-top: 1px solid @borders; padding: 8px 14px; }
    .notification-bar { background: @headerbar_bg_color; border-top: 1px solid @borders; padding: 7px 12px; }
    /* #2141: account cards and headings stay visually distinct with tools open. */
    .sync-source-panel {
        background: @card_bg_color;
        border: 1px solid @borders;
        border-radius: 14px;
        padding: 14px;
    }
    .sync-source-heading .title {
        font-size: 1.14em;
        font-weight: 750;
    }
    .sync-source-heading .subtitle { opacity: 0.92; }
    .settings-tabs button { padding-left: 8px; padding-right: 8px; min-width: 0; }
    .activity-success { color: @success_color; }
    .activity-error { color: @error_color; }
    .loan-overdue { background: alpha(@error_color, 0.14); color: @error_color; font-weight: 650; }
    .loan-imminent { background: alpha(@warning_color, 0.14); color: @warning_color; font-weight: 650; }
    .loan-normal { opacity: 0.85; }
    .metric { font-size: 1.35em; font-weight: 700; }
    .login-brand { font-size: 2.1em; font-weight: 800; }
    """
    provider = Gtk.CssProvider()
    provider.load_from_data(css)
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

