# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 录屏：mss 取帧 + Media Foundation H.264 编码，浮动控制条。

设计要点：
- 窗口用 Qt.Window（与截图覆盖层一致），不用 Qt.Tool——后者在部分 Windows 环境
  下点击行为不可靠，且本条需要稳定接收鼠标/键盘事件。
- R120 起编码器为系统 Media Foundation H.264（recorder_mf.MfH264Writer），直吃
  mss 原生 BGRA 免色彩转换，全量版因此整体裁掉 OpenCV；选区缩放归一走 PIL 双线性。
- 任何失败都在条内红字显示原因，条上永远有 ✕ 关闭按钮 + Esc 可退出。
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QCursor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QMenu, QToolButton, QWidget

import common as C
import icons
from capture import RegionDim, RegionFrame, close_all, create_overlays
from common import copy_file_to_clipboard, videos_dir

FPS = 30.0                               # 默认目标帧率（录屏工具条可选 30/40/50/60）
FPS_OPTIONS = (30, 40, 50, 60)

import numpy as np                         # R107 起 GIF/长截图共用 stamp_cursor：轻量版（无 mss/MF）下仍可用
try:
    import mss
    from recorder_mf import MfH264Writer    # R120：MF H.264 取代 cv2.VideoWriter（系统组件，零三方依赖）
except Exception:                          # 环境异常时由 missing_deps() 自救注入全局
    mss = MfH264Writer = None


# ---- 鼠标指针提取：mss/GDI 抓帧不含光标，录制时需自己取指针对图合成 ----
class _CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("flags", ctypes.c_ulong),
                ("hCursor", ctypes.c_void_p), ("ptScreenPos", wintypes.POINT)]


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", ctypes.c_long), ("xHotspot", ctypes.c_ulong),
                ("yHotspot", ctypes.c_ulong), ("hbmMask", ctypes.c_void_p),
                ("hbmColor", ctypes.c_void_p)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
                ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
                ("bmPlanes", ctypes.c_ushort), ("bmBitsPixel", ctypes.c_ushort),
                ("bmBits", ctypes.c_void_p)]


class _BMIHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_ulong), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_ulong),
                ("biSizeImage", ctypes.c_ulong), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_ulong),
                ("biClrImportant", ctypes.c_ulong)]


class _BMI(ctypes.Structure):
    class _RGBQ(ctypes.Structure):
        _fields_ = [("b", ctypes.c_ubyte), ("g", ctypes.c_ubyte),
                    ("r", ctypes.c_ubyte), ("x", ctypes.c_ubyte)]
    _fields_ = [("bmiHeader", _BMIHEADER), ("bmiColors", _RGBQ * 1)]


def _cursor_rgba(hcursor):
    """HICON → (BGRA 数组, 热点 x, 热点 y)。

    彩色光标走 DrawIconEx 光栅化（R121 根修「录屏没指针」：GetDIBits 读 32bpp color
    位图必丢 alpha——现代 ARGB 系统光标的 AND 掩码又恒全 1，旧路两相叠加得 alpha 全 0，
    合成完全透明）；画进零初始化 32bpp DIB section，ARGB/旧式掩码彩色光标都被 GDI 正确
    光栅化出真 alpha。纯掩码单色光标保留 AND/XOR 路线（本就正确）。热点取自 GetIconInfo。"""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    # 64 位句柄必须声明为指针宽度的 argtypes，否则 ctypes 按 c_int 转换会 OverflowError
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(_ICONINFO)]
    user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_uint, wintypes.HANDLE, ctypes.c_uint]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(_BITMAP)]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                                ctypes.c_void_p, ctypes.POINTER(_BMI), ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BMI), ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, ctypes.c_ulong]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    ii = _ICONINFO()
    if not user32.GetIconInfo(hcursor, ctypes.byref(ii)):
        return None
    try:
        bm = _BITMAP()
        if not ii.hbmMask or not gdi32.GetObjectW(ii.hbmMask, ctypes.sizeof(_BITMAP), ctypes.byref(bm)):
            return None
        w, mh = bm.bmWidth, bm.bmHeight       # mh=掩码总行数：彩色=h，单色=2×字形高
        stride = ((w + 31) // 32) * 4
        if ii.hbmColor:
            dc = user32.GetDC(0)
            mem = gdi32.CreateCompatibleDC(dc)
            bmi = _BMI()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BMIHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -mh          # 自顶向下
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0      # BI_RGB
            ppv = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
            if not hbmp:
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(0, dc)
                return None
            old = gdi32.SelectObject(mem, hbmp)
            try:
                ctypes.memset(ppv, 0, w * mh * 4)   # 零初始化：背景 alpha=0
                if not user32.DrawIconEx(mem, 0, 0, hcursor, w, mh, 0, None, 3):   # DI_NORMAL
                    return None
                bgra = np.frombuffer(
                    (ctypes.c_uint8 * (w * mh * 4)).from_address(ppv.value),
                    dtype=np.uint8).reshape(mh, w, 4).copy()
                if bgra[..., 3].max() == 0:
                    # R122：GetIconInfo 兼容转换丢 alpha 的光标（I 型等）：alpha 全 0，
                    # 预乘颜色留在 RGB（DI_IMAGE 的 BitBlt 已整幅拷入）。RGB 非零＝字形
                    # 白芯，外扩 1px 补黑描边——深浅背景上都可见；RGB 也空才是真空位图。
                    core = (bgra[..., 0] > 0) | (bgra[..., 1] > 0) | (bgra[..., 2] > 0)
                    if not core.any():
                        return None                   # 光栅化没出任何像素：宁缺毋错（全透明=没指针）
                    pad = np.pad(core, 1)
                    ring = np.zeros_like(core)
                    for dy in range(3):               # 3x3 膨胀 − 芯 ＝ 描边环
                        for dx in range(3):
                            ring |= pad[dy:dy + core.shape[0], dx:dx + core.shape[1]]
                    ring &= ~core
                    col = np.where(core[..., None], bgra[..., :3], 0)   # 芯＝原色；环＝黑
                    bgra = np.dstack([col, ((core | ring) * 255).astype(np.uint8)])
            finally:
                gdi32.SelectObject(mem, old)
                gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(mem)
                user32.ReleaseDC(0, dc)
        else:
            dc = user32.GetDC(0)
            try:
                gh = mh // 2
                # R122：改 32bpp 展开读（GDI 自动展开调色板）——1bpp 读法要求 BITMAPINFO
                # 带 2 个颜色表项，_BMI 只含 1 项，GetDIBits 会越界写 4 字节。
                bmi = _BMI()
                bmi.bmiHeader.biSize = ctypes.sizeof(_BMIHEADER)
                bmi.bmiHeader.biWidth = w
                bmi.bmiHeader.biHeight = -mh         # 顶半 AND，底半 XOR
                bmi.bmiHeader.biPlanes = 1
                bmi.bmiHeader.biBitCount = 32
                bmi.bmiHeader.biCompression = 0
                buf = ctypes.create_string_buffer(w * mh * 4)
                if gdi32.GetDIBits(dc, ii.hbmMask, 0, mh, buf, ctypes.byref(bmi), 0) != mh:
                    return None
                bits = np.frombuffer(buf, dtype=np.uint8).reshape(mh, w, 4)[..., 0] > 127
                andm, xorm = bits[:gh], bits[gh:]    # 掩码位值 1（白）＝透明，0（黑）＝字形
                alpha = np.where(andm, 0, 255).astype(np.uint8)
                v = np.where(xorm, 255, 0).astype(np.uint8)
                bgra = np.dstack([v, v, v, alpha])
            finally:
                user32.ReleaseDC(0, dc)
        return bgra, int(ii.xHotspot), int(ii.yHotspot)
    finally:
        if ii.hbmMask:
            gdi32.DeleteObject(ii.hbmMask)
        if ii.hbmColor:
            gdi32.DeleteObject(ii.hbmColor)


_CURSOR_CACHE = {}                         # hCursor → (BGRA, hx, hy) / None（负缓存）
_MISS = object()


def cursor_sprite():
    """当前可见光标 → (BGRA 位图, 热点x, 热点y, 物理屏幕坐标x, y)；隐藏/提取失败 → None。
    进程为 PerMonitorV2 感知，GetCursorInfo 返回物理坐标，与 mss 物理帧同尺度。"""
    if np is None:
        return None
    try:
        ci = _CURSORINFO()
        ci.cbSize = ctypes.sizeof(_CURSORINFO)
        if not ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci)) or ci.flags != 1 or not ci.hCursor:
            return None
        ent = _CURSOR_CACHE.get(ci.hCursor, _MISS)
        if ent is _MISS:
            ent = _cursor_rgba(ci.hCursor)
            # R122：多键共存，不再 clear 单留——箭头/I 型/手型随鼠标内容高频切换，
            # 单键 clear 会让每帧都重建 GDI 对象（真机实测 30fps 掉到 ~8fps）；
            # 系统光标句柄同会话恒定，提取失败也负缓存防每帧重试。超上限整体重建防膨胀。
            if len(_CURSOR_CACHE) >= 16:
                _CURSOR_CACHE.clear()
            _CURSOR_CACHE[ci.hCursor] = ent
        if not ent:
            return None
        return ent[0], ent[1], ent[2], int(ci.ptScreenPos.x), int(ci.ptScreenPos.y)
    except Exception:
        return None


def stamp_cursor(frame, mon_left, mon_top, ox=0, oy=0, bgr_order=True, sprite=None):
    """把当前光标按 alpha 合成进帧（原地修改），返回是否落上了像素。
    frame：物理分辨率 ndarray；mon_left/top：帧左上角在虚拟屏内的物理坐标；
    ox/oy：帧相对整屏已裁掉的物理偏移；bgr_order=False 为 RGB 通道序（grab_array 用）。
    调用方须在选区缩放 resize 之前调用：指针随内容一起缩放，落点才不漂移。"""
    sp = sprite if sprite is not None else cursor_sprite()
    if not sp:
        return False
    try:
        bgra, hx, hy, px, py = sp
        fh, fw = frame.shape[:2]
        x = px - mon_left - hx - ox
        y = py - mon_top - hy - oy
        h, w = bgra.shape[:2]
        x0c, y0c = max(0, x), max(0, y)
        x1c, y1c = min(fw, x + w), min(fh, y + h)
        if x1c <= x0c or y1c <= y0c:
            return False                       # 指针在帧外
        sub = bgra[y0c - y:y1c - y, x0c - x:x1c - x]
        roi = frame[y0c:y1c, x0c:x1c]
        a = sub[..., 3:4].astype(np.float32) / 255.0
        col = sub[..., :3].astype(np.float32)
        if not bgr_order:
            col = col[..., ::-1]
        roi[...] = (col * a + roi * (1.0 - a)).astype(np.uint8)
        return True
    except Exception:
        return False


# ---- 鼠标点击高亮：GetAsyncKeyState 按下边沿检测，扩散圆环随帧淡出 ----
_CLICK_LIFE = 0.55                       # 点击事件存活秒（扩散+淡出全程）
_CLICKS = []                             # (perf_ts, 物理x, 物理y, 键 1=左 2=右)；GIL 下 append/切片赋值原子
_KEY_PREV = {1: False, 2: False}


def poll_click(now=None):
    """录制/GIF 抓帧节拍每拍调用：左右键按下边沿记一次点击事件，顺带清过期事件。返回活跃数。"""
    now = now or time.perf_counter()
    try:
        u32 = ctypes.windll.user32
        for vk, btn in ((0x01, 1), (0x02, 2)):
            down = bool(u32.GetAsyncKeyState(vk) & 0x8000)
            if down and not _KEY_PREV[btn]:
                pt = wintypes.POINT()
                if u32.GetCursorPos(ctypes.byref(pt)):
                    _CLICKS.append((now, int(pt.x), int(pt.y), btn))
            _KEY_PREV[btn] = down
    except Exception:
        pass
    _CLICKS[:] = [e for e in _CLICKS if now - e[0] <= _CLICK_LIFE]
    return len(_CLICKS)


def stamp_click_fx(frame, mon_left, mon_top, ox=0, oy=0, bgr_order=True, now=None):
    """把未过期点击画成扩散圆环（原地修改），返回画上的环数。坐标/通道序语义同 stamp_cursor，
    同样须在选区缩放 resize 之前调用：圆环随内容缩放，落点不漂移。"""
    now = now or time.perf_counter()
    fh, fw = frame.shape[:2]
    hit = 0
    for t0, px, py, btn in list(_CLICKS):
        age = now - t0
        if age < 0 or age > _CLICK_LIFE:
            continue
        cx, cy = px - mon_left - ox, py - mon_top - oy
        r = 7.0 + age * 85.0                           # 7→54px 扩散
        a = 0.8 * (1.0 - age / _CLICK_LIFE)            # 同步淡出
        x0c, y0c = max(0, int(cx - r - 3)), max(0, int(cy - r - 3))
        x1c, y1c = min(fw, int(cx + r + 4)), min(fh, int(cy + r + 4))
        if x1c <= x0c or y1c <= y0c:
            continue
        yy, xx = np.mgrid[y0c:y1c, x0c:x1c]
        ring = np.abs(np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) - r) <= 1.6
        if not ring.any():
            continue
        col = (60, 90, 255) if btn == 2 else (0, 196, 255)   # BGR：右键红、左键金黄
        if not bgr_order:
            col = col[::-1]
        roi = frame[y0c:y1c, x0c:x1c]
        cvec = np.array(col, np.float32)
        roi[ring] = (cvec * a + roi[ring] * (1.0 - a)).astype(np.uint8)
        hit += 1
    return hit


def _resize_bgra(bgra, w, h):
    """PIL 双线性缩放 BGRA 帧到 (w,h)（≡ cv2.resize 默认 INTER_LINEAR，gifrec R108 同款先例）。"""
    from PIL import Image
    return np.asarray(Image.fromarray(bgra, "RGBA").resize((w, h), Image.BILINEAR))


def missing_deps():
    """检查并导入录屏依赖；成功时把模块绑定到全局（模块级 try 失败后仍可自救）。"""
    out = []
    g = globals()
    for m in ("mss", "numpy"):
        try:
            mod = __import__(m)
        except Exception:
            out.append(m)
            continue
        g["np" if m == "numpy" else m] = mod
    if g.get("MfH264Writer") is None:
        try:
            from recorder_mf import MfH264Writer as _W
            g["MfH264Writer"] = _W
        except Exception:
            out.append("mf")                 # 系统组件缺失（Windows N 版），非 pip 可解
    return out


class _FpsPickerButton(QToolButton):
    """帧率菜单按钮：用自绘 V 形箭头，避开不同 Windows style 的原生 menu-indicator 偏斜。"""

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self.isEnabled():
            color = QColor("#8b93a7")
        elif self.underMouse() or self.isDown():
            color = QColor("#ffffff")
        else:
            color = QColor("#dfe4ef")
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(color, 1.7)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() - 12, self.height() // 2
        p.drawLine(cx - 3, cy - 1, cx, cy + 2)
        p.drawLine(cx, cy + 2, cx + 3, cy - 1)
        p.end()


class RecBar(QWidget):
    """录屏控制条：开始 / 时长 / 停止。⬇ 下载保存，✓ 只复制到剪贴板。"""
    def __init__(self, on_done, on_error, sel=None, scr=None):
        super().__init__(None)
        self.on_done = on_done
        self.on_error = on_error
        self._preset = (sel, scr)              # R131 截图工具条直通：选区现成，跳过框选
        saved_fps = C.load_settings().get("rec_fps", int(FPS))
        self.fps = saved_fps if saved_fps in FPS_OPTIONS else int(FPS)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowIcon(QIcon(C.app_icon()))
        self.setStyleSheet(
            "QWidget{background:rgba(16,18,28,235);border:none;border-radius:10px;}"   # R134：白色描边退役；R146 圆角收 mac 档
            "QLabel{color:#dfe4ef;font-size:12.5px;background:transparent;border:none;}"
            "QLabel#dim{color:#8b93a7;font-size:12px;background:transparent;border:none;}"
            "QLabel#time{color:#fff;font-size:13.5px;font-weight:600;background:transparent;border:none;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        lay.setSpacing(8)

        def sep():
            f = QFrame()
            f.setFixedSize(1, 18)
            f.setStyleSheet("background:rgba(255,255,255,28);border:none;")
            lay.addWidget(f)

        _scr = scr or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self._toolbar_screen = _scr              # 无选区时沿用打开控制条所在屏
        dpr = (_scr.devicePixelRatio() or 1.0) if _scr else None
        self.dot = icons.PulseDot()
        self.time = QLabel("00:00")
        self.time.setObjectName("time")
        self.msg = QLabel(f"{self.fps}fps MP4 · 不含系统声音")
        self.rlbl = QLabel("全屏")
        self.rlbl.setObjectName("dim")
        self.region = None                       # 逻辑 QRect；None=整屏
        self.br = icons.tool_button("region", "框选录制范围（Esc 取消返回）", self.pick_region, dpr=dpr)
        self.bc = icons.tool_button(
            "cursor", "包含鼠标指针（mss 抓帧本身不含光标，开启后逐帧合成）",
            lambda: None, checkable=True, dpr=dpr)
        self.bc.setChecked(True)
        self.bc.toggled.connect(lambda on: self.bc.setToolTip(
            ("包含" if on else "不含") + "鼠标指针（mss 抓帧本身不含光标，开启后逐帧合成）"))
        self.bc.toggled.connect(lambda on: setattr(self, "_cursor_on", on))   # 线程侧镜像
        self.bh = icons.tool_button(
            "clickfx", "鼠标点击高亮（录制中点击处画扩散圆环：左键金黄/右键红）",
            lambda: None, checkable=True, dpr=dpr)
        self.bh.setChecked(True)
        self.bh.toggled.connect(lambda on: self.bh.setToolTip(
            ("点击高亮已开：" if on else "点击高亮已关：") + "录制中点击处画扩散圆环"))
        self.bh.toggled.connect(lambda on: setattr(self, "_clickfx_on", on))  # 线程侧镜像
        self.b1 = icons.tool_button("rec", "开始录制", self.start, color="#ef4444", dpr=dpr)
        self.bp = icons.tool_button("pause", "暂停录制（计时与写帧冻结，再点继续）",
                                    self.toggle_pause, color="#fbbf24", dpr=dpr)
        self.bp.hide()                         # 就绪态无录制可停：仅录制中显示
        self.bstop = icons.tool_button("save", "停止并保存到本地目录", lambda: self.stop(copy=False),
                                       color="#60a5fa", dpr=dpr)
        self.bstop.hide()                        # 就绪态无录制可停：仅录制中显示（同 GIF 语义）
        self.bok = icons.tool_button("confirm", "停止并复制视频到剪贴板 (Enter)", self._on_bok,
                                     color="#2ecc71", dpr=dpr)
        self.bok.hide()
        self.bfps = _FpsPickerButton(self)
        self.bfps.setObjectName("recFps")
        self.bfps.setToolTip("录制帧率：30、40、50 或 60 fps")
        self.bfps.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.bfps.setFixedSize(76, 30)
        self.bfps.setCursor(Qt.PointingHandCursor)
        self.bfps.setStyleSheet(
            "QToolButton#recFps{background:rgba(255,255,255,10);color:#dfe4ef;border:none;"
            "border-radius:6px;padding:0 10px 0 7px;font-size:12px;}"
            "QToolButton#recFps:hover{background:rgba(255,255,255,20);color:#ffffff;}"
            "QToolButton#recFps:pressed{background:rgba(59,130,246,80);color:#ffffff;}"
            "QToolButton#recFps:disabled{background:rgba(255,255,255,6);color:#8b93a7;}")
        self.fps_menu = QMenu(self.bfps)
        self.fps_menu.setStyleSheet(C.UI_MENU_QSS)
        self._fps_actions = {}
        for fps in FPS_OPTIONS:
            action = self.fps_menu.addAction(f"{fps} fps")
            action.setCheckable(True)
            action.setChecked(fps == self.fps)
            action.triggered.connect(lambda _=False, value=fps: self._set_fps(value))
            self._fps_actions[fps] = action
        self.bfps.clicked.connect(self._open_fps_menu)
        self._sync_fps_button()
        bx = icons.tool_button("close", "关闭 (Esc)", self.close, color="#f4645f", dpr=dpr)
        lay.addWidget(self.dot)
        lay.addWidget(self.time)
        lay.addWidget(self.msg)
        lay.addWidget(self.rlbl)
        sep()
        lay.addWidget(self.br)
        lay.addWidget(self.bc)
        lay.addWidget(self.bh)
        sep()
        lay.addWidget(self.bfps)
        lay.addWidget(self.b1)
        lay.addWidget(self.bp)      # ⏸ 暂停/继续：录制态出现
        lay.addWidget(self.bstop)   # ⬇ 停止并保存：录制态出现，纯落盘不复制
        lay.addWidget(bx)           # × 在前，对号收尾最右——与 GIF 条/长截图条一致
        lay.addWidget(self.bok)
        self.bc.toggled.connect(self._sync_msg)
        self.tick = QTimer(self)
        self.tick.setInterval(1000)
        self.tick.timeout.connect(self._tick)
        self._dpr = 1.0
        self._cursor_on = True             # 指针合成开关的线程侧镜像（线程内不碰 widget）
        self._clickfx_on = True            # 点击高亮开关的线程侧镜像
        self._dpr_icon = dpr               # 换暂停/继续图标用
        self._paused = False               # 暂停态：计时与写帧冻结
        self._paused_at = 0.0
        self._paused_total = 0.0
        self._capturing = False
        self._cap_thread = None
        self.sct = None
        self.mon = None
        self.writer = None
        self.started_at = 0
        self.path = None
        self._temporary_path = None
        self._copy_pending = False
        self._completed = False
        self.frames = 0
        self._cw = self._ch = 0                # 编码器尺寸（缩放选区时帧需归一）
        self._dim = None
        self._frame = None
        self.region_screen = None              # 框选所在屏（多屏：录制/边框跟随选区屏，不跟控制条屏）
        _sel, _scr = self._preset
        if _sel is not None:                   # R131 预选区直接生效（语义同 pick_region 落点）
            self.region = QRect(_sel)
            self.region_screen = _scr
            self.rlbl.setText(f"范围 {self.region.width()}×{self.region.height()}")
        self.adjustSize()
        self._place_toolbar()
        C.prepare_capture_window(self)
        self.show()
        if self.region:
            self._sync_live_frame()            # 就绪时显示与 GIF 相同的压暗层和可调边框。
        self.raise_()
        self.activateWindow()
        C.force_foreground(self)       # R121：热键后台触发时抢到键盘焦点，Esc 才能关条
        C.exclude_from_capture(self)       # 控制条不烙进视频（呼吸点/计时每帧都算新画面）
        self._right_cancel = C.CaptureRightCancel(self, self._cancel_like_escape, live=True)

    # ---------- 状态反馈 ----------
    def _on_bok(self):
        """对号停止并复制视频文件引用，临时载体留在内部缓存。"""
        self.stop(copy=True)

    def _place_toolbar(self):
        """有选区时贴选区，尺寸变化后重排；全屏模式停在打开时所在屏顶部。"""
        if getattr(self, "region", None):
            scr = self.region_screen or self._toolbar_screen or QGuiApplication.primaryScreen()
            if scr:
                C.place_toolbar_near_region(self, self.region, scr, gap=16, edge=6)
            return
        scr = getattr(self, "_toolbar_screen", None) or QGuiApplication.primaryScreen()
        if scr:
            g = scr.geometry()
            self.move(g.center().x() - self.width() // 2, g.top() + 16)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_toolbar()                   # 按钮/状态文字改变条宽时保持贴选区

    def pick_region(self):
        """框选录制范围（复用截图框选）；Esc 取消回到控制条。"""
        if self.writer or self._capturing or self._copy_pending:
            return                            # 录制期间固定屏幕；仍可拖动/缩放当前边框
        self.hide()
        self._close_live()                     # 重选前收起旧边框和压暗层，不遮挡新的截图框选。

        def done(result):
            close_all(self._pick_ov)              # 多屏组清理（跳过已删败者：炸了后续全断）
            self._pick_ov = None
            if result and result.get("pick"):
                self.region = QRect(result["sel"])
                self.region_screen = result.get("screen")   # 录制/边框跟随选区屏（多屏）
                self.rlbl.setText(f"范围 {self.region.width()}×{self.region.height()}")
                self.adjustSize()
                self._place_toolbar()
            self.show()
            self._sync_live_frame()           # 取消重选也恢复原选区；换屏时重建整套选区窗。
            self.raise_()
            self.activateWindow()
            C.force_foreground(self)          # R121：框选归来前台已让出，抢回焦点保 Esc

        def do():
            self._pick_ov = create_overlays(done, pick=True)   # 每屏一个先到先得（持引用防 GC）
        QTimer.singleShot(160, do)

    def _sync_msg(self):
        if self.writer:
            self.msg.setText("录制中 · " + ("选区" if self.region else "全屏") + " · "
                             + self.codec + f" · {self.fps}fps"
                             + (" · 指针" if self.bc.isChecked() else ""))

    def _sync_fps_button(self):
        self.bfps.setText(f"{self.fps} fps")
        for rate, action in self._fps_actions.items():
            action.setChecked(rate == self.fps)

    def _open_fps_menu(self):
        self.fps_menu.popup(self.bfps.mapToGlobal(QPoint(0, self.bfps.height())))

    def _set_fps(self, fps):
        if fps not in FPS_OPTIONS or self.writer or self._capturing:
            return
        self.fps = fps
        self._sync_fps_button()
        self.msg.setText(f"{self.fps}fps MP4 · 不含系统声音")
        try:
            settings = C.load_settings()
            if settings.get("rec_fps") != fps:
                settings["rec_fps"] = fps
                C.save_settings(settings)
        except Exception:
            pass

    def _stamp_cursor(self, bgr, ox=0, oy=0):
        """把鼠标指针 alpha 合成进帧。ox/oy=选区在整屏帧内的物理偏移。
        必须在选区缩放 resize 之前调用：指针随内容一起缩放，落点才不漂移。"""
        if self._cursor_on:                # 读镜像 bool：抓帧线程里不能碰 widget
            stamp_cursor(bgr, int(self.mon["left"]), int(self.mon["top"]), ox, oy)
        if self._clickfx_on:               # 点击高亮同纪律：先画环再缩放
            stamp_click_fx(bgr, int(self.mon["left"]), int(self.mon["top"]), ox, oy)

    def _elapsed(self):
        """录制有效时长：墙钟 - 暂停总时长（含进行中的暂停段）。标量读写，线程安全。"""
        t = time.time() - self.started_at - self._paused_total
        if self._paused:
            t -= time.time() - self._paused_at
        return max(0.0, t)

    def toggle_pause(self):
        """暂停/继续：冻结计时与写帧（选区边框保持），再点继续。仅录制中有效。"""
        if not self.writer or not self._capturing:
            return
        if not self._paused:
            self._paused = True
            self._paused_at = time.time()
            self.bp.setIcon(icons.icon("play", "#2ecc71", 20, self._dpr_icon))
            self.bp.setToolTip("继续录制")
            self.dot.set_active(False)
            self.msg.setText("已暂停 · 计时与写帧冻结")
        else:
            self._paused = False
            self._paused_total += time.time() - self._paused_at
            self.bp.setIcon(icons.icon("pause", "#fbbf24", 20, self._dpr_icon))
            self.bp.setToolTip("暂停录制（计时与写帧冻结，再点继续）")
            self.dot.set_active(True)
            self._sync_msg()

    def _crop_xy(self, fw, fh):
        """把逻辑 region 换算到帧内物理像素范围（DPR 用 start() 时的缓存值）。"""
        if not self.region:
            return 0, 0, fw, fh
        d = self._dpr or 1.0
        x0 = max(0, int(self.region.x() * d))
        y0 = max(0, int(self.region.y() * d))
        x1 = min(fw, int((self.region.x() + self.region.width()) * d))
        y1 = min(fh, int((self.region.y() + self.region.height()) * d))
        return x0, y0, x1, y1

    def fail(self, msg):
        self.msg.setText(msg)
        self.msg.setStyleSheet("color:#f4645f;font-size:12.5px;background:transparent;")
        self.dot.set_active(False)
        self.bstop.hide()
        self.bok.hide()
        self.bp.hide()
        self._paused = False
        self.bfps.setEnabled(True)
        self.b1.show()                           # 开始钮复位，可重开
        self.br.setEnabled(True)
        self.br.setToolTip("框选录制范围（Esc 取消返回）")
        self.on_error(msg)

    # ---------- 录制 ----------
    def start(self):
        missing = missing_deps()
        if missing:
            if missing == ["mf"]:
                self.fail("系统缺少 Media Foundation（Windows N 版请在 设置>应用>可选功能 装「媒体功能包」）")
            else:
                pkgs = [m for m in missing if m != "mf"]
                self.fail("缺少依赖 " + "、".join(pkgs) + " —— 请执行 pip install " + " ".join(pkgs))
            return
        scr = (self.region_screen if self.region else None) \
            or self.screen() or QGuiApplication.primaryScreen()   # 选区跟选区屏；全屏跟控制条屏
        g = scr.geometry()
        try:
            # Windows Qt 保留屏幕的原生桌面原点，仅尺寸及屏内坐标是逻辑像素。
            # 原点不能乘 DPR：例如 -2560@125% 仍是 -2560，而不是 -3200。
            dpr = scr.devicePixelRatio() or 1.0
            self._dpr = dpr                 # 抓帧线程只读缓存值，不碰 Qt 对象
            self._cursor_on = self.bc.isChecked()
            self.sct = mss.mss()
            self.mon = {"top": g.y(), "left": g.x(),
                        "width": int(g.width() * dpr), "height": int(g.height() * dpr)}
            shot = self.sct.grab(self.mon)
            raw = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
            fh, fw = raw.shape[:2]              # R120：MF 直吃 BGRA，免 cv2.cvtColor
            x0, y0, x1, y1 = self._crop_xy(fw, fh)
            cw, ch = max(16, (x1 - x0) & ~1), max(16, (y1 - y0) & ~1)   # H.264 宏块偶数约束
            base = C.clipboard_media_path("录屏", ".mp4")
            self.path = self._temporary_path = base
            try:
                self.writer = MfH264Writer(base, cw, ch, self.fps)
            except Exception as e:
                self.fail(f"无法创建 H.264 编码器：{e}")
                self._cleanup()
                return
            self.codec = "H.264"
            self._cw, self._ch = cw, ch
            _first = raw[y0:y1, x0:x1].copy()   # frombuffer 只读：拷出可写帧，指针/高亮 stamp 才能原地写
            if (_first.shape[1], _first.shape[0]) != (cw, ch):
                _first = _resize_bgra(_first, cw, ch)   # 选区非偶/极小：归一到编码器尺寸
            self._stamp_cursor(_first[..., :3], x0, y0)   # 3 通道视图贴指针（BGR 语义不变）
            self.writer.write_bgra(_first)     # 首帧
            self.frames = 1
            self.started_at = time.time()
            self._paused = False                 # 暂停态复位：重开录制不带上次暂停账
            self._paused_at = 0.0
            self._paused_total = 0.0
            if self.region:
                self._open_live()              # 选区录制：压暗层 + 可拖动/缩放边框
            self._start_capture()
            self.tick.start()
            self.b1.hide()                      # 录制态：开始钮让位给 ⏸/⬇/✓（GIF 式）
            self.bfps.setEnabled(False)         # 当前 MP4 固定按选定帧率编码
            self.bp.show()
            self.br.setEnabled(False)
            self.br.setToolTip("录制中可拖动或缩放边框；重新框选请先结束本次录制")
            self.bstop.show()
            self.bok.show()
            self.adjustSize()                        # 新按钮亮相，条宽自适应
            self.msg.setStyleSheet("")          # 清掉上次 fail 的红字样式
            self.msg.setText("录制中 · " + ("选区" if self.region else "全屏") + " · "
                             + self.codec + f" · {self.fps}fps"
                             + (" · 指针" if self.bc.isChecked() else ""))
            self.dot.set_active(True)
        except Exception as e:
            self.fail(f"录屏启动失败：{e}")
            self._cleanup()

    # ---------- 抓帧线程 ----------
    def _start_capture(self):
        """抓帧+编码挪到独立线程：原先 GUI 线程 QTimer 会被取帧/编码拖慢，
        实际只有 10~16fps；线程内按所选帧率节拍自转。"""
        self._capturing = True
        self._cap_thread = threading.Thread(target=self._cap_loop, daemon=True)
        self._cap_thread.start()

    def _stop_capture(self):
        self._capturing = False
        if self._cap_thread is not None:
            self._cap_thread.join(timeout=3.0)
            self._cap_thread = None

    def _cap_loop(self):
        period = 1.0 / self.fps
        next_t = time.perf_counter()
        try:
            sct = mss.mss()                 # DC 归属本线程，避免跨线程 GDI 隐患
        except Exception:
            sct = self.sct                  # 兜底：沿用主线程实例
        while self._capturing:
            while self._paused and self._capturing:   # 暂停：挂起抓帧/写帧，节拍跟着走（恢复不爆发补帧）
                next_t = time.perf_counter()
                time.sleep(0.05)
            if not self._capturing:
                break
            next_t += period
            try:
                poll_click()                          # 点击边沿检测：与本节拍同频
                shot = sct.grab(self.mon)
                raw = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(shot.height, shot.width, 4)
                x0, y0, x1, y1 = self._crop_xy(raw.shape[1], raw.shape[0])
                frame = raw[y0:y1, x0:x1].copy()  # BGRA 直裁+可写拷贝：MF 编码器原生输入，免 cvtColor；
                # frombuffer 只读，不拷则 stamp_cursor/stamp_click_fx 原地写静默失败（R121「录屏没指针」根因二）
                self._stamp_cursor(frame[..., :3], x0, y0)   # 先贴指针再缩放：落点/大小随内容一致
                if (frame.shape[1], frame.shape[0]) != (self._cw, self._ch):
                    frame = _resize_bgra(frame, self._cw, self._ch)   # 缩放选区后归一到编码器尺寸
                # 写入数对齐「有效录制时长×fps」（暂停段不计）：落后补帧、提前丢帧
                dup = int(self._elapsed() * self.fps) - self.frames
                if dup > 0:
                    dup = min(dup, int(self.fps * 2)) # 卡顿兑底：最多补 2 秒
                    for _ in range(dup):
                        self.writer.write_bgra(frame)
                    self.frames += dup
            except Exception:
                pass
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()          # 已落后：重置节拍，不越积越多
        try:
            if sct is not self.sct:
                sct.close()
        except Exception:
            pass

    # ---------- 录制中的选区实时框 ----------
    def _sync_live_frame(self):
        """选区就绪即显示 GIF 同款压暗层和可调边框；重选时跟随当前屏幕重建。"""
        self._close_live()
        if self.region:
            self._open_live()

    def _open_live(self):
        """就绪和录制共用选区窗口，开始录制时复用以保持显示连续。"""
        scr = (self.region_screen if self.region else None) \
            or self.screen() or QGuiApplication.primaryScreen()
        if self.region and not self._dim:
            self._dim = RegionDim(scr, lambda: self.region)
        if self.region and not self._frame:
            self._frame = RegionFrame(scr, self.region, self._on_region_move)
        self.raise_()
        self.activateWindow()

    def _close_live(self):
        for attr in ("_dim", "_frame"):
            w = getattr(self, attr, None)
            if w:
                w.close()
                w.deleteLater()
            setattr(self, attr, None)

    def _on_region_move(self, r):
        self.region = QRect(r)
        self.rlbl.setText(f"范围 {self.region.width()}×{self.region.height()}")
        if self._dim:
            self._dim.update()          # 压暗层按新选区重画（读 region 的 lambda 已就绪）
        self._place_toolbar()

    def _tick(self):
        s = int(self._elapsed())
        self.time.setText(f"{s // 60:02d}:{s % 60:02d}")

    def stop(self, copy=False):
        """对号只复制内部缓存的视频；下载才移动到指定保存目录。"""
        if self._completed:
            return
        self._stop_capture()
        self.tick.stop()
        self.dot.set_active(False)
        self._close_live()
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                self.msg.setText("视频生成失败，请重试")
                self.adjustSize()
                return
            self.writer = None
        if self.sct:
            self.sct.close()
            self.sct = None
        if self.path and os.path.isfile(self.path) and os.path.getsize(self.path) > 0:
            copied = bool(copy and copy_file_to_clipboard(self.path))
            if copy and not copied:
                self._copy_pending = True
                self.msg.setText("复制失败，请再点对号重试；视频已保留")
                self.adjustSize()
                return
            if not copy:
                try:
                    self.path = C.publish_media_file(self.path, videos_dir())
                except Exception:
                    self._copy_pending = True
                    self.msg.setText("保存失败，请检查目录后重试；视频已保留")
                    self.adjustSize()
                    return
            C.release_clipboard_media(self._temporary_path)
            self._copy_pending = False
            self._completed = True
            self.bstop.hide()
            self.bp.hide()
            self.bok.hide()
            self.close()
            self.on_done(self.path, copied)
        else:
            self.close()
            self.on_error("未生成视频文件")

    def _cleanup(self):
        self._stop_capture()
        self.tick.stop()
        self._close_live()
        if self.writer:
            try:
                self.writer.close()
            except Exception:
                pass
            self.writer = None
        if self.sct:
            self.sct.close()
            self.sct = None
        if not self._completed and self._temporary_path:
            try:
                os.remove(self._temporary_path)
            except OSError:
                pass
            C.release_clipboard_media(self._temporary_path)
            self._temporary_path = None
            self.path = None

    def _cancel_like_escape(self):
        self.close()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self._cancel_like_escape()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and (self.writer or self._copy_pending):
            if not ev.isAutoRepeat():
                self._on_bok()           # 回车＝对号，失败时重试缓存中的视频
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        self._cleanup()
        ev.accept()
