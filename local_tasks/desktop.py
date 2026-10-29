"""正式版简截的任务页与可开关的本地 MCP。"""
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QPushButton, QWidget
from .core import today
from .state import TaskStateMixin


def install():
    import common as C
    import storage
    import sticky
    from .bridge import Bridge
    if getattr(sticky.TaskManager, "_local_mcp_installed", False):
        return sticky.TaskManager, sticky.TaskWidget
    C.save_tasks = lambda data: storage.atomic_json(C.TASKS_PATH, data)

    class LocalTaskManager(TaskStateMixin, sticky.TaskManager):
        _task_common = C
        _local_mcp_installed = True

        def __init__(self, ctl=None):
            super().__init__(ctl)
            self._load()
            self._bridge = None
            self._day = today()
            self._date_timer = QTimer()
            self._date_timer.setInterval(30000)
            self._date_timer.timeout.connect(self._check_date)
            self._date_timer.start()
            if ctl is not None:
                from .migration import merge_legacy
                try:
                    self.migration_report = merge_legacy(self)
                except (OSError, ValueError):
                    self.migration_report = {"error": "旧个人版任务未能归并；原文件保留"}
                    ctl.tray.showMessage("简截 · 任务归并", self.migration_report["error"])
                if ctl.S.get("local_mcp_enabled", False):
                    self.set_agent_enabled(True)

        def set_agent_enabled(self, enabled):
            if enabled and self._bridge is None:
                self._bridge = Bridge(self, storage.atomic_json)
            elif not enabled and self._bridge is not None:
                self._bridge.close()
                self._bridge = None

        def agent_enabled(self):
            return self._bridge is not None

        def _check_date(self):
            if self._day != today():
                self._day = today()
                if self.widget:
                    self.widget.rebuild()

        def move_task(self, tid, target):
            task = self.tasks.get(tid)
            if task and not task.get("deleted"):
                task["plan_date"] = today() if target == "today" else None
                task["updated"] = time.time()
                self._after_change()

    class LocalTaskWidget(sticky.TaskWidget):
        def __init__(self, manager):
            super().__init__(manager)
            self.setMinimumWidth(340)
            self.title.setText("我的任务")
            self.tabs = QWidget(self.body)
            layout = QHBoxLayout(self.tabs)
            layout.setContentsMargins(2, 0, 2, 4)
            layout.setSpacing(6)
            self.tab_buttons = {}
            for key, label in (("today", "今日 TODO"), ("backlog", "待办清单")):
                button = QPushButton(label, self.tabs)
                button.setCheckable(True)
                button.setCursor(Qt.PointingHandCursor)
                button.setMinimumHeight(32)
                button.clicked.connect(lambda _, k=key: self.select_tab(k))
                layout.addWidget(button)
                self.tab_buttons[key] = button
            self.body.layout().insertWidget(1, self.tabs)
            self.tabs.setStyleSheet("QPushButton {background:rgba(255,255,255,12);color:#c5cbd5;"
                                    "border:1px solid rgba(255,255,255,20);border-radius:7px;"
                                    "padding:4px 8px;font-size:12px;}"
                                    "QPushButton:checked {background:rgba(59,130,246,50);color:#fff;"
                                    "border-color:rgba(96,165,250,120);}"
                                    "QPushButton:hover {border-color:#60a5fa;}")
            self.rebuild()

        def select_tab(self, key):
            if self._editing is not None:
                self.inp.setFocus()
                if self._editing is not None:
                    return
            self.manager.win["task_tab"] = key
            self.rebuild()
            self.manager.save()

        def rebuild(self):
            super().rebuild()
            if hasattr(self, "tab_buttons"):
                active = self.manager.win.get("task_tab", "today")
                for key, button in self.tab_buttons.items():
                    button.setChecked(key == active)
                self.inp.setPlaceholderText("添加今日任务" if active == "today" else "添加待办任务")
                for row in self.rows():
                    planned = row.task.get("plan_date")
                    if planned and planned < today() and not row.done:
                        row.setToolTip("此前未完成 · 计划 " + planned)
                    elif planned and planned > today():
                        row.setToolTip("计划 " + planned)

        def open_task_menu(self, task, anchor):
            super().open_task_menu(task, anchor)
            if task.get("id"):
                menu = self._task_menu
                menu.addSeparator()
                planned = task.get("plan_date")
                target = "backlog" if planned and planned <= today() else "today"
                action = menu.addAction("移至待办清单" if target == "backlog" else "移至今日 TODO")
                action.triggered.connect(lambda: self.manager.move_task(task["id"], target))
                menu.adjustSize()

    sticky.TaskManager = LocalTaskManager
    sticky.TaskWidget = LocalTaskWidget
    return LocalTaskManager, LocalTaskWidget
