"""正式版简截的任务页与可开关的本地 MCP。"""
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMenu, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)
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
                from task_nudges import TodayNudges
                self.today_nudges = TodayNudges(self, ctl)

        def _arm_reminders(self):
            super()._arm_reminders()
            nudges = getattr(self, "today_nudges", None)
            if nudges is not None:
                nudges.changed()

        def set_agent_enabled(self, enabled):
            if enabled and self._bridge is None:
                self._bridge = Bridge(self, storage.atomic_json)
            elif not enabled and self._bridge is not None:
                self._bridge.close()
                self._bridge = None
            if self.widget is not None:
                self.widget.refresh_mcp_status()

        def agent_enabled(self):
            return self._bridge is not None

        def _check_date(self):
            if self._day != today():
                self._day = today()
                if self.widget:
                    self.widget.rebuild()
                nudges = getattr(self, "today_nudges", None)
                if nudges is not None:
                    nudges.changed()

        def move_task(self, tid, target):
            task = self.tasks.get(tid)
            if task and not task.get("deleted"):
                task["plan_date"] = today() if target == "today" else None
                task["updated"] = time.time()
                self._after_change()

    class LocalTaskWidget(sticky.TaskWidget):
        # Only one inactive view can be retained. Large views are rebuilt on demand.
        INACTIVE_ROW_LIMIT = 128

        def __init__(self, manager):
            super().__init__(manager)
            self.setMinimumWidth(340)
            self.title.setText("我的任务")
            self._mcp_info = None
            header = self.head.layout()
            header.setSpacing(4)
            self.mcp_button = QPushButton('MCP', self.head)
            self.mcp_button.setObjectName('taskMcpEntry')
            self.mcp_button.setAutoDefault(False)
            self.mcp_button.setFixedSize(42, 28)
            self.mcp_button.setCursor(Qt.PointingHandCursor)
            self.mcp_button.setAccessibleName('MCP 连接信息')
            self.mcp_button.clicked.connect(self.show_mcp_info)
            header.insertWidget(header.indexOf(self.btn_pin), self.mcp_button)
            self.refresh_mcp_status()
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
            active = self.manager.win.get('task_tab', 'today')
            if active not in self.tab_buttons:
                active = 'today'
                self.manager.win['task_tab'] = active
            self._active_tab = active
            self._page_day = today()
            self._page_stack = QStackedWidget(self.body)
            self._page_stack.setObjectName('taskPages')
            self._page_stack.setStyleSheet('QStackedWidget#taskPages{background:transparent;border:none;}')
            self.body.layout().replaceWidget(self.scroll, self._page_stack)
            self._page_stack.addWidget(self.scroll)
            self._tab_pages = {active: dict(scroll=self.scroll, host=self.list_host, layout=self.list_lay,
                                            dirty=False, count=self.count_lbl.text(), rows=len(self.rows()))}
            self._sync_tab_controls()

        def refresh_mcp_status(self):
            enabled = bool(getattr(self.manager, 'agent_enabled', lambda: False)())
            self.mcp_button.setToolTip(('本地 MCP 已开启' if enabled else '本地 MCP 未开启')+' · 点击复制 Agent 接入配置')
            self.mcp_button.setStyleSheet(
                'QPushButton#taskMcpEntry{border:none;border-radius:6px;padding:0;font-size:11px;font-weight:600;'
                +('color:#9cc6ff;background:rgba(59,130,246,28);}' if enabled else
                  'color:#a4afc1;background:rgba(255,255,255,10);}')
                +'QPushButton#taskMcpEntry:hover{color:#e2eeff;background:rgba(96,165,250,38);}'
                'QPushButton#taskMcpEntry:pressed{background:rgba(96,165,250,54);}')
            if self._mcp_info is not None:
                self._mcp_info.refresh_status()

        def show_mcp_info(self):
            dialog = self._mcp_info
            if dialog is None:
                from .info import McpInfoDialog
                dialog = McpInfoDialog(self.manager, self)
                self._mcp_info = dialog
                dialog.setAttribute(Qt.WA_DeleteOnClose, True)

                def forget(_object=None):
                    if self._mcp_info is dialog:
                        self._mcp_info = None

                dialog.destroyed.connect(forget)
                area = self.screen().availableGeometry()
                x = max(area.left()+12, min(self.frameGeometry().right()-dialog.width(), area.right()-dialog.width()-12))
                y = max(area.top()+12, min(self.frameGeometry().top()+42, area.bottom()-dialog.height()-12))
                dialog.move(x, y)
            dialog.refresh_status()
            dialog.showNormal() if dialog.isMinimized() else dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            C.force_foreground(dialog)

        def select_tab(self, key):
            if key not in self.tab_buttons:
                return
            if self._editing is not None:
                QApplication.inputMethod().commit()
                self.inp.setFocus()
                self.finish_edit(commit=True)
            if key == self._active_tab and self._page_day == today():
                self._sync_tab_controls()
                return
            # Finish any checked-row transition against the old view, before changing scope.
            if any(row._completing for row in self.rows()):
                self.rebuild()
                self.manager.save()
            self.scroll._stop_wheel_animation()
            if self._page_day != today():
                self._page_day = today()
                for page in self._tab_pages.values():
                    page['dirty'] = True
            self.manager.win["task_tab"] = key
            page = self._activate_page(key)
            if page['dirty']:
                self._render_page()
            else:
                self.count_lbl.setText(page['count'])
            self._page_stack.setCurrentWidget(self.scroll)
            self._sync_tab_controls()
            self._trim_page_cache()
            self.schedule_save()
            nudges = getattr(self.manager, "today_nudges", None)
            if key == "today" and nudges is not None and self.isActiveWindow():
                nudges.reviewed(defer_save=True)

        def _activate_page(self, key):
            page = self._tab_pages.get(key)
            if page is None:
                scroll = sticky._SmoothTaskScrollArea(self._page_stack)
                scroll.setWidgetResizable(True)
                scroll.setFrameShape(QFrame.NoFrame)
                scroll.viewport().setAutoFillBackground(False)
                host = QWidget()
                host.setObjectName('taskList')
                host.setAutoFillBackground(False)
                layout = QVBoxLayout(host)
                layout.setContentsMargins(0, 2, 0, 0)
                layout.setSpacing(1)
                layout.addStretch(1)
                scroll.setWidget(host)
                self._page_stack.addWidget(scroll)
                page = dict(scroll=scroll, host=host, layout=layout, dirty=True, count='', rows=0)
                self._tab_pages[key] = page
            self._active_tab = key
            self.scroll, self.list_host, self.list_lay = page['scroll'], page['host'], page['layout']
            return page

        def _trim_page_cache(self, release_all=False):
            for key, page in list(self._tab_pages.items()):
                if key != self._active_tab and (release_all or page['rows'] > self.INACTIVE_ROW_LIMIT):
                    page['scroll']._stop_wheel_animation()
                    self._page_stack.removeWidget(page['scroll'])
                    page['scroll'].hide()
                    page['scroll'].deleteLater()
                    del self._tab_pages[key]

        def hideEvent(self, event):
            super().hideEvent(event)
            if hasattr(self, '_tab_pages'):
                self._trim_page_cache(release_all=True)

        def rebuild(self):
            if hasattr(self, '_tab_pages'):
                for page in self._tab_pages.values():
                    page['dirty'] = True
                self._page_day = today()
                key = self.manager.win.get('task_tab', 'today')
                self._activate_page(key if key in self.tab_buttons else 'today')
            self._render_page()
            if hasattr(self, '_page_stack'):
                self._page_stack.setCurrentWidget(self.scroll)
                self._sync_tab_controls()
                self._trim_page_cache()

        def _render_page(self):
            super().rebuild()
            if self._editing is not None:
                return
            rows, day = self.rows(), today()
            for row in rows:
                planned = row.task.get('plan_date')
                if planned and planned < day and not row.done:
                    row.setToolTip('此前未完成 · 计划 ' + planned)
                elif planned and planned > day:
                    row.setToolTip('计划 ' + planned)
                else:
                    row.setToolTip('')
            if hasattr(self, '_tab_pages'):
                self._tab_pages[self._active_tab].update(dirty=False, count=self.count_lbl.text(), rows=len(rows))

        def _sync_tab_controls(self):
            for key, button in self.tab_buttons.items():
                button.setChecked(key == self._active_tab)
            self.inp.setPlaceholderText('添加今日任务' if self._active_tab == 'today' else '添加待办任务')

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
