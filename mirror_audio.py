"""Windows playback endpoints, without loading a second media runtime into the GUI."""
import ctypes
from dataclasses import dataclass
import os
import uuid


@dataclass(frozen=True)
class AudioOutput:
    id: str
    name: str
    is_default: bool = False


def output_id(value):
    # Endpoint IDs are opaque strings, not paths or GStreamer expressions.
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return ""
    return value


def resolve_output(preferred, outputs):
    preferred = output_id(preferred)
    if preferred:
        for device in outputs:
            if device.id.casefold() == preferred.casefold():
                return device.id, False
    return "", bool(preferred)


def audio_sink(enabled, device=""):
    if not enabled:
        return "0"
    device = output_id(device)
    if not device:
        return "autoaudiosink"
    escaped = device.replace("\\", "\\\\").replace('"', '\\"')
    return 'wasapi2sink device="' + escaped + '"'


class _Guid(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__()
        self.bytes[:] = uuid.UUID(value).bytes_le


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", ctypes.c_uint32)]


class _Vector(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("data", ctypes.c_void_p)]


class _Value(ctypes.Union):
    _fields_ = [("string", ctypes.c_void_p), ("vector", _Vector)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3), ("value", _Value)]


def _method(obj, slot, result, *args):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *args)(table[slot])


def _check(result):
    if result < 0:
        raise OSError("Windows audio endpoint query failed (0x%08x)" % (result & 0xffffffff))


def _release(obj):
    if obj:
        _method(obj, 2, ctypes.c_uint32)(obj)


def enumerate_outputs():
    """List active render endpoints; own and release every COM allocation on this thread."""
    if os.name != "nt":
        return []
    pointer = ctypes.c_void_p
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [pointer, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(_Guid), pointer, ctypes.c_uint32,
                                    ctypes.POINTER(_Guid), ctypes.POINTER(pointer)]
    ole.CoCreateInstance.restype = ctypes.c_int32
    ole.CoTaskMemFree.argtypes = [pointer]
    ole.CoTaskMemFree.restype = None
    ole.PropVariantClear.argtypes = [ctypes.POINTER(_PropVariant)]
    ole.PropVariantClear.restype = ctypes.c_int32
    initialized = ole.CoInitializeEx(None, 0)
    if initialized not in (0, 1, -2147417850):  # Existing STA is valid too (RPC_E_CHANGED_MODE).
        _check(initialized)
    enumerator, collection, default = pointer(), pointer(), pointer()

    def device_id(device):
        value = pointer()
        _check(_method(device, 5, ctypes.c_int32, ctypes.POINTER(pointer))(device, ctypes.byref(value)))
        try:
            return ctypes.wstring_at(value) if value else ""
        finally:
            ole.CoTaskMemFree(value)

    def device_name(device):
        store = pointer()
        value = _PropVariant()
        try:
            _check(_method(device, 4, ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(pointer))(
                device, 0, ctypes.byref(store)))
            key = _PropertyKey(_Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), 14)
            _check(_method(store, 5, ctypes.c_int32, ctypes.POINTER(_PropertyKey), ctypes.POINTER(_PropVariant))(
                store, ctypes.byref(key), ctypes.byref(value)))
            return ctypes.wstring_at(value.value.string) if value.vt == 31 and value.value.string else "播放设备"
        finally:
            ole.PropVariantClear(ctypes.byref(value))
            _release(store)

    try:
        _check(ole.CoCreateInstance(ctypes.byref(_Guid("bcde0395-e52f-467c-8e3d-c4579291692e")),
                                   None, 1, ctypes.byref(_Guid("a95664d2-9614-4f35-a746-de8db63617e6")),
                                   ctypes.byref(enumerator)))
        default_id = ""
        if _method(enumerator, 4, ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(pointer))(
                enumerator, 0, 0, ctypes.byref(default)) >= 0:
            default_id = device_id(default)
        _check(_method(enumerator, 3, ctypes.c_int32, ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(pointer))(
            enumerator, 0, 1, ctypes.byref(collection)))  # eRender, DEVICE_STATE_ACTIVE
        count = ctypes.c_uint32()
        _check(_method(collection, 3, ctypes.c_int32, ctypes.POINTER(ctypes.c_uint32))(
            collection, ctypes.byref(count)))
        devices = []
        for index in range(count.value):
            device = pointer()
            try:
                _check(_method(collection, 4, ctypes.c_int32, ctypes.c_uint32, ctypes.POINTER(pointer))(
                    collection, index, ctypes.byref(device)))
                identity = device_id(device)
                devices.append(AudioOutput(identity, device_name(device), identity == default_id))
            except OSError:
                continue  # An endpoint can disappear during enumeration.
            finally:
                _release(device)
        return sorted(devices, key=lambda item: (not item.is_default, item.name.casefold(), item.id))
    finally:
        _release(default)
        _release(collection)
        _release(enumerator)
        if initialized in (0, 1):
            ole.CoUninitialize()
