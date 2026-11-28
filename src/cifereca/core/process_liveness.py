"""Conservative, non-destructive process-owner liveness probe."""
import os


def _windows_dead(pid, kernel, last_error=lambda: 0):
    # SYNCHRONIZE only; never request termination rights.
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return last_error() == 87
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


def owner_proven_dead(pid):
    if type(pid) is not int or not 0 < pid <= 2**31 - 1 or pid == os.getpid():
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            pass
    elif os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.restype = wintypes.BOOL
            return _windows_dead(pid, kernel, ctypes.get_last_error)
        except (OSError, AttributeError):
            pass
    return False
