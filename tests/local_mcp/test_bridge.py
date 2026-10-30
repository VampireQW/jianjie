"""真实 Qt 主线程 → 回环接口 → MCP 子进程的集成检查。"""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.environ.get("JJ_BASE_SOURCE", r"D:\ai project\qoder\独立项目\简截Windows版\jianjie_win"))
from PySide6.QtWidgets import QApplication


class BridgeTest(unittest.TestCase):
    def test_agent_exits_before_gui_starts_and_gui_submits_saved_request(self):
        from local_tasks.desktop import install
        from local_tasks.bridge import Bridge
        from local_tasks.outbox import Outbox
        import common as C
        import storage
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix="JianjieOfflineBridge-") as directory:
            previous_home = os.environ.get("JJ_MCP_HOME")
            previous_tasks = C.TASKS_PATH
            os.environ["JJ_MCP_HOME"] = directory
            C.TASKS_PATH = str(Path(directory) / "tasks.json")
            manager, bridge = None, None
            try:
                executable = os.environ.get("JJ_MCP_EXE")
                command = [executable, "--mcp"] if executable else [sys.executable,
                    str(Path(__file__).resolve().parents[2] / "main.pyw"), "--mcp"]
                args = {"request_id": "before-gui-001", "tasks": [{"text": "先开 Agent 的离线请求", "target": "today"}]}
                result = subprocess.run(command + ["--call", "jianjie_create_tasks", json.dumps(args)],
                                        capture_output=True, encoding="utf-8", timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["delivery_status"], "queued")
                self.assertFalse(Path(C.TASKS_PATH).exists())
                Manager, _ = install()
                manager = Manager()
                self.assertFalse(manager.agent_enabled())
                # Opening the task manager does not implicitly enable MCP or apply jobs.
                for _ in range(5):
                    app.processEvents()
                self.assertFalse(manager.tasks)
                bridge = Bridge(manager, storage.atomic_json)
                deadline = time.monotonic() + 8
                box = Outbox()
                while box.get(args["request_id"])["state"] != "applied" and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertEqual(box.get(args["request_id"])["state"], "applied")
                self.assertEqual(len(manager.tasks), 1)
                saved = json.loads(Path(C.TASKS_PATH).read_text(encoding="utf-8"))
                self.assertEqual(saved["tasks"][0]["text"], args["tasks"][0]["text"])
                # The same executable can reconnect after enable without duplicating.
                check = subprocess.run(command + ["--call", "jianjie_queue", json.dumps({
                    "action": "get", "request_id": args["request_id"]})],
                    capture_output=True, encoding="utf-8", timeout=30)
                self.assertEqual(json.loads(check.stdout)["request"]["state"], "applied")
            finally:
                if bridge:
                    bridge.close()
                    bridge.outbox_worker.thread.join(1)
                if manager:
                    manager._date_timer.stop()
                    manager._reminder_timer.stop()
                app.processEvents()
                C.TASKS_PATH = previous_tasks
                if previous_home is None:
                    os.environ.pop("JJ_MCP_HOME", None)
                else:
                    os.environ["JJ_MCP_HOME"] = previous_home

    def test_authenticated_mcp_to_gui(self):
        from local_tasks.desktop import install
        from local_tasks.bridge import Bridge
        from local_tasks.client import call
        from local_tasks.paths import endpoint_file
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(prefix="JianjieBridgeTest-") as directory:
            os.environ["JJ_MCP_HOME"] = directory
            import common as C
            original_tasks_path = C.TASKS_PATH
            C.TASKS_PATH = str(Path(directory) / "tasks.json")
            Manager, _ = install()
            import storage
            manager = Manager()
            manager.show_widget()
            bridge = Bridge(manager, storage.atomic_json)
            outcome = {}

            def work():
                try:
                    info = json.loads(endpoint_file().read_text(encoding="utf-8"))
                    for headers in ({}, {"Authorization": "Bearer " + info["token"], "Origin": "https://example.com"}):
                        conn = http.client.HTTPConnection("127.0.0.1", info["port"], timeout=5)
                        conn.request("POST", "/v1/tools/call", b"{}", {"Content-Type": "application/json", **headers})
                        response = conn.getresponse()
                        assert response.status == 403
                        response.read()
                        conn.close()
                    self.assertTrue(call("jianjie_status", {})["ok"])
                    messages = [
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "jianjie_create_tasks", "arguments": {
                            "request_id": "bridge-create", "tasks": [{"text": "MCP 中文端到端测试", "target": "today"}]}}},
                    ]
                    executable = os.environ.get("JJ_MCP_EXE")
                    command = [executable, "--mcp"] if executable else [sys.executable, str(Path(
                        os.environ.get("JJ_BASE_SOURCE", r"D:\ai project\qoder\独立项目\简截Windows版\jianjie_win")) / "main.pyw"), "--mcp"]
                    result = subprocess.run(command, input="\n".join(json.dumps(m) for m in messages) + "\n",
                        encoding="utf-8", capture_output=True, timeout=30, cwd=Path(__file__).resolve().parent.parent)
                    assert result.returncode == 0, result.stderr
                    responses = [json.loads(line) for line in result.stdout.splitlines()]
                    assert len(responses) == 3
                    assert len(responses[1]["result"]["tools"]) == 6
                    value = responses[2]["result"]["structuredContent"]
                    if value.get("queued"):
                        from local_tasks.outbox import Outbox
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            record = Outbox().get("bridge-create")
                            if record["state"] == "applied":
                                value = record["result"]
                                break
                            time.sleep(0.02)
                    assert value["ok"], value
                    assert value["tasks"][0]["text"] == "MCP 中文端到端测试"
                    outcome["value"] = value
                except BaseException as error:
                    outcome["error"] = error

            thread = threading.Thread(target=work)
            thread.start()
            deadline = time.monotonic() + 40
            while thread.is_alive() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            thread.join(timeout=1)
            try:
                if outcome.get("error"):
                    raise outcome["error"]
                self.assertIn("value", outcome)
                self.assertEqual(len(manager.widget.rows()), 1)
                self.assertEqual(manager.widget.rows()[0].task["text"], "MCP 中文端到端测试")
                saved = json.loads(Path(C.TASKS_PATH).read_text(encoding="utf-8"))
                self.assertEqual(len(saved["tasks"]), 1)
            finally:
                bridge.close()
                manager._date_timer.stop()
                manager._reminder_timer.stop()
                manager.widget._save_t.stop()
                manager.widget.hide()
                manager.widget.deleteLater()
                app.processEvents()
                os.environ.pop("JJ_MCP_HOME", None)
                C.TASKS_PATH = original_tasks_path


if __name__ == "__main__":
    unittest.main()
