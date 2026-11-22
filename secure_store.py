"""DPAPI-backed storage for API credentials on this Windows user profile."""

import base64
import ctypes
import json
import os
import re
import tempfile
from ctypes import wintypes
from pathlib import Path


class SecretStoreError(Exception):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _data_blob(data):
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _dpapi(data, *, protect):
    if os.name != "nt":
        raise SecretStoreError("密钥只能在 Windows 当前用户配置中保护。")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source, keepalive = _data_blob(data)
    destination = _DataBlob()
    if protect:
        success = crypt32.CryptProtectData(
            ctypes.byref(source), "SystemAudioQA", None, None, None, 0x1,
            ctypes.byref(destination),
        )
    else:
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0x1,
            ctypes.byref(destination),
        )
    if not success:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree(destination.pbData)
        del keepalive


class SecureStore:
    def __init__(self, path=None, *, protect=None, unprotect=None):
        self.path = Path(path or self.default_path())
        self._protect = protect or (lambda value: _dpapi(value, protect=True))
        self._unprotect = unprotect or (lambda value: _dpapi(value, protect=False))

    @staticmethod
    def default_path():
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
        return root / "SystemAudioQA" / "secrets.dat"

    def load(self, name):
        return self._read().get(name, "")

    def save(self, name, value):
        self._validate(name, value)
        secrets = self._read()
        if value:
            secrets[name] = value
        else:
            secrets.pop(name, None)
        self._write(secrets)

    def delete(self, name):
        self.save(name, "")

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            encrypted = base64.b64decode(self.path.read_bytes(), validate=True)
            data = json.loads(self._unprotect(encrypted).decode("utf-8"))
        except Exception as error:
            if isinstance(error, SecretStoreError):
                raise
            raise SecretStoreError("已保存的密钥无法解密。请重新输入并保存。") from error
        if not isinstance(data, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                                  for key, value in data.items()):
            raise SecretStoreError("密钥存储格式无效。请清除并重新保存。")
        return data

    def _write(self, secrets):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(secrets, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encrypted = base64.b64encode(self._protect(plaintext))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as output:
                temporary = Path(output.name)
                output.write(encrypted)
            os.replace(temporary, self.path)
        except OSError as error:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise SecretStoreError("无法保存加密密钥。请检查本机用户目录权限。") from error

    @staticmethod
    def _validate(name, value):
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", name):
            raise ValueError("密钥名称无效。")
        if not isinstance(value, str) or len(value) > 4096 or (value and not value.isascii()):
            raise ValueError("API Key 格式无效。")
