"""C++ filename search bridge; bounded results, no resident Python filename catalog."""
import ctypes
import os
from pathlib import Path
import sys
import threading

_CANCEL = ctypes.CFUNCTYPE(ctypes.c_int)
_PROGRESS = ctypes.CFUNCTYPE(None, ctypes.c_longlong)
_ROW = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
_dll = None
_load_lock = threading.Lock()


class Cancelled(Exception):
    pass


def index_path(source):
    return os.path.join(os.path.dirname(os.path.abspath(source)), "fsearch-fast.db")


def _library():
    global _dll
    with _load_lock:
        if _dll is None:
            root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            library = ctypes.CDLL(str(root / "native" / "jj_search.dll"))
            library.jj_version.restype = ctypes.c_int
            if library.jj_version() != 1:
                raise RuntimeError("搜索内核版本不匹配")
            paths = [ctypes.c_char_p, ctypes.c_char_p]
            error = [ctypes.c_char_p, ctypes.c_int]
            library.jj_ready.argtypes = paths
            library.jj_ready.restype = ctypes.c_int
            library.jj_build.argtypes = paths + [ctypes.c_int, _CANCEL, _PROGRESS] + error
            library.jj_build.restype = ctypes.c_int
            library.jj_sync.argtypes = paths + [ctypes.c_int, _CANCEL] + error
            library.jj_sync.restype = ctypes.c_int
            library.jj_query.argtypes = paths + [ctypes.POINTER(ctypes.c_char_p), ctypes.c_int,
                                               ctypes.c_int, _CANCEL, _ROW] + error
            library.jj_query.restype = ctypes.c_int
            _dll = library
    return _dll


def _paths(source):
    return os.path.abspath(source).encode("utf-8"), index_path(source).encode("utf-8")


def ready(source):
    return bool(_library().jj_ready(*_paths(source)))


def build(source, force=False, cancelled=lambda: False, progress=lambda count: None):
    error = ctypes.create_string_buffer(2048)
    result = _library().jj_build(*_paths(source), int(force), _CANCEL(cancelled),
                                 _PROGRESS(progress), error, len(error))
    if result == 2:
        raise Cancelled("索引已停止")
    if result < 0:
        raise RuntimeError("快速搜索索引：" + error.value.decode("utf-8", "replace"))


def sync(source, cancelled=lambda: False, limit=4096):
    error = ctypes.create_string_buffer(2048)
    result = _library().jj_sync(*_paths(source), limit, _CANCEL(cancelled), error, len(error))
    if result < 0:
        raise RuntimeError("快速搜索更新：" + error.value.decode("utf-8", "replace"))
    return result


def query(source, tokens, cancelled, limit=500):
    rows = []
    error = ctypes.create_string_buffer(2048)
    arguments = (ctypes.c_char_p * len(tokens))(*(token.encode("utf-8") for token in tokens))

    def deliver(name, directory, path):
        rows.append((name.decode("utf-8", "replace"), directory, path.decode("utf-8", "replace")))

    result = _library().jj_query(*_paths(source), arguments, len(tokens), limit,
                                 _CANCEL(cancelled.is_set), _ROW(deliver), error, len(error))
    if result == 2:
        raise Cancelled()
    if result < 0:
        raise RuntimeError("文件搜索：" + error.value.decode("utf-8", "replace"))
    return rows, bool(result)
