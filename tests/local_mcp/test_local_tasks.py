import copy
import io
import json
import unittest
from unittest.mock import patch
from local_tasks.core import TaskError, TaskService, fingerprint, today
from local_tasks.state import TaskStateMixin
from local_tasks.mcp import serve


class MemoryCommon:
    def __init__(self):
        self.saved = {"win": {}, "tasks": []}
        self.fail = False

    def load_tasks(self):
        return copy.deepcopy(self.saved)

    def save_tasks(self, value):
        if self.fail:
            raise OSError("disk full")
        self.saved = copy.deepcopy(value)


class BaseManager:
    def __init__(self, common):
        self._task_common = common
        self._loaded = False
        self.tasks, self.win, self.widget = {}, {}, None

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        value = self._task_common.load_tasks()
        self.tasks = {t["id"]: t for t in value["tasks"]}
        self.win = value["win"]

    def _arm_reminders(self):
        pass


class Manager(TaskStateMixin, BaseManager):
    pass


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.common = MemoryCommon()
        self.manager = Manager(self.common)
        self.service = TaskService(self.manager)

    def create(self, tasks=None, request="create-001"):
        return self.service.execute("jianjie_create_tasks", {"request_id": request, "tasks": tasks or [
            {"text": "处理客户反馈", "target": "today", "due_date": "2030-03-01"},
            {"text": "整理资料"}]})

    def update(self, task, patch, request="update-001"):
        return self.service.execute("jianjie_update_tasks", {"request_id": request, "updates": [
            {"id": task["id"], "version": task["version"], "patch": patch}]})

    def test_beijing_and_split(self):
        result = self.create()
        self.assertEqual(result["tasks"][0]["plan_date"], today())
        self.assertIsNone(result["tasks"][1]["plan_date"])
        self.assertEqual(len(self.service.execute("jianjie_list_tasks", {"list": "today"})["tasks"]), 1)

    def test_retry_and_restart_do_not_duplicate(self):
        first = self.create()
        self.assertTrue(self.create()["replayed"])
        restarted = TaskService(Manager(self.common))
        second = restarted.execute("jianjie_create_tasks", {"request_id": "create-001", "tasks": [
            {"text": "处理客户反馈", "target": "today", "due_date": "2030-03-01"}, {"text": "整理资料"}]})
        self.assertEqual(first["tasks"], second["tasks"])
        self.assertEqual(len(self.manager.tasks), 2)
        with self.assertRaises(TaskError):
            self.create([{"text": "其他内容"}])

    def test_move_retains_identity_and_due(self):
        task = self.create()["tasks"][0]
        moved = self.update(task, {"target": "backlog"})["tasks"][0]
        self.assertEqual(moved["id"], task["id"])
        self.assertEqual(moved["due_date"], task["due_date"])
        self.assertIsNone(moved["plan_date"])
        self.assertEqual(moved["version"], task["version"] + 1)

    def test_batch_validation_atomic(self):
        with self.assertRaises(TaskError):
            self.create([{"text": "有效任务"}, {"text": "", "priority": 0}])
        self.assertFalse(self.manager.tasks)
        with self.assertRaises(TaskError):
            self.create([{"text": "非法日期", "plan_date": "2026-02-30"}])
        self.assertFalse(self.manager.tasks)

    def test_update_atomic_and_conflict(self):
        a, b = self.create()["tasks"]
        before = copy.deepcopy(self.manager.tasks)
        with self.assertRaises(TaskError):
            self.service.execute("jianjie_update_tasks", {"request_id": "update-001", "updates": [
                {"id": a["id"], "version": 1, "patch": {"done": True}},
                {"id": b["id"], "version": 999, "patch": {"done": True}}]})
        self.assertEqual(before, self.manager.tasks)

    def test_save_failure_rolls_back(self):
        task = self.create()["tasks"][0]
        before = copy.deepcopy(self.manager.tasks)
        self.common.fail = True
        with self.assertRaises(OSError):
            self.update(task, {"done": True})
        self.assertEqual(before, self.manager.tasks)
        self.common.fail = False
        self.assertTrue(self.update(task, {"done": True})["tasks"][0]["done"])

    def test_completion_delete_restore(self):
        task = self.create()["tasks"][0]
        task = self.update(task, {"done": True})["tasks"][0]
        self.assertTrue(self.manager.tasks[task["id"]]["done_at"] > 0)
        task = self.update(task, {"deleted": True}, "delete-001")["tasks"][0]
        with self.assertRaises(TaskError):
            self.update(task, {"text": "已删不可改"}, "update-deleted")
        task = self.update(task, {"deleted": False, "done": False}, "restore-001")["tasks"][0]
        self.assertFalse(task["deleted"] or task["done"])

    def test_undo_preserves_later_edits(self):
        created = self.create()
        task = created["tasks"][0]
        moved = self.update(task, {"target": "backlog"})
        self.service.execute("jianjie_undo", {"request_id": "undo-0001", "batch_id": moved["batch_id"]})
        current = self.manager.tasks[task["id"]]
        self.assertEqual(current["plan_date"], today())
        self.assertGreater(current["version"], 2)
        with self.assertRaises(TaskError):
            self.service.execute("jianjie_undo", {"request_id": "undo-0002", "batch_id": created["batch_id"]})

    def test_ui_edits_advance_versions(self):
        created = self.create()
        task = created["tasks"][0]
        self.manager.tasks[task["id"]]["text"] = "用户在界面中改了内容"
        self.manager.save()
        with self.assertRaises(TaskError) as error:
            self.update(task, {"done": True})
        self.assertEqual(error.exception.code, "conflict")

    def test_previous_unfinished_stays_visible_and_future_in_backlog(self):
        self.create([{"text": "昨天没完成", "plan_date": "2026-01-01"},
                     {"text": "以后再办", "plan_date": "2030-01-01"}])
        self.assertEqual(self.service.execute("jianjie_list_tasks", {"list": "today"})["tasks"][0]["text"], "昨天没完成")
        self.assertEqual(self.service.execute("jianjie_list_tasks", {"list": "backlog"})["tasks"][0]["text"], "以后再办")

    def test_unknown_fields_rejected(self):
        with self.assertRaises(TaskError):
            self.create([{"text": "任务", "file": "C:/anything"}])
        with self.assertRaises(TaskError):
            self.create([{"text": "任务", "priority": True}])

    def test_mcp_wire_protocol_and_unicode(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "jianjie_create_tasks", "arguments": {"request_id": "wire-001", "tasks": [{"text": "中文任务"}]}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "unknown"}},
        ]
        incoming = io.BytesIO(("\n".join(json.dumps(m) for m in messages) + "\n").encode())
        outgoing = io.BytesIO()
        serve(incoming, outgoing, self.service.execute)
        responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual(len(responses), 4)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(len(responses[1]["result"]["tools"]), 6)
        self.assertEqual(responses[2]["result"]["structuredContent"]["tasks"][0]["text"], "中文任务")
        self.assertTrue(responses[3]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
