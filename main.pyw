# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 入口：托盘常驻 + 全局热键 + 主面板 + 动作控制器。

用法：
    pythonw main.pyw              正常运行（托盘常驻，Ctrl+Alt+Space 呼出面板）
    python  main.pyw --smoke      无界面自检（offscreen，跑通核心逻辑后退出）
"""
import ctypes
import hashlib
import os
import queue
import sys
import tempfile
import threading
import time

# 存储切换由旧进程正常退出后启动；避免新实例抢先打开旧数据库。
if "--wait-parent" in sys.argv:
    try:
        _parent_pid = int(sys.argv[sys.argv.index("--wait-parent") + 1])
        _wait_kernel = ctypes.windll.kernel32
        _wait_kernel.OpenProcess.restype = ctypes.c_void_p
        _wait_kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        _wait_kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        _parent_handle = _wait_kernel.OpenProcess(0x00100000, False, _parent_pid)
        if _parent_handle:
            _wait_result = _wait_kernel.WaitForSingleObject(_parent_handle, 30000)
            _wait_kernel.CloseHandle(_parent_handle)
            if _wait_result != 0:
                sys.exit(1)
    except (ValueError, IndexError):
        sys.exit(1)

# 本应用的 NumPy 只做逐像素运算；OpenBLAS 默认线程池会为闲置线程预留数百 MB。
os.environ["OPENBLAS_NUM_THREADS"] = "1"

# —— 环境加固：本机 Python（C:\python.exe）的 site-packages 解析受启动环境/cwd 影响，
# mss 等时有时无。显式补上，保证任何环境下都能导入。必须先于第三方导入执行。
for _sp in (os.path.join(sys.prefix, "Lib", "site-packages"), r"C:\Lib\site-packages"):
    if os.path.isdir(_sp) and _sp not in sys.path:
        sys.path.append(_sp)

import subprocess

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (QAction, QColor, QGuiApplication, QImage, QImageReader, QKeySequence,
                           QPixmap)
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QGraphicsDropShadowEffect,
                               QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QSystemTrayIcon,
                               QTextEdit, QVBoxLayout, QInputDialog, QWidget)

if __name__ == "__main__" and "--fsearch-build" not in sys.argv:
    import storage as _startup_storage
    _storage_state = _startup_storage.read_locator()
    _configured_data = _storage_state.get("data_dir")
    if _configured_data and not os.path.isdir(_configured_data):
        _repair_app = QApplication.instance() or QApplication(sys.argv)
        while True:
            QMessageBox.warning(None, "简截 · 数据目录不可用",
                                "任务和设置所在的目录暂时无法访问：\n" + _configured_data +
                                "\n请连接原磁盘，或重新选择包含原数据的文件夹。")
            _repaired = QFileDialog.getExistingDirectory(None, "重新选择简截数据目录", os.path.expanduser("~"))
            if not _repaired:
                sys.exit(1)
            if not os.path.isfile(os.path.join(_repaired, "settings.json")):
                continue
            _storage_state["data_dir"] = _repaired
            _startup_storage.atomic_json(_startup_storage.locator_path(), _storage_state)
            break

import common as C
import executors as EX
from capture import (PinnedWindow, ShotOverlay, close_all, create_colorpick_overlays,
                     create_overlays, freeze_screens, grab_screen)
from panel import Panel
import sticky
import widgets as W
from jj_variant import LITE          # R107 构建变体：轻量版（截图+长截图）打包前由 build_dual.py 重写


def LongEditor(*args, **kwargs):
    from longedit import LongEditor as Editor
    return Editor(*args, **kwargs)


def LongShotBar(*args, **kwargs):
    from longshot import LongShotBar as Bar
    return Bar(*args, **kwargs)


def RecBar(*args, **kwargs):
    from recorder import RecBar as Bar
    return Bar(*args, **kwargs)

# ---------------- 全局热键（Win32 RegisterHotKey，无需第三方库；支持自定义+热重启） ----------------
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL = 0x0001, 0x0002
MOD_SHIFT, MOD_WIN = 0x0004, 0x0008
VK_SPACE = 0x20

_HOTKEY_ACTIONS_FULL = [("panel", "主面板"), ("shot", "截图"), ("longshot", "长截图"), ("rec", "录屏"),
                        ("note", "任务"), ("colorpick", "取色"), ("fsearch", "文件搜索")]     # 只能尾部加，保留旧动作的热键序号
# R107 轻量版只留 面板/截图/长截图——恰好是头部三项，poll() 序号映射 1/2/3 不变
_HOTKEY_ACTIONS = (_HOTKEY_ACTIONS_FULL[:3] if LITE else _HOTKEY_ACTIONS_FULL)
DEFAULT_HOTKEYS = {"panel": "Ctrl+Alt+Space", "shot": "Ctrl+Alt+A",
                   "longshot": "Ctrl+Alt+S", "rec": "Ctrl+Alt+R", "note": "Alt+N",
                   "colorpick": "Ctrl+Alt+C", "fsearch": "Ctrl+Alt+F"}
_MODS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT, "Win": MOD_WIN}
_VK_NAMED = {"Space": 0x20, "Tab": 0x09, "Enter": 0x0D, "Return": 0x0D, "Backspace": 0x08,
             "Home": 0x24, "End": 0x23, "PgUp": 0x21, "PgDown": 0x22,
             "Del": 0x2E, "Delete": 0x2E, "Ins": 0x2D, "Insert": 0x2D,
             "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
             "Print": 0x2A, "Pause": 0x13, "ScrollLock": 0x91,
             "`": 0xC0, ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE,
             "/": 0xBF, "\\": 0xDC, "[": 0xDB, "]": 0xDD, "'": 0xDE}


def _vk_of(name):
    """键名 → Win32 虚拟键码；不支持返回 None。"""
    if len(name) == 1 and name.isdigit():
        return 0x30 + int(name)
    if len(name) == 1 and name.isalpha():
        return ord(name.upper())
    if len(name) in (2, 3) and name[0] == "F" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    return _VK_NAMED.get(name)


def hk_parse(combo):
    """'Ctrl+Alt+Space' → (mods, vk)；非法组合返回 None。"""
    if not combo or not isinstance(combo, str):
        return None
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    mods = 0
    while parts and parts[0] in _MODS:
        mods |= _MODS[parts.pop(0)]
    if len(parts) != 1:
        return None
    vk = _vk_of(parts[0])
    if vk is None:
        return None
    return mods, vk


_hotkey_ctl = {"queue": queue.Queue(), "thread": None, "thread_id": None,
               "ok": set(), "failed": set(), "stop_event": None, "paused": False,
               "notify": None}


def hotkey_loop(bindings, stop_event=None):
    """注册全部组合后进入消息循环；向本线程 PostThreadMessage(WM_QUIT) 退出并注销。"""
    user32 = ctypes.windll.user32
    stop_event = stop_event or threading.Event()
    import ctypes.wintypes as wt
    msg = wt.MSG()
    # 先创建线程消息队列，再发布 thread_id，避免刚启动就暂停时 WM_QUIT 投递失败。
    user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
    _hotkey_ctl["thread_id"] = ctypes.windll.kernel32.GetCurrentThreadId()
    ok, failed = set(), set()
    try:
        for i, mod, vk in bindings:
            if stop_event.is_set():
                break
            (ok if user32.RegisterHotKey(None, i, mod, vk) else failed).add(i)
        _hotkey_ctl["ok"], _hotkey_ctl["failed"] = ok, failed
        while not stop_event.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and not _hotkey_ctl["paused"]:
                _hotkey_ctl["queue"].put(msg.wParam)
                notify = _hotkey_ctl["notify"]
                if notify is not None:
                    notify()
    finally:
        for i in ok:
            user32.UnregisterHotKey(None, i)


def stop_hotkeys():
    """正常注销热键并清空已排队动作；也能停止尚未完成启动的线程。"""
    user32 = ctypes.windll.user32
    stop_event = _hotkey_ctl.get("stop_event")
    if stop_event is not None:
        stop_event.set()
    old_id = _hotkey_ctl.get("thread_id")
    if old_id:
        user32.PostThreadMessageW(old_id, 0x0012, 0, 0)       # WM_QUIT
    th = _hotkey_ctl.get("thread")
    if th:
        th.join(timeout=1.5)
        if th.is_alive():
            return False
    _hotkey_ctl["thread"] = None
    _hotkey_ctl["thread_id"] = None
    _hotkey_ctl["stop_event"] = None
    _hotkey_ctl["ok"] = _hotkey_ctl["failed"] = set()
    try:
        while True:
            _hotkey_ctl["queue"].get_nowait()
    except queue.Empty:
        pass
    return True


def restart_hotkeys(bindings):
    """先正常注销旧绑定，再启动新注册线程。"""
    if not stop_hotkeys():
        return False
    stop_event = threading.Event()
    _hotkey_ctl["stop_event"] = stop_event
    th = threading.Thread(target=hotkey_loop, args=(bindings, stop_event), daemon=True)
    _hotkey_ctl["thread"] = th
    th.start()
    return True


# ---------------- 屏上 toast ----------------
class ToastWindow(QWidget):
    """按用户选择的屏幕角落浮出提示。"""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput)
        self._settings = settings if settings is not None else C.load_settings()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.lbl = QLabel(self)
        self.lbl.setContentsMargins(16, 9, 16, 9)
        self.lbl.setMaximumWidth(520)
        self.lbl.setWordWrap(True)
        self.lbl.setStyleSheet("background:rgba(18,20,28,238);color:#ffffff;"
                               "border-radius:8px;font-size:13px;")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_msg(self, msg):
        self.lbl.setText(msg)
        self.lbl.adjustSize()
        self.resize(self.lbl.size())
        anchor = self.parentWidget()
        screen = anchor.screen() if anchor is not None else None
        screen = screen or QGuiApplication.primaryScreen()
        C.position_screen_notice(self, self._settings, screen)
        self.show()
        self.raise_()
        self._timer.start(2400)


def _show_dialog_toast(dialog, msg, delay=120, settings=None):
    """模态卡片期间的 toast 由卡片持有，避免独立置顶窗被模态窗压住。"""
    previous = getattr(dialog, "_attached_toast", None)
    if previous is not None:
        dialog._attached_toast_timer.stop()
        dialog._attached_toast_timer.deleteLater()
        previous.hide()
        previous.deleteLater()
    toast = ToastWindow(dialog, settings=settings)
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: toast.show_msg(msg))
    dialog._attached_toast = toast
    dialog._attached_toast_timer = timer
    timer.start(delay)
    return toast


# ---------------- R154 截图完成缩略图通知卡 ----------------
class ShotNotifyCard(QWidget):
    """R154 截图/长截图完成通知卡（用户三点）：右下角缩略图卡——
    ①完成对号后弹出，5s 自动消失（hover 暂停倒计时、右键立即关闭）；
    ②点击卡片进入图片编辑状态（LongEditor 同套标注工具）；
    ③与原「已保存 · path」纯文字 toast 整合——保存/复制状态由卡内副文直接承担，不再双提示。
    壳语言对表 MASTER.md 黑透 HUD：rgba(16,20,30,232) 圆角 10 + 1px 白 18 边 + 柔影 26/4/130。"""

    LIFE_MS = 5000                              # 自动消失时长（hover 暂停）
    THUMB_W, THUMB_H = 96, 56                   # 缩略图槽位（等比缩放入内居中）

    def __init__(self, settings=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self._settings = settings if settings is not None else C.load_settings()
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.PointingHandCursor)
        self._on_edit = None
        self._on_send = None
        self._remain = self.LIFE_MS
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.body = QFrame(self)
        self.body.setObjectName("shotNotify")
        self.body.setStyleSheet(
            "QFrame#shotNotify{background:rgba(16,20,30,232);"
            "border:1px solid rgba(255,255,255,18);border-radius:10px;}"
            "QLabel{background:transparent;color:#e5e7eb;}")
        _sh = QGraphicsDropShadowEffect(self)
        _sh.setBlurRadius(26)
        _sh.setOffset(0, 4)
        _sh.setColor(QColor(0, 0, 0, 130))
        self.body.setGraphicsEffect(_sh)
        root.addWidget(self.body)
        h = QHBoxLayout(self.body)
        h.setContentsMargins(10, 8, 12, 8)
        h.setSpacing(10)
        self.thumb = QLabel(self.body)
        self.thumb.setFixedSize(self.THUMB_W, self.THUMB_H)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("background:rgba(0,0,0,120);border-radius:6px;")
        h.addWidget(self.thumb, 0, Qt.AlignVCenter)
        v = QVBoxLayout()
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        self.title = QLabel(self.body)
        self.title.setStyleSheet("font-size:13px;font-weight:600;color:#f9fafb;")
        self.sub = QLabel(self.body)
        self.sub.setStyleSheet("font-size:11px;color:#9ca3af;")
        v.addWidget(self.title)
        v.addWidget(self.sub)
        h.addLayout(v, 1)
        import icons
        self.send_button = QPushButton(self.body)
        self.send_button.setAutoDefault(False)
        self.send_button.setFixedSize(32, 32)
        self.send_button.setIcon(icons.lucide_icon("smartphone", C.UI_TEXT, 18, sw=2.2))
        self.send_button.setIconSize(QSize(18, 18))
        self.send_button.setToolTip("发送到设备")
        self.send_button.setAccessibleName("发送到设备")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.setStyleSheet(
            f"QPushButton{{background:{C.UI_BG_CARD};border:1px solid {C.UI_TEXT3};border-radius:6px;padding:0;}}"
            f"QPushButton:hover,QPushButton:pressed{{background:{C.UI_POPUP_BG};}}"
            f"QPushButton:focus{{border-color:{C.UI_ACCENT};}}")
        self.send_button.clicked.connect(self._send)
        h.addWidget(self.send_button)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._expire)

    def _expire(self):
        self._on_edit = None                 # 通知消失后释放回调捕获的原尺寸截图
        self._on_send = None
        self.hide()

    def _send(self):
        callback = self._on_send
        self._timer.stop()
        self._expire()
        if callback:
            callback()

    def show_shot(self, pm, title, sub, on_edit, on_send=None):
        """pm=QPixmap（可带 DPR）；on_edit=点击进入编辑回调。重复调用＝换内容重置倒计时。"""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPainterPath
        self._on_edit = on_edit
        self._on_send = on_send
        self.send_button.setVisible(on_send is not None)
        d = pm.devicePixelRatio() or 1.0
        t = pm.scaled(round(self.THUMB_W * d), round(self.THUMB_H * d),
                      Qt.KeepAspectRatio, Qt.SmoothTransformation)
        t.setDevicePixelRatio(d)
        rounded = QPixmap(t.size())
        rounded.fill(Qt.transparent)
        rounded.setDevicePixelRatio(d)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rounded.rect()), 6.0, 6.0)      # 圆角缩略图（QPainter 按 DPR 换算）
        p.setClipPath(path)
        p.drawPixmap(0, 0, t)
        p.end()
        self.thumb.setPixmap(rounded)
        self.title.setText(title)
        self.sub.setText(self.sub.fontMetrics().elidedText(sub, Qt.ElideMiddle, 260))  # 长路径中间省略
        self.setToolTip("点击进入编辑 · 右键关闭")
        self.adjustSize()
        C.position_screen_notice(self, self._settings)
        self.show()
        self.raise_()
        self._remain = self.LIFE_MS
        self._timer.start(self.LIFE_MS)

    def enterEvent(self, ev):
        if self._timer.isActive():                 # hover 暂停：剩余时间留着，移出后续走
            self._remain = max(400, self._timer.remainingTime())
            self._timer.stop()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        if not self._timer.isActive() and self.isVisible():
            self._timer.start(self._remain if self._remain > 0 else 1200)
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.RightButton:          # 右键＝立即关闭（不进编辑）
            self._on_edit = None
            self._on_send = None
            self._timer.stop()
            self.hide()
            return
        if ev.button() == Qt.LeftButton and self._on_edit is not None:
            cb, self._on_edit = self._on_edit, None
            self._on_send = None
            self._timer.stop()
            self.hide()
            cb()                                   # 点击进入编辑
            return
        super().mouseReleaseEvent(ev)


# ---------------- 控制器 ----------------
class _ClipboardImageWorker:
    """One encoder and one pending image; release the worker thread when idle."""

    def __init__(self, directory, deliver):
        self.directory = directory
        self.deliver = deliver
        self._lock = threading.Lock()
        self._pending = None
        self._thread = None
        self._closed = False
        self._cleanup = None

    def submit(self, image):
        with self._lock:
            if self._closed:
                return
            # QImage shares its immutable pixels; avoid another full-sized buffer.
            self._pending = (QImage(image), time.time())
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="jianjie-clipboard-image",
                                                daemon=False)
                self._thread.start()

    def close(self, cleanup):
        with self._lock:
            self._closed = True
            self._pending = None
            self._cleanup = cleanup
            if self._thread is not None:
                return
            self._cleanup = None
        cleanup()

    @staticmethod
    def _encode(image, at, directory):
        if image.width() * image.height() > 16_000_000:
            image = image.scaled(2400, 2400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        size = (image.width(), image.height())
        path = None
        try:
            fd, path = tempfile.mkstemp(prefix="image_", suffix=".png", dir=directory)
            os.close(fd)
            if not image.save(path, "PNG"):
                raise OSError("PNG encoding failed")
            digest = hashlib.blake2b(digest_size=16)
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(chunk)
            value, fingerprint = path, digest.digest()
        except OSError:
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            # Keep the fallback bounded even when disk caching is unavailable.
            if image.width() * image.height() > 2400 * 2400:
                image = image.scaled(2400, 2400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                size = (image.width(), image.height())
            value, fingerprint = image, None
        return {"t": "img", "v": value, "at": at, "fingerprint": fingerprint, "size": size}

    def _run(self):
        cleanup = None
        try:
            while True:
                with self._lock:
                    if self._closed or self._pending is None:
                        self._thread = None
                        cleanup, self._cleanup = self._cleanup, None
                        break
                    image, at = self._pending
                    self._pending = None
                entry = self._encode(image, at, self.directory)
                del image
                with self._lock:
                    closed = self._closed
                    if not closed:
                        self.deliver(entry)
                if closed and isinstance(entry["v"], str):
                    try:
                        os.remove(entry["v"])
                    except OSError:
                        pass
                del entry
        finally:
            with self._lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    cleanup, self._cleanup = self._cleanup, None
            if cleanup is not None:
                cleanup()


class Controller(QObject):
    hotkey_ready = Signal()
    clipboard_image_ready = Signal(object)
    phone_image_ready = Signal(str, object)
    phone_text_ready = Signal(str, object)
    exchange_changed = Signal()
    phone_file_ready = Signal(str, object)
    exchange_file_finished = Signal(object)
    phone_activity = Signal(str, bool)

    def __init__(self, tray, panel, settings):
        super().__init__()
        self.tray = tray
        self.panel = panel
        self.S = settings
        self.pin_win = None
        self._pin_generation = None
        self._long_editor = None
        self._long_editor_generation = None
        self.shot_overlays = None     # 多屏覆盖层组（每屏一个，先到先得）
        self._cp_ovs = None           # R103 取色覆盖层组
        self._cp_active = False       # 等待抓帧和正在取色共用重入守卫
        self._cp_generation = 0
        self._cp_timer = QTimer(self)
        self._cp_timer.setSingleShot(True)
        self._cp_timer.timeout.connect(self._open_colorpick)
        QApplication.instance().aboutToQuit.connect(lambda: self.colorpick_finished(""))
        self.long_bar = None
        self.rec_bar = None
        self.gif_bar = None
        self._netspeed_w = None
        self._netspeed_generation = None
        self.clip_history = []
        self._clip_cache = tempfile.TemporaryDirectory(prefix="JianJieClip_")
        self._clip_shutdown = False
        self._clip_encoder = _ClipboardImageWorker(self._clip_cache.name,
                                                   self.clipboard_image_ready.emit)
        self.clipboard_image_ready.connect(self._accept_clip_image, Qt.QueuedConnection)
        self.hotkey_ready.connect(self._dispatch_hotkeys, Qt.QueuedConnection)
        from phone_exchange import ExchangeStore
        self._exchange = ExchangeStore(self._clip_cache.name, self.exchange_changed.emit,
                                       archive_dir=C.transfers_dir,
                                       history_path=lambda: os.path.join(C.data_dir(), "exchange-history.json"))
        self.exchange_changed.connect(self._refresh_exchange)
        self._exchange_expiry = QTimer(self)
        self._exchange_expiry.setSingleShot(True)
        self._exchange_expiry.setTimerType(Qt.PreciseTimer)
        self._exchange_expiry.timeout.connect(self._expire_exchange)
        self._schedule_exchange_expiry()
        QApplication.instance().aboutToQuit.connect(self._shutdown_clip_cache)
        self._clip_win = None
        self._settings_dialog = None
        self._autostart_action = None
        self._cb_seq = None              # 只监听本次启动后产生的图片，避免启动时解码旧大图
        try:
            if sys.platform == "win32" and QGuiApplication.platformName() != "offscreen":
                self._cb_seq = ctypes.windll.user32.GetClipboardSequenceNumber()
        except Exception:
            pass
        self._pick_cb = None
        self._toast = ToastWindow(settings=self.S)        # 屏上提示（托盘气泡会被系统折叠吞掉）
        self._shot_notify = ShotNotifyCard(settings=self.S)   # R154 截图/长截图完成缩略图通知卡（点击进编辑）
        # R107 轻量版无任务清单入口：不建组件（省窗体/读盘开销）；note() 无调用路径
        self.tasks = None if LITE else sticky.TaskManager(self)   # R87 任务清单（滴答式桌面小插件，由 R85 便签改造）
        self._phone_receiver = None
        self._phone_dialog = None
        self.phone_image_ready.connect(self._accept_phone_image)
        self.phone_text_ready.connect(self._accept_phone_text)
        self.phone_file_ready.connect(self._accept_phone_file)
        self.exchange_file_finished.connect(self._complete_file_send)
        self.phone_activity.connect(self._show_phone_activity)
        self._exchange_file_busy = False
        if self.S.get("phone_receive_enabled", False):
            QTimer.singleShot(0, self._restore_phone_receiver)

    def _shutdown_clip_cache(self):
        self._clip_shutdown = True
        self._exchange_expiry.stop()
        self._stop_phone_receiver()
        self._exchange.clear(preserve_history=True)
        # The encoder owns cleanup until its current write ends; quitting never
        # removes the directory underneath a PNG writer or waits on image work.
        self._clip_encoder.close(self._clip_cache.cleanup)

    @Slot()
    def _dispatch_hotkeys(self):
        poll_hotkeys(self, self.panel)

    def _schedule_exchange_expiry(self):
        delay = self._exchange.next_expiry_delay()
        if delay is None:
            self._exchange_expiry.stop()
        else:
            self._exchange_expiry.start(delay)

    def _expire_exchange(self):
        self._exchange.expire()
        self._refresh_exchange()

    def _refresh_exchange(self):
        self._schedule_exchange_expiry()
        dialog = getattr(self, "_phone_dialog", None)
        if dialog is not None and dialog.isVisible() and getattr(dialog, "exchange_pane", None):
            dialog.exchange_pane.refresh()

    def _receive_phone_text(self, text):
        result = {"event": threading.Event(), "ok": False, "message": "电脑处理文字超时，请重试"}
        self.phone_text_ready.emit(text, result)
        if not result["event"].wait(30):
            return False, result["message"]
        return result["ok"], result["message"]

    def _accept_phone_text(self, text, result):
        try:
            from phone_exchange import validate_text
            text = validate_text(text)
            if self._phone_receiver is None:
                result["message"] = "简捷互传已关闭，请重新发送"
                return
            if not C.copy_text_to_clipboard(text):
                result["message"] = "剪贴板暂被占用，请重新发送"
                return
            self._exchange.add_text(text, "phone")
            result.update(ok=True, message="文字已复制，可以直接粘贴")
            self.toast("来自连接设备 · 文字已复制，可以直接粘贴")
        except (ValueError, OSError) as error:
            result["message"] = str(error)
        finally:
            result["event"].set()

    def _receive_phone_file(self, path, name):
        # 磁盘拷贝在网络线程完成，主线程只接管剪贴板和提示。
        try:
            if self._phone_receiver is None:
                return False, "简捷互传已关闭"
            record = self._exchange.add_file(path, name, "phone", move=True)
            local = next((r for r in self._exchange.local_records() if r["id"] == record["id"]), None)
            if local is None:
                return False, "文件记录已失效，请重新发送"
            result = {"event": threading.Event(), "ok": False, "message": "电脑处理文件超时"}
            self.phone_file_ready.emit(local["_path"], result)
            result["event"].wait(30)
            return result["ok"], result["message"]
        except (ValueError, OSError) as error:
            return False, str(error)

    def _accept_phone_file(self, path, result):
        try:
            if self._phone_receiver is None:
                result["message"] = "简捷互传已关闭"
                return
            copied = C.copy_file_to_clipboard(path)
            result.update(ok=True, message=("文件已接收，可以在资源管理器中 Ctrl+V 保存" if copied else "文件已接收，请在简捷互传记录中保存"))
            self.toast("来自连接设备 · " + result["message"])
        finally:
            result["event"].set()

    def _complete_file_send(self, result):
        self._exchange_file_busy = False
        self.toast(result["message"])
        dialog = getattr(self, "_phone_dialog", None)
        if dialog is not None and getattr(dialog, "exchange_pane", None):
            dialog.exchange_pane.finish_file_send(result["path"], result["ok"], result["message"])

    def _show_phone_activity(self, message, error):
        dialog = getattr(self, "_phone_dialog", None)
        if dialog is not None and not dialog._dirty():
            dialog._feedback(message, error)

    def _send_exchange(self, text=None, image=None, path=None, preview=None, file_path=None, file_paths=None):
        temporary = None
        try:
            from phone_exchange import validate_text
            from phone_receive import read_phone_image
            from PySide6.QtCore import QSize
            # 先验证内容，再开启服务，避免无效点击改变用户的开关。
            files = list(file_paths) if file_paths else ([file_path] if file_path else [])
            if files:
                from phone_exchange import MAX_FILE_BYTES
                if getattr(self, "_exchange_file_busy", False):
                    return False, "正在准备文件，请稍候"
                if len(files) > 5:
                    raise ValueError("一次最多选择 5 个文件")
                if any(not os.path.isfile(p) or not 0 < os.path.getsize(p) <= MAX_FILE_BYTES for p in files):
                    raise ValueError("请选择非空文件，每个最大 2 GiB")
            elif text is not None:
                validate_text(text)
            elif path:
                reader = QImageReader(path)
                size = reader.size()
                if not size.isValid():
                    raise ValueError("图片无法读取，请重新选择")
                if preview is None or preview.isNull():
                    preview = read_phone_image(path, QSize(160, 160))
            elif image is not None and not image.isNull():
                size = image.size()
                fd, temporary = tempfile.mkstemp(prefix="exchange_send_", suffix=".png", dir=self._clip_cache.name)
                os.close(fd)
                if not image.save(temporary, "PNG"):
                    raise OSError("图片保存失败，请重试")
                path = temporary
                preview = image.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            else:
                raise ValueError("请粘贴文字或选择一张图片")
            if self._phone_receiver is None:
                config = self._toggle_phone_receive(True)
                if not config["enabled"]:
                    return False, config["message"]
            if files:
                self._exchange_file_busy = True
                def publish():
                    result = dict(path=files[0], ok=False, message="文件准备失败")
                    prepared = 0
                    try:
                        for current in files:
                            self._exchange.add_file(current)
                            prepared += 1
                        label = "文件" if len(files) == 1 else f"{prepared} 个文件"
                        result.update(ok=True, message=label + "已准备好 · 接收端打开简捷互传即可下载")
                    except (ValueError, OSError) as error:
                        result["message"] = (f"已准备 {prepared}/{len(files)} 个文件；" if len(files) > 1 else "") + str(error)
                    finally:
                        self.exchange_file_finished.emit(result)
                threading.Thread(target=publish, name="JianJieFileSend", daemon=True).start()
                return False, "正在准备文件…"
            if text is not None:
                self._exchange.add_text(text)
            else:
                self._exchange.add_image(path, size.width(), size.height(), preview=preview)
            message = "已准备发送 · 接收端打开简捷互传即可获取"
            self.toast(message)
            dialog = getattr(self, "_phone_dialog", None)
            if dialog is not None:
                dialog._refresh(self._phone_config(), preserve_edits=True)
            return True, message
        except (ValueError, OSError) as error:
            return False, str(error)
        finally:
            if temporary:
                try:
                    os.remove(temporary)
                except OSError:
                    pass

    def _send_exchange_image(self, image):
        ok, message = self._send_exchange(image=image)
        if not ok:
            self.toast(message)

    def _send_exchange_entry(self, entry, preview=None):
        if entry["t"] == "txt":
            return self._send_exchange(text=entry["v"])
        value = entry["v"]
        if isinstance(value, QImage):
            return self._send_exchange(image=value)
        return self._send_exchange(path=value, preview=preview)

    def _stop_phone_receiver(self):
        receiver, self._phone_receiver = getattr(self, "_phone_receiver", None), None
        if receiver is not None:
            receiver.stop()

    def _restore_phone_receiver(self):
        config = self._toggle_phone_receive(True)
        if not config["enabled"]:
            self.toast(config["message"])

    def _phone_config(self, message=""):
        from phone_receive import PORT, local_addresses
        from urllib.parse import urlsplit
        addresses = local_addresses()
        if not self.S.get("phone_receive_password"):
            self.S["phone_receive_password"] = os.urandom(20).hex()
        saved_url = self.S.get("phone_receive_url", "")
        try:
            parsed = urlsplit(saved_url)
            port = parsed.port or PORT
            host = parsed.hostname if parsed.hostname in addresses else (addresses[0] if addresses else "")
        except ValueError:
            host, port = (addresses[0] if addresses else ""), PORT
        return {"enabled": self._phone_receiver is not None, "addresses": addresses,
                "url": f"http://{host}:{port}" if host else "", "port": port,
                "user": self.S.get("phone_receive_user", "jianjie"),
                "password": self.S["phone_receive_password"],
                "android_url": (f"http://{host}:{port}/android#token={self._phone_receiver.browser_token}"
                                if host and self._phone_receiver is not None else ""),
                "message": message}

    def _toggle_phone_receive(self, enabled):
        from phone_receive import PhoneReceiver
        config = self._phone_config()
        candidate = None
        try:
            if enabled and self._phone_receiver is None:
                receiver = PhoneReceiver(config["user"], config["password"], self._clip_cache.name,
                                         self._receive_phone_image, port=config["port"],
                                         exchange=getattr(self, "_exchange", None), accept_text=getattr(self, "_receive_phone_text", None),
                                         accept_file=getattr(self, "_receive_phone_file", None),
                                         on_activity=getattr(getattr(self, "phone_activity", None), "emit", None))
                receiver.start()
                candidate = receiver
            settings = C.load_settings()
            changes = {"phone_receive_enabled": bool(enabled), "phone_receive_password": config["password"],
                       "phone_receive_user": config["user"], "phone_receive_url": config["url"]}
            settings.update(changes)
            C.save_settings(settings)
            self.S.update(changes)
            if candidate is not None:
                self._phone_receiver = candidate
                candidate = None
            if not enabled:
                self._stop_phone_receiver()
            note = ("已开启 · 同一局域网，图片、文字与文件双向互传" if enabled else "简捷互传已关闭")
        except OSError as error:
            if candidate is not None:
                candidate.stop()
            if not enabled:
                self._stop_phone_receiver()
            note = "接收状态未能保存：" + str(error)
        return self._phone_config(note)

    def _save_phone_config(self, url, user, password):
        from phone_receive import PhoneReceiver, validate_config, local_addresses
        candidate = None
        try:
            config = validate_config(url, user, password, local_addresses())
            old = self._phone_receiver
            if old is not None and old.port != config["port"]:
                candidate = PhoneReceiver(config["user"], config["password"], self._clip_cache.name,
                                           self._receive_phone_image, port=config["port"],
                                           exchange=getattr(self, "_exchange", None), accept_text=getattr(self, "_receive_phone_text", None),
                                           accept_file=getattr(self, "_receive_phone_file", None),
                                           on_activity=getattr(getattr(self, "phone_activity", None), "emit", None))
                candidate.start()
            changes = {"phone_receive_url": config["url"], "phone_receive_user": config["user"],
                       "phone_receive_password": config["password"]}
            settings = C.load_settings()
            settings.update(changes)
            C.save_settings(settings)
            self.S.update(changes)
            if candidate is not None:
                self._phone_receiver = candidate
                candidate = None
                old.stop()
            elif old is not None:
                old.update_credentials(config["user"], config["password"])
            return True, self._phone_config("已保存，请在手机端同步更新三项连接信息。")
        except (ValueError, OSError) as error:
            if candidate is not None:
                candidate.stop()
            return False, self._phone_config(str(error))

    def _allow_phone_lan(self):
        from phone_receive import request_firewall_access, validate_config, local_addresses
        from urllib.parse import urlsplit
        config = self._phone_config()
        validated = validate_config(config["url"], config["user"], config["password"], local_addresses())
        return request_firewall_access(sys.executable, urlsplit(validated["url"]).hostname,
                                       validated["port"], C.data_dir())

    def phone_receive(self, arg=""):
        current = getattr(self, "_phone_dialog", None)
        if current is not None:
            current.showNormal()
            if getattr(current, "exchange_pane", None):
                current.exchange_pane.refresh()
            C.force_foreground(current)
            return
        dialog = W.PhoneReceiveDialog(self._phone_config(), self._toggle_phone_receive,
                                       self._save_phone_config, self._allow_phone_lan,
                                       on_exchange=({"store": self._exchange, "send": self._send_exchange}
                                                    if getattr(self, "_exchange", None) else None))
        self._phone_dialog = dialog
        generation = object()
        self._phone_dialog_generation = generation
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)

        def forget(_object=None):
            if self._phone_dialog_generation is generation:
                self._phone_dialog = None

        dialog.destroyed.connect(forget)
        dialog.show()
        C.force_foreground(dialog)

    def _receive_phone_image(self, path):
        result = {"event": threading.Event(), "ok": False, "message": "电脑处理图片超时，请重试"}
        self.phone_image_ready.emit(path, result)
        if not result["event"].wait(30):
            return False, result["message"]
        return result["ok"], result["message"]

    def _accept_phone_image(self, path, result):
        try:
            if self._phone_receiver is None or not os.path.isfile(path):
                result["message"] = "接收已关闭，请重新发送"
                return
            from phone_receive import read_phone_image
            try:
                image = read_phone_image(path)
            except ValueError as error:
                result["message"] = str(error)
                self.toast(result["message"])
                return
            if not C.copy_image_to_clipboard(image):
                result["message"] = "剪贴板暂被占用，请重新发送"
                self.toast(result["message"])
                return
            # 已收到的文件直接入历史；阻止轮询再解码、再编码这张大图。
            self._cb_seq = ctypes.windll.user32.GetClipboardSequenceNumber()
            while sum(1 for entry in self.clip_history if entry["t"] == "img") >= 6:
                index = max(i for i, entry in enumerate(self.clip_history) if entry["t"] == "img")
                self._discard_clip_entry(self.clip_history.pop(index))
            entry = {"t": "img", "v": path, "at": time.time(), "fingerprint": None,
                     "size": (image.width(), image.height()), "phone_original": True}
            self.clip_history.insert(0, entry)
            for old in self.clip_history[10:]:
                self._discard_clip_entry(old)
            del self.clip_history[10:]
            if self._clip_win is not None and self._clip_win.isVisible():
                self._clip_win.prepend_entry(entry)
            result["ok"] = True
            result["message"] = "图片已复制，可以直接粘贴"
            if getattr(self, "_exchange", None):
                try:
                    preview = image.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._exchange.add_image(path, image.width(), image.height(), "phone", preview)
                except OSError:
                    pass  # 剪贴板已成功，不能因历史缓存失败要求用户重复发送。
            self.toast("来自连接设备 · 图片已复制，可以直接粘贴")
        except Exception:
            result["message"] = "电脑处理图片失败，请重新发送"
            self.toast(result["message"])
        finally:
            result["event"].set()

    def toast(self, msg, kind="info"):
        self._toast.show_msg(msg)          # 屏上浮出，2.4s 自动消失

    def notify_shot(self, pm, title, sub, img):
        """R154 截图/长截图完成通知：右下角缩略图卡（整合原纯文字 toast）；点击进入编辑。"""
        self._shot_notify.show_shot(pm, title, sub, lambda: self._open_image_editor(img),
                                    lambda: self._send_exchange_image(img))

    def _open_image_editor(self, img, topmost=False):
        """R154 抽取共用（_clip_edit_img 同路径）：QImage → LongEditor 同套标注工具；
        落盘走临时目录用完即删不污染图片目录，交付仍走 long_finished（复制+新通知卡）。"""
        import numpy as _np
        im = img.convertToFormat(QImage.Format_RGB888)
        raw = bytes(im.constBits())
        arr = _np.frombuffer(raw, dtype=_np.uint8).reshape(im.height(), im.bytesPerLine())
        arr = _np.ascontiguousarray(arr[:, :im.width() * 3].reshape(im.height(), im.width(), 3))
        self._track_long_editor(LongEditor(
            arr, frames=0, on_done=self.long_finished,
            on_cancel=lambda: None, topmost=topmost))

    def _track_long_editor(self, editor):
        """关闭后销毁编辑窗及其整图缓存；失败重试期间仍保留窗口。"""
        generation = object()
        self._long_editor_generation = generation
        self._long_editor = editor
        editor.setAttribute(Qt.WA_DeleteOnClose, True)

        def forget(_object=None):
            if self._long_editor_generation is generation:
                self._long_editor = None
                self._long_editor_generation = None

        editor.destroyed.connect(forget)

    def dispatch(self, meth, arg):
        try:
            getattr(self, meth)(arg)
        except Exception as e:                 # 面板已 hide，失败必须可见而不是无声无息
            self.toast(f"「{meth}」执行失败 · {e}")

    # ---- 任务清单（R87） ----
    def note(self, arg=""):
        """面板/托盘菜单点击：任务清单唤到眼前（R96 幂等语义：可见不隐藏，防启动自恢复后被误关）。"""
        self.tasks.show_or_raise()

    def note_toggle(self, arg=""):
        """Alt+N 热键：显隐快速切换（开关语义保留给热键）。"""
        self.tasks.toggle()

    # ---- 截图 ----
    def shot(self, arg=""):
        self.start_shot(ocr=False, hotkey=(arg == "hotkey"))

    def ocr(self, arg=""):
        self.start_shot(ocr=True)

    def start_shot(self, ocr=False, scan=False, hotkey=False):
        # 全局热键不改变前台：趁原应用浮窗仍在，先抓画面再显示编辑层。
        # 从面板点击时仍先隐藏面板，避免把简截自身截进画面。
        immediate = hotkey and not self.panel.isVisible()
        frozen = freeze_screens() if immediate else None
        if not immediate:
            self.panel.hide()

        def do():
            ovs = create_overlays(self.shot_finished, ocr=ocr, scan=scan, frozen=frozen)   # 每屏一个，先到先得
            for ov in ovs:
                ov.pinned.connect(self.pin_pixmap)
            self.shot_overlays = ovs
        if immediate:
            do()
        else:
            QTimer.singleShot(160, do)

    def shot_finished(self, result):
        close_all(self.shot_overlays)              # 多屏组清理（跳过已删败者：炸了 start_gif 就永远轮不到）
        self.shot_overlays = None
        if not result:
            return
        pm = result["pixmap"]
        path = result.get("path")
        if result.get("error"):
            # 保存失败不丢图：自动转入剪贴板
            if pm:
                if not C.copy_image_to_clipboard(pm.toImage()):
                    QGuiApplication.clipboard().setPixmap(pm)
            self.toast(result["error"] + " · 已改为复制到剪贴板")
            return
        if result.get("rec"):                            # R131 截图工具条直通录屏（选区现成）
            self.start_recorder(result.get("sel"), result.get("screen"))
            return
        if result.get("gif"):
            self.start_gif(result.get("sel"), result.get("screen"))
            return
        if result.get("longshot"):
            self.start_longshot(result.get("sel"), result.get("screen"))
            return
        if result.get("ocr"):
            self.ocr_dialog(pm)
        elif result.get("pinned"):
            self.notify_shot(pm, "截图完成", "已钉到屏幕", pm.toImage())
        elif path:
            _sub = f"已保存 · {os.path.basename(path)}"
            self.notify_shot(pm, "截图完成", _sub, pm.toImage())        # R154 缩略图卡整合保存提示
        else:
            self.notify_shot(pm, "截图完成", "已复制到剪贴板", pm.toImage())   # R154 缩略图卡

    # ---- GIF 录制 ----
    def start_gif(self, sel, scr):
        """把选区录成 GIF 动图（Pillow 合成，10fps）。"""
        from gifrec import GifBar, missing_deps
        miss = missing_deps()
        if miss:
            self.toast("GIF 录制缺少依赖：" + "、".join(miss))
            return
        self.gif_bar = GifBar(sel, scr,
                              on_done=self._gif_finished,
                              on_cancel=self._gif_cancelled)

    def _gif_finished(self, path, copied):
        self.gif_bar = None
        self.toast("GIF 已复制，可直接粘贴到聊天窗" if copied
                   else f"GIF 已保存 · {path}")

    def _gif_cancelled(self):
        self.gif_bar = None
        self.toast("GIF 已取消")

    def ocr_dialog(self, pm):
        tmp = os.path.join(C.data_dir(), "ocr_tmp.png")
        if not pm.save(tmp, "PNG"):
            self.toast("OCR 失败：无法暂存截图")
            return
        d = QDialog()
        d.setWindowTitle(f"{C.APP_NAME} · 提取文字（离线 OCR）")
        lay = QVBoxLayout(d)
        stat = QLabel("正在识别…（Windows 离线 OCR，约 1-3 秒）")
        stat.setStyleSheet("color:#8a92a6;font-size:12px;")
        txt = QTextEdit()
        txt.setReadOnly(True)
        row = QHBoxLayout()
        b1 = QPushButton("复制文字")
        b1.setEnabled(False)
        b1.clicked.connect(lambda: QGuiApplication.clipboard().setText(txt.toPlainText()))
        b2 = QPushButton("关闭")
        b2.clicked.connect(d.accept)
        row.addStretch()
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addWidget(stat)
        lay.addWidget(txt)
        lay.addLayout(row)
        d.resize(520, 420)
        d.setWindowFlag(Qt.WindowStaysOnTopHint, True)   # 不被其他置顶窗挡住
        holder = {"done": False}

        def work():
            holder["ok"], holder["text"] = EX.ocr_image(tmp)
            holder["done"] = True
        threading.Thread(target=work, daemon=True).start()

        def poll():
            if not holder["done"]:
                return
            t.stop()
            if holder["ok"]:
                stat.setText(f"识别完成 · {len(holder['text'])} 字（文字可选中的复制）")
                txt.setPlainText(holder["text"])
                b1.setEnabled(True)
            else:
                stat.setText("识别失败")
                txt.setPlainText(holder["text"])
        t = QTimer(d)
        t.timeout.connect(poll)
        t.start(150)
        d.exec()

    def qr_gen(self, arg=""):
        # R91：系统默认对话框 → 全局基准模态卡片（输入卡 + 结果卡）
        dlg = W.QrInputDialog(on_top=True)
        if dlg.exec() != QDialog.Accepted:
            return
        text = dlg.text()
        if not text:
            return
        path = os.path.join(C.pictures_dir(), f"二维码_{C.timestamp()}.png")
        good, err = EX.qr_generate(text, path)
        if not good:
            self.toast(f"生成失败 · {err}")
            return
        pm = QPixmap(path)
        QGuiApplication.clipboard().setPixmap(pm)
        dlg = W.QrResultDialog(pm, path, on_top=True)
        # 结果卡片是模态置顶窗；将 toast 设为其 owned tool window，确保浮在卡片上方。
        _show_dialog_toast(dlg, "已复制到剪贴板", settings=self.S)
        dlg.exec()

    def trim(self, arg=""):
        src = EX.latest_file(C.videos_dir(), (".mp4", ".avi"))
        if not src:
            self.toast("还没有录屏文件，先录一段")
            return
        d = QDialog()
        d.setWindowTitle(f"{C.APP_NAME} · 一键快剪")
        lay = QVBoxLayout(d)
        tip = QLabel(f"源视频（最近录屏）：\n{src}")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a92a6;font-size:12px;")
        row = QHBoxLayout()
        e1 = QLineEdit("0")
        e2 = QLineEdit("3")
        e1.setFixedWidth(70)
        e2.setFixedWidth(70)
        row.addWidget(QLabel("开始（秒）"))
        row.addWidget(e1)
        row.addSpacing(8)
        row.addWidget(QLabel("结束（秒）"))
        row.addWidget(e2)
        row.addStretch()
        go = QPushButton("截取并保存")

        def run():
            try:
                s, e = float(e1.text()), float(e2.text())
            except ValueError:
                self.toast("秒数格式不对")
                return
            out = os.path.join(C.videos_dir(), f"快剪_{C.timestamp()}.mp4")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            good, msg = EX.trim_video(src, s, e, out)
            QApplication.restoreOverrideCursor()
            if good:
                self.toast(f"快剪完成 · {msg}")
                self.toast(f"已保存 · {out}")
                d.accept()
            else:
                self.toast(f"快剪失败 · {msg}")
        go.clicked.connect(run)
        lay.addWidget(tip)
        lay.addLayout(row)
        lay.addWidget(go)
        d.exec()

    def imgedit(self, arg=""):
        p = EX.latest_file(C.pictures_dir(), (".png", ".jpg", ".jpeg", ".bmp"))
        if not p:
            self.toast("还没有截图，先截一张")
            return
        subprocess.Popen(["mspaint", p])
        self.toast(f"已用画图打开 · {p}")

    def settings(self, arg=""):
        dialog = self._settings_dialog
        if dialog is not None:
            dialog.set_autostart(EX.autostart_enabled())
            dialog.raise_()
            dialog.activateWindow()
            return
        current = C.load_settings()
        dialog = W.AppSettingsDialog(current, EX.autostart_enabled(), parent=self.panel)
        self._settings_dialog = dialog

        def save_option(key, value):
            previous = self.S.get(key, C.DEFAULTS.get(key))
            try:
                settings = C.load_settings()
                settings[key] = value
                C.save_settings(settings)
            except OSError:
                dialog.info.setText("设置未能保存，请重试。")
                return False, previous
            self.S[key] = value
            dialog.info.setText("已保存，下次启动继续使用。")
            return True, value

        def select_position(corner):
            saved, value = save_option("toast_corner", corner)
            dialog.set_corner(value)
            if saved:
                if self._shot_notify.isVisible():
                    C.position_screen_notice(self._shot_notify, self.S)
                self._toast.hide()
                label = dict(C.TOAST_CORNERS)[corner]
                _show_dialog_toast(dialog, f"提示将显示在{label}", delay=0, settings=self.S)

        def select_cursor(cursor):
            saved, value = save_option("shot_cursor", cursor)
            dialog.set_cursor(value)
            if saved:
                label = next(label for key, label, _ in C.SHOT_CURSOR_STYLES if key == cursor)
                dialog.info.setText(f"已保存 · 下次框选使用{label}指针。")

        def edit_hotkeys():
            self.hotkeys()
            if self._settings_dialog is dialog:
                dialog.raise_()
                dialog.activateWindow()

        def finished(_result):
            if self._settings_dialog is dialog:
                self._settings_dialog = None
            dialog.deleteLater()

        dialog.hotkeysRequested.connect(edit_hotkeys)
        dialog.positionSelected.connect(select_position)
        dialog.cursorSelected.connect(select_cursor)
        dialog.autostartToggled.connect(lambda on: self.set_autostart(on, dialog))
        dialog.finished.connect(finished)
        area = self.panel.screen().availableGeometry()
        x = max(area.left() + 12, min(self.panel.frameGeometry().right() - dialog.width(),
                                     area.right() - dialog.width() - 12))
        y = max(area.top() + 12, min(self.panel.frameGeometry().top() + 40,
                                    area.bottom() - dialog.height() - 12))
        dialog.move(x, y)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def set_autostart(self, on, dialog=None):
        ok, error = EX.autostart(on)
        actual = EX.autostart_enabled()
        action = self._autostart_action
        if action is not None:
            blocked = action.blockSignals(True)
            action.setChecked(actual)
            action.blockSignals(blocked)
        settings_dialog = dialog or self._settings_dialog
        if settings_dialog is not None:
            settings_dialog.set_autostart(actual)
            settings_dialog.info.setText(("已开启开机自启动。" if actual else "已关闭开机自启动。")
                                         if ok else "开机自启动设置失败：" + error)
        if dialog is None:
            self.toast(("已开启开机自启" if actual else "已关闭开机自启")
                       if ok else "开机自启设置失败：" + error)

    def toast_settings(self, arg=""):
        dialog = W.ToastPositionDialog(self.S.get("toast_corner", "bottom_right"),
                                       parent=self.panel)

        def select(corner):
            try:
                settings = C.load_settings()
                settings["toast_corner"] = corner
                C.save_settings(settings)
            except OSError:
                dialog.set_corner(self.S.get("toast_corner", "bottom_right"))
                dialog.info.setText("提示位置保存失败，请重试。")
                return
            self.S["toast_corner"] = corner
            dialog.set_corner(corner)
            dialog.info.setText("已保存，下次启动继续使用。")
            if self._shot_notify.isVisible():
                C.position_screen_notice(self._shot_notify, self.S)
            self._toast.hide()
            label = dict(C.TOAST_CORNERS)[corner]
            _show_dialog_toast(dialog, f"提示将显示在{label}", delay=0, settings=self.S)

        dialog.positionSelected.connect(select)
        dialog.exec()
        dialog.deleteLater()

    # ---- 快捷键设置 ----
    def hotkeys(self, arg=""):
        """查看/修改全局快捷键；保存后热重启注册线程。
        R92：主面板保持可见（不再隐藏）；系统默认对话框 → 全局基准模态卡。"""
        if _hotkey_ctl["paused"]:
            return
        from capture import DEFAULT_SHOT_TOOL_KEYS as _DSTK, SHOT_TOOL_ACTIONS as _STA
        cur = dict(DEFAULT_HOTKEYS)
        cur.update(self.S.get("hotkeys", {}))
        tk = dict(_DSTK)                                         # R131 截图工具键第二组
        tk.update(self.S.get("shot_tool_keys", {}))
        d = W.HotkeyDialog(_HOTKEY_ACTIONS, cur, hk_parse, on_top=True,
                           extra={"title": "截图工具键 · 标注态单键切换工具（仅截图时生效）",
                                  "actions": _STA, "cur": tk})
        _hotkey_ctl["paused"] = True
        if not stop_hotkeys():
            _hotkey_ctl["paused"] = False
            self.toast("快捷键暂未就绪，请稍后再打开设置")
            return
        accepted = False
        try:
            accepted = d.exec() == QDialog.Accepted
            if accepted:
                self.S["hotkeys"] = dict(d.cur)
                self.S["shot_tool_keys"] = dict(d.cur2)
                C.save_settings(self.S)
        finally:
            restored = dict(DEFAULT_HOTKEYS)
            restored.update(self.S.get("hotkeys", {}))
            bindings = [(i, *hk_parse(restored[key]))
                        for i, (key, _l) in enumerate(_HOTKEY_ACTIONS, 1)]
            registered = restart_hotkeys(bindings)
            _hotkey_ctl["paused"] = False
        if not registered:
            self.toast("快捷键恢复失败，请重新启动简截")
            return

        def report():
            names = {i: label for i, (_k, label) in enumerate(_HOTKEY_ACTIONS, 1)}
            bad = [names[i] for i in _hotkey_ctl["failed"]]
            if accepted or bad:
                self.toast("快捷键已更新" if not bad else
                           "「" + "、".join(bad) + "」快捷键注册失败（可能被其他程序占用）")
        QTimer.singleShot(400, report)

    # ---- 长截图 ----
    def long(self, arg=""):
        """先框选范围（回车=整屏），再浮动条滚动拼接。"""

        def got(sel, scr):
            self.start_longshot(sel, scr)
        self.pick_region(got)

    def start_longshot(self, sel, scr):
        """R105：启动滚动拼接条——热键路径（pick_region 框完）与截图工具条入口（选区已现成）共用。"""
        if sel is None:
            return
        self.long_bar = LongShotBar(on_finish=self.long_finished, on_cancel=self._long_cancelled,
                                     on_edit=self.long_edit, region=sel, screen=scr)

    def _long_cancelled(self):
        self.long_bar = None

    def long_edit(self, arr, frames):
        """编辑按钮：停止拼接后带长图进编辑器；完成仍走 long_finished（剪贴板+toast）。"""
        self.long_bar = None
        self._track_long_editor(LongEditor(
            arr, frames, on_done=self.long_finished, on_cancel=lambda: None))

    # ---- 范围框选（长截图/其他工具共用）----
    def pick_region(self, cb, hotkey=False):
        """纯框选范围（不进标注）；cb(sel, screen)，Esc 取消 cb(None, None)。"""
        # 录屏热键同截图：先冻结前台画面和窗口位置，保留原应用的临时浮窗。
        immediate = hotkey and not self.panel.isVisible()
        frozen = freeze_screens() if immediate else None
        if not immediate:
            self.panel.hide()
        self._pick_cb = cb

        def do():
            self.shot_overlays = create_overlays(self.pick_finished, pick=True, frozen=frozen)
        if immediate:
            do()
        else:
            QTimer.singleShot(160, do)

    def pick_finished(self, result):
        close_all(self.shot_overlays)              # 多屏组清理（跳过已删败者：R64 同类雷，炸了 cb 就轮不到）
        self.shot_overlays = None
        cb = self._pick_cb
        self._pick_cb = None
        if not cb:
            return
        if result and result.get("pick"):
            cb(QRect(result["sel"]), result["screen"])
        else:
            cb(None, None)

    def long_finished(self, path, frames, copy=True, image=None):
        # copy=True（对号/编辑器路径）：图片直接进剪贴板（Ctrl+V 即贴图）；
        # copy=False（⬇ 下载钮）：纯落盘不碰剪贴板。
        # Windows 写入完整 CF_DIB，避免跨进程读取依赖 Qt/OLE 的延迟呈现。
        # R154：成功路径的纯文字 toast 升级为缩略图通知卡（点击进编辑；保存/复制状态副文承担）；
        # 失败/异常路径仍走纯 toast。
        if not copy and (not path or not os.path.isfile(path)):
            self.toast("长截图保存失败，请检查存放位置后重试")
            return False
        _name = os.path.basename(path) if path else ""
        img = image if image is not None else None
        if copy:
            # 优先使用渲染成品，避免超长 PNG 重读触发 Qt 解码内存上限。
            if img is None and path:
                img = QImage(path)
            if img is None or img.isNull():
                self.toast("长截图图片读取失败，请重试")
                return False
            if not C.copy_image_to_clipboard(img):
                self.toast("复制失败，请重试对号；长截图已保留")
                return False
            _sub = "已复制到剪贴板"
        else:
            _sub = f"已保存 · {_name}"
        if img is None:
            img = QImage(path)
        if not img.isNull():
            self.notify_shot(QPixmap.fromImage(img), f"长截图完成 · {frames} 屏", _sub, img)
        else:
            self.toast(f"长截图完成 · {frames} 屏 · {_sub} · {path}")   # 图读取失败兜底纯文字
        self.long_bar = None
        return True

    # ---- 录屏 ----
    def rec(self, arg=""):
        """直接进入截图同款窗口定位/区域框选，选定后再显示录屏就绪工具条。"""
        self.pick_region(self.start_recorder, hotkey=(arg == "hotkey"))

    def start_recorder(self, sel, scr):
        """独立入口和截图工具条共用已选定的录屏区域；取消选区不打开录屏。"""
        if sel is None:
            return
        self.rec_bar = RecBar(on_done=self.rec_finished, on_error=self.rec_error,
                              sel=sel, scr=scr)

    def rec_finished(self, path, copied=False):
        self.rec_bar = None
        if copied:
            self.toast("录屏完成 · 已复制到剪贴板")
        else:
            self.toast(f"录屏完成 · 已保存 · {path}")

    def rec_error(self, msg):
        self.toast(f"录屏失败 · {msg}")

    # ---- 屏幕取色（R103） ----
    def colorpick(self, arg=""):
        """屏幕取色器：冻结屏+放大镜逐像素取色；左键复制 HEX，右键/Esc 取消。"""
        if self._cp_active:
            return
        self._cp_active = True
        self._cp_generation += 1
        self.panel.hide()
        self._cp_timer.start(160)

    def _open_colorpick(self):
        if not self._cp_active:
            return
        generation = self._cp_generation
        try:
            ovs = create_colorpick_overlays(
                lambda value: self.colorpick_finished(value, generation))
            if generation == self._cp_generation:
                self._cp_ovs = ovs
            else:
                close_all(ovs)
        except Exception as e:
            self.colorpick_finished("", generation)
            self.toast(f"屏幕取色失败 · {e}")

    def colorpick_finished(self, hexs, generation=None):
        if generation is not None and generation != self._cp_generation:
            return
        self._cp_timer.stop()
        self._cp_generation += 1
        self._cp_active = False
        ovs, self._cp_ovs = self._cp_ovs, None
        close_all(ovs)
        if hexs:
            QGuiApplication.clipboard().setText(hexs)
            self.toast(f"色值 {hexs} 已复制 · 直接粘贴即用")

    # ---- 钉图 ----
    def pin_pixmap(self, pm):
        win = PinnedWindow(pm)
        generation = object()
        self._pin_generation = generation
        self.pin_win = win
        win.setAttribute(Qt.WA_DeleteOnClose, True)

        def forget(_object=None):
            if self._pin_generation is generation:
                self.pin_win = None
                self._pin_generation = None

        win.destroyed.connect(forget)
        self.toast("已钉在桌面 · 双击关闭")

    # ---- 剪贴板 ----
    def clip(self, arg=""):
        # R123：空历史也照常开窗（窗口内 empty 空态提示），不再只弹 2.4s 即逝的 toast——
        # 用户反馈空历史时「点剪贴板像没反应」，窗口常开才符合面板入口预期
        if self._clip_win is not None and self._clip_win.isVisible():
            self._clip_win.raise_()            # 已开着：提到最前，不重复开窗
            self._clip_win.activateWindow()
            return
        self._clip_win = W.ClipHistoryWindow(
            self.clip_history, on_pin=self.pin_pixmap,
            on_edit_text=self._clip_edit_text, on_edit_img=self._clip_edit_img,
            on_delete=self._discard_clip_entry, on_send=self._send_exchange_entry)
        self._clip_win.show()                  # 回归：之前只建不 show＝点击无反应

    def _clip_edit_text(self, t):
        """文本条目编辑：送入快记顶部，可继续补充并随快记自动保存。"""
        self._notes = W.NotesWindow(seed=t)
        self._notes.show()
        self.toast("已在快记顶部插入，可继续编辑")

    def _clip_edit_img(self, img):
        """图片条目编辑：直接进长截图编辑器（同套标注工具），完成后结果回剪贴板；
        落盘走临时目录用完即删，不污染用户图片目录；结果图由轮询自动入历史顶部。
        R154：实现抽取为 _open_image_editor 与截图通知卡共用；
        topmost=True（R121）：历史窗常驻置顶，编辑窗同级置顶+提起才能压过它。"""
        self._open_image_editor(img, topmost=True)

    _USE_CB_SEQ = None               # R86：序列号守卫开关（None=惰性判定；冒烟可强制覆盖）

    def _clipboard_changed(self):
        """R86 轮询守卫：Win32 剪贴板序列号未变→False（不碰 OLE/不解码大图）。
        常驻期剪贴板里有截图/长截图大图是常态，原实现每 1.2s 全量取图+解码，是主线程周期卡顿主因。
        offscreen/非 win32 无系统板语义，恒 True 走原路径（冒烟兼容性）。"""
        if Controller._USE_CB_SEQ is None:
            Controller._USE_CB_SEQ = sys.platform == "win32" \
                and QGuiApplication.platformName() != "offscreen"
        if not Controller._USE_CB_SEQ:
            return True
        seq = ctypes.windll.user32.GetClipboardSequenceNumber()
        if seq == self._cb_seq:
            return False
        self._cb_seq = seq
        return True

    def _discard_clip_entry(self, entry):
        path = entry.get("v")
        if isinstance(path, str) and os.path.dirname(path) == self._clip_cache.name:
            try:
                os.remove(path)
            except OSError:
                pass

    def poll_clipboard(self):
        """只收图片（截图/长截图产物）；文本、链接、文件引用（GIF/录屏对号的 CF_HDROP）一律不入库。"""
        if not self._clipboard_changed():
            return                              # R86：剪贴板未变零成本早退（微秒级 Win32 调用）
        cb = QGuiApplication.clipboard()
        img = cb.image()                 # 只生成一份 QImage，避免先解码成整图 QPixmap
        if img.isNull():
            return
        self._clip_encoder.submit(img)

    @Slot(object)
    def _accept_clip_image(self, entry):
        if self._clip_shutdown:
            self._discard_clip_entry(entry)
            return
        value, fingerprint = entry["v"], entry["fingerprint"]
        top = self.clip_history[0] if self.clip_history else None
        if top and top["t"] == "img" and (
                (fingerprint is not None and top.get("fingerprint") == fingerprint)
                or (fingerprint is None and top["v"] == value)):
            if isinstance(value, str):
                self._discard_clip_entry({"v": value})
            return                              # 同图未变：不重复入库
        while sum(1 for x in self.clip_history if x["t"] == "img") >= 6:
            idx = max(i for i, x in enumerate(self.clip_history) if x["t"] == "img")
            self._discard_clip_entry(self.clip_history.pop(idx))
        e = entry
        self.clip_history.insert(0, e)
        for old in self.clip_history[10:]:
            self._discard_clip_entry(old)
        del self.clip_history[10:]             # 上限 10 条：只留最近的（图片另有 6 张保护）
        w = self._clip_win                      # 窗口开着：实时追加，无需重开
        if w is not None and w.isVisible():
            w.prepend_entry(e)

    # ---- 小组件 ----
    def todo(self):
        self._todo = W.TodoWindow()
        self._todo.show()

    def notes(self, arg=""):
        self._notes = W.NotesWindow()
        self._notes.show()

    def calendar(self, arg=""):
        self._cal = W.CalendarWindow()
        self._cal.show()

    def pomodoro(self, arg=""):
        self._pomo = W.PomodoroWindow(
            self.S, notify=lambda t, m: self.tray.showMessage(
                t, m, QSystemTrayIcon.Information, 2200))
        self._pomo.show()

    def _prewarm_fsearch(self):
        import fsearch
        fsearch.prewarm()

    def fsearch(self, arg=""):
        # Everything 式文件搜索：窗口秒开，索引后台构建（构建中即可查）；
        # 已开着就置前，避免双实例并发重建同一索引库。
        w = getattr(self, "_fswin", None)
        if w is not None and w.isVisible():
            w.raise_()
            w.activateWindow()
        else:
            import fsearch                       # R120 懒导入：顶层不再硬引，Lite 打包整体裁（sqlite3 随之排出）
            if w is not None:
                w.close()
            self._fswin = fsearch.FileSearchWindow()
            generation = object()
            self._fswin_generation = generation
            self._fswin.setAttribute(Qt.WA_DeleteOnClose, True)

            def forget(_object=None):
                if self._fswin_generation is generation:
                    self._fswin = None

            self._fswin.destroyed.connect(forget)
            self._fswin.show()
            w = self._fswin
        w.edit.setFocus(Qt.ShortcutFocusReason)
        if arg == "hotkey":
            w.edit.selectAll()

    def netspeed(self, arg=""):
        # 实例属性绝不能叫 self.netspeed——会遮蔽同名方法，getattr 拿到 None，
        # 面板点「网速」就只剩一个托盘错误气泡（连坑两轮的真凶）。
        w = self._netspeed_w
        if w is not None and w.isVisible():
            w.close()
            self._netspeed_w = None
        else:
            w = W.NetSpeedWidget()
            generation = object()
            self._netspeed_generation = generation
            self._netspeed_w = w
            w.setAttribute(Qt.WA_DeleteOnClose, True)

            def forget(_object=None):
                if self._netspeed_generation is generation:
                    self._netspeed_w = None
                    self._netspeed_generation = None

            w.destroyed.connect(forget)
            geo = QGuiApplication.primaryScreen().geometry()
            self._netspeed_w.move(geo.topRight().x() - self._netspeed_w.width() - 12,
                                  geo.top() + 60)
            self._netspeed_w.show()

    # ---- 系统 ----
    def lock(self, arg=""):
        EX.lock_screen()

    def showdesk(self, arg=""):
        EX.show_desktop()

    def theme(self, arg=""):
        self.S["theme"] = "light" if self.S.get("theme", "dark") == "dark" else "dark"
        C.save_settings(self.S)
        self.panel.retheme()
        self.toast("主题已切换 · " + self.S["theme"])

    # ---- 存放位置 ----
    def save_dir(self, arg=""):
        if getattr(self, "_choosing_save_dir", False):
            return
        self._choosing_save_dir = True
        self.panel.storage_error.hide()
        try:
            from storage_ui import StorageDialog
            import storage
            settings = C.load_settings()
            dialog = StorageDialog(settings, parent=self.panel)
            while dialog.exec() == QDialog.Accepted:
                values = dialog.values()
                current = {"save_dir": C.output_dir(False), "data_dir": C.data_dir(),
                           "receive_dir": C.transfers_dir(False)}
                moving = any(not storage.same_path(values[key], current[key]) for key in current)
                if moving:
                    if any(getattr(self, name, None) for name in
                           ("shot_overlays", "rec_bar", "gif_bar", "long_bar", "_long_editor")) \
                            or self._exchange_file_busy:
                        dialog.error.setText("请先结束当前截图、录制或文件传输，再更改位置。")
                        dialog.error.show()
                        continue
                    try:
                        # 未使用过的默认接收文件夹可延迟到本次设置时创建。
                        if storage.same_path(values["receive_dir"], current["receive_dir"]) \
                                and not settings.get("receive_dir"):
                            os.makedirs(values["receive_dir"], exist_ok=True)
                        storage.prepare_change(settings, values, C.historical_output_dirs(),
                                               current["receive_dir"])
                    except (OSError, ValueError) as error:
                        dialog.error.setText(str(error))
                        dialog.error.show()
                        continue
                    if self.tasks is not None:
                        self.tasks._load()
                        self.tasks.save()
                    # 迁移计划在退出前登记，但使用退出时最新设置，避免覆盖几何/提醒状态。
                    self._stop_phone_receiver()
                    if "fsearch" in sys.modules:
                        if not sys.modules["fsearch"].shutdown():
                            storage.cancel_change()
                            dialog.error.setText("索引正在结束，暂未切换存储位置。请稍后重试。")
                            dialog.error.show()
                            if self.S.get("phone_receive_enabled"):
                                self._restore_phone_receiver()
                            continue
                    def relaunch():
                        command = [sys.executable]
                        if not getattr(sys, "frozen", False):
                            command.append(os.path.abspath(__file__))
                        subprocess.Popen(command + ["--wait-parent", str(os.getpid())],
                                         env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    QApplication.instance().aboutToQuit.connect(relaunch)
                    QApplication.instance().quit()
                    return
                settings.update({k: v for k, v in values.items() if k != "data_dir"})
                C.save_settings(settings)
                self.S.update(settings)
                break
            self.panel.refresh_storage()
            if dialog.result() == QDialog.Accepted:
                self.toast("存储设置已保存")
        except OSError as e:
            self.panel.show_storage_error("设置未保存，请选择可写文件夹：" + str(e))
        finally:
            self._choosing_save_dir = False

    def open_save_dir(self, kind="pictures"):
        try:
            _, ready, paths = C.storage_locations()
            if kind == "root":                      # R116 存放条「打开」：已设置开落盘根，未设置开截图目录
                target = paths["pictures"]
            else:
                target = paths.get(kind, paths["pictures"])
            os.startfile(target)
            self.panel.refresh_storage()
        except OSError as e:
            self.panel.show_storage_error("无法打开保存目录：" + str(e))


# ---------------- 面板重皮 ----------------
def retheme(panel):
    panel.pal = C.palette(panel.S.get("theme", "dark"))
    panel.setStyleSheet(panel.qss())
    if hasattr(panel, "_render_storage_paths"):      # R116 存放条弱文本/失效红随主题重上
        panel._render_storage_paths()
    import panel as _PNM                             # R159：头部三钮双图标随主题重着色（防跨主题残色）
    for _hb in panel.findChildren(_PNM._HoverIconBtn):
        _hb.retint(panel.pal)


Panel.retheme = retheme


def tray_activate(panel, r):
    """托盘图标激活：单击/双击/中键一律呼出主面板——部分 Windows 版本会把
    单击（Trigger）吞进托盘飞出层、只把双击送到应用，两种都响应才是「点一下就开」。"""
    if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick, QSystemTrayIcon.MiddleClick):
        panel.popup()


def poll_hotkeys(ctl, panel):
    """按注册动作表分发热键，避免新增动作只注册却漏掉执行入口。"""
    try:
        while True:
            i = _hotkey_ctl["queue"].get_nowait()
            if _hotkey_ctl["paused"]:
                continue
            if not 1 <= i <= len(_HOTKEY_ACTIONS):
                continue
            key = _HOTKEY_ACTIONS[i - 1][0]
            if key == "panel":
                if panel.isVisible():
                    panel.hide()
                else:
                    panel.popup()
            else:
                ctl.dispatch({"longshot": "long", "note": "note_toggle"}.get(key, key),
                             "hotkey" if key in ("shot", "rec", "fsearch") else "")
    except queue.Empty:
        pass


def colorpick_regression(ctl):
    """R111：真实定时器、覆盖层和事件链；合成画面及内存剪贴板，不改用户数据。"""
    import capture as cap
    import shiboken6
    from types import SimpleNamespace
    from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF
    from PySide6.QtGui import QColor, QCursor, QKeyEvent, QMouseEvent

    app = QApplication.instance()

    class Events:
        # 发布包裁掉 QtTest：直接走 Qt 事件分发和真实事件循环，保持零新增运行依赖。
        @staticmethod
        def qWait(ms):
            loop = QEventLoop()
            QTimer.singleShot(ms, loop.quit)
            loop.exec()

        @staticmethod
        def keyClick(widget, key):
            for kind in (QEvent.KeyPress, QEvent.KeyRelease):
                app.sendEvent(widget, QKeyEvent(kind, key, Qt.NoModifier))

        @staticmethod
        def mouseMove(widget, pos):
            app.sendEvent(widget, QMouseEvent(QEvent.MouseMove, QPointF(pos),
                QPointF(widget.mapToGlobal(pos)), Qt.NoButton, Qt.NoButton, Qt.NoModifier))

        @staticmethod
        def mouseClick(widget, button, pos):
            for kind, buttons in ((QEvent.MouseButtonPress, button),
                                  (QEvent.MouseButtonRelease, Qt.NoButton)):
                if shiboken6.isValid(widget):
                    app.sendEvent(widget, QMouseEvent(kind, QPointF(pos),
                        QPointF(widget.mapToGlobal(pos)), button, buttons, Qt.NoModifier))
    saved = (cap.grab_screen, globals()["create_colorpick_overlays"],
             globals()["QGuiApplication"], _hotkey_ctl["queue"], ctl.toast)
    cursor = QCursor.pos()
    callbacks, copied, messages, passed = [], [], [], []
    outsider = QWidget()

    def frames(scr):
        g, d = scr.geometry(), scr.devicePixelRatio()
        pm = QPixmap(round(g.width()*d), round(g.height()*d))
        pm.fill(QColor("#203040"))
        return pm

    def factory(done):
        callbacks.append(done)
        return cap.create_colorpick_overlays(done)

    def visible():
        return [w for w in app.topLevelWidgets()
                if isinstance(w, cap.ColorPickerOverlay) and w.isVisible()]

    def opened():
        for _ in range(100):
            Events.qWait(10)
            if ctl._cp_ovs:
                break
        Events.qWait(150)
        assert ctl._cp_active and ctl._cp_ovs, "取色未保持打开"
        assert len(visible()) == len(app.screens()) == len(ctl._cp_ovs), "覆盖层重复或缺失"
        return ctl._cp_ovs

    def ended():
        Events.qWait(230)
        assert not ctl._cp_active and ctl._cp_ovs is None
        assert not ctl._cp_timer.isActive() and not visible(), "取消后仍有覆盖层或延迟启动"
        assert not app.findChildren(cap._ColorPickSession), "旧会话未销毁"

    try:
        cap.grab_screen = frames
        globals()["create_colorpick_overlays"] = factory
        globals()["QGuiApplication"] = SimpleNamespace(
            clipboard=lambda: SimpleNamespace(setText=copied.append))
        ctl.toast = messages.append
        _hotkey_ctl["queue"] = queue.Queue()
        routed = []
        spy = SimpleNamespace(dispatch=lambda method, arg: routed.append(method))
        fake_panel = SimpleNamespace(isVisible=lambda: False, popup=lambda: routed.append("panel"))
        for i in range(0, 8):
            _hotkey_ctl["queue"].put(i)
        poll_hotkeys(spy, fake_panel)
        assert routed == (["panel", "shot", "long"] if LITE else
                          ["panel", "shot", "long", "rec", "note_toggle", "colorpick"])
        assert _hotkey_ctl["queue"].empty()
        passed.append("热键全表路由与轻量版边界")

        if not LITE:
            _hotkey_ctl["queue"].put(6)
            poll_hotkeys(ctl, ctl.panel)
        else:
            ctl.colorpick()
        for _ in range(5):
            ctl.dispatch("colorpick", "")
        group = opened()
        assert len(callbacks) == 1, "等待期间重复启动"
        for ov in group:
            ov.activateWindow()
            ov.setFocus()
            Events.qWait(150)
            assert ctl._cp_ovs is group, "组内换屏误取消"
        for _ in range(5):
            ctl.colorpick()
        Events.keyClick(group[-1], Qt.Key_Escape)
        ended()
        assert len(callbacks) == 1
        passed.append("热键打开/待启动及活动期防重入/跨屏焦点/Esc一次关闭")

        ctl.colorpick()
        ctl.colorpick_finished("")
        ended()
        assert len(callbacks) == 1, "取消后延迟重新弹出"
        old = callbacks[0]
        ctl.colorpick()
        group = opened()
        old("#ff0000")
        assert ctl._cp_ovs is group and not copied, "旧回调覆盖新会话"
        Events.mouseClick(group[0], Qt.RightButton, pos=QPoint(40, 40))
        ended()
        passed.append("等待期取消/旧回调隔离/右键整组取消")

        ctl.colorpick()
        group = opened()
        Events.mouseMove(group[0], QPoint(40, 40))
        Events.mouseClick(group[0], Qt.LeftButton, pos=QPoint(40, 40))
        ended()
        assert copied == ["#203040"]
        for _ in range(3):
            ctl.colorpick()
            group = opened()
            group[-1].close()
            ended()
        assert copied == ["#203040"], "取消写入剪贴板"
        passed.append("左键只复制一次/外部关窗/连续三轮无残留")

        ctl.colorpick()
        opened()
        outsider.show()
        outsider.activateWindow()
        outsider.setFocus()
        ended()
        outsider.hide()
        passed.append("焦点移出整组自动取消")

        cap.grab_screen = lambda scr: QPixmap()
        ctl.colorpick()
        ended()
        assert messages[-1].startswith("屏幕取色失败")
        cap.grab_screen = frames
        ctl.colorpick()
        opened()
        ctl.colorpick_finished("")
        ended()
        passed.append("空帧失败可见/失败后恢复/控制器主动取消")

        class LateOverlay(QWidget):
            def close_silent(self):
                self.close()

        # 强制会话先删、覆盖层后删；不能让测试局部 group 引用掩盖原生析构顺序。
        disposed = []
        session = cap._ColorPickSession(disposed.append)
        late = LateOverlay()
        session.overlays.append(late)
        late.destroyed.connect(session.cancel)
        session.finish("")
        app.sendPostedEvents(session, QEvent.DeferredDelete)
        assert not shiboken6.isValid(session) and shiboken6.isValid(late)
        shiboken6.delete(late)
        assert disposed == [""] and not app.findChildren(cap._ColorPickSession)
        passed.append("会话先析构/覆盖层后析构无原生崩溃")
        return passed
    finally:
        ctl.colorpick_finished("")
        for session in app.findChildren(cap._ColorPickSession):
            session.cancel()
        outsider.close()
        Events.qWait(30)
        (cap.grab_screen, globals()["create_colorpick_overlays"],
         globals()["QGuiApplication"], _hotkey_ctl["queue"], ctl.toast) = saved
        QCursor.setPos(cursor)


def window_snap_regression():
    """R112：窗口层级、浏览器双区域与点击/拖动状态机；不写真实剪贴板。"""
    import capture as cap
    import shiboken6
    from PySide6.QtCore import QEvent, QPoint, QPointF, QRect
    from PySide6.QtGui import QMouseEvent, QKeyEvent, QPixmap, QColor
    app = QApplication.instance()
    passed, overlays = [], []

    def mouse(ov, kind, pos, button=Qt.NoButton, buttons=Qt.NoButton):
        app.sendEvent(ov, QMouseEvent(kind, QPointF(pos), QPointF(ov.mapToGlobal(pos)),
                                     button, buttons, Qt.NoModifier))

    def key(ov, value):
        app.sendEvent(ov, QKeyEvent(QEvent.KeyPress, value, Qt.NoModifier))

    def click(ov, pos, release=None):
        mouse(ov, QEvent.MouseButtonPress, pos, Qt.LeftButton, Qt.LeftButton)
        mouse(ov, QEvent.MouseButtonRelease, release or pos, Qt.LeftButton)

    def clean():
        cap.close_all(overlays)
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        overlays.clear()

    # 用独立预期公式验证负坐标、纵向副屏和不同缩放的本屏相对量。
    for x, y in ((0, 0), (-2560, 0), (3120, 0), (0, -1440), (-2560, -1440)):
        for scale in (1, 1.25, 1.5, 2):
            geo = QRect(x, y, 800, 600)
            outer = QRect(x+round(40*scale), y+round(40*scale), round(640*scale), round(480*scale))
            inner = QRect(x+round(40*scale), y+round(120*scale), round(640*scale), round(400*scale))
            windows = [{"hwnd": 1, "rect": outer, "contents": [inner]}]
            owner, opts = cap.snap_candidates(windows, geo, scale, QPoint(100, 160))
            assert owner == 1 and opts == [(QRect(40, 120, 640, 400), "网页内容"),
                                           (QRect(40, 40, 640, 480), "窗口")], (geo, scale, opts)
            assert cap.snap_candidates(windows, geo, scale, QPoint(100, 60))[1] == [opts[1]]
            popup = {"hwnd": 2, "rect": inner, "contents": []}
            assert cap.snap_candidates([popup]+windows, geo, scale, QPoint(100, 160))[0] == 2
            assert cap.snap_candidates(windows, geo, scale, QPoint(1, 1))[1] == [(QRect(0, 0, 800, 600), "屏幕")]
            clipped = {"hwnd": 3, "rect": QRect(x-100, y-100, round(1000*scale), round(800*scale)), "contents": []}
            assert cap.snap_candidates([clipped], geo, scale, QPoint(50, 50))[1] == [(QRect(0, 0, 800, 600), "窗口")]
    passed.append("20组缩放/正负原点/跨屏裁切/遮挡Z序/桌面回退")

    scr = app.primaryScreen()
    geo, scale = scr.geometry(), scr.devicePixelRatio()
    outer_local = QRect(40, 40, min(640, geo.width()-80), min(480, geo.height()-80))
    inner_local = outer_local.adjusted(0, 80, 0, 0)

    def physical(r):
        return QRect(geo.x()+round(r.x()*scale), geo.y()+round(r.y()*scale),
                     round(r.width()*scale), round(r.height()*scale))

    windows = [{"hwnd": 7, "rect": physical(outer_local), "contents": [physical(inner_local)]}]
    pos, chrome = inner_local.center(), QPoint(80, 60)

    def make(**kwargs):
        pm = QPixmap(round(geo.width()*scale), round(geo.height()*scale))
        pm.fill(QColor("#203040"))
        ov = cap.ShotOverlay(pm, screen=scr, snap_windows=windows, **kwargs)
        overlays.append(ov)
        return ov

    try:
        ov = make()
        mouse(ov, QEvent.MouseMove, pos)
        assert ov._snap_rect == inner_local and ov.sel is None and ov.toolbar is None
        key(ov, Qt.Key_Tab)
        assert ov._snap_rect == outer_local
        mouse(ov, QEvent.MouseMove, pos+QPoint(2, 1))
        assert ov._snap_rect == outer_local, "同一候选内移动不应丢掉Tab选择"
        key(ov, Qt.Key_Tab)
        assert ov._snap_rect == inner_local
        mouse(ov, QEvent.MouseMove, chrome)
        assert ov._snap_rect == outer_local and len(ov._snap_options) == 1
        mouse(ov, QEvent.MouseMove, pos)
        click(ov, pos, pos+QPoint(1, 1))
        assert ov.phase == "edit" and ov.sel == inner_local and ov.toolbar.isVisible()
        assert not ov.stamps and ov.stroke_pts is None
        pm = ov.composite_crop()
        assert pm.width() == round(inner_local.width()*scale) and pm.height() == round(inner_local.height()*scale)
        assert pm.toImage().pixelColor(0, 0) == QColor("#203040")
        clean()
        passed.append("浏览器内容/整窗悬停与Tab切换/轻微抖动单击确认/导出尺寸")

        ov = make()
        click(ov, chrome)
        assert ov.phase == "edit" and ov.sel == outer_local
        clean()
        for start, end, with_move in ((QPoint(0, 0), QPoint(100, 90), True),
                                      (QPoint(300, 280), QPoint(120, 140), True),
                                      (QPoint(100, 100), QPoint(260, 240), False)):
            ov = make()
            mouse(ov, QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton)
            if with_move:
                mouse(ov, QEvent.MouseMove, end, buttons=Qt.LeftButton)
                assert ov._select_dragging and ov.toolbar is None
            mouse(ov, QEvent.MouseButtonRelease, end, Qt.LeftButton)
            assert ov.phase == "edit" and ov.sel == QRect(start, end).normalized()
            clean()
        passed.append("单击整窗/原点起拖/反向框选/丢失move仍按release框选")

        ov = make()
        start, end = QPoint(100, 100), QPoint(250, 102)
        click(ov, start, end)
        assert ov.phase == "select" and ov.sel is None
        mouse(ov, QEvent.MouseMove, pos)
        assert ov._snap_rect == inner_local
        result = []
        ov.finished.connect(result.append)
        key(ov, Qt.Key_Escape)
        assert len(result) == 1 and not ov.isVisible()
        clean()
        for kwargs in ({"pick": True}, {"ocr": True}, {"scan": True}):
            ov = make(**kwargs)
            result = []
            ov.finished.connect(result.append)
            click(ov, pos)
            if kwargs.get("pick"):
                assert len(result) == 1 and result[0]["sel"] == inner_local and result[0]["screen"] is scr
            else:
                assert ov.phase == "edit" and ov.sel == inner_local
            clean()
        ov = make(pick=True)
        result = []
        ov.finished.connect(result.append)
        key(ov, Qt.Key_Return)
        assert result[0]["sel"] == ov.rect()
        clean()
        passed.append("过小拖框不误选窗口/Esc取消/录屏长截图选区/OCR扫码/回车整屏")

        old_grab, old_snapshot = cap.grab_screen, cap.snapshot_windows
        calls = []
        def fake_snapshot():
            calls.append("windows")
            return windows
        def fake_grab(screen):
            assert not any(isinstance(w, cap.ShotOverlay) and w.isVisible() for w in app.topLevelWidgets())
            calls.append("frame")
            g, d = screen.geometry(), screen.devicePixelRatio()
            pm = QPixmap(round(g.width()*d), round(g.height()*d))
            pm.fill(QColor("#203040"))
            return pm
        cap.snapshot_windows, cap.grab_screen = fake_snapshot, fake_grab
        try:
            result = []
            overlays.extend(cap.create_overlays(result.append))
            assert calls == ["windows"] + ["frame"]*len(app.screens())
            assert all(o._snap_windows is windows for o in overlays)
            winner = overlays[-1]
            click(winner, QPoint(80, 60))
            assert winner.phase == "edit"
            assert sum(shiboken6.isValid(o) and o.isVisible() for o in overlays) == 1
            key(winner, Qt.Key_Escape)
            assert len(result) == 1
        finally:
            cap.grab_screen, cap.snapshot_windows = old_grab, old_snapshot
            clean()
        passed.append("先探测及抓齐所有屏再显示/多屏共享快照/单击先到先得及整组取消")
    finally:
        clean()
    return passed


def launch_storage_regression():
    """R113：隔离目录、文件选择器与资源管理器；覆盖引导与单实例事件。"""
    import tempfile
    import uuid
    from types import SimpleNamespace
    from unittest.mock import patch
    from PySide6.QtCore import QEvent
    app = QApplication.instance()
    original = C.load_settings()
    passed = []
    panel = ctl = None
    try:
        with tempfile.TemporaryDirectory(prefix="_jj_launch_") as root:
            with patch.object(C, "_shell_folder", lambda _: root):
                C.save_settings({**original, "save_dir": ""})
                settings = C.load_settings()
                panel = Panel(settings)
                ctl = Controller(None, panel, settings)
                messages = []
                ctl.toast = messages.append
                panel.actionTriggered.connect(ctl.dispatch)
                show_launch_panel(panel, [])
                assert panel.isVisible() and panel.storage_card.isVisible()
                assert "先设置" in panel.storage_title.text()
                assert "默认目录" in panel.storage_value.text()
                assert all(root in p for p in panel._storage_paths.values())
                assert all(panel._storage_paths[k] in panel.storage_value.toolTip()
                           for k in (["pictures"] if LITE else ["pictures", "videos", "documents"]))
                assert panel.storage_value.toolTip().count("\n") == (0 if LITE else 2)   # R116 Lite 只见截图行
                panel.hide()
                show_launch_panel(panel, ["--background"])
                assert not panel.isVisible() and not panel.testAttribute(Qt.WA_DontShowOnScreen)
                show_launch_panel(panel, [])
                passed.append("手动启动显示面板/未设置引导及实际默认目录/后台启动不弹面板")

                scope = Controller.save_dir.__globals__
                with patch.dict(scope, QFileDialog=SimpleNamespace(getExistingDirectory=lambda *args: "")):
                    panel.storage_setup.click()
                assert panel.isVisible() and not C.load_settings()["save_dir"]
                assert "先设置" in panel.storage_title.text() and not ctl._choosing_save_dir
                chosen = os.path.join(root, "我的文件")
                os.makedirs(chosen)
                parents = []
                def choose(*args):
                    parents.append(args[0])
                    ctl.save_dir()
                    return chosen
                with patch.dict(scope, QFileDialog=SimpleNamespace(getExistingDirectory=choose)):
                    panel.storage_setup.click()
                assert parents == [panel] and C.load_settings()["save_dir"] == chosen
                assert settings["save_dir"] == chosen and not ctl._choosing_save_dir
                assert panel.storage_title.text() == "存放位置" and panel.storage_setup.text() == "更改位置"
                assert panel._storage_text == os.path.join(chosen, C.APP_NAME)   # R116 落盘根（模型值，视图可中间省略）
                assert all(chosen in p for p in panel._storage_paths.values()) and len(messages) == 1
                panel.hide()
                show_launch_panel(panel, [])
                assert "先设置" not in panel.storage_title.text()
                opened = []
                with patch.object(os, "startfile", opened.append):
                    for kind in panel._storage_paths:
                        ctl.open_save_dir(kind)
                    ctl.open_save_dir("root")
                assert opened == [panel._storage_paths[k] for k in panel._storage_paths] \
                    + [os.path.dirname(panel._storage_paths["pictures"])]      # R116 root＝落盘根目录
                passed.append("取消不丢引导/设置成功持久化/选择器防重入/再次打开不重复提示/目录入口")

                with patch.dict(scope, QFileDialog=SimpleNamespace(getExistingDirectory=lambda *args: root)):
                    with patch.object(C, "validate_save_base", side_effect=OSError("无写入权限")):
                        ctl.save_dir()
                    assert C.load_settings()["save_dir"] == chosen and panel.storage_error.isVisible()
                    assert "无写入权限" in panel.storage_error.text() and not ctl._choosing_save_dir
                    with patch.object(C, "save_settings", side_effect=OSError("配置写入失败")):
                        ctl.save_dir()
                    assert settings["save_dir"] == chosen and C.load_settings()["save_dir"] == chosen
                    before = C.load_settings()
                    with patch.object(os, "replace", side_effect=OSError("配置替换失败")):
                        ctl.save_dir()
                    assert C.load_settings() == before and settings["save_dir"] == chosen
                    assert "配置替换失败" in panel.storage_error.text()
                    assert not any(p.startswith("settings-") for p in os.listdir(C.data_dir()))
                C.save_settings({**C.load_settings(), "save_dir": os.path.join(root, "不存在")})
                panel.refresh_storage()
                assert "先设置" in panel.storage_title.text() and "不可用" in panel.storage_value.text()
                assert all(os.path.join(root, C.APP_NAME) in p for p in panel._storage_paths.values())
                for theme in ("dark", "light"):
                    settings["theme"] = theme
                    panel.retheme()
                    panel.resize(680, 640)
                    app.processEvents()
                    assert panel.storage_setup.isVisible() and 24 <= panel.storage_card.height() <= 44
                    assert panel.storage_setup.geometry().right() <= panel.storage_card.width()
                    assert panel.storage_value.width() > 100                  # R116 单行存放条：高 ≤44
                    long_root = os.path.join(chosen, "很长的保存根目录" * 30)
                    long_paths = {k: os.path.join(long_root, C.APP_NAME, k)
                                  for k in (["pictures"] if LITE else ["pictures", "videos", "documents"])}
                    with patch.object(C, "storage_locations", return_value=(long_root, True, long_paths)):
                        panel.refresh_storage()
                        app.processEvents()
                        panel._render_storage_paths()
                        assert all(p in panel.storage_value.toolTip() for p in long_paths.values())
                        assert "…" in panel.storage_value.text() \
                            and panel.storage_value.fontMetrics().horizontalAdvance(
                                panel.storage_value.text()) <= panel.storage_value.width()
                passed.append("不可写目录及配置失败保留旧值/原子替换失败回滚/失效回退/明暗主题长路径")

        name = "JianJie_Test_" + uuid.uuid4().hex
        handles = []
        try:
            first, second, lite = (LaunchInstance(name), LaunchInstance(name), LaunchInstance(name+"_Lite"))
            handles.extend((first, second, lite))
            assert first.primary and not second.primary and lite.primary
            assert not first.consume()
            second.kernel.SetEvent(second.event)
            assert first.consume() and not first.consume() and not lite.consume()
            second.kernel.SetEvent(second.event)
            second.close()
            assert first.consume(), "第二实例退出不能丢掉唤醒事件"
            first.close()
            replacement = LaunchInstance(name)
            handles.append(replacement)
            assert replacement.primary and not replacement.consume()
            passed.append("同版互斥/轻量全量隔离/重复启动事件只消费一次/退出释放句柄")
        finally:
            for handle in handles:
                handle.close()
        import winreg
        writes = []
        with patch.object(winreg, "OpenKey", return_value=object()), \
                patch.object(winreg, "SetValueEx", side_effect=lambda *args: writes.append(args[-1])), \
                patch.object(winreg, "CloseKey"):
            ok, error = EX.autostart(True)
            assert ok and not error and writes[0].endswith(" --background")
        passed.append("开机自启命令带后台参数，测试未修改注册表")
    finally:
        if panel:
            panel.hide()
            panel.deleteLater()
        if ctl:
            ctl.colorpick_finished("")
            ctl._toast.deleteLater()
            ctl.deleteLater()
        app.sendPostedEvents(None, QEvent.DeferredDelete)
        C.save_settings(original)
    return passed


# ---------------- Smoke 自检 ----------------
def smoke():
    # 环境隔离：数据目录整体指向临时目录——settings/todos/默认索引库全部写临时副本，
    # 不再污染用户真实 %APPDATA%\jianjie（历轮冒烟把用户 scheme 写成「自检」的教训）
    import tempfile as _tf0     # 函数后段另有 tempfile 局部导入：名字被占，只能用别名
    os.environ["APPDATA"] = _tf0.mkdtemp(prefix="_jj_appdata_")
    sys.argv += ["-platform", "offscreen"]
    app = QApplication.instance() or QApplication(sys.argv)
    C.apply_ui_font(app)                             # R130 全局字体栈（与真机同源，冒烟可锁）
    S = C.load_settings()
    tray = QSystemTrayIcon(C.app_qicon())
    panel = Panel(S)
    ctl = Controller(tray, panel, S)
    if ctl.tasks is not None:
        app.aboutToQuit.connect(
            lambda: ctl.tasks.save() if ctl.tasks._loaded else None)
    panel.actionTriggered.connect(ctl.dispatch)
    fails = []

    def check(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    # 1 动作表完整性：信号能路由（计数动态取自 ACTIONS，新增动作不破坏检查）
    _acts = __import__("panel").ACTIONS
    check(f"动作表 {len(_acts)} 项且方法全部可路由",
          all(hasattr(Controller, m) for _, _, _, _, _, _, m in _acts))
    # 1b R124：锁屏块归属系统组（G3），不在内置工具组（G1）
    import panel as _pn124
    check("R124 锁屏块在系统组",
          any(g == _pn124.G3 and m == "lock" for g, *_x, m in _acts)
          and not any(g == _pn124.G1 and m == "lock" for g, *_x, m in _acts))
    # 2 面板渲染
    panel.refresh()
    check(f"面板动作渲染（块状 {len(_acts)} 块）", len(panel.rows) == len(_acts) and panel.body_lay.count() >= 1)
    _hl = panel.body_lay.itemAt(0).widget().layout()
    _rows = {}
    for _i in range(_hl.count()):
        _w = _hl.itemAt(_i).widget()
        if _w:
            _rows.setdefault(_hl.getItemPosition(_i)[0], set()).add(type(_w).__name__)
    check("组标题独占一行", all(not ("QLabel" in v and "QFrame" in v) for v in _rows.values()))
    check("R97 面板无搜索框（动作一屏看全）", not hasattr(panel, "search"))
    # 2.5 托盘呼出：单击/双击都必须能打开主面板；首点慢根因＝首次 show 原生建窗，prewarm 屏外吸收
    tray.activated.connect(lambda r: tray_activate(panel, r))
    _t0 = time.perf_counter()
    tray_activate(panel, QSystemTrayIcon.Trigger)
    _t1 = panel.isVisible()
    _t_cold = (time.perf_counter() - _t0) * 1000
    panel.hide()
    tray_activate(panel, QSystemTrayIcon.DoubleClick)
    _t2 = panel.isVisible()
    panel.hide()
    panel.prewarm()
    _t0 = time.perf_counter()
    tray_activate(panel, QSystemTrayIcon.Trigger)
    _t3 = panel.isVisible()
    _t_warm = (time.perf_counter() - _t0) * 1000
    panel.hide()
    check("托盘单击/双击呼出主面板", _t1 and _t2 and _t3)
    check("面板启动预热（屏外 show+winId 后属性复位·不残留可见）",
          not panel.isVisible() and not panel.testAttribute(Qt.WA_DontShowOnScreen))
    # 2.5b 品牌图标同源：窗口图标（任务栏按钮取图源）与头部品牌标都指向程序化图标
    _lp = panel._logo.pixmap()
    check("面板窗口图标 + 头部品牌标同源（多尺寸 QIcon / 34px 头标）",
          not panel.windowIcon().isNull() and _lp is not None and not _lp.isNull())
    print(f"INFO 面板 popup 冷/热：{_t_cold:.0f}ms / {_t_warm:.0f}ms")
    # 2.6 主题切换原地生效：fire 对 theme 不得 hide（回归：点主题面板直接消失）
    _seen = []
    panel.actionTriggered.disconnect()
    panel.actionTriggered.connect(lambda m, a: _seen.append(m))
    panel.show()
    panel.fire("theme", "")
    _th = panel.isVisible() and _seen == ["theme"]
    panel.actionTriggered.disconnect()
    panel.actionTriggered.connect(ctl.dispatch)
    panel.hide()
    check("主题切换原地生效不关面板", _th)
    # 2.6b 网速走真实控制器路径（回归：__init__ 的 self.netspeed=None 遮蔽同名方法，点击只发托盘气泡）
    check(f"动作方法未被实例属性遮蔽（{len(_acts)} 项全部 callable）",
          all(callable(getattr(ctl, m, None)) for _, _, _, _, _, _, m in _acts))
    ctl.dispatch("netspeed", "")
    _nsw = ctl._netspeed_w
    _ns_open = isinstance(_nsw, W.NetSpeedWidget) and _nsw.isVisible()
    ctl.dispatch("netspeed", "")
    _ns_close = ctl._netspeed_w is None and not _nsw.isVisible()
    check("网速点击弹出/再点关闭（控制器真实路径）", _ns_open and _ns_close)
    # 2.6c 剪贴板历史只收图片（R68：文本/链接/文件引用一律不入库）；走真实路径
    QGuiApplication.clipboard().setText("clip-smoke-text")
    ctl.poll_clipboard()
    ctl.poll_clipboard()
    _okc1 = len(ctl.clip_history) == 0         # 文本不入库（重复轮询同样）
    from PySide6.QtGui import QColor, QImage   # 局部导入：同函数内 2.7 段也有同名导入，先用先绑
    _pm = QPixmap(64, 48)
    _pm.fill(QColor("#3b82f6"))
    QGuiApplication.clipboard().setPixmap(_pm)
    ctl.poll_clipboard()
    ctl.poll_clipboard()                       # 同图重复轮询：不堆重复项
    _okc1 = _okc1 and len(ctl.clip_history) == 1 and ctl.clip_history[0]["t"] == "img"
    _pm2 = QPixmap(64, 48)
    _pm2.fill(QColor("#e11d48"))
    QGuiApplication.clipboard().setPixmap(_pm2)
    ctl.poll_clipboard()
    _okc1 = _okc1 and len(ctl.clip_history) == 2
    for _i in range(8):                        # 图片内存保护：最多 6 张淘汰最旧
        _pmx = QPixmap(8, 8)
        _pmx.fill(QColor(20 * _i, 30, 40))
        ctl.clip_history.insert(0, {"t": "img", "v": _pmx.toImage(), "at": 0})
    _pm3 = QPixmap(64, 48)
    _pm3.fill(QColor("#f59e0b"))
    QGuiApplication.clipboard().setPixmap(_pm3)
    ctl.poll_clipboard()
    _okc1 = _okc1 and len(ctl.clip_history) == 6 \
        and all(e["t"] == "img" for e in ctl.clip_history)
    ctl.dispatch("clip", "")                   # 面板真实路径：建窗必须可见
    _cw = ctl._clip_win
    _okc2 = isinstance(_cw, W.ClipHistoryWindow) and _cw.isVisible() \
        and len(_cw.rows) == len(ctl.clip_history) \
        and _cw.rows[0].thumb is not None and not _cw.rows[0].thumb.pixmap().isNull()
    _pinned = []
    _cw.on_pin = lambda pm: _pinned.append(pm)
    _cw.rows[0].act_pin.click()                # 图片行：钉住回调
    _okc2 = _okc2 and len(_pinned) == 1 and not _pinned[0].isNull()
    _cw.rows[0].act_copy.click()               # 图片卡复制钮＝图回剪贴板（R158 复制钮回归按钮路径）
    _okc2 = _okc2 and not QGuiApplication.clipboard().image().isNull() \
        and "已复制图片" in _cw.hint.text()
    from PySide6.QtCore import QEvent as _QEvC
    _r0 = _cw.rows[0]                          # R156 卡片式：方卡+悬停浮钮常态隐藏（显名机制退役）
    _r0.leaveEvent(_QEvC(_QEvC.Type.Leave))    # 先归零：窗开在鼠标下会收合成 Enter（安全段同判例）
    _okc2 = _okc2 and _r0.width() == _r0.height() == 148 and not _r0.btns.isVisible()
    _ed_img = QImage(64, 48, QImage.Format_ARGB32)   # 图片编辑：直调长截图编辑器真实路径
    _ed_img.fill(QColor("#2ecc71"))
    import glob as _glob
    _tmpc_pre = set(_glob.glob(os.path.join(os.environ.get("TEMP", "."), "_jj_clipedit_*")))
    ctl._clip_edit_img(_ed_img)
    _ledc = getattr(ctl, "_long_editor", None)
    _okc2 = _okc2 and _ledc is not None and _ledc.isVisible() \
        and (_ledc.canvas._img_w, _ledc.canvas._img_h) == (64, 48)
    _ledc.finish()                             # 完成：临时目录落盘→回剪贴板→临时目录清理
    _okc2 = _okc2 and not _ledc.isVisible() \
        and not QGuiApplication.clipboard().image().isNull() \
        and QGuiApplication.clipboard().image().width() == 64 \
        and set(_glob.glob(os.path.join(os.environ.get("TEMP", "."), "_jj_clipedit_*"))) <= _tmpc_pre
    # ^ 只锁本轮不新增残留：历史崩溃留下的 _jj_clipedit_ 目录归人工清理，不再把检查永久翻绿转红
    ctl.poll_clipboard()                       # 编辑结果图由轮询自动回收入历史顶部
    _n0 = len(ctl.clip_history)
    _gone = ctl.clip_history[0]
    _cw.rows[0].act_del.click()                # 删除图片行：历史与视图同步收缩
    _okc2 = _okc2 and len(ctl.clip_history) == _n0 - 1 and len(_cw.rows) == _n0 - 1 \
        and _gone not in ctl.clip_history
    _pm4 = QPixmap(32, 32)                     # 窗口开着：新图轮询实时追加到视图
    _pm4.fill(QColor("#8b5cf6"))
    QGuiApplication.clipboard().setPixmap(_pm4)
    ctl.poll_clipboard()
    _okc2 = _okc2 and len(_cw.rows) == len(ctl.clip_history) \
        and _cw.rows[0].entry is ctl.clip_history[0]
    QGuiApplication.clipboard().setText("clip-live-text")   # 窗口开着时文本也不入库
    ctl.poll_clipboard()
    _okc2 = _okc2 and all(e["t"] == "img" for e in ctl.clip_history) \
        and len(_cw.rows) == len(ctl.clip_history)
    _cw.close()
    ctl._clip_win = None
    check("剪贴板历史只收图片（文本不入库·6张内存保护·钉/编/删/复制·方卡网格悬停浮钮·编辑直调长图编辑器）",
          _okc1 and _okc2)
    # 2.6e R123：空历史点剪贴板也照常开窗（窗口内 empty 空态提示），不再只 toast 早退
    ctl.clip_history.clear()
    ctl.dispatch("clip", "")
    _cw2 = ctl._clip_win
    _ok123 = isinstance(_cw2, W.ClipHistoryWindow) and _cw2.isVisible() \
        and not _cw2.rows and _cw2.empty.isVisible()
    _cw2.close()
    ctl._clip_win = None
    check("R123 剪贴板空历史照常开窗（窗口内空态提示）", _ok123)
    # 2.6d R86 流畅性：剪贴板轮询序列号守卫 + 长图底图缓存（滚动/hover 重绘纯 blit）
    _g86 = Controller._USE_CB_SEQ
    Controller._USE_CB_SEQ = True                # 强制打开守卫：offscreen 冒烟也覆盖真实早退分支
    _c86 = Controller.__new__(Controller)
    _c86._cb_seq = None
    _r86a = _c86._clipboard_changed()            # 首查：无记录→视为变化
    _r86b = _c86._clipboard_changed()            # 紧接再查：系统号未变→早退
    _c86._cb_seq = -1                            # 模拟系统号已变（不动用户真实剪贴板）
    _r86c = _c86._clipboard_changed()
    Controller._USE_CB_SEQ = _g86
    check("R86 剪贴板轮询零成本早退（首查收/未变退/号变收）", _r86a and not _r86b and _r86c)
    import numpy as _np86
    from longedit import LongCanvas as _LC86    # 段内导入：顶层是 from 导入，无 longedit 模块名
    _cv86 = _LC86(_np86.zeros((300, 200, 3), _np86.uint8), 800)
    _cv86.repaint()                              # 同步走一遍新 paint 路径（blit 底图 + save/restore）
    app.processEvents()
    check("R86 长图底图缓存（paint 纯 blit·缓存尺寸=画布尺寸）",
          not _cv86._base_pm.isNull()
          and _cv86._base_pm.width() == _cv86.width()
          and _cv86._base_pm.height() == _cv86.height())
    _cv86.close()
    # 2.7 日历自绘单元格：休/班角标随数字成组、选中蓝底，渲染春节页验证
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QDate
    cw = W.CalendarWindow()
    _off = sum(1 for v in cw.cal._marks.values() if v[1])
    _ban = sum(1 for v in cw.cal._marks.values() if not v[1])
    cw.cal.setCurrentPage(2026, 2)
    _img = QImage(640, 720, QImage.Format_ARGB32)
    _img.fill(QColor("#181a24"))
    cw.cal.render(_img)
    _badge = any(_img.pixelColor(x, y) == QColor("#0891b2")
                 for x in range(0, 640, 2) for y in range(0, 720, 2))
    cw.cal.setSelectedDate(QDate(2026, 2, 16))     # 春节日：整格自绘实心蓝底
    _img2 = QImage(640, 720, QImage.Format_ARGB32)
    _img2.fill(QColor("#181a24"))
    cw.cal.render(_img2)
    _selblue = sum(1 for x in range(0, 640, 2) for y in range(0, 720, 2)
                   if _img2.pixelColor(x, y) == QColor("#3b82f6")) >= 8
    check("日历自绘格（选中蓝底/角标随数字成组）+ 休班角标（红休≥30 青班≥6 春节页青角标）",
          _off >= 30 and _ban >= 6 and _badge and _selblue)
    # R51 全年逐日对账：每条节假日数据渲出对应颜色角标（休红/班青），普通日无角标
    from PySide6.QtGui import QPainter as _QP51

    def _cell_has_badge(y, m, d):
        _im = QImage(90, 90, QImage.Format_ARGB32)
        _im.fill(QColor("#181a24"))
        _pp = _QP51(_im)
        cw.cal.paintCell(_pp, QRect(0, 0, 90, 90), QDate(y, m, d))
        _pp.end()
        _red = _cy = False
        for _x in range(0, 90, 2):
            for _yy in range(0, 90, 2):
                _pc = _im.pixelColor(_x, _yy)
                if _pc.red() > 200 and _pc.green() < 110 and _pc.blue() < 110:
                    _red = True
                elif _pc.red() < 40 and 120 <= _pc.green() <= 170 and 160 <= _pc.blue() <= 210:
                    _cy = True
        return _red, _cy

    _okhd = True
    for _ds, _nm, _of in C.HOLIDAYS_ALL:
        _y, _m, _d = (int(v) for v in _ds.split("-"))
        _red, _cy = _cell_has_badge(_y, _m, _d)
        _okhd = _okhd and (_red if _of else _cy)
    for _dy in ((2026, 8, 15), (2026, 11, 11), (2026, 12, 25), (2026, 7, 20),
                (2025, 8, 15), (2025, 12, 25)):          # 两年真空月普通日都无角标
        _red, _cy = _cell_has_badge(*_dy)
        _okhd = _okhd and not _red and not _cy
    check("日历两年节假日逐日角标（2025+2026 共 72 条休/班逐格对账·普通日无角标）", _okhd)
    # R67 跨年补全：HOL_MAP 跨年命中 + 无官方数据年份明说「暂未公布」
    _ok67 = len(C.HOLIDAYS_2025) == 33 and len(C.HOLIDAYS_2026) == 39 \
        and C.HOL_MAP["2025-01-28"][1] == "春节" and C.HOL_MAP["2025-10-06"][1] == "中秋节" \
        and C.HOL_MAP["2026-02-17"][1] == "春节"
    cw._sync_page(2027, 1)
    _ok67 = _ok67 and "暂未公布" in cw.info.text()
    cw._sync_page(2026, 9)                               # 还原页面，免污染后续用例
    check("日历跨年节假日（2025 补录 33 条·2027 未公布明示）", _ok67)
    # R44 导航紧凑化：今天钮宽 ≥ sizeHint 不裁字（46 时“今天”被切）；R148 行高 26→30 呼吸重排（§7.2 同步改锁）
    _th = cw.today_btn.sizeHint()
    cw.layout().activate()                         # 未 show 窗口先激活布局再量几何
    check("日历导航紧凑（今天钮不裁字·行高 30）",
          cw.today_btn.width() >= _th.width() and cw.today_btn.height() == 30
          and cw.page_lbl.height() == 30)
    # R66 导航矢量箭头：字符 «‹›» 在雅黑回退字形下细弱不可见 → chevron 图标钮
    import icons as _ic66                          # 局部别名：防函数内同名绑定闭包坑
    _navic = [b for b in cw.findChildren(QPushButton) if not b.icon().isNull()]

    def _chev_cx(kind):
        _img = _ic66.pixmap(kind, "#ffffff", 20, dpr=1.0).toImage()
        _xs, _n = 0, 0
        for _yy in range(_img.height()):
            for _xx in range(_img.width()):
                _cc = QColor(_img.pixel(_xx, _yy))
                if _cc.red() + _cc.green() + _cc.blue() > 384:   # 亮度阈值：offscreen 下 alpha 恒 255 不可信
                    _xs += _xx
                    _n += 1
        return (_xs / _n if _n else 0), _n

    _cxl, _nl = _chev_cx("chevl")
    _cxr, _nr = _chev_cx("chevr")
    _cxl2, _nl2 = _chev_cx("chevl2")
    _cxr2, _nr2 = _chev_cx("chevr2")
    check("日历导航矢量箭头（四钮图标化·左右镜像·双尖笔画多于单尖）",
          len(_navic) == 4
          and abs(_cxl + _cxr - 19) < 1.0 and abs(_cxl2 + _cxr2 - 19) < 1.0   # 关于网格中心 9.5 镜像
          and abs(_nl - _nr) <= 2 and abs(_nl2 - _nr2) <= 2                  # 像素量镜像一致
          and _nl2 > _nl * 1.8 and _nr2 > _nr * 1.8                          # 双尖笔画近两倍
          and all(n > 20 for n in (_nl, _nr, _nl2, _nr2)))                   # 非空渲染
    # R90 无边框半透壳（全局壳语言首个推广实例：半透明+无系统标题栏+徽章头+拖动+钉切换）
    from PySide6.QtCore import QPoint as _QPt90, QPointF as _QPtF90, QEvent as _QEv90
    from PySide6.QtGui import QMouseEvent as _QMEv90
    _ok90 = cw.testAttribute(Qt.WA_TranslucentBackground) \
        and bool(cw.windowFlags() & Qt.FramelessWindowHint) \
        and not bool(cw.windowFlags() & Qt.WindowStaysOnTopHint) \
        and cw.body.objectName() == "calBody" and cw.title.text() == "日历" \
        and not cw.btn_pin.isChecked() and not cw.btn_pin.icon().isNull() and cw._pinned is False
    _p90 = cw.pos()
    _gp90 = cw.mapToGlobal(_QPt90(10, 10))
    cw.eventFilter(cw.head, _QMEv90(_QEv90.MouseButtonPress, _QPtF90(10, 10), _QPtF90(_gp90),
                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    cw.eventFilter(cw.head, _QMEv90(_QEv90.MouseMove, _QPtF90(45, 36),
                                    _QPtF90(_gp90 + _QPt90(35, 26)),
                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    _ok90 = _ok90 and (cw.pos() - _p90) == _QPt90(35, 26)
    cw.eventFilter(cw.head, _QMEv90(_QEv90.MouseButtonRelease, _QPtF90(45, 36),
                                    _QPtF90(_gp90 + _QPt90(35, 26)),
                                    Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    _hwnd90 = int(cw.winId())                              # R150 无闪硬证据基线：句柄 id
    cw.toggle_pin()                                        # 钉：Win32 换 Z 序（原 setWindowFlag+show 重建句柄必闪）
    _ok90 = _ok90 and cw._pinned is True and cw.btn_pin.isChecked() \
        and int(cw.winId()) == _hwnd90                                # 句柄未重建＝无闪的技术本质
    cw.toggle_pin()                                        # 取消钉（R150 起默认不钉，此为复位）
    _ok90 = _ok90 and cw._pinned is False and not cw.btn_pin.isChecked() \
        and int(cw.winId()) == _hwnd90 \
        and not bool(cw.windowFlags() & Qt.WindowStaysOnTopHint)      # flags 全程无 StaysOnTop（钉态走 _pinned）
    check("R90 日历无边框半透壳（壳语言/徽章标题栏/拖动/钉切换·R150默认不钉+无闪）", _ok90)
    # R148 日历顶部呼吸重排（用户点名：导航行样式/导航↔星期头/星期头自身间距全别扭）：
    # 导航行 26→30、页码 15→16px、nav↔星期头 +8 呼吸、星期头行 26→32、窗高 710→728、日期行 87 不动。
    # 几何预算：24(root)+18(body边)+34(head)+6+30(nav)+14(6+8)+554(32+6×87)+6+20(info)+6+16(leg)=728
    from PySide6.QtWidgets import QTableView as _QTV148
    _vh148 = cw.cal.findChild(_QTV148, "qt_calendar_calendarview").verticalHeader()
    _gap148 = cw.cal.geometry().top() - (cw.today_btn.geometry().top() + cw.today_btn.height())
    # 坑：exe（PyInstaller 冻结）里 widgets 模块 getsource 拿不到源码（capture 等模块可以，机制未明）——
    # OSError 中断冒烟；锁断言一律用运行时实例证据（几何/属性/styleSheet 串），双环境恒真
    check("R148 日历顶部呼吸（窗高728/表头32/日期行87/导航↔星期头14/页码16px/壳透底）",
          cw.height() == 728
          and _vh148.sectionSize(0) == 32
          and _vh148.sectionSize(1) == 87
          and _gap148 == 14
          and not cw.cal.autoFillBackground()      # cal 壳 palette 填白底坑：代码级关（R95/R146 同判例）
          and "font-size:16px" in cw.page_lbl.styleSheet())
    # R149 弹窗叉全局同尺寸（用户三点）：①sticky 叉 20→16（同墨量≠同视感——x 实心双斜线视觉重量压过镂空
    # pin，真机裁决显大；16 下 x bbox 8.9 ≈ pin14 bbox 宽 9.3 同宽协调）；②日历 18→16 与 sticky 一致；
    # ③CardDialog/五窗壳/网速全 16（网速字符×补 lucide 化，R137 漏网）；钉 14 全线不动；
    # 主面板头叉 18 不动——其补偿对象是同排饱满图标（R137 防离群），非钉叉对比战场
    from PySide6.QtWidgets import QToolButton as _QTB149
    _cd149 = W.CardDialog("历", "t")
    _ns149 = W.NetSpeedWidget()
    _todo149 = W.TodoWindow()
    _cdx149 = [b for b in _cd149.findChildren(_QTB149) if b.toolTip() == "关闭（Esc）"]
    _nsx149 = [b for b in _ns149.findChildren(QPushButton) if "关闭悬浮网速" in b.toolTip()]
    _hd149 = sorted(b.iconSize().width() for b in _todo149.findChildren(_QTB149) if b.objectName() == "headBtn")
    check("R149 弹窗叉全局16（日历/对话/五窗壳/网速lucide化）+钉14不动",
          cw.btn_close.iconSize().width() == 16 and cw.btn_pin.iconSize().width() == 14
          and _cdx149 and _cdx149[0].iconSize().width() == 16
          and _hd149 == [14, 16]
          and _nsx149 and _nsx149[0].text() == "" and not _nsx149[0].icon().isNull()
          and _nsx149[0].iconSize().width() == 16)
    _cd149.close(); _cd149.deleteLater()
    _ns149.close(); _ns149.deleteLater()
    _todo149.close(); _todo149.deleteLater()
    # R150（用户两点）：①点钉闪窗根治——setWindowFlag(StaysOnTop) 销毁重建句柄（隐→显即闪），
    # 改 C.set_topmost（Win32 SetWindowPos 直改 Z 序，SWP_NOSIZE/NOMOVE/NOACTIVATE/NOSHOW）句柄不动；
    # 钉态真相源从 windowFlags 转 _pinned 实例属性（flags 全程无 StaysOnTop）。②带钉窗口默认不钉——
    # 日历/五窗壳原默认钉退役（sticky 本就默认不钉；日历侧断言在 R90 锁、sticky 侧在 R88 锁）。
    _todo150 = W.TodoWindow()
    _pin150 = [b for b in _todo150.findChildren(_QTB149) if b.objectName() == "headBtn"][0]
    _hw150 = int(_todo150.winId())                            # 无闪硬证据基线：句柄 id
    _ok150 = (hasattr(C, "set_topmost")
              and _todo150._pinned is False and not _pin150.isChecked()          # 默认不钉
              and not bool(_todo150.windowFlags() & Qt.WindowStaysOnTopHint))
    _pin150.click()                                           # toggled → _toggle_pin：Win32 换 Z 序
    _ok150 = _ok150 and _todo150._pinned is True and _pin150.isChecked() \
        and int(_todo150.winId()) == _hw150                            # 句柄未重建＝无闪的技术本质
    _pin150.click()                                           # 取消钉复位
    _ok150 = _ok150 and _todo150._pinned is False and int(_todo150.winId()) == _hw150 \
        and not bool(_todo150.windowFlags() & Qt.WindowStaysOnTopHint)
    _todo150.close(); _todo150.deleteLater()
    check("R150 五窗壳默认不钉+点钉无闪（句柄不重建·_pinned 真相源）", _ok150)
    cw.close()
    # 2.8 番茄钟真实节拍：完成→自动休息，第 4 个进长休，托盘通知，计数落盘
    _toasts = []
    pw = W.PomodoroWindow({"pomodoro_work": 25, "pomodoro_rest": 5},  # 独立字典+no-op 存盘，不污染真设置
                          notify=lambda t, m: _toasts.append(t), save=lambda: None)
    _base = pw._done_today                     # 今日计数已持久化，以基线为准保持可重复
    pw.toggle()
    _okp = pw.state == "work" and pw.left == pw.work * 60
    pw.left = 1
    pw.tick()
    _okp = _okp and pw.state == "rest" and pw._done_today == _base + 1 \
        and pw.left in (pw.rest * 60, pw.long_rest * 60) and len(_toasts) == 1
    pw.left = 1
    pw.tick()
    _okp = _okp and pw.state == "work" and pw._done_today == _base + 1
    pw._done_today = 3                         # 强制下一拍凑满第 4 个 → 必进长休
    pw.left = 1
    pw.tick()
    _okp = _okp and pw._done_today == 4 and pw._long_rest_now \
        and pw.left == pw.long_rest * 60
    check("番茄钟真实节拍（完成→休息→第4个长休→托盘通知）", _okp)
    pw.close()
    if not LITE:                      # R120：Lite 裁 fsearch（sqlite3 同排），文件搜索锁整体跳过
        # 2.9 文件搜索（Everything 式）：USN 记录解析回环 + 假索引多关键词 AND 即搜 + 父链路径解析
        import fsearch                 # R120 懒导入：本块与 Lite 互斥
        import tempfile as _tfs
        _rec = fsearch.usn_record_v2_bytes(102, 101, 0x20, "notepad.exe")
        _pr = fsearch.parse_usn_record(_rec, 0)
        check("USN_RECORD_V2 解析回环（60 字节头/名字/isdir/长度）",
              _pr == (102, 101, "notepad.exe", False, len(_rec)))
        _fs_db = os.path.join(_tfs.gettempdir(), "_jj_smoke_fs.db")
        fs = fsearch.FileSearchWindow(auto_index=False, db=_fs_db)   # 不起真索引，避免拖慢冒烟
        app.processEvents()                        # 消化 _post_show（构造零磁盘，状态下一拍填充）
        _oksty = fs.edit.isClearButtonEnabled() \
            and "QListWidget::item:hover" in fs.styleSheet() \
            and "QMenu::item:selected" in fs.styleSheet() \
            and "QProgressBar::chunk" in fs.styleSheet() \
            and "QLineEdit#srch:focus" in fs.styleSheet()   # 新增：重点输入框/进度条样式已挂上

        def _fs_wait(win, timeout=3.0):            # 异步查询：等 loading 行被真实结果替换
            _t_end = time.time() + timeout
            while time.time() < _t_end:
                app.processEvents()
                if win.listw.count() and "搜索中" not in win.listw.item(0).text():
                    return True
                time.sleep(0.02)
            return False

        fs._seed([("C:", 100, 5, "Windows", "windows", 1, None),
                  ("C:", 101, 100, "system32", "system32", 1, None),
                  ("C:", 102, 101, "notepad.exe", "notepad.exe", 0, None),
                  ("D:", 200, 5, "projects", "projects", 1, None),
                  ("D:", 201, 200, "notes.txt", "notes.txt", 0, None)])
        fs.edit.setText("NOTE TXT")
        fs._debounce.stop()
        fs._run_query()
        _okfs = _fs_wait(fs) and fs.listw.count() == 1 and "notes.txt" in fs.listw.item(0).text() \
            and "\n" in fs.listw.item(0).text() \
            and fs.listw.item(0).data(Qt.UserRole) == "D:\\projects\\notes.txt"
        fs.edit.setText("")
        fs._run_query()
        _okfs = _okfs and fs.listw.count() == 1 \
            and fs.listw.item(0).data(Qt.UserRole) is None   # 空查询=提示项（无路径数据）
        fs.edit.setText("projects")
        fs._run_query()
        _okfs = _okfs and _fs_wait(fs) and fs.listw.count() == 1 \
            and fs.listw.item(0).foreground().color().name().lower() == "#93c5fd"   # 文件夹淡蓝
        fs.edit.setText("")
        fs._run_query()
        check("文件搜索多关键词 AND 即搜 + USN 父链路径解析 + 文件夹淡蓝标识", _okfs)
        check("搜索窗口样式（一键清除/悬停选中/右键菜单/重点输入框+进度条）", _oksty)
        # 2.9d 查询异步化：loading 行可见 + 慢查询结果被新查询代次作废（连击不闪旧结果）
        _qdb_orig = fsearch.FileSearchWindow._query_db

        def _slow_qdb(self, tokens):
            if "aaa" in tokens:
                time.sleep(0.5)                     # 固定慢查询：让旧代次结果后于新结果到达
            return _qdb_orig(self, tokens)

        fsearch.FileSearchWindow._query_db = _slow_qdb
        try:
            fs.edit.setText("aaa")
            fs._debounce.stop()
            fs._run_query()
            _okld = fs.listw.count() == 1 and "搜索中" in fs.listw.item(0).text()   # loading 行
            fs.edit.setText("projects")
            fs._debounce.stop()
            fs._run_query()                         # 新查询抢先发出：aaa 慢结果到达时必须被丢弃
            _okld = _okld and _fs_wait(fs) and "projects" in fs.listw.item(0).text()
            _t_aaa = time.time() + 1.5              # 等 aaa 慢线程完成并发信号（0.5s 假慢）
            while time.time() < _t_aaa:
                app.processEvents()
                time.sleep(0.02)
            _okld = _okld and fs.listw.count() == 1 \
                and "projects" in fs.listw.item(0).text()   # 过期结果未覆盖新结果
        finally:
            fsearch.FileSearchWindow._query_db = _qdb_orig
        check("搜索异步化：loading 行 + 过期查询结果按代次作废（主线程零 SQL）", _okld)
        # 2.9e R77 监控落库重放风暴治理：静默刷新不闪 loading + 间隔/在途双闸门
        fs.edit.setText("notes")
        fs._debounce.stop()
        fs._run_query()
        _oke = _fs_wait(fs) and "notes.txt" in fs.listw.item(0).text()
        _g0 = fs._qgen
        fsearch._DIRTY["n"] += 1                            # 模拟监控落库
        fs._poll_worker()
        _oke = _oke and fs._qgen == _g0 + 1                 # 间隔外：放一次静默重放
        _oke = _oke and "搜索中" not in fs.listw.item(0).text()   # silent：结果页不被 loading 覆盖
        fsearch._DIRTY["n"] += 1
        fs._poll_worker()
        _oke = _oke and fs._qgen == _g0 + 1                 # 间隔闸门：1.5s 内不再放
        _oke = _oke and _fs_wait(fs)                        # 静默重放的结果正常到达
        fs._last_replay -= 10                               # 强制过期间隔闸门，单测在途闸门

        def _slow_qdb2(self, tokens):
            time.sleep(0.5)
            return _qdb_orig(self, tokens)

        fsearch.FileSearchWindow._query_db = _slow_qdb2
        try:
            fs._run_query()                                 # 发在途慢查询（_qgen→_g0+2，_qinflight=1）
            fsearch._DIRTY["n"] += 1
            fs._poll_worker()
            _oke = _oke and fs._qgen == _g0 + 2             # 在途闸门：慢查询没回来，落库也不再放
            _t_e = time.time() + 2
            while time.time() < _t_e:                       # 等慢线程完成销账
                app.processEvents()
                time.sleep(0.02)
        finally:
            fsearch.FileSearchWindow._query_db = _qdb_orig
        _oke = _oke and fs._qinflight == 0                  # 在途计数归零（每次 emit 都销账）
        check("R77 监控落库重放风暴治理（静默不闪loading+间隔/在途双闸门）", _oke)
        # 2.9b 实时索引：持久化路径 + meta 位标 + USN 增量落库（新建/改名/删除）+ 目录改名平移
        import sqlite3 as _sq3
        import struct as _st
        # 2.9c 启动预热：新鲜跳全量监控接管 + worker 单例复用 + watcher 置 _built
        _fs_db2 = os.path.join(_tfs.gettempdir(), "_jj_smoke_fs2.db")
        for _sfx2 in ("", "-wal", "-shm"):
            try:
                os.remove(_fs_db2 + _sfx2)
            except OSError:
                pass
        _fc = _sq3.connect(_fs_db2)
        fsearch._ensure_schema(_fc)
        _okf = fsearch._index_fresh(_fc) is False                    # 无 built_at＝不新鲜
        fsearch._meta_set(_fc, "built_at", int(time.time()))
        _fc.commit()
        _okf = _okf and fsearch._index_fresh(_fc) is True            # 刚建＝新鲜
        fsearch._meta_set(_fc, "built_at", int(time.time()) - fsearch.INDEX_TTL - 60)
        _fc.commit()
        _okf = _okf and fsearch._index_fresh(_fc) is False           # 超 TTL＝不新鲜
        fsearch._meta_set(_fc, "built_at", int(time.time()))         # 恢复新鲜供跳过路径用
        _fc.commit()
        _fc.close()
        _mons2 = []
        _odr, _oop, _omn = fsearch.fixed_drives, fsearch._open_volume, fsearch._start_monitor
        try:
            fsearch.fixed_drives = lambda: ["C:\\"]
            fsearch._open_volume = lambda d: None                    # 模拟非管理员
            fsearch._start_monitor = lambda v, mode, jid=None, usn=0: _mons2.append((v, mode))
            _wk2 = fsearch._IndexWorker(_fs_db2)
            _wk2.run()                                               # 同步驱动线程体
            _okw = _wk2.done and _wk2.error is None and _wk2.count == 0 \
                and _wk2.mode == "监控接管" and _mons2 == [("C:\\", "dir")]
            _okb0 = fsearch.FileSearchWindow._built
            fsearch.FileSearchWindow._built = True
            _oks = fsearch.ensure_worker(db=_fs_db2) is None         # 会话已建：不新建
            fsearch.FileSearchWindow._built = False
            _fs_db3 = _fs_db2 + ".slow"                             # 无 built_at＝不新鲜走全量
            _owr = fsearch._walk_rows
            fsearch._walk_rows = lambda con, v, tbl: (time.sleep(0.6), 0)[1]   # 假遍历防复用竞争
            try:
                _w1 = fsearch.ensure_worker(db=_fs_db3)
                _w2 = fsearch.ensure_worker(db=_fs_db3)
                _oks = _oks and _w1 is not None and _w1 is _w2 and _w1.is_alive()   # 在跑复用同一实例
                fsearch._prewarm_watch(_w1)                          # 同步调 watcher：完成置 _built
                _oks = _oks and _w1.done and _w1.error is None \
                    and fsearch.FileSearchWindow._built is True
            finally:
                fsearch._walk_rows = _owr
            fsearch.FileSearchWindow._built = _okb0                  # 还原现场
        finally:
            fsearch.fixed_drives, fsearch._open_volume, fsearch._start_monitor = _odr, _oop, _omn
        check("索引预热（新鲜跳全量监控接管·单例复用·watcher置会话已建）", _okf and _okw and _oks)
        check("索引库持久化在用户数据目录", os.path.dirname(fsearch.db_path()) == C.data_dir())
        check("管理员检测可用（bool 无异常）", isinstance(fsearch.is_admin(), bool))
        _fx = _sq3.connect(_fs_db)
        fsearch._ensure_schema(_fx)
        fsearch._meta_set(_fx, "jid:C:", 12345)
        _fx.commit()
        _okm = fsearch._meta_get(_fx, "jid:C:") == "12345"
        _rc = fsearch.parse_usn_record_ex(
            fsearch.usn_record_v2_bytes(9, 5, 0x20, "x.log", reason=fsearch.USN_FILE_CREATE), 0)
        _okm = _okm and _rc == (9, 5, "x.log", False, fsearch.USN_FILE_CREATE, 70)
        _buf = _st.pack("<Q", 777) \
            + fsearch.usn_record_v2_bytes(103, 101, 0x20, "calc.exe", reason=fsearch.USN_FILE_CREATE) \
            + fsearch.usn_record_v2_bytes(102, 101, 0x20, "notepad2.exe", reason=0x00002000)
        _mv, _nu = fsearch._apply_usn_records(_fx, "C:", _buf)
        _fx.commit()
        _okm = _okm and _mv == 2 and _nu == 777 \
            and _fx.execute("SELECT name, parent FROM files WHERE frn=103").fetchone() == ("calc.exe", 101) \
            and _fx.execute("SELECT name FROM files WHERE frn=102").fetchone() == ("notepad2.exe",)
        _buf = _st.pack("<Q", 888) \
            + fsearch.usn_record_v2_bytes(103, 101, 0, "calc.exe", reason=fsearch.USN_FILE_DELETE)
        fsearch._apply_usn_records(_fx, "C:", _buf)
        _fx.commit()
        _okm = _okm and _fx.execute("SELECT COUNT(*) FROM files WHERE frn=103").fetchone()[0] == 0
        _fx.execute("INSERT INTO files VALUES('C:',0,0,'docs','docs',1,'C:\\docs')")
        _fx.execute("INSERT INTO files VALUES('C:',0,0,'a.txt','a.txt',0,'C:\\docs\\a.txt')")
        _fx.commit()
        _okm = _okm and fsearch._apply_fni(_fx, "C:", fsearch.FNI_REN_NEW, "mydocs", "docs") \
            and _fx.execute("SELECT path FROM files WHERE name='mydocs'").fetchone() == ("C:\\mydocs",) \
            and _fx.execute("SELECT path FROM files WHERE name='a.txt'").fetchone() == ("C:\\mydocs\\a.txt",)
        _fx.close()
        check("实时索引：USN 位标/增量落库（新建+改名+删除）/目录改名平移", _okm)
        # 2.9b-2 写锁共存（R47 修复锁）：分批重建写者 × 高频监控落库并发，等锁不报 locked
        import threading as _thr
        _wdir = _tfs.mkdtemp(prefix="_jj_walk47_")
        for _fi in range(40):
            open(os.path.join(_wdir, "f_{}.txt".format(_fi)), "w").close()
        _stopf = []

        def _noisy():
            _nc = _sq3.connect(_fs_db, timeout=30)
            _nc.execute("PRAGMA busy_timeout=30000")
            _i = 0
            while not _stopf:
                fsearch._meta_set(_nc, "noise", str(_i))
                _nc.commit()
                _i += 1
                time.sleep(0.01)
            _nc.close()

        _nth = _thr.Thread(target=_noisy, daemon=True)
        _nth.start()
        _ob, fsearch._BATCH = fsearch._BATCH, 8     # 批调小：commit 频繁，竞争拉满
        _okl = True
        try:
            _cc = _sq3.connect(_fs_db, timeout=30)
            _cc.execute("PRAGMA busy_timeout=30000")
            _cc.execute("CREATE TEMP TABLE vol_tmp AS SELECT * FROM files WHERE 0")
            _cc.commit()
            _okl = fsearch._walk_rows(_cc, _wdir, "temp.vol_tmp") == 40
            _cc.execute("DROP TABLE temp.vol_tmp")
            _cc.commit()
            _cc.close()
        except Exception as _e:
            _okl = False
            print("INFO walk 并发异常：", _e)
        finally:
            fsearch._BATCH = _ob
            _stopf.append(1)
            _nth.join(3)
        import shutil as _sh47
        _sh47.rmtree(_wdir, ignore_errors=True)
        check("索引/监控写锁共存（分批重建撞高频落库·等锁不报 locked）", _okl)
        # 2.9c 上次索引时间 + 不定态进度条（假 worker 驱动 UI 状态机，不起真索引）
        _fx2 = _sq3.connect(_fs_db)
        fsearch._meta_set(_fx2, "built_at", time.time())
        fsearch._meta_set(_fx2, "count", 123456)
        _fx2.commit()
        _fx2.close()
        fs._refresh_meta()
        _okp6 = "上次索引" in fs.meta_lbl.text() and "123,456" in fs.meta_lbl.text() \
            and fs.bar.isHidden() and fs.refresh.isEnabled()
        class _FakeW:
            done = False
            error = None
            count = 1234
            vol = "C:"
            mode = "NTFS 直读"
            elapsed = 0.0
        fs._worker = _FakeW()
        fs._poll_worker()
        _okp6 = _okp6 and not fs.bar.isHidden() and not fs.refresh.isEnabled() \
            and fs.refresh.text() == "索引中…" and "1,234" in fs.status.text()
        fs._worker.done = True
        fs._poll_worker()
        _okp6 = _okp6 and fs.bar.isHidden() and fs.refresh.isEnabled() \
            and fs.refresh.text() == "重新索引" and "上次索引" in fs.meta_lbl.text()
        fs._worker = None
        check("搜索索引进度条+上次索引时间（不定态进度/完成收尾/meta 落库显示）", _okp6)
        fs.close()
        for _sfx in ("", "-wal", "-shm"):
            try:
                os.remove(_fs_db + _sfx)
            except OSError:
                pass
    # 3 截图逻辑（不出窗，直接取帧合成）
    try:
        pm = grab_screen()
        from PySide6.QtGui import QGuiApplication as _QGA
        _ps = _QGA.primaryScreen()
        lg = _ps.size()
        dpr = _ps.devicePixelRatio() or 1.0
        pw, ph = round(lg.width() * dpr), round(lg.height() * dpr)
        # 物理分辨率管线：帧不再降采样，尺寸应等于 逻辑×DPR
        check(f"屏幕取帧物理分辨率（{pm.width()}x{pm.height()}，逻辑 {lg.width()}x{lg.height()}，"
              f"DPR={pm.devicePixelRatio()}）",
              pm.width() == pw and pm.height() == ph and abs((pm.devicePixelRatio() or 1.0) - dpr) < 1e-6)
    except Exception as e:
        print(f"SKIP 屏幕取帧（offscreen 环境）：{e}")
    # 4 长截图拼接：确定性合成用例——frame = long 尾部 256 行 + 新内容 100 行
    import numpy as np
    from longshot import stitch_pair

    def _det_noise(h, w, seed):
        """R120 确定性伪噪声帧（替代 np.random.RandomState：random 子包排包后冒烟照常）。
        Knuth 乘法散列 + xor-shift 打散，行间/通道间零相关，NCC 唯一匹配性与 MT 噪声同效。"""
        import numpy as _n
        yy, xx = _n.mgrid[0:h, 0:w]
        v = (yy.astype(_n.uint32) * _n.uint32(2654435761)
             + xx.astype(_n.uint32) * _n.uint32(2246822519)
             + _n.uint32((seed * 3266489917) & 0xFFFFFFFF))
        v ^= v >> _n.uint32(15)
        v *= _n.uint32(2654435761)
        v ^= v >> _n.uint32(13)
        v3 = v[..., None] + _n.arange(3, dtype=_n.uint32) * _n.uint32(374761393)
        v3 *= _n.uint32(2654435761)
        v3 ^= v3 >> _n.uint32(13)
        return v3.astype(_n.uint8)

    long_arr = _det_noise(300, 200, 7)
    frame_arr = np.vstack([long_arr[-256:], _det_noise(100, 200, 99)])
    s4, e4, sc4, bh4 = stitch_pair(long_arr, frame_arr)
    check(f"长截图拼接识别 offset==256（实际 {s4}，score {sc4:.2f}）",
          s4 == 256 and e4 == 356 and bh4 == 0)
    # 真屏取帧
    try:
        from longshot import grab_array
        arr = grab_array()
        check("真屏取帧", arr.ndim == 3 and arr.shape[0] > 100)
    except Exception as e:
        print(f"SKIP 真屏取帧（offscreen 环境）：{e}")
    # 4.1 长截图右侧实时预览（微信式）：选区右侧停靠 / 整图缩略 / 超长图自适应 / 随条关闭
    try:
        from longshot import LongPreview as _LP
        _lp = _LP(QGuiApplication.primaryScreen(), QRect(40, 40, 400, 300))
        _okp = _lp.isVisible() and _lp.width() == _LP.PW
        _lp.set_arr(np.zeros((600, 300, 3), dtype=np.uint8))
        _okp = _okp and _lp._pm is not None and _lp._pm.height() <= _lp.height() - 12
        _lp.set_arr(np.zeros((30000, 300, 3), dtype=np.uint8))   # 超长图必须整体装进面板
        _okp = _okp and _lp._pm.height() <= _lp.height() - 12
        _lp.close()
        _okp = _okp and not _lp.isVisible()
        check("长截图右侧实时预览（微信式停靠/整图缩略/超长自适应/随条关闭）", _okp)
    except Exception as e:
        print(f"SKIP 长截图预览（offscreen 环境）：{e}")
    # 5 空白文档生成
    import tempfile                            # os 不局部导入：会遮蔽全局 os，前面引用直接 UnboundLocal（真踩过）
    tmp = tempfile.gettempdir()
    p1 = EX.new_blank_docx(tmp)
    p2 = EX.new_blank_xlsx(tmp)
    check("docx 生成", os.path.isfile(p1) and os.path.getsize(p1) > 400)
    check("xlsx 生成", os.path.isfile(p2) and os.path.getsize(p2) > 700)
    os.remove(p1); os.remove(p2)
    # 6 PowerShell
    good, out = EX.run_powershell("2+3")
    check("PowerShell 执行", good and "5" in out)
    # 7 设置与待办持久化
    S["scheme"] = "自检"
    C.save_settings(S)
    check("设置持久化", C.load_settings()["scheme"] == "自检")
    # 7.2 存放位置设置：三组目录跟随 save_dir（测完恢复原值，污染安全）
    try:
        _prev_dir = C.load_settings().get("save_dir", "")
        _td = tempfile.mkdtemp(prefix="_jj_savedir_")
        C.save_settings({**C.load_settings(), "save_dir": _td})
        _pd, _vd, _dd = C.pictures_dir(), C.videos_dir(), C.documents_dir()
        _ok_dir = (_pd == os.path.join(_td, C.APP_NAME, "截图")
                   and _vd == os.path.join(_td, C.APP_NAME, "录屏")
                   and _dd == os.path.join(_td, C.APP_NAME, "新建")
                   and os.path.isdir(_pd) and os.path.isdir(_vd) and os.path.isdir(_dd))
        C.save_settings({**C.load_settings(), "save_dir": _prev_dir})
        _ok_restore = (C.save_base_dir() == (_prev_dir if _prev_dir and os.path.isdir(_prev_dir) else None))
        check(f"存放位置设置生效并恢复（{os.path.join(_td, C.APP_NAME)}）", _ok_dir and _ok_restore)
    except Exception as e:
        print(f"SKIP 存放位置：{e}")
    todos = C.load_todos()
    C.save_todos(todos)
    check("待办持久化", isinstance(C.load_todos(), list) and len(C.load_todos()) >= 1)
    # 8 主题
    check("双主题调色板", len(C.palette("dark")) == len(C.palette("light")))
    # 9 网速计数器
    ns = W.NetSpeedWidget()
    ns.refresh()
    check("网速小组件刷新", ns.up.text().startswith("↑"))
    # 8.5 截图工具条图标化：全部按钮为矢量图标（含新增 high/ocr/qr/gif），DPR 非空
    from capture import ShotOverlay as _SO      # 后段也有局部导入，统一局部避免作用域坑
    _ovt = _SO(QPixmap(400, 300), screen=QGuiApplication.primaryScreen())
    _ovt.sel = QRect(0, 0, 400, 300)
    _ovt.show_toolbar()
    check("截图工具条图标化（9 工具全矢量图标非空）",
          _ovt.toolbar is not None and
          all(not b.icon().pixmap(20, 20).isNull() for b in _ovt.tool_btns.values()))
    _ovt.close()
    # 9.5 网速真实计数：回环拉 4MB 后下行速率必须动起来（不泵事件防 _prev 被中途刷新）
    import http.server
    import threading
    import time as _time
    import urllib.request
    _buf = b"x" * (1024 * 1024)

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(_buf) * 4))
            self.end_headers()
            for _ in range(4):
                self.wfile.write(_buf)

        def log_message(self, *a):
            pass

    _srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    ns2 = W.NetSpeedWidget()
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{_srv.server_port}/d", timeout=5).read()
    except Exception:
        pass
    _time.sleep(1.05)
    ns2.refresh()
    _moved = "0 B/s" not in ns2.dn.text()
    if _moved:
        check("网速真实计数（回环 4MB 拉流后下行速率变化）", True)
    else:
        print("SKIP 网速真实计数（本机回环流量未计入 net_io_counters）")
    _srv.shutdown()
    ns2.close()
    # 9.6 测速：核心回环真收发 + 浮条按钮全流程（类级 URL 换回环，不打真网）
    try:
        import threading as _th
        from http.server import BaseHTTPRequestHandler as _BH, ThreadingHTTPServer as _THS

        class _STH(_BH):
            protocol_version = "HTTP/1.1"     # SpeedTest 用 keep-alive 连接，服务器必须同版本支持

            def log_message(self, *a):
                pass

            def do_GET(self):
                n = 0
                if "?" in self.path:
                    for kv in self.path.split("?", 1)[1].split("&"):
                        if kv.startswith("bytes="):
                            try:
                                n = int(kv[6:])
                            except ValueError:
                                n = 0
                self.send_response(200)
                self.send_header("Content-Length", str(n))
                self.end_headers()
                chunk = b"\0" * 65536
                while n > 0:
                    c = min(65536, n)
                    self.wfile.write(chunk[:c])
                    n -= c

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                got = 0
                while got < n:
                    b2 = self.rfile.read(min(65536, n - got))
                    if not b2:
                        break
                    got += len(b2)
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

        _ths = _THS(("127.0.0.1", 0), _STH)
        _sport = _ths.server_address[1]
        _th2 = _th.Thread(target=_ths.serve_forever, daemon=True)
        _th2.start()
        _stt = W.SpeedTest(down_url=f"http://127.0.0.1:{_sport}/__down?bytes={{n}}",
                           up_url=f"http://127.0.0.1:{_sport}/__up")
        _ms = _stt.ping()
        _gb, _gd, _gm = _stt.download(2 * 1024 * 1024)
        _ub, _ud, _um = _stt.upload(512 * 1024)
        check("测速核心：回环 ping/下载/上传真收发（字节足额·B/s 速率>0）",
              _gb == 2 * 1024 * 1024 and _gm > 0 and _ub == 512 * 1024 and _um > 0 and _ms >= 0)
        # 浮条按钮全流程：类级 URL 换成回环（真点击不打真网），轮询到收尾
        _ns3 = W.NetSpeedWidget()
        _od, _ou = W.SpeedTest.DOWN_URL, W.SpeedTest.UP_URL
        try:
            W.SpeedTest.DOWN_URL = f"http://127.0.0.1:{_sport}/__down?bytes={{n}}"
            W.SpeedTest.UP_URL = f"http://127.0.0.1:{_sport}/__up"
            _ns3.test_btn.click()
            _t0 = time.time()
            while time.time() - _t0 < 20 and getattr(_ns3, "_testing", False):
                app.processEvents()
                time.sleep(0.02)
            # done 态取样上传行：↑ 开头 + B/s 字节单位 + 与下载同权亮色（R93 对齐基准主文本 #e5e7eb）
            res2_up_ok = (_ns3.res2.text().startswith("↑") and "B/s" in _ns3.res2.text()
                          and "e5e7eb" in _ns3.res2.styleSheet())
            _okui = (not _ns3._testing and "Ping" in _ns3.res1.text()
                     and "B/s" in _ns3.res1.text() and res2_up_ok
                     and _ns3.test_btn.isEnabled() and _ns3.test_btn.text() == "测速")
        finally:
            W.SpeedTest.DOWN_URL, W.SpeedTest.UP_URL = _od, _ou
        check("网速浮条测速全流程（按钮→进行中→Ping/↓/↑ 回填·字节单位·上传同权）", _okui)
        # 失败态分阶段回显 + 测速钮紧凑主色样式（协调浮条字号层级）+ UA 浏览器化防间歇 403
        _ns3._st = {"phase": "error", "msg": "下载失败 · RuntimeError: HTTP 403"}
        _ns3._poll_test()
        _okns = "测速失败" in _ns3.res1.text() and "下载失败" in _ns3.res2.text() \
            and "QPushButton#ns_test" in _ns3.styleSheet() \
            and "QLabel#ns_title" in _ns3.styleSheet() \
            and W.SpeedTest.UA.startswith("Mozilla/5.0 (Windows NT")
        check("网速浮条失败态分阶段回显+测速钮样式协调+UA 浏览器化", _okns)
        _ns3.close()
        _ths.shutdown()
    except Exception as e:
        print(f"SKIP 测速：{e}")
    # R93 网速浮条基准壳（半透保留/卡片阴影/徽章标题栏/Esc）
    try:
        from PySide6.QtGui import QKeyEvent as _QKEv93
        from PySide6.QtCore import QEvent as _QEv93
        from PySide6.QtWidgets import QLabel as _QLb93
        _ns93 = W.NetSpeedWidget()
        _ok93 = _ns93.testAttribute(Qt.WA_TranslucentBackground) \
            and bool(_ns93.windowFlags() & Qt.FramelessWindowHint) \
            and bool(_ns93.windowFlags() & Qt.WindowStaysOnTopHint) \
            and _ns93.body.objectName() == "nsBody" \
            and _ns93.findChild(_QLb93, "ns_title") is not None \
            and _ns93.test_btn.objectName() == "ns_test"
        _ns93.show()
        _ns93.keyPressEvent(_QKEv93(_QEv93.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        _ok93 = _ok93 and not _ns93.isVisible()                      # Esc 即关
        _ns93.close()
        check("R93 网速浮条基准壳（半透卡片/徽章标题栏/Esc 关闭）", _ok93)
    except Exception as e:
        print(f"SKIP R93 网速壳：{e}")
    # 10 录屏管线：真实 mss 取帧 + MF H.264 编码（R120）1 秒后落盘
    from recorder import RecBar
    done = {}
    rb = RecBar(on_done=lambda p, c=False: done.update(path=p, copied=c),
                on_error=lambda m: done.update(err=m))
    check("录屏初始（开始录制·暂停/下载/对号隐藏）",
          rb.b1.toolTip() == "开始录制" and not rb.bp.isVisibleTo(rb)
          and not rb.bstop.isVisibleTo(rb) and not rb.bok.isVisibleTo(rb))
    rb.start()
    if rb.writer:
        t0 = time.time()
        while time.time() - t0 < 1.2:
            app.processEvents()
            time.sleep(0.03)
        _nf = rb.frames
        _layr = rb.layout()
        check("录屏开始后（开始钮隐藏·暂停/下载/对号亮相·对号最右）",
              not rb.b1.isVisibleTo(rb) and rb.bp.isVisibleTo(rb) and rb.bstop.isVisibleTo(rb)
              and rb.bok.isVisibleTo(rb)
              and 0 <= _layr.indexOf(rb.bp) < _layr.indexOf(rb.bstop) < _layr.indexOf(rb.bok))
        rb.bstop.click()                               # ⬇ 下载钮：停止+纯保存不复制
        ok = bool(done.get("path")) and os.path.isfile(done["path"]) and os.path.getsize(done["path"]) > 500 \
            and done.get("copied") is False                # ■ 路径：存盘不复制
        check(f"录屏管线 1s 真拍真编码（{os.path.basename(done.get('path', ''))}）", ok)
        check(f"录屏 30fps 节拍（1.2s 实写 {_nf} 帧）", _nf >= 24)
    elif done.get("err"):
        print(f"SKIP 录屏管线（无屏幕资源）：{done['err']}")
    else:
        check("录屏管线", False)
    # R120 补：STA 线程 COM 纪律锁——offscreen 平台不初始化 COM，真机 Qt UI 线程已是 STA；
    # 手工把本线程置 STA 复现真机条件，_co_init 必须容忍 RPC_E_CHANGED_MODE 且不偷拆引用，
    # 编码器在 STA 下照常创建（真机报障「无法创建 H.264 编码器」的回归锁）。
    try:
        import ctypes as _ct
        import shutil as _sh
        import numpy as _npco
        import recorder_mf as _rcmf
        _ole = _ct.windll.ole32
        _hr = _ole.CoInitializeEx(None, 0x2)              # COINIT_APARTMENTTHREADED ≡ 真机 UI 线程
        _owned_sta = _hr in (0, 1)
        _okco = _rcmf._co_init() is False                 # 他人 STA：不持有引用
        _pco = os.path.join(tempfile.mkdtemp(prefix="jj_costa_"), "t.mp4")
        _wco = _rcmf.MfH264Writer(_pco, 320, 240, 30)
        _wco.write_bgra(_npco.zeros((240, 320, 4), _npco.uint8))
        _wco.close()
        _okco = _okco and os.path.getsize(_pco) > 500
        _r = _ole.CoInitializeEx(None, 0)                 # 仍 CHANGED_MODE＝我们的 STA 引用没被偷拆
        _okco = _okco and ((_r & 0xFFFFFFFF) == 0x80010106)
        if _owned_sta:
            _ole.CoUninitialize()                         # 只卸自己那一层，恢复线程无 COM 初态
        _sh.rmtree(os.path.dirname(_pco), ignore_errors=True)
        check("录屏 STA 线程 COM 纪律（_co_init 容忍 CHANGED_MODE·不偷拆引用）", _okco)
    except ModuleNotFoundError as _emnf:
        if LITE and "recorder_mf" in str(_emnf):     # spec 明裁 recorder_mf 仅录屏用：轻量版无此模块属设计态
            print("SKIP 录屏 STA 线程 COM 纪律（轻量版不打包 recorder_mf）")
        else:
            check(f"录屏 STA 线程 COM 纪律（{_emnf!r}）", False)
    except Exception as _eco:
        check(f"录屏 STA 线程 COM 纪律（{_eco!r}）", False)
    # R121-A 聚光并集统一黑度（双侧）+ 长图双击文字保原字号/颜色（用户报障三件套之两）
    try:
        from capture import ShotOverlay as _SO121
        from PySide6.QtGui import (QPixmap as _QPm121, QColor as _QC121, QPainter as _QP121,
                                   QGuiApplication as _QGA121, QFont as _QF121,
                                   QFontMetrics as _QFM121)
        _ovp121 = _QPm121(400, 300); _ovp121.fill(Qt.black)
        _ov121 = _SO121(_ovp121, screen=_QGA121.primaryScreen())
        _ov121.phase = "edit"; _ov121.sel = QRect(0, 0, 400, 300)
        _ov121.stamps.append({"kind": "high", "r": QRect(100, 100, 121, 81), "shape": "rect", "dim": 148})
        _ov121.stamps.append({"kind": "high", "r": QRect(180, 120, 121, 81), "shape": "rect", "dim": 148})
        _ov121._rerender_anno()                          # 新渲染路径直驱：异常不吞（R75 纪律）
        _pm121 = _QPm121(400, 300); _pm121.fill(_QC121("#ffffff"))
        _pt121 = _QP121(_pm121); _pt121.setRenderHint(_QP121.Antialiasing, True)
        _ov121._paint_high_dim(_pt121, _ov121.stamps)
        for _s121 in _ov121.stamps:
            if _s121["kind"] == "high":
                _ov121._draw_high_border(_pt121, _s121["r"], _s121.get("shape", "rect"))
        _pt121.end()
        _im121 = _pm121.toImage()
        _ok121a = _im121.pixelColor(130, 110).red() > 240      # A 独有区：不被 B 盖黑
        _ok121a = _ok121a and _im121.pixelColor(200, 150).red() > 240   # 重叠区：真减法不反黑
        _ok121a = _ok121a and _im121.pixelColor(280, 190).red() > 240   # B 独有区：可见
        _od121 = _im121.pixelColor(20, 20).red()
        _ok121a = _ok121a and 85 < _od121 < 140                # 区外单层 148（二层叠加≈53）
        _bd121 = _im121.pixelColor(130, 100)
        _ok121a = _ok121a and _bd121.red() > 180 and _bd121.blue() < 100   # 琥珀框在压暗层上
        _ov121.close(); _ov121.deleteLater()
        check("R121 截图聚光并集统一黑度（区域互见不盖/重叠不反黑/区外单层不叠加）", _ok121a)
        import longedit as _LE121
        import numpy as _np121
        from PySide6.QtCore import QPoint as _QPt121, QEvent as _QEv121
        from PySide6.QtGui import QMouseEvent as _QME121
        _w121 = _np121.full((300, 400, 3), 255, _np121.uint8)  # 白底：压暗/挖洞可判
        _lc121 = _LE121.LongCanvas(_w121, 448)                  # k=(448-48)/400=1.0，事件坐标=图坐标
        _lc121.stamps.append({"kind": "high", "r": QRect(100, 100, 121, 81), "shape": "rect", "dim": 148})
        _lc121.stamps.append({"kind": "high", "r": QRect(180, 120, 121, 81), "shape": "rect", "dim": 148})
        _il121 = _lc121.render_final()
        _ok121b = _il121.pixelColor(130, 110).red() > 240 and _il121.pixelColor(200, 150).red() > 240 \
            and _il121.pixelColor(280, 190).red() > 240
        _od121b = _il121.pixelColor(20, 20).red()
        _ok121b = _ok121b and 85 < _od121b < 140
        check("R121 长图聚光并集统一黑度（导出同规则：区域互见/区外单层）", _ok121b)
        # B2：双击文字再编辑＝原字号/颜色（此前长图侧丢成当前工具值，与截图侧不一致）
        del _lc121.stamps[:]
        _fm121 = _QFM121(_QF121("Microsoft YaHei UI", 20, _QF121.Bold))
        _lc121.stamps.append({"kind": "text", "lines": ["原样"], "color": "#112233", "fs": 20,
                              "asc": _fm121.ascent(), "lh": _fm121.lineSpacing(),
                              "r": QRect(50, 220, _fm121.horizontalAdvance("原样") + 2,
                                         _fm121.lineSpacing() + 2)})
        _lc121.cur_color, _lc121.cur_size = "#ef4444", 7        # 当前工具值与对象不同：保真才可分辨
        _lc121.mouseDoubleClickEvent(_QME121(_QEv121.Type.MouseButtonDblClick, _QPt121(60, 230),
                                             _QPt121(60, 230), Qt.LeftButton, Qt.LeftButton,
                                             Qt.NoModifier))
        _te121 = _lc121.text_editor
        _ok121c = _te121 is not None and _te121._fs_img == 20 and _te121._color == "#112233"
        _te121.setPlainText("改后")
        _lc121.commit_text_editor()
        _ok121c = _ok121c and _lc121.stamps[0]["fs"] == 20 \
            and _lc121.stamps[0]["color"] == "#112233" and _lc121.stamps[0]["lines"] == ["改后"]
        _lc121.close(); _lc121.deleteLater()
        check("R121 长图双击文字再编辑保原字号/颜色（对齐截图侧）", _ok121c)
    except Exception as _e121a:
        check(f"R121 聚光并集/文字保真（{_e121a!r}）", False)
    # R121-B 三条弹出抢前台（Esc 可达）+ 剪贴板历史编辑窗置顶
    try:
        import common as _C121
        from recorder import RecBar as _RB121
        from longshot import LongShotBar as _LS121
        from gifrec import GifBar as _GB121
        from PySide6.QtGui import QGuiApplication as _QGA121b
        import longedit as _LE121b
        import numpy as _np121b
        # 离屏无法断言真前台，改锁接线：三条构造时必须调 force_foreground（真机报障根因＝
        # activateWindow 受 Windows 前台锁拦截，热键后台触发的条拿不到焦点，Esc 无处可去）
        _ff121 = []
        _orig_ff121 = _C121.force_foreground
        _C121.force_foreground = lambda w: _ff121.append(w)
        try:
            _rb121 = _RB121(on_done=lambda *a: None, on_error=lambda *a: None)
            _ok121d = _rb121 in _ff121
            _rb121.close()
            _ls121 = _LS121(on_finish=lambda *a: None, on_cancel=lambda *a: None,
                            region=None, screen=_QGA121b.primaryScreen())
            _ok121d = _ok121d and _ls121 in _ff121
            _ls121.cancel()                              # 立即取消：停拼接定时器
            _gb121 = _GB121(None, _QGA121b.primaryScreen(), on_done=lambda *a: None,
                            on_cancel=lambda *a: None)
            _ok121d = _ok121d and _gb121 in _ff121
            _gb121.cancel()
        finally:
            _C121.force_foreground = _orig_ff121
        check("R121 录屏/长截图/GIF 条弹出抢前台（Esc 可达）", _ok121d)
        _ea121 = _np121b.zeros((120, 200, 3), _np121b.uint8)
        _ed121a = _LE121b.LongEditor(_ea121, frames=0, on_done=None, on_cancel=None)
        _ok121e = not bool(_ed121a.windowFlags() & Qt.WindowStaysOnTopHint)   # 默认不置顶
        _ed121a.close()
        _ed121b = _LE121b.LongEditor(_ea121, frames=0, on_done=None, on_cancel=None, topmost=True)
        _ok121e = _ok121e and bool(_ed121b.windowFlags() & Qt.WindowStaysOnTopHint)
        _ed121b.close()
        check("R121 长图编辑器 topmost（默认不置顶·剪贴板编辑路径置顶）", _ok121e)
    except Exception as _e121b:
        check(f"R121 三条抢前台/编辑窗置顶（{_e121b!r}）", False)
    # R122 长图文本输入中点空白＝落旧字+新位置直接开输入框（与截图侧同语义：
    # FocusOut 同步提交→press 里 had_edit=False→同一击走到 text 分支开新输入。
    # R81 的 singleShot(0) 延迟提交曾让此击只落字——行为分叉，B2 用户要求统一回摆）
    try:
        import longedit as _LE122
        import numpy as _np122
        from PySide6.QtCore import QPoint as _QPt122, QEvent as _QEv122
        from PySide6.QtGui import QMouseEvent as _QME122
        _w122 = _np122.full((300, 400, 3), 255, _np122.uint8)
        _lc122 = _LE122.LongCanvas(_w122, 448)
        _lc122.cur_tool = "text"
        _lc122.show_text_editor(_QPt122(30, 30))
        _te122 = _lc122.text_editor
        _ok122f = _te122 is not None
        _te122.setPlainText("落字")
        _lc122.eventFilter(_te122, _QEv122(_QEv122.Type.FocusOut))   # 必须同步提交
        _ok122f = _ok122f and _lc122.text_editor is None \
            and sum(1 for s in _lc122.stamps if s["kind"] == "text") == 1  # singleShot 回归＝仍挂着→FAIL
        _lc122.mousePressEvent(_QME122(_QEv122.Type.MouseButtonPress, _QPt122(150, 120),
                                       _QPt122(150, 120), Qt.LeftButton, Qt.LeftButton,
                                       Qt.NoModifier))
        _ok122f = _ok122f and _lc122.text_editor is not None         # 同一击直接开新输入框
        _lc122.commit_text_editor()
        _lc122.close(); _lc122.deleteLater()
        check("R122 长图文本输入中点空白＝落旧字+新位置直接开输入（与截图侧同语义）", _ok122f)
    except Exception as _e122:
        check(f"R122 长图文本点空白连锁（{_e122!r}）", False)
    def _clipboard_file():
        # 使用接收方同款解析器；直接读 UTF-16 字节会漏掉 DROPFILES.fWide 错位。
        import ctypes as ct
        from ctypes import wintypes as wt
        u32, shell = ct.windll.user32, ct.windll.shell32
        u32.OpenClipboard.argtypes = [wt.HWND]
        u32.GetClipboardData.argtypes = [wt.UINT]
        u32.GetClipboardData.restype = wt.HANDLE
        shell.DragQueryFileW.argtypes = [wt.HANDLE, wt.UINT, wt.LPWSTR, wt.UINT]
        shell.DragQueryFileW.restype = wt.UINT
        if not u32.OpenClipboard(None):
            return ""
        try:
            hd = u32.GetClipboardData(15)
            if not hd or shell.DragQueryFileW(hd, 0xFFFFFFFF, None, 0) != 1:
                return ""
            size = shell.DragQueryFileW(hd, 0, None, 0) + 1
            buf = ct.create_unicode_buffer(size)
            shell.DragQueryFileW(hd, 0, buf, size)
            return buf.value
        finally:
            u32.CloseClipboard()

    # 10b 录屏对号复制：停止+保存+CF_HDROP 入剪贴板（Win32 直读回验，同 GIF 模式）
    try:
        _done2 = {}
        _rb3 = RecBar(on_done=lambda p, c=False: _done2.update(path=p, copied=c),
                      on_error=lambda m: _done2.update(err=m))
        _rb3.start()
        if _rb3.writer:
            _t0 = time.time()
            while time.time() - _t0 < 0.8:
                app.processEvents()
                time.sleep(0.03)
            _rb3.bok.click()                           # 对号：停止+保存+复制
            _hdrop_r = _clipboard_file()
            check("录屏对号复制（停止+保存·CF_HDROP 入剪贴板指向视频）",
                  _done2.get("copied") is True and _hdrop_r == _done2.get("path", "-")
                  and _hdrop_r.lower().endswith((".mp4", ".avi")) and os.path.isfile(_done2.get("path", "-")))
            if _done2.get("path"):
                try:
                    os.remove(_done2["path"])
                except OSError:
                    pass
        elif _done2.get("err"):
            print(f"SKIP 录屏对号复制（无屏幕资源）：{_done2['err']}")
        else:
            _rb3.close()
            check("录屏对号复制", False)
    except Exception as e:
        print(f"SKIP 录屏对号复制：{e}")
    # 10e R109 鼠标点击高亮：注入事件即画扩散圆环、过期不画、左右键分色、BGR/RGB 通道序
    try:
        import recorder as _RC
        import numpy as _npc                          # 作用域内 L1791 另有 _np 赋值，自带别名防 unbound
        _RC._CLICKS.clear()
        _now = time.perf_counter()
        _RC._CLICKS.append((_now - 0.10, 60, 40, 1))       # 左键 100ms 前
        _frc = _npc.zeros((80, 120, 3), dtype=_npc.uint8)
        _hit = _RC.stamp_click_fx(_frc, 0, 0, now=_now)
        _px = _frc[40, int(60 + 7.0 + 0.10 * 85.0)]        # 环带正右采样（r≈15.5）
        _okc = _hit == 1 and int(_px[2]) > 120 and int(_px[1]) > 90 \
            and int(_frc[0, 0].sum()) == 0 and int(_frc[40, 60].sum()) == 0   # 远端/圆心不受影响
        _RC._CLICKS.clear()
        _RC._CLICKS.append((_now - 0.9, 60, 40, 1))        # 过期事件（>0.55s）
        _frc2 = _npc.zeros((80, 120, 3), dtype=_npc.uint8)
        _okc = _okc and _RC.stamp_click_fx(_frc2, 0, 0, now=_now) == 0 and not _frc2.any()
        _RC._CLICKS.clear()
        _RC._CLICKS.append((_now - 0.05, 60, 40, 2))       # 右键 + RGB 通道序
        _frc3 = _npc.zeros((80, 120, 3), dtype=_npc.uint8)
        _RC.stamp_click_fx(_frc3, 0, 0, bgr_order=False, now=_now)
        _px3 = _frc3[40, int(60 + 7.0 + 0.05 * 85.0)]
        _okc = _okc and int(_px3[0]) > 150 and int(_px3[0]) > int(_px3[2])    # RGB 序红环 R 高
        _RC._CLICKS.clear()
        _RC.poll_click()                                   # offscreen 无按键：可调用不炸即可
        check("R109 鼠标点击高亮（扩散圆环·过期不画·左右键色·BGR/RGB 序）", _okc)
    except Exception as e:
        print(f"SKIP 点击高亮：{e}")
    # 10f R109 录屏暂停/继续：钮显隐·计时冻结·写帧冻结·恢复后时长不含暂停段
    try:
        _done4 = {}
        _rb5 = RecBar(on_done=lambda p, c=False: _done4.update(path=p, copied=c),
                      on_error=lambda m: _done4.update(err=m))
        _okp = not _rb5.bp.isVisibleTo(_rb5) and _rb5.br.isEnabled()
        _rb5.start()
        if _rb5.writer:
            _rb5.toggle_pause()
            _okp = _okp and _rb5._paused and _rb5.bp.isVisibleTo(_rb5) and "暂停" in _rb5.msg.text()
            _okp = _okp and not _rb5.br.isEnabled()    # 暂停期间也不能跨屏重选
            _rb5.pick_region()
            _okp = _okp and _rb5.isVisible() and not getattr(_rb5, "_pick_ov", None)
            time.sleep(0.35)
            _rb5._tick()
            _ta = _rb5.time.text()
            time.sleep(0.20)
            _rb5._tick()
            _okp = _okp and _rb5.time.text() == _ta        # 暂停中计时冻结
            _fa = _rb5.frames
            time.sleep(0.15)
            _okp = _okp and _rb5.frames <= _fa + 1         # 暂停中不写帧（容忍在途 1 帧）
            _rb5.toggle_pause()
            _okp = _okp and not _rb5._paused and _rb5._paused_total >= 0.30
            time.sleep(0.45)
            _fb = _rb5.frames
            _rb5.stop()
            app.processEvents()
            _okp = _okp and bool(_done4.get("path")) and _fb > _fa   # 恢复后写帧继续
            if _done4.get("path"):
                try:
                    os.remove(_done4["path"])
                except OSError:
                    pass
        elif _done4.get("err"):
            print(f"SKIP 录屏暂停（无屏幕资源）：{_done4['err']}")
            _okp = None
        else:
            _rb5.close()
            _okp = False
        if _okp is not None:
            check("R109 录屏暂停/继续（钮显隐·计时冻结·写帧冻结·恢复续写）", _okp)
    except Exception as e:
        print(f"SKIP 录屏暂停：{e}")
    # 10g R110 多屏坐标：合成取帧替身驱动生产 start/grab/抓帧循环，两版均跑，失败不 SKIP。
    def _coordinates110():
        import recorder as rc110
        import gifrec as gf110
        import numpy as np110
        from types import SimpleNamespace as NS110
        saved = {k: getattr(rc110, k) for k in ("mss", "MfH264Writer", "missing_deps", "poll_click")}
        saved_gif = {k: getattr(gf110, k) for k in ("grab_array", "stamp_cursor", "stamp_click_fx", "poll_click")}
        bar = gif = None
        try:
            bar = RecBar(on_done=lambda *a: None, on_error=lambda m: errors.append(m))
            bar._open_live = lambda: None
            bar._start_capture = lambda: None
            bar._elapsed = lambda: 0.1
            bar.bc.setChecked(False)
            bar.bh.setChecked(False)
            gif = gf110.GifBar(None, QGuiApplication.primaryScreen(), lambda *a: None, lambda: None)
            errors, written, effect_calls = [], [], []
            single_step = False

            def grab(mon):
                if single_step:
                    bar._capturing = False
                h, w = mon["height"], mon["width"]
                raw = np110.zeros((h, w, 4), np110.uint8)
                raw[:, :, 0] = (np110.arange(w) + mon["left"]) % 251
                raw[:, :, 1] = ((np110.arange(h) + mon["top"]) % 251)[:, None]
                raw[:, :, 2:] = 255
                return NS110(bgra=raw.tobytes(), height=h, width=w)

            rc110.mss = NS110(mss=lambda: NS110(grab=grab, close=lambda: None))
            # R120：编码器换 MF——替身拦截 write_bgra（帧为 BGRA 4 通道，可能经偶数化/resize 归一）
            rc110.MfH264Writer = lambda *a: NS110(
                close=lambda: None, frames=0,
                write_bgra=lambda a: written.append(a.copy()))
            rc110.missing_deps = lambda: []
            rc110.poll_click = gf110.poll_click = lambda: None
            gf110.stamp_cursor = lambda a, x, y, **kw: effect_calls.append((x, y, kw))
            gf110.stamp_click_fx = lambda a, x, y, **kw: effect_calls.append((x, y, kw))
            cases = 0
            for sx, sy in ((0, 0), (-2560, 0), (3120, 0), (0, -1440),
                           (0, 2080), (-2560, -1440), (3120, 2080)):
                for d in (1.25, 1.5, 2.0):
                    screen = NS110(geometry=lambda: QRect(sx, sy, 160, 120), devicePixelRatio=lambda: d)
                    bar.screen = lambda: screen
                    for region in (None, QRect(16, 24, 64, 48), QRect(81, 63, 63, 41)):
                        bar.region, bar.region_screen = region, screen
                        gif.region, gif._screen = region, screen
                        ox, oy = (int(region.x()*d), int(region.y()*d)) if region else (0, 0)
                        x1, y1 = ((int((region.x()+region.width())*d), int((region.y()+region.height())*d))
                                  if region else (int(160*d), int(120*d)))
                        expected_mon = dict(left=sx, top=sy, width=int(160*d), height=int(120*d))
                        written.clear()
                        errors.clear()
                        single_step = False
                        source = grab(expected_mon)
                        expected = np110.frombuffer(source.bgra, np110.uint8).reshape(source.height, source.width, 4)
                        expected = expected[oy:y1, ox:x1].copy()          # R120：BGRA 4 通道
                        _cw110 = max(16, (x1 - ox) & ~1)                  # 与生产侧偶数化同式
                        _ch110 = max(16, (y1 - oy) & ~1)
                        if (expected.shape[1], expected.shape[0]) != (_cw110, _ch110):
                            expected = rc110._resize_bgra(expected, _cw110, _ch110)
                        bar.start()
                        assert not errors and bar.writer, errors
                        assert bar.mon == expected_mon and bar._dpr == d
                        assert len(written) == 1 and np110.array_equal(written[0], expected)
                        assert not bar.br.isEnabled()
                        bar.pick_region()                 # 方法级守卫，不仅仅按钮禁用
                        assert bar.isVisible() and not getattr(bar, "_pick_ov", None)
                        single_step = True
                        bar._capturing = True
                        bar._cap_loop()                   # 后续帧必须使用与首帧相同的区域
                        assert len(written) > 1 and all(np110.array_equal(f, expected) for f in written)
                        bar._cleanup()
                        gif._size = None
                        gif._last_bytes = None
                        gif.frames.clear()
                        gif.durs.clear()
                        effect_calls.clear()
                        gf110.grab_array = lambda *a: np110.zeros_like(expected[..., :3])   # GIF 侧仍为 RGB 3 通道语义
                        gif.grab()
                        assert len(effect_calls) == 2
                        assert all((x, y) == (sx, sy) and kw == dict(ox=ox, oy=oy, bgr_order=False)
                                   for x, y, kw in effect_calls)
                        assert gif.frames, "GIF 合成链未产帧"
                        cases += 1
            bar.fail("回归测试")
            assert bar.br.isEnabled()                     # 失败复位后仍可重新框选
            return cases == 63
        finally:
            if bar is not None:
                bar.close()
            if gif is not None:
                gif.cancel()
            for k, v in saved.items():
                setattr(rc110, k, v)
            for k, v in saved_gif.items():
                setattr(gf110, k, v)
    try:
        check("R110 多屏区域一致（63组正负原点/125·150·200%/首帧·续帧/GIF效果/重选守卫）", _coordinates110())
    except Exception as e:
        print(f"R110 坐标回归异常：{e}")
        check("R110 多屏区域一致", False)
    # 10c R78 复制重试：剪贴板被其他程序短时占用（OpenClipboard 连败）不再静默失败
    try:
        import common as _cm78
        import ctypes as _ct78
        from ctypes import wintypes as _wt78
        _u78, _k78 = _ct78.windll.user32, _ct78.windll.kernel32
        _orig_oc = _u78.OpenClipboard
        _calls78 = {"n": 0}

        def _fake_oc(hwnd):
            _calls78["n"] += 1
            # 前 2 次模拟被占（旧逻辑首开即返 False）；第 3 次调真函数真开——假返 1 不真开会让 EmptyClipboard 失败
            return _orig_oc(hwnd) if _calls78["n"] >= 3 else 0

        _u78.OpenClipboard = _fake_oc
        try:
            _d78 = _tf0.mkdtemp(prefix="_jj_cp78_")
            _f78 = os.path.join(_d78, "r78.txt")
            with open(_f78, "w", encoding="utf-8") as _fh78:
                _fh78.write("x")
            _ok78c = _cm78.copy_file_to_clipboard(_f78) is True and _calls78["n"] == 3
        finally:
            _u78.OpenClipboard = _orig_oc              # 先还原真函数再读板回验
        _hd78 = _clipboard_file()
        check("R78 复制重试（剪贴板短时占用·重试后 CF_HDROP 入板）", _ok78c and _hd78 == _f78)
    except Exception as e:
        print(f"SKIP R78 复制重试：{e}")
    # 10d R80 录屏回车＝对号（路由锁：录制中 Enter → _on_bok；空闲不触发）
    try:
        from PySide6.QtGui import QKeyEvent as _QKE80r
        from PySide6.QtCore import QEvent as _QEv80r
        _rb4 = RecBar(on_done=lambda *a: None, on_error=lambda m: None)
        _calls80 = {"n": 0}
        _rb4._on_bok = lambda: _calls80.update(n=_calls80["n"] + 1)   # 实例属性遮蔽：拦路由
        _rb4.keyPressEvent(_QKE80r(_QEv80r.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
        _ok80 = _calls80["n"] == 0                     # 空闲（未录制）：回车不触发
        _rb4.writer = object()                         # 伪装录制中（writer 非 None）
        _rb4.keyPressEvent(_QKE80r(_QEv80r.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
        _ok80 = _ok80 and _calls80["n"] == 1           # 录制中：回车＝对号
        _rb4.writer = None
        _rb4.close()
        check("录屏回车＝对号路由（录制中触发·空闲不触发）", _ok80)
    except Exception as e:
        print(f"SKIP 录屏回车对号：{e}")
    # 10.5 录屏「范围」框选：覆盖层必须存活（防 GC 回归——无引用覆盖层会被立即销毁）
    try:
        _rb2 = RecBar(on_done=lambda p: None, on_error=lambda m: None)
        _rb2.pick_region()
        _t0 = time.time()
        while time.time() - _t0 < 0.8:
            app.processEvents()
            time.sleep(0.02)
        check("录屏范围框选覆盖层存活（多屏组·先到先得）",
              bool(getattr(_rb2, "_pick_ov", None)) and all(o.isVisible() for o in _rb2._pick_ov))
        for _o in _rb2._pick_ov:
            _o.close_silent()
        _rb2._pick_ov = None
        _rb2.close()
    except Exception as e:
        print(f"SKIP 录屏范围框选：{e}")
    # 10.7 多屏覆盖层组：先到先得（拓展屏按下即接管，败者静默关；Esc 由焦点实例统一收尾）
    try:
        from PySide6.QtGui import QPixmap as _QPm7, QMouseEvent as _QME7, QKeyEvent as _QKE7
        from PySide6.QtCore import QPoint as _QPt7, QEvent as _QEv7
        from capture import ShotOverlay as _SO7     # 后段有同名局部导入，必须局部化避免闭包坑

        class _FakeScrM:
            def __init__(self, g):
                self._g = g

            def geometry(self):
                return self._g

        def _mk_pair():
            _fin7, _ovs7 = [], []

            def _claim7(ov):
                for o in _ovs7:
                    if o is not ov:
                        o.close_silent()

            for _g in (QRect(0, 0, 800, 600), QRect(-800, 0, 800, 600)):   # 拓展屏负坐标（同实测布局）
                _pm7 = _QPm7(800, 600)
                _pm7.fill(QColor("#203040"))
                _o = _SO7(_pm7, pick=True, screen=_FakeScrM(_g))
                _o.claimed.connect(_claim7)
                _o.finished.connect(lambda r, _f=_fin7: _f.append(r))
                _ovs7.append(_o)
            return _ovs7, _fin7

        def _press7(o, x, y):
            o.mousePressEvent(_QME7(_QEv7.Type.MouseButtonPress, _QPt7(x, y), _QPt7(x, y),
                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

        def _mv7(o, x, y):
            o.mouseMoveEvent(_QME7(_QEv7.Type.MouseMove, _QPt7(x, y), _QPt7(x, y),
                                   Qt.NoButton, Qt.LeftButton, Qt.NoModifier))

        def _rel7(o, x, y):
            o.mouseReleaseEvent(_QME7(_QEv7.Type.MouseButtonRelease, _QPt7(x, y), _QPt7(x, y),
                                      Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

        _ovs7, _fin7 = _mk_pair()
        _okM7 = len(_ovs7) == 2 and all(o.isVisible() for o in _ovs7)
        _press7(_ovs7[1], 100, 100)                              # 拓展屏（负坐标）按下＝接管
        _okM7 = _okM7 and not _ovs7[0].isVisible() and _ovs7[1].isVisible()   # 主屏败者静默关
        _mv7(_ovs7[1], 300, 260)
        _rel7(_ovs7[1], 300, 260)
        _s7 = _fin7[0]["sel"] if _fin7 else QRect()
        _okM7 = _okM7 and len(_fin7) == 1 and _fin7[0].get("pick") \
            and _fin7[0]["screen"] is _ovs7[1].screen_ \
            and (_s7.left(), _s7.top()) == (100, 100) and _s7.width() >= 200   # 选区相对接管屏坐标
        for o in _ovs7:
            o.close_silent()
        _ovs8, _fin8 = _mk_pair()
        _ovs8[0].keyPressEvent(_QKE7(_QEv7.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        for o in _ovs8:
            o.close_silent()
        _okM8 = _fin8 == [None] and not any(o.isVisible() for o in _ovs8)   # Esc＝统一取消仅一次
        check("多屏覆盖层先到先得（拓展屏接管·败者静默·Esc统一收尾·finished仅一次）", _okM7 and _okM8)
        # 10.8 R64 回归：败者 C++ 已删后，finished 槽内 close_all 遇尸不炸、后续逻辑照走
        _ovs9, _fin9 = _mk_pair()
        _tail9 = []

        def _fin9_slot(r):
            close_all(_ovs9)                       # 等价 shot_finished 首步组清理（此时败者已删）
            _tail9.append(bool(r and r.get("pick")))   # 清理后的后续逻辑（gif/录屏分支位置）

        _ovs9[0].finished.disconnect()
        _ovs9[0].finished.connect(_fin9_slot)
        _press7(_ovs9[0], 100, 100)                # 主屏接管：败者 close_silent → deleteLater 挂起
        app.sendPostedEvents(None, _QEv7.Type.DeferredDelete)   # 真删败者 C++ 对象（真机事件循环等效）
        import shiboken6 as _sb9
        _dead9 = not _sb9.isValid(_ovs9[1])
        _mv7(_ovs9[0], 300, 260)
        _rel7(_ovs9[0], 300, 260)                  # emit finished → 槽内 close_all 遇尸不炸
        app.processEvents()
        check("已删败者清理安全（deleteLater生效后close_all不炸·finished后续逻辑照走）",
              _dead9 and _tail9 == [True])
    except Exception as e:
        print(f"SKIP 多屏覆盖层：{e}")
    # 10.6 鼠标指针提取与合成（真实会话光标 → 位图 → 显式落点贴帧）
    try:
        import ctypes as _ct
        import numpy as _np
        from recorder import _CURSORINFO, _cursor_rgba, stamp_cursor
        _ci = _CURSORINFO()
        _ci.cbSize = _ct.sizeof(_CURSORINFO)
        if _ct.windll.user32.GetCursorInfo(_ct.byref(_ci)) and _ci.flags == 1 and _ci.hCursor:
            _ent = _cursor_rgba(_ci.hCursor)
            if not _ent or _ent[0].ndim != 3 or _ent[0].shape[2] != 4:
                print("SKIP 鼠标指针：会话光标无可提取位图（自定义/动画光标环境态）")
            else:
                # 位图必须带真透明（min!=0=黑底方块回归；sum=0=隐形光标回归——R121 双方向锁）
                _ok = _ent[0][..., 3].min() == 0
                _fr = _np.zeros((80, 120, 3), dtype=_np.uint8)
                # 显式 sprite 落点 (50,40)：旧写法靠真机光标实时坐标，落在 120x80 帧外即 flake
                _landed = stamp_cursor(_fr, 0, 0, sprite=(_ent[0], _ent[1], _ent[2], 50, 40))
                check(f"鼠标指针提取与合成（位图 {_ent[0].shape[1]}x{_ent[0].shape[0]}）",
                      _ok and _landed and _fr.sum() > 0)
        else:
            print("SKIP 鼠标指针：会话内无可见光标")
    except Exception as e:
        print(f"SKIP 鼠标指针：{e}")
    # 10.6b R122 I 型光标提取回退（GetIconInfo 兼容转换丢 alpha → RGB 白芯+黑描边回退）
    try:
        import ctypes as _ct122
        from recorder import _cursor_rgba as _cr122
        _ib122 = _ct122.windll.user32.LoadCursorW(None, 32513)   # IDC_IBEAM：系统共享句柄，与会话光标无关
        if not _ib122:
            print("SKIP I 型光标：LoadCursor 失败")
        else:
            _ent122 = _cr122(_ib122)
            _ok122g = bool(_ent122) and _ent122[0].ndim == 3 and _ent122[0].shape[2] == 4 \
                and _ent122[0][..., 3].min() == 0 and _ent122[0][..., 3].max() == 255 \
                and 0.005 < float((_ent122[0][..., 3] > 0).mean()) < 0.5
            check("R122 I 型光标提取回退（alpha 丢失→RGB 白芯+黑描边）"
                  + (f"（位图 {_ent122[0].shape[1]}x{_ent122[0].shape[0]}）" if _ent122 else "（extract None）"),
                  _ok122g)
    except Exception as _e122g:
        check(f"R122 I 型光标提取回退（{_e122g!r}）", False)
    # 10.7 指针合成落点与混色（喂假游标：落点=物理坐标-热点-裁剪偏移；通道序分帧型验证）
    try:
        import numpy as _np2
        from recorder import stamp_cursor as _sc
        _sp = (_np2.zeros((10, 10, 4), _np2.uint8), 2, 1, 50, 60)
        _sp[0][..., 0] = 10; _sp[0][..., 1] = 20; _sp[0][..., 2] = 250; _sp[0][..., 3] = 255
        _fbgr = _np2.zeros((100, 100, 3), _np2.uint8)
        _frgb = _np2.zeros((100, 100, 3), _np2.uint8)
        _ok1 = _sc(_fbgr, 0, 0, sprite=_sp)                    # BGR 帧
        _ok2 = _sc(_frgb, 0, 0, sprite=_sp, bgr_order=False)   # RGB 帧（GIF 路径）
        check("指针合成落点与混色（BGR/RGB）",
              _ok1 and _ok2
              and _fbgr[59, 48].tolist() == [10, 20, 250]
              and _frgb[59, 48].tolist() == [250, 20, 10]
              and _fbgr[58, 48].max() == 0 and _fbgr[69, 48].max() == 0
              and _fbgr[59, 47].max() == 0)
    except Exception as e:
        print(f"SKIP 指针落点：{e}")
    # 11 二维码生成（R120：识别功能随 cv2 整体退役；生成保留，PIL 读回验证产物有效）
    if not LITE:
        import qrcode  # noqa: F401
        _qr = os.path.join(C.data_dir(), "smoke_qr.png")
        good, err = EX.qr_generate("https://jianjie.test/smoke", _qr)
        if good:
            from PIL import Image as _QrImg
            with _QrImg.open(_qr) as _qim:
                check("二维码生成产物可读", _qim.format == "PNG" and _qim.size[0] > 100)
            os.remove(_qr)
        else:
            print(f"SKIP 二维码生成：{err}")
    # R91 二维码对话卡片化（全局基准模态壳：输入卡/结果卡/复制路径）
    try:
        from PySide6.QtWidgets import QFrame as _QFr91, QLabel as _QLb91
        _qi = W.QrInputDialog()
        _ok91 = _qi.testAttribute(Qt.WA_TranslucentBackground) \
            and bool(_qi.windowFlags() & Qt.FramelessWindowHint) \
            and bool(_qi.windowFlags() & Qt.WindowStaysOnTopHint) \
            and _qi.body.objectName() == "cardBody" and _qi.title.text() == "二维码生成" \
            and _qi.edit.placeholderText() != "" and _qi.ok.objectName() == "primary"
        _qi.edit.setPlainText("  https://jianjie.test/r91  ")
        _ok91 = _ok91 and _qi.text() == "https://jianjie.test/r91"   # text() 去空白
        _qi.close()
        _qrp = os.path.join(C.data_dir(), "r91_result.png")
        _pm91 = QPixmap(40, 40)
        _pm91.fill(QColor("#ffffff"))
        _qr2 = W.QrResultDialog(_pm91, _qrp)
        _ok91 = _ok91 and _qr2.title.text() == "二维码已生成" \
            and _qr2.findChild(_QFr91, "qrHolder") is not None \
            and any(_qrp in lb.text() for lb in _qr2.findChildren(_QLb91))   # qrHolder=白底码卡：扫码可靠性
        _qr2.btn_copy.click()
        _ok91 = _ok91 and QGuiApplication.clipboard().text() == _qrp \
            and _qr2.btn_copy.text() == "已复制 ✓"
        _qr2.close()
        check("R91 二维码对话卡片化（输入卡/结果卡/白底码图/复制路径）", _ok91)
    except Exception as e:
        print(f"SKIP R91 二维码卡片：{e}")
    # R180：复制提示必须由结果模态卡持有，且在其 exec 事件循环内实际显示。
    try:
        _qr180 = W.QrResultDialog(_pm91, _qrp, on_top=True)
        _toast180 = _show_dialog_toast(_qr180, "已复制到剪贴板", delay=20)
        _seen180 = []

        def _observe_qr_toast180():
            _seen180.append(_qr180.isVisible() and _toast180.isVisible()
                            and _toast180.parentWidget() is _qr180
                            and bool(_toast180.windowFlags() & Qt.WindowStaysOnTopHint))
            _qr180.accept()

        QTimer.singleShot(140, _observe_qr_toast180)
        _qr180.exec()
        check("R180 二维码复制 toast 归属结果卡并在模态期间可见",
              _seen180 == [True])
        _qr180.close()
    except Exception as e:
        print(f"SKIP R180 二维码 toast：{e}")
    # 12 截图所见即所得回归：合成帧+选区 → composite_crop 必须满幅且对位（防 DPR 半幅黑边回归）
    try:
        from capture import ShotOverlay
        from PySide6.QtGui import QColor, QImage
        _bw, _bh = 400, 300

        def _pat(x, y):
            return QColor((x * 7) % 256, (y * 11) % 256, (x + y) % 256)

        _img = QImage(_bw, _bh, QImage.Format_ARGB32)
        for _y in range(_bh):
            for _x in range(_bw):
                _img.setPixel(_x, _y, _pat(_x, _y).rgba())
        _base = QPixmap.fromImage(_img)
        _base.setDevicePixelRatio(2.0)

        class _FakeScr:
            def geometry(self):
                return QRect(0, 0, _bw // 2, _bh // 2)   # → 实测 k=2.0，模拟 200% 屏

        _ov = ShotOverlay(_base, screen=_FakeScr())
        _ov.sel = QRect(50, 40, 100, 80)
        _pm = _ov.composite_crop()
        _sx, _sy = 100, 80
        _im = _pm.toImage()
        _corners = {"左上": (0, 0), "右上": (_im.width() - 1, 0),
                    "左下": (0, _im.height() - 1), "右下": (_im.width() - 1, _im.height() - 1)}
        _bad = []
        for _nm, (_cx, _cy) in _corners.items():
            _c = _im.pixelColor(_cx, _cy)
            _e = _pat(_sx + _cx, _sy + _cy)
            if abs(_c.red() - _e.red()) > 6 or abs(_c.green() - _e.green()) > 6 or abs(_c.blue() - _e.blue()) > 6:
                _bad.append(_nm)
        check(f"截图所见即所得（{round(100 * 2)}x{round(80 * 2)} 满幅，四角对位 {'OK' if not _bad else _bad}）",
              _im.width() == 200 and _im.height() == 160
              and abs(_pm.devicePixelRatio() - 2.0) < 0.01 and not _bad)
        _ov.close()
    except Exception as e:
        print(f"SKIP 截图所见即所得：{e}")
    # 13 OCR：PIL 生成文字图 → 系统 OCR 真识别（无引擎则跳过）
    try:
        from PIL import Image, ImageDraw, ImageFont
        _oi = os.path.join(C.data_dir(), "smoke_ocr.png")
        im = Image.new("RGB", (560, 160), "white")
        dr = ImageDraw.Draw(im)
        fnt = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 52)
        dr.text((30, 50), "SMOKE 简截 2026", font=fnt, fill="black")
        im.save(_oi)
        o_ok, o_txt = EX.ocr_image(_oi)
        if o_ok:
            check(f"OCR 真识别（含'2026'：{'2026' in o_txt.replace(' ', '')}）", "2026" in o_txt.replace(" ", ""))
        else:
            print(f"SKIP OCR（无引擎/失败）：{o_txt[:60]}")
        os.remove(_oi)
    except Exception as e:
        print(f"SKIP OCR：{e}")
    # 14 快捷键：组合解析 + 注册线程热重启
    check("快捷键解析默认组合", all(hk_parse(DEFAULT_HOTKEYS[k]) is not None for k, _l in _HOTKEY_ACTIONS))
    check("快捷键解析拒绝非法",
          hk_parse("Ctrl++") is None and hk_parse("Ctrl+Alt+不存在的键") is None
          and hk_parse("A") == (0, ord("A")))
    restart_hotkeys([(9, *hk_parse("Ctrl+Alt+Shift+F9"))])
    _t0 = time.time()
    while time.time() - _t0 < 0.5:
        app.processEvents()
        time.sleep(0.02)
    check("快捷键注册线程可用", _hotkey_ctl["ok"] == {9} and _hotkey_ctl["thread"].is_alive())
    restart_hotkeys([(9, *hk_parse("Ctrl+Alt+Shift+F10"))])
    _t0 = time.time()
    while time.time() - _t0 < 0.5:
        app.processEvents()
        time.sleep(0.02)
    check("快捷键热重启换键重注册", _hotkey_ctl["ok"] == {9} and not _hotkey_ctl["failed"])
    ctypes.windll.user32.PostThreadMessageW(_hotkey_ctl["thread_id"], 0x0012, 0, 0)   # 收尾注销
    # R92 快捷键设置卡片化（全局基准模态壳）＋主面板不再被隐藏
    try:
        from PySide6.QtGui import QKeyEvent as _QKEv92
        from PySide6.QtCore import QEvent as _QEv92
        import inspect as _insp92
        _hd = W.HotkeyDialog(_HOTKEY_ACTIONS, dict(DEFAULT_HOTKEYS), hk_parse)
        _k92 = "longshot" if LITE else "rec"   # R134：lite 动作表裁 rec，换表内 longshot 驱动同链路
        _ok92 = _hd.testAttribute(Qt.WA_TranslucentBackground) \
            and bool(_hd.windowFlags() & Qt.FramelessWindowHint) \
            and bool(_hd.windowFlags() & Qt.WindowStaysOnTopHint) \
            and _hd.body.objectName() == "cardBody" and _hd.title.text() == "快捷键设置" \
            and len(_hd.btns) == len(_HOTKEY_ACTIONS)
        _hd.btns["shot"].click()                                       # 进捕获态
        _ok92 = _ok92 and _hd.cap["key"] == "shot" \
            and _hd.btns["shot"].text() == "按下新组合…" \
            and _hd.btns["shot"].property("cap") == "true"
        _hd.keyPressEvent(_QKEv92(_QEv92.KeyPress, Qt.Key_F5,
                                  Qt.ControlModifier | Qt.AltModifier))  # 按新组合
        _ok92 = _ok92 and _hd.cur["shot"] == "Ctrl+Alt+F5" \
            and _hd.cap["key"] is None and _hd.btns["shot"].text() == "Ctrl+Alt+F5"
        _hd.btns[_k92].click()
        _hd.keyPressEvent(_QKEv92(_QEv92.KeyPress, Qt.Key_F6, Qt.NoModifier))  # 无修饰键不入账
        _ok92 = _ok92 and _hd.cur[_k92] == DEFAULT_HOTKEYS[_k92] and _hd.cap["key"] == _k92
        _hd.keyPressEvent(_QKEv92(_QEv92.KeyPress, Qt.Key_Escape, Qt.NoModifier))  # Esc 只取消捕获
        _ok92 = _ok92 and _hd.cap["key"] is None and _hd.result() != QDialog.Accepted
        _hd.cur[_k92] = "Ctrl+Alt+F5"                                  # 制造冲突
        _hd._save()
        _ok92 = _ok92 and _hd.result() != QDialog.Accepted and "冲突" in _hd.info.text()
        _hd.cur[_k92] = DEFAULT_HOTKEYS[_k92]
        _hd._save()
        _ok92 = _ok92 and _hd.result() == QDialog.Accepted
        _hd.close()
        check("R92 快捷键设置卡（捕获/修饰键约束/Esc/冲突校验/保存）", _ok92)
        check("R92 设置快捷键不再隐藏主面板",
              "panel.hide()" not in _insp92.getsource(Controller.hotkeys))
    except Exception as e:
        print(f"SKIP R92 快捷键卡：{e}")
    # 15 长截图：numpy 列压缩 NCC 像素级拼接（R107 起取代 cv2 模板匹配）
    import numpy as _np
    import longshot as _ls
    _r = _np.arange(2600, dtype=_np.int32)[:, None, None]
    _page = ((_r * 7) % 251 + (_np.arange(400, dtype=_np.int32)[None, :, None] * 3) % 97).astype(_np.uint8)
    _page = _np.repeat(_page, 3, axis=2)
    _acc = _page[0:400].copy()
    _prev = _acc.copy()
    _ok, _n = True, 0
    for _i in range(1, 7):
        _fr = _page[_i * 150:_i * 150 + 400]
        _s, _e, _sc, _bh = _ls.stitch_pair(_acc, _fr, prev=_prev)
        if _s <= 0 or _sc < 0.9:
            _ok = False
            break
        _acc = _np.vstack([_acc, _fr[_s:_e]])
        _prev = _fr.copy()
        _n += 1
    check("长截图模板匹配像素级拼接",
          _ok and _n == 6 and _np.array_equal(_acc, _page[:_acc.shape[0]]))
    _s0 = _ls.stitch_pair(_acc, _prev, prev=_prev)[0]
    check("长截图到底/未滚动检测", _s0 == 0)
    _foot = _np.full((40, 400, 3), 200, _np.uint8)
    _acc = _np.vstack([_page[0:360], _foot]).copy()
    _fr = _np.vstack([_page[150:510], _foot])
    _s, _e, _sc, _bh = _ls.stitch_pair(_acc, _fr, prev=_acc, strip_footer=True)
    _acc2 = _np.vstack([_acc[:-_bh], _fr[_s:_e]]) if _s > 0 and _bh > 0 else _acc
    check("长截图粘性底栏剔除",
          _s == 210 and _bh == 40 and _np.array_equal(_acc2, _page[:510]))
    # 回归：底栏剔除后的二次拼接不得再按 bot_h 回退（老版本每条接缝重复 40 行）
    _pg = _det_noise(800, 400, 11)             # R120 确定性噪声（random 子包已排包）
    _acc3 = _np.vstack([_pg[0:360], _foot]).copy()
    _f1 = _np.vstack([_pg[150:510], _foot])
    _sa, _ea, _sca, _bha = _ls.stitch_pair(_acc3, _f1, prev=_acc3, strip_footer=True)
    _acc3 = _np.vstack([_acc3[:-_bha], _f1[_sa:_ea]])
    _f2 = _np.vstack([_pg[300:660], _foot])
    _sb, _eb, _scb, _bhb = _ls.stitch_pair(_acc3, _f2, prev=_f1, strip_footer=False)
    _acc3 = _np.vstack([_acc3, _f2[_sb:_eb]]) if _sb > 0 else _acc3
    check("长截图二次拼接无重复（剔除后不回退）",
          _sa == 210 and _bha == 40 and _sb == 210 and _bhb == 40
          and _np.array_equal(_acc3, _pg[:660]))
    _fa = _np.zeros((100, 50, 3), _np.uint8)
    _fb = _np.zeros((100, 50, 3), _np.uint8)
    _fb[30:] = 9
    check("长截图粘性区检测", _ls._static_rows(_fa, _fb, True) == 30
          and _ls._static_rows(_fa, _fb, False) == 0)
    _fc = _np.zeros((100, 50, 3), _np.uint8)
    _fd = _np.zeros((100, 50, 3), _np.uint8)
    _fd[:80] = 5
    check("长截图粘性底部检测", _ls._static_rows(_fc, _fd, False) == 20
          and _ls._static_rows(_fc, _fd, True) == 0)
    _lg2 = _det_noise(350, 50, 22)             # R120 确定性噪声
    _lg2[190:] = 7                                     # 页面底部大片纯色（懒加载/空白）
    _fm2 = _np.empty_like(_lg2)
    _fm2[:250] = _lg2[100:]                            # 下滚 100 行
    _fm2[250:] = 7
    _s2, _e2, _sc2, _bh2 = _ls.stitch_pair(_lg2, _fm2)
    check("长截图纯色尾部回退", _s2 == 250 and _e2 == 350 - _bh2)
    _lg3 = _np.zeros((400, 100, 3), _np.uint8)
    _lg3[200:] = 7                                     # 长图尾部大片同色（真实页面：到底后静止区）
    _fr3 = _np.full((400, 100, 3), 7, _np.uint8)       # 到底后静止帧（匹配抖动仍给出 s>0）
    _fr4 = _np.full((400, 100, 3), 9, _np.uint8)       # 真新内容
    _fr5 = _np.zeros((400, 100, 3), _np.uint8)         # 与尾部错开 50 行 → 不算假新
    _fr5[:250] = 7
    check("长截图假新内容拦截（防无限滚屏）",
          _ls.tail_dup(_lg3, _fr3, 250, 400) and _ls.tail_dup(_lg3, _fr3, 248, 398)
          and not _ls.tail_dup(_lg3, _fr4, 250, 400) and not _ls.tail_dup(_lg3, _fr5, 250, 400))
    # 16 GIF：编码器多帧 + 录制端到端（offscreen 静态屏 → 单帧长延时保时序）
    try:
        from gifrec import save_gif, GifBar
        from PIL import Image as _PImage
        _fs = [_PImage.fromarray(_np.full((40, 60, 3), v, _np.uint8), "RGB")
               .convert("P", palette=_PImage.ADAPTIVE) for v in (10, 128, 240)]
        _gp = os.path.join(tempfile.gettempdir(), "_jj_gif_test.gif")
        save_gif(_fs, [100, 100, 100], _gp)
        _im = _PImage.open(_gp)
        _ok1 = _im.n_frames == 3
        _im.close()
        os.remove(_gp)
        _gif_out = {}
        _gb = GifBar(None, _QGA.primaryScreen(),
                     on_done=lambda p, c: _gif_out.update(path=p, copied=c),
                     on_cancel=lambda: _gif_out.update(cancel=True))
        _gb.hide()   # 离屏平台 grabWindow(0) 合成窗口像素、WDA 无效，呼吸点会把静止屏搅成每拍新帧；真平台由 WDA 排除
        _ok2 = (not _gb.timer.isActive() and not _gb.frames
                and not _gb.bok.isEnabled() and not _gb.bstop.isEnabled()
                and _gb.brec.isVisibleTo(_gb))
        _gb.brec.click()                       # 「开始录制」按钮驱动录制态（框选确认后不再自动开录）
        _ok2 = _ok2 and _gb.timer.isActive() and _gb.dot._on \
            and not _gb.brec.isVisibleTo(_gb) and _gb.bok.isEnabled() and _gb.bstop.isEnabled()
        _t0 = time.time()
        while time.time() - _t0 < 0.8 and not _gif_out:
            app.processEvents()
            time.sleep(0.03)
        _gb.copy_gif()                         # 对号＝停止+保存+复制 GIF 文件引用到剪贴板
        while time.time() - _t0 < 4 and not _gif_out:
            app.processEvents()
            time.sleep(0.03)
        if _gif_out.get("path") and os.path.isfile(_gif_out["path"]):
            _im = _PImage.open(_gif_out["path"])
            _ok2 = _ok2 and _im.n_frames >= 1 and _im.info.get("duration", 0) >= 500
            _im.close()
            os.remove(_gif_out["path"])
        # offscreen 的 Qt 剪贴板是进程内假实现，读不到系统 CF_HDROP——绕 Qt 用 Win32 直读回验
        _hdrop_txt = _clipboard_file()
        _ok2 = _ok2 and _gif_out.get("copied") is True \
            and _hdrop_txt == _gif_out.get("path", "-") and _hdrop_txt.lower().endswith(".gif")
        _ok2 = _ok2 and _gb.bok.x() > _gb.bx.x() > _gb.bstop.x()   # 保存/取消/对号：对号收尾最右
        check("GIF 编码器多帧", _ok1)
        check("GIF 就绪→点开始→录制端到端（对号复制入剪贴板·urls 指向 gif）", _ok2)
        _save_out = {}
        _gb3 = GifBar(None, _QGA.primaryScreen(),
                      on_done=lambda p, c: _save_out.update(path=p, copied=c),
                      on_cancel=lambda: _save_out.update(cancel=True))
        _gb3.hide()
        _gb3.start()
        _gb3.grab()                            # 直接抓 1 帧（不等 timer 节拍）
        _gb3.bstop.click()                     # 停止并保存钮：存盘不复制
        _ok3 = bool(_save_out.get("path")) and os.path.isfile(_save_out["path"]) \
            and _save_out.get("copied") is False
        if _save_out.get("path"):
            try:
                os.remove(_save_out["path"])
            except OSError:
                pass
        check("GIF 停止并保存钮（仅存盘·不入剪贴板）", _ok3)
        # R80 GIF 回车＝对号：录制中 Enter 停止+保存+复制（全链路锁，同 bok.click 路径）
        from PySide6.QtGui import QKeyEvent as _QKE80
        from PySide6.QtCore import QEvent as _QEv80
        _enter_out = {}
        _gb4 = GifBar(None, _QGA.primaryScreen(),
                      on_done=lambda p, c: _enter_out.update(path=p, copied=c),
                      on_cancel=lambda: _enter_out.update(cancel=True))
        _gb4.hide()
        _gb4.start()
        _gb4.grab()                            # 直接抓 1 帧（不等 timer 节拍）
        _kev80 = _QKE80(_QEv80.Type.KeyPress, Qt.Key_Return, Qt.NoModifier)
        for _try80 in range(5):                # 系统剪贴板瞬时占用：照产品设计回车重试
            _gb4.keyPressEvent(_kev80)         # （「复制失败后也允许回车重试」真实路径，R78 同纪律）
            if _enter_out.get("copied"):
                break
            time.sleep(0.3)
        _ok4 = _enter_out.get("copied") is True and _enter_out.get("path", "-").lower().endswith(".gif") \
            and os.path.isfile(_enter_out.get("path", "-")) \
            and _clipboard_file() == _enter_out.get("path")
        if _enter_out.get("path"):
            try:
                os.remove(_enter_out["path"])
            except OSError:
                pass
        check("GIF 回车＝对号（停止+保存+复制入剪贴板）", _ok4)
        _fol = {"n": 0}
        _gb2 = GifBar(QRect(120, 120, 320, 200), _QGA.primaryScreen(),
                      on_done=lambda p: None, on_cancel=lambda: None)
        _fol_ok = _gb2._dim is not None and _gb2._frame is not None
        _gb2._dim.update = lambda: _fol.update(n=_fol["n"] + 1)   # 压暗层重绘探针（覆盖绑定方法）
        _gb2._on_region_move(QRect(160, 160, 320, 200))
        _fol_ok = _fol_ok and _fol["n"] == 1 and _gb2.region == QRect(160, 160, 320, 200)
        _gb2.cancel()
        check("GIF 拖拽跟随（黑框条动→压暗层重画·绿框不再分离）", _fol_ok)
    except Exception as e:
        print(f"SKIP GIF：{e}")
    # 16.2 控制条重设计：呼吸点启停 + 图标按钮工厂 + 屏幕捕获排除
    try:
        from longshot import LongShotBar as _LSB
        from icons import PulseDot as _PD
        _ed_in = {}
        _lb2 = _LSB(on_finish=lambda *a: None, on_cancel=lambda: None,
                    on_edit=lambda a, n: _ed_in.update(arr=a, n=n),
                    region=None, screen=_QGA.primaryScreen())
        _okd = isinstance(_lb2.dot, _PD) and _lb2.dot._on and _lb2.dot._t.isActive()  # 进条即滚动态：呼吸点自动亮
        _lb2.dot.set_active(False)
        _okd = _okd and not _lb2.dot._on and not _lb2.dot._t.isActive()
        _lb2.dot.set_active(True)
        _okd = _okd and _lb2.dot._on and _lb2.dot._t.isActive()
        _lb2.dot.set_active(False)
        _okd = _okd and not _lb2.dot._t.isActive()
        # 弹出即手动滚动态（无开始钮）；三键 [✎编辑][⬇下载][✕][✓]，✓ 最右（同 GIF 条）
        _oks = (not hasattr(_lb2, "start_auto") and not hasattr(_lb2, "_auto")
                and not hasattr(_lb2, "b1")                                   # 开始钮已移除：进条即滚动态
                and callable(getattr(_lb2, "edit", None))
                and "编辑" in _lb2.bedit.toolTip()
                and _lb2.b_cancel is not None and _lb2.b_fin is not None and _lb2.b_save is not None
                and _lb2.b_save.isEnabled() and _lb2.b_fin.isEnabled()        # 自动 start：成品钮直接解禁
                and "复制" in _lb2.b_fin.toolTip() and "本地目录" in _lb2.b_save.toolTip()
                and 0 <= _lb2.layout().indexOf(_lb2.b_save) < _lb2.layout().indexOf(_lb2.b_fin)
                and _lb2.running and "慢滚" in _lb2.msg.text())
        # R105：抓帧节拍 400ms（原 800：滚动与拍点错开＝「预览偶尔不实时」主因）；失联收尾窗保持 ≈10s
        _okt = _lb2.timer.interval() == 400 \
            and 8.0 <= _LSB.MISS_LIMIT * _lb2.timer.interval() / 1000.0 <= 12.0
        _lb2.timer.stop()
        _lb2.running = False                       # 冻结自动拼接：后续用例手动驱动，防 grab 穿插改图
        # 手动模式无「到底」判定（暂停阅读是常态）：唯一自动收尾＝连续失联防挂死
        _lb2.running = True
        _lb2._miss = 0
        _okfin = not _lb2._should_autofinish()
        _lb2._miss = _LSB.MISS_LIMIT + 1
        _okfin = _okfin and _lb2._should_autofinish()
        _lb2.running = False
        # 编辑按钮路由：没开始＝条上提示不退出；开始后＝带长图/屏数回调进编辑器
        _lb2.long_arr = None
        _lb2.edit()
        _oked = _lb2.isVisible() and not _ed_in
        _lb2.long_arr = np.zeros((12, 10, 3), dtype=np.uint8)
        _lb2.frames = 2
        _lb2.edit()
        _oked = _oked and not _lb2.isVisible() and _ed_in.get("n") == 2 \
            and _ed_in["arr"].shape == (12, 10, 3)
        # 选区高亮框：选区非空即创建，随条关闭
        _lb2b = _LSB(on_finish=lambda *a: None, on_cancel=lambda: None,
                     region=QRect(10, 10, 300, 200), screen=_QGA.primaryScreen())
        _okh = getattr(_lb2b, "_halo", None) is not None and _lb2b._halo.isVisible()
        _lb2b.close()
        _okh = _okh and getattr(_lb2b, "_halo", None) is None
        # 完成后图片直接进剪贴板（聊天窗/文档 Ctrl+V 即贴图）；⬇ 下载路径纯落盘不动剪贴板
        import tempfile as _tf
        _tmp_png = os.path.join(_tf.gettempdir(), "_jj_smoke_ls.png")
        QImage(9, 6, QImage.Format_RGB888).save(_tmp_png, "PNG")
        ctl.long_finished(_tmp_png, 2)                      # 默认 copy=True（编辑器/对号路径）
        _okc = not _QGA.clipboard().image().isNull()
        _QGA.clipboard().clear()
        ctl.long_finished(_tmp_png, 2, copy=False)          # ⬇ 下载路径：不碰剪贴板
        _okc = _okc and _QGA.clipboard().image().isNull()
        os.remove(_tmp_png)
        ctl._shot_notify.hide()                             # R154：通知卡清场，防残留连坐后段
        # finish(copy) 双路径：⬇ 下载钮＝纯保存回调 copy=False；✓ 对号＝保存+复制回调 copy=True
        import longshot as _LS
        _tmpd = _tf.mkdtemp(prefix="_jj_lsfin_")
        _orig_pdir = _LS.pictures_dir
        _LS.pictures_dir = lambda: _tmpd                    # 落盘指到临时目录，不污染图片库
        _fin_got = []
        try:
            _lb3 = _LSB(on_finish=lambda p, n, c, image=None: _fin_got.append((p, n, c)),
                        on_cancel=lambda: None, region=None, screen=_QGA.primaryScreen())
            _lb3.timer.stop()
            _lb3.running = False                     # 冻结自动拼接，防 grab 穿插改掉断言用图
            _lb3.long_arr = np.zeros((12, 10, 3), dtype=np.uint8)
            _lb3.frames = 3
            _lb3.b_save.click()                             # ⬇ 下载钮路径（自动 start 已解禁，直接可按）
            _lb4 = _LSB(on_finish=lambda p, n, c, image=None: _fin_got.append((p, n, c)),
                        on_cancel=lambda: None, region=None, screen=_QGA.primaryScreen())
            _lb4.timer.stop()
            _lb4.running = False
            _lb4.long_arr = np.zeros((12, 10, 3), dtype=np.uint8)
            _lb4.frames = 4
            _lb4.b_fin.click()                              # ✓ 对号路径
        finally:
            _LS.pictures_dir = _orig_pdir
        _okfp = (len(_fin_got) == 2 and _fin_got[0][2] is False and _fin_got[1][2] is True
                 and all(os.path.isfile(p) and p.startswith(_tmpd) for p, _n, _c in _fin_got))
        import shutil as _shl
        _shl.rmtree(_tmpd, ignore_errors=True)
        _okx = C.exclude_from_capture(_lb2)    # 离屏 Qt 的假 HWND 上必然失败，真会话才生效：仅 INFO
        _lb2.close()
        print(f"INFO 控制条捕获排除 WDA_EXCLUDEFROMCAPTURE：{'已生效' if _okx else '不可用（离屏降级，真平台生效）'}")
        check("控制条呼吸点启停", _okd)
        check("长截图进条即手动滚动（无开始钮·[编辑][⬇][✕][✓]·仅失联收尾）", _oks and _okfin)
        check("R105 长截图抓帧节拍（400ms·失联窗≈10s）", _okt)
        check("长截图编辑按钮路由（空图提示/有图进编辑器）", _oked)
        check("长截图选区高亮框（创建/随条关闭）", _okh)
        check("长截图完成入剪贴板（图片可直接粘贴·下载路径不动剪贴板）", _okc)
        check("长截图下载/对号双路径（⬇纯保存·✓保存+复制·落本地目录）", _okfp)
    except Exception as e:
        print(f"SKIP 控制条重设计：{e}")
    # 16.3 长截图编辑器：滚动画布 + 精简工具（画笔/文字）+ 撤销重做 + 渲染落盘回调
    try:
        import longedit as _LE
        from PySide6.QtCore import QEvent as _QEv3, QPoint as _QPt3
        from PySide6.QtGui import QMouseEvent as _QME3
        _edout = {}
        _tmp_ed = tempfile.mkdtemp(prefix="_jj_lgedit_")
        _ea = np.zeros((160, 240, 3), dtype=np.uint8)
        _ea[:, :120] = 255                            # 左白右黑：红笔/文字对比可判
        _led = _LE.LongEditor(_ea, frames=3,
                              on_done=lambda p, n, image=None: _edout.update(path=p, n=n, image=image),
                              on_cancel=lambda: _edout.update(cancel=True),
                              save_dir=_tmp_ed)
        _cv = _led.canvas
        _row_ys = {w.mapTo(_led, _QPt3(0, w.height() // 2)).y() for w in _led._tool_btns.values()}
        _popy134 = _led.props_pop.mapTo(_led, _QPt3(0, 0)).y()
        _tby134 = next(iter(_led._tool_btns.values())).mapTo(_led, _QPt3(0, 0)).y()
        check("长截图编辑器主条单行+辐条居其下（R134 颜色/粗细/选项迁辐条）",
              len(_row_ys) == 1 and not hasattr(_led, "_dots") and _popy134 > _tby134)

        def _press3(x, y):
            _cv.mousePressEvent(_QME3(_QEv3.Type.MouseButtonPress, _QPt3(x, y), _QPt3(x, y),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

        def _mv3(x, y):
            _cv.mouseMoveEvent(_QME3(_QEv3.Type.MouseMove, _QPt3(x, y), _QPt3(x, y),
                                     Qt.NoButton, Qt.LeftButton, Qt.NoModifier))

        def _rel3(x, y):
            _cv.mouseReleaseEvent(_QME3(_QEv3.Type.MouseButtonRelease, _QPt3(x, y), _QPt3(x, y),
                                        Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        _okE = _led.isVisible() and _cv.stamps == [] and _cv.cur_tool == "rect" \
            and _cv.width() == round(240 * _cv._k)
        _cv.cur_tool = "pen"                          # 画笔：拖一笔成红色笔画（图坐标）
        _cv.cur_color = "#ff0000"
        _press3(10, 10)
        _mv3(40, 12)
        _rel3(40, 12)
        _okE = _okE and len(_cv.stamps) == 1 and _cv.stamps[0]["kind"] == "stroke" \
            and _cv.stamps[0]["pts"][0] == _QPt3(10, 10)
        _cv.cur_tool = "text"                         # 文字：点击出浮层，落字成对象
        _press3(60, 60)
        _okE = _okE and _cv.text_editor is not None
        _cv.text_editor.setPlainText("长图标注")
        _cv.commit_text_editor()
        _okE = _okE and len(_cv.stamps) == 2 and _cv.stamps[1]["kind"] == "text" \
            and _cv.stamps[1]["lines"] == ["长图标注"] and _cv.text_editor is None
        _cv.undo()                                    # 撤销重做到空再到满
        _okU = len(_cv.stamps) == 1
        _cv.redo()
        _okU = _okU and len(_cv.stamps) == 2
        _cv.undo()
        _cv.undo()
        _okU = _okU and len(_cv.stamps) == 0
        _cv.redo()
        _cv.redo()
        _okU = _okU and len(_cv.stamps) == 2
        _img3 = _cv.render_final()
        _okE = _okE and _okU and _img3.width() == 240 and _img3.height() == 160 \
            and sum(1 for _xx in range(10, 41) for _yy in range(8, 16)
                    if _img3.pixelColor(_xx, _yy).red() > 200
                    and _img3.pixelColor(_xx, _yy).green() < 80) >= 5 \
            and sum(1 for _xx in range(58, 116) for _yy in range(52, 78)
                    if _img3.pixelColor(_xx, _yy).red() > 200
                    and _img3.pixelColor(_xx, _yy).green() < 80) >= 3
        check("长截图编辑器（画笔图坐标/文字落字/撤销重做/全分辨率渲染）", _okE)
        # 马赛克笔刷化 + 高光 + 序号 + 双击再编辑（交互同截图侧）
        _okM2 = "num" in _led._tool_btns and "high" in _led._tool_btns
        _bs2, _rad2 = _cv._mosaic_bs(), _cv._mosaic_brush_r()   # 240 宽 → _s=0.8
        _okM2 = _okM2 and _bs2 == 6 and abs(_rad2 - 12.8) < 1e-6
        _tiles4 = _cv._mosaic_tiles([_QPt3(10, 140), _QPt3(120, 140)])
        _okM2 = _okM2 and len(_tiles4) >= 30 \
            and all(tx % _bs2 == 0 and ty % _bs2 == 0 for tx, ty, _c in _tiles4)   # 半径刷头成片
        _n4 = len(_tiles4)
        _tiles5 = _cv._mosaic_tiles([_QPt3(10, 140), _QPt3(120, 140), _QPt3(170, 140)])
        _cv._mosa_cache = None
        _tiles6 = _cv._mosaic_tiles([_QPt3(10, 140), _QPt3(120, 140), _QPt3(170, 140)])
        _okM2 = _okM2 and len(_tiles5) > _n4 and sorted(_tiles5) == sorted(_tiles6)   # 增量=全量
        check("长截图马赛克笔刷化（半径成片·网格对齐·增量一致）", _okM2)

        def _rpress3(x, y):                           # 右键：删除命中的序号圆标
            _cv.mousePressEvent(_QME3(_QEv3.Type.MouseButtonPress, _QPt3(x, y), _QPt3(x, y),
                                      Qt.RightButton, Qt.RightButton, Qt.NoModifier))

        _cv.cur_tool = "high"                         # 高光：选区内亮、圈外暗、无颜色/笔宽键
        _press3(10, 20)
        _mv3(70, 50)
        _rel3(70, 50)
        _okH = len(_cv.stamps) == 3 and _cv.stamps[2]["kind"] == "high" \
            and "color" not in _cv.stamps[2] and "w" not in _cv.stamps[2]
        _imgH = _cv.render_final()
        _okH = _okH and _imgH.pixelColor(40, 35).red() >= 240 \
            and _imgH.pixelColor(200, 100).red() < 160
        check("长截图高光（选区亮圈外暗·对象无色宽键）", _okH)
        _cv.cur_tool = "num"                          # 序号：落标→附言→右键删除→撤销
        _press3(30, 130)
        _okN = len(_cv._nums) == 1 and _cv.text_edit is not None
        _cv.text_edit.setPlainText("第一")
        _cv.commit_text_input()
        _okN = _okN and _cv._nums[0]["text"] == "第一" and "tdx" in _cv._nums[0]
        _press3(90, 130)
        _cv.commit_text_input()
        _okN = _okN and len(_cv._nums) == 2
        _rpress3(90, 130)
        _okN = _okN and len(_cv._nums) == 1 and _cv._nums[0]["pt"] == _QPt3(30, 130)
        _cv.undo()
        _okN = _okN and len(_cv._nums) == 2           # 删除可撤销
        check("长截图序号（落标/附言原位/右键删除/撤销）", _okN)
        _dbl = _QME3(_QEv3.Type.MouseButtonDblClick, _QPt3(62, 62), _QPt3(62, 62),
                     Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        _cv.mouseDoubleClickEvent(_dbl)
        _okT = _cv.text_editor is not None and _cv.text_editor.toPlainText() == "长图标注"
        _cv.text_editor.setPlainText("改字")
        _cv.commit_text_editor()
        _okT = _okT and len(_cv.stamps) == 3 and _cv.stamps[1]["lines"] == ["改字"]
        check("长截图双击文字再编辑（原位替换）", _okT)
        # 文字/附言软换行落字一致：浮层窄视口断的行，提交后逐行保留（同排版引擎）
        _cv.cur_tool = "text"
        _press3(10, 150)
        _te = _cv.text_editor
        _long_t = "甲" * 60                                # 单段超长：必撞画布右缘软换行
        _te.setPlainText(_long_t)
        _selE = _cv.sel or QRect(0, 0, _cv.width(), _cv.height())
        _availE = max(48, _selE.right() - _te.x() + 1)
        _expect = _cv._layout_lines(_long_t, _te.font(), _availE)
        _cv.commit_text_editor()
        _okW = len(_expect) > 1 and _cv.stamps[-1]["lines"] == _expect   # 拆行且与浮层排版逐行一致
        _cv.cur_tool = "num"
        _press3(10, 170)                                   # 落标
        _te2 = _cv.text_edit
        _long_n = "乙" * 60
        _te2.setPlainText(_long_n)
        _availN = max(48, _selE.right() - _te2.x() + 1)
        _expectN = _cv._layout_lines(_long_n, _te2.font(), _availN)
        _cv.commit_text_input()
        _okW = _okW and _cv._nums[-1]["text"].split("\n") == _expectN
        check("长截图文字/附言所见即所得（软换行逐行保留·同排版引擎）", _okW)
        _cv.undo()                                         # 撤序号（含圆标附言）
        _cv.undo()                                         # 撤文字：恢复 finish 用例的 3 对象基线
        # 文字浮层抗祖先 QSS：LongEditor 挂 BASE_QSS（QTextEdit 全局 13px+padding:7/10+border），
        # 浮层 stylesheet 必须全量压过——pt 字号生效、零 padding、preedit 折行也不滚首行
        _cv.show_text_editor(_QPt3(10, 130))
        _te3 = _cv.text_editor
        from PySide6.QtGui import QInputMethodEvent as _QIME56
        _pre56 = "jian'si'de'fu'jia'shi"
        _ev56 = _QIME56(_pre56, [_QIME56.Attribute(_QIME56.Cursor, len(_pre56), 1, None)])
        app.sendEvent(_te3, _ev56)
        app.processEvents()
        _ds3 = _te3.document().documentLayout().documentSize()
        _okQ = _te3.font().pointSize() > 0 \
            and _te3.viewport().width() == _te3.width() \
            and _te3.verticalScrollBar().value() == 0 \
            and _ds3.height() <= _te3.viewport().height()
        _cv.cancel_text_editor()                          # 丢弃浮层：不动 3 对象基线
        check("长图文字浮层抗祖先QSS（pt字号生效·零padding·preedit不滚首行）", _okQ)
        # 对象化交互对齐截图侧：select 拖动握拳/8向缩放/Del/右键删/×手柄/右键失焦落字
        from PySide6.QtGui import QKeyEvent as _QKE3
        _cv2 = _LE.LongCanvas(np.zeros((400, 600, 3), dtype=np.uint8), 648)   # k=1.0：事件坐标＝图坐标

        def _p4(x, y):
            _cv2.mousePressEvent(_QME3(_QEv3.Type.MouseButtonPress, _QPt3(x, y), _QPt3(x, y),
                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))

        def _m4(x, y):
            _cv2.mouseMoveEvent(_QME3(_QEv3.Type.MouseMove, _QPt3(x, y), _QPt3(x, y),
                                      Qt.NoButton, Qt.LeftButton, Qt.NoModifier))

        def _r4(x, y):
            _cv2.mouseReleaseEvent(_QME3(_QEv3.Type.MouseButtonRelease, _QPt3(x, y), _QPt3(x, y),
                                         Qt.LeftButton, Qt.NoButton, Qt.NoModifier))

        def _pr4(x, y):
            _cv2.mousePressEvent(_QME3(_QEv3.Type.MouseButtonPress, _QPt3(x, y), _QPt3(x, y),
                                       Qt.RightButton, Qt.RightButton, Qt.NoModifier))

        _cv2.cur_tool = "rect"
        _p4(60, 60); _m4(180, 140); _r4(180, 140)            # 造矩形（对象基线）
        _okD = len(_cv2.stamps) == 1 and _cv2.stamps[0]["kind"] == "rect"
        _cv2.cur_tool = "select"
        _p4(60, 100)                                         # 按左边框＝选中拖动（十字移动）
        _okD = _okD and _cv2._drag_shape == 0 and _cv2.sel_stamp == ("shape", 0) \
            and _cv2.cursor().shape() == Qt.SizeAllCursor
        _m4(100, 121); _r4(100, 121)
        _okD = _okD and _cv2._drag_shape is None and _cv2.cursor().shape() == Qt.ArrowCursor \
            and _cv2.stamps[0]["r"].left() == 100 and _cv2.stamps[0]["r"].top() == 81
        _hb = _cv2._stamp_handles()                          # 选中态保留：8 向手柄亮相
        _okD = _okD and len(_hb) == 8
        _br2 = _hb["br"]
        _p4(_br2.x(), _br2.y())                              # 右下手柄＝缩放（十字移动）
        _okD = _okD and _cv2._rs is not None and _cv2.cursor().shape() == Qt.SizeAllCursor
        _m4(_br2.x() + 30, _br2.y() + 20); _r4(_br2.x() + 30, _br2.y() + 20)
        _okD = _okD and _cv2._rs is None and _cv2.stamps[0]["r"].width() == 151
        _cv2.keyPressEvent(_QKE3(_QEv3.Type.KeyPress, Qt.Key_Delete, Qt.NoModifier))
        _okD = _okD and len(_cv2.stamps) == 0 and _cv2.sel_stamp is None   # Del 删除选中
        _cv2.cur_tool = "num"
        _p4(300, 100); _r4(300, 100)                         # 落标+附言浮层
        _okN = len(_cv2._nums) == 1 and _cv2.text_edit is not None
        _cv2.commit_text_input()                             # 空附言落字
        _p4(300, 100)                                        # 按圆标＝选中拖动（十字移动）
        _okN = _okN and _cv2._drag_num == 0 and _cv2.cursor().shape() == Qt.SizeAllCursor
        _m4(320, 130); _r4(320, 130)
        _okN = _okN and _cv2._nums[0]["pt"] == _QPt3(320, 130)
        _dx = _cv2._num_del_pt(_cv2._nums[0])
        _p4(_dx.x(), _dx.y())                                # 点 × 手柄＝删除
        _okN = _okN and len(_cv2._nums) == 0
        _cv2.cur_tool = "rect"
        _p4(400, 200); _m4(460, 260); _r4(460, 260)
        _pr4(430, 200)                                       # 右键按在 rect 顶边＝删除（同截图侧）
        _okR = len(_cv2.stamps) == 0
        _cv2.cur_tool = "text"
        _p4(100, 300)                                        # 开文字浮层
        _cv2.text_editor.setPlainText("对齐")
        _pr4(500, 350)                                       # 右键＝失焦落字：浮层收起且不删刚落文字
        _okR = _okR and _cv2.text_editor is None and len(_cv2.stamps) == 1 \
            and _cv2.stamps[0]["kind"] == "text"
        check("长截图编辑器对象化对齐截图（select拖动十字/8向缩放/Del/右键删/×手柄/右键失焦落字）",
              _okD and _okN and _okR)
        check("长截图工具条含选择工具（与截图侧同一序列）", "select" in _led._tool_btns)
        # 失焦对齐（同截图侧）：绘制工具点空白＝旧选中即失焦
        _cv2.cur_tool = "rect"
        _p4(60, 60); _m4(180, 140); _r4(180, 140)      # 再造矩形（stamps[1]，[0]是上文落的文字）
        _cv2.cur_tool = "select"
        _p4(60, 100); _r4(60, 100)                     # 按左边框＝选中
        _okB2 = _cv2.sel_stamp == ("shape", 1)
        _cv2.cur_tool = "ellipse"
        _p4(500, 350)                                  # ellipse 工具点空白＝起笔即失焦
        _okB2 = _okB2 and _cv2.sel_stamp is None
        _cv2.stroke_pts = []
        _cv2._drawing = False                          # 收掉零面积起笔（起笔不 push_undo，release 才落章——手动收笔即净零）
        check("长截图绘制工具点空白＝旧选中失焦（同截图侧）", _okB2)
        # R74 交互对齐截图侧终稿：文字/序号无选中态·箭头端点双手柄·沿线命中·全工具 hover
        _cv2.cur_tool = "select"
        _p4(105, 305); _r4(105, 305)                     # 按文字＝选中（text r 命中区）
        _ok74 = _cv2.sel_stamp == ("shape", 0) and not _cv2._sel_scalable() \
            and _cv2._stamp_handles() == {}              # 文字无选中框、无缩放手柄
        _cv2.cur_tool = "arrow"
        _ai74 = len(_cv2.stamps)                         # 箭头落章后的索引（不依赖前文残留计数）
        _p4(300, 300); _m4(380, 320); _r4(380, 320)      # 落箭头
        _cv2.cur_tool = "select"
        _p4(340, 310); _r4(340, 310)                     # 按线段＝选中
        _ok74 = _ok74 and _cv2.sel_stamp == ("shape", _ai74) \
            and set(_cv2._stamp_handles().keys()) == {"a", "m", "b"}   # R100：classic＝2 端点柄＋中点拉弯柄
        _m4(380, 320)                                    # hover b 端点＝十字（无方向光标兑底）
        _ok74 = _ok74 and _cv2.cursor().shape() == Qt.SizeAllCursor
        _p4(380, 320); _m4(400, 290); _r4(400, 290)      # 拖 b 端点＝直接改向
        _ok74 = _ok74 and _cv2.stamps[_ai74]["b"] == _QPt3(400, 290) \
            and _cv2.stamps[_ai74]["a"] == _QPt3(300, 300)
        _el74 = {"kind": "ellipse", "r": QRect(40, 400, 160, 90), "color": "#ffffff", "w": 3}
        _p74 = _QPt3(round(120 + 80 * 0.7071), round(445 + 45 * 0.7071))   # 45° 弧点 (177,477)
        _ok74 = _ok74 and _cv2._hit_one_shape(_el74, _p74)           # 斜弧命中（旧矩形环不中）
        _ok74 = _ok74 and not _cv2._hit_one_shape(_el74, _QPt3(120, 445))    # 圆心不命中
        _ar74 = {"kind": "arrow", "a": _QPt3(300, 300), "b": _QPt3(380, 320),
                 "color": "#ffffff", "w": 3}
        _ok74 = _ok74 and _cv2._hit_one_shape(_ar74, _QPt3(338, 318))        # 离线 ~8px：放宽后中
        _ok74 = _ok74 and not _cv2._hit_one_shape(_ar74, _QPt3(338, 323))    # 离线 ~13px：仍不中
        _cv2.cur_tool = "rect"
        _m4(120, 60)                                     # rect 工具 hover 已有矩形边框＝十字
        _ok74 = _ok74 and _cv2.cursor().shape() == Qt.SizeAllCursor
        _cv2.cur_tool = "pen"
        _m4(120, 60)                                     # pen 不可拖但属绘制类＝纯十字（R84）
        _ok74 = _ok74 and _cv2.cursor().shape() == Qt.CrossCursor
        _cv2.cur_tool = "text"
        _m4(105, 305)                                    # text 工具 hover 已有文字＝十字
        _ok74 = _ok74 and _cv2.cursor().shape() == Qt.SizeAllCursor
        _cv2.cur_tool = "num"
        _p4(200, 400); _r4(200, 400); _cv2.commit_text_input()     # 落标
        _m4(200, 400)
        _ok74 = _ok74 and _cv2.cursor().shape() == Qt.SizeAllCursor          # hover 圆标＝十字
        _dx74 = _cv2._num_del_pt(_cv2._nums[0])
        _m4(_dx74.x(), _dx74.y())
        _ok74 = _ok74 and _cv2.cursor().shape() == Qt.PointingHandCursor     # hover ×钮＝手型
        _p4(_dx74.x(), _dx74.y())                        # ×删：恢复 _nums 空基线
        _ok74 = _ok74 and len(_cv2._nums) == 0
        check("R74 长图对齐截图终稿（文字序号无选中态·箭头端点柄+R100中点拉弯柄·沿线命中·全工具hover）", _ok74)
        # R84 长图侧光标随工具直觉化（与截图侧同一映射）：绘制/落标类空白＝纯十字、文字＝I 型
        _okL84 = True
        for _toolL, _curL, _yL in (("rect", Qt.CrossCursor, 55), ("ellipse", Qt.CrossCursor, 70),
                                   ("arrow", Qt.CrossCursor, 85), ("pen", Qt.CrossCursor, 100),
                                   ("marker", Qt.CrossCursor, 115), ("mosaic", Qt.CrossCursor, 130),
                                   ("high", Qt.CrossCursor, 145), ("num", Qt.CrossCursor, 160),
                                   ("text", Qt.IBeamCursor, 175), ("select", Qt.ArrowCursor, 190)):
            _cv2.cur_tool = _toolL
            _m4(500, _yL)
            _okL84 = _okL84 and _cv2.cursor().shape() == _curL
        check("R84 长图光标随工具（绘制纯十字/文字I型/select箭头）", _okL84)
        # R75 渲染级锁：选中必须真画出绿框——paintEvent 的 Python 异常被 Qt 吞成控制台打印，
        # 逻辑断言（handles/光标）全绿也证明不了上屏（R75 根因：longedit 缺 QRectF 导入，R59 起选中即炸）
        import capture as _cap75
        _g75 = QColor(_cap75.SEL_GREEN)

        def _green75():
            _im = QImage(_cv2.size(), QImage.Format_ARGB32)
            _im.fill(QColor("#f5f5f5"))
            _cv2.render(_im)                           # 走真实 paintEvent
            _n = 0
            for _yy in range(_im.height()):
                for _xx in range(_im.width()):
                    _c = _im.pixelColor(_xx, _yy)
                    if abs(_c.red() - _g75.red()) < 40 and abs(_c.green() - _g75.green()) < 40 \
                            and abs(_c.blue() - _g75.blue()) < 40:
                        _n += 1
            return _n

        _cv2.cur_tool = "select"
        _p4(120, 60); _r4(120, 60)                       # 按 rect 顶边＝选中
        _ok75 = _cv2.sel_stamp == ("shape", 1) and _green75() > 0      # 可缩放→有绿框
        _p4(105, 305); _r4(105, 305)                     # 按文字＝选中
        _ok75 = _ok75 and _cv2.sel_stamp == ("shape", 0) and _green75() == 0   # 文字→无绿框
        check("R75 长图选中态渲染锁（rect画出绿框/文字无框）", _ok75)
        # R76 事件分发前提锁：悬浮 hover 靠 mouseTracking——冒烟直调 mouseMoveEvent 绕过 Qt 分发，
        # 分支断言全绿也证明不了真机悬浮能收到事件（R76 根因：LongCanvas 从未开 mouseTracking）
        check("R76 长图画布 mouseTracking（悬浮 hover 事件分发前提）", _cv2.hasMouseTracking())
        # R78 长图侧：悬浮即删（免选中）+ 文字按住拖拽——Backspace/Delete 删鼠标正悬浮的元素
        _r78b = QRect(_cv2.stamps[0]["r"])
        _p4(105, 305); _m4(145, 345); _r4(145, 345)    # select 工具按住文字拖拽
        _ok78b = _cv2.stamps[0]["r"].topLeft() == _r78b.topLeft() + _QPt3(40, 40)   # 文字平移
        _cv2.sel_stamp = None                          # 未选中前提：悬浮即删
        _cv2.cur_tool = "num"
        _p4(200, 400); _r4(200, 400); _cv2.commit_text_input()     # 落标
        _m4(200, 400)                                  # hover 圆标（mouseMove 记录 _hover_pt）
        _cv2.keyPressEvent(_QKE3(_QEv3.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78b = _ok78b and len(_cv2._nums) == 0       # 序号悬浮即删
        _cv2.stamps.append({"kind": "ellipse", "r": QRect(40, 300, 160, 90), "color": "#ffffff", "w": 3})
        _m4(round(120 + 80 * 0.7071), round(345 + 45 * 0.7071))    # hover 椭圆斜弧 (177,377)
        _cv2.keyPressEvent(_QKE3(_QEv3.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78b = _ok78b and [_s78["kind"] for _s78 in _cv2.stamps] == ["text", "rect", "arrow"]
        _m4(120, 60)                                   # hover rect 顶边
        _cv2.keyPressEvent(_QKE3(_QEv3.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78b = _ok78b and [_s78["kind"] for _s78 in _cv2.stamps] == ["text", "arrow"]
        _m4(145, 345)                                  # hover 文字（拖拽后新位）
        _cv2.keyPressEvent(_QKE3(_QEv3.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78b = _ok78b and [_s78["kind"] for _s78 in _cv2.stamps] == ["arrow"]
        _m4(350, 297)                                  # hover 箭头线段（距线 ~2px）
        _cv2.keyPressEvent(_QKE3(_QEv3.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78b = _ok78b and len(_cv2.stamps) == 0      # 箭头悬浮即删
        _cv2.undo()
        _ok78b = _ok78b and len(_cv2.stamps) == 1      # 悬浮即删可撤销
        check("R78 长图悬浮即删（序号/椭圆/矩形/文字/箭头免选中）+ 文字按住拖拽", _ok78b)
        # R79 长图侧文字再编辑防重影（渲染级锁）：编辑期间底层 stamp 让位，提交/取消恢复
        from PySide6.QtGui import QRegion as _QRgn79
        from PySide6.QtWidgets import QWidget as _QW79

        def _red79b():
            _im = QImage(_cv2.size(), QImage.Format_ARGB32)
            _im.fill(QColor("#f5f5f5"))
            # 关掉 DrawChildren：只验 canvas paintEvent 的底层让位，不把浮层文字数进来
            _cv2.render(_im, _QPt3(0, 0), _QRgn79(), _QW79.RenderFlag.DrawWindowBackground)
            _n = 0
            for _yy in range(_im.height()):
                for _xx in range(_im.width()):
                    _c = _im.pixelColor(_xx, _yy)
                    if _c.alpha() > 200 and _c.red() > 200 and _c.green() < 90 and _c.blue() < 90:
                        _n += 1
            return _n

        _cv2.stamps.clear()                            # 清场：R78 留下的 arrow 是默认红色，会干扰红字计数
        _cv2.cur_tool = "text"
        _cv2.cur_color = "#ff0000"
        _cv2.show_text_editor(_QPt3(100, 300))
        _cv2.text_editor.setPlainText("影")
        _cv2.commit_text_editor()                      # 落红字（stamps 追加）
        _ti79 = len(_cv2.stamps) - 1
        _ok79b = _cv2.stamps[_ti79]["kind"] == "text" and _red79b() > 0
        _s79 = _cv2.stamps[_ti79]
        _cv2.show_text_editor(_QPt3(_s79["r"].left(), _s79["r"].top()),
                              preset="\n".join(_s79["lines"]), idx=_ti79)   # 双击再编辑等价入口
        _ok79b = _ok79b and _cv2.text_editor is not None and _red79b() == 0  # 底层让位＝无重影
        _cv2.commit_text_editor()                      # 提交：原位替换，红回来
        _ok79b = _ok79b and _red79b() > 0 and len(_cv2.stamps) == _ti79 + 1
        _s79 = _cv2.stamps[_ti79]
        _cv2.show_text_editor(_QPt3(_s79["r"].left(), _s79["r"].top()),
                              preset="\n".join(_s79["lines"]), idx=_ti79)
        _ok79b = _ok79b and _red79b() == 0
        _cv2.cancel_text_editor()                      # Esc 取消：原文恢复上屏
        _ok79b = _ok79b and _red79b() > 0
        check("R79 长图文字再编辑防重影（编辑期底层让位·提交/取消恢复）", _ok79b)
        # R122 失焦击语义（真机事件序列锁，与截图侧统一）：FocusOut 同步提交→
        # 失焦那击 had_edit=False→同一击继续走工具分支＝落旧字同时直接落新标/开新浮层
        from PySide6.QtGui import QFocusEvent as _QFE81
        _cv2.cur_tool = "num"
        _p4(200, 200); _r4(200, 200)                   # 落标+附言浮层
        _ok81 = len(_cv2._nums) == 1 and _cv2.text_edit is not None
        _cv2.text_edit.setPlainText("注")
        app.sendEvent(_cv2.text_edit, _QFE81(_QEv3.Type.FocusOut))   # 真机：点击瞬间焦点先离开浮层
        _ok81 = _ok81 and _cv2.text_edit is None and _cv2._nums[0]["text"] == "注"  # 同步提交（R81 延迟回归＝仍挂着）
        _p4(300, 200); _r4(300, 200)                   # 失焦击＝落旧字同时直接落新标（截图侧同语义）
        _ok81 = _ok81 and len(_cv2._nums) == 2 and _cv2.text_edit is not None
        _cv2.commit_text_input()
        _cv2.cur_tool = "text"
        _p4(100, 350)                                  # 开文字浮层（避开 R79 红字命中区，stamps 已有 1 个）
        _ok81 = _ok81 and _cv2.text_editor is not None
        _cv2.text_editor.setPlainText("甲")
        app.sendEvent(_cv2.text_editor, _QFE81(_QEv3.Type.FocusOut))
        _ok81 = _ok81 and _cv2.text_editor is None and len(_cv2.stamps) == 2  # 同步提交落“甲”
        _p4(300, 320); _r4(300, 320)                   # 失焦击＝落旧字同时直接开新浮层
        _ok81 = _ok81 and _cv2.text_editor is not None
        _cv2.cancel_text_editor()
        _cv2.stamps.pop()                              # 清"甲"：回 R79 的 1 字基线
        check("R122 长图失焦击＝落旧字+同击落新标/开新浮层（FocusOut 同步提交，与截图侧统一）", _ok81)
        # R99 长图侧：num/text 工具按在已有对象上＝拖动它（与截图侧同步泛化），空白随工具不破
        _ptL99 = _QPt3(420, 300)
        while _cv2._hit_shape(_ptL99) is not None or _cv2._hit_num(_ptL99) is not None:
            _ptL99 -= _QPt3(0, 20)                     # 防撞已有对象：修复后 num/text 按对象＝拖动
        _cv2.cur_tool = "text"
        _cv2.show_text_editor(_ptL99)                  # 直接开浮层（绕开 press 命中干扰）
        _cv2.text_editor.setPlainText("久久")
        _cv2.commit_text_editor()
        _tiL99 = len(_cv2.stamps) - 1
        _rL99 = QRect(_cv2.stamps[_tiL99]["r"])
        _hxL, _hyL = _rL99.center().x(), _rL99.center().y()
        _ptN99 = _QPt3(420, 340)
        while _cv2._hit_shape(_ptN99) is not None or _cv2._hit_num(_ptN99) is not None:
            _ptN99 -= _QPt3(0, 20)
        _cv2.cur_tool = "num"
        _p4(_ptN99.x(), _ptN99.y()); _r4(_ptN99.x(), _ptN99.y())   # 落标
        _cv2.commit_text_input()
        _nnL99 = len(_cv2._nums)
        _okL99 = _cv2.stamps[_tiL99]["kind"] == "text" and _nnL99 >= 1
        _p4(_hxL, _hyL)                                # num 工具按住文字＝拖动，不冒新标
        _okL99 = _okL99 and _cv2._drag_shape == _tiL99 and _cv2.text_edit is None
        _m4(_hxL + 24, _hyL + 12); _r4(_hxL + 24, _hyL + 12)
        _okL99 = _okL99 and len(_cv2._nums) == _nnL99 \
            and _cv2.stamps[_tiL99]["r"].topLeft() == _rL99.topLeft() + _QPt3(24, 12)
        _m4(_hxL + 24, _hyL + 12)                      # num 工具 hover 文字＝十字移动
        _okL99 = _okL99 and _cv2.cursor().shape() == Qt.SizeAllCursor
        _cv2.cur_tool = "text"
        _npL99 = _QPt3(_cv2._nums[-1]["pt"])
        _p4(_npL99.x(), _npL99.y())                    # text 工具按住圆标＝拖序号，不开浮层
        _okL99 = _okL99 and _cv2._drag_num == _nnL99 - 1 and _cv2.text_editor is None
        _m4(_npL99.x() + 18, _npL99.y()); _r4(_npL99.x() + 18, _npL99.y())
        _okL99 = _okL99 and _cv2._nums[-1]["pt"] == _npL99 + _QPt3(18, 0)
        _m4(_cv2._nums[-1]["pt"].x(), _cv2._nums[-1]["pt"].y())    # text 工具 hover 圆标＝十字
        _okL99 = _okL99 and _cv2.cursor().shape() == Qt.SizeAllCursor
        _cv2.cur_tool = "num"
        _p4(_ptN99.x(), _ptN99.y() - 60)               # 回归：num 空白仍落新标
        _okL99 = _okL99 and _cv2.text_edit is not None and len(_cv2._nums) == _nnL99 + 1
        _cv2.cancel_text_edit()
        check("R99 长图对象优先拖动（num 按文字＝拖动·text 按圆标＝拖动）", _okL99)
        del _cv2.stamps[_tiL99]                        # 清场：回 R79 的 1 字基线
        _cv2._nums.clear()
        _cv2.sel_stamp = None
        _cv2.cur_tool = "select"
        # R100 长图侧：序号样式/箭头拉曲线/聚光椭圆（本地覆写链与截图侧同语义）；R104：单一样式恒三点
        from PySide6.QtGui import QColor as _QC100L, QPainter as _QP100L
        _cv2.cur_num_style = "uroman"; _cv2.cur_num_start = 2
        _ok100L = (_cv2._num_label(0), _cv2._num_label(1)) == ("II", "III")
        _cv2.cur_num_style = "num"; _cv2.cur_num_start = 1
        _ok100L = _ok100L and _cv2.cur_arrow_style == "line"          # R134：默认值复活（同截图侧 R132），线箭默认
        _cv2.stamps.append({"kind": "arrow", "a": _QPt3(60, 60), "b": _QPt3(180, 60),
                            "w": 2, "color": "#ef4444"})
        _aiL100 = len(_cv2.stamps) - 1
        _cv2.sel_stamp = ("shape", _aiL100)
        _ok100L = _ok100L and set(_cv2._stamp_handles()) == {"a", "m", "b"}   # R104：恒三点
        _cv2._rs = _cv2._begin_stamp_resize("m")
        _cv2._apply_stamp_resize(_QPt3(120, 110))          # 中点下拉 → 弯（k=1 容差 6）
        _ok100L = _ok100L and _cv2.stamps[_aiL100].get("c") == _QPt3(120, 110)
        _cv2._rs = None
        _ok100L = _ok100L and _cv2._hit_one_shape(_cv2.stamps[_aiL100], _QPt3(120, 85))   # 弧点命中
        _ok100L = _ok100L and not _cv2._hit_one_shape(_cv2.stamps[_aiL100], _QPt3(120, 60))  # 原直线不中
        _cv2._rs = _cv2._begin_stamp_resize("m")
        _cv2._apply_stamp_resize(_QPt3(121, 61))           # 拖回中点 ≤6 → 复原直线
        _ok100L = _ok100L and "c" not in _cv2.stamps[_aiL100]
        _cv2._rs = None
        _cv2.stamps[_aiL100]["style"] = "modern"            # 存量 style 键：忽略，仍按经典三点
        _ok100L = _ok100L and set(_cv2._stamp_handles()) == {"a", "m", "b"}
        _hiL100 = {"kind": "high", "r": QRect(300, 40, 120, 80), "shape": "ellipse"}
        _ok100L = _ok100L and _cv2._hit_one_shape(_hiL100, _QPt3(360, 80))        # 中心命中（填充区）
        _ok100L = _ok100L and not _cv2._hit_one_shape(_hiL100, _QPt3(305, 45))    # 界内角点：椭圆外不中
        _hiL100["shape"] = "rect"
        _ok100L = _ok100L and _cv2._hit_one_shape(_hiL100, _QPt3(305, 45))        # 同点 rect 命中
        _pmL100 = QPixmap(_cv2._img_w, _cv2._img_h); _pmL100.fill(_QC100L("#ffffff"))
        _ptL100 = _QP100L(_pmL100)
        _cv2._draw_highlight(_ptL100, QRect(300, 40, 120, 80), "ellipse")
        _ptL100.end()
        _imL100 = _pmL100.toImage()
        _ok100L = _ok100L and _imL100.pixelColor(360, 80).red() > 240      # 椭圆中心不被压暗
        _ok100L = _ok100L and _imL100.pixelColor(305, 45).red() < 200      # 界内角点被压暗
        del _cv2.stamps[_aiL100]                        # 清场：回 R79 的 1 字基线
        _cv2.sel_stamp = None
        check("R100 长图序号样式/箭头拉曲线/聚光椭圆（双侧同语义）", _ok100L)
        # R134 长图侧：辐条范式同步截图侧——属性钮/色点/粗细钮全部退役迁入 capture.ToolPropsPop；
        # 显隐断言用 isHidden 自标记（离屏下祖先窗不可见连累 isVisible 恒 False）
        _ok101L = isinstance(_led.props_pop, _cap75.ToolPropsPop) \
            and not hasattr(_led, "_btn_num_style") and not hasattr(_led, "_btn_high_shape") \
            and not hasattr(_led, "_dots") and not hasattr(_led, "_size_btns")
        _popL101 = _led.props_pop
        _led.set_tool("num")
        _ok101L = _ok101L and not _popL101.isHidden() \
            and not _popL101._btn_num_style.isHidden() and not _popL101._btn_num_start.isHidden() \
            and _popL101._btn_high_shape.isHidden()
        _led.set_tool("arrow")
        _ok101L = _ok101L and not _popL101._btn_arrow_style.isHidden() \
            and _popL101._btn_num_style.isHidden()             # R134：箭头样式钮长图侧补位
        _led.set_tool("high")
        _ok101L = _ok101L and not _popL101._btn_high_shape.isHidden() \
            and _popL101._btn_num_style.isHidden() \
            and _popL101._size_dots[0][1].isHidden()           # 聚光无粗细/颜色区
        _led.set_tool("select")
        _ok101L = _ok101L and _popL101.isHidden()              # 非标注工具即收
        _ok101L = _ok101L and "rgba(59,130,246,48)" in _popL101._OPT_QSS \
            and "border:none" in _popL101._OPT_QSS   # 蓝族选项样式同族（R141 起无描边）
        check("R134 长图辐条范式（属性钮退役迁 ToolPropsPop/选项随工具/箭头样式补位）", _ok101L)
        # R102 长图侧：聚光近点单击不落对象（去小点）＝遮罩暗度逐级加深，满黑后回默认档；
        # 顺带锁 R100 遗漏修复：新画聚光须带 shape 键（椭圆形态钮对 live 路径生效）
        _cv2.cur_tool = "high"; _cv2.cur_high_shape = "ellipse"
        _ok102L = _cv2._hit_shape(_QPt3(300, 40)) is None and _cv2._hit_shape(_QPt3(60, 200)) is None
        _p4(300, 40); _m4(420, 120); _r4(420, 120)
        _hiL102 = len(_cv2.stamps) - 1
        _nL102 = len(_cv2.stamps)
        _ok102L = _ok102L and _cv2.stamps[_hiL102]["kind"] == "high" \
            and _cv2.stamps[_hiL102].get("shape") == "ellipse" \
            and _cv2.stamps[_hiL102].get("dim") == 148 and _cv2.cur_high_dim == 148
        _p4(60, 200); _m4(62, 202); _r4(62, 202)          # 近点单击（2px 抖动）
        _ok102L = _ok102L and len(_cv2.stamps) == _nL102 \
            and _cv2.stamps[_hiL102]["dim"] == 195 and _cv2.cur_high_dim == 195
        _p4(60, 200); _r4(60, 200)                        # 原地单击也循环
        _ok102L = _ok102L and _cv2.stamps[_hiL102]["dim"] == 230
        _p4(60, 200); _r4(60, 200)
        _ok102L = _ok102L and _cv2.stamps[_hiL102]["dim"] == 255   # 满黑
        _p4(60, 200); _r4(60, 200)
        _ok102L = _ok102L and _cv2.stamps[_hiL102]["dim"] == 148 \
            and _cv2.cur_high_dim == 148                           # 回默认档
        _cv2.undo()
        _ok102L = _ok102L and _cv2.stamps[_hiL102]["dim"] == 255   # 一步撤销回满黑
        del _cv2.stamps[_hiL102]                          # 清场：回 R79 的 1 字基线
        _cv2.cur_high_dim = 148; _cv2.cur_high_shape = "rect"; _cv2.cur_tool = "select"
        _cv2.update()
        check("R102 长图聚光暗度循环（去小点/逐级加深/满黑回默认/可撤销/shape入live）", _ok102L)
        _led.finish()
        _okF = _edout.get("n") == 3 and not _led.isVisible() \
            and os.path.isfile(_edout.get("path", ""))
        if _okF:
            _chk = QImage(_edout["path"])
            _okF = _chk.width() == 240 and _chk.height() == 160
            os.remove(_edout["path"])
            try:
                os.rmdir(_tmp_ed)
            except OSError:
                pass
        check("长截图编辑器完成落盘（回调带屏数）", _okF)
    except Exception as e:
        print(f"SKIP 长截图编辑器：{e}")
    # 16.3b R83 输入法跟随：快照/跟随两函数离屏安全执行（真实 HKL 切换经真机探针验证）
    try:
        C.snapshot_input_locale()
        C.follow_system_input_locale()
        _ok83 = True
    except Exception:
        _ok83 = False
    check("R83 输入法跟随（快照/跟随离屏安全执行）", _ok83)
    # 16.4 长截图预览浮窗贴边换侧：右侧贴屏幕边翻到选区左侧，两侧都放不下才叠右缘
    try:
        import longshot as _LS

        class _FakeScr:
            def __init__(self, g):
                self._g = g

            def geometry(self):
                return self._g

        _scr4 = QRect(0, 0, 1200, 900)
        _pv_mid = _LS.LongPreview(_FakeScr(_scr4), QRect(400, 100, 200, 200))
        _okP1 = _pv_mid.x() == QRect(400, 100, 200, 200).right() + 14      # 居中选区：预览在右侧
        _sel_edge = QRect(1000, 100, 184, 200)
        _pv_edge = _LS.LongPreview(_FakeScr(_scr4), QRect(_sel_edge))
        _okP2 = _pv_edge.x() + _pv_edge.width() <= _sel_edge.left()        # 贴右缘：翻到左侧不叠选区
        _pv_full = _LS.LongPreview(_FakeScr(_scr4), None)                  # 整屏选区：两侧都放不下
        _okP3 = _pv_full.x() == _scr4.right() - _LS.LongPreview.PW - 8
        for _pv in (_pv_mid, _pv_edge, _pv_full):
            _pv.close()
            _pv.deleteLater()
        check("长截图预览浮窗贴边自动换侧（右贴边翻左侧·整屏叠右缘）", _okP1 and _okP2 and _okP3)
    except Exception as e:
        print(f"SKIP 长截图预览浮窗：{e}")
    # 16.5 标注工具：荧光笔透明不累积 + 序号即落/回车确认/点击只确认/右键删除自动重排
    try:
        from PySide6.QtCore import QPoint as _QPt, QEvent as _QEv
        from PySide6.QtGui import (QImage as _QImg, QPainter as _QPainter,
                                   QMouseEvent as _QME, QPixmap as _QPm)
        _ovp = _QPm(400, 300)
        _ovp.fill(Qt.black)
        _ov = ShotOverlay(_ovp, screen=_QGA.primaryScreen())
        _ok_sz = _ov.cur_size == 2                    # 粗细默认「细」：须与工具条枚举 (2,4,7) 匹配才有选中项
        _ov.phase = "edit"
        _ov.sel = QRect(0, 0, 400, 300)
        _ov.cur_color = "#ff0000"
        _ov.cur_tool = "marker"
        _ov.push_undo()
        _mpts = [_QPt(20 + i, 30) for i in range(40)]
        _ov.draw_stroke(_mpts)
        _sx, _sy = round(40 * _ov.k), round(30 * _ov.k)
        _c1 = _ov.anno.toImage().pixelColor(_sx, _sy).alpha()
        _ov._rerender_anno()                           # 整层重画幂等（对象引擎基础）
        _ov.draw_stroke(_mpts)                         # 重复绘制不得累积
        _c2 = _ov.anno.toImage().pixelColor(_sx, _sy).alpha()
        _ov.cur_tool = "mosaic"                        # 马赛克：网格随粗细（细 8/中 12/粗 18），默认细档 8px
        _ov.stroke_pts = [_QPt(300 + i * 3, 250) for i in range(8)]
        _ms = _ov._live_stamp()
        _okm = (_ms is not None and _ms["bs"] == _ov._mosaic_bs() == 8
                and len(_ms["tiles"]) >= 2
                and all(c.startswith("#") for _, _, c in _ms["tiles"]))   # 21px 横扫去重后≥2 格、均色带色号
        _bsm = []
        for _sz in (2, 4, 7):                          # 三档映射锁进冒烟：细 8/中 12/粗 18
            _ov.cur_size = _sz
            _bsm.append(_ov._mosaic_bs())
        _ov.cur_size = 2                               # 还原默认细档
        _okm = _okm and _bsm == [8, 12, 18]
        from PySide6.QtGui import QPainterPath as _QPPth
        _sp = _ov._smooth_path([_QPt(0, 0), _QPt(50, 40), _QPt(100, 0)])    # 折角输入
        _oksm = any(_sp.elementAt(_i).type == _QPPth.ElementType.CurveToElement
                    for _i in range(_sp.elementCount()))
        _spl = _ov._smooth_path([_QPt(0, 0), _QPt(50, 0), _QPt(100, 0)])    # 共线输入
        _oksm = _oksm and _spl.boundingRect().height() <= 1e-6   # 曲线拟合不外弓
        _ov.cur_tool = "num"
        _ov.show_num_input(_QPt(100, 100))
        _ed_ok = _ov.text_edit is not None and isinstance(_ov.text_edit, QTextEdit) \
            and _ov.text_edit.toPlainText() == ""          # 所见即所得浮层留空待输
        _ok1 = len(_ov._nums) == 1 and _ov._nums[0]["pt"] == _QPt(100, 100) \
            and _ov._nums[0]["text"] == "" and _ov._nums[0]["color"] == "#ff0000"
        _ov.text_edit.setPlainText("重")
        _ov.commit_text_input()                        # 失焦/确认＝附言落字（真实提交路径）
        _ok2 = _ov.text_edit is None and len(_ov._nums) == 1 and _ov._nums[0]["text"] == "重"

        def _press(btn, x, y):
            _ov.mousePressEvent(_QME(_QEv.Type.MouseButtonPress, _QPt(x, y), _QPt(x, y),
                                     btn, btn, Qt.NoModifier))
        _press(Qt.LeftButton, 260, 120)                # 确认后的新点击：落第二枚
        _ok3 = len(_ov._nums) == 2 and _ov._nums[1]["pt"] == _QPt(260, 120)
        _press(Qt.LeftButton, 200, 200)                # 附言框开着时点空白：只确认不新增
        _ok4 = len(_ov._nums) == 2 and _ov.text_edit is None
        _press(Qt.LeftButton, 200, 200)                # 再点一下：落第三枚
        _ok5 = len(_ov._nums) == 3
        _ov.commit_text_input()                        # 第三枚不留附言，失焦确认
        _press(Qt.RightButton, 260, 120)               # 右键圆标删除第二枚
        _ok6 = len(_ov._nums) == 2
        _press(Qt.RightButton, 200, 200)               # 右键圆标删除第三枚
        _ok7 = len(_ov._nums) == 1 and _ov._nums[0]["text"] == "重"
        _press(Qt.RightButton, 10, 10)                 # 右键空白：不误删
        _ok8 = len(_ov._nums) == 1
        _ov.undo()                                     # 撤销删除：连序号对象一起回退
        _ok9 = len(_ov._nums) == 2
        _img = _QImg(240, 80, _QImg.Format_ARGB32)
        _img.fill(0)
        _pp = _QPainter(_img)
        _ov._paint_nums(_pp, _QPt(-80, -60))           # 序号层现画：圆标+附言渲染
        _pp.end()
        _c = _img.pixelColor(20, 40)
        _bad = _c.alpha() == 255 and _c.red() > 200 and _c.green() < 80   # 圆标红色
        _nr0 = _ov._nums[0]["r"]
        _rim = _img.pixelColor(20, 40 - _nr0)                # 白描边环：描边中线恰在半径 r 处
        _okrim = _rim.red() > 230 and _rim.green() > 230 and _rim.blue() > 230
        _txt = sum(1 for _yy in range(25, 56) for _x in range(41, 111)
                   if _img.pixelColor(_x, _yy).alpha() == 255
                   and _img.pixelColor(_x, _yy).red() > 200
                   and _img.pixelColor(_x, _yy).green() < 80
                   and _img.pixelColor(_x, _yy).blue() < 80)      # 附言随圆标红色
        _dark = sum(1 for _yy in range(26, 58) for _x in range(38, 100)
                    if _img.pixelColor(_x, _yy).alpha() >= 140
                    and _img.pixelColor(_x, _yy).red() < 40
                    and _img.pixelColor(_x, _yy).green() < 40
                    and _img.pixelColor(_x, _yy).blue() < 40)   # 不透明近黑像素＝描边残留（画布透明黑须排除），应为 0

        def _release(x, y):
            _ov.mouseReleaseEvent(_QME(_QEv.Type.MouseButtonRelease, _QPt(x, y), _QPt(x, y),
                                       Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        _nst = len(_ov.undo_stack)
        _press(Qt.LeftButton, 202, 202)                # 按在圆标上＝待拖，不新增
        _okd1 = _ov._drag_num == 1 and len(_ov._nums) == 2
        _ov.mouseMoveEvent(_QME(_QEv.Type.MouseMove, _QPt(140, 138), _QPt(140, 138),
                                Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        _release(140, 138)
        _okd2 = (_ov._drag_num is None and len(_ov.undo_stack) == _nst + 1
                 and _ov._nums[1]["pt"] == _QPt(138, 136))     # 圆标+附言整体挪到位
        _press(Qt.LeftButton, 140, 138)                # 只按不拖：不占撤销步、不新增
        _okd3 = _ov._drag_num == 1 and len(_ov._nums) == 2 and len(_ov.undo_stack) == _nst + 1
        _release(140, 138)
        _okd4 = _ov._drag_num is None and len(_ov.undo_stack) == _nst + 1
        _dp = _ov._num_del_pt(_ov._nums[1])
        _press(Qt.LeftButton, _dp.x(), _dp.y())        # 点 × 手柄删除第二枚
        _okx1 = len(_ov._nums) == 1 and _ov._nums[0]["text"] == "重" \
            and len(_ov.undo_stack) == _nst + 2
        _release(60, 60)
        _ov.undo()                                     # × 删除可撤销
        _okx2 = len(_ov._nums) == 2
        # 附言所见即所得：软换行（文档零改写）、失焦落字、多行竖排渲染
        _ov.sel = QRect(0, 0, 220, 300)
        _ov.show_num_input(_QPt(30, 60))
        _ov.text_edit.setPlainText("序号附言" * 6)
        _nw = _ov.text_edit.toPlainText()
        _exp = _ov._layout_lines(_nw, _ov.text_edit.font(),
                                 max(48, _ov.sel.right() - _ov.text_edit.x() + 1))
        _tx, _ty = _ov.text_edit.x(), _ov.text_edit.y()   # 编辑器实际显示位置（确认前的“所见”）
        _okn = ("\n" not in _nw                                    # 软换行：文档零改写
                and len(_exp) >= 2 and _ov.text_edit.width() <= 190
                and _ov.text_edit.x() + _ov.text_edit.width() <= 221
                and _ov.text_edit.y() + _ov.text_edit.height() <= 301)
        _ov.commit_text_input()
        _okn = _okn and _ov._nums[-1]["text"] == "\n".join(_exp) \
            and _ov._nums[-1]["tdx"] == _tx - 30 and _ov._nums[-1]["tdy"] == _ty - 60
        _img2 = _QImg(240, 160, _QImg.Format_ARGB32)
        _img2.fill(0)
        _pp2 = _QPainter(_img2)
        _ov._paint_nums(_pp2, _QPt(-10, -20))          # 圆标→(20,40)：多行附言竖排现画
        _pp2.end()
        _txt2 = sum(1 for _yy in range(2, 80) for _xx in range(36, 200)
                    if _img2.pixelColor(_xx, _yy).alpha() == 255
                    and _img2.pixelColor(_xx, _yy).red() > 200
                    and _img2.pixelColor(_xx, _yy).green() < 80
                    and _img2.pixelColor(_xx, _yy).blue() < 80)
        _okn = _okn and _txt2 >= 6
        _img3 = _QImg(240, 160, _QImg.Format_ARGB32)   # 真所见即所得：off=None 原坐标现画
        _img3.fill(0)
        _pp3 = _QPainter(_img3)
        _ov._paint_nums(_pp3, None)                    # 附言必须落在编辑器原位（_ty 起）
        _pp3.end()
        _b1 = sum(1 for _yy in range(_ty, _ty + 26) for _xx in range(_tx - 1, _tx + 40)
                  if _img3.pixelColor(_xx, _yy).alpha() == 255
                  and _img3.pixelColor(_xx, _yy).red() > 200)
        _img4 = _QImg(240, 160, _QImg.Format_ARGB32)
        _img4.fill(0)
        _pp4 = _QPainter(_img4)
        _ov._nums[-1]["tdy"] += 100                    # 挪锚点：渲染必须跟着挪（居中算法则原地不动）
        _ov._paint_nums(_pp4, None)
        _pp4.end()
        _ov._nums[-1]["tdy"] -= 100
        _b2 = sum(1 for _yy in range(_ty, _ty + 26) for _xx in range(_tx - 1, _tx + 40)
                  if _img4.pixelColor(_xx, _yy).alpha() == 255
                  and _img4.pixelColor(_xx, _yy).red() > 200)
        _okn = _okn and _b1 >= 5 and _b2 == 0          # 原位有字+挪锚后原位无字＝渲染真由锚点驱动
        check("序号附言真所见即所得（原位落字·无描边·右缘换行）", _okn)
        check("序号×手柄删除+撤销", _okx1 and _okx2)
        check("荧光笔透明不累积（重绘幂等）", 80 <= _c1 <= 100 and _c1 == _c2)
        check("序号即落+失焦落字存附言（所见即所得）", _ed_ok and _ok1 and _ok2)
        check("确认点击不新增序号", _ok3 and _ok4 and _ok5)
        check("右键删除+自动重排+撤销", _ok6 and _ok7 and _ok8 and _ok9)
        check("序号层现画渲染+附言随色（圆标白描边·附言无黑描边）", _bad and _txt >= 3 and _dark == 0 and _okrim)
        check("序号拖动整体跟手", _okd1 and _okd2 and _okd3 and _okd4)
        check("截图粗细默认选中细", _ok_sz)
        check("马赛克网格随粗细（细8/中12/粗18·均色/去重）", _okm)
        # 马赛克笔刷：一划成片（半径覆盖+路径插值补洞），快速拖不漏格；增量缓存与全量重算一致
        _ov.cur_tool = "mosaic"
        _ov.cur_size = 2                                       # 细档 8px 网格：覆盖范围仍由笔刷半径决定
        _ov.stroke_pts = [_QPt(30, 150), _QPt(130, 150)]       # 100px 直线、仅 2 个采样点
        _tiles = _ov._mosaic_tiles(_ov.stroke_pts)
        _n1 = len(_tiles)                                       # 快照：缓存返回的是同一个成长列表
        _cols = sorted({tx for tx, ty, _c in _tiles})
        _rows = {ty for tx, ty, _c in _tiles}
        _okM = bool(_tiles) and len(_rows) >= 3                 # 半径刷头：不止 1 行网格
        _okM = _okM and _cols and _cols[-1] - _cols[0] >= 90 \
            and all(b - a <= _ov._mosaic_bs() for a, b in zip(_cols, _cols[1:]))   # 沿线无漏格
        _ov.stroke_pts.append(_QPt(160, 150))                   # 增量：追加一点
        _tiles2 = _ov._mosaic_tiles(_ov.stroke_pts)
        _okM = _okM and len(_tiles2) > _n1
        _ov._mosa_cache = None                                  # 清缓存全量重算
        _tiles3 = _ov._mosaic_tiles(_ov.stroke_pts)
        _okM = _okM and sorted(_tiles2) == sorted(_tiles3)       # 增量结果＝全量结果
        _ov.stroke_pts = None
        check("马赛克笔刷一划成片（半径覆盖·插值无漏格·增量一致）", _okM)
        # 马赛克遮挡强度：4px 棋盘（模拟文字笔画）经 12px 块均色后黑白信息必须被摧毁成中间灰
        from types import SimpleNamespace as _SNs
        _qimg = QImage(96, 48, QImage.Format_RGB888)
        for _yy in range(48):
            for _xx in range(96):
                _qimg.setPixelColor(_xx, _yy, QColor("#ffffff") if (_xx // 4 + _yy // 4) % 2 else QColor("#000000"))
        _fake = _SNs(k=1.0, base_img=_qimg, cur_size=4, _mosa_cache=None)
        _fake._mosaic_bs = lambda: ShotOverlay._mosaic_bs(_fake)
        _fake._mosaic_brush_r = lambda: ShotOverlay._mosaic_brush_r(_fake)
        _tilesC = ShotOverlay._mosaic_tiles(_fake, [_QPt(24, 24), _QPt(72, 24)])
        _okMC = len(_tilesC) >= 20 and all(
            90 <= int(_c[1:3], 16) <= 170
            and abs(int(_c[1:3], 16) - int(_c[3:5], 16)) < 15
            and abs(int(_c[3:5], 16) - int(_c[5:7], 16)) < 15
            for _, _, _c in _tilesC)     # 每格均色落中间灰＝笔画不可辨（旧 4px 块对齐棋盘＝纯黑/纯白，过不了）
        check("马赛克均色摧毁笔画（棋盘混合成灰·文字不可辨）", _okMC)
        check("画笔/荧光笔 Catmull-Rom 曲线拟合（拐点消除·共线不外弓）", _oksm)
        # 选区框条：按住拖动整体移动 + 框条/手柄双光标态
        _ov.sel = QRect(0, 0, 400, 300)                      # 基准选区（前面用例已把 sel 改窄）
        def _mve(x, y):
            _ov.mouseMoveEvent(_QME(_QEv.Type.MouseMove, _QPt(x, y), _QPt(x, y),
                                    Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        _ov.mousePressEvent(_QME(_QEv.Type.MouseButtonPress, _QPt(100, 0), _QPt(100, 0),
                                 Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        _oks = _ov._drag_sel is not None                     # 按在框条上＝进入整体移动
        _mve(120, 30)
        _ov.mouseReleaseEvent(_QME(_QEv.Type.MouseButtonRelease, _QPt(120, 30), _QPt(120, 30),
                                   Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        _oks = _oks and _ov._drag_sel is None and _ov.sel == QRect(20, 30, 400, 300)
        _mve(120, 30)                                        # 悬停框条（新手柄均 100px 外）
        _oks = _oks and _ov.cursor().shape() == Qt.SizeAllCursor
        _mve(20, 30)                                         # 悬停 tl 手柄
        _oks = _oks and _ov.cursor().shape() == Qt.SizeFDiagCursor
        check("选区框条拖动整体 + 框条/手柄光标态", _oks
              and ShotOverlay._SEL_CURSORS["bm"] == Qt.SizeVerCursor)
        _ov.close()
        _ov.deleteLater()
    except Exception as e:
        print(f"SKIP 标注：{e}")
    # 16.6 标注对象化交互：形状选中/移动/缩放/Del 删除/撤销 + 文字所见即所得 + 回车复制
    try:
        from PySide6.QtCore import QPoint as _QPt2, QEvent as _QEv2
        from PySide6.QtGui import (QMouseEvent as _QME2, QKeyEvent as _QKE2,
                                   QTextCursor as _QTC)
        _ovp2 = _QPm(400, 300)
        _ovp2.fill(Qt.black)
        _ov2 = ShotOverlay(_ovp2, screen=_QGA.primaryScreen())
        _ov2.phase = "edit"
        _ov2.sel = QRect(0, 0, 400, 300)
        _ov2.cur_tool = "rect"
        _ov2.cur_color = "#00ff00"

        def _press2(btn, x, y):
            _ov2.mousePressEvent(_QME2(_QEv2.Type.MouseButtonPress, _QPt2(x, y), _QPt2(x, y),
                                       btn, btn, Qt.NoModifier))

        def _mv2(x, y):
            _ov2.mouseMoveEvent(_QME2(_QEv2.Type.MouseMove, _QPt2(x, y), _QPt2(x, y),
                                      Qt.NoButton, Qt.LeftButton, Qt.NoModifier))

        def _rel2(x, y):
            _ov2.mouseReleaseEvent(_QME2(_QEv2.Type.MouseButtonRelease, _QPt2(x, y), _QPt2(x, y),
                                         Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        _press2(Qt.LeftButton, 40, 240)
        _mv2(150, 290)
        _rel2(150, 290)
        _okA1 = len(_ov2.stamps) == 1 and _ov2.stamps[0]["kind"] == "rect" \
            and _ov2.stamps[0]["r"] == QRect(40, 240, 111, 51)
        _ov2.cur_tool = "select"
        _press2(Qt.LeftButton, 95, 289)                # 按在下边框上＝选中
        _okA2a = _ov2.sel_stamp == ("shape", 0)
        _mv2(95, 249)                                  # 拖动＝整体平移
        _rel2(95, 249)
        _okA2 = _ov2.stamps[0]["r"] == QRect(40, 200, 111, 51) and _ov2.sel_stamp == ("shape", 0)
        _br = _ov2._stamp_handles()["br"]
        _press2(Qt.LeftButton, _br.x(), _br.y())       # 右下手柄＝缩放
        _mv2(200, 290)
        _rel2(200, 290)
        _okA3 = _ov2.stamps[0]["r"] == QRect(40, 200, 161, 91)
        _ov2.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Delete, Qt.NoModifier))
        _okA4 = len(_ov2.stamps) == 0 and _ov2.sel_stamp is None
        _ov2.undo()
        _okA5 = len(_ov2.stamps) == 1
        _press2(Qt.LeftButton, 330, 60)                # 空白点击＝取消选中
        _rel2(330, 60)
        _okA6 = _ov2.sel_stamp is None
        check("形状选中/移动/缩放/Del删除/撤销", _okA1 and _okA2a and _okA2 and _okA3
              and _okA4 and _okA5 and _okA6)
        # 拖拽光标：批注元素按住拖拽＝十字移动（R72 用户否决握拳「小手」），松手恢复
        _ov2.cur_tool = "rect"
        _press2(Qt.LeftButton, 30, 100)
        _mv2(90, 140)
        _rel2(90, 140)                     # 造独立小 rect（避开已有对象与选区手柄）
        _ov2.cur_tool = "select"
        _press2(Qt.LeftButton, 60, 100)    # 按在新 rect 顶边＝进入拖动
        _okC1 = _ov2._drag_shape is not None and _ov2.cursor().shape() == Qt.SizeAllCursor
        _rel2(80, 110)
        _okC1 = _okC1 and _ov2.cursor().shape() == Qt.ArrowCursor
        _press2(Qt.LeftButton, 100, 2)     # 选区上框条（避开手柄 ±6px）＝整体拖动
        _okC1 = _okC1 and _ov2._drag_sel is not None and _ov2.cursor().shape() == Qt.SizeAllCursor
        _rel2(120, 2)
        _okC1 = _okC1 and _ov2.cursor().shape() == Qt.ArrowCursor
        check("批注元素按住拖拽＝十字移动光标（松手恢复）", _okC1)
        _ov2.stamps.pop()                  # 移除拖拽用例的临时 rect：恢复后续文字用例基线
        _ov2.sel_stamp = None
        # 失焦对齐：绘制工具（rect/ellipse/arrow/text/num）点空白＝旧选中即失焦
        def _sel_rect0():
            _ov2.cur_tool = "select"
            _press2(Qt.LeftButton, 120, 200)           # 按 rect 顶边＝选中
            _rel2(120, 200)                            # 松开：拖动态清理，选中保留
            return _ov2.sel_stamp == ("shape", 0)
        _okB = _sel_rect0()
        for _tl in ("rect", "ellipse", "arrow"):
            _ov2.cur_tool = _tl
            _press2(Qt.LeftButton, 330, 60)            # 空白起笔＝即失焦
            _okB = _okB and _ov2.sel_stamp is None
            _ov2.stroke_pts = []                       # 收掉零面积起笔：不进 release 提交路径
            _ov2.undo()                                # 平衡起笔的 push_undo（快照＝原状，净零）
            _okB = _sel_rect0() and _okB
        _ov2.cur_tool = "text"
        _press2(Qt.LeftButton, 330, 60)                # text 点空白＝失焦+开新浮层
        _okB = _okB and _ov2.sel_stamp is None and _ov2.text_editor is not None
        _ov2.cancel_text_editor()
        _okB = _sel_rect0() and _okB
        _ov2.cur_tool = "num"
        _press2(Qt.LeftButton, 330, 60)                # num 点空白＝失焦+落新标
        _okB = _okB and _ov2.sel_stamp is None and _ov2.text_edit is not None
        _ov2.commit_text_input()                       # 空附言落字，圆标保留
        _press2(Qt.RightButton, 330, 60)               # 右键删标：恢复零序号基线
        _ov2.cur_tool = "select"
        _ov2.sel_stamp = None
        check("绘制工具点空白＝旧选中失焦（rect/ellipse/arrow/text/num）", _okB)
        # R71 选中态分级：rect 有框+8向手柄；text/num 无缩放热区；hover 对象=十字/手柄=方向/×钮=手型
        _okR71 = _sel_rect0() and len(_ov2._stamp_handles()) == 8       # rect＝8 向手柄热区
        _mv2(160, 200)                                 # hover rect 顶边框环（可拖动）＝十字移动
        _okR71 = _okR71 and _ov2.cursor().shape() == Qt.SizeAllCursor   # 注：rect/ellipse=边框环命中，内部留给新形状
        _br71 = _ov2._stamp_handles()["br"]
        _mv2(_br71.x(), _br71.y())                     # hover 右下手柄热区＝方向缩放光标
        _okR71 = _okR71 and _ov2.cursor().shape() == Qt.SizeFDiagCursor
        _ov2.cur_tool = "text"
        _press2(Qt.LeftButton, 300, 260)
        _rel2(300, 260)                                # 落文字浮层
        _ov2.text_editor.setPlainText("字号")
        _ov2.commit_text_editor()                      # 落字成对象
        _okR71 = _okR71 and _ov2.stamps[-1]["kind"] == "text"
        _ov2.sel_stamp = ("shape", len(_ov2.stamps) - 1)
        _okR71 = _okR71 and _ov2._stamp_handles() == {}   # 文字：无缩放手柄热区
        _ov2.cur_tool = "num"
        _press2(Qt.LeftButton, 350, 120)
        _rel2(350, 120)                                # 落标+附言浮层
        _ov2.commit_text_input()                       # 空附言落字，圆标保留
        _ov2.sel_stamp = ("num", 0)
        _okR71 = _okR71 and _ov2._stamp_handles() == {}   # 序号：无缩放手柄热区
        _mv2(350, 120)                                 # num 工具 hover 圆标＝十字移动
        _okR71 = _okR71 and _ov2.cursor().shape() == Qt.SizeAllCursor
        _dx71 = _ov2._num_del_pt(_ov2._nums[0])
        _mv2(_dx71.x(), _dx71.y())                     # hover × 删除钮＝手型点击
        _okR71 = _okR71 and _ov2.cursor().shape() == Qt.PointingHandCursor
        _press2(Qt.RightButton, 350, 120)              # 右键删标：恢复零序号基线
        _ov2.stamps.pop()                              # 清掉 R71 临时文字对象：恢复 rect 基线
        _ov2.cur_tool = "select"
        _ov2.sel_stamp = None
        check("R71 选中态分级（rect 8向/text/num 无缩放热区）+ hover 光标（十字/方向/手型）", _okR71)
        # 箭头回归锁：对象必须带 a/b 端点（缺了 _rerender_anno 即 KeyError → PySide6 虚方法 abort）
        _ov2.cur_tool = "arrow"
        _press2(Qt.LeftButton, 30, 260)
        _mv2(150, 210)
        _rel2(150, 210)
        _sa = _ov2.stamps[-1]
        _okA7 = _sa["kind"] == "arrow" and _sa["a"] == _QPt2(30, 260) and _sa["b"] == _QPt2(150, 210)
        try:
            _ov2._rerender_anno()                  # 缺 a/b 在这一步炸
        except Exception:
            _okA7 = False
        _ob_ar = QRect(_QPt2(30, 210), _QPt2(150, 260))
        _okA7 = _okA7 and _ov2._stamp_bbox(_sa) == _ob_ar \
            and _ov2._hit_one_shape(_sa, _QPt2(90, 235)) \
            and _ov2._move_shape(_sa, _QPt2(5, 5))["a"] == _QPt2(35, 265) \
            and _ov2._resize_to_bbox(_sa, _ob_ar, _ob_ar.translated(10, 0))["b"] == _QPt2(160, 210)
        check("箭头绘制落成带端点对象（渲染/包围盒/命中/平移/缩放）", _okA7)
        _ov2.stamps.pop()                      # 移除箭头：恢复后续文字用例的 1 对象基线
        # R72 箭头端点双手柄（拖端点改向）+ hover/按住十字全工具覆盖（修复「绘制工具下 hover 无反馈」）
        # 注：_ov2.sel=(0,0,400,300)——点位全部避开选区框条 y=300 与手柄 ±6px（撞框条会抢成选区拖动）
        _ov2.cur_tool = "arrow"
        _press2(Qt.LeftButton, 240, 240)
        _mv2(300, 270)
        _rel2(300, 270)                      # 落箭头 a=(240,240) b=(300,270)
        _ov2.cur_tool = "select"
        _press2(Qt.LeftButton, 270, 255)     # 按线段中点＝选中
        _rel2(270, 255)
        _ok72 = set(_ov2._stamp_handles().keys()) == {"a", "m", "b"}   # R100：classic＝2 端点柄＋中点拉弯柄
        _mv2(300, 270)                       # hover 箭头端点＝十字（端点无方向光标）
        _ok72 = _ok72 and _ov2.cursor().shape() == Qt.SizeAllCursor
        _press2(Qt.LeftButton, 300, 270)     # 拖 b 端点改向
        _mv2(330, 250)
        _rel2(330, 250)
        _sa72 = _ov2.stamps[-1]
        _ok72 = _ok72 and _sa72["b"] == _QPt2(330, 250) and _sa72["a"] == _QPt2(240, 240)
        # 拖后新线段 (240,240)-(330,250)：取 t=0.25 处 (262,242)——在线上但避开 R100 中点柄 m=(285,245)
        _ov2.cur_tool = "rect"               # R72 主诉：绘制工具下 hover 已画元素＝十字移动
        _mv2(262, 242)
        _ok72 = _ok72 and _ov2.cursor().shape() == Qt.SizeAllCursor
        _press2(Qt.LeftButton, 262, 242)     # rect 工具按住元素＝十字拖拽（不再握拳）
        _ok72 = _ok72 and _ov2._drag_shape is not None \
            and _ov2.cursor().shape() == Qt.SizeAllCursor
        _rel2(262, 242)
        _ov2.cur_tool = "pen"
        _mv2(262, 242)                       # pen 不可拖（按下起新笔）但属绘制类＝纯十字（R84）
        _ok72 = _ok72 and _ov2.cursor().shape() == Qt.CrossCursor
        _ov2.stamps.pop()                    # 清箭头：恢复 rect 基线
        _ov2.cur_tool = "select"
        _ov2.sel_stamp = None
        check("R72 箭头端点手柄（拖端点改向·R100 加中点拉弯柄）+ hover/按住十字全工具覆盖", _ok72)
        # R73 沿线命中：椭圆按弧命中（斜部也中）、箭头容差放宽——悬到线型上即给十字
        _el73 = {"kind": "ellipse", "r": QRect(40, 200, 160, 90), "color": "#ffffff", "w": 3}
        _p73 = _QPt2(round(120 + 80 * 0.7071), round(245 + 45 * 0.7071))  # 45° 弧点 (177,277)
        _ok73 = _ov2._hit_one_shape(_el73, _p73)             # 斜弧命中（旧矩形环：距边 24px 不中）
        _ok73 = _ok73 and not _ov2._hit_one_shape(_el73, _QPt2(120, 245))  # 圆心不命中（内部留给新形状）
        _ar73 = {"kind": "arrow", "a": _QPt2(240, 240), "b": _QPt2(330, 250), "color": "#ffffff", "w": 3}
        _ok73 = _ok73 and _ov2._hit_one_shape(_ar73, _QPt2(284, 252))      # 离线 ~7px：放宽后命中
        _ok73 = _ok73 and not _ov2._hit_one_shape(_ar73, _QPt2(284, 258))  # 离线 ~13px：仍不中
        _ov2.cur_tool = "select"
        _ov2.stamps.append(_el73)
        _mv2(177, 277)                                       # hover 椭圆斜弧＝十字（避 rect 基线环 x∈[193,209]）
        _ok73 = _ok73 and _ov2.cursor().shape() == Qt.SizeAllCursor
        _ov2.stamps.pop()                                    # 恢复 rect 基线
        check("R73 沿线命中：椭圆按弧/箭头放宽容差（hover 线型即十字）", _ok73)
        # R84 光标随工具直觉化：绘制/落标类空白＝纯十字（同框选）、文字＝I 型输入、select＝箭头
        _ok84 = True
        for _tool84, _cur84, _y84 in (("rect", Qt.CrossCursor, 55), ("ellipse", Qt.CrossCursor, 70),
                                      ("arrow", Qt.CrossCursor, 85), ("pen", Qt.CrossCursor, 100),
                                      ("marker", Qt.CrossCursor, 115), ("mosaic", Qt.CrossCursor, 130),
                                      ("high", Qt.CrossCursor, 145), ("num", Qt.CrossCursor, 160),
                                      ("text", Qt.IBeamCursor, 175), ("select", Qt.ArrowCursor, 188)):
            _ov2.cur_tool = _tool84
            _mv2(350, _y84)
            _ok84 = _ok84 and _ov2.cursor().shape() == _cur84
        _ov2.cur_tool = "rect"
        _mv2(120, 200)                                       # 回归：hover 已有矩形边框仍十字移动
        _ok84 = _ok84 and _ov2.cursor().shape() == Qt.SizeAllCursor
        _press2(Qt.LeftButton, 340, 205); _mv2(380, 230); _rel2(380, 230)   # 造小 rect
        _ok84 = _ok84 and _ov2.cursor().shape() == Qt.CrossCursor           # release 恢复＝工具十字
        _ov2.stamps.pop()                                                    # 清理
        _ov2.cur_tool = "select"; _ov2.sel_stamp = None
        check("R84 截图光标随工具（绘制纯十字/文字I型/select箭头·hover可拖仍十字移动）", _ok84)
        # R99 对象优先拖动泛化：num/text 工具按在已有对象上＝拖动它（此前 num 工具按住文字＝误落新标，
        # 用户报「标完序号文本拖不动，还老冒新序号」）；空白行为仍随工具（R84 不破）
        _ov2.cur_tool = "text"
        _ov2.show_text_editor(_QPt2(60, 240))
        _ov2.text_editor.setPlainText("九十九")
        _ov2.commit_text_editor()                      # 落字 → stamps 尾
        _ti99 = len(_ov2.stamps) - 1
        _r99 = QRect(_ov2.stamps[_ti99]["r"])
        _hx99, _hy99 = _r99.center().x(), _r99.center().y()
        _pt99 = _QPt2(370, 270)                        # 落标点：撞已有对象就挪（修复后 num 按对象＝拖动）
        while _ov2._hit_shape(_pt99) is not None or _ov2._hit_num(_pt99) is not None:
            _pt99 -= _QPt2(0, 20)
        _ov2.cur_tool = "num"
        _press2(Qt.LeftButton, _pt99.x(), _pt99.y()); _rel2(_pt99.x(), _pt99.y())
        _ov2.commit_text_input()                       # 空附言落字 → 圆标保留
        _nn99 = len(_ov2._nums)
        _ok99 = _ov2.stamps[_ti99]["kind"] == "text" and _nn99 >= 1
        _press2(Qt.LeftButton, _hx99, _hy99)           # num 工具按住文字＝拖动，不冒新标
        _ok99 = _ok99 and _ov2._drag_shape == _ti99 and _ov2.text_edit is None
        _mv2(_hx99 + 26, _hy99 + 14); _rel2(_hx99 + 26, _hy99 + 14)
        _ok99 = _ok99 and len(_ov2._nums) == _nn99 \
            and _ov2.stamps[_ti99]["r"].topLeft() == _r99.topLeft() + _QPt2(26, 14)
        _mv2(_hx99 + 26, _hy99 + 14)                   # num 工具 hover 文字＝十字移动
        _ok99 = _ok99 and _ov2.cursor().shape() == Qt.SizeAllCursor
        _ov2.cur_tool = "text"
        _npt99 = _QPt2(_ov2._nums[-1]["pt"])
        _press2(Qt.LeftButton, _npt99.x(), _npt99.y())  # text 工具按住圆标＝拖序号，不开浮层
        _ok99 = _ok99 and _ov2._drag_num == _nn99 - 1 and _ov2.text_editor is None
        _mv2(_npt99.x() + 20, _npt99.y()); _rel2(_npt99.x() + 20, _npt99.y())
        _ok99 = _ok99 and _ov2._nums[-1]["pt"] == _npt99 + _QPt2(20, 0)
        _mv2(_ov2._nums[-1]["pt"].x(), _ov2._nums[-1]["pt"].y())   # text 工具 hover 圆标＝十字
        _ok99 = _ok99 and _ov2.cursor().shape() == Qt.SizeAllCursor
        _pt99c = _QPt2(380, 60)                        # 回归：text 空白仍开浮层
        while _ov2._hit_shape(_pt99c) is not None or _ov2._hit_num(_pt99c) is not None:
            _pt99c -= _QPt2(0, 20)
        _press2(Qt.LeftButton, _pt99c.x(), _pt99c.y())
        _ok99 = _ok99 and _ov2.text_editor is not None
        _ov2.cancel_text_editor()
        _ov2.cur_tool = "num"
        _pt99b = _QPt2(380, 60)
        while _ov2._hit_shape(_pt99b) is not None or _ov2._hit_num(_pt99b) is not None:
            _pt99b -= _QPt2(0, 20)
        _press2(Qt.LeftButton, _pt99b.x(), _pt99b.y())  # 回归：num 空白仍落新标
        _ok99 = _ok99 and _ov2.text_edit is not None and len(_ov2._nums) == _nn99 + 1
        _ov2.cancel_text_edit()                        # 连标带言一并取消
        check("R99 截图对象优先拖动（num 按文字＝拖动·text 按圆标＝拖动·空白随工具不破）", _ok99)
        del _ov2.stamps[_ti99]                         # 清场：恢复 R78 段的 rect 基线
        _ov2._nums.clear()
        _ov2._rerender_anno()
        _ov2.sel_stamp = None
        _ov2.cur_tool = "select"
        # R100 序号样式：五档编号（1,2,3/A,B,C/a,b,c/I,II,III/i,ii,iii）+ 起始 1~9 循环
        _ov2.cur_num_style = "num"; _ov2.cur_num_start = 1
        _ok100a = (_ov2._num_label(0), _ov2._num_label(2)) == ("1", "3")
        _ov2.cur_num_style = "upper"; _ov2.cur_num_start = 2
        _ok100a = _ok100a and (_ov2._num_label(0), _ov2._num_label(1)) == ("B", "C")
        _ov2.cur_num_style = "lower"; _ov2.cur_num_start = 1
        _ok100a = _ok100a and (_ov2._num_label(0), _ov2._num_label(25)) == ("a", "z")
        _ov2.cur_num_style = "uroman"
        _ok100a = _ok100a and (_ov2._num_label(0), _ov2._num_label(1), _ov2._num_label(2)) == ("I", "II", "III")
        _ov2.cur_num_style = "lroman"
        _ok100a = _ok100a and (_ov2._num_label(0), _ov2._num_label(3)) == ("i", "iv")
        _ok100a = _ok100a and ShotOverlay._alpha(27) == "AA" \
            and ShotOverlay._roman(3999) == "MMMCMXCIX" and ShotOverlay._roman(4000) == "4000"
        for _ in range(9):                               # 起始 1~9 循环归一
            _ov2._cycle_num_start()
        _ok100a = _ok100a and _ov2.cur_num_start == 1
        # 菜单结构：monkeypatch QMenu.exec 在本版 PySide6 不生效（C++ 调用路径绕过类型字典），
        # 改用 QTimer 在 exec 局部循环里抓到新弹出菜单并关闭（Qt 测试标准打法）。
        # 注意不能用 id(wrapper) 指纹：topLevelWidgets 每次返回新 wrapper 对象，id 必然不同，
        # 既存菜单（如托盘菜单）会被误当新菜单抢先捕获——保持 wrapper 引用用 __eq__ 比 C++ 指针。
        _menus100 = []
        _before100 = [_w for _w in QApplication.topLevelWidgets() if isinstance(_w, QMenu)]

        def _grab100():
            for _w in QApplication.topLevelWidgets():
                if isinstance(_w, QMenu) and not any(_w == _b for _b in _before100) \
                        and _w not in _menus100:
                    _menus100.append(_w)
                    _w.close()                           # 关掉弹层 → exec 返回

        _tm100 = QTimer()
        _tm100.timeout.connect(_grab100)
        _tm100.start(0)
        try:
            _ov2._pick_num_style()
        finally:
            _tm100.stop()
        _acts100 = _menus100[0].actions() if _menus100 else []
        _ok100a = _ok100a and len(_acts100) == 5 and all(a.isCheckable() for a in _acts100) \
            and [a.text() for a in _acts100] == ["1, 2, 3", "A, B, C", "a, b, c", "I, II, III", "i, ii, iii"] \
            and _acts100[4].isChecked()                  # 当前＝lroman（五档第 5 项）
        _ov2._set_num_style("num")                       # 还原默认
        _ok100a = _ok100a and _ov2.cur_num_style == "num"
        check("R100 截图序号样式五档+起始循环+下拉菜单结构", _ok100a)
        # R160：截图新箭头默认实心锥形；中点拖弯/复原/弧点命中保留，经典线箭仍可切换
        _ov2.cur_tool = "arrow"
        _press2(Qt.LeftButton, 220, 60); _mv2(340, 60); _rel2(340, 60)     # 水平直箭头
        _ai104 = len(_ov2.stamps) - 1
        _sa104 = _ov2.stamps[_ai104]
        _ok104a = _sa104["kind"] == "arrow" and _sa104.get("style") == "solid" and "c" not in _sa104
        _ok104a = _ok104a and _ov2.cur_arrow_style == "solid" \
            and hasattr(_ov2, "_cycle_arrow_style")   # R160：截图默认锥形；样式钮仍可切换线箭
        _ov2.sel_stamp = ("shape", _ai104)
        _hd104 = _ov2._stamp_handles()
        _ok104a = _ok104a and set(_hd104) == {"a", "m", "b"} and _hd104["m"] == _QPt2(280, 60)
        _ov2._rs = _ov2._begin_stamp_resize("m")
        _ov2._apply_stamp_resize(_QPt2(280, 110))        # 中点下拉 50px → 弯
        _ok104a = _ok104a and _ov2.stamps[_ai104].get("c") == _QPt2(280, 110)
        _ov2._rs = None
        _ok104a = _ok104a and _ov2._hit_one_shape(_ov2.stamps[_ai104], _QPt2(280, 85))   # 弧点命中
        _ok104a = _ok104a and not _ov2._hit_one_shape(_ov2.stamps[_ai104], _QPt2(280, 60))  # 原直线不中
        _ov2._rs = _ov2._begin_stamp_resize("m")
        _ov2._apply_stamp_resize(_QPt2(281, 61))         # 拖回中点 ≤6px → 复原直线
        _ok104a = _ok104a and "c" not in _ov2.stamps[_ai104]
        _ov2._rs = None
        _lg104 = {"kind": "arrow", "a": _QPt2(220, 100), "b": _QPt2(340, 100),
                  "r": QRect(220, 100, 121, 1), "color": "#ef4444", "w": 3, "style": "modern"}
        _ov2.stamps.append(_lg104)                                  # 存量 modern 对象：非 solid 一律经典线箭
        _ov2.sel_stamp = ("shape", len(_ov2.stamps) - 1)
        _ok104a = _ok104a and set(_ov2._stamp_handles()) == {"a", "m", "b"}   # 仍恒三点
        # 选中渲染像素锁：bbox 上沿无方框虚线（排除手柄邻域）；三点圆点各自在
        from PySide6.QtGui import QImage as _QI104, QPainter as _QP104
        _im104 = _QI104(400, 300, _QI104.Format.Format_ARGB32)
        _im104.fill(Qt.white)
        _pn104 = _QP104(_im104)
        _ov2._draw_stamp_sel(_pn104)
        _pn104.end()

        def _green104(x, y):
            c = _im104.pixelColor(x, y)
            return c.green() > 150 and c.green() > c.red() + 60     # SEL_GREEN 优势像素

        _bb104 = _ov2._sel_bbox()                                   # 直箭头 bbox＝(220,100,121,1)
        _hx104 = [pt.x() for pt in _ov2._stamp_handles().values()]
        _ok104a = _ok104a and not any(
            _green104(x, _bb104.top() - 2)
            for x in range(_bb104.left() - 2, _bb104.right() + 3)
            if all(abs(x - hx) > 7 for hx in _hx104))               # 上沿（虚线框位）零绿点＝无方框
        for _hp in _ov2._stamp_handles().values():
            _ok104a = _ok104a and any(
                _green104(_hp.x() + dx, _hp.y() + dy)
                for dx in range(-5, 6) for dy in range(-5, 6))      # a/m/b 圆点各自在
        del _ov2.stamps[-1]
        del _ov2.stamps[_ai104]                          # 清场：回 rect 基线
        _ov2.sel_stamp = None; _ov2.cur_tool = "select"
        _ov2._rerender_anno()
        check("R160 截图箭头默认锥形（新箭头/中点拖弯复原/弧点命中）", _ok104a)
        # R100 聚光椭圆化：填充区命中 + 圈外压暗渲染锁（rect 形态回归）
        from PySide6.QtGui import QColor as _QC100, QPainter as _QP100
        _ov2.cur_tool = "high"; _ov2.cur_high_shape = "ellipse"
        _press2(Qt.LeftButton, 200, 40); _mv2(320, 120); _rel2(320, 120)
        _hi100 = len(_ov2.stamps) - 1
        _sh100 = _ov2.stamps[_hi100]
        _ok100c = _sh100["kind"] == "high" and _sh100.get("shape") == "ellipse" \
            and _sh100["r"] == QRect(200, 40, 121, 81)
        _ok100c = _ok100c and _ov2._hit_one_shape(_sh100, _QPt2(260, 80))        # 中心命中（填充区）
        _ok100c = _ok100c and not _ov2._hit_one_shape(_sh100, _QPt2(205, 45))    # 界内角点：椭圆外不中
        _sr100 = dict(_sh100); _sr100["shape"] = "rect"
        _ok100c = _ok100c and _ov2._hit_one_shape(_sr100, _QPt2(205, 45))        # 同点 rect 命中
        _pm100 = QPixmap(400, 300); _pm100.fill(_QC100("#ffffff"))
        _pt100 = _QP100(_pm100)
        _ov2._draw_highlight(_pt100, QRect(200, 40, 121, 81), "ellipse")
        _pt100.end()
        _im100 = _pm100.toImage()
        _ok100c = _ok100c and _im100.pixelColor(260, 80).red() > 240       # 椭圆中心不被压暗
        _ok100c = _ok100c and _im100.pixelColor(205, 45).red() < 200       # 界内角点被压暗
        _pm100b = QPixmap(400, 300); _pm100b.fill(_QC100("#ffffff"))
        _pt100b = _QP100(_pm100b)
        _ov2._draw_highlight(_pt100b, QRect(200, 40, 121, 81), "rect")
        _pt100b.end()
        _ok100c = _ok100c and _pm100b.toImage().pixelColor(205, 45).red() > 240  # rect 角点不压暗
        _ov2.sel_stamp = ("shape", _hi100)
        _ov2._cycle_high_shape()                         # 选中聚光 → 改对象（默认不变）
        _ok100c = _ok100c and _ov2.stamps[_hi100]["shape"] == "rect" \
            and _ov2.cur_high_shape == "ellipse"
        _ov2.sel_stamp = None
        _ov2._cycle_high_shape()                         # 未选中 → 默认回 rect
        _ok100c = _ok100c and _ov2.cur_high_shape == "rect"
        del _ov2.stamps[_hi100]                          # 清场：回 rect 基线
        _ov2.cur_tool = "select"; _ov2._rerender_anno()
        check("R100 截图聚光椭圆（填充命中/圈外压暗像素/选中改对象）", _ok100c)
        # R134 截图侧：属性钮退役迁入辐条 ToolPropsPop——主工具条只留工具/动作钮
        # （「高光后两条竖线」＝末位工具+隐藏属性钮夹出的相邻双分隔线，随范式退役消除）；
        # 显隐断言用 isHidden 自标记（离屏下祖先窗不可见连累 isVisible 恒 False）
        _ov2.show_toolbar()                                # R100 段末 cur_tool=select：辐条应收起
        _ok101 = not hasattr(_ov2, "_btn_num_style") and not hasattr(_ov2, "_btn_arrow_style") \
            and not hasattr(_ov2, "_btn_high_shape") and not hasattr(_ov2, "_btn_num_start") \
            and _ov2.props_pop.isHidden()
        _pop101 = _ov2.props_pop
        _ov2.set_tool("rect")
        _ok101 = _ok101 and not _pop101.isHidden() and _pop101._btn_arrow_style.isHidden() \
            and _pop101._btn_num_style.isHidden() and _pop101._btn_high_shape.isHidden()  # rect 无选项区
        _ov2.set_tool("num")
        _ok101 = _ok101 and not _pop101._btn_num_style.isHidden() \
            and not _pop101._btn_num_start.isHidden() and _pop101._btn_high_shape.isHidden()
        _ov2.set_tool("arrow")
        _ok101 = _ok101 and not _pop101._btn_arrow_style.isHidden() \
            and _pop101._btn_num_style.isHidden()
        _ov2.set_tool("high")
        _ok101 = _ok101 and not _pop101._btn_high_shape.isHidden() \
            and _pop101._btn_num_style.isHidden() \
            and _pop101._size_dots[0][1].isHidden()          # 聚光无粗细/颜色区
        _ok101 = _ok101 and "rgba(59,130,246,48)" in _pop101._OPT_QSS \
            and "border:none" in _pop101._OPT_QSS       # 蓝族选项样式同族（R141 起无描边）
        _ov2.set_tool("select")
        _ok101 = _ok101 and _pop101.isHidden()                 # 非标注工具即收
        _ov2.toolbar.close()
        _ov2.toolbar = None
        check("R134 截图属性钮退役迁辐条（选项随工具/聚光无粗细色区/双竖线消除）", _ok101)
        # R102 聚光暗度循环：近点单击不落对象（去小点）＝遮罩逐级加深，满黑后回默认档不关窗；
        # 双击守卫：high 工具下连击不触发复制关窗（双击语义让位给暗度循环）
        from PySide6.QtGui import QPainter as _QP102, QColor as _QC102
        _ov2.cur_tool = "high"
        _ok102 = _ov2._hit_shape(_QPt2(20, 170)) is None   # 点击点前提：空白（ press 不落拖动）
        _press2(Qt.LeftButton, 200, 40); _mv2(320, 120); _rel2(320, 120)
        _hi102 = len(_ov2.stamps) - 1
        _n102 = len(_ov2.stamps)
        _ok102 = _ok102 and _ov2.stamps[_hi102]["kind"] == "high" \
            and _ov2.stamps[_hi102].get("dim") == 148 and _ov2.cur_high_dim == 148
        _press2(Qt.LeftButton, 20, 170); _mv2(22, 172); _rel2(22, 172)   # 近点单击（2px 抖动）
        _ok102 = _ok102 and len(_ov2.stamps) == _n102                     # 不落小点对象
        _ok102 = _ok102 and _ov2.cur_high_dim == 195 \
            and _ov2.stamps[_hi102]["dim"] == 195                         # 既有遮罩同步加深
        _press2(Qt.LeftButton, 20, 170); _rel2(20, 170)                   # 原地单击也循环
        _ok102 = _ok102 and _ov2.stamps[_hi102]["dim"] == 230
        _press2(Qt.LeftButton, 20, 170); _rel2(20, 170)
        _ok102 = _ok102 and _ov2.stamps[_hi102]["dim"] == 255             # 满黑
        _pm102 = QPixmap(400, 300); _pm102.fill(_QC102("#ffffff"))
        _pt102 = _QP102(_pm102)
        _ov2._draw_highlight(_pt102, QRect(200, 40, 121, 81), "rect", 255)
        _pt102.end()
        _im102 = _pm102.toImage()
        _ok102 = _ok102 and _im102.pixelColor(20, 170).alpha() == 255 \
            and _im102.pixelColor(20, 170).red() < 40                     # 满黑完全不透光
        _press2(Qt.LeftButton, 20, 170); _rel2(20, 170)
        _ok102 = _ok102 and _ov2.stamps[_hi102]["dim"] == 148 \
            and _ov2.cur_high_dim == 148 and len(_ov2.stamps) == _n102    # 回默认档，不关窗
        _u102 = len(_ov2.undo_stack)
        _ov2.undo()
        _ok102 = _ok102 and _ov2.stamps[_hi102]["dim"] == 255 \
            and len(_ov2.undo_stack) == _u102 - 1                         # 一步撤销回满黑
        _copied102 = []
        _docopy102 = _ov2.do_copy
        _ov2.do_copy = lambda: _copied102.append(1)
        _ov2.mouseDoubleClickEvent(_QME2(_QEv2.Type.MouseButtonDblClick, _QPt2(20, 170),
                                         _QPt2(20, 170), Qt.LeftButton, Qt.LeftButton,
                                         Qt.NoModifier))
        _ov2.do_copy = _docopy102
        _ok102 = _ok102 and not _copied102                                # 双击不复制不关窗
        del _ov2.stamps[_hi102]                           # 清场：回 rect 基线
        _ov2.cur_high_dim = 148
        _ov2.cur_tool = "select"; _ov2._rerender_anno()
        check("R102 截图聚光暗度循环（去小点/逐级加深/满黑回默认/双击守卫/可撤销）", _ok102)
        # R78 截图侧：悬浮即删（免选中）+ 文字按住拖拽——Backspace/Delete 删鼠标正悬浮的元素
        _ov2.sel_stamp = None                          # 未选中前提
        _ov2.cur_tool = "num"
        _press2(Qt.LeftButton, 320, 60)                # 落标（附言浮层开）
        _rel2(320, 60)
        _ov2.commit_text_input()                       # 空附言落字
        _mv2(320, 60)                                  # hover 圆标（mouseMove 记录 _hover_pt）
        _ov2.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78a = len(_ov2._nums) == 0                  # 序号悬浮即删
        _ov2.stamps.append({"kind": "ellipse", "r": QRect(40, 200, 160, 90), "color": "#ffffff", "w": 3})
        _mv2(177, 277)                                 # hover 椭圆斜弧（R73 命中点）
        _ov2.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78a = _ok78a and len(_ov2.stamps) == 1 and _ov2.stamps[0]["kind"] == "rect"
        _ov2.stamps.append({"kind": "arrow", "a": _QPt2(240, 240), "b": _QPt2(330, 250),
                            "color": "#ffffff", "w": 3})
        _mv2(284, 252)                                 # hover 箭头线段（R73 命中点）
        _ov2.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78a = _ok78a and len(_ov2.stamps) == 1 and _ov2.stamps[0]["kind"] == "rect"
        _mv2(120, 200)                                 # hover rect 顶边
        _ov2.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
        _ok78a = _ok78a and len(_ov2.stamps) == 0      # 矩形悬浮即删
        _ov2.undo()
        _ok78a = _ok78a and len(_ov2.stamps) == 1      # 悬浮即删可撤销（rect 回）
        _ov2.cur_tool = "text"
        _ov2.show_text_editor(_QPt2(250, 40))
        _ov2.text_editor.setPlainText("拖")
        _ov2.commit_text_editor()                      # 落字成对象 → stamps[1]
        _ti78 = len(_ov2.stamps) - 1
        _r78a = QRect(_ov2.stamps[_ti78]["r"])
        _hx78, _hy78 = _r78a.center().x(), _r78a.center().y()
        _ov2.cur_tool = "select"
        _press2(Qt.LeftButton, _hx78, _hy78)           # 按住文字拖拽
        _mv2(_hx78 + 30, _hy78 + 20)
        _rel2(_hx78 + 30, _hy78 + 20)
        _ok78a = _ok78a and _ov2.stamps[_ti78]["r"].topLeft() == _r78a.topLeft() + _QPt2(30, 20)
        _ov2.sel_stamp = None
        _mv2(_hx78 + 30, _hy78 + 20)                   # hover 文字（拖拽后新位中心）
        _ov2.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Delete, Qt.NoModifier))
        _ok78a = _ok78a and len(_ov2.stamps) == 1 and _ov2.stamps[0]["kind"] == "rect"   # 文字悬浮即删
        check("R78 截图悬浮即删（序号/椭圆/箭头/矩形/文字免选中）+ 文字按住拖拽", _ok78a)
        # R79 文字再编辑防重影（渲染级锁）：编辑期间底层 stamp 让位，提交/取消恢复
        _ov2.cur_tool = "text"
        _ov2.cur_color = "#ff0000"
        _ov2.show_text_editor(_QPt2(250, 120))
        _ov2.text_editor.setPlainText("影")
        _ov2.commit_text_editor()                      # 落红字 → stamps[1]

        def _red79():
            _im79 = _ov2.anno.toImage()                # anno 位图不含浮层：直接数底层红字像素
            _n79 = 0
            for _yy in range(_im79.height()):
                for _xx in range(_im79.width()):
                    _c = _im79.pixelColor(_xx, _yy)
                    if _c.alpha() > 200 and _c.red() > 200 and _c.green() < 90 and _c.blue() < 90:
                        _n79 += 1
            return _n79

        _ok79 = len(_ov2.stamps) == 2 and _ov2.stamps[1]["kind"] == "text" and _red79() > 0
        _ov2._reedit_text(1)                           # 双击再编辑：浮层接管，底层让位
        _ok79 = _ok79 and _ov2.text_editor is not None and _red79() == 0   # 无重影
        _ov2.text_editor.setPlainText("影2")
        _ov2.commit_text_editor()                      # 提交：原位替换，红回来
        _ok79 = _ok79 and _red79() > 0 and _ov2.stamps[1]["lines"] == ["影2"]
        _ov2._reedit_text(1)
        _ok79 = _ok79 and _red79() == 0
        _ov2.cancel_text_editor()                      # Esc 取消：原文恢复上屏
        _ok79 = _ok79 and _red79() > 0 and _ov2.stamps[1]["lines"] == ["影2"]
        _ov2.stamps.pop()                              # 恢复 rect 基线
        _ov2.cur_color = "#00ff00"
        check("R79 文字再编辑防重影（编辑期底层让位·提交/取消恢复）", _ok79)
        _ov2.cur_tool = "text"
        _ov2.show_text_editor(_QPt2(250, 40))          # 无输入框：浮层直接写
        _okT1 = _ov2.text_editor is not None and isinstance(_ov2.text_editor, QTextEdit) \
            and _ov2.text_editor.toPlainText() == ""
        _ov2.text_editor.setPlainText("甲")
        _ov2.text_editor.moveCursor(_QTC.MoveOperation.End)   # 光标到末尾（真实输入位置）
        _ov2.text_editor.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
        _ov2.text_editor.insertPlainText("乙")
        _okT2 = _ov2.text_editor.toPlainText() == "甲\n乙"   # 回车＝换行，不是确认
        _ov2.commit_text_editor()                      # 失焦/点空白＝落字成对象
        _okT3 = len(_ov2.stamps) == 2 and _ov2.stamps[1]["kind"] == "text" \
            and _ov2.stamps[1]["lines"] == ["甲", "乙"] and _ov2.stamps[1]["fs"] == 14
        check("文字所见即所得（回车换行·失焦落字）", _okT1 and _okT2 and _okT3)
        # 失焦点击只落字：同一击不得接着开新浮层（再点一下才继续输入）
        _ov2.show_text_editor(_QPt2(30, 30))           # 再开一版浮层
        _ov2.text_editor.setPlainText("丙")
        _press2(Qt.LeftButton, 190, 140)               # 点空白＝失焦落字
        _rel2(190, 140)
        _n_c = len(_ov2.stamps)
        _okT4 = (_ov2.text_editor is None and _n_c == 3        # 浮层已关且只落了"丙"，没接着开新浮层
                 and _ov2.stamps[2]["lines"] == ["丙"])
        _press2(Qt.LeftButton, 190, 140)               # 同一点第二击＝继续输入（新浮层）
        _rel2(190, 140)
        _okT4 = _okT4 and _ov2.text_editor is not None and _ov2.text_editor.toPlainText() == ""
        _ov2.text_editor.setPlainText("丁")
        _ov2.commit_text_editor()                      # 收尾提交 → 第 4 个对象
        _okT5 = len(_ov2.stamps) == 4 and _ov2.stamps[3]["lines"] == ["丁"]
        check("文字点击失焦只落字（再点一下才继续输入）", _okT4 and _okT5)
        # 文字到选区右缘自动换行：软换行（文档零改写），提交按同一排版引擎拆行
        _ov2.sel = QRect(0, 0, 120, 300)
        _ov2.show_text_editor(_QPt2(20, 60))
        _ov2.text_editor.setPlainText("一二三四五六七八九十" * 4)
        _wt = _ov2.text_editor.toPlainText()
        _nln = len(_ov2._layout_lines(_wt, _ov2.text_editor.font(), _ov2.text_editor.width()))
        _okW = ("\n" not in _wt                                  # 软换行：文档零改写
                and _ov2.text_editor.width() <= 101              # 宽封顶＝选区右缘
                and _nln >= 3                                    # 长行确实换行
                and _ov2.text_editor.x() + _ov2.text_editor.width() <= 122
                and _ov2.text_editor.y() + _ov2.text_editor.height() <= 301)
        _ov2.commit_text_editor()
        _okW = _okW and _ov2.stamps[-1]["r"].right() <= 121 \
            and len(_ov2.stamps[-1]["lines"]) == _nln
        check("文字到选区右缘自动换行（软换行·提交同排版不越界）", _okW)
        # 回归重点是输入中的可见性，不是提交后的字符串完整性。
        # 旧实现 nihao 的布局已高 50px，编辑器仍高 26px，隐藏滚动条下滚 24px。
        from PySide6.QtGui import QInputMethodEvent as _QIME2
        _ov2.setAttribute(Qt.WA_DontShowOnScreen, True)
        _ov2.show()
        app.processEvents()

        def _ime_visible(t):
            ds = t.document().documentLayout().documentSize()
            return (t.verticalScrollBar().value() == 0
                    and t.horizontalScrollBar().value() == 0
                    and ds.height() <= t.viewport().height()
                    and t.viewport().rect().contains(t.cursorRect())
                    and _ov2.sel.contains(t.geometry()))

        def _send_ime(t, pre, committed=""):
            attrs = [_QIME2.Attribute(_QIME2.Cursor, len(pre), 1, None)]
            event = _QIME2(pre, attrs)
            event.setCommitString(committed)
            app.sendEvent(t, event)
            # 必须事件返回即完整显示，不能依赖 150ms 延迟或失焦恢复。
            ok = _ime_visible(t)
            app.processEvents()
            return ok and _ime_visible(t)

        for _kind in ("text", "num"):
            _okI = True
            for _sz in (2, 4, 7):
                for _width in (120, 500):
                    _ov2.cur_size = _sz
                    _ov2.sel = QRect(0, 0, _width, 400)
                    if _kind == "text":
                        _ov2.show_text_editor(_QPt2(20, 365))
                        _ted = _ov2.text_editor
                    else:
                        _ov2.show_num_input(_QPt2(20, 365))
                        _ted = _ov2.text_edit
                    for _pre in ("n", "ni", "nihao", "ni'hao'zhong'guo"):
                        _okI = _send_ime(_ted, _pre) and _okI
                        _okI = (_ted.toPlainText() == "" and
                                _ted.document().begin().layout().preeditAreaText() == _pre) and _okI
                    # 候选确认后接着组字，再取消；确认内容不能消失或被布局改写。
                    _okI = _send_ime(_ted, "jie'zhe", "你好") and _okI
                    _okI = (_ted.toPlainText() == "你好") and _okI
                    _okI = _send_ime(_ted, "") and _okI
                    app.sendEvent(_ted, _QKE2(_QEv2.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
                    _okI = _send_ime(_ted, "di'er'hang") and _okI
                    _okI = _send_ime(_ted, "", "第二行") and _okI
                    _okI = (_ted.toPlainText() == "你好\n第二行") and _okI
                    if _kind == "text":
                        _ov2.commit_text_editor()
                        _result = "".join(_ov2.stamps[-1]["lines"])
                    else:
                        _ov2.commit_text_input()
                        _result = _ov2._nums[-1]["text"].replace("\n", "")
                    _okI = (_result == "你好第二行") and _okI
            check(f"{'文字' if _kind == 'text' else '序号附言'}首行可见（预编辑/换行/三字号/右缘底缘·零滚动）", _okI)
        _ov2.cur_size = 2
        _ov3 = ShotOverlay(_QPm(200, 150), screen=_QGA.primaryScreen())
        _ov3.phase = "edit"
        _ov3.sel = QRect(0, 0, 200, 150)
        _copied = []
        _ov3.do_copy = lambda: _copied.append(1)       # 只验证回车路由，不真复制
        _ov3.keyPressEvent(_QKE2(_QEv2.Type.KeyPress, Qt.Key_Return, Qt.NoModifier))
        check("失焦后回车=复制到剪贴板", len(_copied) == 1)
        _ov3.close()
        _ov3.deleteLater()
        _ov2.close()
        _ov2.deleteLater()
    except Exception as e:
        print(f"SKIP 标注交互：{e}")
    # 16.7 屏上 toast：复制/完成提示真实可见（不再是会被折叠吞掉的托盘气泡）
    try:
        ctl.toast("smoke · 屏上提示验证")
        _okTS = ctl._toast.isVisible()
        ctl._toast.hide()
        check("屏上 toast（复制/完成提示真实可见）", _okTS)
    except Exception as e:
        print(f"SKIP toast：{e}")
    # 17 录制选区实时框：压暗层全屏穿透 + 边框移动/缩放数学
    try:
        from PySide6.QtCore import QPoint as _QPt
        from capture import RegionDim, RegionFrame
        _scr = _QGA.primaryScreen()
        _stl = _scr.geometry().topLeft()
        _reg = QRect(100, 100, 200, 150)
        _cb = []
        _fr = RegionFrame(_scr, _reg, lambda r: _cb.append(QRect(r)))
        check("RegionFrame 初始几何", _fr.geometry() ==
              _reg.adjusted(-11, -11, 11, 11).translated(_stl) and not _cb)
        _fr._drag = ("move", _QPt(2, 2))
        _fr._move_to(_QPt(220, 180))
        check("RegionFrame 移动", _fr.region().topLeft() == _QPt(218, 178) and len(_cb) == 1)
        _fr._drag = ("resize", 7)                       # 右下角手柄
        _fr._resize_to(7, _QPt(500, 420))
        check("RegionFrame 右下缩放", _fr.region() == QRect(218, 178, 283, 243) and len(_cb) == 2)
        _fr._drag = ("resize", 0)                       # 左上角手柄
        _fr._resize_to(0, _QPt(50, 60))
        check("RegionFrame 左上缩放", _fr.region() == QRect(50, 60, 451, 361))
        _fr._drag = ("resize", 4)                       # 右缘手柄拖过头 → 钳到最小宽
        _fr._resize_to(4, _QPt(30, 300))
        check("RegionFrame 最小尺寸钳制", _fr.region().width() == RegionFrame.MINW)
        _fr.close()
        _fr.deleteLater()
        _dm = RegionDim(_scr, lambda: _fr.region())
        check("RegionDim 全屏覆盖", _dm.geometry() == _scr.geometry())
        _dm.close()
        _dm.deleteLater()
    except Exception as e:
        print(f"SKIP 实时框：{e}")
    # 17. 任务清单（R87）：存储回环/旧便签迁移 + 勾选完成流 + 双击编辑 + 窗口行为
    try:
        import sticky as _ST
        import tempfile as _tfS
        import json as _jsonS                       # 段内导入：冒烟作用域无顶层 json
        _tmp_st = _tfS.mkdtemp()
        _orig_tk = C.TASKS_PATH
        _orig_lg = C.LEGACY_STICKIES_PATH
        C.TASKS_PATH = os.path.join(_tmp_st, "tasks.json")
        C.LEGACY_STICKIES_PATH = os.path.join(_tmp_st, "stickies.json")
        try:
            # 存储：原子写回环 + 字段保留 + 损坏 JSON 兑底空结构
            C.save_tasks({"win": {"x": 1, "y": 2, "w": 3, "h": 4, "ontop": True, "visible": True},
                          "tasks": [{"id": "a", "text": "甲", "prio": 2, "done": True,
                                     "created": 1.0, "done_at": 2.0, "updated": 3.0, "deleted": False}]})
            _tk1 = C.load_tasks()
            _okS = _tk1["win"]["ontop"] is True and len(_tk1["tasks"]) == 1 \
                and _tk1["tasks"][0]["text"] == "甲" and _tk1["tasks"][0]["prio"] == 2 \
                and _tk1["tasks"][0]["done"] is True
            with open(C.TASKS_PATH, "w", encoding="utf-8") as _fS:
                _fS.write("{oops")
            _okS = _okS and C.load_tasks() == {"win": {}, "tasks": []}
            # 一次性迁移：tasks.json 不可用 + 旧 stickies.json 有未删便签 → 转任务
            with open(C.LEGACY_STICKIES_PATH, "w", encoding="utf-8") as _fL:
                _jsonS.dump([{"id": "n1", "text": "旧便签一", "deleted": False, "updated": 9.0},
                             {"id": "n2", "text": "已删便签", "deleted": True},
                             {"id": "n3", "text": "   ", "deleted": False}], _fL)
            _d3 = C.load_tasks()
            _okS = _okS and len(_d3["tasks"]) == 1 and _d3["tasks"][0]["text"] == "旧便签一" \
                and _d3["tasks"][0]["created"] == 9.0
            os.remove(C.LEGACY_STICKIES_PATH)     # 迁移源只测这一回，后续用例不受干扰
            check("任务清单存储（回环/损坏兑底/旧便签迁移）", _okS)

            # 管理器+窗口：添加（新在上）→ UI勾选进完成（划线）→ 取消回待办 → 双击编辑 →
            # 竖线优先级 → 行删除/清除完成（软删）→ 落盘恢复 → 显隐/拖动/置顶
            _mg = _ST.TaskManager()
            _mg.show_widget()
            _w = _mg.widget
            _t1 = _mg.add("写周报")
            _t2 = _mg.add("回邮件", prio=3)
            _t3 = _mg.add("订机票")
            _todo, _done = _mg.lists()
            _okW = [t["text"] for t in _todo] == ["订机票", "回邮件", "写周报"] and not _done \
                and len(_w.rows()) == 3
            _row = next(r for r in _w.rows() if r.task["id"] == _t2["id"])
            _row.box.setChecked(True)                          # UI 路径勾选（R89 起进过渡）
            _row._commit_done()                                # 冒烟快进：等同动画播完
            _todo, _done = _mg.lists()
            _okW = _okW and len(_todo) == 2 and len(_done) == 1 \
                and _done[0]["text"] == "回邮件" and _done[0]["done_at"] > 0
            _okW = _okW and all(r.task["id"] != _t2["id"] for r in _w.rows())  # R89 完成区默认折叠：完成行不渲染
            _w.toggle_done_collapsed()                         # 展开完成区才能取到完成行
            _dr = next(r for r in _w.rows() if r.task["id"] == _t2["id"])
            _okW = _okW and _dr.label.font().strikeOut() and _dr.box.isChecked()   # R126 自绘框：断言态不取文本
            _dr.box.setChecked(False)                          # 取消勾选 → 回待办（R89 起进过渡）
            _dr._commit_done()                                 # 冒烟快进
            _todo, _done = _mg.lists()
            _okW = _okW and len(_todo) == 3 and not _done \
                and any(t["text"] == "回邮件" for t in _todo)
            _row1 = next(r for r in _w.rows() if r.task["id"] == _t1["id"])
            _w.begin_edit(_row1)                               # 双击编辑 → 落字
            _ed = _w._editing[1]
            _ed.setText("写周报（周五前）")
            _ed.editingFinished.emit()
            _okW = _okW and _mg.tasks[_t1["id"]]["text"] == "写周报（周五前）" \
                and _w._editing is None
            _w._set_add_prio(2)                                # R98 下拉即选即设（取代循环点击）
            _okW = _okW and _w._prio == 2 and _w.add_prio_bar.prio == 2 \
                and _w.add_prio_bar.cursor().shape() == Qt.PointingHandCursor \
                and _w.add_plus.testAttribute(Qt.WA_TransparentForMouseEvents) \
                and _w.add_row.layout().indexOf(_w.add_plus) < _w.add_row.layout().indexOf(_w.add_prio_bar) \
                < _w.add_row.layout().indexOf(_w.inp)
            _w._set_add_prio(1)                                # 改选低优先级
            _w.inp.setText("低优任务")
            _w.inp.returnPressed.emit()
            _okW = _okW and next(t for t in _mg.tasks.values()
                                 if t["text"] == "低优任务")["prio"] == 1 \
                and _w.inp.text() == ""
            _okW = _okW and len(_w._prio_menu(1, lambda p: None).actions()) == 4   # 菜单四档
            _mg.set_done(_t1["id"], True)
            _mg.delete(_t3["id"])
            _okW = _okW and _mg.tasks[_t3["id"]]["deleted"] is True
            _nc = _mg.clear_done()
            _todo, _done = _mg.lists()
            _okW = _okW and _nc >= 1 and not _done
            _mg.save()
            _mgB = _ST.TaskManager(); _mgB._load()             # 重启恢复（不开窗）
            _todoB, _doneB = _mgB.lists()
            _okW = _okW and sorted(t["text"] for t in _todoB) == ["低优任务", "回邮件"]
            _mg.hide_widget()
            _okW = _okW and not _w.isVisible() and C.load_tasks()["win"]["visible"] is False
            _mg.toggle()
            _okW = _okW and _w.isVisible() and C.load_tasks()["win"]["visible"] is True
            from PySide6.QtCore import QEvent as _QEv, QPoint, QPointF as _QPtF
            from PySide6.QtGui import QMouseEvent as _QMEv
            _p0 = _w.pos()
            _gp0 = _w.mapToGlobal(QPoint(10, 10))
            _w.eventFilter(_w.head, _QMEv(_QEv.MouseButtonPress, _QPtF(10, 10), _QPtF(_gp0),
                                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            _w.eventFilter(_w.head, _QMEv(_QEv.MouseMove, _QPtF(47, 39), _QPtF(_gp0 + QPoint(37, 29)),
                                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            _okW = _okW and (_w.pos() - _p0) == QPoint(37, 29)
            _w.eventFilter(_w.head, _QMEv(_QEv.MouseButtonRelease, _QPtF(47, 39),
                                          _QPtF(_gp0 + QPoint(37, 29)),
                                          Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            _w.toggle_pin()
            _okW = _okW and _w._pinned                   # R150：钉态真相源 _pinned（flags 全程无 StaysOnTop）
            _mg.hide_widget()
            check("任务清单（添加/勾选完成/取消/双击编辑/优先级竖线/删除确认/清除确认/恢复/显隐/拖动/置顶）", _okW)
            # R87b UI 升级元素 + 面板入口：空态徽章 / 待办计数 / 添加行胶囊焦点态 / ACTIONS 含 note
            C.TASKS_PATH = os.path.join(_tmp_st, "tasks_ui.json")   # 干净库：不受主用例残留影响
            _mgC = _ST.TaskManager(); _mgC.show_widget(); _wC = _mgC.widget
            _okU = _wC.count_lbl.text() == ""                # 空库：计数隐藏
            _empt = [x for x in _wC.list_host.findChildren(QLabel) if x.text() == "暂无任务"]
            _okU = _okU and len(_empt) == 1                  # 空态徽章文案现身
            _mgC.add("甲"); _mgC.add("乙")
            _okU = _okU and _wC.count_lbl.text() == "2 待办"  # rebuild 刷新计数
            from PySide6.QtCore import QEvent as _QEvU
            _wC.eventFilter(_wC.inp, _QEvU(_QEvU.FocusIn))
            _okU = _okU and _wC.add_row.property("focused") is True
            _wC.eventFilter(_wC.inp, _QEvU(_QEvU.FocusOut))
            _okU = _okU and _wC.add_row.property("focused") is False
            # R88：钉=显式置顶开关（默认不置顶，点钉才置顶、再点取消），钉文字换截图同款矢量图标
            from PySide6.QtCore import Qt as _QtP
            _okP = not _wC._pinned and not bool(_wC.windowFlags() & _QtP.WindowStaysOnTopHint)   # R150：默认不钉+flags 全程无 StaysOnTop
            _okP = _okP and _wC.btn_pin.text() == "" and not _wC.btn_pin.icon().isNull()
            _hwnd88 = int(_wC.winId())                            # R150 无闪硬证据基线
            _wC.toggle_pin()
            _okP = _okP and _wC._pinned and _wC.btn_pin.isChecked() and int(_wC.winId()) == _hwnd88   # 句柄不重建＝无闪
            _wC.toggle_pin()
            _okP = _okP and not _wC._pinned and not _wC.btn_pin.isChecked() and int(_wC.winId()) == _hwnd88
            check("R88 钉图标化+显式置顶开关（默认不置顶·R150无闪）", _okP)
            _mgC.hide_widget()
            _has_note = any(m == "note" and lb == "任务清单"
                            for _g, lb, _gl, _bg, _d, _h, m in __import__("panel").ACTIONS)
            _okU = _okU and (not _has_note if LITE else _has_note)   # R107 轻量版面板裁任务清单
            check("R87b UI 升级（空态徽章/待办计数/胶囊焦点/面板入口）", _okU)
            # R125 照滴答清单样式：行高 44/字号 14、标题 17px 粗体无徽章、胶囊灰白系（无蓝紫描边）
            import sticky as _ST125
            _row125 = next(r for r in _wC.rows() if not r.done)
            _head_lbls = _wC.head.findChildren(QLabel)
            _ok125 = _row125.minimumHeight() == 44 and _row125.label.font().pixelSize() == 14 \
                and "font-size:17px" in _wC.title.styleSheet() and len(_head_lbls) == 2 \
                and "#4a5bd6" not in _ST125._CSS and "74,91,214" not in _ST125._CSS
            check("R125 任务清单照滴答清单样式（44 行/14 字/大标题无徽章/灰白胶囊）", _ok125)
            # R126：自绘勾选框/头钮加大/行内间距压缩/跨屏按逻辑分辨率比自适应
            _box126 = _row125.box
            _ok126 = type(_box126).__name__ == "_RowCheck" and not _box126.isChecked()
            _sig126 = []
            _box126.toggled.connect(_sig126.append)
            _box126.setChecked(True)
            _ok126 = _ok126 and _box126.isChecked() and _sig126 == [True]
            _box126.setChecked(False)                                # 还原（不触发任务状态变化）
            _ok126 = _ok126 and _wC.btn_pin.width() >= 30 and _wC.btn_pin.objectName() == "headBtn" \
                and _row125.layout().spacing() == 5 and _row125.prio_bar.width() == 10  # R128 竖线热区
            from types import SimpleNamespace as _SN126
            _wC._scr_w = 1920
            _w0, _h0 = 300, 400
            _wC.resize(_w0, _h0)
            _fake_scr = _SN126(geometry=lambda: _SN126(width=lambda: 2560))
            _wC._on_screen_change(_fake_scr)
            _ok126 = _ok126 and _wC.width() == int(_w0 * 2560 / 1920) and _wC._scr_w == 2560
            check("R126 任务清单体验包（矢量勾选框/30px 头钮/间距压缩/跨屏缩放自适应）", _ok126)
            # R127：八向缩放/长任务多行/优先级勾选框色/钮图标化/面板缩放与跨屏
            import common as _C127
            _ok127 = isinstance(getattr(_wC, "_edge_resizer", None), _C127.EdgeResizer) \
                and isinstance(getattr(panel, "_edge_resizer", None), _C127.EdgeResizer) \
                and panel.minimumWidth() == 560 and callable(getattr(_wC, "_on_screen_change", None))
            _mgC.add("长" * 120)                                  # 长任务：行必须多行撑高全显
            _long_row = next(r for r in _wC.rows() if r.task["text"].startswith("长长长"))
            _ok127 = _ok127 and _long_row.label.wordWrap() and _long_row.height() > 44
            _mgC.set_prio(_long_row.task["id"], 3)               # 高优先级：勾选框与钮同步带色
            _lr2 = next(r for r in _wC.rows() if r.task["id"] == _long_row.task["id"])
            _ok127 = _ok127 and _lr2.prio_bar.prio == 3 and _lr2.label.wordWrap()  # R128 竖线承优先级
            _mgC.delete(_long_row.task["id"])
            check("R127 任务清单八向缩放/长任务多行全显/优先级竖线/面板缩放跨屏", _ok127)
            # R128：钉叉矢量化/优先级竖线/把手退役整行拖/×居中（R137：钉叉「同 18px」被视觉一致取代——钉 14/叉 20；R149 用户真机裁决叉显大：20→16）
            _ok128 = _wC.btn_pin.iconSize().width() == 14 and _wC.btn_hide.iconSize().width() == 16 \
                and _wC.btn_hide.text() == "" and not _wC.btn_hide.icon().isNull()
            _row128 = next(r for r in _wC.rows() if not r.done)
            _ok128 = _ok128 and isinstance(_row128.prio_bar, _ST125._PrioBar) \
                and not hasattr(_row128, "handle")
            _ok128 = _ok128 and _row128.box.height() == 22 and _row128.prio_bar.height() == 22 \
                and _row128.btn_del.height() == 22    # R129 行内四件同 22px 槽：中心线对齐
            check("R128 钉叉矢量化(R149 视觉一致钉14叉16)/优先级竖线同高勾选框/把手退役整行拖", _ok128)
            # R130：勾选框缩小 14px/分隔线文字左缘缩进/全局字体栈
            import inspect as _insp130
            _fam130 = app.font().families() if hasattr(app.font(), "families") else []
            _ok130 = _fam130 and _fam130[0] == "Segoe UI Variable Text" \
                and "border-bottom" not in _ST125._CSS \
                and _ST125.TaskRow.paintEvent.__qualname__.startswith("TaskRow") \
                and "adjusted(2, 4, -2, -4)" in _insp130.getsource(_ST125._RowCheck.paintEvent)
            check("R130 勾选框 14px/分隔线缩进/Segoe+雅黑字体栈", bool(_ok130))
            # R131：颜色/粗细收浮窗 + 工具快捷键自定义 + 截图直通录屏 + 面板弱文本字体族
            from capture import (ShotOverlay as _SO131, ToolPropsPop as _TPP131,
                                 ColorDot as _CD131)
            from PySide6.QtGui import QKeyEvent as _QKE131
            from PySide6.QtCore import QEvent as _QEv131
            _pm131 = QPixmap(800, 600); _pm131.fill(QColor("#203040"))

            class _FS131:
                def devicePixelRatio(self): return 1.0
                def geometry(self): return QRect(0, 0, 800, 600)
                def name(self): return "fake131"
            _ov131 = _SO131(_pm131, pick=False, screen=_FS131())
            _ov131.sel = QRect(100, 100, 400, 300)
            _ov131.phase = "edit"
            _ov131.show_toolbar()
            _ok131 = isinstance(_ov131.props_pop, _TPP131) \
                and not _ov131.props_pop.isHidden()       # 默认工具 rect：进编辑态即弹
            _ov131.set_tool("select")
            _ok131 = _ok131 and _ov131.props_pop.isHidden()                      # select 不弹
            _ok131 = _ok131 and len(_ov131.toolbar.findChildren(_CD131)) == 0  # 工具条无常驻色点
            _ov131.set_tool("rect")
            _ok131 = _ok131 and not _ov131.props_pop.isHidden()                # 标注工具即弹
            _ov131.set_color("#22c55e"); _ov131.set_size(7)
            _ok131 = _ok131 and _ov131.props_pop._swatches["#22c55e"].isChecked() \
                and _ov131.props_pop._size_dots[-1][1].isChecked()
            _ok131 = _ok131 and _ov131._tool_keys_rev.get("T") == "text"       # 默认键表反查
            _ek131 = _QKE131(_QEv131.KeyPress, Qt.Key_T, Qt.NoModifier)
            _ov131.keyPressEvent(_ek131)                                       # 按 T 切文字
            _ok131 = _ok131 and _ov131.cur_tool == "text"
            _ov131.set_tool("select")
            _ok131 = _ok131 and _ov131.props_pop.isHidden()                    # 非标注工具即收
            _ov131.close(); _ov131.deleteLater()
            check("R131 截图颜色/粗细浮窗（弹收/选中同步/常驻色点退役）", bool(_ok131))
            _hd131 = W.HotkeyDialog([("panel", "主面板")], {"panel": "Ctrl+Alt+Space"},
                                    hk_parse,
                                    extra={"title": "t", "actions": [("rect", "矩形"), ("text", "文字")],
                                           "cur": {"rect": "R", "text": "T"}})
            _ok131d = "tool:rect" in _hd131.btns and _hd131.cur2["text"] == "T"
            _hd131.btns["tool:rect"].click()
            _ok131d = _ok131d and _hd131.cap["key"] == "tool:rect"
            _hd131.keyPressEvent(_QKE131(_QEv131.KeyPress, Qt.Key_B, Qt.NoModifier))
            _ok131d = _ok131d and _hd131.cur2["rect"] == "B"                   # 无修饰单键生效
            _hd131.btns["tool:text"].click()
            _hd131.keyPressEvent(_QKE131(_QEv131.KeyPress, Qt.Key_B, Qt.ControlModifier))
            _ok131d = _ok131d and _hd131.cur2["text"] == "T"                   # 带 Ctrl 拒绝
            _hd131.cur2["text"] = "B"; _hd131._save()
            _ok131d = _ok131d and "重复" in _hd131.info.text()                 # 组内冲突拦截
            _hd131.close(); _hd131.deleteLater()
            check("R131 快捷键卡截图工具键组（捕获/拒修饰/冲突拦截）", bool(_ok131d))
            from recorder import RecBar as _RB131
            _rb131 = _RB131(on_done=lambda *a: None, on_error=lambda *a: None,
                            sel=QRect(10, 10, 200, 100), scr=None)
            _ok131r = _rb131.region == QRect(10, 10, 200, 100) \
                and "200×100" in _rb131.rlbl.text()
            _rb131.close(); _rb131.deleteLater()
            import inspect as _insp131
            import panel as _PNL131
            _qss131 = _insp131.getsource(_PNL131.Panel.qss)
            _ok131r = _ok131r and "#8a93a6" in _qss131 \
                and "'Segoe UI','Microsoft YaHei UI'" in _qss131
            check("R131 录屏预选区直通/面板弱文本字体族提色", bool(_ok131r))
            # R132：箭头实心锥形样式（样式钮贴身/默认切换/选中改对象/楔形像素）
            from PySide6.QtGui import QPainter as _QP132, QPen as _QPen132
            _pm132 = QPixmap(800, 600); _pm132.fill(QColor("#203040"))
            _ov132 = _SO131(_pm132, pick=False, screen=_FS131())
            _ov132.sel = QRect(100, 100, 400, 300); _ov132.phase = "edit"
            _ov132.show_toolbar()
            _ok132 = _ov132.cur_arrow_style == "solid" and _ov132.props_pop._btn_arrow_style.isHidden()
            _ov132.set_tool("arrow")
            _ok132 = _ok132 and not _ov132.props_pop._btn_arrow_style.isHidden() \
                and _ov132.props_pop._btn_arrow_style.text() == "锥"
            _ov132._cycle_arrow_style()                                        # 默认切换
            _ok132 = _ok132 and _ov132.cur_arrow_style == "line" \
                and _ov132.props_pop._btn_arrow_style.text() == "线"
            _ov132.stamps.append({"kind": "arrow", "a": QPoint(10, 10),
                                  "b": QPoint(200, 200), "r": QRect(10, 10, 190, 190),
                                  "color": "#ef4444", "w": 3, "style": "line"})
            _ov132.sel_stamp = ("shape", len(_ov132.stamps) - 1)
            _ov132._cycle_arrow_style()                                        # 选中＝改该对象
            _ok132 = _ok132 and _ov132.stamps[-1]["style"] == "solid" \
                and _ov132.cur_arrow_style == "line"                           # 默认不受影响
            _ov132.sel_stamp = None; _ov132.stamps.clear()
            _q132 = QPixmap(400, 200); _q132.fill(QColor("#ffffff"))
            _pt132 = _QP132(_q132); _pt132.setRenderHint(_QP132.Antialiasing)
            _pt132.setPen(_QPen132(QColor("#ef4444"), 5))
            _ov132._arrow_solid(_pt132, QPoint(40, 160), QPoint(360, 40), 5)
            _pt132.end()
            _im132 = _q132.toImage()

            def _red132(x, y):
                c = _im132.pixelColor(x, y)
                return c.red() > 200 and c.green() < 100
            _ok132p = (_red132(349, 44) and _red132(168, 112)
                       and _red132(324, 54) and not _red132(170, 118))
            _ok132 = _ok132 and _ok132p   # 头内/杆内填充、微信比例箭翼及杆外不填
            _ov132.close(); _ov132.deleteLater()
            check("R160 截图默认锥形+线锥切换/选中改单箭/锥形像素", bool(_ok132))
            # R133 mac 浅色系+lucide 图标管线（design.md）：色板令牌/默认主题/面板块图标/任务清单浅壳
            import icons as _IC133
            import panel as _PN133
            import inspect as _insp133
            _need133 = {"scissors", "scroll", "monitor-play", "pipette", "scan-text", "clipboard-list",
                        "film", "paintbrush", "qr-code", "calendar-days", "gauge", "timer",
                        "file-search", "list-todo", "lock", "monitor", "sun-moon", "keyboard",
                        "x", "folder", "pin", "plus", "flag"}
            _pm133 = _IC133.lucide("pin", "#2563eb", 18)
            _img133 = _pm133.toImage()
            _opaque133 = any(_img133.pixelColor(_x, _y).alpha() > 0
                             for _x in range(0, _img133.width(), 3) for _y in range(0, _img133.height(), 3))
            _ok133 = (_need133 <= set(_IC133.LUCIDE) and _opaque133
                and C.DEFAULTS["theme"] == "light"
                and C.LIGHT["panel"] == "#f5f5f7"              # R134 实色化；R145 整窗灰底容器化（同 panel_solid）
                and C.LIGHT["panel_solid"] == "#f5f5f7" and C.LIGHT["card"] == "#ffffff")
            _pn133 = _PN133.Panel(dict(C.DEFAULTS))
            _ok133 = (_ok133 and _pn133.pal is C.LIGHT
                and all(_PN133.ACT_LUCIDE.get(m) for _g, _n, _gl, _b, _d, _h, m in _PN133.ACTIONS))
            _blk133 = _pn133.rows[0][0]
            _em133 = _blk133.findChild(QLabel, "emLbl")
            _ok133 = _ok133 and _em133 is not None and not _em133.pixmap().isNull()
            _pn133.hide(); _pn133.deleteLater()
            _ok133 = (_ok133 and "rgba(16,20,30,202)" in sticky._CSS          # R137：浅壳被用户点名回退，黑透回归（详 R137 锁）
                and "QColor(255, 255, 255" in _insp133.getsource(sticky._RowCheck.paintEvent))
            check("R133 mac浅色系+lucide（23图标渲染/默认light/面板块图标；任务清单壳 R137 黑透回归）", bool(_ok133))
            # R134：面板实色化+浅色卡片去硬边柔影（用户点名「边缘紧紧挨在一起不符合标准」）；
            # 三侧工具条白色描边退役（「非常非常粗糙」）；辐条 WA_StyledBackground 底色（纯透明事故修复）；
            # 实心箭头重绘＝R157 微信形态（尾尖→缓锥→翼张→收尖三段连续；R134 等宽杆/R142 两段楔形均退役）
            # 双竖线随属性钮范式退役消除
            import inspect as _insp134
            import capture as _cap134
            import recorder as _REC134
            import longshot as _LS134
            import gifrec as _GIF134
            _ok134 = C.LIGHT["panel"] == "#f5f5f7" and "rgba" not in C.LIGHT["panel"]   # R145 灰底
            _qssP134 = _insp134.getsource(_PN133.Panel.qss)
            _ok134 = _ok134 and "border:1px solid {cline}" not in _qssP134 \
                and hasattr(_PN133.Panel, "_soft_shadow")  # 卡片描边退役（R134 浅色退/R159 深色同退）+柔影托底
            _srcTB134 = _insp134.getsource(_cap134.ShotOverlay._tb_qss)   # R147 QSS 抽方法随迁移
            _ok134 = _ok134 and "border:none" in _srcTB134 \
                and "rgba(255,255,255,26)" not in _srcTB134          # 截图工具条白描边退役
            _ok134 = _ok134 and "border:none" in _insp134.getsource(_REC134.RecBar.__init__) \
                and "border:none" in _insp134.getsource(_LS134.LongShotBar.__init__) \
                and "border:none" in _insp134.getsource(_GIF134.GifBar.__init__)   # 三侧控制条同退役
            _srcTPP134 = _insp134.getsource(_cap134.ToolPropsPop.__init__)
            _ok134 = _ok134 and "WA_StyledBackground" in _srcTPP134 \
                and "_btn_arrow_style" in _srcTPP134                 # 辐条底色前提+选项钮迁入
            _ok134 = _ok134 and "ToolPropsPop{background:rgba(22,24,32,246);border:none" in _srcTPP134  # 辐条壳无描边（选项钮 R141 起亦无描边）
            _srcAS134 = _insp134.getsource(_cap134.ShotOverlay._arrow_solid)
            _ok134 = _ok134 and "head_base_h" in _srcAS134 and "transition_f" in _srcAS134 \
                and "head_base_f" in _srcAS134   # R183 平顺箭身与等腰头沿弧长定形
            check("R134 面板实色柔影/三侧条去白边/辐条底色/箭头重绘/双竖线消除", bool(_ok134))
            # R135：录屏入口图标——面板 lucide monitor-play（video 退役）；工具条侧 R136 红点回归、R144 再改 monitor-play（见下锁）
            import icons as _IC135
            _ok135 = (_PN133.ACT_LUCIDE.get("rec") == "monitor-play"
                and "monitor-play" in _IC135.LUCIDE and "video" not in _IC135.LUCIDE)
            check("R135 录屏面板图标 monitor-play（video 退役）", bool(_ok135))
            # R136（用户真机验收四点）：①录屏去红——面板块 #ef4444→#0ea5e9（「默认不要用红色」）；②工具条红点回归——
            # _LUCIDE_FWD 转发退役、_draw 红点分支恢复（「还是应该用那种小红点的」；R144 用户再改口，工具条 rec 钮
            # 改 lucide monitor-play 与面板同源；gifrec/longshot/recorder 开始钮红点保留，_draw 红点分支不下线）；
            # ③#body 灰底加圆角 12；④DARK.panel 实色化（「暗色主题也改为不透明的」）
            _ok136 = (not hasattr(_IC135, "_LUCIDE_FWD")
                and 'kind == "rec"' in _insp133.getsource(_IC135._draw))
            _c136 = _IC135.pixmap("rec", "#ef4444", 20).toImage().pixelColor(10, 10)
            _ok136 = _ok136 and _c136.red() > 200 and _c136.green() < 120    # 工具条红点：中心实心纯红
            _ok136 = _ok136 and any(_a[1] == "录屏" and _a[3] == "#0ea5e9" for _a in _PN133.ACTIONS_FULL)
            _bodyLn136 = [_l for _l in _insp133.getsource(_PN133.Panel.qss).splitlines() if "#body" in _l]
            _ok136 = _ok136 and _bodyLn136 and "border-radius:10px" in _bodyLn136[0]   # R146 圆角收 mac 档
            _ok136 = _ok136 and C.DARK["panel"] == "#1a1c26" and "rgba" not in C.DARK["panel"]
            _srcTB136 = _insp133.getsource(_cap134.ShotOverlay.show_toolbar)
            _ok136 = _ok136 and 'tbtn("rec"' not in _srcTB136 and 'lucide="monitor-play"' in _srcTB136 \
                            and 'color="#ef4444"' not in _srcTB136                      # R144：工具条 rec 红点退役
            check("R136 录屏去红/灰底圆角/暗色实色（工具条红点 R144 改 monitor-play）", bool(_ok136))
            # R137（用户真机验收两点）：①任务清单黑透回归（「还是用原来透明的样式，黑透」）——壳 rgba(16,20,30,202) R89 基准、
            # 白系描边/分隔线/滚动条/斜纹、阴影 26/130、蓝紫胶囊 #4a5bd6 回归；②钉叉视觉一致——钉 14px/叉 20px
            #（实测 lucide pin bbox 12x17 墨98 vs x 10x10 墨52，钉天然大 70%，尺寸补偿拉齐）；③小窗图标同源审计：
            # 面板头叉 15→18 补偿（x 天然 bbox 仅 50% 离群）、日历钉/四箭头 lucide 化（程序化 pin/chevl 系小窗退役）
            _ok137 = ("rgba(16,20,30,202)" in sticky._CSS and "252,252,254" not in sticky._CSS
                and "rgba(96,165,250,160)" in sticky._CSS)   # 胶囊 focused 蓝边（R125 语义深色版，非常态蓝紫）
            _srcPin137 = _insp133.getsource(sticky.TaskWidget._sync_pin_icon)
            _srcW137 = _insp133.getsource(sticky.TaskWidget.__init__)
            _ok137 = _ok137 and '"pin", "#60a5fa" if on else "#9ca3af", 14' in _srcPin137 \
                and 'lucide_icon("x", "#9ca3af", 16)' in _srcW137                     # R149 钉叉补偿改 14/16
            _ok137 = _ok137 and '_HoverIconBtn("x", 18' in _insp133.getsource(_PN133.Panel.build_ui)  # 面板头叉 15→18（R145 追加 sw=2.4；R159 换 _HoverIconBtn 双图标）
            import widgets as _WD137
            _srcCal137 = _insp133.getsource(_WD137.CalendarWindow)
            _ok137 = _ok137 and 'lucide_icon("pin"' in _srcCal137 and 'lucide="chevrons-left"' in _srcCal137  # 日历钉/箭头同源
            _ok137 = _ok137 and 'lucide_icon("x", "#9ca3af", 16)' in _srcCal137 \
                and 'lucide_icon("x", "#9ca3af", 16)' in _insp133.getsource(_WD137.CardDialog)  # 日历/对话框 R149 随 sticky 同收 16
            _ok137 = _ok137 and {"chevron-left", "chevron-right", "chevrons-left", "chevrons-right"} <= set(_IC133.LUCIDE)
            _ok137 = _ok137 and not _IC133.lucide("chevron-left", "#a7adbc", 20).isNull()
            check("R137 任务清单黑透回归/钉叉视觉一致(R149 钉14叉16)/面板头叉补偿/日历钉箭头lucide同源", bool(_ok137))
            # R138（用户建议两点）：①工具钮图标下方键位标签——8px 弱白 QLabel#keyTip 贴钮底居中（精致不生硬）、
            # WA_TransparentForMouseEvents 不挡点击；settings["shot_key_tips"] 开关可关（默认开）；
            # ②工具条设置入口——keyboard lucide 钮（pin 后 close 前），菜单=显示开关 checkable+HotkeyDialog 仅工具组；
            # 改键当次会话即时生效（反查表/tooltip/键位标签三路刷新），tooltip 基于 _base_tip 快照重建不累积
            _ov138 = _SO131(_pm131, pick=False, screen=_FS131())
            _ov138.sel = QRect(100, 100, 400, 300)
            _ov138.phase = "edit"
            _ov138.show_toolbar()
            _ok138 = hasattr(_ov138, "btn_keys") and not _ov138.btn_keys.icon().isNull()
            _kt138 = getattr(_ov138, "_key_tips", {})
            _ok138 = _ok138 and len([_k for _k in _kt138 if not _k.startswith("act:")]) == len(_ov138.tool_btns) \
                and _kt138["rect"].text() == "R" and _kt138["text"].text() == "T"   # 默认键位标签文本
            _ok138 = _ok138 and _ov138._key_tips_on is True and not _kt138["rect"].isHidden()   # 默认显示
            _ok138 = _ok138 and _kt138["rect"].testAttribute(Qt.WA_TransparentForMouseEvents)
            _calls138 = []
            _orig_sv138 = C.save_settings
            C.save_settings = lambda s: _calls138.append(dict(s))      # 锁内不写真实 settings
            _ov138._set_key_tips(False)
            _ok138 = _ok138 and _kt138["rect"].isHidden() \
                and _calls138 and _calls138[-1].get("shot_key_tips") is False        # 开关：即时隐藏+写盘
            _ov138._set_key_tips(True)
            _ok138 = _ok138 and _calls138[-1].get("shot_key_tips") is True and not _kt138["rect"].isHidden()
            C.save_settings = _orig_sv138
            _ov138._tool_keys_rev = {"B": "rect"}                                     # 改键刷新链路
            _ov138._refresh_tool_key_tips()
            _ok138 = _ok138 and _kt138["rect"].text() == "B" \
                and _ov138.tool_btns["rect"].toolTip().count("（") == 1               # tooltip 不累积
            _ok138 = _ok138 and callable(getattr(_ov138, "_open_keys_menu", None)) \
                and callable(getattr(_ov138, "_edit_tool_keys", None))
            _ov138.close(); _ov138.deleteLater()
            check("R138 工具钮键位标签(精致可关)/工具条设置入口/改键即时刷新tooltip不累积", bool(_ok138))
            # R139（用户点名「面板最下方的那个切换主题就可以去掉了」）：底部网格主题块退役，
            # 头部 sun-moon 钮保留（toggle_theme_placeholder → fire theme → Controller.theme 链路不动）
            import panel as _pn139
            import icons as _IC139
            _ok139 = not any(m == "theme" for *_x, m in _pn139.ACTIONS_FULL) \
                and "theme" not in _pn139.ACT_LUCIDE \
                and "sun-moon" in _IC139.LUCIDE \
                and hasattr(Controller, "theme") \
                and callable(getattr(_pn139.Panel, "toggle_theme_placeholder", None))
            check("R139 面板底部主题块退役(头部主题钮保留)", _ok139)
            # R140（用户点名「主面板左上角图标不太精致」）：①头标按屏 DPR 物理渲染（原 34px 逻辑图
            # 在 125%/150% 屏拉伸发糊是主凶）；②取景框呼吸位重分配（臂端/点缘间距 3/256→9/256）+高光收淡
            import common as _CM140
            import inspect as _insp140
            _src140 = _insp140.getsource(_CM140.app_icon)
            _lp140 = panel._logo.pixmap()
            _ok140 = "62 * S, 40 * S" in _src140 and "17 * S, 17 * S" in _src140 \
                and "255, 255, 255, 52" in _src140 \
                and _lp140 is not None and not _lp140.isNull() \
                and _lp140.devicePixelRatio() == panel.devicePixelRatioF() \
                and _lp140.width() == round(34 * _lp140.devicePixelRatio())
            check("R140 头标 DPR 渲染+取景框呼吸位精修", _ok140)
            # R141（用户点名「辐条选项钮边框看起来比较糊，要不把边框去掉」）：_OPT_QSS 1px 半透蓝边退役，
            # 底色 38→48 撑形态+字色微提；R134 两侧锁已同步改 48+border:none，此处锁防回归
            import capture as _cap141
            _ok141 = "1px solid" not in _cap141.ToolPropsPop._OPT_QSS \
                and "border:none" in _cap141.ToolPropsPop._OPT_QSS \
                and "rgba(59,130,246,48)" in _cap141.ToolPropsPop._OPT_QSS
            check("R141 辐条选项钮去描边(蓝族底色撑形)", _ok141)
            # R142（用户点名两点）：①实心锥形方向修正「箭头部分应该是粗的，箭尾部分应该是细的」——
            # 尾细头粗渐进楔形（尾半宽 0.5w→头基杆 1.05w 线性渐宽→头 2.7×杆收尖，R134 等宽杆退役）；
            # ②锥形接上弯势——m 中点柄拖出的 c（R100 体系）经 _paint_shape 传入 _arrow_solid，
            # 沿贝塞尔采样变宽带渲染（c=None 退化直线，弯直同路径）；longedit 覆写同步 c 透传
            import capture as _cap142
            import longedit as _le142
            import inspect as _insp142
            from PySide6.QtGui import QPainter as _QP142, QPen as _QPen142
            _src142 = _insp142.getsource(_cap142.ShotOverlay._arrow_solid)
            _srcPS142 = _insp142.getsource(_cap142.ShotOverlay._paint_shape)
            _srcLE142 = _insp142.getsource(_le142.LongCanvas._arrow_solid)
            _pm142 = QPixmap(800, 600); _pm142.fill(QColor("#203040"))
            _ov142 = _SO131(_pm142, pick=False, screen=_FS131())
            _q142 = QPixmap(400, 200); _q142.fill(QColor("#ffffff"))
            _pt142 = _QP142(_q142); _pt142.setRenderHint(_QP142.Antialiasing)
            _pt142.setPen(_QPen142(QColor("#ef4444"), 5))
            _ov142._arrow_solid(_pt142, QPoint(40, 160), QPoint(360, 40), 5)
            _pt142.end()
            _im142 = _q142.toImage()

            def _red142(x, y):
                c = _im142.pixelColor(x, y)
                return c.red() > 200 and c.green() < 100
            _ok142 = "c=None" in _src142 and "head_base_f" in _src142 and "head_base_h" in _src142 \
                and 's.get("c")' in _srcPS142 and "c=None" in _srcLE142 \
                and _red142(324, 54) and not _red142(70, 154)   # 收窄三角头/尖尾
            _q142b = QPixmap(400, 220); _q142b.fill(QColor("#ffffff"))
            _pt142b = _QP142(_q142b); _pt142b.setRenderHint(_QP142.Antialiasing)
            _pt142b.setPen(_QPen142(QColor("#ef4444"), 5))
            _ov142._arrow_solid(_pt142b, QPoint(40, 160), QPoint(360, 160), 5, QPoint(200, 60))
            _pt142b.end()
            _im142b = _q142b.toImage()
            _ok142 = _ok142 and _red142(200, 110) is not None      # 占位防误删（下两句真断言）
            _im142b_c = _im142b.pixelColor(200, 110)
            _im142b_d = _im142b.pixelColor(200, 160)
            _ok142 = _ok142 and _im142b_c.red() > 200 and _im142b_c.green() < 100   # 弯弧中点有墨
            _ok142 = _ok142 and not (_im142b_d.red() > 200 and _im142b_d.green() < 100)  # 弦中点无墨＝弯离
            _ov142.close(); _ov142.deleteLater()
            check("R142 锥形尾细头粗楔形+弯锥渲染(m 柄 c 接上)", _ok142)
            # R143（用户点名「high 浮窗左边有一个空白区域，可以压缩一下」）：辐条固定间距项随区收放——
            # addSpacing 是永久布局项不随 widget 隐藏（high 隐 cs 区后 4 个 4px 间距+边距全成左空白），
            # 改 QSpacerItem 保留引用+changeSize(0,0)；opt_sep 前后间距随 show_sep（rect 系右端顺带收紧）
            import capture as _cap143
            import inspect as _insp143
            _pm143 = QPixmap(800, 600); _pm143.fill(QColor("#203040"))
            _ov143 = _SO131(_pm143, pick=False, screen=_FS131())
            _ov143.sel = QRect(100, 100, 400, 300); _ov143.phase = "edit"
            _ov143.show_toolbar()
            _ov143.set_tool("num"); _wn143 = _ov143.props_pop.size().width()
            _ov143.set_tool("high"); _wh143 = _ov143.props_pop.size().width()
            _ok143 = _wh143 <= 60 and (_wn143 - _wh143) >= 30 \
                and "changeSize" in _insp143.getsource(_cap143.ToolPropsPop.set_tool)
            _ov143.close(); _ov143.deleteLater()
            check("R143 辐条 high 态空白压缩(间距项随区收放)", _ok143)
            # R144（用户三点）：①那一排动作钮也贴键位标签（GIF/提取文字等「仍然可以加上快捷键」）——
            # 动作单键 X/G/C/L/S/B 标注态无修饰直触发（避开工具键），undo 沿用 Ctrl+Z 贴「^Z」，标签随同一开关显隐；
            # ②工具条录屏钮小红点改 lucide monitor-play（与面板同源，用户改口「还是应该改成类似视频或录屏的样式」）；
            # ③PulseDot 激活态红点外圈加线形圆环（「外层加一个线形的小圆边框」，REC 灯经典造型，环恒显不呼吸）
            import icons as _IC144
            from PySide6.QtGui import QKeyEvent as _KE144
            from PySide6.QtCore import QEvent as _QE144
            _pm144 = QPixmap(800, 600); _pm144.fill(QColor("#203040"))
            _ov144 = _SO131(_pm144, pick=False, screen=_FS131())
            _ov144.sel = QRect(100, 100, 400, 300); _ov144.phase = "edit"
            _ov144.show_toolbar()
            _calls144 = {}
            for _mn in ("do_ocr", "start_rec", "start_gif", "start_longshot", "do_save", "do_pin"):
                setattr(_ov144, _mn, (lambda _n: lambda *a, **k: _calls144.__setitem__(_n, True))(_mn))
            _ov144.keyPressEvent(_KE144(_QE144.KeyPress, Qt.Key_G, Qt.NoModifier))
            _ov144.keyPressEvent(_KE144(_QE144.KeyPress, Qt.Key_X, Qt.NoModifier))
            _ov144.keyPressEvent(_KE144(_QE144.KeyPress, Qt.Key_C, Qt.NoModifier))
            _ok144 = bool(_calls144.get("start_gif")) and not _calls144.get("do_pin")
            if LITE:
                _ok144 = _ok144 and "act:ocr" not in _ov144._key_tips \
                    and not _calls144.get("do_ocr") and not _calls144.get("start_rec")
            else:
                _ok144 = _ok144 and bool(_calls144.get("do_ocr")) and bool(_calls144.get("start_rec")) \
                    and _ov144._key_tips["act:ocr"].text() == "X"
            _ok144 = _ok144 and _ov144._key_tips["act:gif"].text() == "G" \
                and not _ov144._key_tips["act:gif"].isHidden() \
                and _ov144._key_tips["act:undo"].text() == "^Z"
            # rec 钮走 lucide（源码级断言——LITE 裁录屏钮不产生缓存键，不能用缓存断言）
            _ok144 = _ok144 and 'lucide="monitor-play"' in _insp143.getsource(_cap143.ShotOverlay.show_toolbar)
            _pd144 = _IC144.PulseDot(); _pd144.set_active(True)
            _pmPD = QPixmap(14, 14); _pmPD.fill(Qt.transparent)
            _pd144.render(_pmPD)                       # paint device 版（单参 QPainter 版需 targetOffset）
            _imPD = _pmPD.toImage()
            _ok144 = _ok144 and _imPD.pixelColor(7, 1).red() > 150 and _imPD.pixelColor(7, 1).green() < 120  # 环顶红
            _ok144 = _ok144 and "ring" in _insp143.getsource(_IC144.PulseDot.paintEvent)
            _pd144.set_active(False); _pd144.deleteLater()
            _ov144.close(); _ov144.deleteLater()
            check("R144 动作键位标签+动作单键/录屏钮 monitor-play/REC 灯圆环", bool(_ok144))
            # R145（用户点名两点）：①浅色整窗灰底容器化——LIGHT.panel #fafafd→#f5f5f7（=panel_solid
            # 同值，body 灰底与壳融合），白卡+柔影浮灰底（mac 系统设置范式），保存条/头部/动作区同落
            # 灰底（「保存位置条没有灰底」根治）；②暗色头部 iconBtn 去描边撑底色（1px 8% 白边发虚）
            # +头部三钮 lucide sw 2.4 加墨（15px 下 1.25px→1.5px 描边，与 R137 小窗补偿同源）
            import panel as _PN145
            _pl145 = _PN145.Panel(dict(C.DEFAULTS))
            _qssL145 = _pl145.qss()                              # 浅色实例：iconBtn 保留细边
            _SD145 = dict(C.DEFAULTS); _SD145["theme"] = "dark"
            _pd145 = _PN145.Panel(_SD145)
            _qssD145 = _pd145.qss()
            _ibLn145 = [_l for _l in _qssD145.splitlines() if "#iconBtn {" in _l]
            _srcB145 = _insp143.getsource(_PN145.Panel.build_ui)
            _ok145 = (C.LIGHT["panel"] == "#f5f5f7" and C.LIGHT["panel"] == C.LIGHT["panel_solid"]
                and _ibLn145 and "border:none" in _ibLn145[0] and "rgba(255,255,255,18)" in _ibLn145[0]
                and "border:1px solid" in [_l for _l in _qssL145.splitlines() if "#iconBtn {" in _l][0]
                and _srcB145.count("sw=2.4") >= 3)                # keyboard/sun-moon/x 三钮加墨
            _pl145.hide(); _pl145.deleteLater(); _pd145.hide(); _pd145.deleteLater()
            check("R145 浅色整窗灰底容器化/暗色头部钮去描边+图标加墨", bool(_ok145))
            # R146（用户拍板「以 Mac 上应用的设计标准作为设计」→「你全都干了吧」）：五窗 mac 化——
            # 待办/番茄钟/快记/剪贴板历史/文件搜索统一 _plugin_shell 黑透插件壳（sticky R137 范式：
            # 无边框 Tool+Frameless、WA_TranslucentBackground、body QFrame#pluginBody #10141e（R155 实色化）
            # 圆角 10+1px 白 18 边、柔影 26/(0,4)/130、标题栏 38 高钉 14/叉 20 lucide、_DragFilter 拖动、
            # Esc QShortcut 窗级关闭、EdgeResizer 八向缩放）；控件体系 PLUGIN_QSS 黑透无描边（分区靠
            # 底色梯度不靠 1px 半透描边）；圆角阶梯收 mac 档（HIG 窗/卡 10）：UI_RADIUS 14→10、
            # panel #root/pillBtn/body/actBlock/tile、sticky taskBody、nsBody、recorder/gifrec/longshot
            # 控制条壳全落 10；待办勾选框复用 sticky._RowCheck 自绘矢量框（DPR 锐利同源），添加钮退役
            # （回车添加，mac 提醒事项范式）；番茄钟开始钮 #primaryBtn accent 实心（mac 主操作语义）；
            # 快记「已自动保存」并入标题栏副位；BASE_QSS 保留（longedit 长截图编辑器在用，不在清单）
            from PySide6.QtWidgets import QFrame as _QFR146, QToolButton as _QTB146
            from PySide6.QtGui import QShortcut as _QSC146
            import inspect as _insp146

            def _shell_ok146(w):
                _b = w.findChild(_QFR146, "pluginBody")
                _hbs = w.findChildren(_QTB146, "headBtn")
                _esc = [s for s in w.findChildren(_QSC146) if s.key().toString() == "Esc"]
                return (_b is not None and bool(w.windowFlags() & Qt.FramelessWindowHint)
                        and w.testAttribute(Qt.WA_TranslucentBackground)
                        and len(_hbs) == 2 and all(not b.icon().isNull() for b in _hbs)
                        and len(_esc) == 1)

            _orig_todo146, _orig_notes146 = C.TODO_PATH, C.NOTES_PATH
            C.TODO_PATH = os.path.join(_tmp_st, "todos_r146.json")
            C.NOTES_PATH = os.path.join(_tmp_st, "notes_r146.md")
            try:
                _ok146 = (hasattr(W, "_plugin_shell") and hasattr(W, "_DragFilter")
                    and "QFrame#pluginBody" in W.PLUGIN_QSS
                    and "#10141e" in W.PLUGIN_QSS and "rgba(16,20,30,202)" not in W.PLUGIN_QSS
                    and "border-radius:10px" in W.PLUGIN_QSS
                    and "#primaryBtn" in W.PLUGIN_QSS)                # R155 壳实色化同步改断言（§7.2）
                C.save_todos([{"t": "甲", "done": False}, {"t": "乙", "done": True}])
                _tw146 = W.TodoWindow()                       # 待办：壳+_RowCheck 复用+添加钮退役
                _ok146 = _ok146 and _shell_ok146(_tw146) \
                    and "_RowCheck" in _insp146.getsource(W.TodoWindow.render) \
                    and "boundedTo" not in _insp146.getsource(W.TodoWindow.render) \
                    and _tw146.listw.count() == 2 \
                    and "setFixedHeight(34)" in _insp146.getsource(W.TodoWindow.render) \
                    and _tw146.listw.itemWidget(_tw146.listw.item(0)).minimumHeight() == 34 \
                    and not _tw146.findChildren(QPushButton) \
                    and _tw146.cnt.text().startswith("剩余")
                _pm146 = W.PomodoroWindow(dict(C.DEFAULTS), save=lambda: None)
                _ok146 = _ok146 and _shell_ok146(_pm146) \
                    and _pm146.go.objectName() == "primaryBtn"
                _nt146 = W.NotesWindow()
                _ok146 = _ok146 and _shell_ok146(_nt146) \
                    and "notes.md" in _nt146.saved.text()
                _ch146 = W.ClipHistoryWindow([])
                _ok146 = _ok146 and _shell_ok146(_ch146) \
                    and "border:none" in W.CLIP_QSS \
                    and not _ch146.scroll.viewport().autoFillBackground()   # R95 同判例：透底防白版
                _chbody146 = _ch146.scroll.widget()
                _ok146 = _ok146 and _chbody146 is not None \
                    and not _chbody146.autoFillBackground()
                if not LITE:                                   # fsearch 与 Lite 互斥（同 2.9 锁先例）
                    import fsearch as _FS146
                    _fw146 = _FS146.FileSearchWindow(auto_index=False,
                                                     db=os.path.join(_tmp_st, "fs_r146.db"))
                    _ok146 = _ok146 and _shell_ok146(_fw146) \
                        and "border-radius:10px" in _FS146.FSEARCH_QSS
                    _fw146.hide(); _fw146.deleteLater()
                for _w146 in (_tw146, _pm146, _nt146, _ch146):
                    _w146.hide(); _w146.deleteLater()
                # 圆角 mac 档（窗/卡级统一 10；小控件不动）
                import panel as _PN146
                import recorder as _REC146
                import gifrec as _GIF146
                import longshot as _LS146
                _p146 = _PN146.Panel(dict(C.DEFAULTS))
                _rootLn146 = [_l for _l in _p146.qss().splitlines() if "#root {" in _l]
                _p146.hide(); _p146.deleteLater()
                _ok146 = _ok146 and C.UI_RADIUS == 10 \
                    and _rootLn146 and "border-radius:10px" in _rootLn146[0] \
                    and "border-radius:10px" in sticky._CSS \
                    and "border-radius:14px" not in sticky._CSS \
                    and "border-radius:10px" in _insp146.getsource(_REC146) \
                    and "border-radius:12px" not in _insp146.getsource(_REC146) \
                    and "border-radius:10px" in _insp146.getsource(_GIF146) \
                    and "border-radius:12px" not in _insp146.getsource(_GIF146) \
                    and "border-radius:10px" in _insp146.getsource(_LS146) \
                    and "border-radius:12px" not in _insp146.getsource(_LS146)
            finally:
                C.TODO_PATH, C.NOTES_PATH = _orig_todo146, _orig_notes146
            check("R146 五窗 mac 化统一壳（插件壳 R155 实底/钉×/Esc/圆角 10）", bool(_ok146))
            # R147（用户点名「设置工具条上不显示快捷键，那工具条应该适当收窄，不应该保持原来那个宽度」）：
            # _key_tips_on 读取提前到构建期，QSS 抽 _tb_qss() 按开关分档（开 padding 9+钮高 36/关 padding 0
            # +钮高 30），_apply_key_tips 即时改尺寸+adjustSize+place_toolbar 重排。
            # 〔R147①「对号/叉号不贴标签」判例已被 R152 用户点名推翻：现已补贴 Esc/↩，下方断言同步反转〕
            _pm147 = QPixmap(800, 600); _pm147.fill(QColor("#203040"))
            _ov147 = _SO131(_pm147, pick=False, screen=_FS131())
            _ov147.sel = QRect(100, 100, 400, 300); _ov147.phase = "edit"
            _ov147.show_toolbar()                                       # 默认开标签
            _bt147 = [b for b in _ov147.toolbar.findChildren(QPushButton) if b.objectName() == "tbBtn"]
            _ok147 = (hasattr(_ov147, "_tb_qss")
                and "padding:0 0 9px 0" in _ov147._tb_qss()
                and _bt147 and all(b.height() == 36 for b in _bt147))
            _kt147 = getattr(_ov147, "_key_tips", {})
            _ok147 = _ok147 and all(k in _kt147 for k in ("act:confirm", "act:close"))   # R152 起已贴签（原不贴判例反转）
            _h147 = _ov147.toolbar.height()
            _calls147 = []
            _orig_sv147 = C.save_settings
            C.save_settings = lambda s: _calls147.append(dict(s))       # 锁内不写真实 settings
            _ov147._set_key_tips(False)
            _ok147 = _ok147 and "padding:0 0 0px 0" in _ov147._tb_qss() \
                and all(b.height() == 30 for b in _bt147) \
                and _ov147.toolbar.height() < _h147                      # 即时收窄
            _ov147._set_key_tips(True)
            _ok147 = _ok147 and all(b.height() == 36 for b in _bt147) \
                and _ov147.toolbar.height() == _h147
            C.save_settings = _orig_sv147
            _s147 = C.load_settings(); _s147["shot_key_tips"] = False   # 构建期分档：关标签新条直接 30
            _orig_ls147 = C.load_settings
            C.load_settings = lambda: dict(_s147)
            _ov147b = _SO131(_pm147, pick=False, screen=_FS131())
            _ov147b.sel = QRect(100, 100, 400, 300); _ov147b.phase = "edit"
            _ov147b.show_toolbar()
            _ok147 = _ok147 and all(b.height() == 30 for b in _ov147b.toolbar.findChildren(QPushButton)
                                    if b.objectName() == "tbBtn")
            C.load_settings = _orig_ls147
            _ov147.close(); _ov147.deleteLater()          # 锁段 overlay 必销（构造即 visible，残留会顶翻 R112 顶层可见断言）
            _ov147b.close(); _ov147b.deleteLater()
            check("R147 关标签工具条收窄（36↔30+padding 9↔0·叉对 R152 起已贴签）", bool(_ok147))
            # 热键表含 note 且默认组合可解析（R107 轻量版热键裁 note）
            _okS2 = (all(k != "note" for k, _l in _HOTKEY_ACTIONS) if LITE
                     else any(k == "note" for k, _l in _HOTKEY_ACTIONS)
                     and hk_parse(DEFAULT_HOTKEYS["note"]) is not None)
            check("R87 任务热键（_HOTKEY_ACTIONS 含 note · 默认 Alt+N 可解析）", _okS2)
            # R89：完成区折叠沉底/整行拖拽排序/勾选成功过渡（延迟+收缩）/过渡中止/重建收割/真动画链
            C.TASKS_PATH = os.path.join(_tmp_st, "tasks_r89.json")   # 干净库
            _mgD = _ST.TaskManager(); _mgD.show_widget(); _wD = _mgD.widget
            _a = _mgD.add("甲"); _b = _mgD.add("乙"); _c = _mgD.add("丙"); _d = _mgD.add("丁")
            _ra = next(r for r in _wD.rows() if r.task["id"] == _a["id"])
            _ra.box.setChecked(True); _ra._commit_done()              # 甲完成（快进）
            _okG1 = _wD._done_collapsed is True                       # ① 默认折叠
            _okG1 = _okG1 and all(r.task["id"] != _a["id"] for r in _wD.rows()) \
                and _wD.done_hdr() is not None                       # 完成行收起·头还在
            _wD.toggle_done_collapsed()
            _okG1 = _okG1 and any(r.task["id"] == _a["id"] for r in _wD.rows()) \
                and _mgD.win.get("done_collapsed") is False          # 展开+状态入 win
            _wD.toggle_done_collapsed()
            _mgD.save()
            _okG1 = _okG1 and C.load_tasks()["win"].get("done_collapsed") is True   # 折叠态落盘
            _okG1 = _okG1 and [r.task["text"] for r in _wD.rows() if not r.done] == ["丁", "丙", "乙"]
            check("R89a 完成区折叠沉底（默认收/展开/落盘）", _okG1)
            check("R95 列表区透底（viewport/list_host 不填实色盖住半透）",
                  not _wD.scroll.viewport().autoFillBackground()
                  and not _wD.list_host.autoFillBackground())
            # R96：点击=幂等唤起（可见不隐藏）；热键=开关；Controller 双入口绑定正确
            _mgD.show_or_raise()                                       # 已可见：不得隐藏
            _okR96 = _wD.isVisible()
            _mgD.hide_widget(); _mgD.show_or_raise()                   # 不可见：等于 show
            _okR96 = _okR96 and _wD.isVisible()
            _mgD.toggle()                                              # 热键语义保持开关
            _okR96 = _okR96 and not _wD.isVisible()
            _mgD.show_widget()                                         # 复原：后续段需要可见窗
            import inspect as _insp96
            _okR96 = _okR96 and "show_or_raise()" in _insp96.getsource(Controller.note) \
                and "toggle()" in _insp96.getsource(Controller.note_toggle)
            check("R96 点击幂等唤起/热键开关（可见时点击不隐藏）", _okR96)
            # R98：优先级下拉（菜单四档/行级改级落数据+重建）/删除钮 hover 现身
            _okR98 = not hasattr(_wD, "cycle_prio")                    # 循环切换已除
            _m98 = _wD._prio_menu(2, lambda p: None)
            _okR98 = _okR98 and len(_m98.actions()) == 4 \
                and all(a.isCheckable() for a in _m98.actions()) \
                and _m98.actions()[2].isChecked() \
                and not _m98.actions()[2].icon().isNull()
            _r0 = next(r for r in _wD.rows() if r.task["id"] == _b["id"])     # 无级行
            _okR98 = _okR98 and _r0.prio_bar.prio == 0 and _r0.prio_bar.height() == 22  # R129 同槽
            _mgD.set_prio(_b["id"], 3)                                 # 行级改级落数据
            _okR98 = _okR98 and _mgD.tasks[_b["id"]]["prio"] == 3
            app.processEvents()        # offscreen 无事件循环：rebuild 新行不级联可见，先补一拍
            _r98 = next(r for r in _wD.rows() if r.task["id"] == _b["id"])   # rebuild 后新行
            _okR98 = _okR98 and _r98.prio_bar.prio == 3                # R128 竖线同步优先级
            _okR98 = _okR98 and not hasattr(_r98, "handle")            # R128 把手退役：整行按住即拖
            _okR98 = _okR98 and _r98.label.toolTip() == ""              # 内容悬停不再重复弹窗
            _okR98 = _okR98 and _wD.add_prio_bar.prio == _wD._prio \
                and not hasattr(_wD, "btn_flag")                        # 新建任务优先级与已有行统一竖线
            from PySide6.QtGui import QEnterEvent as _QEEv98
            from PySide6.QtCore import QPointF as _QPtF98, QEvent as _QEv98
            _r98.enterEvent(_QEEv98(_QPtF98(1, 1), _QPtF98(1, 1), _QPtF98(1, 1)))
            _okR98 = _okR98 and _r98.btn_del.isVisible()               # hover ×现身（把手已除）
            _r98.leaveEvent(_QEv98(_QEv98.Leave))
            _okR98 = _okR98 and not _r98.btn_del.isVisible()
            check("R98 优先级下拉+行级改级（菜单四档/竖线同步/把手退役/hover×）", _okR98)
            from PySide6.QtCore import QEvent as _QEvG, QPoint as _QPtG2, QPointF as _QPtFG
            from PySide6.QtGui import QMouseEvent as _QMEvG
            _rd = next(r for r in _wD.rows() if r.task["id"] == _d["id"])
            _gp = _rd.mapToGlobal(_QPtG2(30, 18))
            _rd.mousePressEvent(_QMEvG(_QEvG.MouseButtonPress, _QPtFG(30, 18), _QPtFG(_gp),
                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            _rd.mouseMoveEvent(_QMEvG(_QEvG.MouseMove, _QPtFG(30, 30), _QPtFG(_gp + _QPtG2(0, 12)),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            _okG2 = _rd._dragging                                     # ② 阈值触发拖拽
            _rb0 = next(r for r in _wD.rows() if r.task["id"] == _b["id"])
            _gy2 = _rb0.mapToGlobal(_QPtG2(10, 10)).y() + 30          # 拖到乙中心之下=垫底
            _rd.mouseMoveEvent(_QMEvG(_QEvG.MouseMove, _QPtFG(30, 100), _QPtFG(10, _gy2),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            _rd.mouseReleaseEvent(_QMEvG(_QEvG.MouseButtonRelease, _QPtFG(30, 100), _QPtFG(10, _gy2),
                                         Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
            _okG2 = _okG2 and [r.task["text"] for r in _wD.rows() if not r.done] == ["丙", "乙", "丁"] \
                and _mgD.tasks[_d["id"]]["order"] == 2.0               # 落点定序重排
            check("R89b 整行拖拽排序（阈值触发/换位/落点重排）", _okG2)
            _rb = next(r for r in _wD.rows() if r.task["id"] == _b["id"])
            _rb.box.setChecked(True)
            _okG3 = _mgD.tasks[_b["id"]]["done"] is False               # ③ 过渡中数据未变
            _okG3 = _okG3 and _rb._completing and _rb.label.font().strikeOut()   # 即时成功反馈
            _rb._commit_done()
            _okG3 = _okG3 and _mgD.tasks[_b["id"]]["done"] is True      # 动画播完落数据
            check("R89c 勾选成功过渡（延迟/划线反馈/落数据）", _okG3)
            _rc2 = next(r for r in _wD.rows() if r.task["id"] == _c["id"])
            _rc2.box.setChecked(True)
            _okG4 = _rc2._completing
            _rc2.box.setChecked(False)                                 # ④ 过渡中再点=中止回滚
            _okG4 = _okG4 and not _rc2._completing and _mgD.tasks[_c["id"]]["done"] is False \
                and _rc2.minimumHeight() == 44 and not _rc2.label.font().strikeOut()   # R127 行高改下限制（多行自适应）
            check("R89d 过渡中止回滚（数据不变/高度复原/去划线）", _okG4)
            _rc3 = next(r for r in _wD.rows() if r.task["id"] == _c["id"])
            _rc3.box.setChecked(True)                                   # 丙过渡中
            _mgD.add("戊")                                               # ⑤ 触发重建→收割，勾选不被吞
            check("R89e 重建收割（过渡中重建不吞勾选）", _mgD.tasks[_c["id"]]["done"] is True)
            _mgD.save()
            _mgE = _ST.TaskManager(); _mgE._load()                        # ⑥ order 持久化
            _todoE, _dunE = _mgE.lists()
            check("R89f 拖拽 order 持久化（重载顺序不变）", [t["text"] for t in _todoE] == ["戊", "丁"])
            _ST._DONE_DELAY_MS = 1; _ST._DONE_ANIM_MS = 1                 # ⑦ 真动画链（定时器→收缩→finished→落数据）
            _rd2 = next(r for r in _wD.rows() if r.task["id"] == _d["id"])
            _rd2.box.setChecked(True)
            from PySide6.QtTest import QTest as _QTst
            _okG7 = False
            for _ in range(100):                              # 条件轮询 ≤2s：不赌固定时长，容忍机器卡顿
                _QTst.qWait(20)
                if _mgD.tasks[_d["id"]]["done"] is True:      # 链路真走通（timer→shrink→finished→commit）
                    _okG7 = True
                    break
            _ST._DONE_DELAY_MS = 320; _ST._DONE_ANIM_MS = 260
            _mgD.hide_widget()
            check("R89g 真动画链（定时器→收缩→finished→落数据）", _okG7)
        finally:
            C.TASKS_PATH = _orig_tk
            C.LEGACY_STICKIES_PATH = _orig_lg
            import shutil as _shS
            _shS.rmtree(_tmp_st, ignore_errors=True)
    except Exception as e:
        import traceback as _tbS
        _tbS.print_exc()                                   # SKIP 必须见真凶：空消息异常（StopIteration 等）不再无声
        print(f"SKIP 任务清单：{e!r}")
    # 23 R103 屏幕取色器：取样/放大镜渲染/左键取色/三路取消/静默关闭/控制器入板/热键面板块
    try:
        from PySide6.QtGui import QMouseEvent as _QMEC, QKeyEvent as _QKEC, QImage as _QIC, \
            QPixmap as _QPmC, QPainter as _QPnC
        from PySide6.QtCore import QPoint as _QPtC, QPointF as _QPtFC, QEvent as _QEvC
        from capture import ColorPickerOverlay as _CPOC

        def _mk_cpo():
            _pmC = _QPmC(240, 160)
            _pmC.fill(QColor("#203040"))
            _ptc = _QPnC(_pmC)
            _ptc.fillRect(0, 0, 120, 160, QColor("#ff0000"))
            _ptc.fillRect(120, 0, 120, 160, QColor("#0000ff"))
            _ptc.end()

            class _FakeScrC:
                def geometry(self):
                    return QRect(0, 0, 240, 160)

            _gotC = []
            _oC = _CPOC(_pmC, screen=_FakeScrC())
            _oC.picked.connect(lambda s, _f=_gotC: _f.append(s))
            return _oC, _gotC

        _oC1, _gC1 = _mk_cpo()
        _ok103 = _oC1._sample_at(_QPtC(30, 80)) == "#ff0000" \
            and _oC1._sample_at(_QPtC(200, 80)) == "#0000ff"       # 左右半屏取样
        _oC1.mouseMoveEvent(_QMEC(_QEvC.Type.MouseMove, _QPtFC(200, 80), _QPtFC(200, 80),
                                  Qt.NoButton, Qt.NoButton, Qt.NoModifier))
        _ok103 = _ok103 and _oC1._hex == "#0000ff"                 # 移动即更新读数
        # 放大镜渲染像素锁：卡片中心格＝取样色（同 _mag_rect 定位，不硬编码坐标；贴边翻转+钳位路径）
        _imC = _QIC(_oC1.size(), _QIC.Format_ARGB32)
        _oC1.render(_imC)
        _rcC = _oC1._mag_rect(_QPtC(200, 80))
        _cellC = _oC1.MAG // _oC1.CELLS
        _pxC = _imC.pixelColor(_rcC.x() + (_oC1.CELLS // 2) * _cellC + _cellC // 2,
                               _rcC.y() + (_oC1.CELLS // 2) * _cellC + _cellC // 2)
        _ok103 = _ok103 and abs(_pxC.blue() - 255) < 8 and _pxC.red() < 8
        _oC1.mousePressEvent(_QMEC(_QEvC.Type.MouseButtonPress, _QPtFC(200, 80), _QPtFC(200, 80),
                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        _ok103 = _ok103 and _gC1 == ["#0000ff"]                    # 左键＝取色完成
        check("R103 取色器取样/放大镜渲染/左键取色", _ok103)
        _oC2, _gC2 = _mk_cpo()
        _oC2.mousePressEvent(_QMEC(_QEvC.Type.MouseButtonPress, _QPtFC(30, 80), _QPtFC(30, 80),
                                   Qt.RightButton, Qt.RightButton, Qt.NoModifier))
        _oC3, _gC3 = _mk_cpo()
        _oC3.keyPressEvent(_QKEC(_QEvC.Type.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        _oC4, _gC4 = _mk_cpo()
        _oC4.close()
        check("R103 取色器右键/Esc/关窗三路取消", _gC2 == [""] and _gC3 == [""] and _gC4 == [""])
        _oC5, _gC5 = _mk_cpo()
        _oC5.close_silent()
        check("R103 取色器 close_silent 静默（多屏组收尾不二次回调）", _gC5 == [])
        # R114 放大镜不依赖 mouseMoveEvent：一条移动事件都不发，仅改光标位置，轮询必须跟上；
        # 左键落点与当前准星不同时，以点击落点重取样（真机现场：TextInputHost 压顶时 Qt 丢光全部移动事件）
        from PySide6.QtGui import QCursor as _QCuD
        from PySide6.QtCore import QEventLoop as _QELD
        _oC6, _gC6 = _mk_cpo()
        _cur0 = _QCuD.pos()
        try:
            _QCuD.setPos(200, 80)
            _lp = _QELD()
            QTimer.singleShot(_oC6.TRACK_MS * 4, _lp.quit)
            _lp.exec()
            _ok114 = _oC6._track.isActive() and _oC6._pos == _QPtC(200, 80) and _oC6._hex == "#0000ff"
            _oC6.mousePressEvent(_QMEC(_QEvC.Type.MouseButtonPress, _QPtFC(30, 80), _QPtFC(30, 80),
                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            _ok114 = _ok114 and _gC6 == ["#ff0000"] and not _oC6._track.isActive()
        finally:
            _QCuD.setPos(_cur0)
        check("R114 放大镜轮询跟光标（无移动事件）/左键以落点重取样/收尾停轮询", _ok114)
        # R115 读数区排版锁：卡片总高＝MAG+96、信息区底沿刷壳色（不裁切）；
        # 字体可用（加载系统雅黑成功）时再验 HEX/RGB/提示三行文字真实上屏
        from PySide6.QtGui import QFontDatabase as _QFDB
        _pmD = _QPmC(800, 600)
        _ptD = _QPnC(_pmD)
        _ptD.fillRect(0, 0, 800, 600, QColor("#22384f"))
        _ptD.fillRect(200, 150, 300, 200, QColor("#8a4fb8"))
        _ptD.end()

        class _FakeScrD:
            def geometry(self):
                return QRect(0, 0, 800, 600)

        _oC7 = _CPOC(_pmD, screen=_FakeScrD())
        _oC7._done = True; _oC7._track.stop()
        _oC7._set_pos(_QPtC(330, 240))
        _rcD = _oC7._mag_rect(_oC7._pos)
        _ok115 = _oC7.INFO == 96 and _rcD.height() == _oC7.MAG + 96
        _imD = _QIC(_oC7.size(), _QIC.Format_ARGB32)
        _imD.fill(QColor("#20242c"))
        _oC7.render(_imD)
        _pcD = _imD.pixelColor((_rcD.left() + _rcD.right()) // 2, _rcD.bottom() - 3)
        _ok115 = _ok115 and abs(_pcD.red() - 16) < 6 and abs(_pcD.green() - 20) < 6 \
            and abs(_pcD.blue() - 30) < 6                       # 信息区底沿＝壳色（刷满不裁切）
        if _QFDB.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc") >= 0:
            _imD = _QIC(_oC7.size(), _QIC.Format_ARGB32)
            _imD.fill(QColor("#20242c"))
            _oC7.render(_imD)
            _iyD = _rcD.y() + _oC7.MAG

            def _bright(_x0, _y0, _w, _h, _th=120):
                _n = 0
                for _yy in range(_y0, _y0 + _h):
                    for _xx in range(_x0, _x0 + _w):
                        _c = _imD.pixelColor(_xx, _yy)
                        if 0.299 * _c.red() + 0.587 * _c.green() + 0.114 * _c.blue() > _th:
                            _n += 1
                return _n

            _ok115 = _ok115 and _bright(_rcD.x() + 45, _iyD + 12, 110, 22) > 10   # HEX 行
            _ok115 = _ok115 and _bright(_rcD.x() + 12, _iyD + 42, 156, 16) > 10   # RGB 行
            _ok115 = _ok115 and _bright(_rcD.x() + 12, _iyD + 73, 156, 16, 80) > 10   # 提示行（UI_TEXT3 弱文本，亮度阈值降级）
        _oC7.close_silent()
        check("R115 读数区三行排版（HEX 14px/RGB 11px/分隔线/提示 11px）随卡片上屏不裁切", _ok115)
        # R151 放大镜满幅锁（用户真机 200% 屏报障：放大区只剩左上 1/4 显示内容）：k=2 物理帧下，
        # 放大区右下角格必须渲出取样色——toImage 继承 base DPR 时 drawImage 按设备无关尺寸再缩 1/k，
        # 内容只剩左上 1/k²、右下格露壳色底；R151 起 _img 归零 DPR 恢复满幅（既有锁全是 k=1 夹具漏网）
        _pmE = _QPmC(1600, 1200)
        _ptE = _QPnC(_pmE)
        _ptE.fillRect(0, 0, 800, 1200, QColor("#ff0000"))
        _ptE.fillRect(800, 0, 800, 1200, QColor("#0000ff"))
        _ptE.end()

        class _FakeScrE:
            def geometry(self):
                return QRect(0, 0, 800, 600)

        _oC8 = _CPOC(_pmE, screen=_FakeScrE())
        _oC8._done = True; _oC8._track.stop()
        _ok151 = _oC8.k == 2.0 and _oC8._img.devicePixelRatio() == 1.0       # 修复不变量直证
        _ok151 = _ok151 and _oC8._sample_at(_QPtC(200, 240)) == "#ff0000" \
            and _oC8._sample_at(_QPtC(600, 240)) == "#0000ff"                # k=2 物理取样不回归
        _oC8._set_pos(_QPtC(600, 240))
        _rcE = _oC8._mag_rect(_oC8._pos)
        _imE = _QIC(_oC8.size(), _QIC.Format_ARGB32)
        _oC8.render(_imE)
        _pxE = _imE.pixelColor(_rcE.x() + _oC8.MAG - 6, _rcE.y() + _oC8.MAG - 6)   # 放大区右下角格中点（避网格线/准星格）
        _ok151 = _ok151 and abs(_pxE.blue() - 255) < 8 and _pxE.red() < 8            # 满幅：右下格＝取样蓝
        _oC8.close_silent()
        check("R151 放大镜 k=2 满幅渲染（右下格＝取样色·toImage 归零 DPR 不再缩 1/k）", _ok151)
        # R152（用户两点，推翻 R144/R147「confirm/close 不贴」判例）：①叉/对也贴键位标签——
        # Esc 直贴（3 字符够窄）、Enter 换 ↩（mac return 符号，单字符最精致）；同入 _key_tips 统一遍历，
        # 关「显示键位标签」三签随之自动不显示（「不显示的时候，自动就不显示了」）；②键盘钮补贴 K——
        # DEFAULT_SHOT_ACT_KEYS 尾加 keys=K + SHOT_ACT_METHODS 映射 _open_keys_menu，标注态单键 K
        # 直弹设置菜单（「整体统一」）；K 与工具键冲突时工具优先（同 R144 语义）。
        # 锁落 R103 段：4740-5479 巨 try 内 getsource 在 exe 冻结态必炸、炸点后锁全灭（「SKIP 任务清单」
        # 连坐区），新锁双环境恒真必须住 exe 安全段（R148 坑二纪律；R147 老锁留原位维持既有覆盖）。
        from PySide6.QtGui import QKeyEvent as _KE152
        from PySide6.QtCore import QEvent as _QE152
        from capture import ShotOverlay as _SO152
        import capture as _cap152

        class _FScr152:
            def geometry(self):
                return QRect(0, 0, 800, 600)

        _pm152 = _QPmC(800, 600); _pm152.fill(QColor("#203040"))
        _ov152 = _SO152(_pm152, pick=False, screen=_FScr152())
        _ov152.sel = QRect(100, 100, 400, 300); _ov152.phase = "edit"
        _ov152.show_toolbar()
        _kt152 = _ov152._key_tips
        _ok152 = _cap152.DEFAULT_SHOT_ACT_KEYS.get("keys") == "K" \
            and _cap152.SHOT_ACT_METHODS.get("keys") == "_open_keys_menu" \
            and _kt152["act:close"].text() == "Esc" and _kt152["act:confirm"].text() == "↩" \
            and _kt152["act:keys"].text() == "K" \
            and not _kt152["act:close"].isHidden() and not _kt152["act:confirm"].isHidden() \
            and not _kt152["act:keys"].isHidden() and "（K）" in _ov152.btn_keys.toolTip()
        _calls152 = []
        _orig_sv152 = C.save_settings
        C.save_settings = lambda s: _calls152.append(dict(s))      # 锁内不写真实 settings
        _ov152._set_key_tips(False)
        _ok152 = _ok152 and _kt152["act:close"].isHidden() and _kt152["act:confirm"].isHidden() \
            and _kt152["act:keys"].isHidden()                        # 关总开关三签自动不显示
        _ov152._set_key_tips(True)
        C.save_settings = _orig_sv152
        _km152 = []
        _ov152._open_keys_menu = lambda: _km152.append(1)            # 实例遮蔽免弹模态菜单（R144 同法）
        _ov152.keyPressEvent(_KE152(_QE152.KeyPress, Qt.Key_K, Qt.NoModifier))
        _ok152 = _ok152 and _km152 == [1]                            # 单键 K 直触发设置入口
        _ov152.close(); _ov152.deleteLater()
        check("R152 叉对贴 Esc/↩+键盘钮贴 K 随总开关显隐/单键 K 弹设置菜单", bool(_ok152))
        # R153（用户三点）：①优先级竖条矮胖化——宽 3.2→4.5/高 19→11 槽内垂直居中（宽高比 1:5.9→1:2.4，
        # 仍读作竖条），无级浅痕同步 2×17→3×7；②双击编辑黑字 bug——begin_edit QSS 是 R133 浅色遗留
        # color:#1d1d2b（R137 黑透换肤漏网），黑透壳上看不清；③所见即所得——编辑框隐形化（透明底/
        # 无边框/白字与行标签同貌）+同字体（row.label.font() 14px，原 inp 13px 会跳变）+光标落文字尾
        # +minimumHeight 保持行高不跳。锁落 exe 安全段（同 R152 纪律）；竖条矮胖用像素 bbox 审计（R66 同法）。
        import sticky as _ST153
        import tempfile as _tf153
        import shutil as _sh153
        _bar153 = _ST153._PrioBar(None, 2)                       # paintEvent 不触 row（仅点击回调用）
        _bar153.setAttribute(Qt.WA_TranslucentBackground, True)  # 顶层夹具默认背非透明（行内父壳才透）
        _imB = _QIC(_bar153.size(), _QIC.Format_ARGB32)
        _imB.fill(Qt.transparent)
        _bar153.render(_imB)                                     # 未 show 也可直渲（R144 PulseDot 同法）
        _pts153 = [(x, y) for x in range(_imB.width()) for y in range(_imB.height())
                   if _imB.pixelColor(x, y).alpha() > 40]
        _bw153 = max(p[0] for p in _pts153) - min(p[0] for p in _pts153) + 1
        _bh153 = max(p[1] for p in _pts153) - min(p[1] for p in _pts153) + 1
        _cy153 = (min(p[1] for p in _pts153) + max(p[1] for p in _pts153)) / 2
        _bar153.deleteLater()
        _ok153 = 3 <= _bw153 <= 7 and 9 <= _bh153 <= 13 and _bw153 >= _bh153 * 0.34 \
            and abs(_cy153 - 11) <= 1.6                          # 矮胖（宽≥高 1/3）+槽内垂直居中
        _td153 = _tf153.mkdtemp()
        _orig_tp153 = C.TASKS_PATH
        C.TASKS_PATH = os.path.join(_td153, "tasks.json")        # 隔离库：不碰真实任务数据
        try:
            _mg153 = _ST153.TaskManager(); _mg153.show_widget(); _w153 = _mg153.widget
            _mg153.add("所见即所得")
            _r153 = next(r for r in _w153.rows() if not r.done)
            _lh153 = _r153.label.height()
            _w153.begin_edit(_r153)
            _ed153 = _w153._editing[1]
            _ok153 = _ok153 and "background:transparent" in _ed153.styleSheet() \
                and "border:none" in _ed153.styleSheet() \
                and "#eef1f7" in _ed153.styleSheet() and "#1d1d2b" not in _ed153.styleSheet() \
                and _ed153.font().pixelSize() == _r153.label.font().pixelSize() == 14 \
                and _ed153.textCursor().position() == len("所见即所得") \
                and _ed153.minimumHeight() >= max(22, _lh153)      # 隐形框+同字体+光标落尾+行高不跳
            _ed153.editingFinished.emit()                          # 提交收尾（原值未变→重建）
            _ok153 = _ok153 and _w153._editing is None
            _mg153.hide_widget()
        finally:
            C.TASKS_PATH = _orig_tp153
            _sh153.rmtree(_td153, ignore_errors=True)
        check("R153 优先级竖条矮胖(4.5×11居中)/编辑隐形化(黑字根治·同字体·光标落尾)", bool(_ok153))
        # R154（用户三点）：①完成对号后右下角缩略图卡——5s 自动消失（hover 暂停、右键即关）；
        # ②点击卡片进编辑（LongEditor 同套标注工具，_clip_edit_img 路径抽取 _open_image_editor 共用）；
        # ③与原「已保存 · path」纯文字 toast 整合——卡内副文直接承担保存/复制状态，不再双提示
        # （失败路径仍纯 toast）。截图复制+保存、长截图 ✓/⬇ 双路径全部接线；缩略图 DPR 感知+圆角裁剪。
        _pm154 = QPixmap(320, 200)
        _pm154.fill(QColor("#30507a"))
        _card154 = ctl._shot_notify
        _got154 = {}
        _card154.show_shot(_pm154, "截图完成", "已复制到剪贴板",
                           lambda: _got154.update(clicked=True))
        _ok154 = isinstance(_card154, ShotNotifyCard) and _card154.isVisible() \
            and not _card154.thumb.pixmap().isNull() \
            and _card154.title.text() == "截图完成" \
            and _card154.sub.text() == "已复制到剪贴板" \
            and _card154._timer.isActive() \
            and _card154._timer.interval() == ShotNotifyCard.LIFE_MS
        from PySide6.QtCore import QEvent as _QEv154, QPointF as _QPtF154
        from PySide6.QtGui import QEnterEvent as _QEE154, QMouseEvent as _QME154
        _card154.enterEvent(_QEE154(_QPtF154(1, 1), _QPtF154(1, 1), _QPtF154(1, 1)))   # hover 暂停倒计时
        _ok154 = _ok154 and not _card154._timer.isActive()
        _card154.leaveEvent(_QEv154(_QEv154.Leave))                                    # 移出续走
        _ok154 = _ok154 and _card154._timer.isActive()
        _card154.mouseReleaseEvent(_QME154(_QEv154.MouseButtonRelease, _QPtF154(10, 10),
                                           _QPtF154(10, 10), Qt.LeftButton, Qt.LeftButton,
                                           Qt.NoModifier))
        _ok154 = _ok154 and _got154.get("clicked") is True and not _card154.isVisible()
        _card154.show_shot(_pm154, "长截图完成 · 3 屏", "已保存 · 长截图_x.png",
                           lambda: _got154.update(bad=True))
        _card154.mouseReleaseEvent(_QME154(_QEv154.MouseButtonRelease, _QPtF154(10, 10),
                                           _QPtF154(10, 10), Qt.RightButton, Qt.RightButton,
                                           Qt.NoModifier))
        _ok154 = _ok154 and not _card154.isVisible() and "bad" not in _got154    # 右键仅关闭不进编辑
        _ntf154, _tst154 = [], []
        _orig_ntf154, _orig_tst154 = ctl.notify_shot, ctl.toast
        ctl.notify_shot = lambda *a: _ntf154.append(a)
        ctl.toast = lambda *a, **k: _tst154.append(a)
        import tempfile as _tf154
        try:
            ctl.shot_finished({"pixmap": _pm154, "path": None})                 # 截图 ✓ 复制路径
            _ok154 = _ok154 and len(_ntf154) == 1 and _ntf154[0][1] == "截图完成" \
                and "已复制" in _ntf154[0][2] and not _tst154
            _sv154 = os.path.join(_tf154.gettempdir(), "r154_shot.png")
            _pm154.save(_sv154, "PNG")
            ctl.shot_finished({"pixmap": _pm154, "path": _sv154})                # 保存路径（默认自动复制）
            _ok154 = _ok154 and len(_ntf154) == 2 and "已保存" in _ntf154[1][2] \
                and os.path.basename(_sv154) in _ntf154[1][2] and not _tst154
            os.remove(_sv154)
            _ls154 = os.path.join(_tf154.gettempdir(), "r154_long.png")
            QImage(9, 6, QImage.Format_RGB888).save(_ls154, "PNG")
            _ntf154.clear()
            ctl.long_finished(_ls154, 2)                                          # 长截图 ✓（复制）
            _ok154 = _ok154 and len(_ntf154) == 1 and "长截图完成 · 2 屏" in _ntf154[0][1] \
                and "已复制" in _ntf154[0][2] and not _tst154
            _ntf154.clear()
            ctl.long_finished(_ls154, 2, copy=False)                              # 长截图 ⬇（纯落盘）
            _ok154 = _ok154 and len(_ntf154) == 1 and "已保存" in _ntf154[0][2] and not _tst154
            os.remove(_ls154)
            _ed154 = {}
            _orig_le154 = globals()["LongEditor"]
            try:
                globals()["LongEditor"] = lambda *a, **k: _ed154.update(args=a, kw=k) or QWidget()
                ctl._open_image_editor(QImage(12, 8, QImage.Format_RGB888))
            finally:
                globals()["LongEditor"] = _orig_le154
            _ok154 = _ok154 and _ed154["args"][0].shape == (8, 12, 3) \
                and bool(_ed154["kw"].get("save_dir")) and callable(_ed154["kw"].get("on_done"))
            import shutil as _sh154
            _sh154.rmtree(_ed154["kw"]["save_dir"], ignore_errors=True)
        finally:
            ctl.notify_shot = _orig_ntf154
            ctl.toast = _orig_tst154
        _card154.hide()
        check("R154 完成缩略图卡（点击进编辑/保存提示整合/hover暂停/右键关/截图长图双路接线）", bool(_ok154))
        # R155（用户点名「除了任务清单以外，其他小应用的独立窗口不要做成透明的，看起来会比较乱」）：
        # 插件窗壳实色化——PLUGIN_QSS pluginBody（待办/番茄钟/快记/剪贴板/文件搜索五窗）与 UI_BG_CARD
        # 令牌（日历 calBody/CardDialog cardBody 消费）rgba(16,20,30,202)→#10141e 实底深色（同色相去
        # alpha，窗型/圆角/柔影/控件体系不动；WA_TranslucentBackground 保留——圆角与自绘阴影所需）。
        # 例外保留半透：任务清单 sticky taskBody（用户点名除外）、网速浮条 nsBody（R93 用户明确要求
        # 保留项，浮条非「小应用独立窗口」）；ToastWindow/ShotNotifyCard/工具条等悬浮件不在范围。
        # 像素级断言：body 区 alpha==255（修复前 202）、root 边距区 alpha==0（窗级透明仍为圆角阴影保留）。
        _ok155 = C.UI_BG_CARD == "#10141e" \
            and "#10141e" in W.PLUGIN_QSS and "rgba(16,20,30,202)" not in W.PLUGIN_QSS \
            and "rgba(16,20,30,202)" in sticky._CSS                       # 任务清单半透例外不动
        import tempfile as _tf155
        import shutil as _sh155
        _td155 = _tf155.mkdtemp()                                # 独立目录：_tmp_st 在 5585 已 rmtree
        _orig_todo155 = C.TODO_PATH
        C.TODO_PATH = os.path.join(_td155, "todos_r155.json")
        try:
            _tw155 = W.TodoWindow()
            _im155 = QImage(_tw155.size(), _QIC.Format_ARGB32)
            _im155.fill(Qt.transparent)
            QWidget.render(_tw155, _im155)            # 基类显式调：TodoWindow.render() 业务方法遮蔽了 QWidget.render
            _px_body155 = _im155.pixelColor(_tw155.width() // 2, 20)       # body 标题栏区
            _px_edge155 = _im155.pixelColor(2, 2)                          # root 12 阴影留白区
            _ok155 = _ok155 and "#10141e" in _tw155.styleSheet() \
                and _px_body155.alpha() == 255 and abs(_px_body155.red() - 16) <= 2 \
                and _px_edge155.alpha() == 0
            _tw155.hide(); _tw155.deleteLater()
            _cw155 = W.CalendarWindow()
            _ok155 = _ok155 and "#10141e" in _cw155.styleSheet() \
                and "rgba(16,20,30,202)" not in _cw155.styleSheet()
            _cw155.hide(); _cw155.deleteLater()
            _ns155 = W.NetSpeedWidget()
            _ok155 = _ok155 and "rgba(16,20,30,202)" in _ns155.styleSheet()  # 网速浮条半透保留项
            _ns155.hide(); _ns155.deleteLater()
        finally:
            C.TODO_PATH = _orig_todo155
            _sh155.rmtree(_td155, ignore_errors=True)
        check("R155 插件窗实底化（五窗+日历+模态卡 #10141e·像素 alpha 255）/任务清单与网速浮条半透例外", bool(_ok155))
        # R156 剪贴板卡片式（用户点名「把图片改成方块的，鼠标悬停在方块某个位置悬浮出现那些按钮，更规整」）：
        # 行式列表（图 104 高/文 62 高）退役 → 148×148 方卡 QGridLayout 流式排列——_cols_for_width 纯函数
        # 按 viewport 宽算列数（n×148+(n-1)×10 ≤ w），resizeEvent 重排（五窗壳带 EdgeResizer 八向缩放）；
        # 悬停卡片顶部居中浮出胶囊钮（钉/编辑/删除 lucide 13px——复制钮退役：点击卡片即复制与 hint 语义一致；
        # btn_name 显名机制同步退役回归系统 tooltip；📌 emoji 违 MASTER 禁 emoji 当图标一并根治）；
        # 文本分支死代码删除（R68 起文本不入库）。锁落 exe 安全段（同 R152 纪律）。
        # R158 跟进（用户「胶囊外边框糊，去掉换样式」→ 1px 半透边高分屏发虚退役改自绘柔影悬浮感，
        # R134/R141/R145 无描边判例；「悬浮不应该三个按钮，应该还有一个复制」→ 复制钮回归四钮，
        # R156 收敛过头用户改口）：
        import widgets as _W156
        from PySide6.QtGui import QEnterEvent as _QEE156
        from PySide6.QtCore import QPointF as _QPF156, QEvent as _QEv156
        _h156 = []
        for _i in range(5):
            _im156 = QImage(64, 48, QImage.Format_ARGB32)
            _im156.fill(QColor(30 * _i, 90, 140))
            _h156.append({"t": "img", "v": _im156, "at": 0})
        _cw156 = _W156.ClipHistoryWindow(_h156)
        _cw156.show()
        app.processEvents()
        _ok156 = len(_cw156.rows) == 5 and isinstance(_cw156.grid, QGridLayout)
        _r156 = _cw156.rows[0]
        _ok156 = _ok156 and type(_r156).__name__ == "_ClipCard" \
            and _r156.width() == _r156.height() == _W156.ClipHistoryWindow.CARD == 148 \
            and _r156.thumb.objectName() == "clipThumb" \
            and "#clipThumb{background:rgba(0,0,0,120)" in _W156.CLIP_QSS
        _ok156 = _ok156 and _cw156._cols_for_width(510) == 3 \
            and _cw156._cols_for_width(700) == 4 and _cw156._cols_for_width(100) == 1
        # hover 初态断言前先 leaveEvent 归零：窗口 show 时鼠标正悬卡片上会收合成 Enter
        # （探针纯净环境鼠标无位置恒 hidden，冒烟/真机鼠标在哪张卡哪张浮钮就该显示——正确行为）
        _r156.leaveEvent(_QEv156(_QEv156.Type.Leave))
        _ok156 = _ok156 and not _r156.btns.isVisible()                 # 常态隐藏
        _r156.enterEvent(_QEE156(_QPF156(1, 1), _QPF156(1, 1), _QPF156(1, 1)))
        _ok156 = _ok156 and _r156.btns.isVisible()                     # 悬停浮出
        _r156.leaveEvent(_QEv156(_QEv156.Type.Leave))
        _ok156 = _ok156 and not _r156.btns.isVisible()                 # 离开收回
        _ok156 = _ok156 and _r156.btns.parent() is _r156 and 0 < _r156.btns.y() <= 10 \
            and abs(_r156.btns.x() - (148 - _r156.btns.width()) // 2) <= 1   # 顶部居中悬浮
        _ok156 = _ok156 and not _r156.act_pin.icon().isNull() \
            and not _r156.act_edit.icon().isNull() and not _r156.act_del.icon().isNull() \
            and hasattr(_r156, "act_copy") and not _r156.act_copy.icon().isNull() \
            and not hasattr(_r156, "btn_name")                        # R158 复制钮回归；btn_name 仍退役
        _ok156 = _ok156 and "border:1px" not in _W156.CLIP_QSS \
            and _r156.btns.graphicsEffect() is not None               # R158 无描边柔影
        _cw156.close()
        _cw156.deleteLater()
        check("R156 剪贴板卡片式网格（148方卡/流式列数/悬停胶囊浮钮lucide·R158复制钮回归无描边柔影）", bool(_ok156))
        # R165 微信截图箭头：固定箭头头部尺寸，不再按整条箭长拉伸；
        # 中间柄弯曲仍沿弧长保持宽度比例。默认锥形由 R160 锁定，a/m/b 手柄由 R100 锁定。
        import capture as _cap162
        from PySide6.QtGui import QPainter as _QP162, QPen as _QPen162
        _pm162 = QPixmap(800, 600); _pm162.fill(QColor("#203040"))
        _ov162 = _cap162.ShotOverlay(_pm162, pick=False)
        _q162 = QPixmap(400, 200); _q162.fill(QColor("#ffffff"))
        _pt162 = _QP162(_q162); _pt162.setRenderHint(_QP162.Antialiasing)
        _pt162.setPen(_QPen162(QColor("#ef4444"), 3))
        _ov162._arrow_solid(_pt162, QPoint(40, 160), QPoint(360, 40), 3)
        _pt162.end(); _im162 = _q162.toImage()

        def _red162(x, y):
            c = _im162.pixelColor(x, y)
            return c.red() > 200 and c.green() < 100
        _ok162 = (not _red162(41, 162) and _red162(168, 112)
                  and not _red162(170, 118) and _red162(309, 59)
                  and _red162(330, 51)
                  and not _red162(347, 57) and _red162(349, 44))
        _q162b = QPixmap(400, 220); _q162b.fill(QColor("#ffffff"))
        _pt162b = _QP162(_q162b); _pt162b.setRenderHint(_QP162.Antialiasing)
        _pt162b.setPen(_QPen162(QColor("#ef4444"), 3))
        _ov162._arrow_solid(_pt162b, QPoint(40, 160), QPoint(360, 160), 3, QPoint(200, 60))
        _pt162b.end(); _im162b = _q162b.toImage()
        _curve162 = _im162b.pixelColor(200, 110)
        _chord162 = _im162b.pixelColor(200, 160)
        _ok162 = (_ok162 and _curve162.red() > 200 and _curve162.green() < 100
                  and not (_chord162.red() > 200 and _chord162.green() < 100))
        _q165 = QImage(1000, 180, QImage.Format_ARGB32); _q165.fill(QColor("#ffffff"))
        _pt165 = _QP162(_q165); _pt165.setRenderHint(_QP162.Antialiasing)
        _pt165.setPen(_QPen162(QColor("#ef4444"), 3))
        _ov162._arrow_solid(_pt165, QPoint(70, 50), QPoint(260, 50), 3)
        _ov162._arrow_solid(_pt165, QPoint(70, 140), QPoint(840, 140), 3)
        _pt165.end(); _im165 = _q165

        def _cross165(x, y):
            return sum(1 for _yy in range(y - 25, y + 26)
                       if (lambda _c: _c.red() > 200 and _c.green() < 100)(_im165.pixelColor(x, _yy)))
        _ok162 = _ok162 and abs(_cross165(225, 50) - _cross165(805, 140)) <= 2 \
            and _cross165(225, 50) >= 20 and _cross165(805, 140) >= 20
        _ov162.close(); _ov162.deleteLater()
        check("R165 微信箭头头部定尺/尖尾/锐角/弯箭渲染", bool(_ok162))
        # R159 暗色系外边框排查根治+头部三钮 hover 图标变亮（用户两点）：①「那些应用的外边框都去掉，
        # 保存位置那条的外边框也去掉」——actBlock/tile/storageCard 深色 1px 白 20 线退役（DARK line
        # 浅白高分屏发虚；R134 浅色已退本项深色同退，分区靠 card 14→hover 24 底色梯度，PLUGIN_QSS
        # 无描边判例同族）；举一反三排查：窗口壳 root 1px 边保留（无边框窗边缘界定，黑透范式），
        # 输入框/菜单边保留（HIG 深色控件功能边界+焦点蓝边语义），BASE_QSS 仅剩 longedit 待统一旧体系。
        # ②「关闭等那 3 个按钮悬浮还是暗的，图标应该变亮」——_HoverIconBtn 预生成 tx2/tx 双图标
        # enter/leave 切换（QSS color 管不到 QIcon 已着色像素），retint(pal) 随 retheme 重着色。
        import panel as _PN159
        from PySide6.QtGui import QEnterEvent as _QEE159
        from PySide6.QtCore import QPointF as _QPF159, QEvent as _QEv159
        _p159 = _PN159.Panel({"theme": "dark"})
        _q159 = _p159.styleSheet()
        _ok159 = _p159.pal is C.DARK \
            and "border:1px solid" not in _q159.split("#actBlock")[1].split("}")[0] \
            and "border:1px solid" not in _q159.split("#tile")[1].split("}")[0] \
            and "border:1px solid" not in _q159.split("#storageCard")[1].split("}")[0]
        _hb159 = _p159.findChildren(_PN159._HoverIconBtn)
        _ok159 = _ok159 and len(_hb159) == 3                       # 键盘/主题/关闭三钮全换型
        _b159 = _hb159[0]
        _ok159 = _ok159 and _b159._cur is _b159._ic_n              # 常态次文本色图标
        _b159.enterEvent(_QEE159(_QPF159(1, 1), _QPF159(1, 1), _QPF159(1, 1)))
        _ok159 = _ok159 and _b159._cur is _b159._ic_h              # hover 切亮图标
        _b159.leaveEvent(_QEv159(_QEv159.Type.Leave))
        _ok159 = _ok159 and _b159._cur is _b159._ic_n              # 离开回常态
        _icn159 = _b159._ic_n
        _b159.retint(C.LIGHT)                                      # 主题切换重着色（retheme 路径）
        _ok159 = _ok159 and _b159._cur is _b159._ic_n and _b159._ic_n is not _icn159
        _p159.close()
        _p159.deleteLater()
        check("R159 暗色系去浅白线（actBlock/tile/storageCard无描边）+头部三钮hover图标变亮", bool(_ok159))
        ctl.colorpick_finished("#a1b2c3")
        check("R103 控制器取色入板", QGuiApplication.clipboard().text() == "#a1b2c3")
        check("R103 取色热键（保留取色动作及默认 Ctrl+Alt+C）",
              (_HOTKEY_ACTIONS[-1][0] == "longshot" if LITE      # R107 轻量版热键裁取色
               else any(key == "colorpick" for key, _label in _HOTKEY_ACTIONS)
               and hk_parse(DEFAULT_HOTKEYS["colorpick"]) is not None))
        _has_cp = any(m == "colorpick" for *_x, m in _acts)
        check("R103 面板块含屏幕取色且方法可路由", not _has_cp if LITE else _has_cp)
    except Exception as e:
        import traceback as _tbC
        _tbC.print_exc()
        check(f"R103 取色器异常：{e!r}", False)
    try:
        for result in colorpick_regression(ctl):
            check("R111 " + result, True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(f"R111 取色生命周期：{e!r}", False)
    try:
        for result in window_snap_regression():
            check("R112 " + result, True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(f"R112 窗口吸附：{e!r}", False)
    try:
        for result in launch_storage_regression():
            check("R113 " + result, True)
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(f"R113 启动与存放位置：{e!r}", False)
    # 24 R104 序号附言：命中矩形/附言拖动/双击再编辑/撤销与 Esc 回滚/让位防重影（截图＋长图同语义）
    try:
        from PySide6.QtGui import QMouseEvent as _QMD, QPixmap as _QPmD
        from PySide6.QtCore import QPoint as _QPtD, QEvent as _QEvD

        class _FakeScrD:
            def geometry(self):
                return QRect(0, 0, 800, 600)

        def _evD(t, x, y, b1, b2):
            return _QMD(t, _QPtD(x, y), _QPtD(x, y), b1, b2, Qt.NoModifier)

        _pmD = _QPmD(800, 600)
        _pmD.fill(QColor("#203040"))
        _ovD = ShotOverlay(_pmD, pick=False, screen=_FakeScrD())
        _ovD.phase = "edit"
        _ovD.sel = QRect(20, 20, 760, 560)
        _ovD.cur_tool = "num"
        _ovD.show_num_input(_QPtD(200, 200))
        _ovD.text_edit.setPlainText("第一步：点这里")
        _ovD.commit_text_input()
        _nD = _ovD._nums[0]
        _rcD = _ovD._num_note_rect(_nD)
        _ok104 = _rcD is not None and _rcD.width() > 40 and _rcD.height() > 10 \
            and _rcD.topLeft() == _nD["pt"] + _QPtD(_nD["tdx"], _nD["tdy"])  # 命中矩形与渲染同源定位
        _ok104 = _ok104 and _ovD._hit_num(_rcD.center()) == 0                 # 附言命中＝序号 0
        _ok104 = _ok104 and _ovD._hit_num(_QPtD(200, 200)) == 0               # 圆标命中（回归）
        _ok104 = _ok104 and _ovD._hit_num(_QPtD(120, 120)) is None            # 空白不中
        _inD = _rcD.center()
        _ovD.mousePressEvent(_evD(_QEvD.Type.MouseButtonPress, _inD.x(), _inD.y(),
                                  Qt.LeftButton, Qt.LeftButton))
        _ok104 = _ok104 and len(_ovD._nums) == 1 and _ovD.sel_stamp == ("num", 0) \
            and _ovD._drag_num == 0                              # 按附言＝选中拖动，不落新标
        _ovD.mouseMoveEvent(_evD(_QEvD.Type.MouseMove, _inD.x() + 40, _inD.y() + 30,
                                 Qt.NoButton, Qt.LeftButton))
        _ovD.mouseReleaseEvent(_evD(_QEvD.Type.MouseButtonRelease, _inD.x() + 40, _inD.y() + 30,
                                    Qt.LeftButton, Qt.NoButton))
        _ok104 = _ok104 and _nD["pt"] == _QPtD(240, 230)                      # 圆标跟手
        _ok104 = _ok104 and (_nD["tdx"], _nD["tdy"]) == (20, -12)             # 附言偏移不变（整体跟手）
        _inD = _ovD._num_note_rect(_nD).center()
        _ovD.mouseDoubleClickEvent(_evD(_QEvD.Type.MouseButtonDblClick, _inD.x(), _inD.y(),
                                        Qt.LeftButton, Qt.LeftButton))
        _ok104 = _ok104 and _ovD.text_edit is not None \
            and _ovD.text_edit.toPlainText() == "第一步：点这里" \
            and _ovD._num_edit_idx == 0                          # 双击附言＝预填原文再编辑
        _ovD.text_edit.setPlainText("改后的附言")
        _ovD.commit_text_input()
        _ok104 = _ok104 and _ovD._nums[0]["text"] == "改后的附言" \
            and _ovD._num_edit_idx is None                       # 提交落字＋让位恢复
        _ovD.undo()
        _ok104 = _ok104 and _ovD._nums[0]["text"] == "第一步：点这里"         # 整会话一步撤销回原文
        _ovD.mouseDoubleClickEvent(_evD(_QEvD.Type.MouseButtonDblClick, 240, 230,
                                        Qt.LeftButton, Qt.LeftButton))        # 双击圆标同样进再编辑
        _ok104 = _ok104 and _ovD.text_edit is not None \
            and _ovD.text_edit.toPlainText() == "第一步：点这里"
        _txtD = []
        _ovD._draw_one_num = lambda *a: _txtD.append(a[4])        # 第 5 参＝附言文本
        _ovD._paint_nums(None)
        del _ovD._draw_one_num
        _ok104 = _ok104 and _txtD == [""]                         # 再编辑期间底层附言让位（防重影）
        _ovD.cancel_text_edit()
        _ok104 = _ok104 and _ovD._nums[0]["text"] == "第一步：点这里" \
            and _ovD._num_edit_idx is None and _ovD.text_edit is None   # Esc 回滚改前原文
        check("R104 截图序号附言（命中/拖动/双击再编辑/撤销/Esc/让位）", _ok104)
        # 长图侧同语义简版（k=1.0：事件坐标＝图坐标；_hit_num/_num_note_rect 走 capture 委托链）
        _cvD = _LE.LongCanvas(np.zeros((600, 800, 3), dtype=np.uint8), 848)
        _cvD.cur_tool = "num"
        _cvD.show_num_input(_QPtD(200, 200))
        _cvD.text_edit.setPlainText("长图附言")
        _cvD.commit_text_input()
        _nDL = _cvD._nums[0]
        _rcDL = _cvD._num_note_rect(_nDL)
        _ok104L = _rcDL is not None and _cvD._hit_num(_rcDL.center()) == 0    # 附言命中（委托链）
        _inDL = _rcDL.center()
        _cvD.mousePressEvent(_evD(_QEvD.Type.MouseButtonPress, _inDL.x(), _inDL.y(),
                                  Qt.LeftButton, Qt.LeftButton))
        _ok104L = _ok104L and len(_cvD._nums) == 1 and _cvD._drag_num == 0    # 按附言＝拖动不落新标
        _cvD.mouseMoveEvent(_evD(_QEvD.Type.MouseMove, _inDL.x() + 40, _inDL.y() + 30,
                                 Qt.NoButton, Qt.LeftButton))
        _cvD.mouseReleaseEvent(_evD(_QEvD.Type.MouseButtonRelease, _inDL.x() + 40, _inDL.y() + 30,
                                    Qt.LeftButton, Qt.NoButton))
        _ok104L = _ok104L and _nDL["pt"] == _QPtD(240, 230) \
            and (_nDL["tdx"], _nDL["tdy"]) == (17, -12)                       # 跟手且偏移不变
        _inDL = _cvD._num_note_rect(_nDL).center()
        _cvD.mouseDoubleClickEvent(_evD(_QEvD.Type.MouseButtonDblClick, _inDL.x(), _inDL.y(),
                                        Qt.LeftButton, Qt.LeftButton))
        _ok104L = _ok104L and _cvD.text_edit is not None \
            and _cvD.text_edit.toPlainText() == "长图附言" and _cvD._num_edit_idx == 0
        _cvD.cancel_text_edit()
        _ok104L = _ok104L and _cvD._nums[0]["text"] == "长图附言" \
            and _cvD._num_edit_idx is None                                    # Esc 回滚原文
        _ok104L = _ok104L and hasattr(_LE.LongEditor, "_cycle_arrow_style")      # R134：样式方法回归（双侧同步）
        check("R104 长图序号附言（命中/拖动/双击再编辑/Esc·k 换算同语义）", _ok104L)
    except Exception as e:
        import traceback as _tbD
        _tbD.print_exc()
        print(f"SKIP R104：{e!r}")
    # 25 R105：截图工具条长截图入口（GIF 旁）——emit longshot+选区/屏，主窗口路由进拼接条
    try:
        import icons as _ic105

        class _FakeScrE:
            def geometry(self):
                return QRect(0, 0, 800, 600)

        _pmE = QPixmap(800, 600)
        _pmE.fill(QColor("#203040"))
        _ovE = ShotOverlay(_pmE, pick=False, screen=_FakeScrE())
        _ovE.phase = "edit"
        _ovE.sel = QRect(30, 40, 500, 300)
        _ovE.show_toolbar()
        _layE = _ovE.toolbar.layout()
        _tipsE = []
        for _iE in range(_layE.count()):
            _wE = _layE.itemAt(_iE).widget()
            _tipsE.append(_wE.toolTip() if _wE is not None and hasattr(_wE, "toolTip") else "")
        _giE = next((_i for _i, _t in enumerate(_tipsE) if "GIF" in _t), -1)
        _liE = next((_i for _i, _t in enumerate(_tipsE) if "长截图" in _t), -1)
        _sig105 = {}
        _ovE.finished.connect(lambda r: _sig105.update(r))
        _ovE.start_longshot()
        _img105 = _ic105.pixmap("longshot", "#3c3c3c", 24).toImage()
        _ink105 = any(_img105.pixelColor(_x, _y).alpha() > 0
                      for _x in range(24) for _y in range(24))
        _ok105 = (not _ic105.pixmap("longshot").isNull() and _ink105     # 分支存在且有实际笔画
                  and _giE >= 0 and _liE == _giE + 1                      # 长截图钮紧贴 GIF 右侧（R108 GIF 回轻量版，两版同式）
                  and _sig105.get("longshot") is True and _sig105.get("pick") is True
                  and QRect(_sig105.get("sel", QRect())) == QRect(30, 40, 500, 300)
                  and _sig105.get("screen") is not None
                  and _ovE.toolbar is None)                               # 进拼接前工具条已关
        check("R105 截图工具条长截图入口（GIF 旁·emit longshot+sel/screen·关条）", _ok105)
    except Exception as e:
        print(f"SKIP R105：{e!r}")
    # 25b R119：双栏收编进长截图本体——条上切换钮即时切单/双栏（保留已拼内容），独立入口全撤
    try:
        import numpy as _np119
        import longshot as _LS119
        import panel as _PN119

        # compose_columns：两栏顶对齐，短栏底部黑色填充（用户指定语义，R117 起）
        _red119 = _np119.full((800, 300, 3), (200, 30, 30), dtype=_np119.uint8)
        _blu119 = _np119.full((400, 320, 3), (30, 30, 200), dtype=_np119.uint8)
        _cmp119 = _LS119.compose_columns(_red119, _blu119)
        _okC = (_cmp119.shape == (800, 620, 3)
                and _np119.array_equal(_cmp119[:, :300], _red119)          # 左栏顶对齐贯到底
                and _np119.array_equal(_cmp119[:400, 300:], _blu119)       # 右栏顶对齐
                and int(_cmp119[400:, 300:].max()) == 0)                   # 短栏底部纯黑

        # 单栏进条 → 切换钮转双栏 → 左右栏独立滚动（左 3 步右 2 步；步长 150 ≤ 174 推导同 R117）
        _pgL = _det_noise(900, 300, 7)         # R120 确定性噪声
        _pgR = _det_noise(600, 300, 11)

        def _fr119(ly, ry):
            return _np119.hstack([_pgL[ly:ly + 300], _pgR[ry:ry + 300]]).copy()

        _seq119 = iter([_fr119(0, 0), _fr119(150, 0), _fr119(300, 0),
                        _fr119(450, 0), _fr119(450, 150), _fr119(450, 300)])
        _grab_orig119 = _LS119.grab_array
        _LS119.grab_array = lambda scr=None, region=None: next(_seq119)
        _edited119 = {}
        _b119 = None
        try:
            _b119 = LongShotBar(on_finish=lambda *a, **k: None, on_cancel=lambda: None,
                                on_edit=lambda arr, frames: _edited119.update(arr=arr, frames=frames),
                                region=QRect(0, 0, 600, 300),
                                screen=QGuiApplication.primaryScreen())
            _b119.timer.stop()                         # R58 纪律：手工驱动前先冻结自动抓帧
            _b119.running = False
            _okD = (not _b119._dual and _b119.bcols.isCheckable()
                    and not _b119.bcols.isChecked() and _b119._divider is None)   # 默认单栏·切换钮未选
            _b119.bcols.click()                                                  # 切双栏（保留首帧）
            _okD = (_okD and _b119._dual and _b119.bcols.isChecked()
                    and _b119._divider is not None                                # 分栏拖柄随模式出现
                    and _b119._colL.long.shape == (300, 300, 3) and _b119._colR.long.shape == (300, 300, 3))
            for _ in range(5):
                _b119.running = True
                _b119.grab()
            _cL, _cR = _b119._colL, _b119._colR
            _out119 = _b119.long_arr
            _okD = (_okD
                    and _cL.frames == 4 and _cR.frames == 3                  # 左 3 步+首帧 / 右 2 步+首帧
                    and _cL.long.shape == (750, 300, 3) and _cR.long.shape == (600, 300, 3)
                    and _b119.frames == 6
                    and _out119.shape == (750, 600, 3)
                    and _np119.array_equal(_out119[:, :300], _cL.long)       # 左半=左栏全长
                    and _np119.array_equal(_out119[:600, 300:], _cR.long)    # 右栏顶对齐
                    and int(_out119[600:, 300:].max()) == 0                  # 右栏短 150 行→补黑
                    and _b119.divider_locked())                              # 已拼内容→分栏线锁定
            _okD = _okD and _b119.commit_divider(0.7) is False and abs(_b119._div - 0.5) < 1e-9
            _b119.bcols.click()                                              # 切回单栏（保留已拼内容）
            _okD = (_okD and not _b119._dual and not _b119.bcols.isChecked()
                    and _b119._divider is None                               # 拖柄随模式关闭
                    and _b119._colL is None and _b119.long_arr.shape == (750, 600, 3)
                    and _b119.frames == 6 and _b119._appended
                    and _b119._prev is not None and _b119._prev.shape == (300, 600, 3))  # prev 恢复整帧
            _b119.edit()                                                     # 编辑路径交付合成图
            _okD = (_okD and _edited119.get("arr") is not None
                    and _edited119["arr"].shape == (750, 600, 3) and _edited119.get("frames") == 6)
            # 未拼内容时允许调分栏：切双后 0.3 → 180/420 重建，状态复位
            _LS119.grab_array = lambda scr=None, region=None: _fr119(0, 0)
            _b2 = LongShotBar(on_finish=lambda *a, **k: None, on_cancel=lambda: None,
                              on_edit=lambda arr, frames: None,
                              region=QRect(0, 0, 600, 300),
                              screen=QGuiApplication.primaryScreen())
            _b2.timer.stop()
            _b2.running = False
            _b2.bcols.click()
            _okD = (_okD and _b2.commit_divider(0.3) is True
                    and _b2._colL.long.shape == (300, 180, 3) and _b2._colR.long.shape == (300, 420, 3)
                    and _b2.frames == 1 and not _b2.divider_locked())
            _b2.cancel()
        finally:
            _LS119.grab_array = _grab_orig119
            if _b119 is not None:
                _b119.close()

        # 独立入口全撤：工具条无 cols 钮、面板无双栏块、主窗口无双栏路由
        class _FakeScrG:
            def geometry(self):
                return QRect(0, 0, 800, 600)

        _pmG = QPixmap(800, 600)
        _pmG.fill(QColor("#203040"))
        _ovG = ShotOverlay(_pmG, pick=False, screen=_FakeScrG())
        _ovG.phase = "edit"
        _ovG.sel = QRect(30, 40, 500, 300)
        _ovG.show_toolbar()
        _layG = _ovG.toolbar.layout()
        _tipsG = []
        for _iG in range(_layG.count()):
            _wG = _layG.itemAt(_iG).widget()
            _tipsG.append(_wG.toolTip() if _wG is not None and hasattr(_wG, "toolTip") else "")
        _ovG._really_close()
        _okE = (all(not _t.startswith("双栏") for _t in _tipsG)             # 工具条无双栏专属钮（收编进条）
                and any(_t.startswith("长截图") and "双栏" in _t for _t in _tipsG)  # 长截图钮明示条上可切双栏
                and all(a[1] != "双栏长图" for a in _PN119.ACTIONS_FULL)    # 面板无双栏块
                and not hasattr(Controller, "start_longshot_dual")          # 主窗口无双栏路由/方法
                and not hasattr(Controller, "long_dual")
                and hasattr(Controller, "start_longshot"))                  # 单栏入口照常
        check("R119 双栏收编长截图（切换钮单/双即时切·保留内容·独立入口全撤）",
              _okC and _okD and _okE)
    except Exception as e:
        print(f"SKIP R119：{e!r}")
    # 25c R118：系统组「存放位置」块下线——设置入口保留常驻存放条「设置」钮与托盘菜单两路，
    # Controller.save_dir/open_save_dir 方法不动；动作表计数锁动态取自 ACTIONS 不受影响
    try:
        import panel as _PN118
        check("R118 系统组存放位置块下线（存放条/托盘入口兜底·save_dir 方法保留）",
              all(a[1] != "存放位置" for a in _PN118.ACTIONS_FULL)
              and all(a[-1] != "save_dir" for a in _PN118.ACTIONS_FULL)
              and all(a[1] != "存放位置" for a in _PN118.ACTIONS)        # Lite 过滤后同样无此块
              and hasattr(Controller, "save_dir") and hasattr(Controller, "open_save_dir"))
    except Exception as e:
        print(f"SKIP R118：{e!r}")
    # 26 R106：双击再编辑预填排版同步——textChanged 同步读 docSize 拿 0/旧宽度排版值是
    # 「预填后只显末行」的根因；fit 强制 setTextWidth+adjustSize 后高度一次撑足、视口归零
    try:
        import numpy as _np106
        import longedit as _LE106

        class _FakeScr106:
            def geometry(self):
                return QRect(0, 0, 800, 600)

        _txt106 = "第一行文字内容\n第二行文字内容\n第三行文字内容"
        _pm106 = QPixmap(800, 600)
        _pm106.fill(QColor("#203040"))
        _ov106 = ShotOverlay(_pm106, pick=False, screen=_FakeScr106())
        _ov106.phase = "edit"
        _ov106.sel = QRect(20, 20, 760, 560)
        _ov106.cur_tool = "text"
        _ov106.show_text_editor(QPoint(100, 100))
        _ov106.text_editor.setPlainText(_txt106)
        _ov106.commit_text_editor()
        _ov106._reedit_text(len(_ov106.stamps) - 1)
        _t106a = _ov106.text_editor
        _ds106a = _t106a.document().documentLayout().documentSize()
        _ok106a = (_t106a.height() >= _ds106a.height()
                   and _t106a.verticalScrollBar().value() == 0
                   and _t106a.toPlainText() == _txt106)
        _ov106.cancel_text_editor()
        _ov106.cur_tool = "num"
        _ov106.show_num_input(QPoint(200, 200))
        _ov106.text_edit.setPlainText(_txt106)
        _ov106.commit_text_input()
        _ov106._reedit_num(0)
        _t106b = _ov106.text_edit
        _ds106b = _t106b.document().documentLayout().documentSize()
        _ok106 = _ok106a and (_t106b.height() >= _ds106b.height()
                              and _t106b.verticalScrollBar().value() == 0)
        check("R106 再编辑预填排版同步（文字+序号·高度撑足·视口归零）", _ok106)

        _ea106 = _np106.zeros((1200, 300, 3), dtype=_np106.uint8)
        _led106 = _LE106.LongEditor(_ea106, frames=0, on_done=None)
        _cv106 = _led106.canvas
        _cv106.cur_tool = "num"
        _cv106.show_num_input(QPoint(80, 160))
        _cv106.text_edit.setPlainText(_txt106)
        _cv106.commit_text_input()
        _cv106._reedit_num(0)
        _t106c = _cv106.text_edit
        _ds106c = _t106c.document().documentLayout().documentSize()
        _ok106c = (_t106c.height() >= _ds106c.height()
                   and _t106c.verticalScrollBar().value() == 0)
        check("R106 长图附言再编辑预填排版同步（委托同一 fit）", _ok106c)
    except Exception as e:
        print(f"SKIP R106：{e!r}")
    print("SMOKE RESULT:", "PASS" if not fails else f"FAIL x{len(fails)}")
    # 收尾自清理：本套件及历史运行在 TEMP 留下的全部 _jj_* 产物（savedir/lgedit 临时目录、
    # 冒烟 db/gif/png 等），套件自身不再产生遗留；被占用文件（如重定向日志）删除失败自动忽略
    import glob as _glob
    import shutil as _shutil
    import tempfile as _tf2     # 局部别名：套件早段各自局部导入，此处不能依赖
    _tfdir = _tf2.gettempdir()
    for _p in _glob.glob(os.path.join(_tfdir, "_jj_*")):
        try:
            _shutil.rmtree(_p, ignore_errors=True) if os.path.isdir(_p) else os.remove(_p)
        except OSError:
            pass
    print(f"INFO 临时产物已自清理：TEMP 残余 {len(_glob.glob(os.path.join(_tfdir, '_jj_*')))} 项")
    return 0 if not fails else 1


def _set_app_user_model_id():
    """显式 AppUserModelID：任务栏按钮按简截自身归组并取窗口图标。
    不设的话 pythonw 宿主的任务栏按钮会落回 pythonw.exe 默认文档图标
    （任务栏里那张白纸+小蟒蛇）。必须在首个窗口创建前调用。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("JianJie.For.Windows")
    except Exception:
        pass


class LaunchInstance:
    """同一 Windows 用户的同版单实例；显示回执只在该用户的桌面内有效。"""

    @staticmethod
    def current_user_name():
        # Codex 等隔离账号可能继承登录用户的 USERNAME 环境变量，必须向 Windows 查询实际令牌用户。
        from ctypes import wintypes as wt
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.GetUserNameW.argtypes = [wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        advapi.GetUserNameW.restype = wt.BOOL
        size = wt.DWORD(256)
        user = ctypes.create_unicode_buffer(size.value)
        if not advapi.GetUserNameW(user, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return user.value

    @staticmethod
    def default_name():
        account = LaunchInstance.current_user_name()
        return f"JianJie_{C.APP_BUILD_ID}_SingleInstance_{account}" + ("_Lite" if LITE else "")

    def __init__(self, name=None):
        from ctypes import wintypes as wt
        # 同时按构建和实际 Windows 账号隔离；隔离账号中无窗口的进程不能截走桌面用户的双击。
        self.name = name or self.default_name()
        self.event = self.shown_event = self.restart_event = self.mutex = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        self.kernel.CreateEventW.restype = wt.HANDLE
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        self.kernel.CreateMutexW.restype = wt.HANDLE
        self.kernel.SetEvent.argtypes = [wt.HANDLE]
        self.kernel.ResetEvent.argtypes = [wt.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        try:
            self.event = self.kernel.CreateEventW(None, False, False, self.name + "_Activate")
            if not self.event:
                raise ctypes.WinError(ctypes.get_last_error())
            self.shown_event = self.kernel.CreateEventW(None, True, False, self.name + "_Shown")
            if not self.shown_event:
                raise ctypes.WinError(ctypes.get_last_error())
            self.restart_event = self.kernel.CreateEventW(None, False, False, self.name + "_Restart")
            if not self.restart_event:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.set_last_error(0)
            self.mutex = self.kernel.CreateMutexW(None, False, self.name)
            error = ctypes.get_last_error()
            if not self.mutex:
                raise ctypes.WinError(error)
            self.primary = error != 183
        except OSError:
            self.close()
            raise

    def notify(self):
        from ctypes import wintypes as wt
        u = ctypes.windll.user32
        u.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        u.FindWindowW.restype = wt.HWND
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        hwnd = u.FindWindowW(None, "简截 Lite" if LITE else "简截")
        if hwnd:
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            u.AllowSetForegroundWindow(pid.value)
        if not self.kernel.ResetEvent(self.shown_event):
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.kernel.SetEvent(self.event):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_shown(self, milliseconds=2000):
        return self.kernel.WaitForSingleObject(self.shown_event, milliseconds) == 0

    def acknowledge_shown(self):
        if not self.kernel.SetEvent(self.shown_event):
            raise ctypes.WinError(ctypes.get_last_error())

    def request_restart(self):
        if not self.kernel.SetEvent(self.restart_event):
            raise ctypes.WinError(ctypes.get_last_error())

    def consume(self):
        return self.kernel.WaitForSingleObject(self.event, 0) == 0

    def consume_restart(self):
        return self.kernel.WaitForSingleObject(self.restart_event, 0) == 0

    def close(self):
        for attr in ("mutex", "event", "shown_event", "restart_event"):
            handle = getattr(self, attr, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, attr, None)


def show_launch_panel(panel, arguments):
    if "--background" in arguments:
        panel.prewarm()
    else:
        panel.popup()


def main():
    _set_app_user_model_id()
    instance = LaunchInstance()
    if not instance.primary:
        try:
            if "--background" in sys.argv:
                return
            instance.notify()
            if instance.wait_shown():
                return
            instance.request_restart()
        finally:
            instance.close()
        # 驻留实例收到事件却未显示窗口时，等它正常退出，再接管主实例。
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            time.sleep(0.1)
            candidate = LaunchInstance()
            if candidate.primary:
                instance = candidate
                break
            candidate.close()
        else:
            # GUI 线程卡住无法退出时，恢复实例仍能让双击打开面板。
            recovery_name = LaunchInstance.default_name() + "_Recovery"
            instance = LaunchInstance(recovery_name)
            if not instance.primary:
                try:
                    instance.notify()
                    if instance.wait_shown():
                        return
                finally:
                    instance.close()
                instance = LaunchInstance(recovery_name + "_" + str(os.getpid()))
    app = QApplication.instance() or QApplication(sys.argv)
    C.apply_ui_font(app)                             # R130 全局字体栈：Segoe 拉丁+雅黑中文回退
    app.aboutToQuit.connect(instance.close)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(C.app_qicon())    # 多尺寸：任务栏/标题栏按最近档位取图不糊
    from storage_ui import migrate_on_startup
    # 大型索引迁移时继续回应启动 IPC，避免第二次双击误判为旧进程卡死。
    startup_activation = QTimer(app)
    startup_activation.setInterval(100)
    def activate_migration():
        if instance.consume():
            modal = app.activeModalWidget()
            if modal is not None:
                C.force_foreground(modal)
                instance.acknowledge_shown()
    startup_activation.timeout.connect(activate_migration)
    startup_activation.start()
    try:
        migrate_on_startup()
    finally:
        startup_activation.stop()
    S = C.load_settings()

    panel = Panel(S)
    tray = QSystemTrayIcon(C.app_qicon())
    tray.setToolTip("简截 Lite · 截图/长截图" if LITE else "简截 JianJie for Windows")   # R107 两版同驻可辨
    ctl = Controller(tray, panel, S)
    panel.actionTriggered.connect(ctl.dispatch)

    menu = QMenu()
    def add(label, fn):
        a = QAction(label, menu)
        a.triggered.connect(fn)
        menu.addAction(a)
    add("呼出主面板", panel.popup)
    add("截图", lambda: ctl.shot())
    add("长截图", lambda: ctl.long())
    if not LITE:
        add("简捷互传…", lambda: ctl.phone_receive())
    if not LITE:                                   # R107 轻量版：录屏/取色入口裁
        add("录屏", lambda: ctl.rec())
        add("屏幕取色", lambda: ctl.colorpick())         # R103
    add("设置…", lambda: (panel.popup(), ctl.settings()))
    add("设置存放位置", lambda: (panel.popup(), ctl.save_dir()))
    add("打开存放目录", lambda: ctl.open_save_dir())
    if not LITE:                                   # R107 轻量版：外围组件入口裁
        menu.addSeparator()
        add("番茄钟", lambda: ctl.pomodoro())          # 与面板动作表同步：待办/快记未在面板暴露，已移除
        add("任务清单", lambda: ctl.note())              # R87
    menu.addSeparator()

    auto_act = QAction("开机自启", menu)
    auto_act.setCheckable(True)
    auto_act.setChecked(EX.autostart_enabled())
    ctl._autostart_action = auto_act
    auto_act.toggled.connect(ctl.set_autostart)
    menu.addAction(auto_act)
    menu.addSeparator()
    add("退出简截", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda r: tray_activate(panel, r))
    tray.show()
    if "--phone-receive" in sys.argv:
        QTimer.singleShot(300, lambda: (ctl._toggle_phone_receive(True), ctl.phone_receive()))
    else:
        QTimer.singleShot(300, lambda: show_launch_panel(panel, sys.argv))
    def _activate_panel():
        if instance.consume_restart():
            app.quit()
            return
        if not instance.consume():
            return
        try:
            panel.setAttribute(Qt.WA_DontShowOnScreen, False)
            panel.popup()
        except Exception:
            # 窗口从屏外预热/最小化状态恢复失败时，仍尝试直接显示。
            try:
                panel.setAttribute(Qt.WA_DontShowOnScreen, False)
                panel.showNormal()
                panel.raise_()
                panel.activateWindow()
            except Exception:
                pass
        try:
            hwnd = int(panel.winId())
            if panel.isVisible() and ctypes.windll.user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                instance.acknowledge_shown()
        except Exception:
            pass

    activation = QTimer(app)
    activation.setInterval(100)
    activation.timeout.connect(_activate_panel)
    activation.start()
    if not LITE:                               # R107 轻量版无任务清单/文件搜索
        def close_index():
            if "fsearch" in sys.modules:
                sys.modules["fsearch"].shutdown()
        app.aboutToQuit.connect(close_index)
        QTimer.singleShot(0, ctl.tasks.load_and_show)   # R87：上次可见才恢复任务清单
        QTimer.singleShot(3000, ctl._prewarm_fsearch)   # 先完成启动显示，再静默准备文件索引
    _hkc = dict(DEFAULT_HOTKEYS)
    _hkc.update(S.get("hotkeys", {}))
    if "--background" in sys.argv:
        tray.showMessage("简截已就绪", f"{_hkc['panel']} 呼出面板 · {_hkc['shot']} 截图",
                         QSystemTrayIcon.Information, 2600)

    # Win32 热键消息到达时才唤醒 UI；排队信号保持线程安全和暂停时清队列的语义。
    bindings = [(i, *hk_parse(_hkc[key])) for i, (key, _l) in enumerate(_HOTKEY_ACTIONS, 1)]
    _hotkey_ctl["notify"] = ctl.hotkey_ready.emit
    def close_hotkeys():
        _hotkey_ctl["notify"] = None
        stop_hotkeys()
    app.aboutToQuit.connect(close_hotkeys)
    restart_hotkeys(bindings)

    cb = QTimer()
    cb.setInterval(1200)
    cb.timeout.connect(ctl.poll_clipboard)
    cb.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--fsearch-build":
        import fsearch
        sys.exit(fsearch.run_index_process(sys.argv[2], sys.argv[3], sys.argv[4] == "1"))
    if "--smoke" in sys.argv:
        if "--smoke-log" in sys.argv:
            log_path = sys.argv[sys.argv.index("--smoke-log") + 1]
            with open(log_path, "w", encoding="utf-8", buffering=1) as log:
                sys.stdout = sys.stderr = log
                try:
                    result = smoke()
                except Exception:
                    import traceback
                    traceback.print_exc()
                    result = 1
                finally:
                    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
            sys.exit(result)
        sys.exit(smoke())
    main()
