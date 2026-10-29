"""与现有 TaskManager 组合使用的持久化与版本控制。"""
import copy
import time
from .core import fingerprint


class TaskStateMixin:
    def _load(self):
        if self._loaded:
            return
        data = self._task_common.load_tasks()
        self.agent_meta = copy.deepcopy(data.get("agent", {}))
        self._versions = {}
        super()._load()
        self.reset_versions()

    def reset_versions(self):
        self._versions = {}
        for tid, task in self.tasks.items():
            task.setdefault("version", 1)
            self._versions[tid] = fingerprint(task)

    def refresh_versions(self):
        for tid, task in self.tasks.items():
            value = fingerprint(task)
            previous = self._versions.get(tid)
            if previous is None:
                task.setdefault("version", 1)
            elif previous != value:
                task["version"] = task.get("version", 1) + 1
                task["updated"] = time.time()
            self._versions[tid] = value

    def save(self):
        self._load()
        self.refresh_versions()
        if self.widget is not None:
            self.win.update(self.widget.win_state())
        self._task_common.save_tasks({"win": self.win, "tasks": list(self.tasks.values()),
                                      "agent": self.agent_meta})

    def _after_change(self, refresh=True):
        if getattr(self, "_adding_ids", None) is not None:
            for tid in set(self.tasks) - self._adding_ids:
                self.tasks[tid]["plan_date"] = self._adding_plan
        self._arm_reminders()
        if self.widget is not None and refresh:
            self.widget.rebuild()
        self.save()

    def add(self, text, prio=0, schedule=None):
        # 原 add 在保存前创建对象；由当前页决定计划日期，保持原提醒逻辑。
        self._adding_ids = set(self.tasks)
        self._adding_plan = self._selected_plan()
        try:
            return super().add(text, prio, schedule)
        finally:
            self._adding_plan = None
            self._adding_ids = None

    def _selected_plan(self):
        from .core import today
        return today() if self.win.get("task_tab", "today") == "today" else None

    def clear_done(self):
        ids = [t["id"] for t in self.lists()[1]]
        for tid in ids:
            self.tasks[tid]["deleted"] = True
        if ids:
            self._after_change()
        return len(ids)

    def lists(self):
        todo, done = super().lists()
        from .core import today
        scope = self.win.get("task_tab", "today")

        def matches(task):
            planned = task.get("plan_date")
            if scope == "today":
                return bool(planned and planned <= today() and
                            (not task.get("done") or planned == today()))
            return not planned or planned > today()

        return [t for t in todo if matches(t)], [t for t in done if matches(t)]
