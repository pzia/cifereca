"""Send actual X11 pointer and keyboard events via the XTEST extension."""
import ctypes as C


class X11Input:
    def __init__(self):
        self.x11 = C.CDLL("libX11.so.6")
        self.xtst = C.CDLL("libXtst.so.6")
        self.x11.XOpenDisplay.argtypes = [C.c_char_p]
        self.x11.XOpenDisplay.restype = C.c_void_p
        self.x11.XCloseDisplay.argtypes = [C.c_void_p]
        self.x11.XSync.argtypes = [C.c_void_p, C.c_int]
        self.x11.XStringToKeysym.argtypes = [C.c_char_p]
        self.x11.XStringToKeysym.restype = C.c_ulong
        self.x11.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
        self.x11.XKeysymToKeycode.restype = C.c_ubyte
        self.xtst.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
        self.xtst.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.xtst.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Could not open the X11 display.")

    def click(self, x: int, y: int) -> None:
        self.xtst.XTestFakeMotionEvent(self.display, -1, x, y, 0)
        self.xtst.XTestFakeButtonEvent(self.display, 1, True, 0)
        self.xtst.XTestFakeButtonEvent(self.display, 1, False, 0)
        self.x11.XSync(self.display, False)

    def widget_point(self, widget) -> tuple[int, int]:
        """Find an XTEST target without dispatching any input event."""
        from native_capture import pointer
        native = widget.get_native()
        success, rect = widget.compute_bounds(native)
        if not success:
            raise RuntimeError("Could not find widget bounds.")
        lib = C.CDLL("libgtk-4.so.1")
        lib.gdk_x11_surface_get_xid.argtypes = [C.c_void_p]
        lib.gdk_x11_surface_get_xid.restype = C.c_ulong
        xid = lib.gdk_x11_surface_get_xid(pointer(native.get_surface()))
        self.x11.XDefaultRootWindow.argtypes = [C.c_void_p]
        self.x11.XDefaultRootWindow.restype = C.c_ulong
        self.x11.XTranslateCoordinates.argtypes = [
            C.c_void_p, C.c_ulong, C.c_ulong, C.c_int, C.c_int,
            C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_ulong),
        ]
        origin_x, origin_y, child = C.c_int(), C.c_int(), C.c_ulong()
        if not self.x11.XTranslateCoordinates(
            self.display, xid, self.x11.XDefaultRootWindow(self.display), 0, 0,
            C.byref(origin_x), C.byref(origin_y), C.byref(child),
        ):
            raise RuntimeError("Could not translate X11 surface coordinates.")
        transform_x, transform_y = native.get_surface_transform()
        # compute_bounds(native) describes the native widget allocation.
        # Include its render offset inside the X11 surface. Subtracting it
        # moves small targets outside their hit area on decorated windows.
        x = round(origin_x.value + rect.get_x() + rect.get_width() / 2 + transform_x)
        y = round(origin_y.value + rect.get_y() + rect.get_height() / 2 + transform_y)
        return x, y

    def click_widget(self, widget) -> dict:
        x, y = self.widget_point(widget)
        # XTEST may silently clamp a transient GTK modal's unallocated
        # (-5, -5) position. Never dispatch an unintended pointer action.
        if x < 0 or y < 0:
            raise RuntimeError("XTEST widget has no on-screen pointer coordinates")
        self.click(x, y)
        return {"widget": widget.get_name(), "x": x, "y": y, "source": "XTEST pointer/button events"}

    def key(self, keysym: str, pressed: bool) -> None:
        code = self.x11.XKeysymToKeycode(self.display, self.x11.XStringToKeysym(keysym.encode("ascii")))
        if not code:
            raise RuntimeError(f"No keycode for {keysym}")
        self.xtst.XTestFakeKeyEvent(self.display, code, pressed, 0)
        self.x11.XSync(self.display, False)

    def type_text(self, text: str) -> None:
        shifted = {
            **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")),
            "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
            "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
            "_": "minus", "+": "equal", "{": "bracketleft",
            "}": "bracketright", "|": "backslash", ":": "semicolon",
            '"': "apostrophe", "<": "comma", ">": "period",
            "?": "slash", "~": "grave",
        }
        for character in text:
            if character == " ":
                keysym = "space"
            elif character in shifted:
                keysym = shifted[character]
                self.key("Shift_L", True)
                self.key(keysym, True)
                self.key(keysym, False)
                self.key("Shift_L", False)
                continue
            else:
                keysym = character
            self.key(keysym, True)
            self.key(keysym, False)

    def select_all_delete(self) -> None:
        self.key("Control_L", True)
        self.key("a", True)
        self.key("a", False)
        self.key("Control_L", False)
        self.key("BackSpace", True)
        self.key("BackSpace", False)

    def close(self) -> None:
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None
