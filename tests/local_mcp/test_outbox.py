import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_tasks.core import TaskError, TaskService
from local_tasks.outbox import Outbox, OutboxWorker
from local_tasks.client import call
from local_tasks.mcp import serve
from test_local_tasks import Manager, MemoryCommon


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="JianjieOutbox-")
        self.environment = patch.dict(os.environ, {"JJ_MCP_HOME": self.temp.name})
        self.environment.start()
        self.box = Outbox()
        self.common = MemoryCommon()
        self.service = TaskService(Manager(self.common))
        self.args = {"request_id": "offline-create-001", "tasks": [{"text": "等待简截启动", "target": "today"}]}

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def deliver(self, name, args, submitted):
        return self.service.execute(name, args, submitted)

    def test_offline_response_is_queued_not_applied_and_protocol_remains_live(self):
        self.assertFalse(call("jianjie_status", {})["app_available"])
        result = call("jianjie_create_tasks", self.args)
        self.assertEqual(result["delivery_status"], "queued")
        self.assertNotIn("tasks", result)
        self.assertFalse(self.common.saved["tasks"])
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                        "name": "jianjie_create_tasks", "arguments": self.args}},
                    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                        "name": "jianjie_queue", "arguments": {"action": "get", "request_id": self.args["request_id"]}}}]
        outgoing = io.BytesIO()
        serve(io.BytesIO(("\n".join(json.dumps(r) for r in requests) + "\n").encode()), outgoing)
        results = [json.loads(line)["result"] for line in outgoing.getvalue().splitlines()]
        self.assertFalse(results[1]["isError"])
        self.assertEqual(results[2]["structuredContent"]["request"]["state"], "pending")
        self.assertIn("queued", results[0]["instructions"])

    def test_persistence_worker_submits_without_another_agent_call(self):
        self.box.enqueue("jianjie_create_tasks", self.args)
        # An independently created worker, as used by GUI startup, drains saved data.
        with patch("local_tasks.client.call_online", side_effect=lambda n, a, descriptor, day: self.deliver(n, a, day)):
            worker = OutboxWorker(Outbox(), interval=0.01).start()
            try:
                deadline = time.monotonic() + 3
                while self.box.get(self.args["request_id"])["state"] != "applied" and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.box.get(self.args["request_id"])["state"], "applied")
                self.assertEqual(len(self.common.saved["tasks"]), 1)
            finally:
                worker.stop()
                worker.thread.join(2)

    def test_same_request_deduplicates_across_restarts_and_rejects_new_content(self):
        self.box.enqueue("jianjie_create_tasks", self.args)
        self.assertTrue(self.box.drain_once(self.deliver))
        box = Outbox()
        result = box.submit("jianjie_create_tasks", copy.deepcopy(self.args))
        self.assertEqual(result["delivery_status"], "applied")
        self.assertEqual(len(self.common.saved["tasks"]), 1)
        changed = {**self.args, "tasks": [{"text": "其他内容"}]}
        with self.assertRaises(TaskError) as error:
            box.enqueue("jianjie_create_tasks", changed)
        self.assertEqual(error.exception.code, "request_id_reused")

    def test_response_loss_replays_receipt_without_duplicate(self):
        self.box.enqueue("jianjie_create_tasks", self.args)
        def lost(name, args, submitted):
            self.deliver(name, args, submitted)
            raise TaskError("lost response", "app_unavailable", {"delivery_uncertain": True})
        self.assertFalse(self.box.drain_once(lost))
        with self.assertRaises(TaskError):
            self.box.manage({"action": "cancel", "request_id": self.args["request_id"]})
        self.assertTrue(Outbox().drain_once(self.deliver, force=True))
        self.assertEqual(len(self.common.saved["tasks"]), 1)
        self.assertTrue(self.box.get(self.args["request_id"])["result"]["replayed"])

    def test_expired_delivery_lease_keeps_uncertainty_until_receipt(self):
        self.box.enqueue("jianjie_create_tasks", self.args)
        self.assertIsNotNone(self.box.claim())
        self.assertIsNone(Outbox().claim(force=True))
        with self.box.database() as db:
            db.execute("UPDATE jobs SET lease_until=0")
        def unavailable(*_):
            raise TaskError("offline", "app_unavailable")
        self.box.drain_once(unavailable, force=True)
        self.assertTrue(self.box.get(self.args["request_id"])["delivery_uncertain"])
        with self.assertRaises(TaskError):
            self.box.manage({"action": "cancel", "request_id": self.args["request_id"]})

    def test_two_workers_only_one_claims_delivery(self):
        self.box.enqueue("jianjie_create_tasks", self.args)
        entered, release = threading.Event(), threading.Event()
        delivered = []
        def paused(name, args, submitted):
            entered.set()
            release.wait(3)
            delivered.append(args["request_id"])
            return self.deliver(name, args, submitted)
        thread = threading.Thread(target=lambda: self.box.drain_once(paused))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertFalse(Outbox().drain_once(self.deliver, force=True))
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(delivered, [self.args["request_id"]])

    def test_cancelled_offline_request_never_applies(self):
        self.box.enqueue("jianjie_create_tasks", self.args)
        result = self.box.manage({"action": "cancel", "request_id": self.args["request_id"]})
        self.assertEqual(result["request"]["state"], "cancelled")
        self.assertFalse(self.box.drain_once(self.deliver))
        with self.assertRaises(TaskError):
            self.box.submit("jianjie_create_tasks", self.args)
        self.assertFalse(self.common.saved["tasks"])

    def test_conflict_stays_visible_without_overwriting_newer_task(self):
        created = self.deliver("jianjie_create_tasks", self.args, "2026-10-29")["tasks"][0]
        update = {"request_id": "offline-update-001", "updates": [{"id": created["id"],
                  "version": created["version"], "patch": {"text": "旧版本修改"}}]}
        self.box.enqueue("jianjie_update_tasks", update)
        self.deliver("jianjie_update_tasks", {"request_id": "manual-update-001", "updates": [
            {"id": created["id"], "version": created["version"], "patch": {"text": "用户最新修改"}}]}, "2026-10-29")
        self.assertFalse(self.box.drain_once(self.deliver))
        self.assertEqual(self.box.get(update["request_id"])["state"], "conflict")
        self.assertEqual(self.common.saved["tasks"][0]["text"], "用户最新修改")
        with self.assertRaises(TaskError):
            self.box.manage({"action": "retry", "request_id": update["request_id"]})

    def test_today_is_first_submission_day_even_after_midnight(self):
        with patch("local_tasks.outbox.today", return_value="2026-10-28"):
            self.box.enqueue("jianjie_create_tasks", self.args)
        with patch("local_tasks.core.today", return_value="2026-10-29"):
            self.assertTrue(self.box.drain_once(self.deliver))
            replay = self.service.execute("jianjie_create_tasks", self.args, "2026-10-29")
        self.assertEqual(replay["tasks"][0]["plan_date"], "2026-10-28")
        self.assertTrue(replay["replayed"])

    def test_fifo_blocks_later_requests_while_first_is_busy(self):
        self.box.enqueue("jianjie_create_tasks", self.args)
        other = {**self.args, "request_id": "offline-create-002"}
        self.box.enqueue("jianjie_create_tasks", other)
        order = []
        def busy(name, args, day):
            order.append(args["request_id"])
            raise TaskError("editing", "busy")
        self.box.drain_once(busy)
        self.assertFalse(self.box.drain_once(self.deliver))
        self.box.drain_once(self.deliver, force=True)
        self.box.drain_once(self.deliver, force=True)
        self.assertEqual(order, [self.args["request_id"]])
        self.assertEqual(len(self.common.saved["tasks"]), 2)

    def test_invalid_requests_never_enter_queue(self):
        invalid = [({}, "jianjie_create_tasks"),
                   ({**self.args, "tasks": [{"text": "", "target": "today"}]}, "jianjie_create_tasks"),
                   ({**self.args, "tasks": [{"text": "a", "plan_date": "2026-02-30"}]}, "jianjie_create_tasks"),
                   ({"request_id": "bad-update-001", "updates": [{"id": "x", "version": True, "patch": {"done": True}}]}, "jianjie_update_tasks"),
                   ({"request_id": "bad-undo-001"}, "jianjie_undo")]
        for args, tool in invalid:
            with self.subTest(tool=tool, args=args), self.assertRaises(TaskError):
                self.box.enqueue(tool, args)
        self.assertEqual(self.box.counts()["pending"], 0)

    def test_storage_failure_is_not_reported_as_queued(self):
        with patch("local_tasks.outbox.sqlite3.connect", side_effect=__import__('sqlite3').OperationalError("full")):
            with self.assertRaises(TaskError) as error:
                self.box.submit("jianjie_create_tasks", self.args)
        self.assertEqual(error.exception.code, "queue_storage_error")

    def test_payload_pruning_keeps_old_request_deduplication(self):
        for i in range(202):
            args = {"request_id": "retained-%04d" % i, "tasks": [{"text": "任务 %d" % i}]}
            self.box.enqueue("jianjie_create_tasks", args)
            self.box.drain_once(lambda *_: {"ok": True, "tasks": []})
        first = {"request_id": "retained-0000", "tasks": [{"text": "任务 0"}]}
        self.assertIsNone(self.box.get(first["request_id"])["result"])
        self.assertEqual(self.box.submit("jianjie_create_tasks", first)["delivery_status"], "applied")
        self.assertEqual(self.box.counts()["applied"], 202)
        with self.box.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE result IS NOT NULL").fetchone()[0], 200)


if __name__ == "__main__":
    unittest.main()
