"""Render real GTK widgets to PNG through GTK/GSK's C API.

The short ctypes bridge keeps GskRenderNode pointers on the C side instead
of translating concrete render-node subclasses through the Python bindings.
"""
import ctypes as C
from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


def pointer(obj) -> int:
    name = C.pythonapi.PyCapsule_GetName
    name.argtypes = [C.py_object]
    name.restype = C.c_char_p
    get_pointer = C.pythonapi.PyCapsule_GetPointer
    get_pointer.argtypes = [C.py_object, C.c_char_p]
    get_pointer.restype = C.c_void_p
    return get_pointer(obj.__gpointer__, name(obj.__gpointer__))


def capture(widget: Gtk.Widget, filename: Path) -> dict:
    lib = C.CDLL("libgtk-4.so.1")
    gobj = C.CDLL("libgobject-2.0.so.0")
    lib.gtk_snapshot_to_node.argtypes = [C.c_void_p]
    lib.gtk_snapshot_to_node.restype = C.c_void_p
    lib.gsk_renderer_render_texture.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p]
    lib.gsk_renderer_render_texture.restype = C.c_void_p
    lib.gdk_texture_save_to_png.argtypes = [C.c_void_p, C.c_char_p]
    lib.gdk_texture_save_to_png.restype = C.c_int
    lib.gdk_texture_get_width.argtypes = [C.c_void_p]
    lib.gdk_texture_get_width.restype = C.c_int
    lib.gdk_texture_get_height.argtypes = [C.c_void_p]
    lib.gdk_texture_get_height.restype = C.c_int
    lib.gsk_render_node_unref.argtypes = [C.c_void_p]
    lib.gsk_render_node_unref.restype = None
    gobj.g_object_unref.argtypes = [C.c_void_p]
    gobj.g_object_unref.restype = None

    width, height = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, width, height)
    node = lib.gtk_snapshot_to_node(pointer(snapshot))
    if not node:
        raise RuntimeError("GTK generated an empty snapshot; the widget must be mapped and allocated.")
    texture = None
    try:
        renderer = widget.get_native().get_renderer()
        texture = lib.gsk_renderer_render_texture(pointer(renderer), node, None)
        if not texture:
            raise RuntimeError("GSK did not produce a texture.")
        filename.parent.mkdir(parents=True, exist_ok=True)
        if not lib.gdk_texture_save_to_png(texture, str(filename).encode()):
            raise RuntimeError(f"Could not save {filename}")
        return {
            "file": filename.name,
            "width": lib.gdk_texture_get_width(texture),
            "height": lib.gdk_texture_get_height(texture),
            "renderer": renderer.__gtype__.name,
            "capture_api": "Gtk.WidgetPaintable → Gtk.Snapshot → Gsk.Renderer.render_texture → Gdk.Texture PNG",
        }
    finally:
        if texture:
            gobj.g_object_unref(texture)
        lib.gsk_render_node_unref(node)
