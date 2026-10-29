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
                    assert len(responses[1]["result"]["tools"]) == 5
                    value = responses[2]["result"]["structuredContent"]
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
