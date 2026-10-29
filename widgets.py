# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 小组件：待办 / 日历(2026节假日) / 番茄钟 / 快记 / 剪贴板历史 / 网速。"""
import os
import json
import ctypes
import threading
import time
import winsound
from datetime import date
from ctypes import wintypes
from urllib.parse import urlsplit

from PySide6.QtCore import Qt, QTimer, QDate, QRect, QRectF, QSize, QPoint, QEvent, QObject, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QImageReader, QKeySequence, QPainter,
                           QPen, QPixmap, QShortcut, QTextCursor)
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QCalendarWidget, QCheckBox, QDialog, QFrame,
                               QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QStackedWidget, QTextEdit, QTableView,
                               QToolButton, QVBoxLayout, QWidget, QTextBrowser)

import common as C
import icons

BASE_QSS = """
QWidget{background:#181a24;color:#eef1f7;font-family:'Microsoft YaHei UI';}
QLabel{background:transparent;}
QPushButton{background:rgba(255,255,255,18);color:#fff;border:1px solid rgba(255,255,255,36);
border-radius:8px;padding:6px 14px;font-size:12.5px;}
QPushButton:hover{background:rgba(255,255,255,30);}
QLineEdit,QTextEdit{background:rgba(255,255,255,20);color:#fff;border:1px solid rgba(255,255,255,40);
border-radius:8px;padding:7px 10px;font-size:13px;}
QListWidget{background:transparent;border:none;font-size:13px;}
QListWidget::item{background:rgba(255,255,255,16);border-radius:8px;margin:3px 0;padding:10px;}
QListWidget::item:selected{background:rgba(59,130,246,90);}
"""   # R146：桌面插件五窗已迁 PLUGIN_QSS 黑透壳，本表仅剩长截图编辑器（longedit）在用


# ---------- R146 桌面插件统一壳（用户拍板 macOS HIG 唯一裁决，待统一五窗全量改造） ----------
# 照 sticky.py R137 插件范式：无边框 Tool|Frameless+WA_TranslucentBackground、root 12 阴影留白、
# body QFrame#pluginBody（R155 实底 #10141e——用户点名「独立窗口不要透明，透底显乱」，
# 原黑透 rgba(16,20,30,202) 退役；圆角 10/1px 白 18）、柔影 26/4/130、
# 大标题栏 38（标题 17/700+副信息 12/#7c8294+钉 14/叉 20 lucide 同源）、标题栏拖动、Esc 关闭、
# EdgeResizer 八向缩放。控件无描边体系（分区靠底色梯度——R134/R141/R145 判例收敛）。
# WA_TranslucentBackground 保留——圆角与自绘阴影所需，非「透明窗体」。
PLUGIN_QSS = """
QFrame#pluginBody { background:#10141e; border-radius:10px;
  border:1px solid rgba(255,255,255,18); }
QLabel { background:transparent; color:#eef1f7; }
QToolButton#headBtn { background:transparent; border:none; color:#9ca3af;
  font-size:13px; padding:2px 6px; border-radius:6px; }
QToolButton#headBtn:hover { background:rgba(255,255,255,14); color:#ffffff; }
QToolButton#headBtn:checked { color:#60a5fa; background:rgba(96,165,250,26); }
QPushButton { background:rgba(255,255,255,10); color:#eef1f7; border:none;
  border-radius:6px; padding:6px 14px; font-size:12.5px; }
QPushButton:hover { background:rgba(255,255,255,18); }
QPushButton:pressed { background:rgba(255,255,255,24); }
QPushButton#primaryBtn { background:#3b82f6; color:#ffffff; font-weight:600; }
QPushButton#primaryBtn:hover { background:#4a90f9; }
QPushButton#primaryBtn:pressed { background:#2f6fd6; }
QLineEdit, QTextEdit, QPlainTextEdit { background:rgba(255,255,255,8); color:#eef1f7;
  border:1px solid rgba(255,255,255,28); border-radius:10px; padding:8px 12px;
  font-size:13px; selection-background-color:rgba(59,130,246,120); }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border:1px solid rgba(96,165,250,160); }
QListWidget { background:transparent; border:none; font-size:13px; outline:0; }
QListWidget::item { background:transparent; border-radius:9px; margin:2px 4px; padding:9px 12px;
  color:#eef1f7; }
QListWidget::item:hover { background:rgba(255,255,255,26); }
QListWidget::item:selected { background:rgba(59,130,246,105); }
QScrollArea { background:transparent; border:none; }
QScrollBar:vertical { background:transparent; width:6px; margin:2px; }
QScrollBar::handle:vertical { background:rgba(255,255,255,42); border-radius:3px; min-height:30px; }
QScrollBar::handle:vertical:hover { background:rgba(255,255,255,84); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
"""


class _DragFilter(QObject):
    """R146 统一壳标题栏拖动（各窗不再各写 eventFilter）。"""

    def __init__(self, win):
        super().__init__(win)
        self._win = win
        self._off = None

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._off = ev.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
        elif ev.type() == QEvent.MouseMove and self._off is not None:
            self._win.move(ev.globalPosition().toPoint() - self._off)
        elif ev.type() == QEvent.MouseButtonRelease:
            self._off = None
        return False


class _PluginHeaderIconButton(QToolButton):
    """插件窗标题图标钮：悬停换亮色图标，并在移动事件中重申手型光标。"""

    def __init__(self, name, size, parent=None, checkable=False):
        super().__init__(parent)
        self._icon_name = name
        self._icon_size = size
        self._hovered = False
        self._selected = False
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setIconSize(QSize(size, size))
        self._sync_icon()

    def _sync_icon(self):
        color = ("#93c5fd" if self._hovered else "#60a5fa") if self._selected \
            else ("#eef1f7" if self._hovered else "#9ca3af")
        self._cur = icons.lucide_icon(self._icon_name, color, self._icon_size)
        self.setIcon(self._cur)

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._sync_icon()

    def enterEvent(self, ev):
        self._hovered = True
        self.setCursor(Qt.PointingHandCursor)
        self._sync_icon()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hovered = False
        self._sync_icon()
        super().leaveEvent(ev)

    def mouseMoveEvent(self, ev):
        # EdgeResizer 会在顶层窗上切光标；鼠标位于按钮内时重申按钮自己的手型。
        self.setCursor(Qt.PointingHandCursor)
        super().mouseMoveEvent(ev)


class _HoverLucidePushButton(QPushButton):
    """QPushButton 图标钮：QSS 无法改写已着色 QIcon，悬停时直接换亮色图标。"""

    def __init__(self, name, size, parent=None):
        super().__init__(parent)
        self._icon_idle = icons.lucide_icon(name, "#9ca3af", size)
        self._icon_hover = icons.lucide_icon(name, "#eef1f7", size)
        self._hovered = False
        self.setIconSize(QSize(size, size))
        self.setIcon(self._icon_idle)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)

    def _set_hovered(self, hovered):
        hovered = bool(hovered)
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self.setIcon(self._icon_hover if hovered else self._icon_idle)
        self.update()

    def event(self, ev):
        # Windows 上浮动窗的 Enter/Hover 顺序会随合成器变化；两种事件及真实
        # MouseMove 都同步状态，避免只收到其中一种时图标/底色仍停在常态。
        t = ev.type()
        if t in (QEvent.Enter, QEvent.HoverEnter, QEvent.HoverMove, QEvent.MouseMove):
            self._set_hovered(True)
            self.setCursor(Qt.PointingHandCursor)
        elif t in (QEvent.Leave, QEvent.HoverLeave):
            self._set_hovered(False)
        return super().event(ev)

    def mouseMoveEvent(self, ev):
        self.setCursor(Qt.PointingHandCursor)
        self._set_hovered(True)
        super().mouseMoveEvent(ev)


class _UtilityWindowStateFilter(QObject):
    def eventFilter(self, win, event):
        if event.type() == QEvent.Show:
            C.ensure_utility_taskbar_entry(win)
        if (event.type() == QEvent.WindowStateChange
                and event.oldState() & Qt.WindowMinimized and not win.isMinimized()
                and (getattr(win, '_pinned', False) or win.windowFlags() & Qt.WindowStaysOnTopHint)):
            QTimer.singleShot(0, lambda: C.set_topmost(win, True))
        return False


def add_minimize_button(win, layout, size=34):
    button = _PluginHeaderIconButton("minus", 16, layout.parentWidget())
    button.setObjectName("windowMinimizeButton")
    button.setFixedSize(size, size)
    button.setToolTip("最小化")
    button.setAccessibleName("最小化")
    button.setStyleSheet(
        "QToolButton#windowMinimizeButton{background:transparent;border:none;padding:0;border-radius:6px;}"
        "QToolButton#windowMinimizeButton:hover{background:rgba(255,255,255,20);}"
        "QToolButton#windowMinimizeButton:pressed{background:rgba(255,255,255,30);}")
    button.clicked.connect(win.showMinimized)
    layout.addWidget(button)
    win.btn_minimize = button
    win.setWindowIcon(C.app_qicon())
    win._utility_state_filter = _UtilityWindowStateFilter(win)
    win.installEventFilter(win._utility_state_filter)
    return button


def _plugin_shell(win, title, sub="", on_hide=None, min_size=(280, 240)):
    """R146 五窗统一壳装配：无边框黑透插件（标题栏/钉×/拖动/Esc/阴影/八向缩放）。
    返回 (body_layout, sub_lbl)——内容控件往 body_layout 装，副信息（计数等）写 sub_lbl。"""
    win.setWindowFlags(C.utility_window_flags())
    win.setAttribute(Qt.WA_TranslucentBackground, True)
    win._pinned = False                                       # 钉态真相源（_toggle_pin 翻转）
    win.setMinimumSize(*min_size)
    root = QVBoxLayout(win)
    root.setContentsMargins(12, 12, 12, 12)                   # 四边留白给自绘阴影
    body = QFrame(win)
    body.setObjectName("pluginBody")
    sh = QGraphicsDropShadowEffect(win)                       # 无边框窗无系统阴影：自绘提升层次感
    sh.setBlurRadius(26)
    sh.setOffset(0, 4)
    sh.setColor(QColor(0, 0, 0, 130))
    body.setGraphicsEffect(sh)
    root.addWidget(body)
    bl = QVBoxLayout(body)
    bl.setContentsMargins(12, 6, 12, 10)
    bl.setSpacing(6)

    head = QWidget(body)
    head.setFixedHeight(38)
    hl = QHBoxLayout(head)
    hl.setContentsMargins(4, 0, 0, 0)
    hl.setSpacing(8)
    t = QLabel(title, head)
    t.setStyleSheet("color:#eef1f7;font-size:17px;font-weight:700;background:transparent;")
    hl.addWidget(t)
    sub_lbl = QLabel(sub, head)
    sub_lbl.setStyleSheet("color:#7c8294;font-size:12px;background:transparent;")
    hl.addWidget(sub_lbl)
    hl.addStretch()
    pin = _PluginHeaderIconButton("pin", 14, head, checkable=True)
    pin.setObjectName("headBtn")
    pin.setFixedSize(34, 34)
    pin.setChecked(False)                                     # R150：默认不钉
    pin.setToolTip("置顶：钉在最前 / 再点取消")

    def _toggle_pin(on):
        win._pinned = on
        C.set_topmost(win, on)                                # R150：Win32 换 Z 序句柄不重建零闪烁（原 setWindowFlag+show 必闪）
        pin.set_selected(on)

    pin.toggled.connect(_toggle_pin)
    hl.addWidget(pin)
    add_minimize_button(win, hl)
    x = _PluginHeaderIconButton("x", 16, head)
    x.setObjectName("headBtn")
    x.setFixedSize(34, 34)
    x.setToolTip("关闭（Esc）")
    x.clicked.connect(on_hide or win.close)
    hl.addWidget(x)
    bl.addWidget(head)

    drag = _DragFilter(win)                                   # 标题栏拖动（父子关系保活）
    head.installEventFilter(drag)
    esc = QShortcut(QKeySequence("Esc"), win)                 # Esc 关闭（窗级上下文）
    esc.setContext(Qt.WindowShortcut)
    esc.activated.connect(on_hide or win.close)
    win._edge_resizer = C.EdgeResizer(win, *min_size)         # 八向缩放（引用保活）
    return bl, sub_lbl


# ---------- 待办 ----------
class TodoWindow(QWidget):
    """R146 mac 化：统一黑透插件壳（原系统标题栏+BASE_QSS 实底描边体系退役）；
    勾选框复用 sticky._RowCheck 自绘矢量框（DPR 锐利，与任务清单同源）；
    回车添加（添加钮退役——mac 提醒事项范式，输入即回车）。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowTitle(f"{C.APP_NAME} · 待办")
        self.resize(400, 460)
        bl, self.cnt, = _plugin_shell(self, "待办")
        self.setStyleSheet(PLUGIN_QSS)
        self.inp = QLineEdit()
        self.inp.setPlaceholderText("新建待办，回车添加")
        self.inp.returnPressed.connect(self.add)
        bl.addWidget(self.inp)
        self.listw = QListWidget()
        self.listw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 长文本截断不横滚（提醒事项范式）
        bl.addWidget(self.listw, 1)
        self.items = C.load_todos()
        self.render()
        QTimer.singleShot(50, self.inp.setFocus)

    def render(self):
        from sticky import _RowCheck                    # R146 自绘矢量勾选框同源（局部 import 防顶层环）
        self.listw.clear()
        n_left = 0
        for i, it in enumerate(self.items):
            w = QWidget()
            w.setFixedHeight(34)           # R146 修复：行不定高会被 QListWidget 布局压成 0 高（首开列表空白）；mac 提醒事项行高统一
            wl = QHBoxLayout(w)
            wl.setContentsMargins(4, 0, 4, 0)
            cb = _RowCheck(w, it.get("done", False))
            cb.toggled.connect(lambda v, ix=i: self.toggle(ix, v))
            lbl = QLabel(it["t"])
            lbl.setStyleSheet("font-size:13px;")
            if it.get("done"):
                lbl.setStyleSheet("font-size:13px;color:#6b7280;text-decoration:line-through;")
            else:
                n_left += 1
            wl.addWidget(cb)
            wl.addWidget(lbl, 1)
            bi = QListWidgetItem()
            bi.setSizeHint(QSize(-1, 34))
            self.listw.addItem(bi)
            self.listw.setItemWidget(bi, w)
        self.cnt.setText(f"剩余 {n_left} 项")
        C.save_todos(self.items)

    def toggle(self, ix, v):
        self.items[ix]["done"] = bool(v)
        self.render()

    def add(self):
        t = self.inp.text().strip()
        if t:
            self.items.insert(0, {"t": t, "done": False})
            self.inp.clear()
            self.render()


# ---------- 日历（2026 法定节假日/调休角标） ----------
class HolidayCalendar(QCalendarWidget):
    """整格自绘：大号数字统一竖向节奏，休/班角标压在数字正下方成组；选中蓝底、
    今日蓝环微蓝底、非本月置灰。导航条隐藏，由 CalendarWindow 自绘年月选择与翻页。"""

    def __init__(self, marks):
        super().__init__()
        self._marks = marks          # QDate -> (节日名, 是否休)

    def paintCell(self, painter, rect, date):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        h = self._marks.get(date)
        sel = date == self.selectedDate()
        today = date == QDate.currentDate()
        other = date.month() != self.monthShown()
        body = rect.adjusted(3, 3, -3, -3)     # R90：格子多一分呼吸
        if sel:                              # 选中日：实心蓝圆角底（全局选中色 #3b82f6）
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#3b82f6"))
            painter.drawRoundedRect(body, 8, 8)
        elif today:                          # 今日：蓝描边 + 微蓝底，比描边圈更醒目
            painter.setPen(QPen(QColor("#3b82f6"), 1.6))
            painter.setBrush(QColor(59, 130, 246, 36))
            painter.drawRoundedRect(body, 8, 8)
        # 日期数字：全部格子同一竖向节奏（略高于中心），整表对齐不散
        if sel:
            col = QColor("#ffffff")
        elif h and h[1]:
            col = QColor("#f87171")
        elif h:
            col = QColor("#22d3ee")
        elif other:
            col = QColor("#525868")
        elif date.dayOfWeek() >= 6:
            col = QColor("#e08e8e")
        else:
            col = QColor("#e6eaf3")
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(21)
        f.setBold(bool(h) or sel or today)
        painter.setFont(f)
        painter.setPen(col)
        num_top = rect.top() + int(rect.height() * 0.20)
        painter.drawText(QRect(rect.left(), num_top, rect.width(), 30),
                         Qt.AlignCenter, str(date.day()))
        if h:                                # 角标压在数字正下方，与数字成组
            side = 20
            by = min(num_top + 34, rect.bottom() - side - 2)
            badge = QRect(rect.left() + (rect.width() - side) // 2, by, side, side)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#ef4444") if h[1] else QColor("#0891b2"))
            painter.drawRoundedRect(badge, 6, 6)
            painter.setPen(QColor("#ffffff"))
            bf = QFont("Microsoft YaHei UI")
            bf.setPixelSize(13)
            bf.setBold(True)
            painter.setFont(bf)
            painter.drawText(badge, Qt.AlignCenter, "休" if h[1] else "班")
        painter.restore()


class _CalendarPicker(QFrame):
    """年/月选择面板：显式绘制实色底，透明只保留在圆角外部。"""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(C.UI_POPUP_BG))
        painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)


class CalendarWindow(QWidget):
    """R90 重设计：无边框半透插件壳，与任务清单同一语言——自绘阴影卡片（令牌色）、
    徽章标题栏（兼拖动把手）、钉置顶（默认钉：兼容原置顶工具窗行为）、×/Esc 关闭。
    导航四矢量箭头/今天钮保留，年月点击弹出网格选择器，角标与选中格色值保持。
    R148 顶部呼吸重排（用户点名导航样式与三处间距别扭）：导航行 26→30、页码 15→16px、
    导航↔星期头 +8 呼吸位、星期头行 26→32（文字上下各 ~10px）、窗高 710→728 全额投给间距。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowTitle(f"{C.APP_NAME} · 日历")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(C.utility_window_flags())
        self._pinned = False                                  # 钉态真相源（toggle_pin 翻转）
        self.setFixedSize(604, 728)          # 固定尺寸：防止拉大后网格行高被撑爆、角标与数字脱节；R148 +18 全投顶部呼吸
        self._drag_off = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)     # 四边留白给自绘阴影
        root.setSpacing(0)
        self.body = QFrame(self)
        self.body.setObjectName("calBody")
        _shadow = QGraphicsDropShadowEffect(self)   # 无边框窗无系统阴影：自绘提升层次感
        _shadow.setBlurRadius(26)
        _shadow.setOffset(0, 5)
        _shadow.setColor(QColor(0, 0, 0, 130))
        self.body.setGraphicsEffect(_shadow)
        root.addWidget(self.body)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)

        # 标题栏（拖动把手）：徽章 + 标题 + 副标 + 钉/×
        self.head = QWidget(self.body)
        self.head.setFixedHeight(34)
        self.head.installEventFilter(self)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(2, 0, 0, 0)
        hl.setSpacing(8)
        badge = QLabel("历", self.head)
        badge.setFixedSize(22, 22)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                            " stop:0 #60a5fa, stop:1 #2563eb); color:#ffffff;"
                            " font-size:11px; font-weight:700; border-radius:7px;")
        hl.addWidget(badge)
        self.title = QLabel("日历", self.head)
        self.title.setStyleSheet("color:#f9fafb; font-size:14px; font-weight:600;"
                                 " background:transparent;")
        hl.addWidget(self.title)
        sub = QLabel("节假日与调休", self.head)
        sub.setStyleSheet("color:#6b7280; font-size:11px; background:transparent;")
        hl.addWidget(sub)
        hl.addStretch()
        self.btn_pin = self._head_btn("", "置顶：钉在最前 / 再点取消", checkable=True)
        self.btn_pin.setIconSize(QSize(14, 14))          # 钉图标：与任务清单同款矢量
        self.btn_pin.clicked.connect(self.toggle_pin)
        self._sync_pin_icon(False)                       # R150：默认不钉（用户点名：钉由用户手动点）
        add_minimize_button(self, hl, 26)
        self.btn_close = self._head_btn("", "关闭（Esc）")
        self.btn_close.setIcon(icons.lucide_icon("x", "#9ca3af", 16))   # R149：18→16 与任务清单新叉全局一致（用户点名「日历改成跟这个一致」）
        self.btn_close.setIconSize(QSize(16, 16))
        self.btn_close.clicked.connect(self.close)
        lay.addWidget(self.head)

        self.setStyleSheet(f"""
        QFrame#calBody {{ background:{C.UI_BG_CARD}; border-radius:{C.UI_RADIUS}px;
          border:1px solid {C.UI_BORDER}; }}
        QLabel {{ background:transparent; color:{C.UI_TEXT}; font-family:'Microsoft YaHei UI'; }}
        QToolButton {{ background:transparent; border:none; color:{C.UI_TEXT2};
          font-size:13px; padding:2px 6px; border-radius:6px; }}
        QToolButton:hover {{ background:rgba(255,255,255,26); color:{C.UI_TEXT}; }}
        QToolButton:checked {{ color:#60a5fa; background:rgba(59,130,246,30); }}
        QCalendarWidget QAbstractItemView{{background:transparent;outline:0;
          selection-background-color:transparent;font-family:'Microsoft YaHei UI';}}
        QCalendarWidget #qt_calendar_calendarview::item,
        QCalendarWidget #qt_calendar_calendarview::item:selected{{background:transparent;}}
        """)

        self._marks = {}
        for dstr, name, off in C.HOLIDAYS_ALL:
            y, m, d = (int(x) for x in dstr.split("-"))
            self._marks[QDate(y, m, d)] = (name, bool(off))

        # 自绘导航：四个步进按钮保留；年份、月份中间值本身也可点击选择。
        self.cal = HolidayCalendar(self._marks)
        self._year_min = self.cal.minimumDate().year()
        self._year_max = self.cal.maximumDate().year()
        self._year_picker_last_start = ((self._year_max - 1) // 12) * 12 + 1
        self.cal.setDateEditEnabled(False)
        self.cal.setGridVisible(False)       # 去网格线：靠留白分隔，去表格感
        self.cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.cal.setNavigationBarVisible(False)
        self.cal.setAutoFillBackground(False)    # R148：cal 壳 autoFill 用 palette Base 填底（offscreen/fusion 下露白版），QSS 管不到，代码级关（R95/R146 同判例）
        # 表头行（模型第 0 行）默认与日期行一起被 Stretch 均分视口高度（7×79px），
        # 周一…周日孤悬半空、6 行月的角标被挤到贴底信息行——钉成矮条，日期行吃满剩余
        _view = self.cal.findChild(QTableView, "qt_calendar_calendarview")
        if _view is not None:
            _vh = _view.verticalHeader()
            _vh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)   # 关 Stretch：表头不再均分
            _vh.setMinimumSectionSize(22)
            _vh.setDefaultSectionSize(87)                    # 模型翻页重置时新行也取此高
            _vh.resizeSection(0, 32)                         # R148：表头行 26→32——星期头上下各 ~10px 呼吸（原矮条与导航行贴死）
            for _r in range(1, 7):
                _vh.resizeSection(_r, 87)                    # 6 个日期行：32+6×87=554≤视口（728 窗高预算内）

        def nav_btn(txt, tip, cb, wide=30):
            b = QPushButton(txt)
            b.setFixedSize(wide, 30)             # R148：导航行高 26→30（原 26 扁平挤压，箭头与页码喘不过气）
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton{background:transparent;color:#a7adbc;border:none;"
                            "border-radius:7px;font-size:15px;font-weight:600;}"
                            "QPushButton:hover{background:rgba(255,255,255,22);color:#eef1f7;}"
                            "QPushButton:pressed{background:rgba(59,130,246,90);color:#ffffff;}")
            b.clicked.connect(cb)
            return b

        nav = QHBoxLayout()
        nav.setSpacing(2)
        nav.addSpacing(58)                   # 右侧多 今天钮 52+间距6：左侧补等宽，页码标题真居中
        # R137：导航箭头 lucide 化（程序化 chevl 系小窗退役，全窗图标同源 lucide）
        nav.addWidget(icons.tool_button(None, "上一年", self.cal.showPreviousYear,
                                        color="#a7adbc", w=30, h=30, lucide="chevrons-left"))
        nav.addWidget(icons.tool_button(None, "上一月", self.cal.showPreviousMonth,
                                        color="#a7adbc", w=30, h=30, lucide="chevron-left"))
        self.page_lbl = QWidget(self.body)
        self.page_lbl.setFixedHeight(30)
        self.page_lbl.setStyleSheet("background:transparent;font-size:16px;")
        _page_lay = QHBoxLayout(self.page_lbl)
        _page_lay.setContentsMargins(0, 0, 0, 0)
        _page_lay.setSpacing(3)
        _page_lay.addStretch(1)
        _picker_btn_qss = (
            "QPushButton{background:transparent;color:#eef1f7;border:none;"
            "border-radius:7px;padding:0 6px;font-size:16px;font-weight:700;}"
            "QPushButton:hover{background:rgba(255,255,255,18);color:#ffffff;}"
            "QPushButton:pressed{background:rgba(59,130,246,90);color:#ffffff;}")
        self.year_btn = QPushButton("", self.page_lbl)
        self.year_btn.setObjectName("calYearPicker")
        self.year_btn.setToolTip("选择年份")
        self.year_btn.setCursor(Qt.PointingHandCursor)
        self.year_btn.setFixedHeight(28)
        self.year_btn.setStyleSheet(_picker_btn_qss)
        self.year_btn.clicked.connect(self._open_year_picker)
        _page_lay.addWidget(self.year_btn)
        self.month_btn = QPushButton("", self.page_lbl)
        self.month_btn.setObjectName("calMonthPicker")
        self.month_btn.setToolTip("选择月份")
        self.month_btn.setCursor(Qt.PointingHandCursor)
        self.month_btn.setFixedHeight(28)
        self.month_btn.setStyleSheet(_picker_btn_qss)
        self.month_btn.clicked.connect(self._open_month_picker)
        _page_lay.addWidget(self.month_btn)
        _page_lay.addStretch(1)
        self._year_picker = None
        self._month_picker = None
        self._year_picker_start = 1
        nav.addWidget(self.page_lbl, 1)
        nav.addWidget(icons.tool_button(None, "下一月", self.cal.showNextMonth,
                                        color="#a7adbc", w=30, h=30, lucide="chevron-right"))
        nav.addWidget(icons.tool_button(None, "下一年", self.cal.showNextYear,
                                        color="#a7adbc", w=30, h=30, lucide="chevrons-right"))
        nav.addSpacing(6)
        # 宽度须 ≥ sizeHint：13px 加粗“今天”要 54，压窄会裁字——46 时左侧被切掉一截
        self.today_btn = nav_btn("今天", "回到今天", self._go_today, 52)
        self.today_btn.setStyleSheet(
            "QPushButton{background:rgba(59,130,246,36);color:#9ec1ff;border:none;"
            "border-radius:7px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{background:rgba(59,130,246,70);color:#ffffff;}"
            "QPushButton:pressed{background:#3b82f6;color:#ffffff;}")
        nav.addWidget(self.today_btn)
        lay.addLayout(nav)
        lay.addSpacing(8)     # R148：导航↔星期头呼吸位（nav 恒显，永久 spacing 安全；原 6px 贴死，用户点名别扭）

        self._style_header()
        lay.addWidget(self.cal, 1)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#dfe4ef;font-size:13px;font-weight:500;"
                                "background:transparent;border:none;")
        lay.addWidget(self.info)
        leg = QLabel("红「休」= 法定节假日　·　青「班」= 调休上班日　·　点日期看节日名称")
        leg.setStyleSheet("color:#6f7688;font-size:11px;background:transparent;border:none;")
        lay.addWidget(leg)
        self.cal.clicked.connect(self._show_day)
        self.cal.currentPageChanged.connect(self._sync_page)
        self._sync_page(self.cal.yearShown(), self.cal.monthShown())
        self._show_day(date.today())

    def _head_btn(self, text, tip, checkable=False):
        b = QToolButton(self.head)
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFixedSize(26, 26)
        b.setCursor(Qt.PointingHandCursor)
        self.head.layout().addWidget(b)
        return b

    def _sync_pin_icon(self, on):
        """钉选中=蓝图标，未选=灰图标；checked 与置顶标志同源（同任务清单 R88）。"""
        self.btn_pin.setChecked(on)
        self.btn_pin.setIcon(icons.lucide_icon("pin", "#60a5fa" if on else "#9ca3af", 14))   # R137：日历钉与任务清单钉同源（lucide pin 14px 同色板）

    def toggle_pin(self):
        on = not self._pinned
        self._pinned = on
        C.set_topmost(self, on)                    # R150：Win32 换 Z 序句柄不重建零闪烁（原 setWindowFlag+show 必闪，用户点名）
        self._sync_pin_icon(on)

    def _new_calendar_picker(self):
        popup = _CalendarPicker(self, Qt.Popup | Qt.FramelessWindowHint)
        popup.setObjectName("calPicker")
        popup.setAttribute(Qt.WA_TranslucentBackground, True)  # 仅供圆角外轮廓透明；面板内容使用实色底
        popup.setStyleSheet("""
        QFrame#calPicker { background:__CAL_PICKER_BG__; border:1px solid rgba(255,255,255,18);
          border-radius:8px; }
        QLabel { background:transparent; color:#9ca3af; font-family:'Microsoft YaHei UI';
          font-size:12px; font-weight:600; }
        QToolButton#calPickerNav { background:transparent; color:#9ca3af; border:none;
          border-radius:6px; min-width:28px; max-width:28px; min-height:28px; max-height:28px; }
        QToolButton#calPickerNav:hover { background:rgba(255,255,255,18); color:#ffffff; }
        QToolButton#calPickerCell { background:transparent; color:#e5e7eb; border:none;
          border-radius:6px; min-width:46px; max-width:46px; min-height:32px; max-height:32px;
          padding:0 3px; font-family:'Microsoft YaHei UI'; font-size:12px; }
        QToolButton#calPickerCell:hover { background:rgba(255,255,255,18); color:#ffffff; }
        QToolButton#calPickerCell:checked { background:#3b82f6; color:#ffffff; font-weight:600; }
        QToolButton#calPickerCell:disabled { color:#4b5563; }
        """.replace("__CAL_PICKER_BG__", C.UI_POPUP_BG))
        return popup

    def _picker_header(self, popup, title, arrows=False):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        if arrows:
            prev = QToolButton(popup)
            prev.setObjectName("calPickerNav")
            prev.setIcon(icons.lucide_icon("chevron-left", "#9ca3af", 14))
            prev.setIconSize(QSize(14, 14))
            prev.setCursor(Qt.PointingHandCursor)
            row.addWidget(prev)
        label = QLabel(title, popup)
        label.setObjectName("calPickerTitle")
        label.setAlignment(Qt.AlignCenter)
        row.addWidget(label, 1)
        if arrows:
            nxt = QToolButton(popup)
            nxt.setObjectName("calPickerNav")
            nxt.setIcon(icons.lucide_icon("chevron-right", "#9ca3af", 14))
            nxt.setIconSize(QSize(14, 14))
            nxt.setCursor(Qt.PointingHandCursor)
            row.addWidget(nxt)
            return row, label, prev, nxt
        return row, label, None, None

    def _create_year_picker(self):
        popup = self._new_calendar_picker()
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(9, 8, 9, 9)
        layout.setSpacing(7)
        header, label, prev, nxt = self._picker_header(popup, "", arrows=True)
        layout.addLayout(header)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        buttons = []
        for i in range(12):
            button = QToolButton(popup)
            button.setObjectName("calPickerCell")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, index=i: self._select_picker_year(index))
            grid.addWidget(button, i // 4, i % 4)
            buttons.append(button)
        layout.addLayout(grid)
        prev.clicked.connect(lambda: self._shift_year_picker(-12))
        nxt.clicked.connect(lambda: self._shift_year_picker(12))
        self._year_picker = popup
        self._year_picker_title = label
        self._year_picker_prev = prev
        self._year_picker_next = nxt
        self._year_picker_buttons = buttons
        return popup

    def _refresh_year_picker(self):
        if self._year_picker is None:
            return
        start = self._year_picker_start
        end = min(start + 11, self._year_max)
        self._year_picker_title.setText(f"{start} — {end}")
        current = self.cal.yearShown()
        for i, button in enumerate(self._year_picker_buttons):
            year = start + i
            valid = self._year_min <= year <= self._year_max
            button.setText(str(year) if valid else "")
            button.setEnabled(valid)
            button.setChecked(year == current)
        self._year_picker_prev.setEnabled(start > self._year_min)
        self._year_picker_next.setEnabled(start < self._year_picker_last_start)

    def _shift_year_picker(self, delta):
        self._year_picker_start = max(self._year_min, min(
            self._year_picker_last_start, self._year_picker_start + delta))
        self._refresh_year_picker()

    def _create_month_picker(self):
        popup = self._new_calendar_picker()
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(9, 8, 9, 9)
        layout.setSpacing(7)
        header, label, _, _ = self._picker_header(popup, "选择月份")
        layout.addLayout(header)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        buttons = []
        for i in range(12):
            month = i + 1
            button = QToolButton(popup)
            button.setObjectName("calPickerCell")
            button.setText(f"{month} 月")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, value=month: self._select_picker_month(value))
            grid.addWidget(button, i // 4, i % 4)
            buttons.append(button)
        layout.addLayout(grid)
        self._month_picker = popup
        self._month_picker_buttons = buttons
        return popup

    def _position_picker(self, popup, anchor):
        popup.adjustSize()
        anchor_top = anchor.mapToGlobal(QPoint(0, 0))
        anchor_bottom = anchor.mapToGlobal(QPoint(0, anchor.height()))
        x, y = anchor_top.x(), anchor_bottom.y() + 5
        screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center()))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - popup.width() + 1))
            if y + popup.height() > area.bottom() + 1:
                y = anchor_top.y() - popup.height() - 5
            y = max(area.top(), min(y, area.bottom() - popup.height() + 1))
        popup.move(x, y)
        popup.show()
        popup.raise_()
        popup.activateWindow()

    def _open_year_picker(self):
        if self._month_picker is not None:
            self._month_picker.hide()
        if self._year_picker is None:
            self._create_year_picker()
        year = self.cal.yearShown()
        self._year_picker_start = max(self._year_min, ((year - 1) // 12) * 12 + 1)
        self._refresh_year_picker()
        self._position_picker(self._year_picker, self.year_btn)

    def _open_month_picker(self):
        if self._year_picker is not None:
            self._year_picker.hide()
        if self._month_picker is None:
            self._create_month_picker()
        month = self.cal.monthShown()
        for i, button in enumerate(self._month_picker_buttons, start=1):
            button.setChecked(i == month)
        self._position_picker(self._month_picker, self.month_btn)

    def _select_picker_year(self, index):
        year = self._year_picker_start + index
        if self._year_min <= year <= self._year_max:
            self._year_picker.hide()
            self.cal.setCurrentPage(year, self.cal.monthShown())

    def _select_picker_month(self, month):
        self._month_picker.hide()
        self.cal.setCurrentPage(self.cal.yearShown(), month)

    def eventFilter(self, obj, ev):
        if obj is self.head:                         # 标题栏拖动（同任务清单）
            t = ev.type()
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._drag_off = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if t == QEvent.MouseMove and self._drag_off is not None:
                self.move(ev.globalPosition().toPoint() - self._drag_off)
                return True
            if t == QEvent.MouseButtonRelease and self._drag_off is not None:
                self._drag_off = None
                return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()                             # 无边框窗：Esc 即关
            return
        super().keyPressEvent(ev)

    def _sync_page(self, y, m):
        self.year_btn.setText(f"{y} 年  ▾")
        self.month_btn.setText(f"{m} 月  ▾")
        self._refresh_year_picker()
        if self._month_picker is not None:
            for i, button in enumerate(self._month_picker_buttons, start=1):
                button.setChecked(i == m)
        if y not in C.HOL_YEARS:
            if y > QDate.currentDate().year():
                self.info.setText(f"{y} 年放假安排暂未公布 · 公布后随版本更新")
            else:
                self.info.setText(f"{y} 年节假日与调休数据未收录")

    def _go_today(self):
        t = QDate.currentDate()
        self.cal.setSelectedDate(t)
        self.cal.setCurrentPage(t.year(), t.month())
        self._show_day(date.today())

    def _style_header(self):
        from PySide6.QtGui import QTextCharFormat
        for dow, weekend in ((Qt.Monday, False), (Qt.Tuesday, False), (Qt.Wednesday, False),
                             (Qt.Thursday, False), (Qt.Friday, False), (Qt.Saturday, True),
                             (Qt.Sunday, True)):
            hf = QTextCharFormat()
            hf.setForeground(QColor("#e08e8e" if weekend else "#9aa1b3"))
            hf.setFontPointSize(11)
            self.cal.setWeekdayTextFormat(dow, hf)

    def _show_day(self, d):
        if not isinstance(d, QDate):          # 兼容 python date（.year 是属性）与 QDate（.year() 方法）
            d = QDate(d.year, d.month, d.day)
        key = f"{d.year():04d}-{d.month():02d}-{d.day():02d}"
        h = C.HOL_MAP.get(key)
        if h:
            tag = "休" if h[2] else "班（调休上班）"
            self.info.setText(f"{key} · {h[1]} · {tag}")
        else:
            self.info.setText(f"{key} · 普通日期")


# ---------- 全局基准：模态卡片对话（R91，MASTER §4 模态卡片） ----------
class CardDialog(QDialog):
    """无边框半透模态对话壳：徽章标题栏（兼拖动把手）＋ ×/Esc 关闭，
    内容区由子类填 self.content_lay。模态短生命周期不带钉；插件窗壳语言的对话版。"""

    def __init__(self, glyph, title, subtitle="", on_top=True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{C.APP_NAME} · {title}")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(C.utility_window_flags(on_top))
        self._drag_off = None
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)     # 四边留白给自绘阴影
        root.setSpacing(0)
        self.body = QFrame(self)
        self.body.setObjectName("cardBody")
        _shadow = QGraphicsDropShadowEffect(self)
        _shadow.setBlurRadius(26)
        _shadow.setOffset(0, 5)
        _shadow.setColor(QColor(0, 0, 0, 130))
        self.body.setGraphicsEffect(_shadow)
        root.addWidget(self.body)
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(12, 8, 12, 12)
        bl.setSpacing(8)
        # 标题栏（拖动把手）：徽章 + 标题 + 副标 + ×
        self.head = QWidget(self.body)
        self.head.setFixedHeight(34)
        self.head.installEventFilter(self)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(2, 0, 0, 0)
        hl.setSpacing(8)
        badge = QLabel(glyph, self.head)
        badge.setFixedSize(22, 22)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                            " stop:0 #60a5fa, stop:1 #2563eb); color:#ffffff;"
                            " font-size:11px; font-weight:700; border-radius:7px;")
        hl.addWidget(badge)
        self.title = QLabel(title, self.head)
        self.title.setStyleSheet("color:#f9fafb; font-size:14px; font-weight:600;"
                                 " background:transparent;")
        hl.addWidget(self.title)
        if subtitle:
            sub = QLabel(subtitle, self.head)
            sub.setStyleSheet("color:#6b7280; font-size:11px; background:transparent;")
            hl.addWidget(sub)
        hl.addStretch()
        add_minimize_button(self, hl, 26)
        x = QToolButton(self.head)
        x.setIcon(icons.lucide_icon("x", "#9ca3af", 16))          # R149：18→16 全局同尺寸（「其他弹窗叉同时改」）
        x.setIconSize(QSize(16, 16))
        x.setToolTip("关闭（Esc）")
        x.setFixedSize(26, 26)
        x.setCursor(Qt.PointingHandCursor)
        x.clicked.connect(self.reject)
        hl.addWidget(x)
        bl.addWidget(self.head)
        self.content_lay = QVBoxLayout()
        self.content_lay.setSpacing(8)
        bl.addLayout(self.content_lay, 1)
        self.setStyleSheet(f"""
        QFrame#cardBody {{ background:{C.UI_BG_CARD}; border-radius:{C.UI_RADIUS}px;
          border:1px solid {C.UI_BORDER}; }}
        QLabel {{ background:transparent; color:{C.UI_TEXT}; font-family:'Microsoft YaHei UI'; }}
        QToolButton {{ background:transparent; border:none; color:{C.UI_TEXT2};
          font-size:13px; padding:2px 6px; border-radius:6px; }}
        QToolButton:hover {{ background:rgba(255,255,255,26); color:{C.UI_TEXT}; }}
        QPushButton {{ background:rgba(255,255,255,16); border:none; border-radius:8px;
          color:{C.UI_TEXT}; padding:7px 16px; font-size:12.5px;
          font-family:'Microsoft YaHei UI'; }}
        QPushButton:hover {{ background:rgba(255,255,255,28); }}
        QPushButton#primary {{ background:{C.UI_ACCENT}; color:#ffffff; font-weight:600; }}
        QPushButton#primary:hover {{ background:{C.UI_BADGE_TO}; }}
        QPlainTextEdit {{ background:rgba(255,255,255,10);
          border:1px solid {C.UI_ACCENT_BORDER}; border-radius:10px; color:{C.UI_TEXT};
          padding:8px 10px; font-size:13px; font-family:'Microsoft YaHei UI';
          selection-background-color:rgba(59,130,246,90); }}
        """)

    def eventFilter(self, obj, ev):
        if obj is self.head:                         # 标题栏拖动（同插件窗）
            t = ev.type()
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._drag_off = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if t == QEvent.MouseMove and self._drag_off is not None:
                self.move(ev.globalPosition().toPoint() - self._drag_off)
                return True
            if t == QEvent.MouseButtonRelease and self._drag_off is not None:
                self._drag_off = None
                return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.reject()                            # 无边框窗：Esc 即关
            return
        super().keyPressEvent(ev)


class ToastPositionDialog(CardDialog):
    positionSelected = Signal(str)

    def __init__(self, corner, parent=None):
        super().__init__("位", "提示位置", parent=parent)
        self.setFixedWidth(360)
        description = QLabel("选择提示出现的位置，点击后立即保存。", self.body)
        description.setWordWrap(True)
        description.setStyleSheet(f"color:{C.UI_TEXT2};font-size:12px;")
        self.content_lay.addWidget(description)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        arrows = ("↖", "↗", "↙", "↘")
        for index, (key, label) in enumerate(C.TOAST_CORNERS):
            button = QPushButton(f"{arrows[index]}  {label}", self.body)
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.setMinimumHeight(54)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName(label)
            button.setStyleSheet(
                f"QPushButton{{border:1px solid {C.UI_BORDER};}}"
                f"QPushButton:checked{{background:{C.UI_ACCENT};color:white;border-color:{C.UI_ACCENT};}}"
                f"QPushButton:checked:hover{{background:{C.UI_BADGE_TO};}}"
                f"QPushButton:focus{{border-color:{C.UI_ACCENT_BORDER};}}")
            button.clicked.connect(lambda _=False, value=key: self.positionSelected.emit(value))
            self.group.addButton(button)
            self.buttons[key] = button
            grid.addWidget(button, index // 2, index % 2)
        self.content_lay.addLayout(grid)
        self.info = QLabel("文字提示与截图完成卡片使用同一位置。", self.body)
        self.info.setWordWrap(True)
        self.info.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
        self.content_lay.addWidget(self.info)
        footer = QHBoxLayout()
        footer.addStretch()
        done = QPushButton("完成", self.body)
        done.setAutoDefault(False)
        done.setCursor(Qt.PointingHandCursor)
        done.clicked.connect(self.accept)
        footer.addWidget(done)
        self.content_lay.addLayout(footer)
        self.set_corner(corner)

    def set_corner(self, corner):
        self.buttons.get(corner, self.buttons["bottom_right"]).setChecked(True)


class _PhoneReceiveSwitch(QAbstractButton):
    def __init__(self, parent):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(52, 36)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("接收手机图片")
        self.setToolTip("开启后常驻接收；关闭设置窗口不会停止接收")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(C.UI_BADGE_FROM if self.isChecked() else C.UI_TEXT2), 1))
        painter.setBrush(QColor(C.UI_BADGE_TO if self.isChecked() else C.UI_POPUP_BG))
        painter.drawRoundedRect(QRect(4, 6, 44, 24), 12, 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(C.LIGHT['card']))
        painter.drawEllipse(QRect(27 if self.isChecked() else 7, 9, 18, 18))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(C.UI_BADGE_FROM), 2))
            painter.drawRoundedRect(QRect(1, 2, 50, 32), 10, 10)


class AppSettingsDialog(CardDialog):
    hotkeysRequested = Signal()
    positionSelected = Signal(str)
    cursorSelected = Signal(str)
    autostartToggled = Signal(bool)

    def __init__(self, settings, autostart=False, parent=None):
        super().__init__("设", "设置", parent=parent)
        self.setWindowModality(Qt.NonModal)
        self.setFixedWidth(480)
        screen = self.screen() or QGuiApplication.primaryScreen()
        height = min(560, screen.availableGeometry().height() - 48) if screen else 560
        self.resize(480, max(340, height))
        self.setMinimumHeight(340)

        scroll = QScrollArea(self.body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(12)
        scroll.setWidget(content)
        self.content_lay.addWidget(scroll, 1)
        self.setStyleSheet(self.styleSheet() + f"""
        QScrollArea, QScrollArea > QWidget > QWidget {{background:transparent;border:none;}}
        QFrame#settingsGroup {{background:rgba(255,255,255,7);border:1px solid {C.UI_BORDER};border-radius:10px;}}
        QPushButton#settingChoice {{background:rgba(255,255,255,9);border:1px solid transparent;padding:6px 10px;}}
        QPushButton#settingChoice:hover {{background:rgba(255,255,255,20);}}
        QPushButton#settingChoice:checked {{background:rgba(59,130,246,35);border-color:{C.UI_ACCENT};}}
        QPushButton#settingChoice:focus {{border-color:{C.UI_ACCENT_BORDER};}}
        QScrollBar:vertical {{background:transparent;width:5px;margin:2px 0;}}
        QScrollBar::handle:vertical {{background:#4b5260;border-radius:2px;min-height:24px;}}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{height:0;}}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{background:transparent;}}
        """)

        def section(title):
            frame = QFrame(content)
            frame.setObjectName("settingsGroup")
            group = QVBoxLayout(frame)
            group.setContentsMargins(12, 10, 12, 12)
            group.setSpacing(8)
            label = QLabel(title, frame)
            label.setStyleSheet(f"color:{C.UI_TEXT};font-size:12.5px;font-weight:600;")
            group.addWidget(label)
            layout.addWidget(frame)
            return frame, group

        frame, general = section("常规")
        row = QHBoxLayout()
        row.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(QLabel("快捷键", frame))
        detail = QLabel("全局快捷键与截图工具键", frame)
        detail.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
        text.addWidget(detail)
        row.addLayout(text, 1)
        hotkeys = QPushButton("编辑快捷键", frame)
        hotkeys.setAutoDefault(False)
        hotkeys.setCursor(Qt.PointingHandCursor)
        hotkeys.clicked.connect(lambda: self.hotkeysRequested.emit())
        row.addWidget(hotkeys)
        general.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("开机自启动", frame), 1)
        self.autostart_button = _PhoneReceiveSwitch(frame)
        self.autostart_button.setAccessibleName("开机自启动")
        self.autostart_button.setToolTip("登录 Windows 后自动启动简截")
        self.autostart_button.setChecked(autostart)
        self.autostart_button.toggled.connect(self.autostartToggled.emit)
        row.addWidget(self.autostart_button)
        general.addLayout(row)

        frame, positions = section("Toast 提示位置")
        grid = QGridLayout()
        grid.setSpacing(8)
        self.position_buttons = {}
        self.position_group = QButtonGroup(self)
        self.position_group.setExclusive(True)
        for index, (key, label) in enumerate(C.TOAST_CORNERS):
            button = QPushButton(("↖", "↗", "↙", "↘")[index] + "  " + label, frame)
            button.setObjectName("settingChoice")
            button.setAutoDefault(False)
            button.setCheckable(True)
            button.setMinimumHeight(36)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, value=key: self.positionSelected.emit(value))
            self.position_group.addButton(button)
            self.position_buttons[key] = button
            grid.addWidget(button, index // 2, index % 2)
        positions.addLayout(grid)
        self.set_corner(settings.get("toast_corner", "bottom_right"))

        frame, cursors = section("截图框选指针")
        choices = QHBoxLayout()
        choices.setSpacing(8)
        self.cursor_buttons = {}
        self.cursor_group = QButtonGroup(self)
        self.cursor_group.setExclusive(True)
        for key, label, icon in C.SHOT_CURSOR_STYLES:
            button = QPushButton(label, frame)
            button.setObjectName("settingChoice")
            button.setIcon(icons.lucide_icon(icon, C.UI_TEXT, 20))
            button.setIconSize(QSize(20, 20))
            button.setAutoDefault(False)
            button.setCheckable(True)
            button.setMinimumHeight(48)
            button.setCursor(C.shot_selection_cursor({"shot_cursor": key}))
            button.clicked.connect(lambda _=False, value=key: self.cursorSelected.emit(value))
            self.cursor_group.addButton(button)
            self.cursor_buttons[key] = button
            choices.addWidget(button, 1)
        cursors.addLayout(choices)
        detail = QLabel("默认使用十字指针；下次框选时生效。", frame)
        detail.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
        cursors.addWidget(detail)
        self.set_cursor(settings.get("shot_cursor", "crosshair"))
        layout.addStretch()

        self.info = QLabel("更改后自动保存，下次启动继续使用。", self.body)
        self.info.setWordWrap(True)
        self.info.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
        self.content_lay.addWidget(self.info)
        footer = QHBoxLayout()
        footer.addStretch()
        done = QPushButton("完成", self.body)
        done.setAutoDefault(False)
        done.setCursor(Qt.PointingHandCursor)
        done.clicked.connect(self.accept)
        footer.addWidget(done)
        self.content_lay.addLayout(footer)

    def set_corner(self, corner):
        self.position_buttons.get(corner, self.position_buttons["bottom_right"]).setChecked(True)

    def set_cursor(self, cursor):
        self.cursor_buttons.get(cursor, self.cursor_buttons["crosshair"]).setChecked(True)

    def set_autostart(self, enabled):
        blocked = self.autostart_button.blockSignals(True)
        self.autostart_button.setChecked(enabled)
        self.autostart_button.blockSignals(blocked)


_PHONE_QR_SIDE = 192


def _phone_shortcut_qr(url):
    # 固定逻辑尺寸，在屏幕像素网格上直接绘制；保留四格扫码留白。
    import qrcode
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    code.add_data(url)
    code.make(fit=True)
    matrix = code.get_matrix()
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1
    side = round(_PHONE_QR_SIDE * dpr)
    scale = side / len(matrix)
    image = QImage(side, side, QImage.Format_RGB32)
    image.fill(QColor(C.LIGHT['card']))
    painter = QPainter(image)
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                left, top = round(x * scale), round(y * scale)
                painter.fillRect(left, top, round((x + 1) * scale) - left,
                                 round((y + 1) * scale) - top, QColor(C.UI_BG_CARD))
    painter.end()
    image.setDevicePixelRatio(dpr)
    return QPixmap.fromImage(image)


class PhoneReceiveDialog(CardDialog):
    """固定标签的连接表单和 iPhone / 安卓首次使用引导。"""

    def __init__(self, config, on_toggle, on_save, on_allow, on_exchange=None):
        from phone_receive import SHORTCUT_URL, SHORTCUT_NAME
        super().__init__("传", "简捷互传", "手机 ↔ 电脑 · 图片 / 文字 / 文件", on_top=False)
        self.setWindowTitle("简捷互传")
        self._config = config
        self._on_toggle, self._on_save, self._on_allow = on_toggle, on_save, on_allow
        self._firewall_files = None
        self._fields = {}
        self.setFixedWidth(704)
        screen = QGuiApplication.primaryScreen()
        self.setFixedHeight(min(640, max(380, screen.availableGeometry().height() - 48)) if screen else 640)
        self.setStyleSheet(self.styleSheet() + f"""
        QLabel {{ font-size:12px; }}
        QLabel#phoneSection {{ font-size:14px; font-weight:600; color:{C.UI_TEXT}; }}
        QLabel#phoneStepTitle {{ font-size:12px; font-weight:600; color:{C.UI_TEXT}; }}
        QLabel#phoneHint {{ font-size:11px; color:{C.UI_TEXT2}; }}
        QLabel#phoneStatus {{ font-size:11px; font-weight:600; }}
        QLabel#phoneStep {{ color:{C.UI_BADGE_FROM}; font-size:12px; font-weight:600; }}
        QLabel#phoneFeedback {{ color:{C.UI_TEXT2}; font-size:11px; }}
        QLabel#phoneFeedback[tone=error] {{ color:{C.UI_ERROR_TEXT}; }}
        QFrame#phoneGroup {{ background:{C.UI_POPUP_BG}; border:none; border-radius:{C.UI_RADIUS}px; }}
        QLineEdit {{ background:{C.UI_BG_CARD}; color:{C.UI_TEXT};
          border:1px solid {C.UI_TEXT3}; border-radius:6px;
          padding:4px 8px; font-size:12px; font-family:'Microsoft YaHei UI'; }}
        QLineEdit:hover {{ border-color:{C.UI_TEXT2}; }}
        QLineEdit:focus {{ border-color:{C.UI_BADGE_FROM}; }}
        QLineEdit:disabled {{ color:{C.UI_TEXT2}; }}
        QPushButton {{ background:{C.UI_BG_CARD}; border:1px solid {C.UI_TEXT3}; padding:4px 10px; border-radius:6px; }}
        QPushButton:hover {{ background:{C.UI_POPUP_BG}; }}
        QPushButton:pressed {{ background:{C.UI_ACCENT_BORDER}; }}
        QPushButton:focus {{ border-color:{C.UI_BADGE_FROM}; }}
        QPushButton:disabled {{ color:{C.UI_TEXT2}; border-color:{C.UI_BORDER}; }}
        QPushButton#phoneSave {{ background:{C.UI_BADGE_TO}; color:{C.LIGHT['card']};
          border-color:{C.UI_BADGE_TO}; font-weight:600; }}
        QPushButton#phoneSave:disabled {{ background:{C.UI_POPUP_BG}; color:{C.UI_TEXT2}; border-color:{C.UI_BORDER}; }}
        QPushButton#phoneSave:focus {{ border-color:{C.UI_BADGE_FROM}; }}
        QPushButton#phoneCopy {{ background:transparent; border:none; color:{C.UI_TEXT2}; }}
        QPushButton#phoneCopy:hover {{ background:{C.UI_BG_CARD}; color:{C.UI_TEXT}; }}
        QPushButton#phoneCopy:focus {{ border:1px solid {C.UI_BADGE_FROM}; }}
        QPushButton#phoneHelp {{ background:transparent; border:none; color:{C.UI_TEXT2};
          padding:2px 4px; text-align:right; font-size:11px; }}
        QPushButton#phoneHelp:hover {{ color:{C.UI_TEXT}; }}
        QPushButton#phoneHelp:focus {{ border:1px solid {C.UI_BADGE_FROM}; }}
        QPushButton#phonePlatform {{ background:transparent; color:{C.UI_TEXT2};
          border:1px solid {C.UI_TEXT3}; font-size:11px; padding:2px 8px; }}
        QPushButton#phonePlatform:checked {{ background:{C.UI_BADGE_TO}; color:{C.LIGHT['card']};
          border-color:{C.UI_BADGE_TO}; }}
        QPushButton#phonePlatform:focus {{ border-color:{C.UI_BADGE_FROM}; }}
        QFrame#phoneFooter {{ background:transparent; border:none;
          border-top:1px solid {C.UI_BORDER}; border-radius:0; }}
        QLineEdit#phoneLink {{ font-size:10px; padding:2px 6px; }}
        QPushButton#phoneLinkCopy {{ font-size:11px; padding:2px 6px; }}
        QScrollArea#phoneScroll, QWidget#phonePage {{ background:transparent; border:none; }}
        QScrollBar:vertical {{ background:{C.UI_BG_CARD}; width:8px; margin:0; }}
        QScrollBar::handle:vertical {{ background:{C.UI_TEXT3}; border-radius:4px; min-height:24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:none; }}
        """)
        # 样式挂在原生内容壳，保证滚动区内子控件也继承表单样式。
        self.body.setStyleSheet(self.styleSheet())
        # 标题栏继承壳结构，辅助文字改用可读的次文本令牌。
        for label in self.head.findChildren(QLabel):
            if label.text() == "手机 ↔ 电脑 · 图片 / 文字 / 文件":
                label.setStyleSheet(f"color:{C.UI_TEXT2}; font-size:11px; background:transparent;")

        scroll = QScrollArea(self.body)
        scroll.setObjectName("phoneScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        page = QWidget()
        page.setObjectName("phonePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 8, 0)
        layout.setSpacing(12)
        scroll.setWidget(page)
        self.content_lay.addWidget(scroll, 1)
        self.scroll = scroll
        self.exchange_pane = None
        if on_exchange:
            from exchange_ui import ExchangePane
            self.exchange_pane = ExchangePane(on_exchange, page, separate_records=True)
            layout.addWidget(self.exchange_pane)
        self.connection_group = QFrame(page)
        self.connection_group.setObjectName("phoneGroup")
        self.connection_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        connection = QVBoxLayout(self.connection_group)
        connection.setContentsMargins(12, 8, 12, 12)
        connection.setSpacing(6)
        status_row = QHBoxLayout()
        self.connection_title = self._label("快捷指令连接信息", "phoneSection")
        status_row.addWidget(self.connection_title)
        status_row.addStretch()
        self.status = self._label("", "phoneStatus")
        status_row.addWidget(self.status)
        self.toggle = _PhoneReceiveSwitch(self.connection_group)
        self.toggle.clicked.connect(self._toggle)
        status_row.addWidget(self.toggle)
        connection.addLayout(status_row)
        connection.addWidget(self._label("修改后保存生效；账号密码变更后，手机需重新配对。", "phoneHint"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        for index, (key, title) in enumerate((("url", "URL"), ("user", "User"), ("password", "Password"))):
            label = QLabel(title, page)
            label.setFixedWidth(76)
            edit = QLineEdit(page)
            edit.setAccessibleName(title)
            edit.setFixedHeight(30)
            edit.setMaxLength(256 if key == "url" else (64 if key == "user" else 128))
            edit.textChanged.connect(self._changed)
            self._fields[key] = edit
            grid.addWidget(label, index, 0)
            grid.addWidget(edit, index, 1)
            copy = self._button("复制", lambda checked=False, name=key: self._copy_field(name))
            copy.setObjectName("phoneCopy")
            copy.setMinimumWidth(44)
            copy.setToolTip(f"复制 {title}")
            grid.addWidget(copy, index, 2)
            label.setBuddy(edit)
        self.url_edit, self.user_edit, self.password_edit = (self._fields[k] for k in ("url", "user", "password"))
        self.url_edit.setPlaceholderText("http://本机局域网 IP:端口")
        connection.addLayout(grid)

        self.feedback = self._label("", "phoneFeedback")
        self.feedback.setMinimumHeight(18)
        self.feedback.setMinimumWidth(0)
        feedback_policy = self.feedback.sizePolicy()
        feedback_policy.setHorizontalPolicy(QSizePolicy.Ignored)
        feedback_policy.setHeightForWidth(True)
        self.feedback.setSizePolicy(feedback_policy)
        save_row = QHBoxLayout()
        save_row.setContentsMargins(0, 4, 0, 0)
        save_row.setSpacing(12)
        save_row.addWidget(self._label("填写到 iPhone 的 URL、Username、Password。", "phoneHint"), 1, Qt.AlignVCenter)
        self.save = self._button("保存配置", self._save)
        self.save.setObjectName("phoneSave")
        save_row.addWidget(self.save)
        connection.addLayout(save_row)

        self.mobile_group = QFrame(page)
        self.mobile_group.setObjectName("phoneGroup")
        self.mobile_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        mobile = QVBoxLayout(self.mobile_group)
        mobile.setContentsMargins(12, 12, 12, 12)
        mobile.setSpacing(12)
        mobile_header = QHBoxLayout()
        mobile_header.addWidget(self._label("浏览器连接", "phoneSection"))
        mobile_header.addSpacing(8)
        self.ios_button = self._button("iPhone 快捷指令", lambda: self._select_phone_platform(0))
        self.android_button = self._button("网页互传 · Mac / 手机", lambda: self._select_phone_platform(1))
        for button in (self.android_button, self.ios_button):
            button.setObjectName("phonePlatform")
            button.setCheckable(True)
            button.setFixedHeight(26)
            button.setMinimumWidth(64)
            mobile_header.addWidget(button)
        self.ios_button.setChecked(True)
        mobile_header.addStretch()
        mobile.addLayout(mobile_header)
        layout.addWidget(self.mobile_group)

        ios_setup, self.qr, instructions = self._phone_setup(page)
        self.shortcut_url = SHORTCUT_URL
        self.qr.setAccessibleName(f"扫码添加 {SHORTCUT_NAME} 快捷指令")
        self.qr.setToolTip("微信或 iPhone 相机扫码；微信无法打开时，将链接复制到 Safari")
        self.qr.setPixmap(_phone_shortcut_qr(SHORTCUT_URL))
        first_step = self._step("1", "扫码获取快捷指令", "相机扫码后点链接；微信扫码后，在右上角“…”中选择用浏览器打开。也可复制链接到 Safari。")
        link_row = QHBoxLayout()
        link = QLineEdit(SHORTCUT_URL, page)
        link.setReadOnly(True)
        link.setAccessibleName("快捷指令链接")
        link.setObjectName("phoneLink")
        link.setToolTip(SHORTCUT_URL)
        link.setFixedHeight(24)
        link.setCursorPosition(0)
        self.shortcut_link = link
        link_row.addWidget(link, 1)
        self.copy_link = self._button("复制链接", lambda: self._copy_text(SHORTCUT_URL, self.copy_link))
        self.copy_link.setObjectName("phoneLinkCopy")
        self.copy_link.setFixedHeight(24)
        self.copy_link.setMinimumWidth(64)
        link_row.addWidget(self.copy_link)
        first_step.itemAt(1).layout().addLayout(link_row)
        instructions.addLayout(first_step)
        instructions.addLayout(self._step("2", "填入下方连接信息", "将下方三项复制到配置页；User 对应 Username。若添加按钮无响应，先点“跳过设置”添加，再进入编辑页填写。"))
        instructions.addLayout(self._step("3", "获取或发送，一次配置", f"单独运行 {SHORTCUT_NAME}，选择“获取”接收电脑发送的内容，选择“发送”上传手机剪贴板。照片或文件也可从分享菜单发送。"))
        instructions.addWidget(self._label(f"当前快捷指令：{SHORTCUT_NAME}", "phoneHint"))
        instructions.addStretch()
        self.phone_pages = QStackedWidget(page)
        self.phone_pages.addWidget(ios_setup)
        self.phone_pages.addWidget(self._android_setup(page))
        mobile.addWidget(self.phone_pages)
        self.advanced_button = self._button("高级连接设置 ▾", self._toggle_advanced)
        self.advanced_button.setObjectName("phoneHelp")
        self.advanced_button.setCheckable(True)
        mobile.addWidget(self.connection_group)
        self.mobile_footer = QFrame(self.mobile_group)
        self.mobile_footer.setObjectName("phoneFooter")
        footer = QVBoxLayout(self.mobile_footer)
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(8)
        self.help_button = self._button("使用帮助 ▾", self._toggle_help)
        self.help_button.setObjectName("phoneHelp")
        self.help_button.setCheckable(True)
        self.help_button.setAccessibleName("手机设置使用帮助")
        self.help_button.setFixedWidth(80)
        self.help_button.setFixedHeight(26)
        help_row = QHBoxLayout()
        help_row.setSpacing(12)
        help_row.addWidget(self.advanced_button)
        reminder = self.feedback
        reminder.setMinimumWidth(0)
        reminder_policy = reminder.sizePolicy()
        reminder_policy.setHorizontalPolicy(QSizePolicy.Ignored)
        reminder_policy.setHeightForWidth(True)
        reminder.setSizePolicy(reminder_policy)
        help_row.addWidget(reminder, 1, Qt.AlignVCenter)
        help_row.addWidget(self.help_button)
        footer.addLayout(help_row)
        self.help_panel = QWidget(page)
        help_content = QVBoxLayout(self.help_panel)
        help_content.setContentsMargins(0, 0, 0, 0)
        help_content.setSpacing(6)
        self.help_text = self._label("", "phoneHint")
        help_content.addWidget(self.help_text)
        repair_row = QHBoxLayout()
        self.allow = self._button("修复局域网连接", self._allow_lan)
        self.allow.setToolTip("仅放行本程序、当前网卡和局域网；Windows 会请求管理员授权")
        repair_row.addWidget(self.allow)
        repair_row.addStretch()
        help_content.addLayout(repair_row)
        self.help_panel.hide()
        footer.addWidget(self.help_panel)
        mobile.addWidget(self.mobile_footer)
        if self.exchange_pane is not None:
            layout.addWidget(self.exchange_pane.record_panel)
        layout.addStretch()

        self._firewall_timer = QTimer(self)
        self._firewall_timer.setInterval(800)
        self._firewall_timer.timeout.connect(self._check_firewall)
        self._refresh(config)
        self._select_phone_platform(1)
        if self.exchange_pane is not None:
            self.exchange_pane.header.addWidget(self.status)
            self.exchange_pane.header.addWidget(self.toggle)
            self.toggle.setAccessibleName("启用简捷互传")
        else:
            mobile_header.addWidget(self.status)
            mobile_header.addWidget(self.toggle)

    def _scroll_phone_setup(self):
        position = self.mobile_group.mapTo(self.scroll.widget(), QPoint(0, 0)).y()
        self.scroll.verticalScrollBar().setValue(position)

    def _toggle_advanced(self):
        expanded = self.advanced_button.isChecked()
        self.connection_group.setVisible(expanded)
        self.advanced_button.setText("高级连接设置 ▴" if expanded else "高级连接设置 ▾")
        if expanded:
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.save))

    def _phone_setup(self, parent):
        page = QWidget(parent)
        setup = QHBoxLayout(page)
        setup.setContentsMargins(0, 0, 0, 0)
        setup.setSpacing(16)
        scan = QWidget(page)
        scan.setFixedWidth(_PHONE_QR_SIDE)
        qr_column = QVBoxLayout(scan)
        qr_column.setContentsMargins(0, 0, 0, 0)
        qr_column.setSpacing(8)
        qr = QLabel(scan)
        qr.setFixedSize(_PHONE_QR_SIDE, _PHONE_QR_SIDE)
        qr.setAlignment(Qt.AlignCenter)
        qr_column.addWidget(qr)
        caption = self._label("微信 / 手机相机扫码", "phoneHint")
        caption.setAlignment(Qt.AlignCenter)
        caption.setMinimumHeight(18)
        qr_column.addWidget(caption)
        qr_column.addStretch()
        setup.addWidget(scan, 0)
        steps = QWidget(page)
        steps.setMinimumWidth(0)
        steps.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        instructions = QVBoxLayout(steps)
        instructions.setContentsMargins(0, 0, 0, 0)
        instructions.setSpacing(12)
        setup.addWidget(steps, 1)
        return page, qr, instructions

    def _android_setup(self, parent):
        page, self.android_qr, instructions = self._phone_setup(parent)
        self.android_qr.setAccessibleName("连接简捷互传网页，Mac、Windows、iPhone 与安卓通用")
        self.android_qr.setToolTip("微信或手机相机扫码；微信无法选图时，用浏览器打开")
        self._android_qr_url = None
        first = self._step("1", "连接这台电脑", "Mac / Windows 浏览器打开下方配对链接；手机也可以扫码。同一局域网内即可使用。")
        link_row = QHBoxLayout()
        self.android_link = QLineEdit(page)
        self.android_link.setReadOnly(True)
        self.android_link.setObjectName("phoneLink")
        self.android_link.setAccessibleName("浏览器局域网互传链接")
        self.android_link.setPlaceholderText("开启接收后生成局域网传图链接")
        self.android_link.setFixedHeight(24)
        self.android_link.setCursorPosition(0)
        link_row.addWidget(self.android_link, 1)
        self.android_copy_link = self._button("复制链接", lambda: self._copy_text(self.android_link.text(), self.android_copy_link))
        self.android_copy_link.setObjectName("phoneLinkCopy")
        self.android_copy_link.setFixedHeight(24)
        self.android_copy_link.setMinimumWidth(64)
        link_row.addWidget(self.android_copy_link)
        first.itemAt(1).layout().addLayout(link_row)
        instructions.addLayout(first)
        instructions.addLayout(self._step("2", "电脑发送，浏览器领取", "在上方准备内容并发送，接收端网页的“最近互传”中即可复制文字或下载文件。"))
        instructions.addLayout(self._step("3", "浏览器发送，电脑接收", "网页拖入或选择文件、图片，或粘贴文字后发送。电脑收到可直接粘贴，文件也可在记录中保存。"))
        instructions.addStretch()
        return page

    def _select_phone_platform(self, index):
        self.phone_pages.setCurrentIndex(index)
        self.advanced_button.setVisible(index == 1)
        self.connection_group.setVisible(index == 0 or self.advanced_button.isChecked())
        self.connection_title.setText("快捷指令连接信息" if index == 0 else "高级连接设置")
        self.ios_button.setChecked(index == 0)
        self.android_button.setChecked(index == 1)
        self.ios_button.setAccessibleDescription("已选择" if index == 0 else "未选择")
        self.android_button.setAccessibleDescription("已选择" if index == 1 else "未选择")
        common = "手机连接不上：确认同一局域网和 URL 地址；仍失败或换过端口时，可修复局域网连接。"
        from phone_receive import SHORTCUT_NAME
        ios = f"微信无法打开：复制链接到 Safari，再点“获取快捷指令”。\n使用新版：添加 {SHORTCUT_NAME}，将 URL、Username 和 Password 配置为下方连接信息。\n单独运行：选择“获取”接收电脑发送的内容，选择“发送”上传手机剪贴板。\n分享照片或文件：在分享菜单中选择此快捷指令。电脑收到请求后会在这里显示处理结果。\nHEIC 等格式按文件接收，可在资源管理器粘贴保存。"
        android = "无需安装 App：扫码即可收发，无需填写账号密码。Mac / Windows / iPhone / 安卓均可使用。\n微信不能选图：用常用浏览器打开；必要时复制配对链接。\n图片最大 32 MiB；其他文件最大 2 GiB，可通过“选择文件”发送 HEIC / WebP 原文件。\n最近 5 条临时保留 30 分钟，请及时下载或保存。"
        self.help_text.setText((ios if index == 0 else android) + "\n" + common)
        if self.mobile_group.isVisible():
            QTimer.singleShot(0, self._scroll_phone_setup)

    def _button(self, title, callback):
        button = QPushButton(title, self.body)
        button.setCursor(Qt.PointingHandCursor)
        button.setAutoDefault(False)
        button.setFixedHeight(30)
        button.setMinimumWidth(max(60, button.fontMetrics().horizontalAdvance(title) + 28))
        button.clicked.connect(callback)
        return button

    def _label(self, text, name):
        label = QLabel(text, self.body)
        label.setObjectName(name)
        label.setWordWrap(True)
        return label

    def _step(self, number, title, detail):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        badge = self._label(number, "phoneStep")
        badge.setFixedWidth(20)
        row.addWidget(badge, 0, Qt.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(4)
        column.addWidget(self._label(title, "phoneStepTitle"))
        detail_label = self._label(detail, "phoneHint")
        detail_label.setMinimumWidth(0)
        policy = detail_label.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Ignored)
        policy.setHeightForWidth(True)
        detail_label.setSizePolicy(policy)
        column.addWidget(detail_label)
        row.addLayout(column, 1)
        return row

    def _toggle_help(self):
        self.help_panel.setVisible(self.help_panel.isHidden())
        self.help_button.setText("收起帮助 ▴" if not self.help_panel.isHidden() else "使用帮助 ▾")
        self.help_button.setChecked(not self.help_panel.isHidden())
        if not self.help_panel.isHidden():
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.allow))

    def _dirty(self):
        return any(edit.text() != self._config.get(key, "") for key, edit in self._fields.items())

    def _changed(self):
        if hasattr(self, "save"):
            dirty = self._dirty()
            self.save.setEnabled(dirty)
            if dirty:
                self._feedback("修改后保存，再更新手机端的连接信息。")

    def _feedback(self, message, error=False):
        self.feedback.setText(message)
        self.feedback.setProperty("tone", "error" if error else "normal")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)

    def _refresh(self, config, preserve_edits=False):
        draft = {key: edit.text() for key, edit in self._fields.items()} if preserve_edits and self._dirty() else None
        self._config = config
        for key, edit in self._fields.items():
            edit.blockSignals(True)
            edit.setText(draft[key] if draft else config.get(key, ""))
            edit.blockSignals(False)
        enabled = config["enabled"]
        self.status.setText("● 简捷互传已开启" if enabled else "○ 简捷互传已关闭")
        self.status.setStyleSheet(f"color:{C.UI_BADGE_FROM if enabled else C.UI_TEXT2};")
        self.toggle.setChecked(enabled)
        self.toggle.setAccessibleDescription("已开启" if enabled else "已关闭")
        self.toggle.update()
        self.save.setEnabled(bool(draft and self._dirty()))
        android_url = config.get("android_url", "") if enabled else ""
        self.android_link.setText(android_url)
        self.android_link.setCursorPosition(0)
        self.android_copy_link.setEnabled(bool(android_url))
        if android_url != self._android_qr_url:
            self._android_qr_url = android_url
            if android_url:
                self.android_qr.setPixmap(_phone_shortcut_qr(android_url))
            else:
                self.android_qr.clear()
                self.android_qr.setText("开启接收后\n显示二维码")
        self._feedback(config.get("message") or ("同一局域网内收发；关闭窗口不影响互传。" if config["url"] else "未发现局域网地址，请连接 Wi-Fi 后重新打开。"))

    def _save(self):
        self.save.setEnabled(False)
        ok, config = self._on_save(*(self._fields[k].text() for k in ("url", "user", "password")))
        if ok:
            self._refresh(config)
        else:
            self.save.setEnabled(True)
            self._feedback(config["message"], True)
        return ok

    def _toggle(self):
        if not self._config["enabled"] and self._dirty() and not self._save():
            return
        config = self._on_toggle(not self._config["enabled"])
        # 关闭接收保留尚未保存的表单内容。
        if self._dirty() and not config["enabled"]:
            edits = {key: edit.text() for key, edit in self._fields.items()}
            self._refresh(config)
            for key, value in edits.items():
                self._fields[key].setText(value)
        else:
            self._refresh(config)

    def _copy_text(self, text, button=None):
        QGuiApplication.clipboard().setText(text)
        if button is not None:
            original = button.text()
            button.setText("已复制")
            QTimer.singleShot(1400, lambda: button.setText(original))

    def _copy_field(self, key):
        self._copy_text(self._fields[key].text())
        self._feedback(f"已复制 { {'url':'URL','user':'User','password':'Password'}[key] }")

    def _allow_lan(self):
        if self._dirty() and not self._save():
            return
        self.allow.setEnabled(False)
        self.allow.setText("等待授权…")
        self._feedback("请在 Windows 管理员提示中点“是”，无需在 PowerShell 中输入命令。")
        try:
            self._firewall_files = self._on_allow()
        except (ValueError, OSError) as error:
            self.allow.setEnabled(True)
            self.allow.setText("修复局域网连接")
            self._feedback(str(error), True)
            return
        self.allow.setText("正在修复…")
        self._firewall_deadline = time.monotonic() + 120
        self._feedback("正在配置局域网连接，请稍候…")
        self._firewall_timer.start()

    def _check_firewall(self):
        if self._firewall_files is None:
            self._firewall_timer.stop()
            return
        root, result = self._firewall_files
        try:
            with open(result, encoding="utf-8-sig") as stream:
                outcome = json.load(stream)
            if (not isinstance(outcome, dict) or type(outcome.get("ok")) is not bool or
                    not isinstance(outcome.get("message"), str)):
                raise ValueError("修复结果内容不完整")
            message, failed = outcome["message"], not outcome["ok"]
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            if time.monotonic() < self._firewall_deadline:
                return
            message, failed = "未收到修复结果。请确认 Windows 管理员提示已点“是”，然后重试。", True
        except (OSError, ValueError) as error:
            if getattr(error, "winerror", None) in (32, 33) and time.monotonic() < self._firewall_deadline:
                return
            message, failed = "无法读取修复结果：" + str(error), True
        self._firewall_timer.stop()
        self.allow.setEnabled(True)
        self.allow.setText("修复局域网连接")
        self._feedback(message, failed)
        for name in ("configure.ps1", "result.json"):
            try:
                os.remove(os.path.join(root, name))
            except OSError:
                pass
        try:
            os.rmdir(root)
        except OSError:
            pass
        self._firewall_files = None


class QrInputDialog(CardDialog):
    """二维码生成 · 输入卡片：多行文本 + 取消/生成（主钮）。"""

    def __init__(self, on_top=True, parent=None):
        super().__init__("码", "二维码生成", "文本 / 链接均可", on_top=on_top, parent=parent)
        self.edit = QPlainTextEdit(self.body)
        self.edit.setPlaceholderText("输入文本或链接，例如 https://…")
        self.edit.setMinimumHeight(120)
        self.content_lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消", self.body)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok = QPushButton("生成", self.body)
        self.ok.setObjectName("primary")
        self.ok.setCursor(Qt.PointingHandCursor)
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        self.content_lay.addLayout(row)
        self.edit.setFocus()

    def text(self):
        return self.edit.toPlainText().strip()


class QrResultDialog(CardDialog):
    """二维码生成 · 结果卡片：白底码图（扫码可靠性底线）＋保存路径 + 复制路径/完成。"""

    def __init__(self, pm, path, on_top=True, parent=None):
        super().__init__("码", "二维码已生成", "已复制到剪贴板", on_top=on_top, parent=parent)
        self.path = path
        holder = QFrame(self.body)
        holder.setObjectName("qrHolder")
        holder.setStyleSheet("QFrame#qrHolder { background:#ffffff; border-radius:10px; }")
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(14, 14, 14, 14)
        img = QLabel(holder)
        img.setAlignment(Qt.AlignCenter)
        img.setPixmap(pm.scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        hv.addWidget(img)
        self.content_lay.addWidget(holder, 0, Qt.AlignHCenter)
        tip = QLabel(f"已保存：{path}", self.body)
        tip.setStyleSheet("color:#6b7280; font-size:11px; background:transparent;")
        tip.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.content_lay.addWidget(tip)
        row = QHBoxLayout()
        row.addStretch()
        self.btn_copy = QPushButton("复制路径", self.body)
        self.btn_copy.setCursor(Qt.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_path)
        row.addWidget(self.btn_copy)
        done = QPushButton("完成", self.body)
        done.setObjectName("primary")
        done.setCursor(Qt.PointingHandCursor)
        done.clicked.connect(self.accept)
        row.addWidget(done)
        self.content_lay.addLayout(row)

    def _copy_path(self):
        QGuiApplication.clipboard().setText(self.path)
        self.btn_copy.setText("已复制 ✓")


class HotkeyDialog(CardDialog):
    """快捷键设置卡（R92）：行=动作名+组合按钮；点按钮进捕获态后直接按新组合，
    Esc 取消捕获；保存前校验有效组合与相互冲突，结果从 self.cur 带出。
    hk_parse 由调用方注入（避免 widgets → main 反向依赖）。"""

    def __init__(self, actions, cur, hk_parse, on_top=True, parent=None, extra=None):
        super().__init__("键", "快捷键设置", "点组合按钮后按新键 · Esc 取消捕获",
                         on_top=on_top, parent=parent)
        self._actions = list(actions)
        self._hk_parse = hk_parse
        self.cur = dict(cur)
        # R131 第二组（可选）：截图工具单键——extra={"title","actions","cur"}，
        # btns 键加 "tool:" 前缀与全局组同表，结果从 self.cur2 带出；无修饰单键即有效。
        self._extra = extra
        self._eactions = list(extra["actions"]) if extra else []
        self.cur2 = dict(extra["cur"]) if extra else {}
        self.cap = {"key": None}
        self._key_filter_on = False
        self._held_capture_keys = set()
        self.btns = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for row, (key, label) in enumerate(self._actions):
            lb = QLabel(label, self.body)
            lb.setStyleSheet("color:#c3c9d4; font-size:12.5px; background:transparent;")
            grid.addWidget(lb, row, 0)
            b = QPushButton(self.cur[key], self.body)
            b.setObjectName("hk")
            b.setMinimumWidth(190)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._start_cap(k))
            grid.addWidget(b, row, 1)
            self.btns[key] = b
        self.content_lay.addLayout(grid)
        if self._extra:
            t = QLabel(self._extra["title"], self.body)
            t.setStyleSheet("color:#8a93a6; font-size:11.5px; background:transparent;"
                            "border-top:1px solid rgba(255,255,255,18); padding-top:10px; margin-top:4px;")
            self.content_lay.addWidget(t)
            grid2 = QGridLayout()
            grid2.setHorizontalSpacing(12)
            grid2.setVerticalSpacing(6)
            for row, (key, label) in enumerate(self._eactions):
                lb = QLabel(label, self.body)
                lb.setStyleSheet("color:#c3c9d4; font-size:12.5px; background:transparent;")
                grid2.addWidget(lb, row, 0)
                b = QPushButton(self.cur2[key], self.body)
                b.setObjectName("hk")
                b.setMinimumWidth(90)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, k="tool:" + key: self._start_cap(k))
                grid2.addWidget(b, row, 1)
                self.btns["tool:" + key] = b
            self.content_lay.addLayout(grid2)
        self.info = QLabel("", self.body)
        self.info.setStyleSheet("color:#f0b429; font-size:11.5px; background:transparent;")
        self.content_lay.addWidget(self.info)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消", self.body)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        save = QPushButton("保存并应用", self.body)
        save.setObjectName("primary")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save)
        row.addWidget(save)
        self.content_lay.addLayout(row)
        self.setStyleSheet(self.styleSheet() + """
        QPushButton#hk { font-family:Consolas,'Microsoft YaHei UI'; font-size:12.5px;
          padding:6px 14px; }
        QPushButton#hk[cap="true"] { border:1px solid #4a5bd6; color:#60a5fa;
          background:rgba(59,130,246,22); }
        """)

    def _start_cap(self, k):
        self.cap["key"] = None if self.cap["key"] == k else k   # 再点同一钮=退出捕获
        self.info.setText("")
        self._paint()
        if self.cap["key"]:
            self.btns[k].setFocus(Qt.MouseFocusReason)

    def showEvent(self, ev):
        super().showEvent(ev)
        if not self._key_filter_on:
            QGuiApplication.instance().installEventFilter(self)
            self._key_filter_on = True

    def hideEvent(self, ev):
        if self._key_filter_on:
            QGuiApplication.instance().removeEventFilter(self)
            self._key_filter_on = False
        self.cap["key"] = None
        self._held_capture_keys.clear()
        super().hideEvent(ev)

    def eventFilter(self, obj, ev):
        if obj is self and ev.type() == QEvent.WindowDeactivate:
            if getattr(self, "cap", {}).get("key"):
                self.cap["key"] = None
                self._paint()
            if hasattr(self, "_held_capture_keys"):
                self._held_capture_keys.clear()
        owned = isinstance(obj, QWidget) and (obj is self or self.isAncestorOf(obj))
        if owned and hasattr(self, "_held_capture_keys"):
            kind = ev.type()
            if kind in (QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
                active = bool(self.cap["key"])
                held = ev.key() in self._held_capture_keys
                if active or held:
                    ev.accept()
                    if kind == QEvent.KeyPress:
                        self._held_capture_keys.add(ev.key())
                        if active and not ev.isAutoRepeat():
                            self._capture_key(ev)
                    elif kind == QEvent.KeyRelease:
                        if not ev.isAutoRepeat():
                            self._held_capture_keys.discard(ev.key())
                        if active:
                            self._preview_modifiers(ev.modifiers())
                    return True
        return super().eventFilter(obj, ev)

    @staticmethod
    def _modifier_names(mods):
        return [name for flag, name in ((Qt.ControlModifier, "Ctrl"), (Qt.AltModifier, "Alt"),
                                       (Qt.ShiftModifier, "Shift"), (Qt.MetaModifier, "Win"))
                if mods & flag]

    def _preview_modifiers(self, mods):
        k = self.cap["key"]
        if k:
            parts = self._modifier_names(mods)
            tip = "按下新键…" if k.startswith("tool:") else "按下新组合…"
            self.btns[k].setText("+".join(parts + ["…"]) if parts else tip)

    def _paint(self):
        for k, b in self.btns.items():
            on = self.cap["key"] == k
            tool = k.startswith("tool:")
            cur = self.cur2[k[5:]] if tool else self.cur[k]
            tip = "按下新键…" if tool else "按下新组合…"   # 老锁断言后者，文案分组保留
            b.setText(tip if on else cur)
            b.setProperty("cap", "true" if on else "false")
            b.style().unpolish(b)
            b.style().polish(b)

    def keyPressEvent(self, ev):
        if not self.cap["key"]:
            super().keyPressEvent(ev)              # 未捕获：走 CardDialog（Esc 关窗）
            return
        ev.accept()
        self._capture_key(ev)

    def _capture_key(self, ev):
        k = self.cap["key"]
        if not k or ev.isAutoRepeat():
            return
        if ev.key() in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta,
                        Qt.Key_AltGr, Qt.Key_NumLock, Qt.Key_CapsLock):
            self._preview_modifiers(ev.modifiers())
            return                                  # 等修饰键后的主键
        parts = self._modifier_names(ev.modifiers())
        if ev.key() == Qt.Key_Escape:               # 捕获中 Esc=取消捕获（不关窗）
            self.cap["key"] = None
            self.info.setText("")
            self._paint()
            return
        name = QKeySequence(ev.key()).toString(QKeySequence.PortableText)
        if not name:
            self.info.setText("暂不支持此按键，请换一个键")
            return                                  # 不支持的键
        if k.startswith("tool:"):                   # 截图工具键：无修饰单键（拒 Ctrl/Alt/Win）
            if parts and parts != ["Shift"]:
                self.info.setText("截图工具键只支持单键，可配合 Shift")
                return
            if len(name) != 1 and not name.startswith("F"):
                self.info.setText("截图工具键请使用字母、数字或功能键")
                return                              # 单字符或功能键
            self.cur2[k[5:]] = name.upper()
        else:
            if not parts:                           # 全局热键必须带修饰键
                self.info.setText("全局快捷键需要包含 Ctrl、Alt、Shift 或 Win")
                return
            if self._hk_parse("+".join(parts + [name])) is None:
                self.info.setText("暂不支持此组合，请换一个键")
                return                              # 不支持的键
            self.cur[k] = "+".join(parts + [name])
        self.cap["key"] = None
        self.info.setText("")
        self._paint()

    def _save(self):
        seen = {}
        for key, label in self._actions:
            if self._hk_parse(self.cur[key]) is None:
                self.info.setText(f"「{self.cur[key]}」不是有效组合")
                return
            if self.cur[key] in seen:
                self.info.setText(f"与「{seen[self.cur[key]]}」的快捷键冲突")
                return
            seen[self.cur[key]] = key
        seen2 = {}
        for key, label in self._eactions:           # R131 工具键组内查重（与全局组命名空间独立）
            v = self.cur2[key]
            if not v:
                self.info.setText(f"「{label}」的键位不能为空")
                return
            if v in seen2:
                self.info.setText(f"工具键与「{seen2[v]}」重复")
                return
            seen2[v] = label
        self.accept()


# ---------- 番茄钟（真实节拍：专注→休息自动循环、第 4 个进长休、提示音+托盘通知、今日计数落盘） ----------
class PomodoroWindow(QWidget):
    """R146 mac 化：统一黑透插件壳；开始钮 accent 实心主钮（mac 主操作语义），重置次钮。"""

    def __init__(self, settings, notify=None, save=None):
        super().__init__(None)
        self.setWindowTitle(f"{C.APP_NAME} · 番茄钟")
        self.resize(340, 330)
        self.work = int(settings.get("pomodoro_work", 25))
        self.rest = int(settings.get("pomodoro_rest", 5))
        self.long_rest = self.rest * 3
        self.settings = settings
        self._save = save or (lambda: C.save_settings(settings))   # 冒烟可传 no-op，不污染真设置
        self.notify = notify
        today = date.today().isoformat()
        if settings.get("pomodoro_done_date") != today:
            self._done_today = 0
        else:
            self._done_today = int(settings.get("pomodoro_done", 0))
        settings["pomodoro_done_date"] = today
        self.state = "idle"      # idle / work / rest / pause
        self.left = 0
        self._long_rest_now = False
        self._paused_from = "work"
        bl, _sub, = _plugin_shell(self, "番茄钟", min_size=(300, 280))
        self.setStyleSheet(PLUGIN_QSS)
        lay = QVBoxLayout()
        lay.setContentsMargins(6, 6, 6, 4)
        lay.setSpacing(10)
        self.phase = QLabel("番茄钟")
        self.phase.setStyleSheet("color:#7c8294;font-size:12px;")
        self.phase.setAlignment(Qt.AlignCenter)
        self.clock = QLabel(f"{self.work:02d}:00")
        self.clock.setStyleSheet("font-size:52px;font-weight:800;letter-spacing:2px;color:#eef1f7;")
        self.clock.setAlignment(Qt.AlignCenter)
        self.count = QLabel(f"今日完成 ×{self._done_today}")
        self.count.setStyleSheet("color:#22c55e;font-size:12px;")
        self.count.setAlignment(Qt.AlignCenter)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.go = QPushButton("开始")
        self.go.setObjectName("primaryBtn")           # R146：主操作 accent 实心（mac 主钮语义）
        self.go.setCursor(Qt.PointingHandCursor)
        self.go.clicked.connect(self.toggle)
        rst = QPushButton("重置")
        rst.setCursor(Qt.PointingHandCursor)
        rst.clicked.connect(self.reset)
        row.addWidget(self.go)
        row.addWidget(rst)
        lay.addWidget(self.phase)
        lay.addWidget(self.clock)
        lay.addWidget(self.count)
        lay.addLayout(row)
        bl.addLayout(lay, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)

    def toggle(self):
        if self.state == "idle":
            self.state = "work"
            self.left = self.work * 60
            self.timer.start()
        elif self.state in ("work", "rest"):
            self._paused_from = self.state
            self.state = "pause"
            self.timer.stop()
        elif self.state == "pause":
            self.state = self._paused_from
            self.timer.start()
        self.refresh()

    def reset(self):
        self.timer.stop()
        self.state = "idle"
        self.left = 0
        self._long_rest_now = False
        self.refresh()

    def tick(self):
        self.left -= 1
        if self.left <= 0:
            if self.isVisible():
                winsound.MessageBeep()
            if self.state == "work":
                self._done_today += 1
                self.settings["pomodoro_done"] = self._done_today
                self._save()
                if self._done_today % 4 == 0:
                    self.state = "rest"
                    self.left = self.long_rest * 60
                    self._long_rest_now = True
                    self._flash(f"第 {self._done_today} 个番茄完成，长休 {self.long_rest} 分钟")
                else:
                    self.state = "rest"
                    self.left = self.rest * 60
                    self._long_rest_now = False
                    self._flash(f"专注完成，休息 {self.rest} 分钟")
            else:
                self.state = "work"
                self.left = self.work * 60
                self._long_rest_now = False
                self._flash("休息结束，开始下一个番茄")
        self.refresh()

    def _flash(self, msg):
        self.phase.setText(msg)
        if self.notify:
            self.notify(f"{C.APP_NAME} · 番茄钟", msg)
        QTimer.singleShot(3000, self.refresh)

    def refresh(self):
        self.count.setText(f"今日完成 ×{self._done_today}")
        if self.state == "idle":
            self.setWindowTitle(f"{C.APP_NAME} · 番茄钟")
            self.phase.setText("番茄钟")
            self.clock.setText(f"{self.work:02d}:00")
            self.go.setText("开始")
        elif self.state == "work":
            self.setWindowTitle(f"{C.APP_NAME} · 番茄钟 [{self.left // 60:02d}:{self.left % 60:02d}] 专注")
            self.phase.setText("专注中")
            self.clock.setText(f"{self.left // 60:02d}:{self.left % 60:02d}")
            self.go.setText("暂停")
        elif self.state == "pause":
            self.setWindowTitle(f"{C.APP_NAME} · 番茄钟 [已暂停]")
            self.phase.setText("已暂停")
            self.clock.setText(f"{self.left // 60:02d}:{self.left % 60:02d}")
            self.go.setText("继续")
        elif self.state == "rest":
            tag = "长休" if self._long_rest_now else "休息"
            self.setWindowTitle(f"{C.APP_NAME} · 番茄钟 [{self.left // 60:02d}:{self.left % 60:02d}] {tag}")
            self.phase.setText("长休息中" if self._long_rest_now else "休息中")
            self.clock.setText(f"{self.left // 60:02d}:{self.left % 60:02d}")
            self.go.setText("暂停")


# ---------- 快记 ----------
class NotesWindow(QWidget):
    """R146 mac 化：统一黑透插件壳（「已自动保存」副信息并入标题栏副位）。"""

    def __init__(self, seed=None):
        super().__init__(None)
        self.setWindowTitle(f"{C.APP_NAME} · 快记")
        self.resize(520, 420)
        bl, self.saved, = _plugin_shell(self, "快记", "已自动保存至 notes.md")
        self.setStyleSheet(PLUGIN_QSS)
        self.edit = QTextEdit()
        base = C.load_notes()
        self.edit.setPlainText(seed + "\n\n" + base if seed else base)
        self.edit.setStyleSheet("font-size:13.5px;line-height:1.5;")
        bl.addWidget(self.edit, 1)
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(800)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save)
        self.edit.textChanged.connect(lambda: self._save_timer.start())
        if seed:
            self.edit.document().setModified(True)
            self._save_timer.start()
        QTimer.singleShot(50, self.edit.setFocus)

    def prepend_text(self, text):
        """剪贴板内容插入现有快记顶部，保留正在编辑且尚未保存的内容。"""
        cursor = self.edit.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.insertText(text + ("\n\n" if not self.edit.document().isEmpty() else ""))
        self.edit.setTextCursor(cursor)
        self.edit.ensureCursorVisible()
        self.edit.setFocus()

    def _save(self):
        try:
            C.save_notes(self.edit.toPlainText())
        except OSError:
            self.saved.setText("保存失败，请检查数据目录")
            return False
        self.edit.document().setModified(False)
        self.saved.setText("已自动保存至 notes.md")
        return True

    def closeEvent(self, event):
        # UI 销毁前完成尚在 800 ms 防抖期内的保存。
        if self._save_timer.isActive() or self.edit.document().isModified():
            self._save_timer.stop()
            if not self._save():
                event.ignore()
                return
        super().closeEvent(event)


# ---------- 剪贴板历史（R68 起只收图片；R156 方卡网格，悬停顶部胶囊浮钮：钉/编辑/删除） ----------
CLIP_QSS = """   /* R156 方卡+浮钮；R171 图片尽量铺满卡片预览区 */
QScrollArea{border:none;background:transparent;}
QScrollArea > QWidget{background:transparent;}
#clipCard{background:rgba(255,255,255,10);border:none;border-radius:10px;}
#clipCard:hover{background:rgba(255,255,255,26);}
#clipThumb{background:rgba(0,0,0,120);border-radius:6px;}
#clipMeta{color:#c7ccd8;font-size:10px;background:rgba(16,20,30,190);border-radius:5px;padding:0 5px;}
#clipBtns{background:rgba(16,20,30,235);border:none;border-radius:15px;}  /* R158：1px 半透边高分屏发虚退役（R134/R141/R145 无描边判例），悬浮感改自绘柔影 */
#clipBtns QToolButton{background:transparent;border:none;border-radius:6px;}
#clipBtns QToolButton:hover{background:rgba(255,255,255,24);}
QToolButton{background:transparent;color:#cfd5e4;border:none;border-radius:13px;font-size:13px;padding:0;}
QToolButton:hover{background:rgba(59,130,246,150);color:#fff;}
QToolButton:pressed{background:rgba(59,130,246,220);}
"""


class ClipHistoryWindow(QWidget):
    """与控制器共享同一 history list（条目：{"t":"img","v":…,"at":时间戳}），
    删除/追加实时同步；R156 卡片式（用户点名「图片改成方块，悬停浮出按钮更规整」）：
    方卡网格流式排列，卡片大小和列数随 viewport 宽动态重排；点击卡片＝复制。"""

    CARD = 148                               # 自适应卡片最小边长
    MAX_CARD = 192                           # 少量记录不放大到占满整行
    GAP = 6                                  # 卡片横纵间距（R178 收紧 10→6）

    def __init__(self, history, on_pin=None, on_edit_text=None, on_edit_img=None,
                 on_delete=None, on_send=None):
        super().__init__(None)
        self.setWindowTitle(f"{C.APP_NAME} · 剪贴板历史")
        self.resize(560, 520)
        bl, self.cnt, = _plugin_shell(self, "剪贴板历史")
        self.setStyleSheet(PLUGIN_QSS + CLIP_QSS)
        self.history = history
        self.on_pin = on_pin
        self.on_edit_text = on_edit_text
        self.on_edit_img = on_edit_img
        self.on_delete = on_delete
        self.on_send = on_send
        self.rows = []
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 2, 2, 2)
        self.body_lay.setSpacing(6)
        self.empty = QLabel("暂无记录 · 截图与长截图会自动出现在这里")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color:#525868;font-size:12.5px;padding:40px 0;")
        self.body_lay.addWidget(self.empty)
        self.grid_host = QWidget()               # R156 方卡网格宿主
        self.grid_host.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(self.GAP)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._layout_signature = None
        self._relayout_timer = QTimer(self)
        self._relayout_timer.setSingleShot(True)
        self._relayout_timer.timeout.connect(self._relayout)
        self.scroll.viewport().installEventFilter(self)
        self.body_lay.addWidget(self.grid_host)
        self.scroll.setWidget(body)
        # R146 透底（R95 同判例）：viewport/body 默认 palette 填底白版，QSS 管不到，必须代码级关；
        # 坑：setWidget 会把 body 的 autoFillBackground 重开，必须在其之后关
        self.scroll.viewport().setAutoFillBackground(False)
        body.setAutoFillBackground(False)
        bl.addWidget(self.scroll, 1)
        self.hint = QLabel("点击卡片复制 · 悬停可钉住 / 编辑 / 删除")
        self.hint.setStyleSheet("color:#6f7688;font-size:11.5px;")
        bl.addWidget(self.hint)
        for e in list(history):
            self._append_row(e, front=False)
        self.body_lay.addStretch()
        self._recount()

    # ---- 卡片管理 ----
    def _append_row(self, entry, front=True):
        row = _ClipCard(entry, self)
        if front:
            self.rows.insert(0, row)
        else:
            self.rows.append(row)
        self._relayout()
        self._recount()

    def prepend_entry(self, entry):
        while len(self.rows) >= 10:                     # 与控制器上限（10 条）同步：视图裁掉最旧
            old = self.rows.pop()
            old.setParent(None)
            old.deleteLater()
        self._append_row(entry, front=True)
        while len(self.rows) > len(self.history):       # 数据层可能同时淘汰了最旧（图片 6 张内存保护）：视图随数据收缩
            old = self.rows.pop()
            old.setParent(None)
            old.deleteLater()
        self._relayout()
        self._recount()

    def _cols_for_width(self, w):
        """n 卡占宽 n×CARD+(n-1)×GAP ≤ w → 纯函数便于锁断言。"""
        return max(1, int((w + self.GAP) // (self.CARD + self.GAP)))

    def _relayout(self):
        margins = self.body_lay.contentsMargins()
        width = max(1, self.scroll.viewport().width() - margins.left() - margins.right())
        cols = self._cols_for_width(width)
        side = min(self.MAX_CARD, max(self.CARD, (width - (cols - 1) * self.GAP) // cols))
        signature = (cols, side, tuple(self.rows))
        if signature == self._layout_signature:
            return
        self._layout_signature = signature
        while self.grid.count():                        # 摘出不销毁：卡片只重新摆位
            self.grid.takeAt(0)
        for i, row in enumerate(self.rows):
            row.set_card_size(side)
            self.grid.addWidget(row, i // cols, i % cols,
                                Qt.AlignTop | Qt.AlignLeft)
        self.grid.invalidate()
        self.grid_host.updateGeometry()

    def eventFilter(self, obj, ev):
        if obj is self.scroll.viewport() and ev.type() == QEvent.Resize:
            # 等 viewport 完成布局后读实际可用宽度，包含滚动条出现/消失。
            self._relayout_timer.start(0)
        return super().eventFilter(obj, ev)

    def _recount(self):
        self.cnt.setText(f"{len(self.rows)} 条")
        self.empty.setVisible(not self.rows)

    def _feedback(self, text):
        self.hint.setText(text)
        self.hint.setStyleSheet("color:#22c55e;font-size:11.5px;")


class _ClipCard(QFrame):
    """R156 方形卡片（_ClipRow 行式退役）：图片尽量铺满卡片、规格信息叠在底部；
    悬停时顶部居中浮出胶囊操作钮（复制/钉/编辑/删除，lucide 图标——R158 用户改口复制钮回归；
    胶囊无描边、柔影悬浮感：1px 半透边高分屏发虚退役，R134/R141/R145 判例；📌 emoji 根治；
    btn_name 显名机制退役回归系统 tooltip）。文本条目自 R68 起不入库，文本分支退役。"""

    def __init__(self, entry, win):
        super().__init__(None)
        self.setObjectName("clipCard")
        self.entry = entry
        self.win = win
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(ClipHistoryWindow.CARD, ClipHistoryWindow.CARD)
        e = entry
        at = time.strftime("%H:%M", time.localtime(e.get("at", time.time())))
        img = self._preview_image()
        preview_side = ClipHistoryWindow.MAX_CARD - 12
        self._preview_pixmap = QPixmap.fromImage(img.scaled(
            QSize(preview_side, preview_side), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        width, height = e.get("size", (img.width(), img.height()))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)
        self.thumb = QLabel(self)
        self.thumb.setObjectName("clipThumb")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setFixedSize(136, 136)
        self.thumb.setPixmap(self._preview_pixmap.scaled(
            self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(self.thumb)
        self.meta = QLabel(f"{width} × {height} px · {at}", self)
        self.meta.setObjectName("clipMeta")
        self.meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # 超长规格裁剪不撑卡
        self.meta.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.meta.setToolTip(f"{width} × {height} px · {at}")
        self.meta.setGeometry(10, self.height() - 26, self.width() - 20, 16)
        self.meta.raise_()
        # 悬停浮钮：顶部居中胶囊（不进布局，绝对定位浮在缩略图上）
        self.btns = QWidget(self)
        self.btns.setObjectName("clipBtns")
        bl = QHBoxLayout(self.btns)
        bl.setContentsMargins(5, 2, 5, 2)
        bl.setSpacing(2)
        self.act_copy = self._mk_btn(bl, "copy", "#c7ccd8", "复制", self._copy)   # R158 用户改口：复制钮回归
        self.act_pin = self._mk_btn(bl, "pin", "#c7ccd8", "钉住到桌面", self._pin)
        self.act_edit = self._mk_btn(bl, "pencil", "#c7ccd8", "编辑", self._edit)
        if self.win.on_send:
            self.act_send = self._mk_btn(bl, "smartphone", C.DARK["tx"], "发送到手机", self._send)
            self.act_send.setAccessibleName("发送到手机")
        self.act_del = self._mk_btn(bl, "x", "#f87171", "删除记录", self._del)   # 危险语义色常态浅红
        if self.win.on_send:
            for button in self.btns.findChildren(QToolButton):
                button.setFixedSize(24, 24)
        _bs = QGraphicsDropShadowEffect(self.btns)      # R158：柔影悬浮感替 1px 描边（描边高分屏发虚）
        _bs.setBlurRadius(12)
        _bs.setOffset(0, 2)
        _bs.setColor(QColor(0, 0, 0, 120))
        self.btns.setGraphicsEffect(_bs)
        self.btns.adjustSize()
        self.btns.move((self.width() - self.btns.width()) // 2, 8)
        self.btns.setVisible(False)                     # 悬停才浮出
        self.btns.raise_()

    def set_card_size(self, side):
        if side == self.width() and side == self.height():
            return
        self.setFixedSize(side, side)
        self.thumb.setFixedSize(side - 12, side - 12)
        self.thumb.setPixmap(self._preview_pixmap.scaled(
            self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.meta.setGeometry(10, side - 26, side - 20, 16)
        self.btns.move((side - self.btns.width()) // 2, 8)

    def _mk_btn(self, bl, name, color, tip, fn):
        b = QToolButton(self.btns)
        b.setIcon(icons.lucide_icon(name, color, 13))
        b.setIconSize(QSize(13, 13))
        b.setToolTip(tip)
        b.setFixedSize(26, 26)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        bl.addWidget(b)
        return b

    def _send(self):
        ok, message = self.win.on_send(self.entry, self._preview_pixmap.toImage())
        self.win._feedback(message)

    def _image(self):
        value = self.entry["v"]
        self._image_error = ""
        if isinstance(value, str):
            if self.entry.get("phone_original"):
                from phone_receive import read_phone_image
                try:
                    return read_phone_image(value)
                except ValueError as error:
                    self._image_error = str(error)
                    return QImage()
            reader = QImageReader(value)
            reader.setAutoTransform(True)
            return reader.read()
        return QImage.fromData(value, "PNG") if isinstance(value, bytes) else value

    def _preview_image(self):
        value = self.entry["v"]
        preview_side = ClipHistoryWindow.MAX_CARD - 12
        preview_size = QSize(preview_side, preview_side)
        if isinstance(value, str):
            if self.entry.get("phone_original"):
                from phone_receive import read_phone_image
                try:
                    return read_phone_image(value, preview_size)
                except ValueError:
                    return QImage()
            reader = QImageReader(value)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid():
                reader.setScaledSize(size.scaled(preview_size, Qt.KeepAspectRatio))
            preview = reader.read()
            return preview if not preview.isNull() else self._image()
        return self._image()

    def enterEvent(self, ev):
        self.btns.setVisible(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.btns.setVisible(False)
        super().leaveEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._copy()
        super().mousePressEvent(ev)

    def _copy(self):
        image = self._image()
        if image.isNull():
            self.win._feedback(self._image_error or "图片无法读取，请重试")
            return
        if C.copy_image_to_clipboard(image):
            self.win._feedback("已复制图片 · 可直接粘贴")
        else:
            self.win._feedback("剪贴板暂被占用，请重试")

    def _pin(self):
        if self.win.on_pin:
            image = self._image()
            if image.isNull():
                self.win._feedback(self._image_error or "图片无法读取，请重试")
                return
            self.win.on_pin(QPixmap.fromImage(image))
            self.win._feedback("已钉在桌面 · 双击关闭")

    def _edit(self):
        if self.win.on_edit_img:
            image = self._image()
            if image.isNull():
                self.win._feedback(self._image_error or "图片无法读取，请重试")
                return
            self.win.on_edit_img(image)

    def _del(self):
        w = self.win
        if self.entry in w.history:
            w.history.remove(self.entry)
        if w.on_delete:
            w.on_delete(self.entry)
        if self in w.rows:
            w.rows.remove(self)
        self.setParent(None)
        self.deleteLater()
        w._relayout()
        w._recount()
        w._feedback("已删除该条记录")


# ---------- 网速（psutil 实时字节差分 + 本次会话累计） ----------
def _fmt_speed(v):
    if v >= 1048576:
        return f"{v / 1048576:.2f} MB/s"
    if v >= 1024:
        return f"{v / 1024:.0f} KB/s"
    return f"{v:.0f} B/s"


def _fmt_bytes(v):
    for unit in ("B", "KB", "MB", "GB"):
        if v < 1024 or unit == "GB":
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024.0


# ---------- 测速（默认 Cloudflare speed 端点，无需鉴权；URL 可注入＝冒烟走回环真收发） ----------
# 速率统一字节/秒（B/s），展示走 _fmt_speed 自适应 KB/s·MB/s——与实时速率条同一单位语言


_WINHTTP = None
_WINHTTP_LOCK = threading.Lock()


def _winhttp_api():
    """复用 Windows 自带 WinHTTP，避免 Python SSL/OpenSSL 随单文件应用分发。"""
    global _WINHTTP
    if _WINHTTP is None:
        with _WINHTTP_LOCK:
            if _WINHTTP is None:
                api = ctypes.WinDLL("winhttp.dll", use_last_error=True)
                h = ctypes.c_void_p
                d = wintypes.DWORD
                api.WinHttpOpen.argtypes = [ctypes.c_wchar_p, d, ctypes.c_wchar_p,
                                            ctypes.c_wchar_p, d]
                api.WinHttpOpen.restype = h
                api.WinHttpConnect.argtypes = [h, ctypes.c_wchar_p, wintypes.WORD, d]
                api.WinHttpConnect.restype = h
                api.WinHttpOpenRequest.argtypes = [h, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                                   ctypes.c_wchar_p, ctypes.c_wchar_p, h, d]
                api.WinHttpOpenRequest.restype = h
                api.WinHttpSetTimeouts.argtypes = [h, ctypes.c_int, ctypes.c_int,
                                                   ctypes.c_int, ctypes.c_int]
                api.WinHttpSetTimeouts.restype = wintypes.BOOL
                api.WinHttpSendRequest.argtypes = [h, ctypes.c_wchar_p, d, h, d, d,
                                                   ctypes.c_size_t]
                api.WinHttpSendRequest.restype = wintypes.BOOL
                api.WinHttpWriteData.argtypes = [h, h, d, ctypes.POINTER(d)]
                api.WinHttpWriteData.restype = wintypes.BOOL
                api.WinHttpReceiveResponse.argtypes = [h, h]
                api.WinHttpReceiveResponse.restype = wintypes.BOOL
                api.WinHttpQueryHeaders.argtypes = [h, d, ctypes.c_wchar_p, h,
                                                    ctypes.POINTER(d), ctypes.POINTER(d)]
                api.WinHttpQueryHeaders.restype = wintypes.BOOL
                api.WinHttpReadData.argtypes = [h, h, d, ctypes.POINTER(d)]
                api.WinHttpReadData.restype = wintypes.BOOL
                api.WinHttpCloseHandle.argtypes = [h]
                api.WinHttpCloseHandle.restype = wintypes.BOOL
                _WINHTTP = api
    return _WINHTTP


def _winhttp_error(operation):
    code = ctypes.get_last_error()
    detail = ctypes.FormatError(code).strip() if code else "未知 WinHTTP 错误"
    return OSError(code, "{}：{}".format(operation, detail))


class _WinHttpResponse:
    def __init__(self, api, request, status):
        self._api = api
        self._request = request
        self.status = status
        self._eof = False

    def _read_chunk(self, size):
        if self._eof or size <= 0:
            return b""
        buf = ctypes.create_string_buffer(size)
        received = wintypes.DWORD()
        if not self._api.WinHttpReadData(self._request, buf, size, ctypes.byref(received)):
            raise _winhttp_error("WinHttpReadData")
        if not received.value:
            self._eof = True
            return b""
        return buf.raw[:received.value]

    def read(self, size=-1):
        if size == 0 or self._eof:
            return b""
        if size is None or size < 0:
            chunks = []
            while True:
                chunk = self._read_chunk(65536)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        return self._read_chunk(min(int(size), 1024 * 1024))


class _WinHttpConnection:
    """轻量 HTTP/HTTPS 请求壳；HTTPS 证书校验由 Windows 系统栈完成。"""
    _NO_PROXY = 1
    _FLAG_SECURE = 0x00800000
    _QUERY_STATUS_CODE = 19
    _QUERY_FLAG_NUMBER = 0x20000000

    def __init__(self, host, port, timeout, secure):
        self.host = host
        self.port = int(port)
        self.timeout = max(1, int(timeout * 1000))
        self.secure = secure
        self._api = None
        self._session = None
        self._connection = None
        self._request = None

    def connect(self):
        if self._session:
            return
        self._api = _winhttp_api()
        # User-Agent 由每个请求显式传入，避免 WinHttpOpen 默认头与请求头重复。
        self._session = self._api.WinHttpOpen(None, self._NO_PROXY, None, None, 0)
        if not self._session:
            raise _winhttp_error("WinHttpOpen")
        if not self._api.WinHttpSetTimeouts(self._session, self.timeout, self.timeout,
                                            self.timeout, self.timeout):
            err = _winhttp_error("WinHttpSetTimeouts")
            self.close()
            raise err
        self._connection = self._api.WinHttpConnect(self._session, self.host, self.port, 0)
        if not self._connection:
            err = _winhttp_error("WinHttpConnect")
            self.close()
            raise err

    def _close_request(self):
        if self._request:
            self._api.WinHttpCloseHandle(self._request)
            self._request = None

    def begin_request(self, method, path, headers=None, total_body=0):
        self.connect()
        self._close_request()
        flags = self._FLAG_SECURE if self.secure else 0
        self._request = self._api.WinHttpOpenRequest(
            self._connection, method, path, None, None, None, flags)
        if not self._request:
            raise _winhttp_error("WinHttpOpenRequest")
        header_text = "".join("{}: {}\r\n".format(k, v) for k, v in (headers or {}).items())
        header_length = 0 if not header_text else 0xFFFFFFFF  # WinHTTP: NUL 结尾的宽字符串
        if not self._api.WinHttpSendRequest(self._request, header_text or None, header_length,
                                            None, 0, int(total_body), 0):
            raise _winhttp_error("WinHttpSendRequest")

    def write_body(self, payload):
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            part = view[offset:offset + 1024 * 1024]
            buf = ctypes.create_string_buffer(part.tobytes(), len(part))
            written = wintypes.DWORD()
            if not self._api.WinHttpWriteData(self._request, buf, len(part), ctypes.byref(written)):
                raise _winhttp_error("WinHttpWriteData")
            if not written.value:
                raise RuntimeError("WinHTTP 未能发送请求数据")
            offset += written.value

    def request(self, method, path, body=None, headers=None):
        size = len(body) if body is not None else 0
        self.begin_request(method, path, headers, size)
        if body:
            self.write_body(body)

    def getresponse(self):
        if not self._request:
            raise RuntimeError("尚未发送 HTTP 请求")
        if not self._api.WinHttpReceiveResponse(self._request, None):
            raise _winhttp_error("WinHttpReceiveResponse")
        status = wintypes.DWORD()
        size = wintypes.DWORD(ctypes.sizeof(status))
        if not self._api.WinHttpQueryHeaders(
                self._request, self._QUERY_STATUS_CODE | self._QUERY_FLAG_NUMBER,
                None, ctypes.byref(status), ctypes.byref(size), None):
            raise _winhttp_error("WinHttpQueryHeaders")
        return _WinHttpResponse(self._api, self._request, int(status.value))

    def close(self):
        if self._api is None:
            return
        self._close_request()
        for name in ("_connection", "_session"):
            handle = getattr(self, name)
            if handle:
                self._api.WinHttpCloseHandle(handle)
                setattr(self, name, None)


class SpeedTest:
    """测速核心：单连接 keep-alive 真收发（连接建立/握手不计速，测纯吞吐）。
    ping＝预热后小包往返最小 RTT；下载＝单请求流式拉（限时截停按实收算）；
    上传＝多个 POST 顺序真发（逐请求重连，握手不计速）。速率一律返回字节/秒。
    URL 可注入，冒烟走 127.0.0.1 回环不打真网。"""

    DOWN_URL = "https://speed.cloudflare.com/__down?bytes={n}"
    UP_URL = "https://speed.cloudflare.com/__up"
    # 浏览器 UA：CF 边缘 WAF 对非浏览器 UA 间歇拦 403，伪装成浏览器流量后实测稳定 200
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
          " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

    def __init__(self, down_url=None, up_url=None, timeout=15):
        self.down_url = down_url or self.DOWN_URL
        self.up_url = up_url or self.UP_URL
        self.timeout = timeout

    def _conn(self, url):
        u = urlsplit(url)
        scheme = u.scheme.lower()
        if scheme not in ("http", "https") or not u.hostname:
            raise ValueError("不支持的测速 URL")
        port = u.port or (443 if scheme == "https" else 80)
        origin = "{}://{}".format(scheme, u.hostname) + \
            (":" + str(u.port) if u.port is not None else "")
        return _WinHttpConnection(u.hostname, port, self.timeout, scheme == "https"), \
            (u.path or "/") + ("?" + u.query if u.query else ""), origin

    @staticmethod
    def public_identity(timeout=8):
        """通过 HTTPS 获取出口公网 IP 与 IP 归属地；失败不影响实际测速。"""
        conn, path, _ = SpeedTest(timeout=timeout)._conn("https://ipapi.co/json/")
        try:
            conn.connect()
            conn.request("GET", path, headers={
                "User-Agent": SpeedTest.UA,
                "Accept": "application/json",
            })
            response = conn.getresponse()
            raw = response.read(65536)
            if response.status != 200:
                raise RuntimeError("IP 查询 HTTP {}".format(response.status))
            data = json.loads(raw.decode("utf-8", errors="replace"))
            ip = str(data.get("ip") or "").strip()
            if not ip:
                raise RuntimeError("IP 查询结果为空")
            fields = (data.get("city"), data.get("region"), data.get("country_name"))
            location = " · ".join(dict.fromkeys(str(v).strip() for v in fields if v))
            return ip, location or "归属地未知"
        finally:
            conn.close()

    def _send(self, conn, method, path, origin, payload=None):
        # 同源上下文：CF 边缘对无 Origin/Referer 的大额 __down 直接 403（小额也降速），必须带上
        h = {"User-Agent": self.UA, "Accept-Encoding": "identity",
             "Origin": origin, "Referer": origin + "/"}
        if payload is not None:
            h["Content-Type"] = "application/octet-stream"
        conn.request(method, path, body=payload, headers=h)
        r = conn.getresponse()
        if r.status != 200:
            r.read()
            raise RuntimeError("HTTP {}".format(r.status))
        return r

    def ping(self, times=3):
        """预热连接后小包往返取最小 RTT（ms）；全失败抛最后一个异常。"""
        conn, path, origin = self._conn(self.down_url.format(n=1))
        best, err = None, None
        try:
            conn.connect()
            # WinHTTP 在首个 SendRequest 时才完成 DNS/TCP/TLS；先发一次 1 字节预热，不计入 RTT。
            self._send(conn, "GET", path, origin).read()
            for _ in range(times):
                t0 = time.perf_counter()
                self._send(conn, "GET", path, origin).read()
                rt = (time.perf_counter() - t0) * 1000.0
                best = rt if best is None else min(best, rt)
        except Exception as e:
            err = e
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if best is None:
            raise err or RuntimeError("ping 失败")
        return best

    def download(self, total, cap_seconds=8, progress=None):
        """单请求流式拉 total 字节（超 cap_seconds 截停按实收算速率），返回 (字节, 秒, B/s)。"""
        conn, path, origin = self._conn(self.down_url.format(n=total))
        got = 0
        try:
            conn.connect()
            # 与原 socket.connect 语义对齐：先完成首轮 DNS/TCP/TLS 握手，再开始统计吞吐。
            warm_u = urlsplit(self.down_url.format(n=1))
            warm_path = (warm_u.path or "/") + ("?" + warm_u.query if warm_u.query else "")
            self._send(conn, "GET", warm_path, origin).read()
            t0 = time.perf_counter()
            r = self._send(conn, "GET", path, origin)
            while got < total:
                chunk = r.read(65536)
                if not chunk:
                    break
                got += len(chunk)
                dt = time.perf_counter() - t0
                if progress:
                    progress(got, dt)
                if dt >= cap_seconds:
                    break
        finally:
            try:
                conn.close()
            except Exception:
                pass
        dt = max(time.perf_counter() - t0, 1e-6)
        return got, dt, got / dt

    def upload(self, total, chunk_max=1024 * 1024):
        """顺序 POST total 字节随机负载；逐请求重连但连接/握手不计速（不赌服务端 keep-alive），
        计时只含发送+响应。返回 (字节, 秒, B/s)。"""
        chunk = min(chunk_max, max(total, 1))
        n_req = max(1, round(total / chunk))
        payload = os.urandom(chunk)
        sent, tsum = 0, 0.0
        for _ in range(n_req):
            conn, path, origin = self._conn(self.up_url)
            try:
                conn.connect()
                h = {"User-Agent": self.UA, "Accept-Encoding": "identity",
                     "Origin": origin, "Referer": origin + "/",
                     "Content-Type": "application/octet-stream"}
                # SendRequest 先完成 DNS/TCP/TLS 并发送请求头；计时只覆盖请求体与响应。
                conn.begin_request("POST", path, h, len(payload))
                t0 = time.perf_counter()
                conn.write_body(payload)
                r = conn.getresponse()
                if r.status != 200:
                    r.read()
                    raise RuntimeError("HTTP {}".format(r.status))
                r.read()
                tsum += time.perf_counter() - t0
                sent += chunk
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        dt = max(tsum, 1e-6)
        return sent, dt, sent / dt


class NetSpeedWidget(QWidget):
    """悬浮网速条（R93 基准壳）：实时上/下行 + 本次累计 + 一键测速；
    半透 QFrame 卡片+阴影（半透为用户明确要求保留项），整窗拖动，×/双击/Esc 关闭。
    浮条紧凑变体：标题栏自适应行高、× 22×22（MASTER §6 变体注）。"""

    def __init__(self):
        super().__init__(None)
        self.setWindowTitle(f"{C.APP_NAME} · 网速")
        self.setWindowFlags(C.utility_window_flags(on_top=True))
        self.setAttribute(Qt.WA_TranslucentBackground, True)   # 半透保留项
        self.setStyleSheet(
            "QLabel{color:#e5e7eb;font-size:12px;background:transparent;"
            "font-family:'Microsoft YaHei UI';}"
            "QFrame#nsBody{background:rgba(16,20,30,202);border-radius:10px;"   # R146 收 10
            "border:1px solid rgba(255,255,255,18);}"
            "QLabel#ns_title{color:#f9fafb;font-size:13px;font-weight:600;}"
            "QPushButton{background:transparent;border:none;border-radius:6px;"
            "color:#9ca3af;font-size:13px;padding:2px 6px;}"
            "QPushButton:hover{color:#e5e7eb;background:rgba(255,255,255,26);}"
            "QPushButton#ns_close{background:transparent;border:none;border-radius:6px;padding:0;}"
            "QPushButton#ns_close:hover{background:rgba(255,255,255,46);}"
            "QPushButton#ns_close:pressed{background:rgba(255,255,255,68);}"
            "QPushButton#ns_test{background:rgba(59,130,246,18);"
            "border:1px solid #4a5bd6;color:#60a5fa;"
            "font-size:11.5px;padding:2px 12px;border-radius:9px;}"
            "QPushButton#ns_test:hover{background:rgba(59,130,246,40);color:#fff;}"
            "QPushButton#ns_test:pressed{background:rgba(59,130,246,64);color:#fff;}"
            "QPushButton#ns_test:disabled{background:rgba(255,255,255,10);"
            "border-color:rgba(255,255,255,30);color:#6b7280;}")
        self._drag_at = None
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)     # 四边阴影留白（基准壳）
        root.setSpacing(0)
        self.body = QFrame(self)
        self.body.setObjectName("nsBody")
        _sh = QGraphicsDropShadowEffect(self)
        _sh.setBlurRadius(26)
        _sh.setOffset(0, 5)
        _sh.setColor(QColor(0, 0, 0, 130))
        self.body.setGraphicsEffect(_sh)
        root.addWidget(self.body)
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(12, 6, 8, 10)
        bl.setSpacing(4)
        head = QHBoxLayout()                        # 徽章标题栏（整窗可拖，不再单设 eventFilter）
        head.setSpacing(8)
        badge = QLabel("速", self.body)
        badge.setFixedSize(22, 22)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                            " stop:0 #60a5fa, stop:1 #2563eb); color:#ffffff;"
                            " font-size:11px; font-weight:700; border-radius:7px;")
        head.addWidget(badge)
        t = QLabel("网速", self.body)
        t.setObjectName("ns_title")          # 选择器名沿用：冒烟固化条款
        head.addWidget(t)
        head.addStretch(1)
        self.test_btn = QPushButton("测速", self.body)
        self.test_btn.setObjectName("ns_test")
        self.test_btn.setToolTip("宽带测速：Ping / 下载 / 上传")
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.clicked.connect(self._on_test)
        head.addWidget(self.test_btn)
        add_minimize_button(self, head, 22)
        x = _HoverLucidePushButton("x", 16, self.body)  # R176：独立 hover 样式与事件状态，不受通用按钮内边距影响
        x.setObjectName("ns_close")
        x.setFixedSize(22, 22)
        x.setToolTip("关闭悬浮网速（Esc）")
        x.clicked.connect(self.close)
        head.addWidget(x)
        bl.addLayout(head)
        self.up = QLabel("↑ 0 B/s", self.body)
        self.dn = QLabel("↓ 0 B/s", self.body)
        rate = QHBoxLayout()              # 上/下行同行：浮条更矮更整齐
        rate.setSpacing(14)
        rate.addWidget(self.up)
        rate.addWidget(self.dn)
        rate.addStretch(1)
        bl.addLayout(rate)
        self.tot = QLabel("本次累计 ↑ 0 B · ↓ 0 B", self.body)
        self.tot.setStyleSheet("color:#6b7280;font-size:11px;background:transparent;")
        bl.addWidget(self.tot)
        self.ip_info = QLabel("", self.body)
        self.ip_info.setObjectName("ns_ip_info")
        self.ip_info.setWordWrap(True)
        self.ip_info.setTextFormat(Qt.PlainText)
        self.ip_info.setStyleSheet("color:#9ca3af;font-size:11px;background:transparent;")
        self.ip_info.hide()
        bl.addWidget(self.ip_info)
        self.res1 = QLabel("", self.body)            # 测速行 1：进行中实时速率 / Ping+下载
        self.res1.setStyleSheet("color:#e5e7eb;font-size:11.5px;background:transparent;")
        self.res2 = QLabel("", self.body)            # 测速行 2：上传速率（与下载同权亮色）/ 失败原因
        self.res2.setStyleSheet("color:#e5e7eb;font-size:11.5px;background:transparent;")
        bl.addWidget(self.res1)
        bl.addWidget(self.res2)
        self.res1.hide()
        self.res2.hide()
        self.setFixedWidth(256)           # 卡片 232（分阶段失败原因不截断）+ 两侧阴影留白
        self._testing = False
        self._st = None
        self._poll = QTimer(self)         # 后台线程结果轮询回填（同 fsearch 的线程+QTimer 模式）
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._poll_test)
        self._s0, self._r0 = self._counters()      # 会话起点，算累计
        self._prev = (self._s0, self._r0)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.close()                            # 基准：Esc 即关
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_at = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, ev):
        if self._drag_at is not None and ev.buttons() & Qt.LeftButton:
            self.move(ev.globalPosition().toPoint() - self._drag_at)

    def mouseReleaseEvent(self, ev):
        self._drag_at = None

    def _counters(self):
        try:
            import psutil
            c = psutil.net_io_counters()
            return c.bytes_sent, c.bytes_recv
        except Exception:
            return 0, 0

    def refresh(self):
        now = self._counters()
        dt = max(self.timer.interval() / 1000.0, 1e-6)
        up = (now[0] - self._prev[0]) / dt
        dn = (now[1] - self._prev[1]) / dt
        self._prev = now
        self.up.setText(f"↑ {_fmt_speed(max(up, 0))}")
        self.dn.setText(f"↓ {_fmt_speed(max(dn, 0))}")
        self.tot.setText(f"本次累计 ↑ {_fmt_bytes(max(now[0] - self._s0, 0))} · "
                         f"↓ {_fmt_bytes(max(now[1] - self._r0, 0))}")

    def mouseDoubleClickEvent(self, ev):
        self.close()

    def closeEvent(self, ev):
        self.timer.stop()
        self._poll.stop()
        super().closeEvent(ev)

    # ---- 一键测速：后台线程真收发，150ms 轮询回填（QTimer 回 UI 线程，不在工作线程碰控件） ----
    def _on_test(self):
        self._start_test()

    def _start_test(self, down_url=None, up_url=None):
        if self._testing:
            return
        self._testing = True
        self._st = {"phase": "ping", "down_url": down_url or SpeedTest.DOWN_URL,
                    "up_url": up_url or SpeedTest.UP_URL,
                    "down_bytes": 15 * 1024 * 1024, "up_bytes": 6 * 1024 * 1024,
                    "identity_done": False}
        self.test_btn.setEnabled(False)
        self.test_btn.setText("…")
        self.ip_info.setText("公网 IP 与归属地查询中…")
        self.ip_info.setToolTip("")
        self.ip_info.show()
        self.res1.show()
        self.res2.show()
        self.res1.setText("测速中…")
        self.res2.setText("")
        threading.Thread(target=self._test_run, args=(self._st,), daemon=True).start()
        threading.Thread(target=self._query_public_identity, args=(self._st,), daemon=True).start()
        self._poll.start()

    def _query_public_identity(self, st):
        try:
            host = urlsplit(st["down_url"]).hostname
            if host in ("127.0.0.1", "localhost", "::1"):
                st["identity_text"] = "本地测速 · 未查询公网 IP"
            else:
                ip, location = SpeedTest.public_identity()
                st["identity_text"] = f"公网 IP：{ip} · {location}"
                st["identity_tip"] = f"公网 IP：{ip}\nIP 归属地：{location}"
        except Exception as e:
            st["identity_text"] = "公网 IP / 归属地暂不可用"
            st["identity_tip"] = f"查询失败：{type(e).__name__}: {e}"
        finally:
            st["identity_done"] = True

    def _test_run(self, st):
        label = "Ping"                       # 当前阶段中文名：失败时回显「XX失败 · 原因」

        def _retry(fn):
            try:
                return fn()
            except Exception:
                time.sleep(0.4)              # 瞬时 403/网络抖动自动补试一次，不再让用户手点
                return fn()

        try:
            t = SpeedTest(down_url=st["down_url"], up_url=st["up_url"])
            st["ping"] = _retry(lambda: t.ping())
            st["phase"], label = "down", "下载"
            got, dt, _ = _retry(lambda: t.download(
                st["down_bytes"],
                progress=lambda g, d: st.update(rate=g / max(d, 1e-6))))
            if got < 65536:
                raise RuntimeError("下载数据异常")
            st["down_rate"] = got / dt
            st["phase"], label = "up", "上传"
            _, _, st["up_rate"] = _retry(lambda: t.upload(st["up_bytes"]))
            st["phase"] = "done"
        except Exception as e:
            st["phase"] = "error"
            st["msg"] = f"{label}失败 · {type(e).__name__}: {e}"[:70]

    def _poll_test(self):
        st = self._st
        if st is None:
            return
        if st.get("identity_text"):
            self.ip_info.setText(st["identity_text"])
            self.ip_info.setToolTip(st.get("identity_tip", st["identity_text"]))
        ph = st.get("phase")
        if ph == "ping":
            self.res1.setText("测速中 · Ping…")
        elif ph == "down":
            self.res1.setText(f"测速中 · ↓ {_fmt_speed(st.get('rate', 0.0))}")
        elif ph == "up":
            self.res1.setText(f"Ping {st.get('ping', 0):.0f} ms · ↓ {_fmt_speed(st.get('down_rate', 0.0))}")
            self.res2.setText("测速中 · ↑ …")   # upload 无逐字节进度回调，只报阶段不报实时速率
        elif ph == "done":
            self.res1.setText(f"Ping {st.get('ping', 0):.0f} ms · ↓ {_fmt_speed(st.get('down_rate', 0.0))}")
            self.res2.setText(f"↑ {_fmt_speed(st.get('up_rate', 0.0))}")
            if st.get("identity_done", True):
                self._end_test()
        elif ph == "error":
            self.res1.setText("测速失败 · 检查网络")
            self.res2.setText(st.get("msg", ""))
            if st.get("identity_done", True):
                self._end_test()

    def _end_test(self):
        self._testing = False
        self._poll.stop()
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测速")
