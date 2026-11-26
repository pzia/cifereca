"""Bounded synthetic native title witness; never exports text or screenshot crops."""
from collections import Counter
import math
import re
import subprocess

MIN_CONTRAST = 4.5


def require_inside(inner, outer):
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    if (not all(math.isfinite(v) for v in (*inner, *outer))
            or min(w, h, ow, oh) <= 0
            or x < ox or y < oy or x + w > ox + ow or y + h > oy + oh):
        raise AssertionError("native title clipped")


def luminance(rgb):
    def linear(value):
        value /= 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    return sum(weight * linear(channel)
               for weight, channel in zip((0.2126, 0.7152, 0.0722), rgb))


def contrast(first, second):
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def check_pixels(crop, foreground):
    """Use actual dominant background and visible pixels near native text color."""
    pixels = Counter(crop.convert("RGB").getdata())
    if not pixels:
        raise AssertionError("empty native title crop")
    background, count = pixels.most_common(1)[0]
    if count * 4 < sum(pixels.values()):
        raise AssertionError("unresolved native title background")
    matching = sum(count for rgb, count in pixels.items()
                   if max(abs(a - b) for a, b in zip(rgb, foreground)) <= 24
                   and contrast(rgb, background) >= MIN_CONTRAST)
    if contrast(foreground, background) < MIN_CONTRAST or matching < 3:
        raise AssertionError("native title contrast or paint missing")


def native_title_witness(window, panel, screenshot, expected_title):
    from gi.repository import Gtk
    import gi
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11

    def walk(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from walk(child)
            child = child.get_next_sibling()

    def bounds(widget):
        ok, rect = widget.compute_bounds(window)
        if not ok:
            raise AssertionError("native title coordinates unavailable")
        return (rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height())

    titles = [node for node in walk(panel.content)
              if isinstance(node, Gtk.Label) and node.get_mapped()
              and node.has_css_class("detail-title")]
    if len(titles) != 1 or titles[0].get_text() != expected_title:
        raise AssertionError("exact synthetic native title absent")
    label = titles[0]
    layout = label.get_layout()
    if layout.is_ellipsized():
        raise AssertionError("native title ellipsized")
    ink, _ = layout.get_pixel_extents()
    dx, dy = label.get_layout_offsets()
    local = (dx + ink.x, dy + ink.y, ink.width, ink.height)
    require_inside(local, (0, 0, label.get_width(), label.get_height()))
    lx, ly, _, _ = bounds(label)
    rect = (lx + local[0], ly + local[1], local[2], local[3])
    require_inside(rect, bounds(panel.body_scroll))
    require_inside(rect, (0, 0, window.get_width(), window.get_height()))
    surface = window.get_surface()
    if not isinstance(surface, GdkX11.X11Surface) or surface.get_scale_factor() != 1:
        raise AssertionError("native title requires unscaled X11 surface")
    raw = subprocess.check_output(
        ["xwininfo", "-id", str(surface.get_xid())], text=True, timeout=3,
    )
    def origin(axis):
        found = re.search(r"^\s*Absolute upper-left " + axis + r":\s*(-?\d+)\s*$",
                          raw, re.MULTILINE)
        if not found:
            raise AssertionError("native title X11 origin unavailable")
        return int(found.group(1))
    x, y, w, h = rect
    left, top = math.floor(x) + origin("X"), math.floor(y) + origin("Y")
    right, bottom = math.ceil(x + w) + origin("X"), math.ceil(y + h) + origin("Y")
    require_inside((left, top, right - left, bottom - top),
                   (0, 0, screenshot.width, screenshot.height))
    color = label.get_style_context().get_color()
    if color.alpha < 0.99:
        raise AssertionError("unresolved native title alpha")
    foreground = tuple(round(v * 255) for v in (color.red, color.green, color.blue))
    check_pixels(screenshot.crop((left, top, right, bottom)), foreground)
    return True
