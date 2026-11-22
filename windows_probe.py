"""Opt-in diagnostics for a real Windows key chord and a local capture receiver."""

import ctypes as c
import json
import time
from ctypes import wintypes as w
from pathlib import Path

from window_privacy import native_window, set_privacy

user = c.WinDLL("user32", use_last_error=True)
gdi = c.WinDLL("gdi32", use_last_error=True)


class KeyInput(c.Structure):
    _fields_ = [("vk", w.WORD), ("scan", w.WORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", w.WPARAM)]


class MouseInput(c.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG), ("data", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("extra", w.WPARAM)]


class InputValue(c.Union):
    _fields_ = [("key", KeyInput), ("mouse", MouseInput)]


class Input(c.Structure):
    _fields_ = [("kind", w.DWORD), ("value", InputValue)]


def press_shift_z():
    user.GetAsyncKeyState.argtypes = (c.c_int,)
    user.GetAsyncKeyState.restype = c.c_short
    if any(user.GetAsyncKeyState(key) < 0 for key in (0x10, 0x11, 0x12, 0x5A, 0x5B, 0x5C)):
        raise RuntimeError("A modifier or Z is held; skipped the test keystroke.")
    user.SendInput.argtypes = (w.UINT, c.POINTER(Input), c.c_int)
    user.SendInput.restype = w.UINT
    sequence = (Input * 4)(
        *(Input(1, InputValue(key=KeyInput(key, 0, flags, 0, 0)))
          for key, flags in ((0x10, 0), (0x5A, 0), (0x5A, 2), (0x10, 2)))
    )
    count = user.SendInput(len(sequence), sequence, c.sizeof(Input))
    if count != len(sequence):
        release = (Input * 2)(sequence[2], sequence[3])
        user.SendInput(2, release, c.sizeof(Input))
        raise RuntimeError(f"Windows SendInput delivered {count} of 4 keyboard events")


def capture_receiver(folder):
    path = Path(folder)
    coordinates = json.loads((path / "coordinates.json").read_text(encoding="utf-8"))
    user.GetDC.argtypes = (w.HWND,)
    user.GetDC.restype = w.HDC
    user.ReleaseDC.argtypes = (w.HWND, w.HDC)
    gdi.CreateCompatibleDC.argtypes = (w.HDC,)
    gdi.CreateCompatibleDC.restype = w.HDC
    gdi.CreateCompatibleBitmap.argtypes = (w.HDC, c.c_int, c.c_int)
    gdi.CreateCompatibleBitmap.restype = w.HBITMAP
    gdi.SelectObject.argtypes = (w.HDC, w.HANDLE)
    gdi.SelectObject.restype = w.HANDLE
    gdi.BitBlt.argtypes = (w.HDC, c.c_int, c.c_int, c.c_int, c.c_int,
                          w.HDC, c.c_int, c.c_int, w.DWORD)
    gdi.GetPixel.argtypes = (w.HDC, c.c_int, c.c_int)
    gdi.GetPixel.restype = w.DWORD
    gdi.DeleteObject.argtypes = (w.HANDLE,)
    gdi.DeleteDC.argtypes = (w.HDC,)
    screen = user.GetDC(None)
    dest = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, 1, 1)
    previous = gdi.SelectObject(dest, bitmap)
    try:
        samples = []
        for x, y in coordinates:
            if not gdi.BitBlt(dest, 0, 0, 1, 1, screen, x, y, 0x40CC0020):
                raise c.WinError(c.get_last_error())
            samples.append(gdi.GetPixel(dest, 0, 0))
        (path / "samples.json").write_text(json.dumps(samples), encoding="ascii")
    finally:
        gdi.SelectObject(dest, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(dest)
        user.ReleaseDC(None, screen)


def prevent_test_activation(widget):
    widget.update_idletasks()
    hwnd = native_window(widget)
    user.GetWindowLongW.argtypes = (w.HWND, c.c_int)
    user.SetWindowLongW.argtypes = (w.HWND, c.c_int, w.LONG)
    user.SetWindowLongW(hwnd, -20, user.GetWindowLongW(hwnd, -20) | 0x08000000)
    return hwnd


def raise_test_window(hwnd):
    user.SetWindowPos.argtypes = (w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
    if not user.SetWindowPos(hwnd, w.HWND(-1), 0, 0, 0, 0, 0x0013):
        raise c.WinError(c.get_last_error())


def capture_probe(root, folder, receiver):
    import tkinter as tk

    # A synthetic marker, separate from the real UI and never activated.
    marker = tk.Toplevel(root, background="#13da71")
    marker.withdraw()
    marker.overrideredirect(True)
    marker.geometry("64x64+80+80")
    hwnd = prevent_test_activation(marker)
    path = Path(folder)
    try:
        marker.deiconify()
        root.update()
        raise_test_window(hwnd)
        root.update()
        rectangle = w.RECT()
        user.GetWindowRect.argtypes = (w.HWND, c.POINTER(w.RECT))
        if not user.GetWindowRect(hwnd, c.byref(rectangle)):
            raise c.WinError(c.get_last_error())
        x = (rectangle.left + rectangle.right) // 2
        y = (rectangle.top + rectangle.bottom) // 2
        (path / "coordinates.json").write_text(json.dumps([[x, y]]), encoding="utf-8")
        visibility = []
        for protection in (False, True, False):
            set_privacy(hwnd, protection)
            c.windll.dwmapi.DwmFlush()
            time.sleep(0.15)
            receiver(path)
            pixel, = json.loads((path / "samples.json").read_text(encoding="ascii"))
            visibility.append(pixel == 0x71DA13)
        return {"transport": "local GDI BitBlt receiver process, one synthetic pixel",
                "marker_visible_off_on_off": visibility,
                "pass": visibility == [True, False, True]}
    finally:
        marker.destroy()
