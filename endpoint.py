"""Query the live Windows default playback endpoint independently of PortAudio."""

import ctypes as c
import uuid
from ctypes import wintypes

ole = c.OleDLL("ole32")


def guid(value):
    return (c.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le)


ENUMERATOR = guid("bcde0395-e52f-467c-8e3d-c4579291692e")
ENUMERATOR_IID = guid("a95664d2-9614-4f35-a746-de8db63617e6")
ole.CoCreateInstance.argtypes = (c.c_void_p, c.c_void_p, wintypes.DWORD, c.c_void_p, c.c_void_p)
ole.CoInitializeEx.argtypes = (c.c_void_p, wintypes.DWORD)
ole.CoTaskMemFree.argtypes = (c.c_void_p,)
ole.CoTaskMemFree.restype = None


def method(pointer, index, result, *arguments):
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[index])


def default_playback_id():
    """Return the eRender/eConsole endpoint ID; None means no queryable endpoint."""
    initialized = False
    enumerator, endpoint = c.c_void_p(), c.c_void_p()
    identifier = c.c_void_p()
    try:
        try:
            ole.CoInitializeEx(None, 2)
            initialized = True
        except OSError as error:
            if error.winerror != -2147417850:  # The thread already uses another apartment model.
                return None
        ole.CoCreateInstance(c.byref(ENUMERATOR), None, 1, c.byref(ENUMERATOR_IID), c.byref(enumerator))
        get_default = method(enumerator, 4, c.c_long, c.c_int, c.c_int, c.POINTER(c.c_void_p))
        if get_default(enumerator, 0, 0, c.byref(endpoint)) < 0:
            return None
        get_id = method(endpoint, 5, c.c_long, c.POINTER(c.c_void_p))
        if get_id(endpoint, c.byref(identifier)) < 0:
            return None
        return c.wstring_at(identifier)
    except OSError:
        return None
    finally:
        if identifier:
            ole.CoTaskMemFree(identifier)
        for pointer in (endpoint, enumerator):
            if pointer:
                method(pointer, 2, wintypes.ULONG)(pointer)
        if initialized:
            ole.CoUninitialize()
