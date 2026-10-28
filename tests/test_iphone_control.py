import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from iphone_control import ControlSession, mouse_reports, normalize_input
from iphone_mirror import MirrorSession

APP = QApplication.instance() or QApplication([])


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return False


class FakeProcess:
    def __init__(self):
        self.commands = []
        self.done = threading.Event()
        self.lines = queue.Queue()
        self.first_mouse = threading.Event()
        self.returncode = None
        self.terminated = False
        self.stdin = self.Input(self)
        self.stdout = self.Output(self)

    class Input:
        def __init__(self, owner):
            self.owner = owner

        def write(self, line):
            item = json.loads(line)
            self.owner.commands.append(item)
            # Deliberately withhold the first mouse ACK: release must preempt it.
            if item["cmd"] == "mouse" and not self.owner.first_mouse.is_set():
                self.owner.first_mouse.set()
                return len(line)
            self.owner.lines.put((json.dumps(dict(type="inputAck", seq=item["seq"])) + "\n").encode())
            if item["cmd"] == "quit":
                self.owner.returncode = 0
                self.owner.done.set()
                self.owner.lines.put(b"")
            return len(line)

        def close(self):
            pass

    class Output:
        def __init__(self, owner):
            self.owner = owner

        def readline(self, limit):
            return self.owner.lines.get(timeout=4)

        def close(self):
            pass

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise TimeoutError()
        return self.returncode

    def terminate(self):
        self.terminated = True


class InputTests(unittest.TestCase):
    def setUp(self):
        diagnostics = patch('iphone_control.record_failure')
        diagnostics.start()
        self.addCleanup(diagnostics.stop)

    def test_split_preserves_signed_distance_and_button_edges(self):
        original = dict(cmd="mouse", buttons=1, dx=401, dy=-260, wheel=130)
        parts = list(mouse_reports(original))
        for name in ("dx", "dy", "wheel"):
            self.assertEqual(sum(part[name] for part in parts), original[name])
            self.assertTrue(all(abs(part[name]) <= 127 for part in parts))
        self.assertTrue(all(part["buttons"] == 1 for part in parts))
        self.assertEqual(len(list(mouse_reports(dict(cmd="mouse", buttons=0, dx=0, dy=0, wheel=0)))), 1)

    def test_rejects_invalid_hid_states(self):
        for value in ({"cmd": "mouse", "buttons": 8}, {"cmd": "key", "keys": [4] * 7},
                      {"cmd": "key", "mod": 256}, {"cmd": "key", "keys": [232]}, {"cmd": "shell"}):
            with self.assertRaises(ValueError):
                normalize_input(value)
        self.assertEqual(normalize_input(dict(cmd="key", keys=[1] * 6))["keys"], [1] * 6)

    def test_subscription_not_advertisement_gates_input(self):
        session = ControlSession()
        session.process = object()
        session._receive(0, dict(type="ready", computerName="Test", inputAckSupported=True))
        self.assertEqual(session.state, "waiting")
        self.assertFalse(session.send(dict(cmd="mouse", dx=1)))
        session._receive(0, dict(type="connected", mouseSubscribed=True, keyboardSubscribed=False))
        self.assertTrue(session.send(dict(cmd="mouse", dx=1)))
        self.assertFalse(session.send(dict(cmd="key", keys=[4])))

    def test_release_clears_pending_and_increments_epoch(self):
        session = ControlSession()
        session.process = object()
        session.state, session.mouse_subscribed = "connected", True
        session.send(dict(cmd="mouse", buttons=1, dx=10))
        session.send(dict(cmd="key", keys=[]))
        previous = session._input_epoch
        session.send(dict(cmd="release"))
        self.assertEqual([item["cmd"] for item in session._pending], ["release"])
        self.assertGreater(session._input_epoch, previous)

    def test_release_preempts_inflight_split_and_pipe_is_bounded(self):
        process = FakeProcess()
        session = ControlSession()
        with patch("iphone_control.subprocess.Popen", return_value=process):
            session.start()
        session._receive(session._generation, dict(type="connected", mouseSubscribed=True, keyboardSubscribed=True))
        session.send(dict(cmd="mouse", buttons=1, dx=32767))
        self.assertTrue(process.first_mouse.wait(1))
        self.assertEqual(len(process.commands), 1, "Writer must wait for ACK before writing another report")
        session.send(dict(cmd="release"))
        self.assertTrue(until(lambda: any(item["cmd"] == "release" for item in process.commands)))
        self.assertEqual(sum(item["cmd"] == "mouse" for item in process.commands), 1)
        session.stop()
        self.assertTrue(until(lambda: session.process is None))
        self.assertFalse(process.terminated)

    def test_error_stops_helper_and_preserves_retry_message(self):
        session = ControlSession()
        session.process = FakeProcess()
        session.state, session.mouse_subscribed = "connected", True
        session._receive(0, dict(type="error", stage="release", message="disconnected"))
        self.assertTrue(session._stop_requested)
        self.assertEqual([item["cmd"] for item in session._pending], ["release", "quit"])
        # Further errors from the same release cannot enqueue an infinite feedback loop.
        session._receive(0, dict(type="error", stage="release", message="again"))
        self.assertEqual(len(session._pending), 2)
        session.process.returncode = 0
        session.process.done.set()
        session._finish(0, 0)
        self.assertEqual((session.state, session.detail), ("error", "电脑控制暂时不可用，请检查蓝牙连接后重新开启。"))

    def test_timing_snapshot_contains_only_bounded_numeric_metrics(self):
        session = ControlSession()
        session.process = object()
        session.state, session.mouse_subscribed = "connected", True
        for _ in range(100):
            session.send(dict(cmd="mouse", buttons=1, dx=5, dy=2))
        self.assertEqual(len(session._pending), 1)
        snapshot = session.metrics()
        self.assertEqual(set(snapshot), {"reportsSent", "reportsAcked", "maxQueueDepth", "queueDepth", "queueP95Ms", "ackP95Ms", "helperP95Ms"})
        self.assertTrue(all(value is None or isinstance(value, (int, float)) for value in snapshot.values()))
        self.assertEqual(snapshot["maxQueueDepth"], 1)

    def test_initial_zero_subscriptions_keep_assistive_touch_instructions(self):
        session = ControlSession()
        session.process = object()
        session._receive(0, dict(type="ready", computerName="Test", inputAckSupported=True))
        session._receive(0, dict(type="subscribed", mouseSubscribed=False, keyboardSubscribed=False))
        self.assertIn("辅助触控", session.detail)
        self.assertIn("设备 → 蓝牙设备", session.detail)

    def test_stale_receiver_input_and_input_before_capture_are_ignored(self):
        session = MirrorSession()
        class Control:
            available = True
            state = "connected"
            detail = "Connected"
            mouse_subscribed = True
            keyboard_subscribed = True
            process = None
            def __init__(self):
                self.commands = []
            def send(self, value):
                self.commands.append(value)
        session.control = Control()
        session._input_live = True
        with patch.object(session, "_state"):
            try:
                session._receiver_line(-1, "JJ_AIRPLAY_INPUT mouse 1 12 0 0")
                session._receiver_line(0, "JJ_AIRPLAY_INPUT mouse 1 12 0 0")
                self.assertEqual(session.control.commands, [])
                session._receiver_line(0, "JJ_AIRPLAY_CONTROL capture 1")
                session._receiver_line(0, "JJ_AIRPLAY_INPUT mouse 1 999999 -999999 0")
                self.assertEqual(session.control.commands[-1], dict(cmd="mouse", buttons=1, dx=2048, dy=-2048, wheel=0))
                self.assertEqual(session._tail, [], "Input must not enter diagnostic tail")
            finally:
                session._control_metrics_timer.stop()


if __name__ == "__main__":
    unittest.main()
