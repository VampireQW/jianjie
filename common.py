# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 公共基础：设置存储、路径、节假日数据、图标生成。"""
import ctypes
import json
import os
import struct
import sys
import uuid
from ctypes import wintypes
from datetime import date

from PySide6.QtCore import (QBuffer, QEvent, QEventLoop, QIODevice, QObject,
                            QPointF, QRect, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QApplication, QWidget

APP_NAME = "简截"
APP_VERSION = "4.13.7-win.1"
APP_BUILD_ID = "R301"


def utility_window_flags(on_top=False):
    """自绘标题栏的小应用保留任务栏入口，以便最小化后恢复。"""
    flags = (Qt.Window | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
             | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
    return flags | Qt.WindowStaysOnTopHint if on_top else flags


def ensure_utility_taskbar_entry(win):
    """带父窗口的设置/投屏对话框也需要独立的任务栏恢复入口。"""
    try:
        user = ctypes.windll.user32
        get_style = user.GetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style = user.SetWindowLongPtrW
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        handle = int(win.winId())
        style = get_style(handle, -20)
        updated = (style | 0x00040000) & ~0x00000080
        if style != updated:
            set_style(handle, -20, updated)
    except (AttributeError, OSError):
        pass

# ---------- 系统剪贴板（Win32 文件引用 / 静态图片） ----------
_CF_HDROP = 15
_GMEM_MOVEABLE_ZEROINIT = 0x0042
_FILE_CLIPBOARD_OWNER = None
_CLIPBOARD_WRITING = False


class _DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wintypes.DWORD), ("pt", wintypes.POINT),
                ("fNC", wintypes.BOOL), ("fWide", wintypes.BOOL)]


def _file_clipboard_owner():
    """持久隐藏窗口拥有剪贴板，不依赖即将销毁的控制条。"""
    global _FILE_CLIPBOARD_OWNER
    u32 = ctypes.windll.user32
    u32.IsWindow.argtypes = [wintypes.HWND]
    u32.IsWindow.restype = wintypes.BOOL
    if not _FILE_CLIPBOARD_OWNER or not u32.IsWindow(_FILE_CLIPBOARD_OWNER):
        u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                       wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        u32.CreateWindowExW.restype = wintypes.HWND
        _FILE_CLIPBOARD_OWNER = u32.CreateWindowExW(
            0, "STATIC", "JianJieClipboard", 0, 0, 0, 0, 0, wintypes.HWND(-3), None, None, None)
    return _FILE_CLIPBOARD_OWNER


def copy_file_to_clipboard(path):
    """把文件作为「文件引用」放进系统剪贴板（CF_HDROP，同资源管理器复制文件）：
    粘贴到资源管理器＝复制文件；粘贴到微信/QQ 等聊天窗＝以文件发送（GIF 可动、MP4 可播）。
    QClipboard.setImage 只能放静态图；双格式 QMimeData 有 0xC0000005 崩溃史，故走 Win32。
    64 位陷阱：Global*/SetClipboardData 的句柄参数必须显式声明 argtypes/restype，
    否则 ctypes 按 c_int 截断 64 位地址 → OverflowError: int too long to convert。"""
    path = os.path.abspath(os.fspath(path))
    if not os.path.isfile(path):
        return False
    header = _DROPFILES()
    header.pFiles = ctypes.sizeof(_DROPFILES)
    header.fWide = True
    return _set_clipboard_data(_CF_HDROP, bytes(header) + (path + "\0\0").encode("utf-16-le"))


def _set_clipboard_data(format_id, data=None, *, byte_count=None, fill=None):
    """重试处理系统事件期间禁止嵌套写入剪贴板。"""
    global _CLIPBOARD_WRITING
    if _CLIPBOARD_WRITING:
        return False
    _CLIPBOARD_WRITING = True
    try:
        return _write_clipboard_data(format_id, data, byte_count=byte_count, fill=fill)
    finally:
        _CLIPBOARD_WRITING = False


def _write_clipboard_data(format_id, data, *, byte_count=None, fill=None):
    """立即写入完整载荷并移交系统所有权，无延迟呈现或 Qt/OLE 数据对象。"""
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    owner = _file_clipboard_owner()
    if not owner:
        return False
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.restype = wintypes.BOOL
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalLock.restype = wintypes.LPVOID
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalFree.restype = wintypes.HGLOBAL
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    u32.SetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    # 剪贴板可能被短时占用；重试用于容错，不能替代文件头格式的正确性。
    import time as _t
    for _ in range(6):
        if u32.OpenClipboard(owner):
            break
        # 外部剪贴板监控可能持锁等待本应用呈现上一张 Qt 图片。
        # 重试前处理系统消息，排除鼠标键盘输入，避免只 sleep 造成互相等待。
        if QGuiApplication.instance() is not None:
            QGuiApplication.processEvents(QEventLoop.ExcludeUserInputEvents |
                                          QEventLoop.ExcludeSocketNotifiers, 10)
        _t.sleep(0.02)
    else:
        return False
    try:
        size = len(data) if fill is None else byte_count
        hglob = k32.GlobalAlloc(_GMEM_MOVEABLE_ZEROINIT, size)
        if not hglob:
            return False
        lock = k32.GlobalLock(hglob)
        if not lock:
            k32.GlobalFree(hglob)
            return False
        if fill is None:
            ctypes.memmove(lock, data, size)
        else:
            try:
                fill(lock)
            except Exception:
                k32.GlobalUnlock(hglob)
                k32.GlobalFree(hglob)
                raise
        k32.GlobalUnlock(hglob)
        if not u32.EmptyClipboard():
            k32.GlobalFree(hglob)
            return False
        if not u32.SetClipboardData(format_id, hglob):   # 成功后所有权归系统
            k32.GlobalFree(hglob)
            return False
        return True
    finally:
        u32.CloseClipboard()

def copy_text_to_clipboard(text):
    if not isinstance(text, str) or "\0" in text:
        return False
    if QGuiApplication.platformName() != "windows":
        QGuiApplication.clipboard().setText(text)
        return QGuiApplication.clipboard().text() == text
    return _set_clipboard_data(13, (text + "\0").encode("utf-16-le"))


def copy_image_to_clipboard(image):
    """图片以完整 CF_DIB 写入 Windows，跨进程粘贴不再等 GUI 线程延迟呈现。"""
    if image.isNull():
        return False
    if QGuiApplication.platformName() != "windows":
        # 非 Windows 平台（含离屏逻辑自检）使用对应平台的 Qt 剪贴板。
        cb = QGuiApplication.clipboard()
        cb.setImage(image)
        return cb.image().size() == image.size()
    # CF_DIB 是倒序 BGR 行；直接写进系统缓冲区，省去完整 BMP、bytes 与切片副本。
    bgr = image.convertToFormat(QImage.Format_BGR888)
    width, height = bgr.width(), bgr.height()
    if width <= 0 or height <= 0:
        return False
    row_bytes = width * 3
    stride = (row_bytes + 3) & ~3
    pixel_bytes = stride * height
    header = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24,
                         0, pixel_bytes, 0, 0, 0, 0)
    source = bgr.bits()
    source_address = ctypes.addressof(ctypes.c_char.from_buffer(source))
    source_stride = bgr.bytesPerLine()

    def fill(destination):
        ctypes.memmove(destination, header, len(header))
        for row in range(height):
            ctypes.memmove(destination + len(header) + row * stride,
                           source_address + (height - row - 1) * source_stride,
                           row_bytes)

    return _set_clipboard_data(8, byte_count=len(header) + pixel_bytes, fill=fill)


# ---------- 输入法跟随（R83） ----------
# Windows 按“线程/窗口”记忆输入语言：截图覆盖层每次新建，被分到系统默认布局（常见是
# 英文键盘），于是微信输入法/微软拼音不接管按键、只能敲英文。修复＝覆盖层抢前台之前
# 记下用户正在用的布局，文字浮层打开时把本进程切回它。
_SYSTEM_INPUT_HKL = None


def snapshot_input_locale():
    """记录前台线程正在用的键盘布局：必须在我们自己的窗口成为前台之前调用。"""
    global _SYSTEM_INPUT_HKL
    u32 = ctypes.windll.user32
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    u32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
    u32.GetKeyboardLayout.restype = wintypes.HKL
    fg = u32.GetForegroundWindow()
    if not fg:
        return
    pid = wintypes.DWORD()
    tid = u32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    if not tid or pid.value == os.getpid():
        return                       # 前台是我们自己：保留此前记录的用户布局
    hkl = u32.GetKeyboardLayout(tid)
    if hkl:
        _SYSTEM_INPUT_HKL = int(hkl)


def follow_system_input_locale():
    """文字浮层打开时把本进程切回用户刚在用的输入法语言（无记录则不动）。"""
    if not _SYSTEM_INPUT_HKL:
        return
    u32 = ctypes.windll.user32
    u32.ActivateKeyboardLayout.argtypes = [wintypes.HKL, wintypes.UINT]
    u32.ActivateKeyboardLayout.restype = wintypes.HKL
    prev = u32.ActivateKeyboardLayout(
        wintypes.HKL(_SYSTEM_INPUT_HKL), 0x100)  # KLF_SETFORPROCESS
    lang = _SYSTEM_INPUT_HKL & 0xFFFF
    if not prev and lang != 0x0409:  # 激活失败且非英文：按语言 ID 装载标准布局兜底
        u32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
        u32.LoadKeyboardLayoutW.restype = wintypes.HKL
        u32.LoadKeyboardLayoutW(f"{lang:04X}{lang:04X}", 0x101)  # KLF_ACTIVATE|KLF_SETFORPROCESS


# ---------- 目录 ----------
def data_dir():
    import storage
    return storage.data_dir()

def _writable_dir(sub_name, candidates):
    """在候选目录下建 sub_name 子目录，逐个试探，全部失败则退到数据目录。

    本机 Pictures/Videos/Documents 因 OneDrive 重定向残留已损坏（isdir=True 但
    任何创建都报 WinError 2），不能只信 isdir，必须真实试写。"""
    import storage
    for cand in dict.fromkeys(candidates):
        if not cand or not os.path.isdir(cand):
            continue
        sub = os.path.join(cand, sub_name)
        try:
            os.makedirs(sub, exist_ok=True)
            storage.validate_directory(sub)
            return sub
        except OSError:
            continue
    d = os.path.join(data_dir(), sub_name)
    os.makedirs(d, exist_ok=True)
    storage.validate_directory(d)
    return d

def _shell_folder(value):
    import winreg
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        v, _ = winreg.QueryValueEx(k, value)
        return v
    except Exception:
        return None

def save_base_dir():
    return load_settings().get("save_dir") or None


_DEFAULT_OUTPUT_DIR = None


def output_dir(create=True):
    """选中的目录直接接收成品；失效配置报错，不悄悄另存。"""
    global _DEFAULT_OUTPUT_DIR
    base = save_base_dir()
    if base:
        if create and not os.path.isdir(base):
            raise OSError("存放位置不可用：" + base)
        return base
    home = os.path.expanduser("~")
    candidates = [_shell_folder("My Pictures"), os.path.join(home, "Pictures"),
                  _shell_folder("Desktop"), os.path.join(home, "Desktop"), data_dir()]
    if create:
        # Windows 已知文件夹可能存在却无法创建文件；默认目录逐个真实试写。
        _DEFAULT_OUTPUT_DIR = _writable_dir(APP_NAME, candidates)
        return _DEFAULT_OUTPUT_DIR
    return _DEFAULT_OUTPUT_DIR or os.path.join(
        next(p for p in candidates if p and os.path.isdir(p)), APP_NAME)


def pictures_dir():
    return output_dir()


def videos_dir():
    return output_dir()

def documents_dir():
    return output_dir()


def transfers_dir(create=True):
    configured = load_settings().get("receive_dir")
    directory = configured or os.path.join(output_dir(create=False), "互传文件")
    if create:
        if configured and not os.path.isdir(directory):
            raise OSError("互传文件夹不可用：" + directory)
        os.makedirs(directory, exist_ok=True)
    return directory

def storage_locations():
    """返回当前实际落盘路径及是否需要设置；失效配置不能冒充生效目录。"""
    configured = load_settings().get("save_dir") or ""
    root = output_dir(create=False)
    paths = {"pictures": root, "videos": root, "documents": root,
             "data": data_dir(), "receive": transfers_dir(create=False)}
    ready = bool(configured) and os.path.isdir(configured)
    return configured, ready, paths


def validate_save_base(directory):
    import storage
    storage.validate_directory(directory)


def historical_output_dirs():
    """旧分类目录和旧兜底路径；迁移时只识别简截生成的成品名。"""
    home = os.path.expanduser("~")
    bases = [save_base_dir(), _shell_folder("My Pictures"), _shell_folder("My Video"),
             _shell_folder("Personal"), os.path.join(home, "Pictures"),
             os.path.join(home, "Videos"), os.path.join(home, "Documents"),
             os.path.join(home, "Desktop"), data_dir()]
    roots = [output_dir(create=False), data_dir()]
    for base in bases:
        if base:
            roots.extend(os.path.join(base, APP_NAME, leaf) for leaf in ("截图", "录屏", "新建"))
    return list(dict.fromkeys(roots))


def refresh_data_paths():
    global TODO_PATH, NOTES_PATH, TASKS_PATH, LEGACY_STICKIES_PATH
    directory = data_dir()
    TODO_PATH = os.path.join(directory, "todos.json")
    NOTES_PATH = os.path.join(directory, "notes.md")
    TASKS_PATH = os.path.join(directory, "tasks.json")
    LEGACY_STICKIES_PATH = os.path.join(directory, "stickies.json")


def timestamp():
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def save_image(image, path):
    """PNG 原子落盘；失败保留已有文件，不留下半张成品。"""
    import tempfile
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".jianjie-image-", suffix=".tmp", dir=os.path.dirname(path))
        os.close(fd)
        if image.isNull() or not image.save(temporary, "PNG"):
            raise OSError("图片写入失败，请检查目录权限和磁盘空间")
        with open(temporary, "rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)
    return True


_CLIPBOARD_MEDIA_ACTIVE = set()


def clipboard_cache_dir():
    """文件型剪贴板的临时载体；当前剪贴板引用的文件不能提前清理。"""
    import tempfile
    import time
    directory = os.path.join(tempfile.gettempdir(), "JianJieClipboard")
    os.makedirs(directory, exist_ok=True)
    protected = set(_CLIPBOARD_MEDIA_ACTIVE)
    try:
        mime = QGuiApplication.clipboard().mimeData()
        if mime:
            protected.update(os.path.normcase(os.path.abspath(url.toLocalFile()))
                             for url in mime.urls() if url.isLocalFile())
    except Exception:
        return directory
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if not name.startswith(("GIF_", "录屏_", "._jianjie_gif_")) or \
                os.path.normcase(path) in protected:
            continue
        try:
            if os.path.isfile(path) and time.time() - os.path.getmtime(path) > 24 * 3600:
                os.remove(path)
        except OSError:
            pass
    return directory


def retain_clipboard_media(path):
    _CLIPBOARD_MEDIA_ACTIVE.add(os.path.normcase(os.path.abspath(path)))


def release_clipboard_media(path):
    if path:
        _CLIPBOARD_MEDIA_ACTIVE.discard(os.path.normcase(os.path.abspath(path)))


def clipboard_media_path(prefix, suffix):
    path = os.path.join(clipboard_cache_dir(), prefix + "_" + timestamp() + suffix)
    retain_clipboard_media(path)
    return path


def publish_media_file(source, directory):
    """下载时才将完成的缓存文件移到保存目录，跨盘复制也使用原子替换。"""
    import errno
    import shutil
    import tempfile
    target = os.path.join(directory, os.path.basename(source))
    stem, extension = os.path.splitext(target)
    number = 2
    while os.path.exists(target):
        target = f"{stem}_{number}{extension}"
        number += 1
    try:
        os.replace(source, target)
    except OSError as error:
        if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
            raise
        pending = None
        try:
            fd, pending = tempfile.mkstemp(prefix=".jianjie-download-", dir=directory)
            with os.fdopen(fd, "wb") as output, open(source, "rb") as input_file:
                shutil.copyfileobj(input_file, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if os.path.getsize(pending) != os.path.getsize(source):
                raise OSError("文件保存不完整，请重试")
            os.replace(pending, target)
            pending = None
            try:
                os.remove(source)
            except OSError:
                pass
        finally:
            if pending and os.path.exists(pending):
                os.remove(pending)
    release_clipboard_media(source)
    return target

# ---------- 设置 ----------
DEFAULTS = {
    "theme": "light",           # dark / light（R133 起默认浅色 mac 风，design.md）
    "hotkey_panel": "Ctrl+Alt+Space",
    "hotkey_shot": "Ctrl+Alt+A",
    "hotkey_long": "Ctrl+Alt+S",
    "hotkey_rec": "Ctrl+Alt+R",
    "rec_fps": 30,             # 录屏帧率：30 / 40 / 50 / 60
    "toast_corner": "bottom_right",
    "task_nudge_enabled": False,
    "task_nudge_interval": 60,
    "task_nudge_start": "09:00",
    "task_nudge_end": "18:00",
    "shot_cursor": "crosshair",
    "phone_receive_enabled": False,
    "pomodoro_work": 25,
    "pomodoro_rest": 5,
    "scheme": "默认",
    "save_dir": "",             # 用户自定义存放位置；空=按系统图片/视频/文档目录
    "receive_dir": "",
}

def load_settings():
    p = os.path.join(data_dir(), "settings.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    d.pop("save_shot_on_finish", None)
    d.pop("save_shot_auto_copy", None)
    out = dict(DEFAULTS)
    out.update(d)
    return out


TOAST_CORNERS = (("top_left", "左上角"), ("top_right", "右上角"),
                 ("bottom_left", "左下角"), ("bottom_right", "右下角"))


SHOT_CURSOR_STYLES = (("crosshair", "十字", "crosshair"),
                      ("arrow", "默认鼠标", "mouse-pointer"),
                      ("hand", "小手", "hand"))


def shot_selection_cursor(settings=None):
    settings = load_settings() if settings is None else settings
    return {"crosshair": Qt.CrossCursor, "arrow": Qt.ArrowCursor,
            "hand": Qt.PointingHandCursor}.get(settings.get("shot_cursor"), Qt.CrossCursor)


def position_screen_notice(window, settings, screen=None):
    screen = screen or QGuiApplication.primaryScreen()
    if screen is None:
        return
    corner = settings.get("toast_corner", "bottom_right")
    if corner not in dict(TOAST_CORNERS):
        corner = "bottom_right"
    area = screen.availableGeometry()
    right = area.left() + max(0, area.width() - window.width())
    bottom = area.top() + max(0, area.height() - window.height())
    x = area.left() + 24 if corner.endswith("left") else right - 24
    y = area.top() + 24 if corner.startswith("top") else bottom - 24
    window.move(max(area.left(), min(x, right)), max(area.top(), min(y, bottom)))

def save_settings(s):
    import tempfile
    directory = data_dir()
    p = os.path.join(directory, "settings.json")
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="settings-", suffix=".tmp", delete=False) as f:
            pending = f.name
            json.dump(s, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(pending, p)
    finally:
        if pending and os.path.exists(pending):
            os.remove(pending)

# ---------- 待办 ----------
TODO_PATH = os.path.join(data_dir(), "todos.json")

def load_todos():
    try:
        with open(TODO_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [{"t": "体验简截的截图", "d": False, "done": False}]

def save_todos(todos):
    with open(TODO_PATH, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)

# ---------- 快记 ----------
NOTES_PATH = os.path.join(data_dir(), "notes.md")

def load_notes():
    try:
        with open(NOTES_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "# 快记\n\n随手记录文本和公式，锁定后置顶参考。\n"

def save_notes(text):
    with open(NOTES_PATH, "w", encoding="utf-8") as f:
        f.write(text)

# ---------- 任务清单（R87，由 R85 便签改造） ----------
# 每个任务：{"id","text","prio","done","created","done_at","updated","deleted"}
# 窗口状态：{"x","y","w","h","ontop","visible"}；整文件＝{"win": {...}, "tasks": [...]}
# id/updated/deleted 为云端同步预留（软删除才能合并多端）；读写收敛在这两个函数，
# 后续上云只替换本层，UI 与控制器不动。
TASKS_PATH = os.path.join(data_dir(), "tasks.json")
LEGACY_STICKIES_PATH = os.path.join(data_dir(), "stickies.json")   # R85 旧便签（一次性迁移源）

def load_tasks():
    """读任务清单；损坏/非 dict 兜底空结构。tasks.json 不存在而旧 stickies.json
    有未删便签时一次性迁移（用户 R87 澄清真实意图是任务清单，便签内容不丢）。"""
    try:
        with open(TASKS_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            if not isinstance(d.get("win"), dict):
                d["win"] = {}
            if not isinstance(d.get("tasks"), list):
                d["tasks"] = []
            return d
    except Exception:
        pass
    try:
        import time as _t
        with open(LEGACY_STICKIES_PATH, "r", encoding="utf-8") as f:
            old = json.load(f)
        ts = []
        for n in old if isinstance(old, list) else []:
            txt = (n.get("text") or "").strip() if isinstance(n, dict) else ""
            if txt and not n.get("deleted"):
                ts.append({"id": n.get("id") or uuid.uuid4().hex, "text": txt,
                           "prio": 0, "done": False,
                           "created": n.get("updated", _t.time()), "done_at": 0.0,
                           "updated": n.get("updated", _t.time()), "deleted": False})
        if ts:
            return {"win": {}, "tasks": ts}
    except Exception:
        pass
    return {"win": {}, "tasks": []}

def save_tasks(data):
    # 原子写（tmp+replace）：崩溃/同步盘截断不会留下半个 JSON
    tmp = TASKS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TASKS_PATH)

# ---------- 法定节假日（逐年官方通知；[date, name, isOffDay] 1=休 0=班/调休上班）----------
# 2025：国办发明电〔2024〕12 号（2024-11-12 发布）
HOLIDAYS_2025 = [
    ["2025-01-01", "元旦", 1],
    ["2025-01-26", "春节", 0], ["2025-01-28", "春节", 1], ["2025-01-29", "春节", 1],
    ["2025-01-30", "春节", 1], ["2025-01-31", "春节", 1], ["2025-02-01", "春节", 1],
    ["2025-02-02", "春节", 1], ["2025-02-03", "春节", 1], ["2025-02-04", "春节", 1],
    ["2025-02-08", "春节", 0],
    ["2025-04-04", "清明节", 1], ["2025-04-05", "清明节", 1], ["2025-04-06", "清明节", 1],
    ["2025-04-27", "劳动节", 0],
    ["2025-05-01", "劳动节", 1], ["2025-05-02", "劳动节", 1], ["2025-05-03", "劳动节", 1],
    ["2025-05-04", "劳动节", 1], ["2025-05-05", "劳动节", 1],
    ["2025-05-31", "端午节", 1], ["2025-06-01", "端午节", 1], ["2025-06-02", "端午节", 1],
    ["2025-09-28", "国庆节", 0],
    ["2025-10-01", "国庆节", 1], ["2025-10-02", "国庆节", 1], ["2025-10-03", "国庆节", 1],
    ["2025-10-04", "国庆节", 1], ["2025-10-05", "国庆节", 1], ["2025-10-06", "中秋节", 1],
    ["2025-10-07", "国庆节", 1], ["2025-10-08", "国庆节", 1], ["2025-10-11", "国庆节", 0],
]
# 2026：国务院办公厅 2025-11-04 通知
HOLIDAYS_2026 = [
    ["2026-01-01", "元旦", 1], ["2026-01-02", "元旦", 1], ["2026-01-03", "元旦", 1],
    ["2026-01-04", "元旦", 0], ["2026-02-14", "春节", 0], ["2026-02-15", "春节", 1],
    ["2026-02-16", "春节", 1], ["2026-02-17", "春节", 1], ["2026-02-18", "春节", 1],
    ["2026-02-19", "春节", 1], ["2026-02-20", "春节", 1], ["2026-02-21", "春节", 1],
    ["2026-02-22", "春节", 1], ["2026-02-23", "春节", 1], ["2026-02-28", "春节", 0],
    ["2026-04-04", "清明节", 1], ["2026-04-05", "清明节", 1], ["2026-04-06", "清明节", 1],
    ["2026-05-01", "劳动节", 1], ["2026-05-02", "劳动节", 1], ["2026-05-03", "劳动节", 1],
    ["2026-05-04", "劳动节", 1], ["2026-05-05", "劳动节", 1], ["2026-05-09", "劳动节", 0],
    ["2026-06-19", "端午节", 1], ["2026-06-20", "端午节", 1], ["2026-06-21", "端午节", 1],
    ["2026-09-20", "国庆节", 0], ["2026-09-25", "中秋节", 1], ["2026-09-26", "中秋节", 1],
    ["2026-09-27", "中秋节", 1], ["2026-10-01", "国庆节", 1], ["2026-10-02", "国庆节", 1],
    ["2026-10-03", "国庆节", 1], ["2026-10-04", "国庆节", 1], ["2026-10-05", "国庆节", 1],
    ["2026-10-06", "国庆节", 1], ["2026-10-07", "国庆节", 1], ["2026-10-10", "国庆节", 0],
]
HOLIDAYS_ALL = HOLIDAYS_2025 + HOLIDAYS_2026
HOL_MAP = {h[0]: h for h in HOLIDAYS_ALL}
HOL_YEARS = (2025, 2026)        # 有官方数据的年份；2027 安排国务院尚未发布（约前一年 11 月发布后补录）

# ---------- 主题 ----------
DARK = {
        "panel": "#1a1c26", "panel_solid": "#1a1c26",      # R136：暗色壳底实色化（用户点名「暗色主题也改为不透明」）
    "card": "rgba(255,255,255,14)", "card_hover": "rgba(255,255,255,24)",
    "line": "rgba(255,255,255,20)",
    "tx": "#eef1f7", "tx2": "#a7adbc", "tx3": "#7c8294",
    "accent": "#3b82f6", "accent2": "#60a5fa",
    "input": "rgba(255,255,255,16)",
}
LIGHT = {
    # R133 mac 浅色系（design.md §2）；R134 用户点名：壳底改实色（半透退役）；
    # R145 用户点名「白色加灰底不是特别好、保存位置条没有灰底、不精致，换个设计」——
    # 整窗灰底容器化：壳底拉到与内容底同灰（#f5f5f7），body 灰底与壳融合边界感消失，
    # 白卡+柔影浮灰底（mac 系统设置范式），头部/保存条/动作区同落灰底不再割裂
    "panel": "#f5f5f7", "panel_solid": "#f5f5f7",
    "card": "#ffffff", "card_hover": "#eff1f7",
    "line": "rgba(0,0,0,10)",
    "tx": "#1d1d2b", "tx2": "#4c5364", "tx3": "#8a90a2",
    "accent": "#2563eb", "accent2": "#3b82f6",
    "input": "rgba(0,0,0,6)",
}

def palette(theme):
    return LIGHT if theme == "light" else DARK

# ---------- 应用图标（程序化绘制，免资源文件）----------
_icon_cache = {}

def app_icon(size=64, bg1="#4f8bf7", bg2="#8b5cf6"):
    """应用图标：蓝紫渐变圆角方 + 白色取景框（四角括号 + 中心对焦点）。
    纯矢量图形、不依赖系统字体：缩到 16px 任务栏尺寸仍是清晰的“框选截图”，
    不会像文字图标那样糊成一团。"""
    key = (size, bg1, bg2)
    if key in _icon_cache:
        return _icon_cache[key]
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    S = size / 256.0
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1),
                        size * 0.23, size * 0.23)
    p.save()
    p.setClipPath(path)
    g = QLinearGradient(0, 0, size, size)            # 主底：亮蓝→紫对角渐变
    g.setColorAt(0.0, QColor(bg1))
    g.setColorAt(1.0, QColor(bg2))
    p.fillPath(path, g)
    hi = QLinearGradient(0, 0, 0, size * 0.52)       # 顶部高光：玻璃质感
    hi.setColorAt(0.0, QColor(255, 255, 255, 52))    # R140：64→52，小尺寸不显油
    hi.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(0, 0, size, int(size * 0.52), hi)
    lo = QLinearGradient(0, size * 0.68, 0, size)    # 底部轻收暗：压住重心
    lo.setColorAt(0.0, QColor(24, 16, 66, 0))
    lo.setColorAt(1.0, QColor(24, 16, 66, 40))       # R140：48→40 同步收淡
    p.fillRect(0, int(size * 0.68), size, int(size * 0.32), lo)
    p.restore()
    p.setPen(QPen(QColor(255, 255, 255, 54), max(1.0, 2.0 * S)))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)                                 # 内圈细白描边：立体不平板
    # 取景框：四角括号 + 中心对焦点，“框选截图”一眼可读
    # R140 呼吸位重分配（用户点名「主面板左上角不太精致」）：原臂端 104/256 与点缘 107/256
    # 间距仅 3/256（34px 下 0.4px 几乎相贴），括号外移+臂收短+点缩小+线改细后间距 9/256
    p.setPen(QPen(QColor("#ffffff"), 15.5 * S, Qt.SolidLine,
                  Qt.RoundCap, Qt.RoundJoin))
    inset, arm, e = 62 * S, 40 * S, size - 62 * S
    for x1, y1, x2, y2, x3, y3 in (
            (inset, inset + arm, inset, inset, inset + arm, inset),
            (e - arm, inset, e, inset, e, inset + arm),
            (e, e - arm, e, e, e - arm, e),
            (inset + arm, e, inset, e, inset, e - arm)):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.drawLine(QPointF(x2, y2), QPointF(x3, y3))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(size / 2, size / 2), 17 * S, 17 * S)   # R140：点 21→17 让出呼吸位
    p.end()
    _icon_cache[key] = pm
    return pm


def app_qicon():
    """多尺寸 QIcon：任务栏/标题栏按最接近档位取图，
    单一 64px pixmap 档会被系统缩放发糊，全尺寸入列各处都脆。"""
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(app_icon(s))
    return ic


def toast_text(msg):
    return f"{APP_NAME} · {msg}"


def exclude_from_capture(widget):
    """把窗口从屏幕捕获中排除（Win10 2004+ WDA_EXCLUDEFROMCAPTURE）。
    录屏/GIF/长截图控制条不烙进产物；否则条上的呼吸点每拍都算新内容，
    GIF 去重失效、长图首帧也带条。失败（旧系统/异常会话）静默降级。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = int(widget.winId())
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011))
    except Exception:
        return False

def prepare_capture_window(window):
    """捕获工具在模态弹窗之上接收输入，同时保持独立的窗口生命周期。"""
    modal = QApplication.activeModalWidget()
    if modal is None or modal is window:
        return
    # 只设置顶层窗口的临时所属关系，不用 QObject 父子关系；原弹窗关闭时
    # 不应销毁仍在录制的工具条。Qt/Windows 都能据此允许鼠标和键盘输入。
    modal.winId()
    window.winId()
    window.windowHandle().setTransientParent(modal.windowHandle())


def force_foreground(widget):
    """热键后台触发的小窗抢键盘焦点（R121：RecBar/LongShotBar/GifBar 弹出即调）。
    后台进程的 activateWindow 受 Windows 前台锁拦截（只闪任务栏不拿焦点，Esc 无处可去）；
    全局热键使本进程短暂持有前台权限，此时补 SetForegroundWindow 才能成功。
    ShotOverlay._activate_selection 同款；框选覆盖层一直有效，三条此前漏接。"""
    if sys.platform != "win32" or QGuiApplication.platformName() != 'windows':
        return
    try:
        handle = ctypes.c_void_p(int(widget.winId()))
        user = ctypes.windll.user32
        # A hidden STARTUPINFO can suppress the first normal window even
        # though Qt has already marked it visible. Honor Qt's explicit intent.
        if widget.isVisible() and not user.IsWindowVisible(handle):
            user.ShowWindow(handle, 5)  # SW_SHOW preserves the requested window state.
        user.SetForegroundWindow(handle)
    except Exception:
        pass

def is_frozen():
    return hasattr(sys, "frozen")


# ---------- 全局设计令牌（R90 起：全软件 UI 统一基准，说明见 design-system/jianjie-desktop/MASTER.md） ----------
UI_BG_CARD = "#10141e"     # 卡片底：实色深色（R155 用户点名「独立窗口不要透明，透底显乱」；同色相去 alpha。例外保留半透 rgba(16,20,30,202)：任务清单 sticky taskBody、网速浮条 nsBody）
UI_POPUP_BG = "#181d2a"   # 年/月选择浮层实底：不与日历内容透叠
UI_RADIUS = 10                        # 卡片圆角（R146 收 mac 档 14→10：HIG 唯一裁决）
UI_BORDER = "rgba(255,255,255,18)"   # 卡片 1px 细边
UI_ACCENT = "#3b82f6"                # 选中/填充主色（勾选框、选中格、焦点边）
UI_ACCENT_BORDER = "#4a5bd6"          # 描边主色（胶囊输入框常态边）
UI_BADGE_FROM = "#60a5fa"             # 徽章渐亮起
UI_BADGE_TO = "#2563eb"               # 徽章渐变止
UI_TEXT = "#e5e7eb"                  # 主文本
UI_TEXT2 = "#9ca3af"                 # 次文本
UI_TEXT3 = "#6b7280"                 # 弱文本/占位
UI_ERROR_TEXT = "#f87171"            # 深色卡片可读的错误反馈
UI_DANGER = "#ef4444"                # 危险操作
UI_TASK_BG = "rgba(20,20,20,174)"    # 偏黑的约 68% 不透明底；白底叠加后约 #5F5F5F
UI_TASK_TEXT = "#f3f4f6"
UI_TASK_MUTED = UI_TEXT
UI_TASK_CONTROL = "#d4d7dc"
UI_TASK_INPUT_BORDER = "#bfc3ca"
UI_TASK_ADD_BG = (255, 255, 255, 8)
UI_TASK_ADD_FOCUS_BG = (255, 255, 255, 12)
UI_TASK_OVERDUE = "#ffe0dc"
UI_TASK_LINE = "rgba(255,255,255,32)"
UI_TASK_PRIORITY_LOW = "#93c5fd"
UI_TASK_PRIORITY_MEDIUM = "#fcd34d"
UI_TASK_PRIORITY_HIGH = "#ffabab"
# QMenu 全局样式（R98 首例于任务清单优先级下拉，R100 推广截图工具条）：
# QMenu 是不透顶层窗，不吃宿主样式表，必须自带 QSS——底=半透卡的不透版
UI_MENU_QSS = """
QMenu { background:rgba(24,29,42,245); border:1px solid rgba(255,255,255,18);
  border-radius:8px; padding:4px; }
QMenu::item { color:#e5e7eb; padding:5px 20px 5px 8px; border-radius:5px;
  font-size:12.5px; }
QMenu::item:selected { background:rgba(59,130,246,30); }
QMenu::item:checked { color:#60a5fa; font-weight:600; }
"""


class CaptureRightCancel(QObject):
    """捕获窗口及子控件的右键统一退出；实时捕获也监听透传区域的右键按下。"""

    def __init__(self, owner, cancel, live=False):
        super().__init__(owner)
        self.owner = owner
        self.cancel = cancel
        self._pressed = False
        self._suspended = False
        self._timer = QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._poll)
        self._live = live and QGuiApplication.platformName() == "windows"
        QGuiApplication.instance().installEventFilter(self)
        if self._live and owner.isVisible():
            self._pressed = self._right_down()
            self._timer.start()

    @staticmethod
    def _right_down():
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x02) & 0x8000)

    def _poll(self):
        if self._suspended:
            return
        down = self._right_down()
        pressed, self._pressed = self._pressed, down
        if down and not pressed and self.owner.isVisible():
            self.cancel()

    def set_suspended(self, suspended):
        self._suspended = bool(suspended)
        self._timer.stop()
        if not self._suspended and self._live and self.owner.isVisible():
            self._pressed = self._right_down()
            self._timer.start()

    def eventFilter(self, obj, ev):
        if obj is self.owner:
            if ev.type() == QEvent.Show and self._live and not self._suspended:
                self._pressed = self._right_down()
                self._timer.start()
            elif ev.type() in (QEvent.Hide, QEvent.Close):
                self._timer.stop()
        if (not self._suspended and ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.RightButton
                and self.owner.isVisible() and isinstance(obj, QWidget)
                and (obj is self.owner or self.owner.isAncestorOf(obj))):
            self._pressed = True
            self.cancel()
            return True
        return False


# ---------- R130 全局 UI 字体栈 ----------
UI_FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]


def apply_ui_font(app):
    """R130 全局字体栈：拉丁/数字/标点走 Segoe UI Variable（Win11 Fluent 原生精致感），
    中文按 family 顺序回退微软雅黑 UI——Qt 按字符逐字匹配，缺字形的 family 自动跳过。
    注意：各窗 QSS 若单写 font-family:'Microsoft YaHei UI' 会整体掩盖本栈，须同步改栈。"""
    f = QFont(UI_FONT_FAMILIES[0])
    if hasattr(f, "setFamilies"):                    # Qt6.1+ 家族列表回退
        f.setFamilies(UI_FONT_FAMILIES)
    app.setFont(f)


# ---------- R127 无边框窗全向缩放 + 跨屏自适应（面板/任务清单共用） ----------
class EdgeResizer(QObject):
    """无边框窗八向缩放：装在 QApplication 上筛目标窗子孙事件，边缘 8px 热区拖拽调整
    geometry（左/右/上/下/四角），hover 给出对应光标形状。子控件区边缘同样生效。
    与宿主自身拖拽不冲突——只拦截热区内 press/move。"""

    EDGE = 8
    resizeFinished = Signal()
    _CURSORS = {1: Qt.SizeHorCursor, 2: Qt.SizeHorCursor,
                4: Qt.SizeVerCursor, 8: Qt.SizeVerCursor,
                5: Qt.SizeFDiagCursor, 10: Qt.SizeFDiagCursor,
                6: Qt.SizeBDiagCursor, 9: Qt.SizeBDiagCursor}   # 位：左1 右2 上4 下8

    def __init__(self, win, min_w, min_h, edge_inset=0):
        super().__init__(win)
        self.win = win
        self.min_w, self.min_h = min_w, min_h
        self.edge_inset = edge_inset
        self._dir = 0
        self._g0 = None
        self._geo0 = None
        self._cur = None                            # 当前已设光标形状（去重防递归）
        win.setMouseTracking(True)
        QGuiApplication.instance().installEventFilter(self)
        win.destroyed.connect(self._detach)         # 窗销毁即摘除——否则过滤器持有已删对象，
                                                    # 其后任意事件 isVisible() 直接 RuntimeError
    def _detach(self):
        app = QGuiApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    def _hit(self, pos):
        r = self.win.rect()
        if self.edge_inset:
            # 半透明窗的外圈是阴影留白；热区应沿可见卡片边缘，而不是沿透明窗口边缘。
            r.adjust(self.edge_inset, self.edge_inset,
                     -self.edge_inset, -self.edge_inset)
            if not r.adjusted(-self.EDGE, -self.EDGE,
                              self.EDGE, self.EDGE).contains(pos):
                return 0
            d = 0
            if abs(pos.x() - r.left()) <= self.EDGE:
                d |= 1
            elif abs(pos.x() - r.right()) <= self.EDGE:
                d |= 2
            if abs(pos.y() - r.top()) <= self.EDGE:
                d |= 4
            elif abs(pos.y() - r.bottom()) <= self.EDGE:
                d |= 8
            return d
        d = 0
        if pos.x() <= self.EDGE:
            d |= 1
        elif pos.x() >= r.width() - self.EDGE:
            d |= 2
        if pos.y() <= self.EDGE:
            d |= 4
        elif pos.y() >= r.height() - self.EDGE:
            d |= 8
        return d

    def eventFilter(self, obj, ev):
        try:
            if not isinstance(obj, QWidget) or not self.win.isVisible():
                return False
            if obj is not self.win and not self.win.isAncestorOf(obj):
                return False
        except RuntimeError:                        # win C++ 对象已删（防御；正常已被 _detach 摘除）
            return False
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress and ev.button() == Qt.LeftButton:
            d = self._hit(self.win.mapFromGlobal(ev.globalPosition().toPoint()))
            if d:
                self._dir = d
                self._g0 = ev.globalPosition().toPoint()
                self._geo0 = QRect(self.win.geometry())
                return True                     # 热区按下：拦截，进缩放
        elif t == QEvent.Type.MouseMove:
            gp = ev.globalPosition().toPoint()
            if self._dir:
                dx, dy = gp.x() - self._g0.x(), gp.y() - self._g0.y()
                g = QRect(self._geo0)
                if self._dir & 1:
                    g.setLeft(min(g.left() + dx, g.right() - self.min_w))
                if self._dir & 2:
                    g.setRight(max(g.right() + dx, g.left() + self.min_w))
                if self._dir & 4:
                    g.setTop(min(g.top() + dy, g.bottom() - self.min_h))
                if self._dir & 8:
                    g.setBottom(max(g.bottom() + dy, g.top() + self.min_h))
                self.win.setGeometry(g)
                return True
            d = self._hit(self.win.mapFromGlobal(gp))
            cur = self._CURSORS.get(d)
            if cur != self._cur:                    # 只在形状变化时 set/unset——
                self._cur = cur                     # setCursor 会触发合成 MouseMove，
                if cur is not None:                 # 不去重则 eventFilter 无限递归
                    self.win.setCursor(cur)
                else:
                    self.win.unsetCursor()
        elif t == QEvent.Type.MouseButtonRelease and self._dir:
            self._dir = 0
            cur = self._CURSORS.get(self._hit(
                self.win.mapFromGlobal(ev.globalPosition().toPoint())))
            self._cur = cur
            if cur is not None:
                self.win.setCursor(cur)
            else:
                self.win.unsetCursor()
            self.resizeFinished.emit()
            return True
        return False


def wire_screen_adaptive(win, min_w, min_h):
    """跨屏自适应：挪屏按逻辑分辨率比缩放窗口（1920→2560 逻辑宽 k=1.33），
    占比恒定、来回可逆——解决低分屏→高分屏「显得更小」。showEvent 里调一次即可。"""
    h = win.windowHandle()
    if h is None or getattr(win, "_scr_adapt_wired", False):
        return
    win._scr_adapt_wired = True
    win._scr_w = h.screen().geometry().width()

    def _on_change(scr):
        old = getattr(win, "_scr_w", 0)
        new = scr.geometry().width()
        if old and new and new != old:
            k = new / old
            win.resize(max(min_w, int(win.width() * k)),
                       max(min_h, int(win.height() * k)))
        win._scr_w = new

    h.screenChanged.connect(_on_change)
    win._on_screen_change = _on_change           # 暴露给冒烟直驱


def place_toolbar_near_region(bar, region, screen, gap=10, edge=6):
    """独立工具条与屏内逻辑选区右缘对齐，并限制在目标屏幕可用范围内。"""
    if region is None or screen is None:
        return False
    local = QRect(region).normalized()
    if not local.isValid():
        return False
    selected = local.translated(screen.geometry().topLeft())
    bounds = screen.availableGeometry()
    if not bounds.isValid():
        bounds = screen.geometry()
    width, height = max(1, bar.width()), max(1, bar.height())
    left = bounds.left() + edge
    right = max(left, bounds.right() - edge - width + 1)
    top = bounds.top() + edge
    bottom = max(top, bounds.bottom() - edge - height + 1)
    x = max(left, min(selected.right() - width + 1, right))
    below = selected.bottom() + 1 + gap
    above = selected.top() - height - gap
    if below <= bottom:
        y = max(top, below)
    elif above >= top:
        y = min(bottom, above)
    else:
        room_below = bounds.bottom() - edge - selected.bottom() - gap
        room_above = selected.top() - bounds.top() - edge - gap
        preferred = below if room_below >= room_above else above
        y = max(top, min(preferred, bottom))
    bar.move(x, y)
    return True

def set_topmost(win, on):
    """R150：Win32 SetWindowPos 直改 Z 序置顶/取消——不重建窗口句柄，
    根治 setWindowFlag(StaysOnTopHint) 隐窗重建造成的闪烁（用户点名「点钉窗口闪一下」）。
    Qt flags 层全程不含 StaysOnTop；钉态真相源在各窗 _pinned 实例属性。
    flags：SWP_NOSIZE 0x1 | SWP_NOMOVE 0x2 | SWP_NOACTIVATE 0x10 | SWP_NOSHOW 0x40。"""
    try:
        position = ctypes.windll.user32.SetWindowPos
        position.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT]
        position.restype = wintypes.BOOL
        position(int(win.winId()), wintypes.HWND(-1 if on else -2),
                 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
    except Exception:
        pass          # offscreen/非 Windows 容错：视觉行为降级不崩，钉态账由各窗 _pinned 自管
