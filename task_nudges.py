"""Optional today-task nudge, interactive notice and settings controls."""
import copy
import ctypes
import math
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QButtonGroup, QFrame,
                               QHBoxLayout, QLabel, QMenu, QPushButton, QTimeEdit,
                               QVBoxLayout, QWidget)
import common as C
import icons
from polished_controls import SurfaceButton, crisp_window_shadow
from task_nudge_policy import DAILY_LIMIT, PREFIX, NudgePolicy, configuration, today_tasks


def desktop_locked():
    if sys.platform != "win32" or QGuiApplication.platformName() == "offscreen":
        return False
    try:
        user = ctypes.windll.user32
        user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user.OpenInputDesktop.restype = wintypes.HANDLE
        user.CloseDesktop.argtypes = [wintypes.HANDLE]
        handle = user.OpenInputDesktop(0, False, 0x100)
        if handle:
            user.CloseDesktop(handle)
            return False
        return True
    except (AttributeError, OSError):
        return True


class TaskNudgeCard(QWidget):
    action = Signal(str)

    def __init__(self, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.settings = settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("简截 · 今日待办提醒")
        self.setAccessibleName("今日待办提醒")
        self.setFixedWidth(376)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self.body = QFrame(self)
        self.body.setObjectName("taskNudgeBody")
        self.body.setStyleSheet("QFrame#taskNudgeBody{background:#141c29;border-radius:12px;}"
                                "QLabel{background:transparent;color:#e7eef9;}"
                                "QMenu{font-family:'Microsoft YaHei UI';}")
        root.addWidget(self.body)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(16, 12, 12, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(8)
        badge = QLabel(self.body)
        badge.setPixmap(icons.lucide_icon("list-todo", "#83b6ff", 18).pixmap(18, 18))
        badge.setFixedSize(20, 24)
        header.addWidget(badge)
        self.title = QLabel(self.body)
        self.title.setStyleSheet("font-size:13px;font-weight:600;")
        header.addWidget(self.title, 1)
        self.close_button = self.icon_button("x", "关闭本次提醒", "close")
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.labels = []
        for _ in range(2):
            label = QLabel(self.body)
            label.setTextFormat(Qt.PlainText)
            label.setStyleSheet("color:#b9c6d9;font-size:12px;")
            label.setMinimumHeight(20)
            self.labels.append(label)
            layout.addWidget(label)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.view_button = SurfaceButton("查看今日任务", self.body, variant="primary", pixels=11)
        self.snooze_button = SurfaceButton("30 分钟后提醒", self.body, pixels=11)
        for button in (self.view_button, self.snooze_button):
            button.setFixedHeight(32)
            button.setFocusPolicy(Qt.NoFocus)
            footer.addWidget(button, 1)
        self.view_button.clicked.connect(lambda: self.dismiss("view"))
        self.snooze_button.clicked.connect(lambda: self.dismiss("snooze"))
        self.more_button = self.icon_button("ellipsis", "更多提醒操作")
        footer.addWidget(self.more_button)
        layout.addLayout(footer)
        self.caption = QLabel(self.body)
        self.caption.setStyleSheet("font-size:10px;color:#7f90a8;")
        layout.addWidget(self.caption)
        self.menu = QMenu(self)
        self.menu.setStyleSheet(C.UI_MENU_QSS)
        self.menu.addAction("今天不再提醒", lambda: self.dismiss("mute"))
        self.menu.addAction("提醒设置…", lambda: self.dismiss("settings"))
        self.more_button.clicked.connect(self.open_menu)
        self.menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self.resume_timeout))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.dismiss("timeout"))
        self.remaining = 10000
        self.task_ids = []
        crisp_window_shadow(self, self.body, 12)

    def icon_button(self, icon, tip, action=None):
        button = QPushButton(self.body)
        button.setFixedSize(28, 28)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setIcon(icons.lucide_icon(icon, "#94a5be", 16))
        button.setIconSize(QSize(16, 16))
        button.setStyleSheet("QPushButton{border:none;background:transparent;border-radius:6px;}"
                             "QPushButton:hover{background:#28364b;}"
                             "QPushButton:pressed{background:#334761;}")
        if action:
            button.clicked.connect(lambda: self.dismiss(action))
        return button

    def render(self, rows, total, count):
        self.task_ids = [row["id"] for row in rows]
        self.title.setText(f"今日还有 {total} 项未完成")
        for index, label in enumerate(self.labels):
            label.setVisible(index < len(rows))
            if index < len(rows):
                value = str(rows[index].get("text", "")).replace("\n", " ").replace("\r", " ")
                label.setText(label.fontMetrics().elidedText(value, Qt.ElideRight, self.width() - 60))
                label.setToolTip(value)
        self.caption.setText(f"今日轻提醒 {count}/{DAILY_LIMIT} · 位置跟随全局设置")
        self.adjustSize()

    def reposition(self):
        C.position_screen_notice(self, self.settings)

    def present(self, rows, total, count):
        self.render(rows, total, count)
        self.reposition()
        self.show()
        C.exclude_from_capture(self)
        self.raise_()
        self.remaining = 10000
        self.timer.start(self.remaining)

    def pause_timeout(self):
        if self.timer.isActive():
            self.remaining = max(1, self.timer.remainingTime())
            self.timer.stop()

    def resume_timeout(self):
        if self.isVisible() and not self.underMouse() and not self.menu.isVisible():
            self.timer.start(max(1, self.remaining))

    def enterEvent(self, event):
        self.pause_timeout()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.resume_timeout()
        super().leaveEvent(event)

    def open_menu(self):
        self.pause_timeout()
        self.menu.popup(self.more_button.mapToGlobal(QPoint(0, self.more_button.height())))

    def hide_notice(self):
        self.hide()
        self.menu.close()
        self.timer.stop()

    def dismiss(self, action):
        self.hide_notice()
        self.action.emit(action)

    def closeEvent(self, event):
        self.dismiss("close")
        event.accept()


class TodayNudges(QObject):
    def __init__(self, manager, ctl, clock=None, uniform=None):
        super().__init__(ctl if isinstance(ctl, QObject) else None)
        self.manager, self.ctl = manager, ctl
        self.clock = clock or time.time
        self.policy = NudgePolicy(ctl.S, manager.win.get("today_nudge"), uniform)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.reconcile)
        self.card = None
        self.stopped = False
        app = QApplication.instance()
        app.installEventFilter(self)
        app.aboutToQuit.connect(self.stop)
        self.reconcile(startup=True)

    def tasks(self):
        return today_tasks(self.manager.tasks.values(), self.clock())

    def persist(self):
        if self.manager.win.get("today_nudge") == self.policy.state:
            return True
        previous = copy.deepcopy(self.manager.win.get("today_nudge"))
        self.manager.win["today_nudge"] = copy.deepcopy(self.policy.state)
        try:
            self.manager.save()
            return True
        except OSError:
            if previous is None:
                self.manager.win.pop("today_nudge", None)
            else:
                self.manager.win["today_nudge"] = previous
            return False

    def arm(self, deadline):
        self.timer.stop()
        if deadline and not self.stopped:
            self.timer.start(max(1, min(86400000, math.ceil((deadline - self.clock()) * 1000))))

    def changed(self):
        if not self.stopped and self.policy.config["enabled"] and not self.refresh_timer.isActive():
            self.refresh_timer.start(0)

    def reconcile(self, startup=False):
        if self.stopped:
            return
        if not self.policy.config["enabled"]:
            self.timer.stop()
            self.interrupt()
            return
        rows, now = self.tasks(), self.clock()
        deadline = self.policy.refresh(now, bool(rows), startup)
        if not self.persist():
            self.interrupt()
            self.arm(now + 300)
            return
        if self.card and self.card.isVisible():
            if not rows or self.policy.state.get("mute_day") == self.policy.state["day"]:
                self.interrupt()
            else:
                ids = set(self.card.task_ids)
                selected = [row for row in rows if row["id"] in ids][:2]
                selected += [row for row in rows if row not in selected][:2-len(selected)]
                self.card.render(selected, len(rows), self.policy.state["count"])
                self.card.reposition()
        self.arm(deadline)

    def configure(self):
        was_enabled = self.policy.config["enabled"]
        self.policy.config = configuration(self.ctl.S)
        if self.policy.config["enabled"]:
            self.policy.postpone(self.clock(), self.policy.delay() if was_enabled else 600)
        self.reconcile()

    def blocked(self):
        if desktop_locked() or QApplication.activeModalWidget() or QApplication.activePopupWidget():
            return True
        if getattr(self.ctl, "shot_overlays", None) or getattr(self.ctl, "_cp_active", False):
            return True
        for name in ("rec_bar", "gif_bar", "long_bar", "_toast", "_shot_notify"):
            window = getattr(self.ctl, name, None)
            try:
                if window is not None and window.isVisible():
                    return True
            except RuntimeError:
                pass
        widget = self.manager.widget
        return bool(widget and widget.isVisible() and widget.isActiveWindow())

    def tick(self):
        if self.stopped or not self.policy.config["enabled"]:
            return
        now, rows = self.clock(), self.tasks()
        deadline = self.policy.refresh(now, bool(rows))
        if not deadline or deadline > now:
            self.reconcile()
            return
        if now - deadline > 300:
            self.policy.postpone(now, 600)
        elif self.blocked():
            self.policy.postpone(now, 300)
        else:
            before = copy.deepcopy(self.policy.state)
            selected = self.policy.consume(now, rows)
            if selected and self.persist():
                if self.card is None:
                    self.card = TaskNudgeCard(self.ctl.S)
                    self.card.action.connect(self.handle_action)
                self.card.present(selected, len(rows), self.policy.state["count"])
            else:
                self.policy.state = before
                self.policy.postpone(now, 300)
        self.reconcile()

    def handle_action(self, action):
        now = self.clock()
        if action == "snooze":
            self.policy.postpone(now, 1800)
        elif action == "mute":
            self.policy.mute(now)
        elif action == "view":
            self.manager.show_or_raise()
            self.manager.widget.select_tab("today")
            self.reviewed()
        elif action == "settings":
            self.ctl.settings("today_reminders")
        self.reconcile()

    def reviewed(self):
        if self.policy.config["enabled"] and self.tasks():
            self.interrupt()
            self.policy.review(self.clock())
            self.reconcile()

    def exact_reminder(self):
        if self.policy.config["enabled"]:
            self.interrupt()
            self.policy.postpone(self.clock(), 900, at_least=True)
            self.reconcile()

    def interrupt(self):
        if self.card:
            self.card.hide_notice()

    def reposition(self):
        if self.card and self.card.isVisible():
            self.card.reposition()

    def eventFilter(self, window, event):
        if (event.type() in (QEvent.WindowActivate, QEvent.WindowDeactivate)
                and self.manager.widget is not None and window is self.manager.widget
                and self.manager.win.get("task_tab", "today") == "today"):
            self.reviewed()
        return False

    def stop(self):
        self.stopped = True
        self.timer.stop()
        self.refresh_timer.stop()
        self.interrupt()
        QApplication.instance().removeEventFilter(self)


class NudgeSettings(QWidget):
    changed = Signal(dict)

    def __init__(self, parent, settings, switch_type):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(QLabel("温和提醒今天未完成的任务", self), 1)
        self.toggle = switch_type(self)
        self.toggle.setAccessibleName("今日待办轻提醒")
        row.addWidget(self.toggle)
        layout.addLayout(row)
        self.options = QWidget(self)
        options = QVBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("提醒频率", self), 1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.frequencies = {}
        for value, text in ((30, "约半小时"), (60, "约 1 小时"), (120, "约 2 小时")):
            button = SurfaceButton(text, self, variant="tab", pixels=11)
            button.setFixedSize(78, 32)
            button.setCheckable(True)
            self.group.addButton(button, value)
            self.frequencies[value] = button
            row.addWidget(button)
        self.group.idClicked.connect(lambda value: self.changed.emit({PREFIX + "interval": value}))
        options.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("提醒时段", self), 1)
        self.start, self.end = QTimeEdit(self), QTimeEdit(self)
        for label, edit in (("提醒开始时间", self.start), ("提醒结束时间", self.end)):
            edit.setAccessibleName(label)
            edit.setDisplayFormat("HH:mm")
            edit.setButtonSymbols(QAbstractSpinBox.NoButtons)
            edit.setFixedSize(90, 32)
            edit.setStyleSheet("QTimeEdit{background:#141c29;color:#dbe8fa;border:1px solid #35445a;"
                               "border-radius:6px;padding:4px 12px;font-size:12px;selection-background-color:#315f9c;}"
                               "QTimeEdit:focus{border-color:#73adfc;}QTimeEdit:disabled{color:#708099;}")
        row.addWidget(self.start)
        row.addWidget(QLabel("—", self))
        row.addWidget(self.end)
        options.addLayout(row)
        layout.addWidget(self.options)
        self.error = QLabel(self)
        self.error.setStyleSheet("font-size:11px;color:#f3a9a9;")
        self.error.hide()
        layout.addWidget(self.error)
        hint = QLabel("间隔会自动错开，每天最多 6 次。首次等待 10 分钟；截图、录屏时暂缓。位置跟随上方 Toast 设置。", self)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
        layout.addWidget(hint)
        self.set_configuration(settings)
        self.toggle.toggled.connect(lambda enabled: self.changed.emit({PREFIX + "enabled": enabled}))
        self.start.editingFinished.connect(self.time_changed)
        self.end.editingFinished.connect(self.time_changed)

    def time_changed(self):
        valid = self.start.time() < self.end.time()
        self.error.setText("结束时间需要晚于开始时间。")
        self.error.setVisible(not valid)
        if valid:
            self.changed.emit({PREFIX + "start": self.start.time().toString("HH:mm"),
                               PREFIX + "end": self.end.time().toString("HH:mm")})

    def set_configuration(self, settings):
        values = configuration(settings)
        self.toggle.blockSignals(True)
        self.toggle.setChecked(values["enabled"])
        self.toggle.blockSignals(False)
        self.options.setEnabled(values["enabled"])
        self.frequencies[values["interval"]].setChecked(True)
        for edit, key in ((self.start, "start"), (self.end, "end")):
            edit.blockSignals(True)
            edit.setTime(QTime.fromString(values[key], "HH:mm"))
            edit.blockSignals(False)
        self.error.hide()
