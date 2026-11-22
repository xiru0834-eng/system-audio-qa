"""Native hotkeys on a separate message queue from Tk."""

import ctypes
import json
import os
import tempfile
import threading
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 1, 2, 4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 1
MODIFIERS = ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"))
KEYS = {letter: ord(letter) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
KEYS.update({f"F{index}": 0x6F + index for index in range(1, 12)})
KEYS["SPACE"] = 0x20
KEY_NAMES = {code: name.title() if name == "SPACE" else name for name, code in KEYS.items()}


@dataclass(frozen=True)
class Shortcut:
    modifiers: int
    key: int

    def __post_init__(self):
        if type(self.modifiers) is not int or not 1 <= self.modifiers <= 7:
            raise ValueError("Unsupported shortcut modifiers")
        if type(self.key) is not int or self.key not in KEY_NAMES:
            raise ValueError("Unsupported shortcut key")

    @property
    def label(self):
        parts = [label for flag, label in MODIFIERS if self.modifiers & flag]
        return "+".join([*parts, KEY_NAMES[self.key]])

    @classmethod
    def from_tk(cls, keysym, state, keycode=None):
        # Tk Windows can report Alt through either the Mod1 or the system Alt bit.
        modifiers = 0
        for mask, modifier in ((0x0004, MOD_CONTROL), (0x20008, MOD_ALT), (0x0001, MOD_SHIFT)):
            if state & mask:
                modifiers |= modifier
        key = keycode if keycode in KEY_NAMES else KEYS.get(keysym.upper())
        return cls(modifiers, key) if key is not None and modifiers else None


DEFAULT = Shortcut(MOD_SHIFT, ord("Z"))


def settings_path():
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return local / "SystemAudioQA" / "hotkey.json"


def load_shortcut(path=None):
    try:
        saved = json.loads((path or settings_path()).read_text(encoding="utf-8"))
        return None if saved is None else Shortcut(**saved)
    except (OSError, ValueError, TypeError):
        return DEFAULT


def save_shortcut(shortcut, path=None):
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w",
                                     suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
    try:
        data = None if shortcut is None else asdict(shortcut)
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterHotKey.argtypes = wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
user32.UnregisterHotKey.argtypes = wintypes.HWND, ctypes.c_int
user32.PeekMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT,
)
user32.GetMessageW.argtypes = (
    ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
)
user32.PostThreadMessageW.argtypes = (
    wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
)
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class Hotkey:
    def __init__(self, modifiers, on_press, key=ord("J")):
        self.thread_id = None
        self.error = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._listen, args=(modifiers, on_press, key),
                                       daemon=True)
        self.thread.start()
        self.ready.wait()
        if self.error is not None:
            self.thread.join()
            raise ctypes.WinError(self.error)

    def _listen(self, modifiers, on_press, key):
        message = wintypes.MSG()
        self.thread_id = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers | MOD_NOREPEAT, key):
            self.error = ctypes.get_last_error()
            self.ready.set()
            return
        self.ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    on_press()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def close(self):
        if self.thread.is_alive():
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join()
