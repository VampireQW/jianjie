# -*- coding: utf-8 -*-
"""任务日期/提醒的时间计算与按钮旁设置浮层。日期按本机时区保存。"""
import math
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QDate, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractSpinBox, QFrame, QHBoxLayout, QLabel,
                               QMenu, QPushButton, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

import common as C
import icons


def due_date(task):
    try:
        return date.fromisoformat(task.get("due_date", ""))
    except (ValueError, TypeError):
        return None


def reminder_time(task):
    try:
        value = float(task.get("remind_at", 0))
        return value if math.isfinite(value) and value > 0 else 0.0
    except (ValueError, TypeError):
        return 0.0


def relative_reminder(day, offset):
    return datetime.combine(day - timedelta(days=offset), time(9)).timestamp()


def set_due(task, day):
    task["due_date"] = day.isoformat() if day else ""
    offset = task.get("remind_offset")
    if offset not in (0, 1, 2, 3, 7):
        offset = None
    if offset is not None:
        task["remind_at"] = relative_reminder(day, offset) if day else 0.0
        task["reminded_at"] = 0.0
        if day is None:
            task["remind_offset"] = None


def set_reminder(task, stamp=0.0, offset=None):
    task.update(remind_at=stamp, remind_offset=offset, reminded_at=0.0)


def metadata(task):
    parts = []
    day = due_date(task)
    if day:
        year = f"{day.year}年" if day.year != date.today().year else ""
        parts.append(f"{year}{day.month}月{day.day}日，周{'一二三四五六日'[day.weekday()]}")
    stamp = reminder_time(task)
    if stamp:
        try:
            dt = datetime.fromtimestamp(stamp)
            tag = "已提醒" if task.get("reminded_at") == stamp else "提醒"
            parts.append(f"{tag} {dt.month}/{dt.day} {dt:%H:%M}")
        except (ValueError, OverflowError, OSError):
            pass
    return " · ".join(parts)


# 日期卡片采用用户指定参考的中性灰与浅蓝；不随任务面板透明度透底。
DATE_SURFACE = "#232323"
DATE_LINE = "#3d3d3d"
DATE_CONTROL = "#7c7c7c"
DATE_CONTROL_ACTIVE = "#a0a0a0"
DATE_TEXT = "#f5f5f5"
DATE_DAY_TEXT = "#d2d2d2"
DATE_ADJACENT_TEXT = "#989898"
DATE_BUTTON = "#313131"
DATE_HOVER = "#3b3b3b"
DATE_PRESSED = "#484848"
DATE_ACCENT = "#45bff7"
DATE_ACCENT_HOVER = "#62ccfa"
DATE_ACCENT_PRESSED = "#28afe7"
DATE_SELECTED = "#78b9e9"
DATE_SELECTED_BORDER = "#0d344b"
DATE_ON_ACCENT = "#07141c"
DATE_ERROR = "#ffaaaa"

POPUP_QSS = f"""
QFrame#taskDatePopup {{ background:transparent; border:none; }}
QLabel {{ background:transparent; color:{DATE_TEXT}; font-size:13px; }}
QLabel#taskDateMonth {{ font-size:21px; font-weight:400; }}
QLabel#taskDateError {{ color:{DATE_ERROR}; font-size:12px; }}
QFrame#taskDateTimeBar {{ background:{DATE_BUTTON}; border:1px solid {DATE_CONTROL};
 border-radius:6px; }}
QFrame#taskDateTimeDivider {{ background:{DATE_LINE}; border:none; }}
QSpinBox {{ background:transparent; color:{DATE_TEXT}; border:2px solid transparent;
 border-radius:5px; padding:0 18px; font-size:15px; selection-background-color:{DATE_SELECTED};
 selection-color:{DATE_ON_ACCENT}; }}
QSpinBox:focus {{ border-color:{DATE_ACCENT}; }}
QSpinBox:disabled {{ color:{DATE_ADJACENT_TEXT}; }}
QPushButton {{ background:{DATE_BUTTON}; color:{DATE_TEXT}; border:1px solid {DATE_CONTROL};
 border-radius:6px; padding:0 12px; font-size:14px; }}
QPushButton:hover {{ background:{DATE_HOVER}; border-color:{DATE_CONTROL_ACTIVE}; }}
QPushButton:pressed {{ background:{DATE_PRESSED}; border-color:{DATE_CONTROL_ACTIVE}; }}
QPushButton:focus {{ border:2px solid {DATE_ACCENT}; }}
QPushButton#taskDateSave {{ background:{DATE_ACCENT}; color:{DATE_ON_ACCENT};
 border-color:{DATE_ACCENT}; }}
QPushButton#taskDateSave:hover {{ background:{DATE_ACCENT_HOVER}; border-color:{DATE_ACCENT_HOVER}; }}
QPushButton#taskDateSave:pressed {{ background:{DATE_ACCENT_PRESSED}; border-color:{DATE_ACCENT_PRESSED}; }}
QPushButton#taskDateSave:focus {{ border:2px solid {DATE_TEXT}; }}
QPushButton:disabled {{ background:{DATE_BUTTON}; color:{DATE_ADJACENT_TEXT}; }}
"""


class _TriangleButton(QToolButton):
    def __init__(self, parent, upwards, compact=False):
        super().__init__(parent)
        self.upwards = upwards
        self.compact = compact
        self.setFixedSize(16 if compact else 32, 13 if compact else 32)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus if compact else Qt.StrongFocus)
        self.setAutoRepeat(compact)
        self.setStyleSheet("QToolButton { background:transparent; border:none; padding:0; }")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.underMouse() or self.hasFocus() or self.isDown():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(DATE_PRESSED if self.isDown() else DATE_HOVER))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        if self.hasFocus():
            painter.setPen(QPen(QColor(DATE_ACCENT), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        x, y = self.width() / 2, self.height() / 2
        half, height = (3, 2.5) if self.compact else (4, 3)
        direction = -1 if self.upwards else 1
        points = QPolygonF([QPointF(x - half, y - direction * height / 2),
                            QPointF(x + half, y - direction * height / 2),
                            QPointF(x, y + direction * height / 2)])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(DATE_DAY_TEXT if self.compact else DATE_TEXT))
        painter.drawPolygon(points)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class _DateGrid(QWidget):
    selectionChanged = Signal(QDate)
    pageChanged = Signal(int, int)
    HEADER_H = 32
    CELL_H = 38

    def __init__(self, parent, selected):
        super().__init__(parent)
        self._selected = QDate(selected)
        self._page = QDate(selected.year(), selected.month(), 1)
        self._hovered = -1
        self.setFixedHeight(self.HEADER_H + 6 * self.CELL_H)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("日期，方向键选择日期，翻页键切换月份")

    def selectedDate(self):
        return QDate(self._selected)

    def setSelectedDate(self, value):
        if not value.isValid():
            return
        changed = value != self._selected
        self._selected = QDate(value)
        self.setCurrentPage(value.year(), value.month())
        self.update()
        if changed:
            self.selectionChanged.emit(QDate(value))

    def setCurrentPage(self, year, month):
        page = QDate(year, month, 1)
        if page.isValid() and page != self._page:
            self._page = page
            self._hovered = -1
            self.pageChanged.emit(year, month)
            self.update()

    def shiftMonth(self, offset):
        page = self._page.addMonths(offset)
        self.setCurrentPage(page.year(), page.month())

    def _first_date(self):
        return self._page.addDays(1 - self._page.dayOfWeek())

    def _index_at(self, point):
        if not self.rect().contains(point) or point.y() < self.HEADER_H:
            return -1
        row = int((point.y() - self.HEADER_H) / self.CELL_H)
        column = min(6, int(point.x() * 7 / max(1, self.width())))
        return row * 7 + column if 0 <= row < 6 else -1

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = self.font()
        font.setPixelSize(14)
        painter.setFont(font)
        width = self.width() / 7
        painter.setPen(QColor(DATE_DAY_TEXT))
        for column, weekday in enumerate("一二三四五六日"):
            painter.drawText(QRectF(column * width, 0, width, self.HEADER_H), Qt.AlignCenter, weekday)
        first = self._first_date()
        for index in range(42):
            day = first.addDays(index)
            row, column = divmod(index, 7)
            cell = QRectF(column * width, self.HEADER_H + row * self.CELL_H, width, self.CELL_H)
            side = min(width, self.CELL_H) - 4
            square = QRectF(cell.center().x() - side / 2, cell.center().y() - side / 2, side, side)
            selected = day == self._selected
            if selected:
                painter.fillRect(square, QColor(DATE_SELECTED))
                painter.setPen(QPen(QColor(DATE_SELECTED_BORDER), 2))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(square.adjusted(2.5, 2.5, -2.5, -2.5))
                if self.hasFocus():
                    painter.setPen(QPen(QColor(DATE_ACCENT), 1))
                    painter.drawRect(square.adjusted(-1, -1, 1, 1))
            elif index == self._hovered:
                painter.fillRect(square, QColor(DATE_HOVER))
            painter.setPen(QColor(DATE_ON_ACCENT if selected else
                                  DATE_DAY_TEXT if day.month() == self._page.month() else DATE_ADJACENT_TEXT))
            painter.drawText(cell, Qt.AlignCenter, str(day.day()))

    def mousePressEvent(self, event):
        index = self._index_at(event.position().toPoint())
        if event.button() == Qt.LeftButton and index >= 0:
            self.setFocus(Qt.MouseFocusReason)
            self.setSelectedDate(self._first_date().addDays(index))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        hovered = self._index_at(event.position().toPoint())
        if hovered != self._hovered:
            self._hovered = hovered
            self.update()
        self.setCursor(Qt.PointingHandCursor if hovered >= 0 else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hovered = -1
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        steps = {Qt.Key_Left: -1, Qt.Key_Right: 1, Qt.Key_Up: -7, Qt.Key_Down: 7}
        if event.key() in steps:
            self.setSelectedDate(self._selected.addDays(steps[event.key()]))
        elif event.key() in (Qt.Key_PageUp, Qt.Key_PageDown):
            self.setSelectedDate(self._selected.addMonths(-1 if event.key() == Qt.Key_PageUp else 1))
        elif event.key() == Qt.Key_Home:
            self.setSelectedDate(QDate.currentDate())
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class _TimeSpinBox(QSpinBox):
    def __init__(self, parent, maximum, value, label):
        super().__init__(parent)
        self.setRange(0, maximum)
        self.setValue(value)
        self.setWrapping(True)
        self.setAlignment(Qt.AlignCenter)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setFixedHeight(34)
        self._up = _TriangleButton(self, True, True)
        self._down = _TriangleButton(self, False, True)
        self._up.setToolTip(f"增加{label}")
        self._down.setToolTip(f"减少{label}")
        self._up.setAccessibleName(f"增加{label}")
        self._down.setAccessibleName(f"减少{label}")
        self._up.clicked.connect(lambda: self._step(1))
        self._down.clicked.connect(lambda: self._step(-1))
        self._sync_arrows()

    def textFromValue(self, value):
        return f"{value:02d}"

    def _step(self, offset):
        self.setFocus(Qt.MouseFocusReason)
        self.stepBy(offset)

    def _sync_arrows(self):
        visible = self.underMouse() or self.hasFocus()
        self._up.setVisible(visible)
        self._down.setVisible(visible)

    def resizeEvent(self, event):
        if hasattr(self, "_up"):
            self._up.move(self.width() - 19, 4)
            self._down.move(self.width() - 19, 17)
        super().resizeEvent(event)

    def enterEvent(self, event):
        self._sync_arrows()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._sync_arrows()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._sync_arrows()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._sync_arrows()
        super().focusOutEvent(event)


class DatePopup(QFrame):
    def __init__(self, parent, point, task, on_due, on_reminder, reminder=False):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("taskDatePopup")
        self.setAccessibleName("设置提醒时间" if reminder else "设置截止日期")
        self.setStyleSheet(POPUP_QSS)
        self.setFont(parent.font())
        self.setFixedWidth(304)
        self._point = QPoint(point)
        self._reminder = reminder
        self._on_due = on_due
        self._on_reminder = on_reminder
        self._accepted = False
        current = datetime.now()
        default = (current + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        stamp = reminder_time(task)
        day = due_date(task) or current.date()
        if reminder:
            try:
                if stamp > current.timestamp():
                    default = datetime.fromtimestamp(stamp)
                elif due_date(task):
                    candidate = datetime.combine(day, time(9))
                    if candidate > current:
                        default = candidate
            except (ValueError, OverflowError, OSError):
                pass
            day = default.date()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.month_title = QLabel(self)
        self.month_title.setObjectName("taskDateMonth")
        self.month_title.setFixedHeight(44)
        header.addWidget(self.month_title, 1)
        self.prev_month = _TriangleButton(self, True)
        self.prev_month.setToolTip("上一月")
        self.prev_month.setAccessibleName("上一月")
        header.addWidget(self.prev_month)
        self.next_month = _TriangleButton(self, False)
        self.next_month.setToolTip("下一月")
        self.next_month.setAccessibleName("下一月")
        header.addWidget(self.next_month)
        layout.addLayout(header)
        self.calendar = _DateGrid(self, QDate(day.year, day.month, day.day))
        self.calendar.pageChanged.connect(self._month_changed)
        self.calendar.selectionChanged.connect(self._clear_error)
        self.prev_month.clicked.connect(lambda: self.calendar.shiftMonth(-1))
        self.next_month.clicked.connect(lambda: self.calendar.shiftMonth(1))
        self._month_changed(day.year, day.month)
        layout.addWidget(self.calendar)
        if reminder:
            timebar = QFrame(self)
            timebar.setObjectName("taskDateTimeBar")
            timerow = QHBoxLayout(timebar)
            timerow.setContentsMargins(1, 1, 1, 1)
            timerow.setSpacing(0)
            self.hour = _TimeSpinBox(timebar, 23, default.hour, "小时")
            self.minute = _TimeSpinBox(timebar, 59, default.minute, "分钟")
            self.hour.valueChanged.connect(self._clear_error)
            self.minute.valueChanged.connect(self._clear_error)
            timerow.addWidget(self.hour, 1)
            divider = QFrame(timebar)
            divider.setObjectName("taskDateTimeDivider")
            divider.setFixedWidth(1)
            timerow.addWidget(divider)
            timerow.addWidget(self.minute, 1)
            layout.addWidget(timebar)
        self.error = QLabel("", self)
        self.error.setObjectName("taskDateError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.setObjectName("taskDateCancel")
        self.cancel_button.setFixedHeight(36)
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.clicked.connect(self.close)
        buttons.addWidget(self.cancel_button, 1)
        self.save_button = QPushButton("保存", self)
        self.save_button.setObjectName("taskDateSave")
        self.save_button.setFixedHeight(36)
        self.save_button.setCursor(Qt.PointingHandCursor)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._apply)
        buttons.addWidget(self.save_button, 1)
        layout.addLayout(buttons)
        self._place()
        self.show()
        self.activateWindow()
        self.calendar.setFocus(Qt.PopupFocusReason)

    def _month_changed(self, year, month):
        self.month_title.setText(f"{year}年{month}月")

    def _clear_error(self, *args):
        if self.error.isVisible():
            self.error.clear()
            self.error.hide()
            self._place()

    def _place(self):
        self.adjustSize()
        screen = self.parentWidget().screen().availableGeometry()
        self.move(max(screen.left() + 4, min(self._point.x(), screen.right() - self.width() - 4)),
                  max(screen.top() + 4, min(self._point.y(), screen.bottom() - self.height() - 4)))

    def _apply(self):
        if self._accepted:
            return
        selected = self.calendar.selectedDate()
        day = date(selected.year(), selected.month(), selected.day())
        if self._reminder:
            self.hour.interpretText()
            self.minute.interpretText()
            value = datetime.combine(day, time(self.hour.value(), self.minute.value())).timestamp()
            if value <= datetime.now().timestamp():
                self.error.setText("请选择未来的提醒时间")
                self.error.show()
                self._place()
                return
            self._accepted = True
            self.close()
            self._on_reminder(float(value), None)
        else:
            self._accepted = True
            self.close()
            self._on_due(day)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(DATE_SURFACE))
        painter.setPen(QPen(QColor(DATE_LINE), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            event.accept()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._apply()
            event.accept()
        else:
            super().keyPressEvent(event)


def schedule_menu(parent, task, on_due, on_reminder, on_delete=None):
    menu = QMenu(parent)
    menu.setObjectName("taskMoreMenu")
    menu.setStyleSheet(C.UI_MENU_QSS + f"""
      QMenu {{ background:{C.UI_BG_CARD}; min-width:190px; }}
      QMenu::item:disabled {{ color:{C.UI_TEXT2}; }}
      QMenu::separator {{ height:1px; background:{C.UI_TASK_LINE}; margin:4px 8px; }}
    """)
    remind = menu.addMenu(icons.lucide_icon("alarm-clock", C.UI_TEXT, 16), "提醒我")
    day = due_date(task)
    if day is None:
        remind.addAction("先设置截止日期").setEnabled(False)
    for offset in (0, 1, 2, 3, 7):
        label = "当天（9:00）" if offset == 0 else f"提前 {offset} 天（9:00）"
        action = remind.addAction(label)
        value = relative_reminder(day, offset) if day else 0
        action.setEnabled(value > datetime.now().timestamp())
        action.setCheckable(True)
        action.setChecked(bool(reminder_time(task)) and task.get("remind_offset") == offset)
        action.triggered.connect(lambda _=False, stamp=value, days=offset: on_reminder(stamp, days))
    remind.addSeparator()
    custom_reminder = remind.addAction("自定义…")
    if reminder_time(task):
        remind.addAction("取消提醒", lambda: on_reminder(0.0, None))
    due = menu.addMenu(icons.lucide_icon("calendar-days", C.UI_TEXT, 16),
                       "修改截止日期" if day else "添加截止日期")
    today = date.today()
    for label, value in (("今天", today), ("明天", today + timedelta(days=1)),
                         ("下周一", today + timedelta(days=7 - today.weekday()))):
        act = due.addAction(f"{label}    周{'一二三四五六日'[value.weekday()]}")
        act.setCheckable(True)
        act.setChecked(day == value)
        act.triggered.connect(lambda _=False, d=value: on_due(d))
    due.addSeparator()
    custom_due = due.addAction("选择日期…")
    if day:
        due.addAction("清除截止日期", lambda: on_due(None))
    # 先让菜单退出，再弹出日历，避免父菜单自动关闭时连带新浮层。
    from PySide6.QtCore import QTimer
    def open_picker(reminder):
        point = QPoint(menu._anchor_point)
        menu.close()
        QTimer.singleShot(0, lambda: DatePopup(parent, point, task, on_due, on_reminder, reminder))
    custom_due.triggered.connect(lambda: open_picker(False))
    custom_reminder.triggered.connect(lambda: open_picker(True))
    if on_delete:
        menu.addSeparator()
        menu.addAction(icons.lucide_icon("trash-2", C.UI_ERROR_TEXT, 16), "删除任务", on_delete)
    menu.aboutToHide.connect(menu.deleteLater)
    return menu
