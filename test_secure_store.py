import tempfile
import unittest
from pathlib import Path

from secure_store import SecureStore


class SecureStoreTests(unittest.TestCase):
    def test_saved_key_is_protected_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets.dat"
            protect = lambda value: b"protected:" + value[::-1]
            unprotect = lambda value: value.removeprefix(b"protected:")[::-1]
            first = SecureStore(path, protect=protect, unprotect=unprotect)
            first.save("dashscope", "test-api-key")

            self.assertNotIn(b"test-api-key", path.read_bytes())
            reopened = SecureStore(path, protect=protect, unprotect=unprotect)
            self.assertEqual(reopened.load("dashscope"), "test-api-key")

            reopened.delete("dashscope")
            self.assertEqual(reopened.load("dashscope"), "")


if __name__ == "__main__":
    unittest.main()
