import tempfile
import unittest
from pathlib import Path

from hotkey import DEFAULT, MOD_ALT, MOD_CONTROL, MOD_SHIFT, Shortcut, load_shortcut, save_shortcut


class ShortcutTests(unittest.TestCase):
    def test_default_is_shift_z(self):
        self.assertEqual(DEFAULT, Shortcut(MOD_SHIFT, ord("Z")))
        self.assertEqual(DEFAULT.label, "Shift+Z")

    def test_records_modifiers_and_a_supported_key(self):
        self.assertEqual(Shortcut.from_tk("J", 0x0005).label, "Ctrl+Shift+J")
        self.assertEqual(Shortcut.from_tk("F8", 0x20004), Shortcut(MOD_CONTROL | MOD_ALT, 0x77))
        self.assertEqual(Shortcut.from_tk("space", 0x0001).label, "Shift+Space")
        self.assertEqual(Shortcut.from_tk("at", 0x5, ord("2")).label, "Ctrl+Shift+2")

    def test_rejects_unmodified_keys_and_reserved_f12(self):
        self.assertIsNone(Shortcut.from_tk("z", 0))
        self.assertIsNone(Shortcut.from_tk("Shift_L", 0))
        self.assertIsNone(Shortcut.from_tk("F12", 1))

    def test_remembers_recorded_shortcut_and_button_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "hotkey.json"
            self.assertEqual(load_shortcut(path), DEFAULT)
            shortcut = Shortcut(MOD_CONTROL | MOD_SHIFT, ord("8"))
            save_shortcut(shortcut, path)
            self.assertEqual(load_shortcut(path), shortcut)
            save_shortcut(None, path)
            self.assertIsNone(load_shortcut(path))
            path.write_text('{"modifiers": 99, "key": 5}', encoding="utf-8")
            self.assertEqual(load_shortcut(path), DEFAULT)
