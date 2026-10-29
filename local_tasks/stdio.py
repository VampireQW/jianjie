"""让 Windows 无控制台正式 EXE 在 --mcp 模式下使用父进程传入的管道。"""
import io
import os
import sys


def prepare_stdio():
    for name, number, mode in (("stdin", -10, "rb"), ("stdout", -11, "wb"), ("stderr", -12, "wb")):
        stream = getattr(sys, name)
        if stream is None:
            if os.name != "nt":
                raise OSError("MCP requires redirected standard streams")
            import ctypes
            import msvcrt
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel.GetStdHandle.restype = wintypes.HANDLE
            handle = kernel.GetStdHandle(number & 0xffffffff)
            if not handle or handle == ctypes.c_void_p(-1).value:
                if name == "stderr":
                    stream = open(os.devnull, "w", encoding="utf-8")
                else:
                    raise OSError("请由 MCP 客户端使用 --mcp 启动简截")
            else:
                flags = os.O_BINARY | (os.O_RDONLY if name == "stdin" else os.O_WRONLY)
                fd = msvcrt.open_osfhandle(handle, flags)
                stream = io.TextIOWrapper(os.fdopen(fd, mode, buffering=0), encoding="utf-8",
                                          write_through=True)
            setattr(sys, name, stream)
        elif hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
