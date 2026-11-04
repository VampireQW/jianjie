"""Optional dual-column wheel forwarding. No pointer moves, focus changes or polling."""
import ctypes as ct
from ctypes import wintypes as W
from collections import deque
from dataclasses import dataclass
import os
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication


@dataclass(frozen=True)
class LinkGeometry:
    left: int
    top: int
    right: int
    bottom: int
    split: int
    margin: int = 10

    def peer(self, x, y):
        if not (self.left <= x < self.right and self.top <= y < self.bottom):
            return None
        if abs(x-self.split) <= self.margin:
            return None
        if x < self.split:
            return (self.split+self.right)//2, y
        return (self.left+self.split)//2, y

    def centers(self):
        y = (self.top+self.bottom)//2
        return ((self.left+self.split)//2, y), ((self.split+self.right)//2, y)


def scaled_geometry(monitor, local, scale, fraction):
    x, y, width, height = local
    left, top = monitor[:2]
    x0, y0 = left+round(x*scale), top+round(y*scale)
    x1, y1 = min(monitor[2], left+round((x+width)*scale)), min(monitor[3], top+round((y+height)*scale))
    x0, y0 = max(left, x0), max(top, y0)
    if x1-x0 < 20 or y1-y0 < 20:
        raise ValueError('选区过小，无法启用联动滚动。')
    split = x0+round((x1-x0)*min(.9, max(.1, fraction)))
    return LinkGeometry(x0, y0, x1, y1, split, max(2, round(10*scale)))


def wheel_parameters(delta, point):
    x, y = point
    # WM_MOUSEWHEEL uses signed 16-bit screen coordinates, including negative monitors.
    return (delta & 0xffff) << 16, (x & 0xffff) | ((y & 0xffff) << 16)


def physical_geometry(screen, region, fraction):
    user = ct.WinDLL('user32')
    class MonitorInfo(ct.Structure):
        _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT),
                    ('flags', W.DWORD), ('name', W.WCHAR*32)]
    callback_type = ct.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ct.POINTER(W.RECT), W.LPARAM)
    user.EnumDisplayMonitors.argtypes = [W.HDC, ct.POINTER(W.RECT), callback_type, W.LPARAM]
    user.GetMonitorInfoW.argtypes = [W.HMONITOR, ct.POINTER(MonitorInfo)]
    found = []
    def visit(handle, dc, rectangle, data):
        info = MonitorInfo(); info.size = ct.sizeof(info)
        if user.GetMonitorInfoW(handle, ct.byref(info)) and info.name.casefold() == screen.name().casefold():
            r = info.monitor
            found.append((r.left, r.top, r.right, r.bottom))
        return True
    callback = callback_type(visit)
    if not user.EnumDisplayMonitors(None, None, callback, 0) or len(found) != 1:
        raise OSError('未能定位截图屏幕，请重新框选后开启联动。')
    if region is None:
        g = screen.geometry()
        local = (0, 0, g.width(), g.height())
    else:
        local = (region.x(), region.y(), region.width(), region.height())
    return scaled_geometry(found[0], local, screen.devicePixelRatio() or 1., fraction)


class SystemMouseEvent(ct.Structure):
    _fields_ = [('point', W.POINT), ('data', W.DWORD), ('flags', W.DWORD),
                ('time', W.DWORD), ('extra', ct.c_size_t)]


def decode_system_wheel(code, message, event):
    if code != 0 or message != 0x20a:
        return None
    delta = ct.c_short(event.data >> 16).value
    return (event.point.x, event.point.y, delta) if delta else None


class WheelEventBuffer:
    """Bounded, ordered small deltas; never round, merge opposite directions, or replay old sessions."""
    def __init__(self):
        self._events = deque()

    def add(self, generation, packet):
        if len(self._events) >= 256:
            raise OverflowError('滚动过快，联动已暂停，请缓慢滚动后重新开启。')
        wake = not self._events
        self._events.append((generation, packet))
        return wake

    def take(self, generation):
        while self._events:
            recorded, packet = self._events.popleft()
            if recorded == generation:
                yield packet

    def clear(self):
        self._events.clear()


class _Windows:
    WM_UPDATE, WM_FORWARD, WM_CLOSE = 0x8001, 0x8002, 0x10

    def __init__(self):
        self.user = u = ct.WinDLL('user32', use_last_error=True)
        self.kernel = k = ct.WinDLL('kernel32', use_last_error=True)
        self.proc_type = ct.WINFUNCTYPE(ct.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        self.hook_type = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_int, W.WPARAM, W.LPARAM)
        class WindowClass(ct.Structure):
            _fields_ = [('style', W.UINT), ('procedure', self.proc_type), ('class_extra', ct.c_int),
                        ('window_extra', ct.c_int), ('instance', W.HINSTANCE), ('icon', W.HICON),
                        ('cursor', W.HANDLE), ('brush', W.HBRUSH), ('menu', W.LPCWSTR), ('name', W.LPCWSTR)]
        self.WindowClass = WindowClass
        u.RegisterClassW.argtypes = [ct.POINTER(WindowClass)]
        u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ct.c_int, ct.c_int,
                                      ct.c_int, ct.c_int, W.HWND, W.HMENU, W.HINSTANCE, ct.c_void_p]
        u.CreateWindowExW.restype = W.HWND
        u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.DefWindowProcW.restype = ct.c_ssize_t
        u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.GetMessageW.argtypes = [ct.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
        u.DispatchMessageW.argtypes = [ct.POINTER(W.MSG)]
        u.DispatchMessageW.restype = ct.c_ssize_t
        u.TranslateMessage.argtypes = [ct.POINTER(W.MSG)]
        u.DestroyWindow.argtypes = [W.HWND]
        u.UnregisterClassW.argtypes = [W.LPCWSTR, W.HINSTANCE]
        u.SetWindowsHookExW.argtypes = [ct.c_int, self.hook_type, W.HINSTANCE, W.DWORD]
        u.SetWindowsHookExW.restype = W.HHOOK
        u.UnhookWindowsHookEx.argtypes = [W.HHOOK]
        u.CallNextHookEx.argtypes = [W.HHOOK, ct.c_int, W.WPARAM, W.LPARAM]
        u.CallNextHookEx.restype = ct.c_ssize_t
        u.WindowFromPhysicalPoint.argtypes = [W.POINT]
        u.WindowFromPhysicalPoint.restype = W.HWND
        u.GetAncestor.argtypes = [W.HWND, W.UINT]
        u.GetAncestor.restype = W.HWND
        u.GetWindowThreadProcessId.argtypes = [W.HWND, ct.POINTER(W.DWORD)]
        u.GetAsyncKeyState.argtypes = [ct.c_int]
        u.GetAsyncKeyState.restype = ct.c_short
        k.GetModuleHandleW.argtypes = [W.LPCWSTR]
        k.GetModuleHandleW.restype = W.HMODULE

    def window_at(self, point):
        return self.user.WindowFromPhysicalPoint(W.POINT(*point)) or 0

    def root(self, handle):
        return self.user.GetAncestor(handle, 2) or 0

    def process(self, handle):
        process = W.DWORD()
        self.user.GetWindowThreadProcessId(handle, ct.byref(process))
        return process.value

    def post_wheel(self, handle, delta, point):
        return bool(self.user.PostMessageW(handle, 0x20a, *wheel_parameters(delta, point)))

class WheelForwarder:
    """Only forward into the two original visible root windows, never an unrelated overlay."""
    def __init__(self, api, geometry, excluded_pid):
        self.api, self.geometry, self.excluded_pid = api, geometry, excluded_pid
        handles = [api.window_at(point) for point in geometry.centers()]
        if any(not handle or api.process(handle) == excluded_pid for handle in handles):
            raise OSError('请让左右两栏显示在选区内，再开启联动滚动。')
        self.roots = tuple(api.root(handle) for handle in handles)
        if not all(self.roots):
            raise OSError('未能定位左右窗口，请重新开启联动滚动。')

    def forward(self, x, y, delta, modified=False):
        target = self.geometry.peer(x, y)
        if target is None or not delta or modified:
            return False
        side = 0 if x < self.geometry.split else 1
        source_handle, target_handle = self.api.window_at((x, y)), self.api.window_at(target)
        if (not source_handle or not target_handle
                or self.api.process(source_handle) == self.excluded_pid
                or self.api.process(target_handle) == self.excluded_pid
                or self.api.root(source_handle) != self.roots[side]
                or self.api.root(target_handle) != self.roots[1-side]):
            return False
        if not self.api.post_wheel(target_handle, delta, target):
            raise OSError('另一栏暂时无法接收滚动，请确认窗口没有以更高权限运行。')
        return True


class WheelLink(QObject):
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._configuration = None
        self._closed = False
        self._window = 0
        self._thread = None
        self._api = None
        self._hook = 0
        self._generation = 0
        self._packets = WheelEventBuffer()

    def configure(self, geometry):
        if self._closed:
            return
        self._generation += 1
        self._configuration = geometry
        if geometry is not None and (self._thread is None or not self._thread.is_alive()):
            if os.name != 'nt' or QGuiApplication.platformName() != 'windows':
                return
            self._api = _Windows()
            self._thread = threading.Thread(target=self._run, name='JianJieLinkedScroll', daemon=True)
            self._thread.start()
        if self._window:
            self._api.user.PostMessageW(self._window, _Windows.WM_UPDATE, 0, 0)

    def close(self):
        self._closed = True
        self._generation += 1
        self._configuration = None
        if self._window:
            self._api.user.PostMessageW(self._window, _Windows.WM_CLOSE, 0, 0)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=.3)

    def _notify_failure(self, error):
        self._generation += 1
        self._configuration = None
        if not self._closed:
            try:
                self.failed.emit(str(error))
            except RuntimeError:
                pass

    def _run(self):
        api, forwarder = self._api, None
        forwarder_generation = -1
        u = api.user
        instance = api.kernel.GetModuleHandleW(None)
        name = 'JianJieLinkedScroll-%s-%x' % (os.getpid(), id(self))

        def detach():
            if self._hook:
                u.UnhookWindowsHookEx(self._hook)
                self._hook = 0
            self._packets.clear()

        def low_level(code, message, data):
            # This callback only queues wheel data and always passes the original
            # input onward. Window queries and forwarding run after it returns.
            try:
                geometry, generation = self._configuration, self._generation
                if (code == 0 and message == 0x20a and geometry is not None
                        and not self._closed):
                    event = ct.cast(data, ct.POINTER(SystemMouseEvent)).contents
                    packet = decode_system_wheel(code, message, event)
                    if packet is not None and geometry.peer(*packet[:2]) is not None:
                        modified = any(u.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 1, 2, 4))
                        if not modified and self._packets.add(generation, (*packet, False)):
                            if not u.PostMessageW(self._window, api.WM_FORWARD, 0, 0):
                                raise OSError('联动滚动暂时不可用，请重新开启。')
            except Exception as error:
                self._notify_failure(error)
                u.PostMessageW(self._window, api.WM_UPDATE, 0, 0)
            return u.CallNextHookEx(self._hook, code, message, data)

        hook_callback = api.hook_type(low_level)

        def procedure(window, message, parameter, data):
            nonlocal forwarder, forwarder_generation
            try:
                if message == api.WM_CLOSE:
                    u.PostQuitMessage(0)
                    return 0
                if message == api.WM_UPDATE:
                    geometry = self._configuration
                    self._packets.clear()
                    forwarder = WheelForwarder(api, geometry, os.getpid()) if geometry is not None else None
                    forwarder_generation = self._generation
                    if forwarder is not None and not self._hook:
                        self._hook = u.SetWindowsHookExW(14, hook_callback, instance, 0)
                        if not self._hook:
                            raise OSError('暂时无法启用联动滚动，请关闭后重新尝试。')
                    elif forwarder is None:
                        detach()
                    return 0
                if message == api.WM_FORWARD:
                    generation = self._generation
                    if forwarder_generation != generation:
                        return 0
                    for packet in self._packets.take(generation):
                        if generation != self._generation:
                            self._packets.clear()
                            break
                        if forwarder is not None and forwarder.geometry == self._configuration and not self._closed:
                            forwarder.forward(*packet)
                    return 0
            except Exception as error:
                self._notify_failure(error)
                forwarder = None
                detach()
            return u.DefWindowProcW(window, message, parameter, data)

        callback = api.proc_type(procedure)
        window_class = api.WindowClass(0, callback, 0, 0, instance, None, None, None, None, name)
        atom = window = 0
        try:
            atom = u.RegisterClassW(ct.byref(window_class))
            if not atom:
                raise OSError('暂时无法创建联动滚动会话，请重新尝试。')
            window = u.CreateWindowExW(0, name, '', 0, 0, 0, 0, 0, W.HWND(-3), None, instance, None)
            if not window:
                raise OSError('暂时无法创建联动滚动会话，请重新尝试。')
            self._window = window
            if self._closed:
                return
            procedure(window, api.WM_UPDATE, 0, 0)
            message = W.MSG()
            while not self._closed:
                result = u.GetMessageW(ct.byref(message), None, 0, 0)
                if result <= 0:
                    if not self._closed:
                        self._notify_failure('联动滚动会话已结束，请重新开启。')
                    break
                u.TranslateMessage(ct.byref(message))
                u.DispatchMessageW(ct.byref(message))
        except Exception as error:
            self._notify_failure(error)
        finally:
            self._window = 0
            detach()
            if window:
                u.DestroyWindow(window)
            if atom:
                u.UnregisterClassW(name, instance)
