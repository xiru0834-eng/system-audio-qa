"""Windows capture preference for application-owned top-level windows."""

import ctypes
from ctypes import wintypes

EXCLUDE = 0x11
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.GetWindowDisplayAffinity.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))


def native_window(widget):
    return user32.GetAncestor(widget.winfo_id(), 2)


def read_affinity(hwnd):
    result = wintypes.DWORD()
    if not user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    return result.value


def set_privacy(hwnd, enabled):
    value = EXCLUDE if enabled else 0
    if not user32.SetWindowDisplayAffinity(hwnd, value):
        raise ctypes.WinError(ctypes.get_last_error())
    if read_affinity(hwnd) != value:
        raise OSError("Windows did not report the requested capture setting")
