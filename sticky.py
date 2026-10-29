# -*- coding: utf-8 -*-
"""简截 JianJie — 任务清单桌面插件：
半透明无边框小窗常驻桌面，随手记任务（回车连记＋竖条选择优先级）、勾选成功过渡
（打勾划线→延迟→收缩淡出，下一条自动提上来）、整行拖拽排序（hover 行显删除钮）、
完成区默认折叠沉底；500ms 防抖本地 JSON 存储，id/updated/deleted/order 字段为云端同步预留。
由 R85 自由文本便签按用户真实意图原位改造（热键 action id 复用 note）。"""
import math
import time
import uuid

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent,
                            QParallelAnimationGroup, QPoint,
                            QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QColor, QFont, QIcon, QLinearGradient, QPainter, QPalette,
                           QPainterPath, QPen, QPixmap, QTextCursor, QTextDocument, QTextOption)
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

import common as C
import icons
import task_schedule as TS

PRIO_COLORS = {0: "", 1: "#60a5fa", 2: "#fbbf24", 3: "#ef4444"}   # 无/低/中/高
PRIO_TIPS = {0: "无优先级", 1: "低优先级", 2: "中优先级", 3: "高优先级"}

# R98 优先级下拉菜单样式：R100 起统一下沉为 common.UI_MENU_QSS（QMenu 不透顶层窗，必须自带）


def _dot_icon(color):
    """优先级菜单色点小图标；无色档画灰描边空心圈。"""
    pm = QPixmap(14, 14)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    if color:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
    else:
        p.setPen(QPen(QColor("#9ca3af"), 1.4))   # R137 黑透回归：菜单无色档描边回深色系灰
        p.setBrush(Qt.NoBrush)
    p.drawEllipse(3, 3, 8, 8)
    p.end()
    return QIcon(pm)

_DONE_DELAY_MS = 320      # R89 勾选成功过渡：打勾划线停留时长（看得见的反馈）
_DONE_ANIM_MS = 260       # 收缩淡出动画时长（下一条随 layout 自动提上来）
_DRAG_THRESHOLD = 8       # R89 拖拽排序触发位移（px），小于此视为点击/双击

def _paint_task_shadow(painter, rect, bounds):
    """只画外侧阴影，不将输入框和整张列表转成模糊效果的离屏图像。"""
    panel = QPainterPath()
    panel.addRoundedRect(rect, 10, 10)
    outside = QPainterPath()
    outside.addRect(bounds)
    painter.save()
    painter.setClipPath(outside.subtracted(panel), Qt.IntersectClip)
    painter.setPen(Qt.NoPen)
    for spread in range(12, 0, -1):
        alpha = max(1, round(17 * math.exp(-.5 * (spread / 6) ** 2)))
        painter.setBrush(QColor(0, 0, 0, alpha))
        painter.drawRoundedRect(rect.translated(0, 4).adjusted(-spread, -spread, spread, spread),
                                10 + spread, 10 + spread)
    painter.restore()


_CSS = """
QFrame#taskBody { background:rgba(16,20,30,202); border-radius:10px;   /* R146 圆角收 mac 档 14→10 */
  border:1px solid rgba(255,255,255,18); }               /* R137 黑透壳回归（用户点名「还是用原来透明的样式，黑透」；R89 基准 alpha202） */
QLabel { background:transparent; }
QToolButton { background:transparent; border:none; color:#9ca3af;
  font-size:13px; padding:2px 6px; border-radius:6px; }
QToolButton#headBtn { font-size:17px; padding:0; }   /* R127 头部钉/叉再加大 */
QToolButton:hover { background:rgba(255,255,255,14); color:#ffffff; }
QToolButton:checked { color:#60a5fa; background:rgba(96,165,250,26); }
QPlainTextEdit { background:transparent; border:none; color:#eef1f7; padding:0; }
QFrame#taskRow, QFrame#taskRowDone { background:transparent; border-radius:6px;
  /* R130 分隔线改行内自绘：从文字左缘起笔（勾选框/竖线留位，滴答同款缩进），不再贯穿全宽 */
  }
QFrame#taskRow:hover, QFrame#taskRowDone:hover { background:rgba(255,255,255,10); }
QToolButton#taskCheck { border:1.5px solid rgba(255,255,255,95); border-radius:5px;
  background:transparent; color:#ffffff; font-size:11px; font-weight:700; padding:0; }
QToolButton#taskCheck:hover { border-color:rgba(255,255,255,150); }
QToolButton#taskCheck:checked { border-color:#2563eb;
  background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #60a5fa, stop:1 #2563eb); }
QToolButton#rowDel { color:#9aa3b2; font-size:13px; padding:0; border-radius:6px; }
QToolButton#rowDel:hover { color:#ffffff; background:#ef4444; }
QFrame#doneHdr { background:transparent; border:none;
  border-top:1px solid rgba(255,255,255,12); }
QFrame#doneHdr:hover { background:rgba(255,255,255,8); }
QScrollArea { background:transparent; border:none; }
QWidget#taskList { background:transparent; }
QScrollBar:vertical { background:transparent; width:6px; margin:2px; }
QScrollBar::handle:vertical { background:rgba(255,255,255,42); border-radius:3px;
  min-height:30px; }
QScrollBar::handle:vertical:hover { background:rgba(255,255,255,84); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
"""
_CSS = (_CSS.replace("rgba(16,20,30,202)", C.UI_TASK_BG)
        .replace("#9ca3af", C.UI_TASK_CONTROL).replace("#eef1f7", C.UI_TASK_TEXT))
_CSS += f"""
QToolButton#rowMore {{ color:{C.UI_TASK_CONTROL}; padding:0; border-radius:6px; }}
QToolButton#rowMore:hover {{ background:rgba(255,255,255,26); }}
QToolButton:focus {{ border:1px solid {C.UI_TASK_CONTROL}; }}
QPlainTextEdit {{ placeholder-text-color:{C.UI_TASK_MUTED}; }}
"""


class _RowCheck(QWidget):
    """R126 自绘勾选框：矢量圆角矩形+对勾，任何 DPR 下边缘锐利（原 11px 文本✓发虚）。
    R128 回退优先级色——优先级表达移交 _PrioBar 竖线，勾选框统一蓝系（两色彩叠显乱）。"""

    toggled = Signal(bool)

    def __init__(self, parent, checked=False):
        super().__init__(parent)
        self._on = bool(checked)
        self._hov = False
        self.setFixedSize(18, 22)                    # R129 22px 槽高：与行内×/文字同槽，中心线对齐
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self):
        return self._on

    def setChecked(self, on):
        on = bool(on)
        if on != self._on:
            self._on = on
            self.update()
            self.toggled.emit(self._on)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.setChecked(not self._on)
        super().mouseReleaseEvent(ev)

    def enterEvent(self, ev):
        self._hov = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hov = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(2, 4, -2, -4)       # R130 视觉 14px（滴答同档小框），槽内居中
        if self._on:
            grad = QLinearGradient(r.topLeft(), r.bottomRight())
            grad.setColorAt(0, QColor("#60a5fa"))
            grad.setColorAt(1, QColor("#2563eb"))
            p.setPen(QPen(QColor(C.UI_TASK_CONTROL), 1.2))
            p.setBrush(grad)
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QPen(QColor("#ffffff"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(r.left() + r.width() * 0.24, r.top() + r.height() * 0.52)
            path.lineTo(r.left() + r.width() * 0.43, r.top() + r.height() * 0.72)
            path.lineTo(r.left() + r.width() * 0.78, r.top() + r.height() * 0.30)
            p.drawPath(path)
        else:
            p.setPen(QPen(QColor(C.UI_TASK_TEXT if self._hov else C.UI_TASK_CONTROL), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 4, 4)
        p.end()


class _PrioBar(QWidget):
    """R128 优先级竖线（替 R127 圆点钮）；R153 矮胖化（用户点名「加宽一点、高度变小一点」）：
    有级=优先级色圆头胶囊条 4.5×11 槽内垂直居中（原 3.2×19 细长，宽高比 1:5.9→1:2.4，仍读作竖条），
    hover 提亮微粗；无级=3×7 浅痕（可点提示）。点击仍弹四档下拉；10px 宽透明热区保证可点。
    可选 on_click 用于复用在新增任务行。"""

    def __init__(self, row, prio=0, on_click=None):
        super().__init__(row)
        self.row = row
        self.prio = prio
        self._on_click = on_click
        self._hov = False
        self.setFixedSize(10, 22)                    # R129 同 22px 槽（绘制本已按 height 自适应）
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(PRIO_TIPS.get(prio, "") + "（点击修改）")

    def enterEvent(self, ev):
        self._hov = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hov = False
        self.update()
        super().leaveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()):
            (self._on_click or self.row._pick_row_prio)()
        super().mouseReleaseEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pc = {1: C.UI_TASK_PRIORITY_LOW, 2: C.UI_TASK_PRIORITY_MEDIUM,
              3: C.UI_TASK_PRIORITY_HIGH}.get(self.prio, "")
        cx = self.width() / 2
        if pc:
            w = 5.4 if self._hov else 4.5            # R153 矮胖：宽 3.2→4.5（hover 微粗半档保留）
            bh = 11.0                                # R153 矮胖：高 19→11、槽内垂直居中
            y0 = (self.height() - bh) / 2
            col = QColor(pc).lighter(130 if self._hov else 100)
            grad = QLinearGradient(0, y0, 0, y0 + bh)
            grad.setColorAt(0, QColor(col).lighter(112))   # 顶端微亮：视觉有光不愣
            grad.setColorAt(1, col)
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(cx - w / 2, y0, w, bh), w / 2, w / 2)
        else:
            p.setPen(Qt.NoPen)                        # 无级：3×7 浅痕=「这里能点」的占位提示（R153 同步矮胖，R137 白系 46/hover120）
            p.setBrush(QColor(C.UI_TASK_TEXT if self._hov else C.UI_TASK_CONTROL))
            p.drawRoundedRect(QRectF(cx - 1.5, (self.height() - 7.0) / 2, 3.0, 7.0), 1.5, 1.5)
        p.end()


class _TaskTextEdit(QPlainTextEdit):
    """任务输入/编辑框：软换行并按内容高度展开；回车仍提交，与原单行输入一致。"""

    returnPressed = Signal()
    editingFinished = Signal()
    heightChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._height_floor = 0
        self._syncing_height = False
        self._vertical_padding = 2
        self._preedit_text = ""
        self._placeholder_text = ""
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._sync_height)
        self.setObjectName("taskText")
        self.setFrameShape(QFrame.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.setViewportMargins(0, 2, 0, 2)
        palette = self.palette()
        palette.setColor(QPalette.PlaceholderText, QColor(C.UI_TASK_MUTED))
        palette.setColor(QPalette.Text, QColor(C.UI_TASK_TEXT))
        palette.setColor(QPalette.Base, Qt.transparent)
        palette.setColor(QPalette.Window, Qt.transparent)
        self.setPalette(palette)
        self.viewport().setPalette(palette)
        self.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.viewport().setAttribute(Qt.WA_NoSystemBackground, False)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.textChanged.connect(self._queue_height)
        self._sync_height()

    def text(self):
        return self.toPlainText()             # 兼容任务清单原有输入接口

    def setText(self, text):
        self.setPlainText(str(text))

    def setPlaceholderText(self, text):
        self._placeholder_text = str(text)
        super().setPlaceholderText("" if self._preedit_text else self._placeholder_text)

    def placeholderText(self):
        return self._placeholder_text

    def setHeightFloor(self, height):
        height = max(0, int(height))
        if self._height_floor == height:
            return
        self._height_floor = height
        self._sync_height()

    def setVerticalPadding(self, padding):
        padding = max(0, int(padding))
        if self._vertical_padding == padding:
            return
        self._vertical_padding = padding
        self.setViewportMargins(0, self._vertical_padding, 0, self._vertical_padding)
        self._sync_height()

    def _queue_height(self):
        if hasattr(self, "_height_timer") and not self._height_timer.isActive():
            self._height_timer.start(0)

    def _sync_height(self):
        if not hasattr(self, "_height_floor") or self._syncing_height:
            return
        self._height_timer.stop()
        self._syncing_height = True
        height_changed = False
        try:
            # QPlainTextDocumentLayout.documentSize 的高度是行数，并非像素。
            # 用实际排版后的段落高度，避免软换行后裁切或滚动挤走上一行。
            layout = self.document().documentLayout()
            block = self.document().begin()
            doc_h = 0.0
            while block.isValid():
                doc_h += layout.blockBoundingRect(block).height()
                block = block.next()
            padding = self._vertical_padding * 2
            target = max(self._height_floor, self.fontMetrics().lineSpacing() + padding,
                         math.ceil(doc_h) + padding)
            if target != self.height():
                self.setFixedHeight(target)
                height_changed = True
        finally:
            self._syncing_height = False
        if height_changed:
            self.heightChanged.emit(self.height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if ev.size().width() != ev.oldSize().width():
            self._queue_height()

    def inputMethodEvent(self, ev):
        # 预编辑还未写入 document；此时不能继续绘制“添加任务”占位文案。
        self._preedit_text = ev.preeditString()
        super().setPlaceholderText("" if self._preedit_text else self._placeholder_text)
        super().inputMethodEvent(ev)
        self._queue_height()
        self.viewport().update()

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            ev.accept()
            if self._preedit_text:
                # 第一次回车只确认输入法组词，下次回车才提交任务。
                QApplication.inputMethod().commit()
                return
            self.returnPressed.emit()
            return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.editingFinished.emit()


class _TaskAddBar(QFrame):
    """添加栏统一处理焦点、时间信息和高度，不在布局过渡中重新抛光样式。"""

    def __init__(self, parent):
        super().__init__(parent)
        self._focused = False
        self._syncing = False
        self._content = None
        self.setObjectName("taskAddBar")
        self.setFrameShape(QFrame.NoFrame)
        self.setAutoFillBackground(False)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(40)

    def bind(self, editor, metadata, content, slots):
        self._measure_doc = QTextDocument(self)
        self._measure_doc.setDocumentMargin(0)
        self._measure_doc.setDefaultTextOption(editor.document().defaultTextOption())
        self._editor, self._metadata, self._content, self._slots = editor, metadata, content, slots
        editor.heightChanged.connect(self.syncGeometry)
        self.syncGeometry()

    def heightForWidth(self, width):
        if self._content is None:
            return 40
        margins = self.layout().contentsMargins()
        used = margins.left() + margins.right() + sum(slot.width() for slot in self._slots)
        used += self.layout().spacing() * (self.layout().count() - 1)
        text_width = max(1, width - used)
        if self._measure_doc.defaultFont() != self._editor.font():
            self._measure_doc.setDefaultFont(self._editor.font())
        text = self._editor.toPlainText()
        if self._measure_doc.toPlainText() != text:
            self._measure_doc.setPlainText(text)
        if self._measure_doc.textWidth() != text_width:
            self._measure_doc.setTextWidth(text_width)
        input_height = max(self._editor._height_floor,
                           self._editor.height(),
                           math.ceil(self._measure_doc.size().height()) + self._editor._vertical_padding * 2)
        meta_height = 0
        if not self._metadata.isHidden():
            meta_height = self._metadata.heightForWidth(text_width)
            if meta_height < 0:
                meta_height = self._metadata.sizeHint().height()
            meta_height += self._content.layout().spacing()
        return max(40, margins.top() + input_height + meta_height + margins.bottom())

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width(), self.heightForWidth(self.width()))

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(hint.width(), 40)

    def setFocused(self, focused):
        if self._focused != bool(focused):
            self._focused = bool(focused)
            self.update()

    def setSchedule(self, text, clear_input=False):
        if self._metadata.text() == text and self._metadata.isHidden() == (not bool(text)):
            if clear_input:
                self._editor.clear()
            return
        owner = self.window()
        updates_enabled = owner.updatesEnabled()
        if updates_enabled:
            owner.setUpdatesEnabled(False)
        try:
            self._metadata.setText(text)
            self._metadata.setVisible(bool(text))
            if clear_input:
                self._editor.clear()
            self.syncGeometry()
        finally:
            if updates_enabled:
                owner.setUpdatesEnabled(True)
                owner.update()

    def syncGeometry(self, *_args):
        if self._content is None or self._syncing:
            return
        self._syncing = True
        try:
            line_height = max(24, self._editor.fontMetrics().lineSpacing() + 6)
            self._editor.setHeightFloor(line_height)
            for slot in self._slots:
                if slot.height() != line_height:
                    slot.setFixedHeight(line_height)
            # 内容使用独立 QWidget，隐藏时间时失效的是它自己的直接布局。
            # 同步外层缓存及父布局，收起和展开只呈现完整的最终帧。
            self._content.layout().invalidate()
            self._content.layout().activate()
            self._content.updateGeometry()
            self.layout().invalidate()
            self.layout().activate()
            self._editor._sync_height()
            self.updateGeometry()
            parent_layout = self.parentWidget().layout()
            if parent_layout is not None:
                parent_layout.invalidate()
                parent_layout.activate()
        finally:
            self._syncing = False
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.syncGeometry()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        border = C.UI_TASK_PRIORITY_LOW if self._focused else C.UI_TASK_INPUT_BORDER
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(*(C.UI_TASK_ADD_FOCUS_BG if self._focused else C.UI_TASK_ADD_BG)))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), C.UI_RADIUS, C.UI_RADIUS)


class _SmoothTaskScrollArea(QScrollArea):
    """鼠标滚轮短缓动；触控板像素滚动保持原生逐像素响应。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        bar = self.verticalScrollBar()
        self._wheel_anim = QPropertyAnimation(bar, b"value", self)
        self._wheel_anim.setDuration(180)
        self._wheel_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._wheel_target = None
        bar.sliderPressed.connect(self._stop_wheel_animation)
        bar.actionTriggered.connect(lambda _action: self._stop_wheel_animation())
        bar.rangeChanged.connect(lambda _min, _max: self._stop_wheel_animation())

    def _stop_wheel_animation(self):
        self._wheel_anim.stop()
        self._wheel_target = None

    def wheelEvent(self, ev):
        bar = self.verticalScrollBar()
        delta = ev.angleDelta().y()
        if (ev.pixelDelta().y() or ev.angleDelta().x()
                or ev.modifiers() & Qt.ControlModifier or not delta or not bar.maximum()):
            self._stop_wheel_animation()
            super().wheelEvent(ev)
            return
        # 高精度滚轮的小增量不加缓动，避免触控板/自由滚轮跟手延迟。
        if abs(delta) < 120:
            self._stop_wheel_animation()
            bar.setValue(bar.value() - round(delta * 64 / 120))
            ev.accept()
            return
        origin = (self._wheel_target if self._wheel_target is not None
                  and self._wheel_anim.state() == QAbstractAnimation.State.Running
                  else bar.value())
        self._wheel_target = max(bar.minimum(), min(bar.maximum(),
                                  origin - round(delta * 64 / 120)))
        self._wheel_anim.stop()
        self._wheel_anim.setStartValue(bar.value())
        self._wheel_anim.setEndValue(self._wheel_target)
        self._wheel_anim.start()
        ev.accept()


class TaskRow(QFrame):
    """单行任务：勾选框＋优先级竖条＋文字（双击就地编辑）＋ hover 删除×。
    R89：勾选走成功过渡（打勾划线→延迟→高度收缩淡出），过渡中再点=中止回滚；
    整行按住拖拽排序（label 不消费鼠标 press，事件传播到行；子控件天然避让）。"""

    def __init__(self, widget, task, done):
        super().__init__(None)
        self.widget = widget                       # TaskWidget 回调宿主
        self.task = task
        self.done = done
        self._render_key = self.render_key(task, done)
        self._completing = False                   # 完成过渡进行中
        self._committed = False                    # 过渡已落数据（防二次提交）
        self._trans_to_done = done                 # 本次过渡目标态
        self._press_g = None                       # 拖拽起点（全局坐标）
        self._dragging = False
        self.setObjectName("taskRowDone" if done else "taskRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(44)                    # R127 长任务多行全显：下限 44、随内容撑高
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 11, 6, 11)         # R129 11+22+11=44 行高不变，槽中心锁 y=22
        lay.setSpacing(5)                            # R126 间距压缩：内容区让出更多

        self.box = _RowCheck(self, done)           # R126 自绘矢量勾选框
        self.box.setToolTip("取消勾选：回到待办" if done else "勾选完成")
        self.box.toggled.connect(self._on_toggled)
        lay.addWidget(self.box, 0, Qt.AlignTop)

        self.prio_bar = _PrioBar(self, task.get("prio", 0))   # R128 优先级竖线（与勾选框同高）
        lay.addWidget(self.prio_bar, 0, Qt.AlignTop)

        self.label = QLabel(task.get("text", ""), self)
        self.label.setWordWrap(True)               # R127 长任务全部展示（不收单行）
        f = self.label.font()
        f.setStrikeOut(done)                       # 完成态划线（照滴答清单）
        f.setPixelSize(14)                         # R125 照滴答清单：字号加大
        self.label.setFont(f)
        self.label.setStyleSheet(
            "color:%s; background:transparent;" % (C.UI_TASK_MUTED if done else C.UI_TASK_TEXT))
        self.content = QWidget(self)
        self.content_lay = QVBoxLayout(self.content)
        self.content_lay.setContentsMargins(0, 0, 0, 0)
        self.content_lay.setSpacing(4)
        self.label.setMinimumHeight(22)
        self.content_lay.addWidget(self.label)
        self.meta = QLabel(TS.metadata(task), self.content)
        self.meta.setWordWrap(True)
        self.meta.setStyleSheet(f"color:{C.UI_TASK_MUTED}; font-size:11px; background:transparent;")
        day = TS.due_date(task)
        if day and day < TS.date.today() and not done:
            self.meta.setText("已逾期 · " + self.meta.text())
            self.meta.setStyleSheet(f"color:{C.UI_TASK_OVERDUE}; font-size:11px; background:transparent;")
        self.meta.setVisible(bool(self.meta.text()))
        self.content_lay.addWidget(self.meta)
        lay.addWidget(self.content, 1, Qt.AlignTop)

        # R128：拖拽把手退役——整行按住即拖（mousePress/Move/Release 原生支持），行更干净
        self.delete_slot = QWidget(self)
        self.delete_slot.setFixedSize(24, 22)       # 永久保留右侧空间，悬停时文字不再跳动
        slot_lay = QHBoxLayout(self.delete_slot)
        slot_lay.setContentsMargins(1, 0, 1, 0)
        slot_lay.setSpacing(0)
        self.btn_del = QToolButton(self.delete_slot)
        self.btn_del.setObjectName("rowMore")
        self.btn_del.setIcon(icons.lucide_icon("ellipsis", C.UI_TASK_CONTROL, 16))
        self.btn_del.setIconSize(QSize(16, 16))
        self.btn_del.setToolTip("截止日期、提醒与删除")
        self.btn_del.setCursor(Qt.PointingHandCursor)
        self.btn_del.setFixedSize(22, 22)
        self.btn_del.setVisible(False)             # hover 才现身：列表常态干净
        self.btn_del.clicked.connect(lambda: self.widget.open_task_menu(self.task, self.btn_del))
        slot_lay.addWidget(self.btn_del)
        lay.addWidget(self.delete_slot, 0, Qt.AlignVCenter)  # 多行任务的删除槽保持垂直居中

    @staticmethod
    def render_key(task, done):
        day = TS.due_date(task)
        return (task.get("text", ""), task.get("prio", 0), bool(done), TS.metadata(task),
                bool(day and day < TS.date.today() and not done))

    # ---------- 勾选成功过渡 ----------
    def _on_toggled(self, on):
        if self._completing:                       # 过渡中任何再点 = 中止回滚
            self._abort_transition()
            return
        if on and not self.done:
            self._start_done_transition(True)
        elif not on and self.done:
            self._start_done_transition(False)

    def _start_done_transition(self, to_done):
        self._completing = True
        self._committed = False
        self._trans_to_done = to_done
        # 即时成功反馈：划线＋灰化先上身，勾已在框里——用户立刻看到「成了」
        f = self.label.font()
        f.setStrikeOut(to_done)
        self.label.setFont(f)
        self.label.setStyleSheet(
            "color:%s; background:transparent;" % (C.UI_TASK_MUTED if to_done else C.UI_TASK_TEXT))
        self._c_t = QTimer(self)
        self._c_t.setSingleShot(True)
        self._c_t.timeout.connect(self._start_shrink)
        self._c_t.start(_DONE_DELAY_MS if to_done else max(120, _DONE_DELAY_MS // 2))

    def _start_shrink(self):
        if not self._completing or self._committed:
            return
        self.setMinimumHeight(0)                   # 解除 fixed 锁：动画 maximumHeight 才有效
        self._op = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._op)
        self._anim = QParallelAnimationGroup(self)
        a1 = QPropertyAnimation(self, b"maximumHeight", self)
        a1.setDuration(_DONE_ANIM_MS)
        a1.setStartValue(max(0, self.height()))
        a1.setEndValue(0)
        a1.setEasingCurve(QEasingCurve.InCubic)    # 收缩先慢后快，退场利落
        a2 = QPropertyAnimation(self._op, b"opacity", self)
        a2.setDuration(_DONE_ANIM_MS)
        a2.setStartValue(1.0)
        a2.setEndValue(0.0)
        self._anim.addAnimation(a1)
        self._anim.addAnimation(a2)
        self._anim.finished.connect(self._commit_done)   # stop() 不发 finished，中止安全
        self._anim.start()

    def _commit_done(self):
        """动画播完（或 rebuild 收割/冒烟快进）：落数据 → rebuild。"""
        if self._committed:
            return
        self._committed = True
        self._completing = False
        if hasattr(self, "_c_t"):
            self._c_t.stop()
        self.widget.toggle_task(self.task["id"], self._trans_to_done)

    def _abort_transition(self):
        """过渡中再点勾选框：回滚视觉，数据从未变化。"""
        if hasattr(self, "_c_t"):
            self._c_t.stop()
        if hasattr(self, "_anim"):
            self._anim.stop()                      # stop 不触发 finished → 不会误落数据
        self._completing = False
        self._committed = False
        self.setGraphicsEffect(None)
        self.setMinimumHeight(44)                  # R127 复原：下限 44 + 解除上限（多行自适应）
        self.setMaximumHeight(16777215)
        f = self.label.font()
        f.setStrikeOut(self.done)
        self.label.setFont(f)
        self.label.setStyleSheet(
            "color:%s; background:transparent;" % (C.UI_TASK_MUTED if self.done else C.UI_TASK_TEXT))

    # ---------- 拖拽排序 ----------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self.done and not self.widget._editing:
            self._press_g = ev.globalPosition().toPoint()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._press_g is not None:
            gp = ev.globalPosition().toPoint()
            if not self._dragging and (gp - self._press_g).manhattanLength() > _DRAG_THRESHOLD:
                self._dragging = True
                self.widget.drag_begin(self)
            if self._dragging:
                self.widget.drag_move(self, gp.y())
                return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            if self._dragging:
                self._dragging = False
                self.widget.drag_release(self)
            self._press_g = None
        super().mouseReleaseEvent(ev)

    def _pick_row_prio(self):
        """R98/R128：点优先级竖线 → 下拉改级。"""
        m = self.widget._prio_menu(self.task.get("prio", 0),
                                   lambda p: self.widget._set_row_prio(self, p))
        m.exec(self.prio_bar.mapToGlobal(self.prio_bar.rect().bottomLeft()))

    def paintEvent(self, ev):
        """R130：分隔线自绘——从文字左缘起笔画到行右缘（勾选框/竖线区不穿线，滴答同款缩进）。"""
        super().paintEvent(ev)
        p = QPainter(self)
        p.setPen(QColor(255, 255, 255, 9))              # R137 黑透回归（R89 原值行底细分隔线）
        p.drawLine(self.content.x(), self.height() - 1, self.width() - 8, self.height() - 1)
        p.end()

    # ---------- 原有交互 ----------
    def enterEvent(self, ev):
        self.btn_del.setVisible(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.btn_del.setVisible(False)
        super().leaveEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if ev.button() == Qt.LeftButton and not self._dragging:
            self._press_g = None
            self.widget.begin_edit(self)           # 双击就地编辑
            ev.accept()
            return
        super().mouseDoubleClickEvent(ev)


class _DoneHdr(QFrame):
    """已完成区头：▸/▾ 折叠箭头 + 计数 + 清除；整行点击切换折叠（清除钮自消费不冒泡）。"""

    def __init__(self, widget, n):
        super().__init__(None)
        self.widget = widget
        self.setObjectName("doneHdr")
        self.setFixedHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点击展开 / 收起已完成")
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 6, 8, 0)
        h.setSpacing(4)
        self.arrow = QLabel("▸" if widget._done_collapsed else "▾", self)
        self.arrow.setStyleSheet(f"color:{C.UI_TASK_MUTED}; font-size:12px; background:transparent;")
        h.addWidget(self.arrow)
        lb = QLabel(f"已完成 {n}", self)
        lb.setStyleSheet(f"color:{C.UI_TASK_MUTED}; font-size:12px; background:transparent;")
        h.addWidget(lb)
        h.addStretch()
        clr = QToolButton(self)
        clr.setText("清除")
        clr.setToolTip("删除全部已完成任务")
        clr.setCursor(Qt.PointingHandCursor)
        clr.setStyleSheet(f"font-size:12px; color:{C.UI_TASK_CONTROL};")
        clr.clicked.connect(lambda: widget.confirm_clear_done(clr))
        h.addWidget(clr)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.widget.toggle_done_collapsed()
            return
        super().mousePressEvent(ev)


def task_window_rect(rect, areas, preferred=None):
    """恢复窗口到当前逻辑工作区；保留有效的副屏负坐标，避开屏幕间空隙。"""
    rect = QRect(rect)
    areas = [QRect(area) for area in areas if area.isValid()]
    if not areas or any(area.contains(rect) for area in areas):
        return rect
    overlap = [max(0, area.intersected(rect).width()) * max(0, area.intersected(rect).height())
               for area in areas]
    if max(overlap) > 0:
        area = areas[overlap.index(max(overlap))]
    else:
        area = QRect(preferred) if preferred is not None and preferred in areas else areas[0]
    inner = area.adjusted(8, 8, -8, -8)
    if not inner.isValid():
        inner = area
    width, height = min(rect.width(), inner.width()), min(rect.height(), inner.height())
    x = max(inner.left(), min(rect.x(), inner.right() - width + 1))
    y = max(inner.top(), min(rect.y(), inner.bottom() - height + 1))
    return QRect(x, y, width, height)


class TaskWidget(QWidget):
    """任务清单窗口：标题栏拖动 / 添加行（回车连记＋优先级竖条）/
    待办（可拖拽排序）＋已完成（默认折叠沉底）/ 一键清除完成 / 右下角缩放 / 钉置顶。"""

    def __init__(self, manager):
        super().__init__(None)
        self.manager = manager
        self._drag_off = None
        self._prio = 0                             # 添加行当前优先级
        self._draft_schedule = {}
        self._editing = None                       # (row, editor) 编辑会话：重建避让
        self._rebuild_pending = False
        self._drag_row = None                      # 正在拖拽排序的行
        self._done_collapsed = bool(manager.win.get("done_collapsed", True))   # R89 默认收起
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(C.utility_window_flags())
        self.setWindowTitle(f"{C.APP_NAME} · 任务清单")   # 否则 Alt+Tab/枚举显示进程名 pythonw
        self.setMinimumSize(250, 300)
        win = manager.win
        self.resize(max(250, win.get("w", 320)), max(300, win.get("h", 440)))
        self.move(win.get("x", 120), win.get("y", 120))
        # R150：钉态真相源从 windowFlags 转 _pinned 实例属性（flags 全程不含 StaysOnTop）；
        # 恢复持久化钉态走 Win32 改 Z 序——setWindowFlag 会销毁重建句柄（点钉闪窗根因）
        self._pinned = bool(win.get("ontop"))
        if self._pinned:
            C.set_topmost(self, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)     # 四边留白给自绘阴影
        self.body = QFrame(self)
        self.body.setObjectName("taskBody")
        self.body.setMouseTracking(True)           # 边框悬停时持续更新缩放光标
        # 阴影由窗口背景绘制，输入和光标不再使整个内容区离屏渲染、重新模糊。
        root.addWidget(self.body)
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(10, 6, 10, 8)
        bl.setSpacing(4)

        # 标题栏（拖动把手）：R125 照滴答清单收集箱——去徽章、列表名大标题 + 待办计数 + 钉/隐
        self.head = QWidget(self.body)
        self.head.setFixedHeight(38)
        self.head.installEventFilter(self)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(4, 0, 0, 0)
        hl.setSpacing(8)
        self.title = QLabel("任务清单", self.head)
        self.title.setStyleSheet(f"color:{C.UI_TASK_TEXT}; font-size:17px; font-weight:700;"
                                 " background:transparent;")   # R137 黑透回归
        hl.addWidget(self.title)
        self.count_lbl = QLabel("", self.head)      # 待办计数（rebuild 刷新）
        self.count_lbl.setStyleSheet(f"color:{C.UI_TASK_MUTED}; font-size:12px; background:transparent;")
        hl.addWidget(self.count_lbl)
        hl.addStretch()
        self.btn_pin = self._head_btn(hl, "", "置顶：钉在最前 / 再点取消", self.toggle_pin, checkable=True)
        self.btn_pin.setIconSize(QSize(14, 14))          # R137 钉叉视觉一致：钉缩 14（lucide pin 天然比 x 大 70%）
        self._sync_pin_icon(self._pinned)                # 内含 setChecked；默认不置顶
        from widgets import add_minimize_button
        add_minimize_button(self, hl)
        self.btn_hide = self._head_btn(hl, "", "隐藏（Alt+N 或托盘再呼出）", self.manager.hide_widget)
        self.btn_hide.setIcon(icons.lucide_icon("x", C.UI_TASK_CONTROL, 16))
        self.btn_hide.setIconSize(QSize(16, 16))                     # x16 bbox 8.9 ≈ pin14 bbox 宽 9.3，同宽协调
        bl.addWidget(self.head)

        # 添加行：蓝紫描边胶囊（参考滴答清单收集箱）
        self.add_row = _TaskAddBar(self.body)
        al = QHBoxLayout(self.add_row)
        al.setContentsMargins(12, 8, 8, 8)
        al.setSpacing(6)
        self.add_plus = QLabel(self.add_row)
        self.add_plus.setPixmap(icons.lucide("plus", C.UI_TASK_CONTROL, 15))
        self.add_plus.setAlignment(Qt.AlignCenter)
        self.add_plus.setFixedSize(18, 24)
        self.add_plus.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        al.addWidget(self.add_plus, 0, Qt.AlignTop)
        self.add_prio_bar = _PrioBar(self, self._prio, self._pick_add_prio)
        self.add_prio_bar.setToolTip(f"新任务优先级：{PRIO_TIPS[self._prio]}（点击修改）")
        self.add_prio_bar.setFixedHeight(24)
        al.addWidget(self.add_prio_bar, 0, Qt.AlignTop)
        add_content_widget = QWidget(self.add_row)
        add_content_widget.setAutoFillBackground(False)
        self.inp = _TaskTextEdit(add_content_widget)
        self.inp.setPlaceholderText("添加任务")
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(14)
        self.inp.setFont(font)
        self.inp.setStyleSheet(f"background:transparent; border:none; color:{C.UI_TASK_TEXT}; padding:0px;")
        self.inp.setVerticalPadding(3)
        self.inp.setHeightFloor(24)
        self.inp.installEventFilter(self)            # 焦点态 → 胶囊高亮
        self.inp.returnPressed.connect(self.add_from_input)
        add_content = QVBoxLayout(add_content_widget)
        add_content.setContentsMargins(0, 0, 0, 0)
        add_content.setSpacing(4)
        add_content.addWidget(self.inp)
        self.add_meta = QLabel(add_content_widget)
        self.add_meta.setWordWrap(True)
        self.add_meta.setStyleSheet(f"color:{C.UI_TASK_MUTED}; font-size:11px; background:transparent;")
        self.add_meta.hide()
        add_content.addWidget(self.add_meta)
        al.addWidget(add_content_widget, 1, Qt.AlignTop)
        self.add_more = QToolButton(self.add_row)
        self.add_more.setObjectName("rowMore")
        self.add_more.setFixedSize(22, 24)
        self.add_more.setCursor(Qt.PointingHandCursor)
        self.add_more.setIcon(icons.lucide_icon("ellipsis", C.UI_TASK_CONTROL, 16))
        self.add_more.setIconSize(QSize(16, 16))
        self.add_more.setToolTip("新任务截止日期与提醒")
        self.add_more.clicked.connect(lambda: self.open_task_menu(self._draft_schedule, self.add_more))
        al.addWidget(self.add_more, 0, Qt.AlignTop)
        self.add_row.bind(self.inp, self.add_meta, add_content_widget,
                          (self.add_plus, self.add_prio_bar, self.add_more))
        bl.addWidget(self.add_row)

        # 列表区（待办＋已完成，复用未变化的任务行）
        self.scroll = _SmoothTaskScrollArea(self.body)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        # R95：viewport 默认 autoFillBackground=True 会填 base 实色，盖住卡片半透（中间一块死色）
        self.scroll.viewport().setAutoFillBackground(False)
        self.list_host = QWidget()
        self.list_host.setObjectName("taskList")
        self.list_host.setAutoFillBackground(False)
        self.list_lay = QVBoxLayout(self.list_host)
        self.list_lay.setContentsMargins(0, 2, 0, 0)
        self.list_lay.setSpacing(1)
        self.list_lay.addStretch(1)                # 行一律插到 stretch 前
        self.scroll.setWidget(self.list_host)
        bl.addWidget(self.scroll, 1)

        # R127：八向边缘缩放（左右/上下/四角全支持）取代 QSizeGrip（半透窗近乎隐形）
        self._edge_resizer = C.EdgeResizer(self, 250, 300, edge_inset=12)
        self._edge_resizer.EDGE = 10               # 可见卡片边框内外各 10px，角落也易命中

        self._save_t = QTimer(self)
        self._save_t.setSingleShot(True)
        self._save_t.timeout.connect(self.manager.save)
        self._edge_resizer.resizeFinished.connect(self._save_window_geometry)

        self.setStyleSheet(_CSS)
        self.rebuild()

    def ensure_on_screen(self):
        screens = QApplication.screens()
        primary = QApplication.primaryScreen()
        target = task_window_rect(self.geometry(), [s.availableGeometry() for s in screens],
                                  primary.availableGeometry() if primary else None)
        if target == self.geometry():
            return False
        self.resize(target.size())
        self.move(target.topLeft())
        return True

    def _head_btn(self, hl, text, tip, cb, checkable=False):
        b = QToolButton(self.head)
        b.setObjectName("headBtn")                     # R127：头部钮再加大（30→34、字 17px）
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFixedSize(34, 34)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(cb)
        hl.addWidget(b)
        return b

    # ---------- 列表渲染 ----------
    def rebuild(self):
        if self._editing is not None:
            self._rebuild_pending = True
            return                                 # 编辑会话进行中：重建会吃掉编辑器
        self._rebuild_pending = False
        lay = self.list_lay
        old_widgets = [lay.itemAt(i).widget() for i in range(lay.count() - 1)]
        old_rows = {}
        for w in old_widgets:
            if not isinstance(w, TaskRow):
                continue
            if w._completing:
                # 过渡遇到其他操作：先提交完成状态，保持原先不吞勾选的行为。
                w._completing = False
                w._committed = True
                if hasattr(w, "_c_t"):
                    w._c_t.stop()
                if hasattr(w, "_anim"):
                    w._anim.stop()
                t = self.manager.tasks.get(w.task["id"])
                if t:
                    t["done"] = w._trans_to_done
                    t["done_at"] = time.time() if w._trans_to_done else 0.0
                    t["updated"] = time.time()
            old_rows[(w.task["id"], w.done)] = w
        todo, done = self.manager.lists()
        desired = []

        def task_row(task, completed):
            row = old_rows.get((task["id"], completed))
            if row is None or row._render_key != TaskRow.render_key(task, completed):
                return TaskRow(self, task, completed)
            row.task = task
            return row

        if not todo and not done:
            empty = next((w for w in old_widgets if w.objectName() == "taskEmpty"), None)
            if empty is None:
                empty = QWidget(self.list_host)
                empty.setObjectName("taskEmpty")
                empty.setMinimumHeight(220)
                el = QVBoxLayout(empty)
                el.setContentsMargins(0, 26, 0, 26)
                el.setSpacing(8)
                eb = QLabel("✓", empty)
                eb.setFixedSize(52, 52)
                eb.setAlignment(Qt.AlignCenter)
                eb.setStyleSheet("background:rgba(59,130,246,34); color:#3b82f6;"
                                 " font-size:24px; font-weight:700; border-radius:26px;")
                el.addWidget(eb, 0, Qt.AlignHCenter)
                et = QLabel("暂无任务", empty)
                et.setAlignment(Qt.AlignCenter)
                et.setStyleSheet(f"color:{C.UI_TASK_MUTED}; font-size:13px; background:transparent;")
                el.addWidget(et)
                es = QLabel("在上方输入，回车随手记", empty)
                es.setAlignment(Qt.AlignCenter)
                es.setStyleSheet(f"color:{C.UI_TASK_MUTED}; font-size:11px; background:transparent;")
                el.addWidget(es)
            desired.append(empty)
        for t in todo:
            desired.append(task_row(t, False))
        if done:
            desired.append(_DoneHdr(self, len(done)))
            if not self._done_collapsed:           # R89：默认折叠沉底，点开才展开
                for t in done:
                    desired.append(task_row(t, True))
        # 一次添加只创建新行；未变化的行、鼠标位置和滚动状态继续保留。
        retained = set(desired)
        updates_enabled = self.list_host.updatesEnabled()
        if updates_enabled:
            self.list_host.setUpdatesEnabled(False)
        try:
            for w in old_widgets:
                if w not in retained:
                    lay.removeWidget(w)
                    w.hide()                       # deleteLater 前也不能留在旧位置盖字
                    w.deleteLater()
            for i, w in enumerate(desired):
                if lay.indexOf(w) != i:
                    lay.insertWidget(i, w)
                if w.isHidden():
                    w.show()
        finally:
            if updates_enabled:
                self.list_host.setUpdatesEnabled(True)
        self.count_lbl.setText(f"{len(todo)} 待办" if (todo or done) else "")

    def rows(self):
        """当前渲染出的 TaskRow（冒烟与内部遍历用）。"""
        return [self.list_lay.itemAt(i).widget() for i in range(self.list_lay.count())
                if isinstance(self.list_lay.itemAt(i).widget(), TaskRow)]

    def done_hdr(self):
        for i in range(self.list_lay.count()):
            w = self.list_lay.itemAt(i).widget()
            if isinstance(w, _DoneHdr):
                return w
        return None

    # ---------- 任务动作（直通 manager） ----------
    def add_from_input(self):
        text = self.inp.toPlainText().strip()
        if not text:
            return
        self.manager.add(text, self._prio, self._draft_schedule)
        self._draft_schedule = {}
        self.add_row.setSchedule("", clear_input=True)
        self.inp.setFocus()

    def _pick_add_prio(self):
        """添加行优先级竖条点击后选择档位。"""
        m = self._prio_menu(self._prio, self._set_add_prio)
        m.exec(self.add_prio_bar.mapToGlobal(self.add_prio_bar.rect().bottomLeft()))

    def _set_add_prio(self, p):
        self._prio = p
        self.add_prio_bar.prio = p
        self.add_prio_bar.setToolTip(f"新任务优先级：{PRIO_TIPS[p]}（点击修改）")
        self.add_prio_bar.update()

    def _set_row_prio(self, row, p):
        self.manager.set_prio(row.task["id"], p)   # _after_change 落盘并 rebuild

    def open_task_menu(self, task, anchor):
        def changed():
            if task.get("id"):
                task["updated"] = time.time()
                self.manager._after_change()
            else:
                self.add_row.setSchedule(TS.metadata(task))

        def set_due(day):
            TS.set_due(task, day)
            changed()

        def set_reminder(stamp, offset):
            TS.set_reminder(task, stamp, offset)
            changed()

        remove = (lambda: QTimer.singleShot(0, lambda: self.confirm_delete_task(task["id"], anchor))) \
                 if task.get("id") else None
        menu = TS.schedule_menu(self, task, set_due, set_reminder, remove)
        self._task_menu = menu
        menu.adjustSize()
        point = anchor.mapToGlobal(QPoint(anchor.width() - menu.sizeHint().width(), anchor.height() + 4))
        menu._anchor_point = point
        menu.popup(point)

    def _prio_menu(self, current, on_pick):
        """R98：优先级下拉（无/低/中/高 色点＋当前对号着色）。"""
        m = QMenu(self)
        m.setStyleSheet(C.UI_MENU_QSS)
        for p in (0, 1, 2, 3):
            act = m.addAction(_dot_icon(PRIO_COLORS[p]), PRIO_TIPS[p])
            act.setCheckable(True)
            act.setChecked(p == current)
            act.triggered.connect(lambda _c=False, pp=p: on_pick(pp))
        return m

    def toggle_task(self, tid, on):
        self.manager.set_done(tid, on)

    def _confirm_destructive(self, text, action_text, anchor, on_confirm):
        """将二次确认贴在触发按钮旁；点空白或按 Esc 自动取消。"""
        if getattr(self, "_delete_menu", None) is not None:
            self._delete_menu.close()
        menu = QMenu(self)
        menu.setObjectName("deleteConfirmPopup")
        menu.setStyleSheet(C.UI_MENU_QSS + f"""
            QMenu#deleteConfirmPopup {{ padding:0; }}
            QWidget#deletePopupContent {{ background:transparent; }}
            QLabel#deletePopupText {{ color:{C.UI_TEXT}; font-size:12px; }}
            QToolButton#deletePopupCancel, QToolButton#deletePopupConfirm {{
                min-width:54px; height:26px; padding:0 8px; border-radius:5px;
                border:1px solid {C.UI_BORDER}; color:{C.UI_TEXT2};
                background:rgba(255,255,255,8); font-size:12px;
            }}
            QToolButton#deletePopupCancel:hover {{ background:rgba(255,255,255,18); color:{C.UI_TEXT}; }}
            QToolButton#deletePopupConfirm {{ border:none; background:{C.UI_DANGER}; color:{C.UI_TEXT}; }}
            QToolButton#deletePopupConfirm:hover {{ background:{C.UI_DANGER}; color:{C.UI_TEXT}; }}
        """)
        content = QWidget(menu)
        content.setObjectName("deletePopupContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(9)
        message = QLabel(text, content)
        message.setObjectName("deletePopupText")
        layout.addWidget(message)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addStretch()
        cancel = QToolButton(content)
        cancel.setObjectName("deletePopupCancel")
        cancel.setText("取消")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(menu.close)
        buttons.addWidget(cancel)
        confirm = QToolButton(content)
        confirm.setObjectName("deletePopupConfirm")
        confirm.setText(action_text)
        confirm.setCursor(Qt.PointingHandCursor)
        confirm.clicked.connect(lambda: (menu.close(), on_confirm()))
        buttons.addWidget(confirm)
        layout.addLayout(buttons)
        action = QWidgetAction(menu)
        action.setDefaultWidget(content)
        menu.addAction(action)
        menu.aboutToHide.connect(menu.deleteLater)
        self._delete_menu = menu
        menu.destroyed.connect(
            lambda: setattr(self, "_delete_menu", None)
            if getattr(self, "_delete_menu", None) is menu else None)
        menu.adjustSize()
        # 右边缘和删除钮对齐，浮层向左展开；QMenu 会在屏幕边缘自动翻转。
        point = anchor.mapToGlobal(QPoint(anchor.width() - menu.sizeHint().width(),
                                          anchor.height() + 4))
        menu.popup(point)

    def confirm_delete_task(self, tid, anchor=None):
        task = self.manager.tasks.get(tid)
        if task and not task.get("deleted"):
            if anchor is None:
                anchor = next((row.btn_del for row in self.rows()
                               if row.task["id"] == tid), None)
            if anchor is not None:
                self._confirm_destructive("删除这条任务？", "删除", anchor,
                                          lambda: self.delete_task(tid))

    def confirm_clear_done(self, anchor=None):
        _, done = self.manager.lists()
        count = len(done)
        if count:
            if anchor is None:
                header = self.done_hdr()
                anchor = header.findChild(QToolButton) if header is not None else None
            if anchor is not None:
                self._confirm_destructive(f"删除全部 {count} 条已完成任务？", "全部删除",
                                          anchor, self.manager.clear_done)

    def delete_task(self, tid):
        self.manager.delete(tid)

    # ---------- 完成区折叠 ----------
    def toggle_done_collapsed(self):
        self._done_collapsed = not self._done_collapsed
        self.manager.win["done_collapsed"] = self._done_collapsed   # 折叠态入 win 持久化
        self.schedule_save()
        self.rebuild()

    # ---------- 拖拽排序 ----------
    def drag_begin(self, row):
        self._drag_row = row
        row.setCursor(Qt.SizeAllCursor)            # R98：拖拽全程手型跟着走
        eff = QGraphicsOpacityEffect(row)
        eff.setOpacity(0.55)                       # 被拖行半透：一眼认出「抓着它」
        row.setGraphicsEffect(eff)

    def drag_move(self, row, gy):
        """实时换位：按鼠标 y 找到应插入的位置，layout 自动推开其他行。
        insertWidget 的 index 是取出本行之后的位次——目标在本行下方时要减 1。"""
        if self._drag_row is not row:
            return
        hy = self.list_host.mapFromGlobal(QPoint(0, gy)).y()
        lay = self.list_lay
        target_i = -1                              # 第一个中心在鼠标下方的待办行
        last_todo_i = -1
        for i in range(lay.count()):
            w = lay.itemAt(i).widget()
            if isinstance(w, TaskRow) and not w.done and w is not row:
                if target_i < 0 and hy < w.y() + w.height() / 2:
                    target_i = i
                last_todo_i = i
        cur = lay.indexOf(row)
        if target_i >= 0:
            ti = target_i - 1 if target_i > cur else target_i
            if ti != cur:
                lay.insertWidget(ti, row)
        elif last_todo_i >= 0:
            li = last_todo_i - 1 if last_todo_i > cur else last_todo_i
            if li + 1 != cur:
                lay.insertWidget(li + 1, row)

    def drag_release(self, row):
        """落点定序：按 layout 现顺序整体重赋 order（整数重排，无浮点漂移）。"""
        if self._drag_row is not row:
            return
        self._drag_row = None
        row.unsetCursor()
        row.setGraphicsEffect(None)
        n = 0
        for i in range(self.list_lay.count()):
            w = self.list_lay.itemAt(i).widget()
            if isinstance(w, TaskRow) and not w.done:
                w.task["order"] = float(n)
                w.task["updated"] = time.time()
                n += 1
        self.schedule_save()

    # ---------- 双击就地编辑 ----------
    def begin_edit(self, row):
        if self._editing is not None:
            if self._editing[0] is row:
                self._editing[1].setFocus(Qt.MouseFocusReason)
                return
            self.finish_edit(commit=True)          # 先落上一个
        ed = _TaskTextEdit(row.content)
        ed.setFont(row.label.font())               # R153 所见即所得：family/划线等与显示态同源
        # R153 所见即所得（用户点名「保持现在的显示样子，双击后直接在后面出现光标直接输入」）：
        # 透明底/无边框/白字与行标签完全同貌，零「进入文本框」跳变——
        # 同步根治点②：R133 浅色遗留 color:#1d1d2b 深字在黑透壳上看不清（R137 换肤漏网）；
        # QPlainTextEdit 样式不声明字号，保留上方 setFont 与任务行一致。
        ed.setStyleSheet(f"background:transparent; border:none; color:{C.UI_TASK_TEXT}; padding:0px;")
        ed.resize(max(1, row.label.width()), max(22, row.label.height()))
        ed.setPlainText(row.task.get("text", ""))
        ed.setHeightFloor(max(22, row.label.height()))     # 多行任务进编辑行高不跳
        self._editing = (row, ed)
        ed.installEventFilter(self)                # Esc＝取消
        ed.returnPressed.connect(lambda: self.finish_edit(commit=True, editor=ed))
        ed.editingFinished.connect(lambda: self._queue_edit_finish(ed))
        row.content_lay.replaceWidget(row.label, ed)
        row.label.hide()
        row.content_lay.activate()
        ed.show()
        ed._sync_height()
        ed.setFocus(Qt.MouseFocusReason)
        ed.moveCursor(QTextCursor.End)              # 光标直接落在文字后面（不全选不跳头）

    def _queue_edit_finish(self, editor):
        # 等当前鼠标事件完成再处理焦点；旧编辑框的延迟信号不能关闭新会话。
        def finish_if_blurred():
            if (self._editing is not None and self._editing[1] is editor
                    and not editor.hasFocus()):
                self.finish_edit(commit=True, editor=editor)
        QTimer.singleShot(0, finish_if_blurred)

    def _flush_edit_rebuild(self):
        if self._rebuild_pending and self._editing is None:
            self.rebuild()

    def finish_edit(self, commit=True, editor=None):
        if self._editing is None or (editor is not None and self._editing[1] is not editor):
            return
        row, ed = self._editing
        self._editing = None
        txt = ed.toPlainText().strip() if commit else ""
        row.content_lay.replaceWidget(ed, row.label)
        ed.hide()
        ed.deleteLater()
        changed = bool(txt and txt != row.task.get("text", ""))
        if changed:
            row.label.setText(txt)
        row.label.show()
        if changed:
            self.manager.edit_text(row.task["id"], txt, refresh=False)
        # 文字提交只更新当前行，保留另一个双击事件正在使用的任务行。
        if self._rebuild_pending:
            QTimer.singleShot(0, self._flush_edit_rebuild)

    # ---------- 窗口状态 ----------
    def win_state(self):
        g = self.geometry()
        return {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height(),
                "ontop": self._pinned}                   # R150：钉态读 _pinned（flags 全程无 StaysOnTop）

    def _sync_pin_icon(self, on):
        """R88/R128：钉选中=蓝图标，未选=灰图标；checked 与置顶标志同源；R137 钉 14px 与叉 20px 视觉拉齐。"""
        self.btn_pin.setChecked(on)
        self.btn_pin.setIcon(icons.lucide_icon("pin", C.UI_TASK_TEXT if on else C.UI_TASK_CONTROL, 14))

    def toggle_pin(self):
        on = not self._pinned
        self._pinned = on
        C.set_topmost(self, on)                    # R150：Win32 换 Z 序句柄不重建零闪烁（原 setWindowFlag+show 必闪，用户点名）
        self._sync_pin_icon(on)
        self.schedule_save()

    def schedule_save(self):
        self._save_t.start(500)                    # 防抖：连续操作只落一次盘

    def _save_window_geometry(self):
        self._save_t.stop()
        self.manager.save()                        # 松开缩放边框后立即持久化宽高

    # ---------- 事件 ----------
    def eventFilter(self, obj, ev):
        if obj is self.head:
            t = ev.type()
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                self._drag_off = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if t == QEvent.MouseMove and self._drag_off is not None:
                self.move(ev.globalPosition().toPoint() - self._drag_off)
                return True
            if t == QEvent.MouseButtonRelease and self._drag_off is not None:
                self._drag_off = None
                self.schedule_save()
                return True
        if obj is getattr(self, "inp", None):    # 构造期 inp 未建：事件先到，防御前向引用
            if ev.type() in (QEvent.FocusIn, QEvent.FocusOut):
                self.add_row.setFocused(ev.type() == QEvent.FocusIn)
            return False                                     # 焦点事件照常送达输入框
        if self._editing is not None and obj is self._editing[1]:
            if ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Escape:
                if obj._preedit_text:
                    return False                  # 先交给输入法取消组词，再处理编辑会话
                self.finish_edit(commit=False)     # Esc＝放弃本次编辑
                return True
        return super().eventFilter(obj, ev)

    def moveEvent(self, ev):
        super().moveEvent(ev)
        if hasattr(self, "_save_t"):            # 构造期 resize/move 会提前派发事件：
            self.schedule_save()                # grip/_save_t 尚未创建，异常挂起会炸下一个 Qt 调用

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "_save_t"):
            self.schedule_save()

    def showEvent(self, ev):
        super().showEvent(ev)
        C.follow_system_input_locale()             # 抢焦点后防落默认英文（同 R83）
        C.wire_screen_adaptive(self, 250, 300)     # R126/R127 跨屏自适应（common 共用助手）

    def paintEvent(self, ev):
        """R127：缩放斜纹画进 body 卡片内右下角（原画在 12px 透明边距区，视觉上「跑出去了」）。"""
        super().paintEvent(ev)
        g = self.body.geometry()                   # 卡片在部件内的几何（含 12px 阴影边距）
        x1, y1 = g.right() - 4, g.bottom() - 4
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        _paint_task_shadow(p, QRectF(g), QRectF(self.rect()))
        p.setPen(QPen(QColor(255, 255, 255, 48), 1.6, Qt.SolidLine, Qt.RoundCap))   # R137 黑透回归
        for i in (10, 6, 2):                         # 三条短斜线收在卡片右下内角
            p.drawLine(x1 - i, y1, x1, y1 - i)
        p.end()


class TaskManager:
    """任务清单：单窗口＋任务集；读写收敛 common.load_tasks/save_tasks——上云只动存储层。"""

    def __init__(self, ctl=None):
        self.ctl = ctl                             # 预留（toast 等）
        self.widget = None
        self.win = {"x": 120, "y": 120, "w": 320, "h": 440,
                    "ontop": False, "visible": False}
        self.tasks = {}                            # id -> dict（含软删，合并同步需要）
        self._loaded = False
        self._reminder_timer = QTimer()
        self._reminder_timer.setSingleShot(True)
        self._reminder_timer.timeout.connect(self.check_reminders)

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        data = C.load_tasks()
        if isinstance(data.get("win"), dict):
            self.win.update(data["win"])
        for t in data.get("tasks") or []:
            if isinstance(t, dict) and "id" in t:
                self.tasks[t["id"]] = t
        # R89：老数据补 order（按原 created 倒序赋 0,1,2…，视觉顺序与升级前一致）
        _todo = [t for t in self.tasks.values() if not t.get("deleted") and not t.get("done")]
        if any("order" not in t for t in _todo):
            for i, t in enumerate(sorted(_todo, key=lambda x: -x.get("created", 0))):
                t.setdefault("order", float(i))
        self._arm_reminders()

    def load_and_show(self):                       # 启动恢复：上次可见才自动弹出
        self._load()
        if self.win.get("visible"):
            self.show_widget()

    def show_widget(self):
        self._load()
        if self.widget is None:
            self.widget = TaskWidget(self)
        else:
            self.widget.rebuild()
        if self.widget.isMinimized():
            self.widget.showNormal()
        self.widget.ensure_on_screen()
        self.widget.show()
        self.widget.ensure_on_screen()
        self.widget.raise_()
        self.widget.activateWindow()
        self.win["visible"] = True
        self.save()

    def hide_widget(self):
        self.win["visible"] = False
        old = self.widget
        if old is not None:
            for popup in old.findChildren(QMenu):
                popup.close()
            old.hide()
        self.save()
        if (old is not None and old._editing is None and not old.inp.toPlainText().strip()
                and not TS.metadata(old._draft_schedule)):
            old._save_t.stop()
            self.widget = None
            old.deleteLater()

    def toggle(self):                              # 热键/托盘：显隐切换
        self._load()
        if self.widget is not None and self.widget.isVisible() and not self.widget.isMinimized():
            if self.widget.ensure_on_screen():
                self.show_widget()
            else:
                self.hide_widget()
        else:
            self.show_widget()

    def show_or_raise(self):
        """R96：面板/托盘菜单点击语义=「确保它在眼前」（幂等：可见绝不隐藏，只唤到最前）。
        启动自恢复/被遮挡时用户没意识到窗已开着，点击若走 toggle 会把窗误关——
        用户报「第一次点击打不开」即此错位。关闭入口：标题栏 × 或 Alt+N。"""
        self._load()
        if self.widget is not None and self.widget.isVisible() and not self.widget.isMinimized():
            if self.widget.ensure_on_screen():
                self.save()
            self.widget.raise_()
            self.widget.activateWindow()
        else:
            self.show_widget()

    # ---------- 数据 ----------
    def lists(self):
        """(待办, 已完成)：待办按 order 升序（用户拖出来的序，新任务 order 最小在上），
        完成按完成时间倒序。"""
        todo = sorted((t for t in self.tasks.values()
                       if not t.get("deleted") and not t.get("done")),
                      key=lambda t: (t.get("order", 0.0), -t.get("created", 0)))
        done = sorted((t for t in self.tasks.values()
                       if not t.get("deleted") and t.get("done")),
                      key=lambda t: -t.get("done_at", 0))
        return todo, done

    def add(self, text, prio=0, schedule=None):
        text = (text or "").strip()
        if not text:
            return None
        orders = [t.get("order", 0.0) for t in self.tasks.values()
                  if not t.get("deleted") and not t.get("done")]
        t = {"id": uuid.uuid4().hex, "text": text, "prio": prio, "done": False,
             "order": (min(orders) - 1.0) if orders else 0.0,   # 新任务置顶
             "created": time.time(), "done_at": 0.0,
             "updated": time.time(), "deleted": False}
        if schedule:
            for key in ("due_date", "remind_at", "remind_offset", "reminded_at"):
                if key in schedule:
                    t[key] = schedule[key]
        self.tasks[t["id"]] = t
        self._after_change()
        return t

    def set_done(self, tid, on):
        t = self.tasks.get(tid)
        if not t:
            return
        t["done"] = bool(on)
        t["done_at"] = time.time() if on else 0.0
        t["updated"] = time.time()
        self._after_change()

    def edit_text(self, tid, text, refresh=True):
        t = self.tasks.get(tid)
        text = (text or "").strip()
        if not t or not text:
            return
        t["text"] = text
        t["updated"] = time.time()
        self._after_change(refresh=refresh)

    def set_prio(self, tid, prio):
        """R98：行级下拉改优先级；_after_change 落盘并 rebuild。"""
        t = self.tasks.get(tid)
        if not t:
            return
        t["prio"] = prio
        t["updated"] = time.time()
        self._after_change()

    def delete(self, tid):
        t = self.tasks.get(tid)
        if not t:
            return
        t["deleted"] = True                         # 软删除：云端同步可合并
        t["updated"] = time.time()
        self._after_change()

    def clear_done(self):
        now = time.time()
        n = 0
        for t in self.tasks.values():
            if t.get("done") and not t.get("deleted"):
                t["deleted"] = True
                t["updated"] = now
                n += 1
        if n:
            self._after_change()
        return n

    def _after_change(self, refresh=True):
        self._arm_reminders()
        if self.widget is not None:
            if refresh:
                self.widget.rebuild()
            self.widget.schedule_save()
        else:
            self.save()

    def _arm_reminders(self):
        times = [TS.reminder_time(t) for t in self.tasks.values()
                 if not t.get("done") and not t.get("deleted") and TS.reminder_time(t)
                 and t.get("reminded_at") != TS.reminder_time(t)]
        if times:
            # 最多每分钟检查一次，兼容休眠恢复与系统时间变更；无提醒时完全停止。
            delay = max(100, min(60000, int((min(times) - time.time()) * 1000)))
            self._reminder_timer.start(delay)
        else:
            self._reminder_timer.stop()

    def check_reminders(self):
        now = time.time()
        ready = []
        for task in self.tasks.values():
            stamp = TS.reminder_time(task)
            if (not task.get("done") and not task.get("deleted") and 0 < stamp <= now
                    and task.get("reminded_at") != stamp):
                task["reminded_at"] = stamp
                task["updated"] = now
                ready.append(task)
        if ready:
            self.save()  # 通知前保存已提醒标记，重启后不会重复弹出
            if self.widget is not None:
                self.widget.rebuild()
            if self.ctl is not None:
                message = "\n".join(t.get("text", "") for t in ready[:3])
                if len(ready) > 3:
                    message += f"\n还有 {len(ready) - 3} 条任务"
                self.ctl.tray.showMessage(f"{C.APP_NAME} · 任务提醒", message)
                self.ctl.toast("任务提醒：" + ready[0].get("text", "")[:100])
        self._arm_reminders()

    def save(self):                                # 防抖落盘点＋显隐切换直存
        if self.widget is not None:
            self.win.update(self.widget.win_state())
        C.save_tasks({"win": self.win, "tasks": list(self.tasks.values())})
