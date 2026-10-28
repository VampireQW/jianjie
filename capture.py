# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 截图：冻结屏幕 → 框选 → 标注（矩形/椭圆/箭头/画笔/荧光笔/文字/序号/马赛克/高光）→ 复制/保存/钉图。"""
import math
import os
import time

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage,
                           QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QRegion,
                           QTextCursor, QTextLayout, QTextOption)
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QSizePolicy, QSpacerItem, QTextEdit, QWidget, QVBoxLayout)

import common as C
import icons
from jj_variant import LITE          # R107 构建变体：轻量版裁 OCR/二维码/GIF 工具条钮

SEL_GREEN = "#22c55e"

TOOL_LIST = ["select", "rect", "ellipse", "arrow", "pen", "marker", "text", "num", "mosaic", "high"]
TOOL_NAMES = {"select": "选择/移动", "rect": "矩形", "ellipse": "椭圆", "arrow": "箭头", "pen": "画笔",
              "marker": "荧光笔", "text": "文字", "num": "序号", "mosaic": "马赛克", "high": "高光"}
# R131 截图态工具快捷键：进入标注态后单键切换工具（可在主面板「快捷键设置」卡自定义）。
# 默认避开 Ctrl+Z/Esc/Del/回车等既有键位；单字母无修饰。
SHOT_TOOL_ACTIONS = [(t, TOOL_NAMES[t]) for t in TOOL_LIST]
DEFAULT_SHOT_TOOL_KEYS = {"select": "V", "rect": "R", "ellipse": "O", "arrow": "A",
                          "pen": "P", "marker": "E", "mosaic": "M", "text": "T",
                          "num": "N", "high": "H"}
# R144 动作单键（用户点名「GIF、提取文字等那一排功能下面，仍然可以加上快捷键」）：标注态
# 无修饰单键直触发动作，键位避开工具键 V/R/O/A/P/E/M/T/N/H；undo 沿用 Ctrl+Z 组合键不占单键；
# 固定键不进自定义卡（工具键已可自定义，动作键再开一档卡片价值低、键面冲突管理变复杂）
# R152 尾加 keys=K：键盘钮（设置入口）也补单键，用户点名「整体统一」；叉/对贴 Esc/↩ 标签
# （Enter/Esc 键路早已存在，只补标签不新增键路）；K 与工具键冲突时工具优先（同 R144 语义）
DEFAULT_SHOT_ACT_KEYS = {"ocr": "X", "gif": "G", "rec": "C", "longshot": "L", "beautify": "F",
                         "save": "S", "pin": "B", "keys": "K"}
SHOT_ACT_METHODS = {"ocr": "do_ocr", "rec": "start_rec", "gif": "start_gif",
                    "longshot": "start_longshot", "beautify": "do_beautify", "save": "do_save", "pin": "do_pin",
                    "keys": "_open_keys_menu"}
SHOT_ACT_LITE_SKIP = ("ocr", "rec")     # R107/R108 轻量版裁 OCR/录屏钮，键位同步裁
# R102：聚光遮罩暗度循环档——空处单击逐级加深，满黑（255）后再点回到默认档（不关窗）
HIGH_DIM_STEPS = (148, 195, 230, 255)
HIGH_DIM_DEFAULT = HIGH_DIM_STEPS[0]
COLORS = ["#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#ffffff", "#111111"]


class ColorDot(QPushButton):
    """色块圆点：paintEvent 手绘抗锯齿圆+描边环（随 DPR 自适应）。
    样式表 border+radius 在 200% 缩放下描边落半像素，白环发糊粗糙，故弃用。"""

    def __init__(self, color, tip, cb):
        super().__init__()
        self._color = color
        self.setFixedSize(22, 22)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.clicked.connect(cb)

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        checked = self.isChecked()
        pen = QPen(QColor(255, 255, 255, 255 if (checked or self.underMouse()) else 165),
                   2.0 if checked else 1.2)
        p.setPen(pen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(QPointF(self.width() / 2, self.height() / 2), 8.0, 8.0)
        p.end()


class _SwatchBtn(QPushButton):
    """R131 浮窗色块：方形圆角（参考图样式），选中=2px 白描边，hover=1.2px 半透白。"""

    def __init__(self, color, tip, cb):
        super().__init__()
        self._color = color
        self.setFixedSize(22, 22)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.clicked.connect(cb)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        checked = self.isChecked()
        if checked or self.underMouse():
            p.setPen(QPen(QColor(255, 255, 255, 255 if checked else 120),
                          2.0 if checked else 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(1, 1, 20, 20), 5, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color))
        p.drawRoundedRect(QRectF(3.5, 3.5, 15, 15), 3, 3)
        p.end()


class _SizeDot(QPushButton):
    """R131 浮窗粗细档：实心圆点 6/9/12px 递增（参考图），选中=亮白，未选=灰。"""

    def __init__(self, size, dot_d, tip, cb):
        super().__init__()
        self._size = size
        self._d = dot_d
        self.setFixedSize(24, 22)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.clicked.connect(cb)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 235 if self.isChecked() else
                                          (170 if self.underMouse() else 110)))
        p.drawEllipse(QPointF(self.width() / 2, self.height() / 2), self._d / 2, self._d / 2)
        p.end()


class ToolPropsPop(QWidget):
    """R134 辐条重做：粗细/颜色 + 工具级选项（箭头样式/序号样式起始/聚光形态）统一收进
    贴工具钮下方的浮条——主工具条不再塞工具级选项（R100/R101 贴身钮范式退役）。
    底色必须 WA_StyledBackground：裸 QWidget 默认不画自身 QSS 背景，R134 实测辐条纯透明事故。"""

        # R141：选项钮描边退役（用户点名「边框看起来比较糊，要不把边框去掉」）——1px 半透蓝边
    # 在高分屏抗锯齿下发虚，与 R134「深色悬浮条无描边」规范同病；去边后底色 38→48 撑形态、
    # 字色微提 #a9c8ff（去边后文字是对比主体）、圆角 4→5/padding 补 8px
    _OPT_QSS = ("QPushButton{background:rgba(59,130,246,48);color:#a9c8ff;"
                "border:none;border-radius:5px;padding:0 8px;"
                "font-size:12px;font-weight:700;}"
                "QPushButton:hover{background:rgba(59,130,246,80);color:#dbeafe;}")

    def __init__(self, overlay):
        super().__init__(overlay)
        self.ov = overlay
        self.setAttribute(Qt.WA_StyledBackground, True)   # R134：裸 QWidget 画 QSS 底的前提
        self.setStyleSheet(
            "ToolPropsPop{background:rgba(22,24,32,246);border:none;border-radius:9px;}"
            "QFrame{background:rgba(255,255,255,30);border:none;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(2)
        # 粗细/颜色区（high 无颜色粗细概念，set_tool 整区隐藏）
        self._cs_widgets = []
        self._size_dots = []
        for size, dot, name in zip((2, 4, 7), (6, 9, 12), ("细", "中", "粗")):
            b = _SizeDot(size, dot, f"粗细：{name}", lambda _, s=size: self.ov.set_size(s))
            b.setChecked(size == self.ov.cur_size)
            lay.addWidget(b)
            self._size_dots.append((size, b))
            self._cs_widgets.append(b)
        # R143：间距项保留引用随区显隐（用户点名「high 浮窗左边有一个空白区域」——
        # addSpacing 是固定布局项不随 widget 隐藏，cs 区隐后 4 个间距+边距全成左边空白）
        self._cs_spacers = []
        self._opt_spacers = []

        def _sp(group):
            sp = QSpacerItem(4, 1, QSizePolicy.Fixed, QSizePolicy.Fixed)
            lay.addItem(sp)
            group.append(sp)
            return sp

        f = QFrame(self)
        f.setFixedSize(1, 18)
        _sp(self._cs_spacers)
        lay.addWidget(f)
        _sp(self._cs_spacers)
        self._cs_widgets.append(f)
        self._swatches = {}
        color_names = dict(zip(COLORS, ("红色", "橙色", "绿色", "蓝色", "白色", "黑色")))
        for c in COLORS:
            b = _SwatchBtn(c, color_names[c], lambda _, cc=c: self.ov.set_color(cc))
            b.setChecked(c == self.ov.cur_color)
            lay.addWidget(b)
            self._swatches[c] = b
            self._cs_widgets.append(b)
        # R134 工具级选项区（从主工具条迁入；按 set_tool 显隐）
        self._opt_sep = QFrame(self)
        self._opt_sep.setFixedSize(1, 18)
        _sp(self._opt_spacers)
        lay.addWidget(self._opt_sep)
        _sp(self._opt_spacers)

        def _opt_btn(tip, cb):
            b = QPushButton(self)
            b.setFixedHeight(22)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._OPT_QSS)
            b.clicked.connect(cb)
            lay.addWidget(b)
            return b

        self._btn_arrow_style = _opt_btn("箭头样式：线箭/实心锥形；选中箭头时点击＝改该对象",
                                         lambda: self.ov._cycle_arrow_style())
        self._btn_num_style = _opt_btn("序号样式：点击选择编号格式",
                                       lambda: self.ov._pick_num_style())
        self._btn_num_start = _opt_btn("序号起始：点击 +1（1~9 循环）",
                                       lambda: self.ov._cycle_num_start())
        self._btn_high_shape = _opt_btn("聚光形态：矩形/椭圆；选中聚光时点击＝改该对象",
                                        lambda: self.ov._cycle_high_shape())
        self._opt_widgets = {"arrow": (self._btn_arrow_style,),
                             "num": (self._btn_num_style, self._btn_num_start),
                             "high": (self._btn_high_shape,)}
        self.adjustSize()

    def set_tool(self, tool):
        """辐条内容随工具：粗细/颜色区对聚光整区隐藏；选项区只亮当前工具的选项。"""
        has_cs = tool != "high"
        for w in self._cs_widgets:
            w.setVisible(has_cs)
        opts = self._opt_widgets.get(tool, ())
        for t, btns in self._opt_widgets.items():
            for b in btns:
                b.setVisible(t == tool)
        show_sep = has_cs and bool(opts)
        self._opt_sep.setVisible(show_sep)
        # R143：固定间距项随区收放（QSpacerItem 无 setVisible，changeSize(0,0) 等效）
        for sp in self._cs_spacers:
            sp.changeSize(4 if has_cs else 0, 1, QSizePolicy.Fixed, QSizePolicy.Fixed)
        for sp in self._opt_spacers:
            sp.changeSize(4 if show_sep else 0, 1, QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.sync()
        self.layout().invalidate()
        self.adjustSize()

    def sync(self):
        for size, b in self._size_dots:
            b.setChecked(size == self.ov.cur_size)
        for c, b in self._swatches.items():
            b.setChecked(c == self.ov.cur_color)
        ov = self.ov
        self._btn_arrow_style.setText("锥" if getattr(ov, "cur_arrow_style", "line") == "solid" else "线")
        self._btn_num_style.setText(ov._num_label(0))
        self._btn_num_start.setText("起%d" % ov.cur_num_start)
        scr = getattr(ov, "screen_", None)
        try:
            dpr = scr.devicePixelRatio() or 1.0 if scr else None
        except Exception:
            dpr = None
        self._btn_high_shape.setIcon(icons.icon(
            "ellipse" if ov.cur_high_shape == "ellipse" else "rect", "#93c5fd", 16, dpr))
        self._btn_high_shape.setIconSize(QSize(16, 16))


def active_screen():
    """鼠标当前所在的屏（回退主屏）——拓展屏支持的统一入口。"""
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def grab_screen(scr=None):
    """抓取目标屏当前画面（冻结帧），保留物理分辨率。

    不再缩放到逻辑分辨率（否则缩放屏上冻结帧变糊，「分辨率感览变了」），
    逻辑/物理映射由 ShotOverlay 用实测像素比统一处理。
    DPR 元数据按实测宽度比重写——混合缩放多屏下元数据可能与内容不符，
    一旦不符就会出现「看到的选择区」和「导出图」对不上。"""
    scr = scr or QGuiApplication.primaryScreen()
    pm = scr.grabWindow(0)
    geo = scr.geometry()
    if geo.width() > 0 and pm.width() > 0:
        k = pm.width() / geo.width()
        if abs((pm.devicePixelRatio() or 1.0) - k) > 1e-6:
            pm.setDevicePixelRatio(k)
    return pm


def close_all(ovs):
    """多屏组清理：逐个静默关闭，跳过 C++ 已删对象。
    败者 close_silent 后 deleteLater 挂起，真机事件循环空闲即删 C++ 对象——
    之后任何再访问（isVisible/close_silent）都会 RuntimeError 把宿主槽函数炸断
    （R64：点 GIF 后 shot_finished 中断，接管者已关而 GifBar 未创建）。"""
    for ov in (ovs or []):
        try:
            if shiboken6.isValid(ov):
                ov.close_silent()
        except RuntimeError:
            pass


def snapshot_windows():
    """冻结前按 Z 序记录原生窗口及浏览器渲染区；悬停只查快照，不跨进程查询。"""
    if QGuiApplication.platformName() != "windows":
        return []
    import ctypes as ct
    from ctypes import wintypes as wt

    u, d, kernel = ct.windll.user32, ct.windll.dwmapi, ct.windll.kernel32
    callback = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    u.EnumWindows.argtypes = [callback, wt.LPARAM]
    u.EnumChildWindows.argtypes = [wt.HWND, callback, wt.LPARAM]
    u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ct.c_int]
    u.GetWindowRect.argtypes = [wt.HWND, ct.POINTER(wt.RECT)]
    u.GetClientRect.argtypes = [wt.HWND, ct.POINTER(wt.RECT)]
    u.ClientToScreen.argtypes = [wt.HWND, ct.POINTER(wt.POINT)]
    u.IsWindowVisible.argtypes = u.IsIconic.argtypes = [wt.HWND]
    u.GetWindowThreadProcessId.argtypes = [wt.HWND, ct.POINTER(wt.DWORD)]
    d.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ct.c_void_p, wt.DWORD]
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    u.SetThreadDpiAwarenessContext.argtypes = [ct.c_void_p]
    u.SetThreadDpiAwarenessContext.restype = ct.c_void_p
    browsers = {"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe",
                "chromium.exe", "360chrome.exe", "360se.exe", "qqbrowser.exe"}
    processes, found = {}, []
    own_panels = {}
    for window in QApplication.topLevelWidgets():
        try:
            if not window.isVisible() or not window.testAttribute(Qt.WA_TranslucentBackground):
                continue
            layout, handle = window.layout(), window.windowHandle()
            if layout is None or layout.count() != 1 or handle is None:
                continue
            body = layout.itemAt(0).widget()
            if (isinstance(body, QFrame) and body.isVisible()
                    and body.objectName() in {"cardBody", "pluginBody", "taskBody", "calBody", "nsBody"}):
                own_panels[int(handle.winId())] = (window, body)
        except RuntimeError:
            continue

    def class_name(hwnd):
        text = ct.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, text, len(text))
        return text.value

    def bounds(hwnd, frame=False):
        r = wt.RECT()
        if not (frame and d.DwmGetWindowAttribute(hwnd, 9, ct.byref(r), ct.sizeof(r)) == 0):
            if not u.GetWindowRect(hwnd, ct.byref(r)):
                return QRect()
        return QRect(r.left, r.top, r.right-r.left, r.bottom-r.top)

    def is_browser(hwnd):
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        if pid.value not in processes:
            handle = kernel.OpenProcess(0x1000, False, pid.value)
            name = ""
            if handle:
                try:
                    text, size = ct.create_unicode_buffer(32768), wt.DWORD(32768)
                    if kernel.QueryFullProcessImageNameW(handle, 0, text, ct.byref(size)):
                        name = os.path.basename(text.value).lower()
                finally:
                    kernel.CloseHandle(handle)
            processes[pid.value] = name in browsers
        return processes[pid.value]

    def panel_bounds(hwnd, outer):
        panel = own_panels.get(hwnd)
        if panel is None:
            return outer
        try:
            window, body = panel
            client, origin = wt.RECT(), wt.POINT()
            if (window.width() <= 0 or window.height() <= 0
                    or not u.GetClientRect(hwnd, ct.byref(client))
                    or not u.ClientToScreen(hwnd, ct.byref(origin))):
                return outer
            sx = (client.right - client.left) / window.width()
            sy = (client.bottom - client.top) / window.height()
            position = body.mapTo(window, QPoint(0, 0))
            left = origin.x + round(position.x() * sx)
            top = origin.y + round(position.y() * sy)
            right = origin.x + round((position.x() + body.width()) * sx)
            bottom = origin.y + round((position.y() + body.height()) * sy)
            area = QRect(left, top, right - left, bottom - top).intersected(outer)
            return area if area.width() >= 8 and area.height() >= 8 else outer
        except RuntimeError:
            return outer

    @callback
    def visit(hwnd, _):
        if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return True
        name = class_name(hwnd)
        if name in {"Progman", "WorkerW", "tooltips_class32"}:
            return True
        cloaked = wt.DWORD()
        if (d.DwmGetWindowAttribute(hwnd, 14, ct.byref(cloaked), ct.sizeof(cloaked)) == 0
                and cloaked.value):
            return True
        rect = panel_bounds(hwnd, bounds(hwnd, True))
        if rect.width() < 8 or rect.height() < 8:
            return True
        contents = []
        if is_browser(hwnd):
            @callback
            def child(ch, _):
                if u.IsWindowVisible(ch) and class_name(ch) == "Chrome_RenderWidgetHostHWND":
                    area = bounds(ch).intersected(rect)
                    if area.width() >= 80 and area.height() >= 60 and area != rect and area not in contents:
                        contents.append(area)
                return True
            u.EnumChildWindows(hwnd, child, 0)
        found.append({"hwnd": hwnd, "rect": rect, "contents": contents})
        return True

    previous = u.SetThreadDpiAwarenessContext(ct.c_void_p(-4))
    try:
        u.EnumWindows(visit, 0)
    finally:
        if previous:
            u.SetThreadDpiAwarenessContext(previous)
    return found


def snap_candidates(windows, geometry, scale, point):
    """原生桌面边界转屏内逻辑矩形；只缩放相对量，屏幕原点绝不乘 DPR。"""
    physical = QPoint(geometry.x() + round(point.x()*scale),
                      geometry.y() + round(point.y()*scale))
    screen_rect = QRect(0, 0, geometry.width(), geometry.height())

    def local(rect):
        left = round((rect.x()-geometry.x()) / scale)
        top = round((rect.y()-geometry.y()) / scale)
        right = round((rect.x()+rect.width()-geometry.x()) / scale)
        bottom = round((rect.y()+rect.height()-geometry.y()) / scale)
        return QRect(left, top, right-left, bottom-top).intersected(screen_rect)

    for window in windows:
        if window["rect"].contains(physical):
            outer = local(window["rect"])
            options = [(local(r), "网页内容") for r in window["contents"] if r.contains(physical)]
            options = [(r, label) for r, label in options if r != outer and r.width() >= 8 and r.height() >= 8]
            options.sort(key=lambda item: item[0].width()*item[0].height(), reverse=True)
            if outer.width() >= 8 and outer.height() >= 8:
                options.append((outer, "窗口"))
            return window["hwnd"], options
    return None, [(screen_rect, "屏幕")]


def freeze_screens():
    """热键到达时先冻结鼠标所在屏，避免短暂浮窗在覆盖层创建前消失。"""
    screens = QGuiApplication.screens()
    current = active_screen()
    ordered = ([current] if current in screens else []) + [s for s in screens if s is not current]
    frames = [(scr, grab_screen(scr)) for scr in ordered]
    return snapshot_windows(), frames


def create_overlays(on_finished, ocr=False, scan=False, pick=False, frozen=None):
    """多屏覆盖层组：每屏一个 ShotOverlay，各抓本屏冻结帧、各用本屏 DPR
    （混合缩放多屏下零画质损失——拼单一大位图只有一个 DPR，必损一屏）。
    先到先得：鼠标在哪屏按下框选，哪屏 claimed 接管，其余屏静默关闭；
    完成/Esc 由接管者（或焦点实例）emit finished 一次，宿主据此 close_silent 全组。"""
    ovs = []

    def _claim(ov):
        close_all([o for o in ovs if o is not ov])

    if frozen is None:
        windows = snapshot_windows()
        frames = [(scr, grab_screen(scr)) for scr in QGuiApplication.screens()]
    else:
        windows, frames = frozen
    try:
        for scr, frame in frames:
            ov = ShotOverlay(frame, ocr=ocr, scan=scan, pick=pick, screen=scr, snap_windows=windows)
            ov.claimed.connect(_claim)
            ov.finished.connect(on_finished)
            ovs.append(ov)
        current = active_screen()
        for ov in ovs:
            if ov.screen_ is current:
                ov._activate_selection()
                ov._update_snap(ov.mapFromGlobal(QCursor.pos()))
                break
        return ovs
    except Exception:
        close_all(ovs)
        raise


class AnnotationTextEdit(QTextEdit):
    """在 Qt 完成输入排版后同步视口，避免隐藏滚动条把首行滚走。"""

    def inputMethodEvent(self, ev):
        self._composing = bool(ev.preeditString())
        self._handling_input = True
        try:
            super().inputMethodEvent(ev)
        finally:
            self._handling_input = False
        self.parentWidget()._fit_anno_editor(self)

    def keyPressEvent(self, ev):
        self._handling_input = True
        try:
            super().keyPressEvent(ev)
        finally:
            self._handling_input = False
        self.parentWidget()._fit_anno_editor(self)

    def setPlainText(self, text):
        # R106：程序性预填（双击再编辑/预设文本）发生在浮层未 show、布局惰性期——
        # 打标记让 fit 强制同步排版；真实键盘/IME 输入不经本方法，天然不受影响
        self._need_relayout = True
        super().setPlainText(text)


class ShotOverlay(QWidget):
    """全屏覆盖层：先拖拽框选，再进入标注编辑。"""
    finished = Signal(object)   # QPixmap 裁剪结果（退出未完成时为 None）
    pinned = Signal(QPixmap, object, QPoint)    # 图像、来源屏幕、选区全局位置
    claimed = Signal(object)    # 多屏先到先得：本实例首次左键框选即接管（携带自身）

    def __init__(self, base_pixmap, ocr=False, scan=False, screen=None, pick=False, snap_windows=()):
        super().__init__(None)
        C.snapshot_input_locale()   # R83：抢前台前记下用户正在用的输入法语言
        self.base = base_pixmap
        self.ocr = ocr
        self.scan = scan
        self.pick = pick            # 纯框选模式（长截图/录屏范围），不进标注
        self.setFocusPolicy(Qt.StrongFocus)
        self.screen_ = screen or QGuiApplication.primaryScreen()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)  # 整屏底图一次画满，不先清成空白
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)  # 底图上屏前不夺走原浮窗焦点
        self._selection_cursor = C.shot_selection_cursor()
        self.setCursor(self._selection_cursor)
        geo = self.screen_.geometry()
        self.setScreen(self.screen_)
        self.setGeometry(geo)
        # 实测像素比：物理帧宽 ÷ 逻辑屏宽。不信任 DPR 元数据，否则导出与所见错位
        self.k = self.base.width() / max(1, geo.width())
        self.base.setDevicePixelRatio(self.k)
        # 标注层用物理分辨率存储（挂 k 后 QPainter 自动用逻辑坐标绘制），笔画不损失精度
        self.anno = QPixmap(round(geo.width() * self.k), round(geo.height() * self.k))
        self.anno.setDevicePixelRatio(self.k)
        self.anno.fill(Qt.transparent)
        self.phase = "select"
        self.sel = None          # QRect（逻辑坐标）
        self.drag_start = None
        self._snap_windows = snap_windows
        self._snap_rect = None
        self._snap_pos = QPoint()
        self._snap_options = []
        self._snap_owner = None
        self._snap_index = 0
        self._snap_label = ""
        self._select_dragging = False
        self.cur_tool = "rect"
        self.cur_color = "#ef4444"
        self.cur_size = 2                  # 默认「细」：必须取工具条粗细枚举 (2,4,7) 之一，否则无选中项
        # R100 标注属性（会话级默认值；与 cur_color/cur_size 同纪律不持久化）：
        self.cur_num_style = "num"         # 序号样式：num/upper/lower/uroman/lroman
        self.cur_num_start = 1             # 序号起始（1~9）
        
        self.cur_high_shape = "rect"       # 聚光形态：rect/ellipse
        self.cur_arrow_style = "solid"    # R160：截图新箭头默认微信式实心锥形；仍可切回经典线箭
        self.cur_high_dim = HIGH_DIM_DEFAULT   # R102：聚光遮罩暗度（空处单击循环加深）
        self._nums = []          # 序号对象列表：{pt,text,color,r}；标号=顺序+1，删除后自动重排
        self._drag_num = None    # 正在拖动的序号下标；_drag_moved=首次实际移动才压撤销
        self._drag_moved = False
        self._drag_off = QPoint(0, 0)
        self.stamps = []         # 形状/文字/马赛克对象：所见即对象，统一选中/移动/缩放/删除
        self.sel_stamp = None    # 当前选中标注：("num", i) | ("shape", i) | None
        self._drag_shape = None  # 正在拖动的形状下标（_drag_moved 与序号共用：首次实际移动才压撤销）
        self._shape_orig = None  # 拖动前的形状快照（每个 move 事件据此重建，避免原地变异）
        self._drag_pos0 = None
        self._rs = None          # 选中标注的缩放会话：{tag, handle, ob, orig}
        self._drag_sel = None    # 选区框条拖动锚点：按住框条＝整体移动选区（手柄仍是缩放）
        self.setMouseTracking(True)   # 悬停光标反馈：框条可抓/手柄可缩放/对象可拖
        self.text_editor = None  # 文字工具 WYSIWYG 编辑器（透明多行浮层，回车换行/失焦落字）
        self._text_edit_idx = None  # 再编辑中的文字对象下标：浮层接管期间底层不画（R79 防重影）
        self._num_edit_idx = None     # R104：再编辑中的序号下标（附言让位防重影；圆标保留）
        self.undo_stack = []
        self.stroke_pts = None
        self._mosa_cache = None  # 马赛克增量缓存：(id(stroke_pts), 已算点数, tiles, seen)
        self.resize_handle = None
        self.resize_base = None
        self.text_edit = None
        self.base_img = self.base.toImage()   # 物理像素（马赛克取样用）
        self._color_pos = self.mapFromGlobal(QCursor.pos())
        self._color_copied_until = 0.0
        self._color_copied_hex = ""
        self._color_track = QTimer(self)
        self._color_track.setInterval(20)
        self._color_track.timeout.connect(self._poll_selection_color)
        self._color_track.start()
        self._right_cancel = C.CaptureRightCancel(self, self._cancel_like_escape)
        self.toolbar = None
        C.prepare_capture_window(self)
        self.showFullScreen()     # 屏幕已指定，直接全屏显示，避免两次显示造成空白闪帧
        self._update_snap(self.mapFromGlobal(QCursor.pos()))
        self.repaint()            # 同步提交冻结帧，之后才抢焦点让用户框选
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self._activate_selection()

    def _activate_selection(self):
        self.activateWindow()
        if QGuiApplication.platformName() == "windows":
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(int(self.winId())))
        self.setFocus()

    def _update_snap(self, pos, cycle=False):
        self._snap_pos = QPoint(pos)
        if not self.rect().contains(pos):
            self._snap_rect = None
            self.update()
            return
        owner, options = snap_candidates(self._snap_windows, self.screen_.geometry(), self.k, pos)
        if owner != self._snap_owner or options != self._snap_options:
            self._snap_index = 0
        self._snap_owner, self._snap_options = owner, options
        if cycle and options:
            self._snap_index = (self._snap_index + 1) % len(options)
        self._snap_rect, self._snap_label = options[self._snap_index] if options else (None, "")
        self.update()

    def _select_move(self, pos):
        delta = pos - self.drag_start
        threshold = max(4, QGuiApplication.styleHints().startDragDistance())
        if max(abs(delta.x()), abs(delta.y())) >= threshold:
            self._select_dragging = True
        if self._select_dragging:
            self.sel = QRect(self.drag_start, pos).normalized().intersected(self.rect())

    def enterEvent(self, ev):
        super().enterEvent(ev)
        if self.phase == "select" and self.drag_start is None:
            self._activate_selection()
            self._update_snap(ev.position().toPoint())

    def leaveEvent(self, ev):
        if self.phase == "select" and self.drag_start is None:
            self._snap_rect = None
            self.update()
        super().leaveEvent(ev)

    def event(self, ev):
        if (ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Tab
                and getattr(self, "phase", None) == "select"):
            self.keyPressEvent(ev)
            return True
        return super().event(ev)

    # ---------- 绘制 ----------
    def _poll_selection_color(self):
        if self.phase != "select":
            self._color_track.stop()
            return
        pos = self.mapFromGlobal(QCursor.pos())
        if pos != self._color_pos:
            self._color_pos = pos
            if self.rect().contains(pos):
                if self.drag_start is None:
                    self._update_snap(pos)
                self.update()

    def _selection_color(self, pos):
        x = max(0, min(self.base_img.width() - 1, round(pos.x() * self.k)))
        y = max(0, min(self.base_img.height() - 1, round(pos.y() * self.k)))
        return self.base_img.pixelColor(x, y)

    def _draw_selection_color(self, p):
        pos = self._color_pos
        if not self.rect().contains(pos) or self.base_img.isNull():
            return
        copied = self._color_copied_hex if time.monotonic() < self._color_copied_until else ""
        _ColorPickerCard.draw(
            p, self.base_img, self.k, pos,
            _ColorPickerCard.rect(pos, self.width(), self.height()),
            hint="Ctrl+C 复制 · 右键 / Esc 取消", copied_hex=copied)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)   # 矢量层（序号圆标/选区框/角标）抗锯齿
        p.drawPixmap(self.rect(), self.base)      # QRect 版本：DPR 感知，物理像素 1:1 上屏
        preview = self.phase == "select" and not self._select_dragging
        selection = self.sel if self.sel is not None else (self._snap_rect if preview else None)
        if selection:
            p.setPen(Qt.NoPen)
            p.fillRect(QRect(0, 0, self.width(), self.height()), QColor(6, 8, 16, 135))
            p.save()
            p.setClipRect(selection)
            p.drawPixmap(self.rect(), self.base)
            if self.phase == "edit":
                p.drawPixmap(self.rect(), self.anno)
                if self._nums:
                    self._paint_nums(p)    # 序号层对象化现画：可删除、删除后自动重排
                    if self.cur_tool == "num":
                        self._paint_num_del(p)   # × 删除手柄只上屏，不进导出
            p.restore()
            p.setPen(QPen(QColor(SEL_GREEN), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRect(selection)
            self._draw_badge(p, selection, self._snap_label if preview else "")
            if self.phase == "edit":
                self._draw_handles(p)
                # R71：可缩放标注（矩形/椭圆/箭头/笔画/马赛克/高亮）画选中框+手柄；
                # 文字/序号不画（用户定为不可缩放类）——拖动/删除/双击重编辑不受影响
                if self.sel_stamp and self._sel_scalable():
                    self._draw_stamp_sel(p)
        elif self.phase == "select":
            p.setPen(QColor(255, 255, 255, 190))
            p.setFont(QFont("Microsoft YaHei UI", 12))
            hint = "  单击选窗 · 拖动框选 · Esc 取消"
            if self.pick:
                hint = "  拖拽框选范围 · 回车=整屏 · Esc 取消"
            elif self.ocr:
                hint = "  拖拽框选要识别文字的区域，Esc 取消"
            elif self.scan:
                hint = "  拖拽框选包含二维码的区域，Esc 取消"
            p.drawText(self.rect(), Qt.AlignHCenter | Qt.AlignTop, hint)
        if self.phase == "select":
            self._draw_selection_color(p)
        p.end()

    def _draw_badge(self, p, selection=None, label=""):
        """微信风格尺寸徽标：选区上方左对齐的暗色胶囊。

        显示物理像素尺寸（与导出文件的真实尺寸一致，同微信行为），
        否则缩放屏上徽标数字和保存的图对不上，看起来「非所见即所得」。"""
        selection = self.sel if selection is None else selection
        txt = f"{round(selection.width() * self.k)} × {round(selection.height() * self.k)}"
        if label:
            txt = f"{label}  {txt} · 单击选择 / 拖动框选"
            if len(self._snap_options) > 1:
                txt += " · Tab 切换"
        p.setFont(QFont("Microsoft YaHei UI", 9, QFont.DemiBold))
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(txt) + 14, fm.height() + 6
        w = min(w, self.width() - 8)
        x = max(4, min(selection.left(), self.width() - w - 4))
        y = selection.top() - h - 6
        if y < 2:
            y = selection.top() + 6
        y = max(2, min(y, self.height() - h - 2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 30, 235))
        p.drawRoundedRect(QRect(x, y, w, h), 5, 5)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRect(x, y, w, h), Qt.AlignCenter, txt)

    def _handle_pts(self, r):
        """8 向手柄点位（选区与选中标注共用同一套手柄几何）。"""
        mx = (r.left() + r.right()) // 2
        my = (r.top() + r.bottom()) // 2
        return {"tl": QPoint(r.left(), r.top()), "tm": QPoint(mx, r.top()),
                "tr": QPoint(r.right(), r.top()),
                "ml": QPoint(r.left(), my), "mr": QPoint(r.right(), my),
                "bl": QPoint(r.left(), r.bottom()), "bm": QPoint(mx, r.bottom()),
                "br": QPoint(r.right(), r.bottom())}

    def _handle_rects(self):
        return self._handle_pts(self.sel)

    def _draw_handles(self, p):
        p.setPen(QPen(QColor(SEL_GREEN), 1.2))
        p.setBrush(QColor("#ffffff"))
        for pt in self._handle_rects().values():
            p.drawRect(QRect(pt.x() - 3, pt.y() - 3, 6, 6))

    _SEL_CURSORS = {"tl": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                    "bl": Qt.SizeBDiagCursor, "br": Qt.SizeFDiagCursor,
                    "tm": Qt.SizeVerCursor, "bm": Qt.SizeVerCursor,
                    "ml": Qt.SizeHorCursor, "mr": Qt.SizeHorCursor}
    
    def _on_sel_frame(self, pos):
        """pos 是否在选区框条上（边沿 ±4px，手柄由 _hit_handle 先行拦截）：
        框条＝可按住拖动整体移动选区，光标用移动态提示可抓。"""
        if not self.sel:
            return False
        outer = self.sel.adjusted(-4, -4, 4, 4)
        inner = self.sel.adjusted(4, 4, -4, -4)
        return outer.contains(pos) and not inner.contains(pos)
    
    def _hit_handle(self, pos):
        for name, pt in self._handle_rects().items():
            if abs(pos.x() - pt.x()) <= 6 and abs(pos.y() - pt.y()) <= 6:
                return name
        return None

    # ---------- 鼠标 ----------
    def mousePressEvent(self, ev):
        pos = ev.position().toPoint()
        if ev.button() == Qt.RightButton:
            self._cancel_like_escape()
            ev.accept()
            return
        if self.phase == "select":
            if ev.button() != Qt.LeftButton:
                return
            self._update_snap(pos)
            self.claimed.emit(self)         # 多屏先到先得：本屏按下即接管，其余屏覆盖层静默关闭
            self.drag_start = pos
            self._select_dragging = False
            self.sel = QRect(self._snap_rect) if self._snap_rect is not None else QRect(pos, pos)
            self.update()
            return
        if self.phase != "edit":
            return
        if ev.button() != Qt.LeftButton:
            return
        had_edit = self.text_editor is not None or self.text_edit is not None
        self.commit_text_editor()          # 文字浮层：点任意处＝落字（失焦即提交）
        self.commit_text_input()           # 序号附言：点任意处＝落字（失焦即提交）
        if had_edit:
            return                         # 失焦那一下只落字：同一击不再继续，再点一下才行
        h = self._hit_handle(pos)
        if h:
            self.resize_handle = h
            self.resize_base = QRect(self.sel)
            self.setCursor(Qt.SizeAllCursor)   # R72：按住拖拽＝十字移动（用户否决握拳「小手」）
            return
        if self._on_sel_frame(pos):        # 按在选区框条上＝抓取整体拖动（手柄已先行拦截）
            self._drag_sel = pos
            self.setCursor(Qt.SizeAllCursor)
            return
        if self.cur_tool == "num":
            dx = self._hit_num_del(pos)
            if dx is not None:             # 点 × 手柄＝删除（先于选中手柄：两者相距仅几像素）
                self.push_undo()
                del self._nums[dx]
                self.sel_stamp = None
                self.update()
                return
        h = self._hit_stamp_handle(pos)    # 选中标注的缩放手柄（箭头仅 a/b 两端点）
        if h is not None:
            self._rs = self._begin_stamp_resize(h)
            self.setCursor(Qt.SizeAllCursor)
            return
        if self.cur_tool == "num":
            hit = self._hit_num(pos)
            if hit is not None:            # 按在已有圆标上＝选中拖动（圆标+附言整体跟手）
                self._select_and_drag("num", hit, pos)
                return
            hit = self._hit_shape(pos)     # R99：按在文字/形状上＝拖动它（此前误当空白又落新标，
            if hit is not None:            # 用户报「标完序号按住文本拖不动，还老冒新序号」）
                self._select_and_drag("shape", hit, pos)
                return
            self.sel_stamp = None          # 落新标＝旧选中失焦（同 select 点空白）
            self.update()
            self.show_num_input(pos)
            return
        if self.cur_tool == "select":
            hit = self._hit_num(pos)
            if hit is not None:
                self._select_and_drag("num", hit, pos)
                return
            hit = self._hit_shape(pos)
            if hit is not None:
                self._select_and_drag("shape", hit, pos)
                return
            self.sel_stamp = None          # 选择工具点空白＝取消选中
            self.update()
            return
        if self.cur_tool == "text":
            hit = self._hit_text(pos)
            if hit is not None:            # 文字工具点到已有文字＝选中拖动
                self._select_and_drag("shape", hit, pos)
                return
            hit = self._hit_num(pos)       # R99：按在圆标/形状上＝拖动它（与 num 工具对称：
            if hit is not None:            # 对象优先于落新笔，空白行为才随工具）
                self._select_and_drag("num", hit, pos)
                return
            hit = self._hit_shape(pos)
            if hit is not None:
                self._select_and_drag("shape", hit, pos)
                return
            self.sel_stamp = None          # 开新文字浮层＝旧选中失焦
            self.update()
            self.show_text_editor(pos)
            return
        if self.cur_tool in ("rect", "ellipse", "arrow", "high"):
            hit = self._hit_shape(pos)
            if hit is not None:            # 形状工具按在已有形状边框上＝选中拖动
                self._select_and_drag("shape", hit, pos)
                return
        self.sel_stamp = None              # 起笔画新元素＝旧选中失焦（rect/ellipse/arrow/pen/mosaic 共用落点）
        self.update()
        self.push_undo()
        self.stroke_pts = [pos]
        self._mosa_cache = None             # 新笔画：增量缓存重置
        self._preview_live()

    def _select_and_drag(self, tag, i, pos):
        """选中标注并进入拖动：序号走 _drag_num 老机制，形状走快照重建。"""
        self.setCursor(Qt.SizeAllCursor)   # 按住拖拽＝十字移动（序号/形状/文字/高亮框共用此入口）
        self.sel_stamp = (tag, i)
        self._drag_moved = False
        self._drag_shape = None
        if tag == "num":
            self._drag_num = i
            n = self._nums[i]
            self._drag_off = QPoint(pos.x() - n["pt"].x(), pos.y() - n["pt"].y())
        else:
            self._drag_num = None
            self._drag_shape = i
            self._shape_orig = self._clone_entry(self.stamps[i])
            self._drag_pos0 = QPoint(pos)
        self.update()

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint()
        self._color_pos = pos
        self._hover_pt = pos               # R78：记录悬浮点，Backspace/Delete 悬浮即删用
        if self._drag_sel is not None:     # 框条拖动：选区整体平移（标注留在屏图原位）
            self.sel.translate(pos - self._drag_sel)
            self._drag_sel = pos
            self.place_toolbar()
            self.update()
            return
        if self._drag_num is not None:
            if not self._drag_moved:
                self.push_undo()           # 首次实际移动才记快照：只点击不拖不占撤销步
                self._drag_moved = True
            n = self._nums[self._drag_num]
            n["pt"] = QPoint(pos.x() - self._drag_off.x(), pos.y() - self._drag_off.y())
            self.update()
            return
        if self._drag_shape is not None:
            if not self._drag_moved:
                self.push_undo()           # 首次实际移动才记快照：只点击不拖不占撤销步
                self._drag_moved = True
            self.stamps[self._drag_shape] = self._move_shape(
                self._shape_orig, pos - self._drag_pos0)   # 每次从快照重建，不原地累积
            self._rerender_anno()
            self.update()
            return
        if self._rs is not None:
            self._apply_stamp_resize(pos)  # 选中标注的 8 向缩放
            self.update()
            return
        if self.resize_handle:
            r = QRect(self.resize_base)
            if "l" in self.resize_handle:
                r.setLeft(pos.x())
            if "r" in self.resize_handle:
                r.setRight(pos.x())
            if "t" in self.resize_handle:
                r.setTop(pos.y())
            if "b" in self.resize_handle:
                r.setBottom(pos.y())
            r = r.normalized()
            if r.width() >= 12 and r.height() >= 12:
                self.sel = r
                self.place_toolbar()
            self.update()
            return
        if self.phase == "select":
            if self.drag_start is not None:
                self._select_move(pos)
                self.update()
            else:
                self._update_snap(pos)
        elif self.phase == "edit" and self.stroke_pts:
            self.stroke_pts.append(pos)
            self._preview_live()           # 进行中笔画/形状：全量重画预览
            self.update()
        if (self.phase == "edit" and self._drag_num is None and self._drag_shape is None
                and self._rs is None and not self.resize_handle and not self.stroke_pts
                and self._drag_sel is None):
            h = self._hit_handle(pos)
            if h:
                self.setCursor(self._SEL_CURSORS[h])   # 选区 8 向手柄：方向缩放光标
            elif self._on_sel_frame(pos):
                self.setCursor(Qt.SizeAllCursor)       # 选区框条：可抓取拖动整体
            else:
                sh = self._hit_stamp_handle(pos)       # 选中标注缩放手柄：全工具生效（同 press 顺序）
                if sh:
                    # 8 向手柄＝方向缩放光标；箭头 a/b 端点无方向 → 十字
                    self.setCursor(self._SEL_CURSORS.get(sh, Qt.SizeAllCursor))
                elif self.cur_tool == "num":
                    if self._hit_num_del(pos) is not None:
                        self.setCursor(Qt.PointingHandCursor)      # × 删除钮：手型点击
                    else:
                        # R99：圆标与文字/形状都可拖＝十字（与 press 可拖范围一一对应）
                        on = self._hit_num(pos) is not None or self._hit_shape(pos) is not None
                        self.setCursor(Qt.SizeAllCursor if on else self._tool_idle_cursor())
                else:
                    # R72 主诉修复：hover 可拖动对象＝十字移动，全工具生效（此前只在 select/high 工具
                    # 给反馈，用户常态在绘制工具下 hover 已画元素毫无反应）。可拖范围与 press 一一对应：
                    if self.cur_tool == "select":
                        on = self._hit_num(pos) is not None or self._hit_shape(pos) is not None
                    elif self.cur_tool == "text":
                        # R99：文字/圆标/形状都可拖（与 press 泛化同步）
                        on = (self._hit_text(pos) is not None or self._hit_num(pos) is not None
                              or self._hit_shape(pos) is not None)
                    elif self.cur_tool in ("rect", "ellipse", "arrow", "high"):
                        on = self._hit_shape(pos) is not None
                    else:
                        on = False                     # pen/mosaic：按已有元素＝起新笔，不给移动态
                    self.setCursor(Qt.SizeAllCursor if on else self._tool_idle_cursor())  # R84

    def mouseReleaseEvent(self, ev):
        self.setCursor(self._selection_cursor if self.phase == "select" else self._tool_idle_cursor())
        # 拖拽结束恢复（R84：edit 阶段随工具）；悬停光标由后续 move 按位重设（select 阶段保持框选十字）
        if self._drag_sel is not None:
            self._drag_sel = None          # 框条拖动结束
            return
        if self._drag_num is not None:
            self._drag_num = None          # 拖动结束
            return
        if self._drag_shape is not None:
            self._drag_shape = None
            self._shape_orig = None
            return
        if self._rs is not None:
            self._rs = None
            return
        if self.resize_handle:
            self.resize_handle = None
            return
        if self.phase == "select" and self.drag_start is not None:
            if ev.button() != Qt.LeftButton:
                return
            self._select_move(ev.position().toPoint())
            self.drag_start = None
            self._select_dragging = False
            self._snap_rect = None
            if self.sel and self.sel.width() >= 8 and self.sel.height() >= 8:
                if self.pick:
                    self.finished.emit({"pixmap": None, "pick": True, "sel": QRect(self.sel),
                                        "screen": self.screen_, "ocr": False, "scan": False,
                                        "path": None, "error": None})
                    self._really_close()
                    return
                self.phase = "edit"
                self.setCursor(Qt.ArrowCursor)
                self.push_undo()
                self.show_toolbar()
            else:
                self.sel = None
            self.update()
        elif self.phase == "edit":
            if self.stroke_pts:
                self._commit_live()        # 松手＝把进行中的笔画/形状落成对象
            self.stroke_pts = None

    def mouseDoubleClickEvent(self, ev):
        pos = ev.position().toPoint()
        if self.phase == "edit" and self.sel and self.sel.contains(pos):
            hit = self._hit_text(pos)
            if hit is not None:
                self._reedit_text(hit)     # 双击文字＝原地再编辑
                return
            hit = self._hit_num(pos)       # R104：双击序号（圆标或附言）＝附言原地再编辑
            if hit is not None:
                self._reedit_num(hit)
                return
            if self.cur_tool == "high":
                return                     # R102：聚光工具连击＝暗度循环，不触发复制关窗
            self.do_copy()

    def _cancel_like_escape(self):
        if self.text_editor:
            self.cancel_text_editor()
        else:
            self.close_cancel()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Tab and self.phase == "select":
            if self.drag_start is None:
                self._update_snap(self._snap_pos, cycle=True)
        elif ev.key() == Qt.Key_Escape:
            self._cancel_like_escape()
        elif (ev.key() == Qt.Key_C and ev.modifiers() == Qt.ControlModifier
              and self.phase == "select"):
            pos = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(pos) and not self.base_img.isNull():
                self._color_copied_hex = self._selection_color(pos).name().upper()
                QGuiApplication.clipboard().setText(self._color_copied_hex)
                self._color_pos = pos
                self._color_copied_until = time.monotonic() + 1.4
                self.update()
                QTimer.singleShot(1450, self.update)
            ev.accept()
        elif ev.key() == Qt.Key_Z and ev.modifiers() & Qt.ControlModifier:
            self.undo()
        elif ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.phase == "edit":
            if self.sel_stamp:
                self._delete_selected()    # 选中标注 Del/退格＝删除
            else:
                self._delete_hover()       # R78：未选中时删鼠标正悬浮的元素（免先点选）
        elif ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.phase == "select" and self.pick:
            self.sel = QRect(0, 0, self.width(), self.height())      # 回车=整屏范围
            self.finished.emit({"pixmap": None, "pick": True, "sel": QRect(self.sel),
                                "screen": self.screen_, "ocr": False, "scan": False,
                                "path": None, "error": None})
            self._really_close()
        elif (ev.key() in (Qt.Key_Return, Qt.Key_Enter) and self.phase == "edit"
              and self.sel and not self.text_editor and not self.text_edit):
            self.do_copy()                 # 文字已失焦落定后，回车＝复制到剪贴板
        elif (self.phase == "edit" and not self.text_editor and not self.text_edit
              and not ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            # R131 工具快捷键：单键切工具（键位可自定义，见主面板快捷键设置卡）
            _t = getattr(self, "_tool_keys_rev", {}).get(
                QKeySequence(ev.key()).toString().upper())
            if _t:
                self.set_tool(_t)
            else:
                # R144 动作单键：那一排功能（提取/录屏/GIF/长截图/保存/钉图）直触发
                _a = getattr(self, "_act_keys_rev", {}).get(
                    QKeySequence(ev.key()).toString().upper())
                if _a:
                    getattr(self, SHOT_ACT_METHODS.get(_a, ""), lambda: None)()

    def _refresh_tool_key_tips(self):
        """R131：把自定义键位写进工具钮提示（名字 · 键位），与自定义卡保持同一份；
        R138：同步图标下方键位标签文本；tooltip 基于 _base_tip 快照重建，改键后重调不累积。"""
        for t, b in getattr(self, "tool_btns", {}).items():
            for name, tool in self._tool_keys_rev.items():
                if tool == t:
                    b.setToolTip(getattr(b, "_base_tip", b.toolTip()) + f"（{name}）")
                    lb = getattr(self, "_key_tips", {}).get(t)
                    if lb is not None:
                        lb.setText(name)
                    break

    def _key_tip_for(self, b, text):
        """R138/R144 键位标签统一构建：8px 弱白小字贴钮底部居中，精致不生硬；透明不挡点击。
        R144 抽成方法——动作钮（那一排功能）也贴同款标签。"""
        lb = QLabel(b)
        lb.setObjectName("keyTip")
        lb.setAttribute(Qt.WA_TransparentForMouseEvents, True)   # 不挡钮点击
        lb.setAlignment(Qt.AlignCenter)
        lb.setGeometry(0, 25, 34, 10)
        lb.setStyleSheet("color:rgba(255,255,255,120);font-size:8px;background:transparent;")
        lb.setText(text)
        return lb
    
    def _tb_qss(self):
        """R147：工具条 QSS 按键位标签开关分档——开=底 padding 9px 抬图标留标签位；
        关=图标回中、工具条收窄（用户点名「不显示快捷键，工具条应该适当收窄」）。"""
        pad = 9 if getattr(self, "_key_tips_on", True) else 0
        return ("QWidget{background:rgba(22,24,32,246);border:none;border-radius:10px;}"   # R134：白色描边退役
                f"QPushButton#tbBtn{{background:transparent;border:none;border-radius:7px;padding:0 0 {pad}px 0;}}"
                "QPushButton#tbBtn:hover{background:rgba(255,255,255,30);}"
                "QPushButton#tbBtn:checked{background:rgba(59,130,246,120);}"
                "QFrame{background:rgba(255,255,255,30);border:none;}")

    def _apply_key_tips(self):
        """R138：键位标签显隐跟随开关（settings["shot_key_tips"]，默认开）；
        R147：关标签工具条即时收窄——钮高 36↔30、padding 9↔0、重排+重定位。"""
        on = getattr(self, "_key_tips_on", True)
        for lb in getattr(self, "_key_tips", {}).values():
            lb.setVisible(on)
        tb = getattr(self, "toolbar", None)
        if tb is not None:
            tb.setStyleSheet(self._tb_qss())
            for b in tb.findChildren(QPushButton):
                if b.objectName() == "tbBtn":
                    b.setFixedSize(34, 36 if on else 30)
            tb.adjustSize()
            self.place_toolbar()

    def _set_key_tips(self, on):
        """R138：键位标签开关——写 settings 并即时显隐（下次截图随工具条构建读默认）。"""
        self._key_tips_on = bool(on)
        s = C.load_settings()
        s["shot_key_tips"] = self._key_tips_on
        C.save_settings(s)
        self._apply_key_tips()

    def _open_keys_menu(self):
        """R138 工具条设置入口菜单：键位标签开关（checkable）+ 工具键自定义卡。"""
        m = QMenu(self)
        m.setStyleSheet(C.UI_MENU_QSS)
        act = m.addAction("显示键位标签")
        act.setCheckable(True)
        act.setChecked(getattr(self, "_key_tips_on", True))
        act.toggled.connect(self._set_key_tips)
        m.addSeparator()
        m.addAction("自定义工具键…").triggered.connect(self._edit_tool_keys)
        m.exec(self.btn_keys.mapToGlobal(self.btn_keys.rect().bottomLeft()))

    def _edit_tool_keys(self):
        """R138：工具键自定义卡——HotkeyDialog 仅工具组（空第一组；hk_parse 仅作用于第一组组合键，
        此处无调用面，传 None Lambda 安全）；保存当次会话即时生效（反查表+tooltip+键位标签三路刷新）。"""
        import widgets as _W
        tk = dict(DEFAULT_SHOT_TOOL_KEYS)
        tk.update(C.load_settings().get("shot_tool_keys", {}))
        d = _W.HotkeyDialog([], {}, lambda _c: None, on_top=True,
                            extra={"title": "截图工具键 · 标注态单键切换工具（仅截图时生效）",
                                   "actions": SHOT_TOOL_ACTIONS, "cur": tk})
        if d.exec() != QDialog.Accepted:
            return
        s = C.load_settings()
        s["shot_tool_keys"] = dict(d.cur2)
        C.save_settings(s)
        _nk = dict(DEFAULT_SHOT_TOOL_KEYS)
        _nk.update(d.cur2)
        self._tool_keys_rev = {v.upper(): t for t, v in _nk.items() if v}
        self._refresh_tool_key_tips()

    # ---------- 选区手柄与工具条定位 ----------
    def place_toolbar(self):
        if not self.toolbar or not self.sel:
            return
        tb = self.toolbar
        x = self.sel.right() + 1 - tb.width()  # QRect.right() 含末像素，工具条右缘对齐需 +1
        y = self.sel.bottom() + 10
        if y + tb.height() > self.height() - 4:
            y = self.sel.top() - tb.height() - 10
        x = max(6, min(x, self.width() - tb.width() - 6))
        y = max(6, y)
        tb.move(x, y)
        self._place_props_pop()                      # R131 工具条移位则浮窗跟移

    # R134：辐条服务的工具集（select/mosaic 无属性不弹；high 弹形态选项、无粗细/颜色区）
    _PROPS_TOOLS = ("rect", "ellipse", "arrow", "pen", "marker", "text", "num", "high")

    def _place_props_pop(self):
        """浮窗贴当前工具钮正下方（x 对钮中心钳屏）；工具条贴选区上方时浮窗仍向下展开。"""
        pop = getattr(self, "props_pop", None)
        if not pop or not pop.isVisible() or not self.toolbar:
            return
        btn = self.tool_btns.get(self.cur_tool)
        if btn:
            cx = btn.mapTo(self, QPoint(btn.width() // 2, 0)).x()
        else:
            cx = self.toolbar.x() + self.toolbar.width() // 2
        x = max(6, min(cx - pop.width() // 2, self.width() - pop.width() - 6))
        pop.move(x, self.toolbar.y() + self.toolbar.height() + 6)

    def _sync_props_pop(self):
        """按当前工具显隐浮窗（标注工具激活即弹出，非标注工具即收）。"""
        pop = getattr(self, "props_pop", None)
        if not pop:
            return
        if self.cur_tool in self._PROPS_TOOLS and self.phase == "edit" and self.toolbar:
            pop.set_tool(self.cur_tool)            # R134：辐条内容随工具（选项区+粗细/颜色区显隐，内部含 sync）
            pop.show()
            self._place_props_pop()
        else:
            pop.hide()

    # ---------- 标注 ----------
    def _clone_entry(self, s):
        """条目浅克隆：QPoint/QRect/列表全部换新，快照与本体彻底隔离。"""
        s = dict(s)
        for k in ("pt", "a", "b"):
            if k in s:
                s[k] = QPoint(s[k])
        if "pts" in s:
            s["pts"] = [QPoint(p) for p in s["pts"]]
        if "tiles" in s:
            s["tiles"] = list(s["tiles"])
        if isinstance(s.get("r"), QRect):
            s["r"] = QRect(s["r"])
        return s

    def push_undo(self):
        if len(self.undo_stack) > 18:
            self.undo_stack.pop(0)
        # 快照三件套：anno 位图 + 序号对象 + 形状对象（条目浅拷字典，内部容器整体换新）
        self.undo_stack.append((QPixmap(self.anno), [dict(n) for n in self._nums],
                                [self._clone_entry(s) for s in self.stamps]))

    def undo(self):
        if self.text_editor:
            self.commit_text_editor()      # 先落字再撤：刚落的字会被这次撤销回退
        if self.text_edit:
            if getattr(self.text_edit, "_num", False):
                self.cancel_text_edit()
                return                     # 序号输入中撤销＝取消落标
            self.commit_text_input()   # 先提交再撤，撤销栈才一致
        if len(self.undo_stack) > 1:
            a, nums, stamps = self.undo_stack[-1]      # 先回退到上一动作前，再弹出该快照
            self.anno = QPixmap(a)
            self._nums = [dict(n) for n in nums]
            self.stamps = [self._clone_entry(s) for s in stamps]
            self.sel_stamp = None
            self.undo_stack.pop()              # （先弹后恢会一次回退两个动作）
            self.update()

    # ---------- 标注引擎：全部对象化，整层重画（选中/移动/缩放/撤销共用） ----------
    def _rerender_anno(self, live=None):
        """透明底 → 全部标注对象 → 进行中预览。荧光笔等透明笔天然幂等不累积。"""
        self.anno = QPixmap(self.anno.size())
        self.anno.fill(Qt.transparent)     # 新 QPixmap 是未初始化内存，必须清透明（否则花屏）
        self.anno.setDevicePixelRatio(self.k)
        p = QPainter(self.anno)
        p.setRenderHint(QPainter.Antialiasing)
        for _i, s in enumerate(self.stamps):
            if _i == self._text_edit_idx and s["kind"] == "text":
                continue                     # 再编辑中：浮层在画，底层这帧让位（R79 防重影）
            if s["kind"] == "high":
                continue                     # R121：聚光不逐对象画——并集压暗+边框统一在最后
            self._paint_shape(p, s)
        self._paint_high_dim(p, self.stamps, live)   # R121：全部聚光区域并集一次压暗（统一黑度）
        for s in self.stamps:
            if s["kind"] == "high":
                self._draw_high_border(p, s["r"], s.get("shape", "rect"))
        if live is not None:
            self._paint_live(p, live)
        p.end()
        self.update()

    def draw_stroke(self, pts):
        """整条笔画预览（鼠标路径每次全量重画，防透明笔跨事件累积）。"""
        self._rerender_anno(live=("stroke", [QPoint(p) for p in pts]))

    def redraw_shape(self, s, e):
        """单形状预览（保留旧接口）。"""
        self._rerender_anno(live=(self.cur_tool, QPoint(s), QPoint(e)))

    def _preview_live(self):
        self._rerender_anno(live=self._live_shape())

    def _live_shape(self):
        if not self.stroke_pts:
            return None
        t = self.cur_tool
        if t in ("pen", "marker"):
            return ("stroke", self.stroke_pts)
        if t == "mosaic":
            return ("mosaic", self._mosaic_tiles(self.stroke_pts))
        if t == "high":
            r = QRect(self.stroke_pts[0], self.stroke_pts[-1]).normalized()
            if r.width() < 8 and r.height() < 8:
                return None                  # R102：近点单击无预览（防橙点小点+整屏压暗一闪）
        return (t, self.stroke_pts[0], self.stroke_pts[-1])

    def _commit_live(self):
        """松手：把进行中的笔画/形状落成标注对象（所见即对象）。"""
        s = self._live_stamp()
        if s is not None:
            self.stamps.append(s)
            self._rerender_anno()
        elif self.cur_tool == "high":
            self._cycle_high_dim()           # R102：近点单击＝聚光遮罩暗度循环加深
        self.update()

    def _cycle_high_dim(self):
        """R102：空处单击把聚光遮罩暗度推进一档（满黑后回到默认档，不关窗）。
        作用于全部已存在聚光对象并同步后续默认档。
        不再 push_undo：截图侧按下起笔时已记快照（一次点击＝一步撤销）。"""
        steps = HIGH_DIM_STEPS
        nxt = steps[(steps.index(self.cur_high_dim) + 1) % len(steps)] \
            if self.cur_high_dim in steps else steps[0]
        self.cur_high_dim = nxt
        if any(s["kind"] == "high" for s in self.stamps):
            for i, s in enumerate(self.stamps):
                if s["kind"] == "high":
                    s = self._clone_entry(s)
                    s["dim"] = nxt
                    self.stamps[i] = s
            self._rerender_anno()

    def _live_stamp(self):
        if not self.stroke_pts:
            return None
        t = self.cur_tool
        if t in ("pen", "marker"):
            if len(self.stroke_pts) < 2:
                return None
            return {"kind": "stroke", "pts": [QPoint(p) for p in self.stroke_pts],
                    "color": self.cur_color,
                    "w": self.cur_size * 6 if t == "marker" else self.cur_size,
                    "op": 0.35 if t == "marker" else None}
        if t == "mosaic":
            tiles = self._mosaic_tiles(self.stroke_pts)
            return {"kind": "mosaic", "tiles": tiles, "bs": self._mosaic_bs()} if tiles else None
        r = QRect(self.stroke_pts[0], self.stroke_pts[-1]).normalized()
        if r.isNull():
            return None
        if t == "arrow":
            if self.stroke_pts[0] == self.stroke_pts[-1]:
                return None               # 原地单击不成箭头
            # 箭头对象必须带 a/b 端点：渲染/包围盒/命中全靠它，缺了即 KeyError 崩溃
            return {"kind": "arrow", "a": QPoint(self.stroke_pts[0]),
                    "b": QPoint(self.stroke_pts[-1]), "r": QRect(r),
                    "color": self.cur_color, "w": self.cur_size + 1,
                    "style": self.cur_arrow_style}              # R132 样式随对象
        if t == "high":
            # R102：近点单击不落对象（小点痕迹），松手转暗度循环（见 _commit_live）
            if r.width() < 8 and r.height() < 8:
                return None
            return {"kind": "high", "r": QRect(r), "shape": self.cur_high_shape,
                    "dim": self.cur_high_dim}   # R100 形态；R102 暗度随当前默认档
        return {"kind": t, "r": QRect(r), "color": self.cur_color, "w": self.cur_size + 1}

    def _paint_live(self, p, live):
        tag = live[0]
        if tag == "stroke":
            self._paint_shape(p, {"kind": "stroke", "pts": live[1],
                                  "color": self.cur_color,
                                  "w": self.cur_size * 6 if self.cur_tool == "marker" else self.cur_size,
                                  "op": 0.35 if self.cur_tool == "marker" else None})
        elif tag == "mosaic":
            bs = self._mosaic_bs()
            p.setPen(Qt.NoPen)
            for tx, ty, col in live[1]:
                p.setBrush(QColor(col))
                p.drawRect(tx, ty, bs, bs)
        elif tag in ("rect", "ellipse", "arrow", "high"):
            p.setPen(QPen(QColor(self.cur_color), self.cur_size + 1))
            p.setBrush(Qt.NoBrush)
            if tag == "rect":
                p.drawRect(QRect(live[1], live[2]).normalized())
            elif tag == "ellipse":
                p.drawEllipse(QRect(live[1], live[2]).normalized())
            elif tag == "arrow":
                if self.cur_arrow_style == "solid":              # R132 实心锥形
                    self._arrow_solid(p, live[1], live[2], self.cur_size + 1)
                else:
                    self._arrow(p, live[1], live[2], self.cur_size + 1, None)
            else:
                # R121：拖拽预览只画边框——压暗洞已由 _paint_high_dim(live) 并入并集
                self._draw_high_border(p, QRect(live[1], live[2]).normalized(), self.cur_high_shape)

    @staticmethod
    def _smooth_path(pts):
        """Catmull-Rom → 三次贝塞尔：把鼠标采样折线拟成平滑曲线（快速画圆无折线拐点）。
        曲线精确穿过每个采样点；共线点退化后仍严格在线上，
        荧光笔“整条一次 drawPath 不累积透明”的前提不变。"""
        path = QPainterPath(QPointF(pts[0]))
        n = len(pts)
        if n == 2:
            path.lineTo(QPointF(pts[1]))
            return path
        for i in range(n - 1):
            p0 = QPointF(pts[i - 1]) if i > 0 else QPointF(pts[0])
            p1, p2 = QPointF(pts[i]), QPointF(pts[i + 1])
            p3 = QPointF(pts[i + 2]) if i + 2 < n else QPointF(pts[-1])
            path.cubicTo(p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2)
        return path

    def _paint_shape(self, p, s):
        k = s["kind"]
        if k == "stroke":
            # 荧光笔透明两个前提：①整条路径一次 drawPath——逐段 drawLine 圆头大面积
            # 重叠会 0.35^N 段累积成实色（用户实测踩坑，探针实证）；②对象化整层重画，
            # 每次都从透明底起笔，天然幂等。
            # 丝滑：Catmull-Rom 曲线拟合替代折线连接，实时预览与成品同走本分支。
            pts = s["pts"]
            p.setPen(QPen(QColor(s["color"]), s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            op = s.get("op")
            if op:
                p.setOpacity(op)               # 荧光效果：底层内容仍可读
            if len(pts) > 1:
                p.drawPath(self._smooth_path(pts))
            if op:
                p.setOpacity(1.0)
        elif k == "rect":
            p.setPen(QPen(QColor(s["color"]), s["w"]))
            p.setBrush(Qt.NoBrush)
            p.drawRect(s["r"])
        elif k == "ellipse":
            p.setPen(QPen(QColor(s["color"]), s["w"]))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(s["r"])
        elif k == "arrow":
            p.setPen(QPen(QColor(s["color"]), s["w"]))
            if s.get("style", "line") == "solid":      # R132 实心锥形（老对象无 style 键=经典线箭）
                self._arrow_solid(p, s["a"], s["b"], s["w"], s.get("c"))   # R142：锥形接上弯势（m 柄拖出的 c）
            else:
                p.setBrush(Qt.NoBrush)
                self._arrow(p, s["a"], s["b"], s["w"], s.get("c"))
        elif k == "high":
            # R121：压暗由 _paint_high_dim 并集统一渲染，对象层只画边框
            self._draw_high_border(p, s["r"], s.get("shape", "rect"))
        elif k == "text":
            p.setFont(QFont("Microsoft YaHei UI", s["fs"], QFont.Bold))
            p.setPen(QColor(s["color"]))
            y = s["r"].top() + s["asc"]
            for ln in s["lines"]:
                p.drawText(QPoint(s["r"].left(), y), ln)
                y += s["lh"]
        elif k == "mosaic":
            bs = s["bs"]
            p.setPen(Qt.NoPen)
            for tx, ty, col in s["tiles"]:
                p.setBrush(QColor(col))
                p.drawRect(tx, ty, bs, bs)

    def _high_dim_rect(self):
        """压暗覆盖范围（逻辑坐标）：截图侧＝全屏覆盖层；长图侧覆写为整图。"""
        return QRect(0, 0, self.width(), self.height())

    def _fill_high_dim(self, p, regs, dim):
        """R121：全部聚光区域并集挖洞，一次性压暗（统一黑度，不逐对象叠加变暗）。
        区域重叠必须用 QPainterPath.subtracted 真减法——OddEvenFill 下重叠区穿越次数为偶
        会被填回压暗（两区重叠处反而变黑的坑）。"""
        if not regs:
            return
        full = QPainterPath()
        full.addRect(QRectF(self._high_dim_rect()))
        for r, shape in regs:
            hole = QPainterPath()
            if shape == "ellipse":
                hole.addEllipse(QRectF(r))
            else:
                hole.addRect(QRectF(r))
            full = full.subtracted(hole)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 12, 20, dim))
        p.drawPath(full)
        p.setBrush(Qt.NoBrush)

    def _paint_high_dim(self, p, stamps, live=None):
        """R121：聚光统一渲染入口——全部 high 对象并集一层压暗，黑度取最新一枚的档
        （R102 循环本来全量同步，撤销分裂时以画面最后一枚为准＝所见即撤销）；
        拖拽中的实时选区同步挖洞（live 截图侧为元组、长图侧为对象字典）。
        只有点黑色区域（一切区域外）才循环暗度，区域永远可见不被互盖。"""
        regs, dim = [], None
        for s in stamps:
            if s["kind"] == "high":
                regs.append((s["r"], s.get("shape", "rect")))
                dim = s.get("dim", HIGH_DIM_DEFAULT)
        if live is not None:
            if isinstance(live, dict):
                if live.get("kind") == "high":
                    regs.append((live["r"], live.get("shape", "rect")))
            elif live[0] == "high":
                r = QRect(live[1], live[2]).normalized()
                if r.width() >= 8 or r.height() >= 8:   # R102：近点单击无预览
                    regs.append((r, self.cur_high_shape))
        if not regs:
            return
        self._fill_high_dim(p, regs, self.cur_high_dim if dim is None else dim)

    def _draw_high_border(self, p, r, shape="rect"):
        """聚光橙色边框（在压暗层之上画，保持鲜亮不被压暗）。"""
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#f59e0b"), 4))
        if shape == "ellipse":
            p.drawEllipse(r)
        else:
            p.drawRect(r)

    def _draw_highlight(self, p, r, shape="rect", dim=HIGH_DIM_DEFAULT):
        """单区域压暗+边框（冒烟锁直驱的兼容原语；对象渲染路径不走这里——
        多区域统一渲染走 _paint_high_dim + _draw_high_border）。
        R100：椭圆形态。R102：dim＝遮罩暗度档。"""
        self._fill_high_dim(p, [(r, shape)], dim)
        self._draw_high_border(p, r, shape)

    def _arrow(self, p, s, e, w, c=None):
        """主线停在三角头的底边，弯线按弧长截断；箭尖始终只由三角形形成。"""
        sx, sy, ex, ey = s.x(), s.y(), e.x(), e.y()
        if c is None:
            length = math.hypot(ex - sx, ey - sy)
            if length < 1e-9:
                return
            head_len = min(max(10.0, w * 3.5), length * 0.42)
            ux, uy = (ex - sx) / length, (ey - sy) / length
            bx, by = ex - ux * head_len, ey - uy * head_len
            shaft = QPainterPath(QPointF(s))
            shaft.lineTo(QPointF(bx, by))
        else:
            cx, cy = c.x(), c.y()
            samples = 64
            curve = [(0.0, sx, sy)]
            distances = [0.0]
            for i in range(1, samples + 1):
                t = i / samples
                mt = 1.0 - t
                x = mt * mt * sx + 2.0 * mt * t * cx + t * t * ex
                y = mt * mt * sy + 2.0 * mt * t * cy + t * t * ey
                distances.append(distances[-1] + math.hypot(x - curve[-1][1], y - curve[-1][2]))
                curve.append((t, x, y))
            length = distances[-1]
            if length < 1e-9:
                return
            head_len = min(max(10.0, w * 3.5), length * 0.42)
            target = length - head_len
            for i in range(1, samples + 1):
                if distances[i] >= target:
                    span = distances[i] - distances[i - 1]
                    blend = 0.0 if span < 1e-9 else (target - distances[i - 1]) / span
                    t = (i - 1 + blend) / samples
                    break
            mt = 1.0 - t
            bx = mt * mt * sx + 2.0 * mt * t * cx + t * t * ex
            by = mt * mt * sy + 2.0 * mt * t * cy + t * t * ey
            shaft = QPainterPath(QPointF(s))
            shaft.quadTo(QPointF(sx + (cx - sx) * t, sy + (cy - sy) * t), QPointF(bx, by))
            head_len = math.hypot(ex - bx, ey - by)
            if head_len < 1e-9:
                return
            ux, uy = (ex - bx) / head_len, (ey - by) / head_len
        half_width = head_len / 2.5
        nx, ny = -uy, ux
        p1 = QPointF(bx + nx * half_width, by + ny * half_width)
        p2 = QPointF(bx - nx * half_width, by - ny * half_width)
        col = QColor(p.pen().color())
        p.save()
        shaft_pen = QPen(p.pen())
        # 短箭头同步缩细箭杆，避免线宽盖住三角头的两侧肩角。
        shaft_pen.setWidthF(min(shaft_pen.widthF(), 2.0 * half_width * 0.42))
        shaft_pen.setCapStyle(Qt.FlatCap)
        p.setPen(shaft_pen)
        p.drawPath(shaft)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon([QPointF(e), p1, p2])
        p.restore()

    def _arrow_solid(self, p, s, e, w, c=None):
        """箭身平顺接入三角箭头；短箭保留窄颈，弯曲中心线按真实弧长展开。"""
        sx, sy, ex, ey = s.x(), s.y(), e.x(), e.y()
        cx, cy = ((c.x(), c.y()) if c is not None
                  else ((sx + ex) / 2.0, (sy + ey) / 2.0))
        rough_len = math.hypot(cx - sx, cy - sy) + math.hypot(ex - cx, ey - cy)
        if rough_len < 2:
            return

        n = max(48, min(512, math.ceil(rough_len / 1.5)))
        curve, dist = [], [0.0]
        for i in range(n + 1):
            t = i / n
            mt = 1.0 - t
            x = mt * mt * sx + 2.0 * mt * t * cx + t * t * ex
            y = mt * mt * sy + 2.0 * mt * t * cy + t * t * ey
            curve.append((t, x, y))
            if i:
                dist.append(dist[-1] + math.hypot(x - curve[-2][1], y - curve[-2][2]))
        length = dist[-1]
        if length < 2:
            return

        # 微信参考：定尺三角头约 2.5:1，底边外角直接收进较窄箭杆；短箭头才缩小。
        head_base_h = min(max(1.0, w * 5.5), length * 0.14)
        # R272：箭杆最多占头部宽度的 42%，短箭缩小时仍有清晰肩角；
        # 常规长箭的原有比例约为 2 / 5.5，无需改变。
        shaft_h = min(head_base_h * 0.42, max(0.5, w * 2.0))
        head_len = min(max(6.0, w * 13.5), length * 0.42)
        head_base_f = 1.0 - head_len / length
        head_base_dist = head_base_f * length
        # 只把弯曲中心线平顺对齐箭头轴线，箭杆宽度不扩到三角头的两侧翼角。
        transition_len = min(max(2.0, w * 3.0), head_base_dist * 0.35)
        transition_f = head_base_f - transition_len / length
        end_dx, end_dy = ex - cx, ey - cy
        end_len = math.hypot(end_dx, end_dy)
        if end_len < 1e-9:
            end_dx, end_dy = ex - sx, ey - sy
            end_len = math.hypot(end_dx, end_dy) or 1.0
        end_ux, end_uy = end_dx / end_len, end_dy / end_len
        transition_dist = transition_f * length

        def _param_at(distance):
            # 长度浮点运算可能令 length * n / n 比 length 大 1 ULP，
            # 二分因此落到 lo == n，拖动中点时会访问 dist[n + 1] 并让 Qt 事件异常退出。
            distance = min(length, max(0.0, distance))
            if distance <= 0.0:
                return 0.0
            if distance >= length:
                return 1.0
            lo, hi = 0, n
            while lo < hi:
                mid = (lo + hi) // 2
                if dist[mid + 1] < distance:
                    lo = mid + 1
                else:
                    hi = mid
            lo = min(lo, n - 1)
            span = dist[lo + 1] - dist[lo]
            r = 0.0 if span <= 0 else min(1.0, max(0.0, (distance - dist[lo]) / span))
            return curve[lo][0] + (curve[lo + 1][0] - curve[lo][0]) * r

        # 等弧长取样保证弯箭身平滑；根部保留窄颈，两侧外角独立构成锐角三角头。
        distances = {length * (i / n) for i in range(n + 1)
                     if length * (i / n) < head_base_dist}
        distances.update((transition_f * length, head_base_dist))
        left, right = [], []
        for distance in sorted(distances):
            f = distance / length
            t = _param_at(distance)
            mt = 1.0 - t
            x = mt * mt * sx + 2.0 * mt * t * cx + t * t * ex
            y = mt * mt * sy + 2.0 * mt * t * cy + t * t * ey
            tx = 2.0 * ((1.0 - t) * (cx - sx) + t * (ex - cx))
            ty = 2.0 * ((1.0 - t) * (cy - sy) + t * (ey - cy))
            if distance > transition_dist:
                # 只在连接短段内顺滑对齐至末端切线，保证三角头等腰且弯箭头方向正确。
                u = min(1.0, max(0.0, (distance - transition_dist) / max(transition_len, 1e-9)))
                eased = u * u * (3.0 - 2.0 * u)
                target_x = ex - end_ux * (length - distance)
                target_y = ey - end_uy * (length - distance)
                x += (target_x - x) * eased
                y += (target_y - y) * eased
                tangent_len = math.hypot(tx, ty) or 1.0
                local_ux, local_uy = tx / tangent_len, ty / tangent_len
                tx = local_ux * (1.0 - eased) + end_ux * eased
                ty = local_uy * (1.0 - eased) + end_uy * eased
            if distance >= head_base_dist - 1e-8:
                # 最后横截面垂直于三角头的轴线，保证箭身和等腰三角底边无错位。
                tx, ty = ex - x, ey - y
            tl = math.hypot(tx, ty)
            if tl < 1e-9:
                tx, ty = ex - sx, ey - sy
                tl = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / tl, tx / tl
            u = min(1.0, max(0.0, f / max(transition_f, 1e-9)))
            h = shaft_h * u
            left.append(QPointF(x + nx * h, y + ny * h))
            right.append(QPointF(x - nx * h, y - ny * h))

        base_x, base_y = ex - end_ux * head_len, ey - end_uy * head_len
        head_left = QPointF(base_x - end_uy * head_base_h, base_y + end_ux * head_base_h)
        head_right = QPointF(base_x + end_uy * head_base_h, base_y - end_ux * head_base_h)
        col = QColor(p.pen().color())
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(left + [head_left, QPointF(ex, ey), head_right] + list(reversed(right)))
        p.restore()

    def num_radius(self):
        return 12 + self.cur_size

    _NUM_STYLES = ("num", "upper", "lower", "uroman", "lroman")   # R100：序号样式五档

    @staticmethod
    def _roman(v):
        """1~3999 → 罗马数字（序号标号用，超出回退阿拉伯）。"""
        if not 0 < v < 4000:
            return str(v)
        vals = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
        out = []
        for n, sym in vals:
            while v >= n:
                out.append(sym)
                v -= n
        return "".join(out)

    @staticmethod
    def _alpha(v):
        """1..26→A..Z，27→AA（Excel 式列名，序号标号用）。"""
        out = []
        while v > 0:
            v, r = divmod(v - 1, 26)
            out.append(chr(65 + r))
        return "".join(reversed(out)) or "A"

    def _num_label(self, i):
        """R100：标号＝起始+i，按当前样式格式化（1,2,3 / A,B,C / a,b,c / I,II,III / i,ii,iii）。"""
        v = max(1, self.cur_num_start) + i
        st = self.cur_num_style
        if st == "upper":
            return self._alpha(v)
        if st == "lower":
            return self._alpha(v).lower()
        if st == "uroman":
            return self._roman(v)
        if st == "lroman":
            return self._roman(v).lower()
        return str(v)

    def _draw_one_num(self, p, pos, tpos, label, text, color, r):
        """单个序号：圆标+白字标号；text 非空时按提交时记录的编辑器原位（tpos）画附言：
        基线＝顶边+ascent、逐行 +lineSpacing，与浮层逐像素对位＝真所见即所得；
        圆标白描边衬边（遮锯齿、从底图剥出）；附言纯填充随圆标色（同文字工具），不加描边。"""
        f = QFont("Microsoft YaHei UI", r - 2 if len(label) <= 2 else max(7, r - 1 - len(label) * 2),
                  QFont.Bold)   # R100：罗马/双字母标号自适应缩小，不溢出圆标
        p.setFont(f)
        p.setPen(QPen(QColor("#ffffff"), 2))   # 白描边衬边：遮锯齿，圆标从杂色底图上剥出来
        p.setBrush(QColor(color))
        p.drawEllipse(pos, r, r)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRect(pos.x() - r, pos.y() - r, r * 2, r * 2), Qt.AlignCenter, label)
        if text:
            tf = QFont("Microsoft YaHei UI", r, QFont.Bold)   # 附言与编辑器同字号（所见即所得）
            p.setFont(tf)
            fm = p.fontMetrics()
            p.setPen(QColor(color))    # 纯填充随圆标色；黑描边已按需求去掉
            y0 = tpos.y() + fm.ascent()          # QTextEdit 首行基线＝顶边+ascent
            for i, ln in enumerate(text.split("\n")):
                p.drawText(QPoint(tpos.x(), y0 + i * fm.lineSpacing()), ln)

    def _paint_nums(self, p, off=None):
        """序号层：按 _nums 列表现画（不烙进 anno 像素），标号=顺序+1，删除后自动重排。"""
        for i, n in enumerate(self._nums):
            pt = n["pt"] if off is None else QPoint(n["pt"].x() + off.x(), n["pt"].y() + off.y())
            tpos = QPoint(pt.x() + n.get("tdx", n["r"] + 6), pt.y() + n.get("tdy", -12))
            text = "" if i == getattr(self, "_num_edit_idx", None) else n["text"]   # R104 再编辑让位
            self._draw_one_num(p, pt, tpos, self._num_label(i), text, n["color"], n["r"])   # R100：样式标号

    def _num_note_rect(self, n):
        """附言文本的命中矩形（与 _draw_one_num 同一套定位/字体度量），无附言返回 None。"""
        if not n.get("text"):
            return None
        r = n["r"]
        tpos = QPoint(n["pt"].x() + n.get("tdx", r + 6), n["pt"].y() + n.get("tdy", -12))
        fm = QFontMetrics(QFont("Microsoft YaHei UI", r, QFont.Bold))
        lines = n["text"].split("\n")
        w = max((fm.horizontalAdvance(ln) for ln in lines), default=0)
        return QRect(tpos.x(), tpos.y(), w + 2, fm.lineSpacing() * len(lines) + 2)

    def _hit_num(self, pos):
        """命中检测：圆心距 ≤ 半径+2 或 点在附言矩形内 视为命中，返回下标（后画的优先）。
        R104：附言是序号对象的一部分——按住附言可拖动、双击附言可再编辑。"""
        for i in range(len(self._nums) - 1, -1, -1):
            n = self._nums[i]
            dx, dy = pos.x() - n["pt"].x(), pos.y() - n["pt"].y()
            if dx * dx + dy * dy <= (n["r"] + 2) ** 2:
                return i
            rc = self._num_note_rect(n)
            if rc is not None and rc.contains(pos):
                return i
        return None

    def _num_del_pt(self, n):
        """× 删除手柄圆心：圆标右上角外侧，不遮标号。"""
        r = n["r"]
        return QPoint(n["pt"].x() + r + 4, n["pt"].y() - r - 4)

    def _paint_num_del(self, p):
        """× 删除手柄：屏幕现画（不进 composite_crop 导出），num 工具下可见。"""
        for n in self._nums:
            c = self._num_del_pt(n)
            p.setPen(QPen(QColor(0, 0, 0, 190), 1))
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(c, 7, 7)
            p.setPen(QPen(QColor(43, 47, 56), 2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c.x() - 3, c.y() - 3, c.x() + 3, c.y() + 3)
            p.drawLine(c.x() - 3, c.y() + 3, c.x() + 3, c.y() - 3)

    def _hit_num_del(self, pos):
        """× 手柄命中（须先于圆标拖动检测），返回下标或 None。"""
        for i in range(len(self._nums) - 1, -1, -1):
            c = self._num_del_pt(self._nums[i])
            dx, dy = pos.x() - c.x(), pos.y() - c.y()
            if dx * dx + dy * dy <= 81:            # 9px 命中半径
                return i
        return None

    def _mosaic_brush_r(self):
        """马赛克笔刷半径（逻辑像素）：跟随粗细档位，一划覆盖成片而非 7px 细线。"""
        return 8 + self.cur_size * 4        # 细/中/粗 → 16/24/36

    def _mosaic_bs(self):
        """马赛克网格边长（逻辑像素）：跟随粗细档位——细 8 / 中 12 / 粗 18。
        遮挡有效的充要条件：块度 ≥ 字高的 2/3（一个字摊不到两块，均色混合后笔画信息才被摧毁）。
        旧档 4/7/11 对 14-16px 聊天文字每字 2×4 块，只"打了格子"没"抹掉信息"，字照样可读。"""
        return {2: 8, 4: 12, 7: 18}.get(self.cur_size, 12)

    def _mosaic_tiles(self, pts):
        """笔刷半径内覆盖到的马赛克网格（去重，网格随粗细档位），每格取底图物理像素均色。
        刷头是圆形：格中心距路径 ≤ 半径即入刷；路径按 bs/2 步进插值，
        快速拖动也不漏格——此前只糊鼠标采样点，一划只有点线，
        用户要来回涂很多次才盖住一片。增量缓存：拖动中每次只算新段。"""
        bs, d, img = self._mosaic_bs(), self.k, self.base_img
        bsz = max(1, round(bs * d))
        step = max(2, bsz // 10)            # 块大则稀疏取样：均色对密度不敏感，粗档拖动性能恒定
        rad = self._mosaic_brush_r()
        cache = self._mosa_cache
        if cache is not None and cache[0] == id(pts) and cache[1] <= len(pts):
            start, out, seen = cache[1] - 1, cache[2], set(cache[3])
        else:
            start, out, seen = 0, [], set()
        if not pts:
            return out
        half = bs / 2.0
        r2 = rad * rad
        iw, ih = img.width(), img.height()

        def emit(px, py):
            for ty in range(int(math.floor((py - rad) / bs)) * bs,
                            int(math.floor((py + rad) / bs)) * bs + 1, bs):
                for tx in range(int(math.floor((px - rad) / bs)) * bs,
                                int(math.floor((px + rad) / bs)) * bs + 1, bs):
                    if (tx, ty) in seen:
                        continue
                    dx = tx + half - px
                    dy = ty + half - py
                    if dx * dx + dy * dy > r2:
                        continue
                    seen.add((tx, ty))
                    x0 = min(max(int(tx * d) // bsz * bsz, 0), max(iw - 1, 0))
                    y0 = min(max(int(ty * d) // bsz * bsz, 0), max(ih - 1, 0))
                    r = g = b = n = 0
                    for yy in range(y0, min(y0 + bsz, ih), step):  # 稀疏取样：均色足够代表性，
                        for xx in range(x0, min(x0 + bsz, iw), step):  # 大刷头下拖动实时性优先
                            c = img.pixelColor(xx, yy)
                            r += c.red(); g += c.green(); b += c.blue(); n += 1
                    if n:
                        out.append((tx, ty, QColor(r // n, g // n, b // n).name()))

        if start == 0:
            emit(pts[0].x(), pts[0].y())
        for i in range(max(start, 1), len(pts)):
            a, bp = pts[i - 1], pts[i]
            dist = math.hypot(bp.x() - a.x(), bp.y() - a.y())
            steps = max(1, int(dist // (bs / 2)))
            for s_ in range(1, steps + 1):
                f = s_ / steps
                emit(a.x() + (bp.x() - a.x()) * f, a.y() + (bp.y() - a.y()) * f)
        self._mosa_cache = (id(pts), len(pts), out, list(seen))
        return out

    # ---------- 标注对象：命中/选中/移动/缩放/删除 ----------
    def _stamp_bbox(self, s):
        k = s["kind"]
        if k in ("rect", "ellipse", "high", "text"):
            return QRect(s["r"])
        if k == "arrow":
            pts = [s["a"], s["b"]] + ([s["c"]] if s.get("c") is not None else [])   # R100：弯箭头包围盒并入控制点
            xs = [p.x() for p in pts]
            ys = [p.y() for p in pts]
            return QRect(QPoint(min(xs), min(ys)), QPoint(max(xs), max(ys)))
        if k == "stroke":
            xs = [p.x() for p in s["pts"]]
            ys = [p.y() for p in s["pts"]]
            return QRect(QPoint(min(xs), min(ys)), QPoint(max(xs), max(ys)))
        if k == "mosaic":
            bs = s["bs"]
            xs = [t[0] for t in s["tiles"]]
            ys = [t[1] for t in s["tiles"]]
            return QRect(QPoint(min(xs), min(ys)),
                         QPoint(max(xs) + bs - 1, max(ys) + bs - 1))
        return QRect()

    def _seg_dist(self, p, a, b):
        dx, dy = b.x() - a.x(), b.y() - a.y()
        l2 = dx * dx + dy * dy
        if l2 == 0:
            return math.hypot(p.x() - a.x(), p.y() - a.y())
        t = max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / l2))
        return math.hypot(p.x() - (a.x() + t * dx), p.y() - (a.y() + t * dy))

    @staticmethod
    def _quad_pts(a, c, b, n=24):
        """R100：二次贝塞尔采样折线（弯箭头命中用；a/c/b 为 QPoint）。"""
        out = []
        for i in range(n + 1):
            t = i / n
            u = 1 - t
            out.append(QPoint(round(u * u * a.x() + 2 * u * t * c.x() + t * t * b.x()),
                              round(u * u * a.y() + 2 * u * t * c.y() + t * t * b.y())))
        return out

    def _hit_one_shape(self, s, pos):
        k = s["kind"]
        if k == "stroke":
            return any(self._seg_dist(pos, s["pts"][i - 1], s["pts"][i]) <= s["w"] / 2 + 5
                       for i in range(1, len(s["pts"])))
        if k == "arrow":
            # R73：容差放宽到 w/2+8——悬到线段上即给十字，不再要求像素级对准
            tol = s["w"] / 2 + 8
            c = s.get("c")                      # R100：弯箭头按贝塞尔采样折线命中
            if c is not None:
                pts = self._quad_pts(s["a"], c, s["b"])
                return any(self._seg_dist(pos, pts[i - 1], pts[i]) <= tol
                           for i in range(1, len(pts)))
            return self._seg_dist(pos, s["a"], s["b"]) <= tol
        if k == "ellipse":
            # R73：按椭圆弧本身命中（此前用矩形边框环，弧的斜部深陷矩形内部悬不中）。
            # 归一化到单位圆空间判径向偏差，×短半径换回像素（保守放宽，好悬是第一诉求）
            r = s["r"]
            rx, ry = max(1.0, r.width() / 2), max(1.0, r.height() / 2)
            cx, cy = r.left() + rx, r.top() + ry
            d = math.hypot((pos.x() - cx) / rx, (pos.y() - cy) / ry)
            return abs(d - 1) * min(rx, ry) <= 6 + self.cur_size
        if k == "rect":
            tol = 6 + self.cur_size
            outer = QRect(s["r"]).adjusted(-tol, -tol, tol, tol)
            inner = QRect(s["r"]).adjusted(tol, tol, -tol, -tol)
            return outer.contains(pos) and not inner.contains(pos)   # 边框环命中；内部留给新形状
        if k == "high":
            if s.get("shape") == "ellipse":            # R100：椭圆聚光按填充区命中（归一化判径）
                r = s["r"]
                rx, ry = max(1.0, r.width() / 2), max(1.0, r.height() / 2)
                d = math.hypot((pos.x() - (r.left() + rx)) / rx, (pos.y() - (r.top() + ry)) / ry)
                return d <= 1.0
            return QRect(s["r"]).contains(pos)
        if k == "text":
            return QRect(s["r"]).contains(pos)
        if k == "mosaic":
            bs = s["bs"]
            return any(QRect(tx, ty, bs, bs).contains(pos) for tx, ty, _ in s["tiles"])
        return False

    def _hit_shape(self, pos):
        for i in range(len(self.stamps) - 1, -1, -1):
            if self._hit_one_shape(self.stamps[i], pos):
                return i
        return None

    def _hit_text(self, pos):
        for i in range(len(self.stamps) - 1, -1, -1):
            s = self.stamps[i]
            if s["kind"] == "text" and QRect(s["r"]).contains(pos):
                return i
        return None

    def _move_shape(self, s, d):
        """平移标注：返回新对象（拖动期间每次从快照重建，不原地累积）。"""
        s = self._clone_entry(s)
        k = s["kind"]
        if k in ("rect", "ellipse", "high", "text"):
            s["r"] = s["r"].translated(d)
        elif k == "arrow":
            s["a"] = QPoint(s["a"].x() + d.x(), s["a"].y() + d.y())
            s["b"] = QPoint(s["b"].x() + d.x(), s["b"].y() + d.y())
            if s.get("c") is not None:                   # R100：控制点随整体平移
                s["c"] = QPoint(s["c"].x() + d.x(), s["c"].y() + d.y())
        elif k == "stroke":
            s["pts"] = [QPoint(p.x() + d.x(), p.y() + d.y()) for p in s["pts"]]
        elif k == "mosaic":
            s["tiles"] = [(tx + d.x(), ty + d.y(), col) for tx, ty, col in s["tiles"]]
        return s

    def _map_pt(self, pt, ob, nb):
        sx = nb.width() / max(1, ob.width())
        sy = nb.height() / max(1, ob.height())
        return QPoint(round(nb.left() + (pt.x() - ob.left()) * sx),
                      round(nb.top() + (pt.y() - ob.top()) * sy))

    def _resize_to_bbox(self, s, ob, nb):
        """把标注从原包围盒 ob 映射到新包围盒 nb（返回新对象）。"""
        s = self._clone_entry(s)
        k = s["kind"]
        if k in ("rect", "ellipse", "high"):
            s["r"] = QRect(nb)
        elif k == "text":
            ratio = min(nb.width() / max(1, ob.width()), nb.height() / max(1, ob.height()))
            s["fs"] = max(8, min(72, round(s["fs"] * ratio)))
            fm = QFontMetrics(QFont("Microsoft YaHei UI", s["fs"], QFont.Bold))
            w = max((fm.horizontalAdvance(ln) for ln in s["lines"]), default=0)
            s["asc"], s["lh"] = fm.ascent(), fm.lineSpacing()
            s["r"] = QRect(nb.left(), nb.top(), w + 2, fm.lineSpacing() * len(s["lines"]) + 2)
        elif k == "arrow":
            s["a"] = self._map_pt(s["a"], ob, nb)
            s["b"] = self._map_pt(s["b"], ob, nb)
            if s.get("c") is not None:                   # R100：控制点同比例映射
                s["c"] = self._map_pt(s["c"], ob, nb)
        elif k == "stroke":
            s["pts"] = [self._map_pt(p, ob, nb) for p in s["pts"]]
            s["w"] = max(1, round(s["w"] * min(nb.width() / max(1, ob.width()),
                                                nb.height() / max(1, ob.height()))))
        elif k == "mosaic":
            bs = s["bs"]
            s["tiles"] = [
                (nb.left() + round((tx - ob.left()) / max(1, ob.width()) * nb.width() / bs) * bs,
                 nb.top() + round((ty - ob.top()) / max(1, ob.height()) * nb.height() / bs) * bs,
                 col)
                for tx, ty, col in s["tiles"]]
        return s

    def _sel_bbox(self):
        tag, i = self.sel_stamp
        if tag == "num":
            n = self._nums[i]
            return QRect(n["pt"].x() - n["r"], n["pt"].y() - n["r"], n["r"] * 2, n["r"] * 2)
        return self._stamp_bbox(self.stamps[i])

    def _sel_scalable(self):
        """选中条目是否可缩放（R71 用户规则）：文字/序号不可缩放——无选中框、无缩放手柄。"""
        tag, i = self.sel_stamp
        return tag == "shape" and self.stamps[i]["kind"] != "text"

    def _stamp_handles(self):
        if not self.sel_stamp or not self._sel_scalable():
            return {}                          # 文字/序号：缩放热区一并禁用（行为与视觉一致）
        tag, i = self.sel_stamp
        if tag == "shape" and self.stamps[i]["kind"] == "arrow":
            s = self.stamps[i]                 # R72：端点 a/b；R100：中点 m 拉曲线；R104：单一样式恒有三点
            m = s.get("c") or QPoint((s["a"].x() + s["b"].x()) // 2,
                                     (s["a"].y() + s["b"].y()) // 2)
            return {"a": s["a"], "m": m, "b": s["b"]}
        return self._handle_pts(self._sel_bbox())

    def _hit_stamp_handle(self, pos):
        for name, pt in self._stamp_handles().items():
            if abs(pos.x() - pt.x()) <= 6 and abs(pos.y() - pt.y()) <= 6:
                return name
        return None

    def _begin_stamp_resize(self, handle):
        """缩放会话：初始包围盒 + 选中条目快照；首次实际缩放才压撤销。"""
        return {"handle": handle, "pushed": False, "ob": QRect(self._sel_bbox()),
                "snap": self._clone_entry(
                    self._nums[self.sel_stamp[1]] if self.sel_stamp[0] == "num"
                    else self.stamps[self.sel_stamp[1]])}

    def _handle_drag_rect(self, handle, pos):
        ob = self._rs["ob"]
        r = QRect(ob)
        if "l" in handle:
            r.setLeft(min(pos.x(), ob.right() - 1))
        if "r" in handle:
            r.setRight(max(pos.x(), ob.left() + 1))
        if "t" in handle:
            r.setTop(min(pos.y(), ob.bottom() - 1))
        if "b" in handle:
            r.setBottom(max(pos.y(), ob.top() + 1))
        return r.normalized()

    def _apply_stamp_resize(self, pos):
        rs = self._rs
        if rs["handle"] in ("a", "b", "m") and self.sel_stamp[0] == "shape" \
                and self.stamps[self.sel_stamp[1]]["kind"] == "arrow":
            if not rs["pushed"]:
                self.push_undo()               # 首次实际拖动才记快照
                rs["pushed"] = True
            s = self._clone_entry(rs["snap"])
            if rs["handle"] == "m":            # R100：中点手柄＝拉曲线；拖回中点 6px 内恢复直线
                mx = (s["a"].x() + s["b"].x()) / 2
                my = (s["a"].y() + s["b"].y()) / 2
                if math.hypot(pos.x() - mx, pos.y() - my) <= 6:
                    s.pop("c", None)
                else:
                    s["c"] = QPoint(pos)
            else:
                old = QPoint(s[rs["handle"]])
                s[rs["handle"]] = QPoint(pos)  # 端点手柄＝直接改箭尾/箭头坐标（非包围盒缩放）
                if s.get("c") is not None:     # 弯箭头拖端点：控制点随位移一半漂移，弯势保持
                    s["c"] = QPoint(round(s["c"].x() + (pos.x() - old.x()) / 2),
                                    round(s["c"].y() + (pos.y() - old.y()) / 2))
            self.stamps[self.sel_stamp[1]] = s
            self._rerender_anno()
            self.update()
            return
        nb = self._handle_drag_rect(rs["handle"], pos)
        if nb.width() < 10 and nb.height() < 10:
            return                         # 两维都太小才拒绝：细线（高度≈1 的笔画）也要能沿长
        if not rs["pushed"]:
            self.push_undo()               # 首次实际缩放才记快照
            rs["pushed"] = True
        if self.sel_stamp[0] == "num":
            n = self._clone_entry(rs["snap"])
            ratio = min(nb.width() / rs["ob"].width(), nb.height() / rs["ob"].height())
            n["r"] = max(6, min(60, round(n["r"] * ratio)))
            self._nums[self.sel_stamp[1]] = n
        else:
            self.stamps[self.sel_stamp[1]] = self._resize_to_bbox(rs["snap"], rs["ob"], nb)
            self._rerender_anno()
        self.update()

    def _draw_stamp_sel(self, p):
        """可缩放标注的选中态：绿色虚线框 + 白手柄。只上屏，不进 anno 导出。
        R104：箭头不画方框——只画箭尾 a/中间 m/箭头 b 三枚圆点（点落在箭身线上）。"""
        tag, i = self.sel_stamp
        is_arrow = tag == "shape" and self.stamps[i]["kind"] == "arrow"
        if not is_arrow:
            p.setPen(QPen(QColor(SEL_GREEN), 1.2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(self._sel_bbox().adjusted(-2, -2, 2, 2))
        p.setPen(QPen(QColor(SEL_GREEN), 1.2))
        p.setBrush(QColor("#ffffff"))
        for pt in self._stamp_handles().values():
            if is_arrow:
                p.drawEllipse(pt, 4, 4)      # R104：箭头选中＝三点，不画方框
            else:
                p.drawRect(QRect(pt.x() - 3, pt.y() - 3, 6, 6))

    def _delete_selected(self):
        if not self.sel_stamp:
            return
        tag, i = self.sel_stamp
        self.push_undo()
        if tag == "num":
            del self._nums[i]              # 序号删除后按剩余顺序自动重排
        else:
            del self.stamps[i]
            self._rerender_anno()
        self.sel_stamp = None
        self.update()

    def _delete_hover(self):
        """悬浮即删（R78）：未选中时 Del/退格删鼠标正悬浮的元素——序号/文字/形状免先点选。"""
        pos = getattr(self, "_hover_pt", None)
        if pos is None:
            return
        hit = self._hit_num(pos)
        if hit is not None:
            self.push_undo()
            del self._nums[hit]            # 序号删除后按剩余顺序自动重排
            self.update()
            return
        hit = self._hit_text(pos)
        if hit is None:
            hit = self._hit_shape(pos)
        if hit is not None:
            self.push_undo()
            del self.stamps[hit]
            self._rerender_anno()
            self.update()

    # ---------- 文字工具：所见即所得（无输入框，浮层直接编辑） ----------
    def _wysiwyg_editor(self, pos, fs, color):
        """文字/序号附言共用的透明无边框多行浮层：所见即所得 + 选区右缘自动换行。"""
        t = AnnotationTextEdit(self)
        t.setAcceptRichText(False)
        t.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setLineWrapMode(QTextEdit.WidgetWidth)  # 软换行：宽封顶＝选区右缘，文档零改写
        t.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        t.setFrameShape(QFrame.NoFrame)   # 视口宽＝部件宽：换行点与提交排版严格一致
        t.document().setDocumentMargin(0)
        frame = t.document().rootFrame()
        fmt = frame.frameFormat()
        fmt.setRightMargin(t.cursorWidth())  # 右缘预留光标，避免文字刚好填满时向左滚动
        frame.setFrameFormat(fmt)
        t.setFont(QFont("Microsoft YaHei UI", fs, QFont.Bold))
        # 全量显式声明：LongEditor 挂 BASE_QSS（QTextEdit 全局 font-size:13px+padding:7px/10px+border），
        # 祖先 QSS 优先级高于 setFont——不逐条压过，浮层字号被锁死、padding 吃掉视口把首行滚走（长图侧实锤）。
        t.setStyleSheet("QTextEdit{background:transparent;border:none;padding:0px;color:%s;"
                        "font-family:'Microsoft YaHei UI';font-size:%dpt;font-weight:bold;}" % (color, fs))
        t.move(QPoint(max(0, pos.x()), max(0, pos.y())))
        t.textChanged.connect(lambda: self._fit_anno_editor(t))
        C.follow_system_input_locale()  # R83：让候选框接管按键，不再落到默认英文键盘
        return t

    def show_text_editor(self, pos, preset=None, idx=None, fs=None, color=None):
        """点击处直接开写：透明无边框多行浮层盖在截图上，所见即所得。
        回车＝换行；Esc＝放弃；失焦（点空白/点工具条）＝落字成对象；
        行宽到选区右缘自动软换行，提交时拆行绘制。
        preset/idx：双击已有文字原地再编辑，提交时原位替换。"""
        self.commit_text_editor()
        fs = 12 + self.cur_size if fs is None else fs
        color = self.cur_color if color is None else color
        sel = self.sel or QRect(0, 0, self.width(), self.height())
        pos = QPoint(max(sel.left(), min(pos.x(), sel.right() - 47)),
                     max(sel.top(), min(pos.y(), sel.bottom() - 20)))
        t = self._wysiwyg_editor(pos, fs, color)
        t._idx, t._fs, t._color = idx, fs, color
        self.text_editor = t
        if idx is not None:
            self._text_edit_idx = idx        # 再编辑期间底层文字让位（R79 防重影），提交/取消恢复
            self._rerender_anno()
        if preset:
            t.setPlainText(preset)
            t.moveCursor(QTextCursor.MoveOperation.End)   # 再编辑：光标落到末尾像接着写
        self._fit_anno_editor(t)
        t.show()
        t.setFocus()
        t.installEventFilter(self)

    def _layout_lines(self, text, font, width):
        """按 QTextLayout 在 width 内断行——与浮层 WidgetWidth 软换行同一排版引擎、
        同一换行模式，提交渲染的换行点与编辑时所见逐行一致＝真所见即所得。
        QTextLayout 只排单段：回车（\n）先拆段再逐段断行；空段＝占一整行空行。"""
        to = QTextOption()
        to.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        out = []
        for para in text.split("\n"):
            if not para:                     # 空段也是一行（连续回车的空行要保留）
                out.append("")
                continue
            tl = QTextLayout(para, font)
            tl.setTextOption(to)
            tl.beginLayout()
            while True:
                ln = tl.createLine()
                if not ln.isValid():
                    break
                ln.setLineWidth(max(1, width - 1))  # 与编辑器右缘 1px 光标留白一致
                out.append(para[ln.textStart(): ln.textStart() + ln.textLength()])
            tl.endLayout()
        return out or [""]

    def _natural_width(self, t):
        """计入正在组字的拼音；preedit 在块布局里，不在 toPlainText 中。
        使用独立布局测自然宽，不修改编辑器文档，也不受当前窄视口限制。"""
        wmax = 0.0
        block = t.document().begin()
        while block.isValid():
            source = block.layout()
            tl = QTextLayout(block.text(), t.font())
            tl.setPreeditArea(source.preeditAreaPosition(), source.preeditAreaText())
            tl.beginLayout()
            ln = tl.createLine()
            if ln.isValid():
                ln.setLineWidth(1e7)
                wmax = max(wmax, ln.naturalTextWidth())
            tl.endLayout()
            block = block.next()
        return wmax

    def _fit_anno_editor(self, t):
        """先按含 preedit 的自然宽扩展，再按真实文档布局撑高。
        隐藏滚动条不等于禁止滚动：视口不足时 Qt 会把首行滚走。尺寸同步必须在
        输入事件排版结束后完成，并清掉已无需滚动的偏移；全程不改文档和光标。"""
        if getattr(t, "_fitting", False) or getattr(t, "_handling_input", False):
            return
        t._fitting = True
        try:
            sel = self.sel or QRect(0, 0, self.width(), self.height())
            avail = max(48, sel.right() - t.x() + 1)
            composing = getattr(t, "_composing", False)
            nat = self._natural_width(t)
            w = min(avail, max(t.width() if composing else 48, math.ceil(nat) + 10))
            if w != t.width():
                t.resize(w, t.height())
            # R106：文档布局惰性——程序性预填时 textChanged 同步读 docSize 常拿到 0，高度只撑
            # 1 行、视口滚到光标处只显末行（双击再编辑「文字变少」的根因）：预填标记下强制
            # 同步排版再读。真实键盘/IME 输入排版链完整，不可强排——adjustSize 走 QTextDocument
            # 直排会丢掉 QTextControl 注入的 preedit 段、光标映射错位（「首行可见」锁的教训）。
            _vpw = t.viewport().width()
            if _vpw > 0 and not composing and getattr(t, "_need_relayout", False):
                t._need_relayout = False
                t.document().setTextWidth(_vpw)
                t.document().adjustSize()
            # documentSize 包含软换行、字体实际行高及预编辑，不能用已提交文本估算。
            doc_size = t.document().documentLayout().documentSize()
            h = max(QFontMetrics(t.font()).height(), math.ceil(doc_size.height()) + 2)
            if composing:
                h = max(t.height(), h)
            if h != t.height():
                t.resize(t.width(), h)
            t.move(max(sel.left(), min(t.x(), sel.right() - t.width() + 1)),
                   max(sel.top(), min(t.y(), sel.bottom() - t.height() + 1)))
            if doc_size.height() <= t.viewport().height():
                t.verticalScrollBar().setValue(0)
            if doc_size.width() <= t.viewport().width():
                t.horizontalScrollBar().setValue(0)
        finally:
            t._fitting = False

    def commit_text_editor(self):
        """失焦/确认：浮层文字落成文字对象（空＝丢弃）。回车换行已在编辑中发生。"""
        t = self.text_editor
        if not t:
            return
        self.text_editor = None          # 先摘钩子，防 FocusOut 重入
        idx, fs, color = t._idx, t._fs, t._color
        top = QPoint(t.x(), t.y())
        sel = self.sel or QRect(0, 0, self.width(), self.height())
        avail = max(48, sel.right() - top.x() + 1)
        lines = self._layout_lines(t.toPlainText(),
                                   QFont("Microsoft YaHei UI", fs, QFont.Bold), avail)
        while lines and not lines[-1].strip():
            lines.pop()
        t.deleteLater()
        self.setFocus()                  # 焦点回覆盖层：再按回车＝复制到剪贴板
        if not "".join(lines).strip():
            self._text_edit_idx = None   # 空提交＝丢弃新内容：原文字恢复上屏（R79）
            self._rerender_anno()
            return
        fm = QFontMetrics(QFont("Microsoft YaHei UI", fs, QFont.Bold))
        w = max((fm.horizontalAdvance(ln) for ln in lines), default=0)
        s = {"kind": "text", "lines": lines, "color": color, "fs": fs,
             "asc": fm.ascent(), "lh": fm.lineSpacing(),
             "r": QRect(top.x(), top.y(), w + 2, fm.lineSpacing() * len(lines) + 2)}
        self._text_edit_idx = None       # 提交后底层恢复上屏（R79）
        self.push_undo()
        if idx is not None and idx < len(self.stamps) and self.stamps[idx]["kind"] == "text":
            self.stamps[idx] = s         # 再编辑：原位替换（撤销可回退）
        else:
            self.stamps.append(s)
        self._rerender_anno()

    def cancel_text_editor(self):
        """Esc：丢弃浮层里的文字。"""
        t = self.text_editor
        if not t:
            return
        self.text_editor = None
        self._text_edit_idx = None       # Esc 放弃：原文字恢复上屏（R79）
        t.deleteLater()
        self.setFocus()
        self._rerender_anno()

    def _reedit_text(self, i):
        """双击文字：原地浮层再编辑（提交时替换该对象）。"""
        s = self.stamps[i]
        self.show_text_editor(QPoint(s["r"].left(), s["r"].top()),
                              preset="\n".join(s["lines"]), idx=i,
                              fs=s["fs"], color=s["color"])

    def _reedit_num(self, i):
        """R104：双击序号（圆标/附言）＝附言原地浮层再编辑——预填原文，失焦落字；
        整个会话＝一步撤销（快照在开场压入，Esc 走 cancel_text_edit 回滚它）；
        再编辑期间底层附言让位（_num_edit_idx，同 R79 防重影），圆标保留。"""
        n = self._nums[i]
        self.commit_text_editor()
        self.commit_text_input()
        self.push_undo()
        r = n["r"]
        tpos = QPoint(n["pt"].x() + n.get("tdx", r + 6), n["pt"].y() + n.get("tdy", -12))
        sel = self.sel or QRect(0, 0, self.width(), self.height())
        x = max(sel.left(), min(tpos.x(), sel.right() - 47))
        y = max(sel.top(), min(tpos.y(), sel.bottom() - 20))
        t = self._wysiwyg_editor(QPoint(x, y), r, n["color"])   # 附言字号＝圆标半径（与渲染同）
        t._anchor = QPoint(n["pt"])
        t._num = True
        t._entry = n
        self.text_edit = t
        self._num_edit_idx = i             # 再编辑期间底层附言让位（防重影）
        if n.get("text"):
            t.setPlainText(n["text"])
            t.moveCursor(QTextCursor.MoveOperation.End)   # 光标落末尾像接着写
        self._fit_anno_editor(t)
        t.show()
        t.setFocus()
        t.installEventFilter(self)
        self.update()

    def show_num_input(self, pos):
        """序号：点击落标（追加进 _nums，画面即现圆标），右侧所见即所得浮层输附言。
        失焦/点任意处＝落字（同文字工具，那一下不再新增序号，再点一下才落新标）；
        行宽到选区右缘自动换行，多行附言竖排贴圆标；Esc 取消落标；右键圆标删除，删除后自动重排。"""
        self.commit_text_input()
        self.push_undo()
        self._nums.append({"pt": QPoint(pos), "text": "",
                           "color": self.cur_color, "r": self.num_radius()})
        r = self._nums[-1]["r"]
        sel = self.sel or QRect(0, 0, self.width(), self.height())
        x = max(sel.left(), min(pos.x() + r + 6, sel.right() - 47))
        y = max(sel.top(), min(pos.y() - 12, sel.bottom() - 20))
        t = self._wysiwyg_editor(QPoint(x, y), 12 + self.cur_size, self.cur_color)
        t._anchor = QPoint(pos)
        t._num = True
        t._entry = self._nums[-1]
        self.text_edit = t
        self._fit_anno_editor(t)
        t.show()
        t.setFocus()
        t.installEventFilter(self)
        self.update()

    def commit_text_input(self):
        """把当前输入框的文字写入标注层；Esc 才丢弃。
        失焦（点工具条/完成/保存）时自动触发，保证文字不丢；序号框失焦不自动提交，
        统一由回车/点空白显式确认——确认那一下不落新标。
        序号附言：文字存进序号对象由序号层现画，无字=只留序号圆标。"""
        t = self.text_edit
        if not t:
            return
        self.text_edit = None          # 先摘钩子，防 FocusOut 重入
        txt = t.toPlainText().strip()
        pos = getattr(t, "_anchor", QPoint(t.pos()))
        is_num = getattr(t, "_num", False)
        entry = getattr(t, "_entry", None)
        t.deleteLater()
        if is_num:
            if entry is not None:
                sel = self.sel or QRect(0, 0, self.width(), self.height())
                avail = max(48, sel.right() - t.x() + 1)
                entry["text"] = "\n".join(self._layout_lines(    # 与编辑器同一排版拆行：
                    txt, t.font(), avail)) if txt else ""      # 序号层逐行画＝所见即所得
                entry["tdx"] = t.x() - entry["pt"].x()   # 附言原位锚点（相对圆标）：
                entry["tdy"] = t.y() - entry["pt"].y()   # 渲染按编辑器实际位置落字，确认后不跳位
                self._num_edit_idx = None   # R104：提交后底层附言恢复上屏
                self.update()
            return
        if txt:
            self.push_undo()
            f = QFont("Microsoft YaHei UI", 12 + self.cur_size, QFont.Bold)
            fm = QFontMetrics(f)
            self.stamps.append({"kind": "text", "lines": [txt], "color": self.cur_color,
                                "fs": 12 + self.cur_size, "asc": fm.ascent(),
                                "lh": fm.lineSpacing(),
                                "r": QRect(pos.x(), pos.y() + 2,
                                           fm.horizontalAdvance(txt) + 2, fm.lineSpacing() + 2)})
            self._rerender_anno()

    def cancel_text_edit(self):
        """Esc：关闭输入框；序号则连同点击时落的圆标一并取消（回退快照）。"""
        t = self.text_edit
        if not t:
            return
        self.text_edit = None
        if getattr(t, "_num", False):
            a, nums, stamps = self.undo_stack[-1]      # 回到落标前：圆标与附言一并消失
            self.anno = QPixmap(a)                     # （再编辑会话＝回到改前原文）
            self._nums = [dict(n) for n in nums]
            self.stamps = [self._clone_entry(s) for s in stamps]
            self.sel_stamp = None
            if len(self.undo_stack) > 1:
                self.undo_stack.pop()                  # 取回落标/再编辑时压入的快照
            self._num_edit_idx = None                  # R104：放弃后底层附言恢复上屏
            self.update()
        t.deleteLater()
        self.setFocus()

    def eventFilter(self, obj, ev):
        if obj is self.text_editor:
            if ev.type() == ev.Type.KeyPress and ev.key() == Qt.Key_Escape:
                self.cancel_text_editor()
                return True
            if ev.type() == ev.Type.FocusOut:
                self.commit_text_editor()  # 失焦＝落字（点空白/点工具条/点完成都走这里）
        elif obj is self.text_edit:
            if ev.type() == ev.Type.KeyPress and ev.key() == Qt.Key_Escape:
                self.cancel_text_edit()
                return True
            if ev.type() == ev.Type.FocusOut:
                self.commit_text_input()   # 失焦＝落字（附言所见即所得，与文字工具同语义）
        return super().eventFilter(obj, ev)

    # ---------- 工具条（暗色圆角矢量图标条，贴选区下方；图标按所在屏 DPR 渲染保持锐利） ----------
    def show_toolbar(self):
        # R147：键位标签开关读取提前到构建期——按开关定钮高/padding（关标签直接收窄，不留空标签位）
        self._key_tips_on = bool(C.load_settings().get("shot_key_tips", True))
        tb = QWidget(self)
        tb.setStyleSheet(self._tb_qss())
        lay = QHBoxLayout(tb)
        lay.setContentsMargins(7, 5, 7, 5)
        lay.setSpacing(2)

        def sep():
            f = QFrame(tb)
            f.setFixedSize(1, 20)
            lay.addWidget(f)
            lay.addSpacing(5)

        try:                       # 图标跟工具条所在屏的 DPR，多屏混合 DPI 也不糊
            dpr = self.screen_.devicePixelRatio() or 1.0
        except Exception:
            dpr = None

        def tbtn(kind, tip, cb, color=None, checkable=False, w=34, h=None, lucide=None):
            b = QPushButton(tb)
            b.setObjectName("tbBtn")
            b.setIcon(icons.lucide_icon(lucide, color or icons.STROKE, 20, dpr) if lucide
                      else icons.icon(kind, color or icons.STROKE, 20, dpr))   # R138 lucide 通道（设置钮）
            b.setIconSize(QSize(20, 20))
            b.setFixedSize(w, h or (36 if self._key_tips_on else 30))   # R147：关标签钮高回 30（收窄）
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            lay.addWidget(b)
            return b

        self.tool_btns = {}
        self._key_tips = {}                                # R138 图标下方键位标签
        for tool in TOOL_LIST:
            b = tbtn("cursor" if tool == "select" else tool, TOOL_NAMES[tool],
                     lambda _, t=tool: self.set_tool(t), checkable=True)
            b.setChecked(tool == self.cur_tool)
            self._key_tips[tool] = self._key_tip_for(b, "")
            self.tool_btns[tool] = b
        self.tool_btns["select"].setToolTip("选择 · 点标注拖动/缩放，Del 或右键删除")
        self.tool_btns["text"].setToolTip("文字 · 点击落字直接输入，回车换行，点空白完成")
        self.tool_btns["num"].setToolTip("序号 · 点击落标，按住拖动，点 × 或右键删除（自动重排）")
        for b in self.tool_btns.values():
            b._base_tip = b.toolTip()                      # R138：tooltip 快照——改键刷新防累积
        sep()
        # R134：工具级选项（箭头样式/序号样式起始/聚光形态）全部迁入辐条 ToolPropsPop——
        # 主工具条只留工具钮与动作钮；属性钮与第二根分隔线退役（「高光后两条竖线」bug 随之消除）
        # R144 动作钮也贴键位标签（用户点名「那一排功能下面，仍然可以加上快捷键」）；
        # rec 钮小红点退役改 lucide monitor-play 与面板同源（用户改口「还是应该改成类似视频或录屏的样式」）
        def _act(kind, key, tip, cb, lucide=None):
            b = tbtn(kind, tip + f"（{DEFAULT_SHOT_ACT_KEYS[key]}）" if key else tip, cb,
                     lucide=lucide)
            if key:
                self._key_tips["act:" + key] = self._key_tip_for(b, DEFAULT_SHOT_ACT_KEYS[key])
            return b

        if not LITE:                                   # R107/R108 轻量版：OCR 钮裁（GIF/长截图钮保留）；R120 二维码识别随 cv2 退役
            _act("ocr", "ocr", "提取 · 识别选区文字（Windows 离线 OCR）", self.do_ocr)
            _act(None, "rec", "录屏 · 以当前选区直接开始录制（MP4）", self.start_rec,
                 lucide="monitor-play")               # R131 截图态直通录屏（选区现成）
        _act("gif", "gif", "把选区录成 GIF 动图", self.start_gif)
        _act("longshot", "longshot", "长截图 · 选区进滚动拼接（条上可切双栏模式）", self.start_longshot)   # R105/R119
        _act(None, "beautify", "美化 · 背景、留白、圆角、阴影与比例", self.do_beautify, lucide="palette")
        _b = tbtn("undo", "撤销 (Ctrl+Z)", self.undo)
        self._key_tips["act:undo"] = self._key_tip_for(_b, "^Z")
        _act("save", "save", "保存为 PNG", self.do_save)
        _act("pin", "pin", "钉在桌面", self.do_pin)
        self.btn_keys = tbtn(None, "快捷键 · 键位标签开关与工具键自定义（K）", self._open_keys_menu,
                             lucide="keyboard")                     # R138 设置入口（pin 后 close 前）
        self._key_tips["act:keys"] = self._key_tip_for(self.btn_keys, "K")   # R152 键盘钮补贴（全条统一）
        _bc = tbtn("close", "退出截图 (Esc)", self.close_cancel, color="#f4645f")
        self._key_tips["act:close"] = self._key_tip_for(_bc, "Esc")          # R152 叉号贴 Esc（R144 不贴判例被用户点名推翻）
        _bo = tbtn("confirm", "完成并复制（回车/双击选区）", self.do_copy, color="#2ecc71")
        self._key_tips["act:confirm"] = self._key_tip_for(_bo, "↩")          # R152 对号贴 ↩（mac return 符号，单字符最精致）
        # R152：三签同入 _key_tips——_apply_key_tips 统一遍历，关「显示键位标签」时随之自动不显示

        tb.adjustSize()
        self.toolbar = tb
        self.place_toolbar()
        tb.show()
        self.props_pop = ToolPropsPop(self)          # R131 颜色/粗细浮窗（默认隐，标注工具激活即贴）
        self.props_pop.hide()
        self._sync_props_pop()
        # R131 工具快捷键反查表（每次截图随工具条构建读一次设置，当次生效）
        _tk = dict(DEFAULT_SHOT_TOOL_KEYS)
        _tk.update(C.load_settings().get("shot_tool_keys", {}))
        self._tool_keys_rev = {v.upper(): t for t, v in _tk.items() if v}
        # R144 动作键反查表（固定键不读设置；LITE 同步裁 ocr/rec）
        self._act_keys_rev = {v.upper(): k for k, v in DEFAULT_SHOT_ACT_KEYS.items()
                              if not (LITE and k in SHOT_ACT_LITE_SKIP)}
        self._refresh_tool_key_tips()
        self._apply_key_tips()

    def start_rec(self):
        """R131：以当前选区直接进录屏——关闭覆盖层，主窗口起 RecBar(sel, scr) 跳过框选。"""
        if self.toolbar:
            self.toolbar.close()
            self.toolbar = None
        self.finished.emit({"pixmap": None, "pick": True, "sel": QRect(self.sel),
                            "screen": self.screen_, "ocr": False, "scan": False,
                            "path": None, "error": None, "rec": True})
        self._really_close()

    def start_gif(self):
        """把当前选区录成 GIF：关闭覆盖层，交给主窗口启动 GifBar。"""
        if self.toolbar:
            self.toolbar.close()
            self.toolbar = None
        self.finished.emit({"pixmap": None, "pick": True, "sel": QRect(self.sel),
                            "screen": self.screen_, "ocr": False, "scan": False,
                            "path": None, "error": None, "gif": True})
        self._really_close()

    def start_longshot(self):
        """R105：把当前选区滚成长截图——关闭覆盖层，交给主窗口启动 LongShotBar（同 start_gif 路由模式）。"""
        if self.toolbar:
            self.toolbar.close()
            self.toolbar = None
        self.finished.emit({"pixmap": None, "pick": True, "sel": QRect(self.sel),
                            "screen": self.screen_, "ocr": False, "scan": False,
                            "path": None, "error": None, "longshot": True})
        self._really_close()

    def _tool_idle_cursor(self):
        """R84 空白处光标随工具直觉化：绘制/落标类＝纯十字（同框选十字），文字＝I 型输入。"""
        if self.cur_tool == "text":
            return Qt.IBeamCursor
        if self.cur_tool == "select":
            return Qt.ArrowCursor
        return Qt.CrossCursor       # rect/ellipse/arrow/pen/marker/mosaic/high/num

    def set_tool(self, tool):
        self.commit_text_editor()          # 切工具前先落字
        self.cur_tool = tool
        self.setCursor(self._tool_idle_cursor())   # R84：切完即生效，不等下一次移动
        for t, b in self.tool_btns.items():
            b.setChecked(t == tool)
        self._sync_props_pop()               # R134 辐条跟随工具显隐（选项区随 set_tool 换内容）

    # ---------- R100 工具属性钮 ----------
    def _refresh_prop_btns(self):
        """R134：选项钮已迁入辐条（ToolPropsPop），刷新统一委托 pop.sync。"""
        pop = getattr(self, "props_pop", None)
        if pop:
            pop.sync()

    def _pick_num_style(self):
        """序号样式下拉（R98 QMenu 范式：不透顶层窗自带 QSS）。"""
        m = QMenu(self)
        m.setStyleSheet(C.UI_MENU_QSS)
        for v, label in (("num", "1, 2, 3"), ("upper", "A, B, C"), ("lower", "a, b, c"),
                         ("uroman", "I, II, III"), ("lroman", "i, ii, iii")):
            act = m.addAction(label)
            act.setCheckable(True)
            act.setChecked(v == self.cur_num_style)
            act.triggered.connect(lambda _=False, vv=v: self._set_num_style(vv))
        m.exec(QCursor.pos())

    def _set_num_style(self, v):
        self.cur_num_style = v
        self._refresh_prop_btns()
        self.update()                        # 序号层现画：全体标号即时换样式

    def _cycle_num_start(self):
        self.cur_num_start = self.cur_num_start % 9 + 1      # 1~9 循环
        self._refresh_prop_btns()
        self.update()

    def _cycle_high_shape(self):
        """选中聚光＝改该对象；未选中＝改新画默认。"""
        if self.sel_stamp and self.sel_stamp[0] == "shape" \
                and self.stamps[self.sel_stamp[1]]["kind"] == "high":
            self.push_undo()
            s = self._clone_entry(self.stamps[self.sel_stamp[1]])
            s["shape"] = "ellipse" if s.get("shape", "rect") == "rect" else "rect"
            self.stamps[self.sel_stamp[1]] = s
            self._rerender_anno()
            self.update()
        else:
            self.cur_high_shape = "ellipse" if self.cur_high_shape == "rect" else "rect"
        self._refresh_prop_btns()

    def _cycle_arrow_style(self):
        """R132 箭头样式两档循环：选中箭头＝改该对象（压撤销）；未选中＝改新画默认。"""
        if self.sel_stamp and self.sel_stamp[0] == "shape" \
                and self.stamps[self.sel_stamp[1]]["kind"] == "arrow":
            self.push_undo()
            s = self._clone_entry(self.stamps[self.sel_stamp[1]])
            s["style"] = "solid" if s.get("style", "line") == "line" else "line"
            self.stamps[self.sel_stamp[1]] = s
            self._rerender_anno()
            self.update()
        else:
            self.cur_arrow_style = "solid" if self.cur_arrow_style == "line" else "line"
        self._refresh_prop_btns()

    def set_color(self, c):
        self.cur_color = c
        if hasattr(self, "props_pop") and self.props_pop:   # R131 浮窗选中态
            self.props_pop.sync()
        if self.text_editor:               # 编辑中的文字即时换色（所见即所得）
            self.text_editor._color = c
            self.text_editor.setStyleSheet(
                "QTextEdit{background:transparent;border:none;color:%s;}" % c)

    def set_size(self, s):
        self.cur_size = s
        if hasattr(self, "props_pop") and self.props_pop:   # R131 浮窗选中态
            self.props_pop.sync()

    # ---------- 完成 ----------
    def composite_crop(self):
        """导出选区：物理分辨率真像素，挂回 DPR 供钉图/预览按逻辑尺寸显示。

        必须用 QPixmap.copy（源矩形=设备像素）裁剪，不能用 drawPixmap 源矩形版：
        带 DPR 的源图裁出的子图继承 DPR，会被缩回逻辑尺寸绘制，
        导出图只有左上角约 1/k 面积有内容、其余是未初始化黑底（实测踩坑）。"""
        self.commit_text_editor()      # WYSIWYG 文字浮层先落成对象
        self.commit_text_input()       # 完成/保存/钉图前先落字，避免文字丢失
        d = self.k
        s = QRect(round(self.sel.x() * d), round(self.sel.y() * d),
                  round(self.sel.width() * d), round(self.sel.height() * d))
        c = self.base.copy(s)          # 物理像素原样拷贝
        a = self.anno.copy(s)
        c.setDevicePixelRatio(1.0)     # 先清 DPR 按 1:1 叠标注层，避免二次缩放
        a.setDevicePixelRatio(1.0)
        p = QPainter(c)
        p.setRenderHint(QPainter.Antialiasing, True)   # 序号层补画抗锯齿（导出与屏幕同观感）
        p.drawPixmap(0, 0, a)
        if self._nums:
            p.scale(d, d)              # 序号层已对象化：物理画布上按逻辑坐标补画
            self._paint_nums(p, QPoint(-self.sel.x(), -self.sel.y()))
        p.end()
        c.setDevicePixelRatio(d)       # 最后挂回：钉图/预览按逻辑尺寸显示，保存/剪贴板仍是全分辨率
        return c

    def do_copy(self):
        if not self.sel:
            return
        pm = self.composite_crop()
        if not C.copy_image_to_clipboard(pm.toImage()):
            QGuiApplication.clipboard().setPixmap(pm)
        self.emit_done(pm)

    def do_save(self):
        if not self.sel:
            return
        import common as C
        pm = self.composite_crop()
        try:
            path = os.path.join(C.pictures_dir(), f"截图_{C.timestamp()}.png")
            C.save_image(pm, path)
        except OSError as error:
            self.emit_done(pm, None, "保存失败 · " + str(error))
            return
        self.emit_done(pm, path)

    def do_pin(self):
        if not self.sel:
            return
        pm = self.composite_crop()
        self.pinned.emit(pm, self.screen_, self.sel.topLeft() + self.screen_.geometry().topLeft())
        self.emit_done(pm, pinned=True)

    def do_beautify(self):
        if not self.sel:
            return
        self.emit_done(None, beautify=True)

    def do_ocr(self):
        """工具条直达：把当前选区送去文字识别。
        先藏掉全屏遮罩再发信号，否则结果弹窗被置顶遮罩挡住，看起来像点击无效。"""
        if not self.sel:
            return
        pm = self.composite_crop()
        self.hide()
        self.emit_done(pm, ocr=True)

    def emit_done(self, pm, save_path=None, error=None, ocr=None, scan=None, pinned=False, beautify=False):
        document = None
        if self.sel and not (self.ocr if ocr is None else ocr) and not (self.scan if scan is None else scan):
            from screenshot_history import from_overlay
            document = from_overlay(self)
        self.finished.emit({"pixmap": pm, "path": save_path,
                            "ocr": self.ocr if ocr is None else ocr,
                            "scan": self.scan if scan is None else scan,
                            "error": error, "pinned": pinned, "document": document, "beautify": beautify})
        self._really_close()

    def close_cancel(self):
        self.finished.emit(None)
        self._really_close()

    def close_silent(self):
        """多屏组清理：静默关闭（不发 finished、不碰剪贴板/文件），重复调用幂等。"""
        self._really_close()

    def _really_close(self):
        self.hide()
        self._release_images()
        self.deleteLater()

    def _release_images(self):
        """标注控件存在 Python 引用环；关闭时先释放像素，不等循环垃圾回收。"""
        self._color_track.stop()
        self.base = QPixmap()
        self.base_img = QImage()
        self.anno = QPixmap()
        self.undo_stack.clear()
        self.stamps.clear()
        self._nums.clear()
        self._mosa_cache = None
        self.stroke_pts = None
        pop = getattr(self, "props_pop", None)
        if pop is not None:
            pop.hide()
            pop.ov = None
            self.props_pop = None

    def closeEvent(self, ev):
        self._release_images()
        ev.accept()


class PinnedWindow(QWidget):
    """钉图：置顶悬浮窗，拖拽移动，边缘等比缩放，双击关闭。"""
    EDGE = 7
    _EDGE_CURSORS = {1: Qt.SizeHorCursor, 2: Qt.SizeHorCursor,
                     4: Qt.SizeVerCursor, 8: Qt.SizeVerCursor,
                     5: Qt.SizeFDiagCursor, 10: Qt.SizeFDiagCursor,
                     6: Qt.SizeBDiagCursor, 9: Qt.SizeBDiagCursor}

    def __init__(self, pixmap, screen=None, position=None):
        super().__init__(None)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.pm = QPixmap(pixmap)
        if screen not in QGuiApplication.screens():
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            self.setScreen(screen)
        # Source pixels already carry the capture DPR. Divide exactly once;
        # a primary-screen HWND followed by adjustSize() can initially use
        # the wrong scale on a secondary screen with a different DPI.
        image_size = self.pm.deviceIndependentSize().toSize()
        self.resize(max(1, image_size.width()) + 2, max(1, image_size.height()) + 2)
        if screen is not None:
            bounds = screen.availableGeometry()
            if position is None:
                point = bounds.center() - self.rect().center()
            else:
                point = QPoint(position) - QPoint(1, 1)
            # Keep the origin on the selected display without shrinking the image.
            point.setX(max(bounds.left(), min(point.x(), bounds.right() - self.width() + 1)))
            point.setY(max(bounds.top(), min(point.y(), bounds.bottom() - self.height() + 1)))
            self.move(point)
        self._drag = None
        self._resize_drag = None
        self._edge_cursor = None
        self.setMouseTracking(True)
        C.prepare_capture_window(self)
        self.show()

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111111"))
        natural = self.pm.deviceIndependentSize()
        if not self.pm.isNull():
            # Always paint from the original; repeated resizing must not
            # accumulate blur or allocate another full-size image cache.
            scale = min(max(1, self.width() - 2) / natural.width(),
                        max(1, self.height() - 2) / natural.height())
            painter.save()
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.translate((self.width() - natural.width() * scale) / 2,
                              (self.height() - natural.height() * scale) / 2)
            painter.scale(scale, scale)
            painter.drawPixmap(0, 0, self.pm)
            painter.restore()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 4, 4)
        painter.end()

    def _resize_edge(self, point):
        if not self.rect().contains(point):
            return 0
        edge = min(self.EDGE, max(1, min(self.width(), self.height()) // 3))
        direction = 0
        if point.x() < edge:
            direction |= 1
        elif point.x() >= self.width() - edge:
            direction |= 2
        if point.y() < edge:
            direction |= 4
        elif point.y() >= self.height() - edge:
            direction |= 8
        return direction

    def _set_edge_cursor(self, direction):
        cursor = self._EDGE_CURSORS.get(direction)
        if cursor == self._edge_cursor:
            return  # setCursor can itself generate a mouse move on Windows.
        self._edge_cursor = cursor
        if cursor is None:
            self.unsetCursor()
        else:
            self.setCursor(cursor)

    def _resize_from_pointer(self, point):
        direction, start, original = self._resize_drag
        delta = point - start
        natural = self.pm.deviceIndependentSize()
        nw, nh = max(1., natural.width()), max(1., natural.height())
        width, height = max(1, original.width() - 2), max(1, original.height() - 2)
        horizontal, vertical = direction & 3, direction & 12
        requested_w = width + (-delta.x() if direction & 1 else delta.x())
        requested_h = height + (-delta.y() if direction & 4 else delta.y())
        if horizontal and vertical:
            # Project a corner drag onto the image's aspect-ratio diagonal.
            scale = (nw * requested_w + nh * requested_h) / (nw * nw + nh * nh)
        elif horizontal:
            scale = requested_w / nw
        else:
            scale = requested_h / nh
        minimum = min(1., 48. / max(nw, nh))
        maximum = min((self.maximumWidth() - 2) / nw,
                      (self.maximumHeight() - 2) / nh)
        scale = min(maximum, max(minimum, scale))
        width, height = max(1, round(nw * scale)) + 2, max(1, round(nh * scale)) + 2
        if direction & 1:
            x = original.x() + original.width() - width
        elif direction & 2:
            x = original.x()
        else:
            x = original.x() + round((original.width() - width) / 2)
        if direction & 4:
            y = original.y() + original.height() - height
        elif direction & 8:
            y = original.y()
        else:
            y = original.y() + round((original.height() - height) / 2)
        self.setGeometry(x, y, width, height)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            point = ev.globalPosition().toPoint()
            direction = self._resize_edge(ev.position().toPoint())
            if direction:
                self._resize_drag = (direction, point, QRect(self.geometry()))
                self._drag = None
                self._set_edge_cursor(direction)
            else:
                self._drag = point - self.frameGeometry().topLeft()
                self._resize_drag = None
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._resize_drag is not None:
            self._resize_from_pointer(ev.globalPosition().toPoint())
        elif self._drag is not None:
            self.move(ev.globalPosition().toPoint() - self._drag)
        else:
            self._set_edge_cursor(self._resize_edge(ev.position().toPoint()))

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag = None
            self._resize_drag = None
            self._set_edge_cursor(self._resize_edge(self.mapFromGlobal(ev.globalPosition().toPoint())))

    def leaveEvent(self, ev):
        if self._drag is None and self._resize_drag is None:
            self._set_edge_cursor(0)
        super().leaveEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self.close()


# ---------------- 录制中的选区实时框（GIF / 录屏共用） ----------------
# 两层窗口：压暗层全屏覆盖但完全穿透输入（纯视觉），边框条只包住选区外沿
# （mask 镂空中心，点击穿透到下层应用）；所有可见像素都在选区外，不会录进画面。

class RegionDim(QWidget):
    """录制选区的外部压暗层：圈外变暗 + 选区外沿高亮边，不拦截任何鼠标/键盘。"""

    def __init__(self, screen, get_region, color=SEL_GREEN):
        super().__init__(None)
        self._get_region = get_region          # callable → 逻辑 QRect（屏内坐标）
        self._color = color
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setGeometry(screen.geometry())
        C.prepare_capture_window(self)
        self.show()

    def paintEvent(self, ev):
        r = self._get_region()
        if not r or r.width() < 4 or r.height() < 4:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRect(self.rect())
        path.addRect(r)
        path.setFillRule(Qt.OddEvenFill)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 8, 14, 96))       # 圈外压暗
        p.setClipPath(path)
        p.drawRect(self.rect())
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(self._color), 2))
        p.drawRect(r.adjusted(-2, -2, 2, 2))   # 高亮线画在选区外沿，不进录制画面
        p.end()


class RegionFrame(QWidget):
    """录制选区的可交互边框：拖边框移动、8 手柄缩放；中心镂空，点击穿透。"""

    M = 11                                     # 边框条宽
    MINW, MINH = 64, 48
    _CURSORS = [Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor,
                Qt.SizeHorCursor, Qt.SizeHorCursor,
                Qt.SizeBDiagCursor, Qt.SizeVerCursor, Qt.SizeFDiagCursor]

    def __init__(self, screen, region, on_change, color=SEL_GREEN):
        super().__init__(None)
        self._scr = QRect(screen.geometry())
        self._color = color
        self._on_change = on_change            # callable(QRect)
        self._region = QRect(region).normalized()
        self._drag = None                      # ("move", offset) / ("resize", handle)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self._place()
        C.prepare_capture_window(self)
        self.show()
        self.raise_()

    # ---------- 几何 ----------
    def region(self):
        return QRect(self._region)

    def _inner(self):
        return self.rect().adjusted(self.M, self.M, -self.M, -self.M)

    def _place(self):
        self.setGeometry(self._region.adjusted(-self.M, -self.M, self.M, self.M)
                         .translated(self._scr.topLeft()))
        self.setMask(QRegion(self.rect()).subtracted(QRegion(self._inner())))

    def _apply(self):
        self._place()
        self.update()
        self._on_change(QRect(self._region))

    # ---------- 交互（_move_to/_resize_to 独立成方法，便于自检直接驱动） ----------
    def _handles(self):
        r = self._inner()
        cy, cx = (r.top() + r.bottom()) // 2, (r.left() + r.right()) // 2
        return [QPoint(r.left(), r.top()), QPoint(cx, r.top()), QPoint(r.right(), r.top()),
                QPoint(r.left(), cy), QPoint(r.right(), cy),
                QPoint(r.left(), r.bottom()), QPoint(cx, r.bottom()),
                QPoint(r.right(), r.bottom())]

    def _hit(self, pos):
        for i, pt in enumerate(self._handles()):
            if abs(pos.x() - pt.x()) <= 7 and abs(pos.y() - pt.y()) <= 7:
                return i
        return -1

    def _to_screen_pt(self, pos):
        gp = self.mapToGlobal(pos)
        return QPoint(gp.x() - self._scr.x(), gp.y() - self._scr.y())

    def _move_to(self, sp):
        r = QRect(self._region)
        r.moveTopLeft(sp - self._drag[1])
        r.moveLeft(max(0, min(r.left(), self._scr.width() - r.width())))
        r.moveTop(max(0, min(r.top(), self._scr.height() - r.height())))
        self._region = r
        self._apply()

    def _resize_to(self, h, sp):
        r = QRect(self._region)
        if h in (0, 3, 5):
            r.setLeft(min(sp.x(), r.right() - self.MINW))
        if h in (2, 4, 7):
            r.setRight(max(sp.x(), r.left() + self.MINW - 1))
        if h in (0, 1, 2):
            r.setTop(min(sp.y(), r.bottom() - self.MINH))
        if h in (5, 6, 7):
            r.setBottom(max(sp.y(), r.top() + self.MINH - 1))
        r.setLeft(max(0, r.left()))
        r.setTop(max(0, r.top()))
        r.setRight(min(self._scr.width() - 1, r.right()))
        r.setBottom(min(self._scr.height() - 1, r.bottom()))
        self._region = r.normalized()
        self._apply()

    def mousePressEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            return
        pos = ev.position().toPoint()
        h = self._hit(pos)
        if h >= 0:
            self._drag = ("resize", h)
        else:
            self._drag = ("move", self._to_screen_pt(pos) - self._region.topLeft())

    def mouseMoveEvent(self, ev):
        pos = ev.position().toPoint()
        if not self._drag:
            h = self._hit(pos)
            self.setCursor(self._CURSORS[h] if h >= 0 else Qt.SizeAllCursor)
            return
        sp = self._to_screen_pt(pos)
        if self._drag[0] == "move":
            self._move_to(sp)
        else:
            self._resize_to(self._drag[1], sp)

    def mouseReleaseEvent(self, ev):
        self._drag = None

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        outer, inner = self.rect(), self._inner()
        strip = QPainterPath()
        strip.addRoundedRect(QRectF(outer), 8, 8)
        strip.addRect(QRectF(inner))
        strip.setFillRule(Qt.OddEvenFill)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(14, 16, 24, 232))    # 边框条深色底
        p.setClipPath(strip)
        p.drawRect(outer)
        p.setClipping(False)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(self._color), 2))  # 内侧高亮线，贴着选区外沿
        p.drawRect(inner.adjusted(1, 1, -1, -1))
        p.setPen(QPen(QColor(self._color), 1))
        p.setBrush(QColor("#ffffff"))
        for pt in self._handles():              # 8 个缩放手柄
            p.drawRoundedRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8), 2, 2)
        p.end()


class _ColorPickerCard:
    """截图框选与独立取色共用的放大镜卡片。"""

    CELLS = 15
    MAG = 180
    INFO = 96
    GAP = 22

    @classmethod
    def rect(cls, pos, width, height):
        total = cls.MAG + cls.INFO
        x, y = pos.x() + cls.GAP, pos.y() + cls.GAP
        if x + cls.MAG > width - 8:
            x = pos.x() - cls.GAP - cls.MAG
        if y + total > height - 8:
            y = pos.y() - cls.GAP - total
        return QRect(max(8, x), max(8, y), cls.MAG, total)

    @classmethod
    def draw(cls, p, image, scale, pos, rc,
             hint="左键 复制  ·  右键 / Esc 取消", copied_hex=""):
        if image.isNull():
            return
        mag = QRect(rc.x(), rc.y(), cls.MAG, cls.MAG)
        half, cell = cls.CELLS // 2, cls.MAG // cls.CELLS
        cx = min(image.width() - 1, max(0, round(pos.x() * scale)))
        cy = min(image.height() - 1, max(0, round(pos.y() * scale)))
        src = QRect(cx - half, cy - half, cls.CELLS, cls.CELLS).intersected(image.rect())
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 20, 30, 236))
        p.drawRoundedRect(rc, C.UI_RADIUS, C.UI_RADIUS)
        crop = image.copy(src)
        crop.setDevicePixelRatio(1.0)
        crop = crop.scaled(src.width() * cell, src.height() * cell,
                           Qt.IgnoreAspectRatio, Qt.FastTransformation)
        p.save()
        shell = QPainterPath()
        shell.addRoundedRect(QRectF(rc), C.UI_RADIUS, C.UI_RADIUS)
        preview = QPainterPath()
        preview.addRect(QRectF(mag))
        p.setClipPath(shell.intersected(preview))
        p.drawImage(QPoint(mag.x() + (src.x() - (cx - half)) * cell,
                           mag.y() + (src.y() - (cy - half)) * cell), crop)
        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        for i in range(1, cls.CELLS):
            p.drawLine(mag.x() + i * cell, mag.y(), mag.x() + i * cell, mag.y() + cls.MAG)
            p.drawLine(mag.x(), mag.y() + i * cell, mag.x() + cls.MAG, mag.y() + i * cell)
        # 边缘只裁掉缺失像素，取样格始终位于放大区中央。
        sample = QRect(mag.x() + half * cell, mag.y() + half * cell, cell, cell)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(10, 12, 20, 200), 3))
        p.drawRect(sample.adjusted(-1, -1, 0, 0))
        p.setPen(QPen(QColor(255, 255, 255, 240), 1.4))
        p.drawRect(sample.adjusted(-1, -1, 0, 0))
        p.restore()
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(rc).adjusted(.5, .5, -.5, -.5), C.UI_RADIUS, C.UI_RADIUS)
        iy = rc.y() + cls.MAG
        lx, rx = rc.x() + 12, rc.right() - 12
        cur = image.pixelColor(cx, cy)
        chip = QRect(lx, iy + 12, 22, 22)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(cur)
        p.drawRoundedRect(chip, 6, 6)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(14)
        font.setWeight(QFont.Bold)
        p.setFont(font)
        p.setPen(QColor(C.UI_TEXT))
        p.drawText(QRect(chip.right() + 11, chip.y(), rx - chip.right() - 11, chip.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, cur.name().upper())
        font.setPixelSize(11)
        font.setWeight(QFont.Normal)
        p.setFont(font)
        rgb = QRect(lx, iy + 42, rx - lx, 16)
        p.setPen(QColor(C.UI_TEXT3))
        p.drawText(rgb, Qt.AlignLeft | Qt.AlignVCenter, "RGB")
        p.setPen(QColor(C.UI_TEXT2))
        p.drawText(rgb, Qt.AlignRight | Qt.AlignVCenter,
                   f"{cur.red()} · {cur.green()} · {cur.blue()}")
        p.setPen(QPen(QColor(255, 255, 255, 12), 1))
        p.drawLine(lx, iy + 66, rx, iy + 66)
        copied = bool(copied_hex) and copied_hex == cur.name().upper()
        p.setPen(QColor(C.UI_BADGE_FROM if copied else C.UI_TEXT3))
        p.drawText(QRect(lx, iy + 73, rx - lx, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   "已复制到剪贴板" if copied else hint)
        p.restore()


class ColorPickerOverlay(QWidget):
    """R103 屏幕取色器：冻结屏 + 放大镜逐像素取色。
    左键＝取准星处色值 emit picked("#rrggbb")；右键/Esc/外部关窗＝取消 emit picked("")。
    取样/放大镜定位独立成 _sample_at/_mag_rect，便于自检直接驱动（同 RegionFrame 纪律）。
    R114 放大镜由定时器轮询真实光标驱动，不依赖 Qt 派发的 mouseMoveEvent：
    当非 Qt 全屏窗口（实测为系统输入法宿主 TextInputHost「Windows 输入体验」）压在覆盖层之上时，
    Windows 仍把鼠标消息投给覆盖层，但 Qt 平台层用 ChildWindowFromPointEx 找不到自身窗口，
    会丢弃全部无按键 WM_MOUSEMOVE（点击/键盘不受影响）→ 放大镜定格、看似卡死。"""

    CELLS = _ColorPickerCard.CELLS
    MAG = _ColorPickerCard.MAG
    INFO = _ColorPickerCard.INFO
    GAP = _ColorPickerCard.GAP
    TRACK_MS = 16                                # R114 光标轮询周期（≈60fps）

    picked = Signal(str)

    def __init__(self, base_pixmap, screen=None):
        super().__init__(None)
        C.snapshot_input_locale()   # R83 同纪律：抢前台前记下用户正在用的输入法语言
        self.base = base_pixmap
        self.screen_ = screen or QGuiApplication.primaryScreen()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setCursor(Qt.BlankCursor)           # 放大镜自带准星，藏系统指针
        geo = self.screen_.geometry()
        self.setGeometry(geo)
        # 实测像素比：物理帧宽 ÷ 逻辑屏宽（与 ShotOverlay 同法，不信 DPR 元数据）
        self.k = self.base.width() / max(1, geo.width())
        self.base.setDevicePixelRatio(self.k)
        self._img = self.base.toImage()          # 物理像素（取样用）
        self._img.setDevicePixelRatio(1.0)       # R151：toImage 继承 base 的 DPR=k，drawImage 按设备无关尺寸
                                                 # 再缩 1/k → 放大区只剩左上 1/k²（真机 200% 屏只见 1/4）；同 2524 清 DPR 纪律
        self._done = False
        self._silent = False
        gp = QCursor.pos()                       # 初始锚点＝真实光标（起手即瞄准）
        self._pos = QPoint(min(geo.width() - 1, max(0, gp.x() - geo.x())),
                           min(geo.height() - 1, max(0, gp.y() - geo.y())))
        self._hex = self._sample_at(self._pos)
        self.setMouseTracking(True)
        self._track = QTimer(self)               # R114：真实光标轮询，绕开 Qt 丢移动事件的路径
        self._track.setInterval(self.TRACK_MS)
        self._track.timeout.connect(self._poll_cursor)
        self._track.start()
        C.prepare_capture_window(self)
        self.show()               # 先落地，让平台窗口归属目标屏（拓展屏）
        self.showFullScreen()     # 再全屏盖住任务栏
        self.activateWindow()
        self.setFocus()

    # ---------- 取样/定位（独立成方法，便于自检直接驱动） ----------
    def _sample_at(self, pos):
        """逻辑坐标 → 物理像素色 "#rrggbb"（出界钳制到边缘像素）。"""
        dx = min(self._img.width() - 1, max(0, round(pos.x() * self.k)))
        dy = min(self._img.height() - 1, max(0, round(pos.y() * self.k)))
        return self._img.pixelColor(dx, dy).name()

    def _mag_rect(self, pos):
        """与截图框选共用卡片尺寸、跟随间距和贴边翻转。"""
        return _ColorPickerCard.rect(pos, self.width(), self.height())

    def _set_pos(self, pos):
        """准星落点唯一入口：钳屏内 → 取样 → 变了才重绘。"""
        pos = QPoint(min(self.width() - 1, max(0, pos.x())),
                     min(self.height() - 1, max(0, pos.y())))
        if pos == self._pos and self._hex:
            return
        self._pos = pos
        self._hex = self._sample_at(pos)
        self.update()

    def _poll_cursor(self):
        """R114：按周期读真实光标；光标在其它屏时本屏放大镜保持不动（同原 mouseMove 语义）。"""
        if self._done:
            return
        gp = QCursor.pos()
        geo = self.screen_.geometry()
        if geo.contains(gp):
            self._set_pos(QPoint(gp.x() - geo.x(), gp.y() - geo.y()))

    # ---------- 绘制 ----------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawPixmap(self.rect(), self.base)     # 冻结帧（DPR 感知，物理像素 1:1 上屏）
        pos = self._pos
        # 准星：白线 + 深色描边，任何底色上都看得见
        p.setPen(QPen(QColor(10, 12, 20, 160), 3))
        p.drawLine(pos.x() - 9, pos.y(), pos.x() + 9, pos.y())
        p.drawLine(pos.x(), pos.y() - 9, pos.x(), pos.y() + 9)
        p.setPen(QPen(QColor(255, 255, 255, 230), 1))
        p.drawLine(pos.x() - 9, pos.y(), pos.x() + 9, pos.y())
        p.drawLine(pos.x(), pos.y() - 9, pos.x(), pos.y() + 9)
        _ColorPickerCard.draw(p, self._img, self.k, pos, self._mag_rect(pos))
        p.end()

    # ---------- 交互 ----------
    def _finish(self, hexs):
        if self._done:
            return
        self._done = True
        self._track.stop()
        self.picked.emit(hexs)
        self.close()

    def close_silent(self):
        """多屏组收尾：静默关闭，不再 emit。"""
        self._silent = True
        self._track.stop()
        self.close()

    def closeEvent(self, ev):
        self._track.stop()
        if not self._done and not self._silent:
            self._done = True
            self.picked.emit("")                 # 窗口被外部关掉＝取消
        super().closeEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._set_pos(ev.position().toPoint())   # R114：移动事件可能从未送达，以点击落点为准重取样
            self._finish(self._hex)
        elif ev.button() == Qt.RightButton:
            self._finish("")

    def mouseMoveEvent(self, ev):
        self._set_pos(ev.position().toPoint())

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self._finish("")


class _ColorPickSession(QObject):
    """多屏取色共用一次收尾；焦点离开整组时取消，组内换屏不取消。"""

    def __init__(self, on_picked):
        super().__init__(QGuiApplication.instance())
        self.overlays = []
        self._on_picked = on_picked
        self._finished = False
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._check_focus)
        app = QGuiApplication.instance()
        app.focusWindowChanged.connect(self._focus_changed)
        app.applicationStateChanged.connect(self._focus_changed)
        app.aboutToQuit.connect(self.cancel)

    def _focus_changed(self, *_):
        if not self._finished:
            # 等待组内各屏的激活/失活事件完成，避免切屏瞬间误取消。
            self._focus_timer.start(100)

    def _check_focus(self):
        if self._finished:
            return
        app = QGuiApplication.instance()
        windows = [o.windowHandle() for o in self.overlays
                   if shiboken6.isValid(o) and o.isVisible()]
        if app.applicationState() != Qt.ApplicationActive or app.focusWindow() not in windows:
            self.cancel()

    def cancel(self, *_):
        self.finish("")

    def finish(self, value):
        if self._finished:
            return
        self._finished = True
        self._focus_timer.stop()
        # 覆盖层可能晚于会话释放；先断开反向销毁回调，避免访问已失效的 Python 包装对象。
        for ov in self.overlays:
            if shiboken6.isValid(ov):
                try:
                    ov.destroyed.disconnect(self.cancel)
                except (RuntimeError, TypeError):
                    pass
        close_all(self.overlays)
        try:
            self._on_picked(value)
        finally:
            # QObject 销毁会断开应用级信号，旧会话不能干扰下一次取色。
            self.deleteLater()


def create_colorpick_overlays(on_picked):
    """每屏独立冻结帧；取色、取消、失焦或外部关闭均统一结束整组。"""
    # 在显示任何覆盖层之前完成抓帧，失败时不遗留已打开的半组窗口。
    frames = [(scr, grab_screen(scr)) for scr in QGuiApplication.screens()]
    if not frames or any(pm.isNull() for _, pm in frames):
        raise RuntimeError("无法读取屏幕画面，请重试")
    session = _ColorPickSession(on_picked)
    try:
        for scr, pm in frames:
            ov = ColorPickerOverlay(pm, screen=scr)
            session.overlays.append(ov)
            ov.picked.connect(session.finish)
            ov.destroyed.connect(session.cancel)
        session._focus_changed()
        return session.overlays
    except Exception:
        session.cancel()
        raise
