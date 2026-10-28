import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from iphone_mirror import MirrorSession
from iphone_control import normalize_input

APP = QApplication.instance() or QApplication([])


class Control:
    available = True
    state = "connected"
    detail = "Connected"
    def __init__(self):
        self.commands = []
        self.mouse = threading.Event()
    def send(self, value):
        value = normalize_input(value)
        self.commands.append(value)
        if value["cmd"] == "mouse":
            self.mouse.set()
        return True


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.session = MirrorSession()
        self.session.control = Control()
        self.session._input_live = True
        self.state = patch.object(self.session, "_state")
        self.state.start()
        self.addCleanup(self.state.stop)
        self.addCleanup(self.session._control_metrics_timer.stop)
        self.addCleanup(APP.processEvents)

    def feed(self, *lines):
        for line in lines:
            self.session._receiver_line(self.session._generation, line)

    def test_background_input_does_not_require_gui_event_processing(self):
        worker = threading.Thread(target=self.feed, args=("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 0 12 -3 0"))
        worker.start()
        self.assertTrue(self.session.control.mouse.wait(1), "Input must arrive while the GUI is not processing events")
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertFalse(self.session._control_captured, "UI update should remain queued")
        APP.processEvents()
        self.assertTrue(self.session._control_captured)
        self.assertEqual(self.session.control.commands[0], dict(cmd="mouse", buttons=0, dx=12, dy=-3, wheel=0))

    def test_delayed_gui_capture_on_cannot_reopen_released_capture(self):
        worker = threading.Thread(target=self.feed, args=("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 1 4 0 0", "JJ_AIRPLAY_INPUT release", "JJ_AIRPLAY_CONTROL capture 0"))
        worker.start();worker.join(1)
        self.assertFalse(self.session._input_captured)
        APP.processEvents()
        self.assertFalse(self.session._control_captured)
        self.assertFalse(self.session._control_metrics_timer.isActive())
        self.assertEqual(self.session.control.commands[-1], {"cmd": "release"})

    def test_disable_seals_input_until_explicit_ready_and_recapture(self):
        self.feed("JJ_AIRPLAY_CONTROL capture 1")
        self.session._control_ready(False)
        self.feed("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 1 4 0 0")
        self.assertEqual(self.session.control.commands, [{"cmd": "release"}])
        self.session.process = object()
        self.session._control_ready(True)
        self.feed("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 0 7 0 0")
        self.assertEqual(self.session.control.commands[-1]["dx"], 7)

    def test_invalid_report_releases_before_gui_can_handle_error(self):
        worker = threading.Thread(target=self.feed, args=("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 8 4 0 0", "JJ_AIRPLAY_INPUT mouse 0 5 0 0"))
        worker.start();worker.join(1)
        self.assertFalse(self.session._input_live)
        self.assertFalse(self.session._input_captured)
        self.assertEqual(self.session.control.commands, [{"cmd": "release"}])

    def test_keys_coordinates_never_enter_gui_signals_or_tail(self):
        forwarded = []
        self.session.output.connect(lambda generation, line: forwarded.append(line))
        self.feed("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT key 0 4", "JJ_AIRPLAY_INPUT mouse 0 999 -999 0")
        APP.processEvents()
        self.assertTrue(forwarded)
        self.assertFalse(any("JJ_AIRPLAY_INPUT" in line for line in forwarded))
        self.assertFalse(any("JJ_AIRPLAY_INPUT" in line for line in self.session._tail))

    def test_worker_and_disable_preserve_final_release(self):
        first = threading.Event();resume = threading.Event()
        def worker():
            self.feed("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 1 4 0 0")
            first.set();resume.wait(1)
            self.feed("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 1 5 0 0")
        thread = threading.Thread(target=worker);thread.start()
        self.assertTrue(first.wait(1))
        self.session._control_ready(False)
        resume.set();thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.session.control.commands[-1], {"cmd": "release"})
        self.assertEqual(sum(c["cmd"] == "mouse" for c in self.session.control.commands), 1)

    def test_key_and_button_edges_keep_order(self):
        self.feed("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_INPUT mouse 1 4 0 0", "JJ_AIRPLAY_INPUT key 2 4", "JJ_AIRPLAY_INPUT mouse 0 0 0 0", "JJ_AIRPLAY_INPUT key 0")
        values = self.session.control.commands
        self.assertEqual([v["cmd"] for v in values], ["mouse", "key", "mouse", "key"])
        self.assertEqual([v.get("buttons") for v in values if v["cmd"] == "mouse"], [1,0])
        self.assertEqual(values[-1]["keys"], [])

    def test_stale_generation_and_stale_error_cannot_affect_new_capture(self):
        self.session._receiver_line(-1, "JJ_AIRPLAY_CONTROL capture 1")
        self.assertFalse(self.session._input_captured)
        self.session._generation = 2
        self.session._input_live = True
        self.feed("JJ_AIRPLAY_CONTROL capture 1")
        self.session._input_failed(1)
        self.feed("JJ_AIRPLAY_INPUT mouse 0 9 0 0")
        self.assertEqual(self.session.control.commands[-1]["dx"], 9)


if __name__ == "__main__":unittest.main(verbosity=2)
