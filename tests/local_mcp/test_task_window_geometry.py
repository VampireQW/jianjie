import json
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
import common as C
import sticky


class WindowGeometryTests(unittest.TestCase):
    def setUp(self):
        self.main = QRect(0, 0, 1560, 992)
        self.left = QRect(-2560, 0, 2048, 1104)

    def fit(self, rect):
        return sticky.task_window_rect(rect, [self.main, self.left], self.main)

    def test_real_saved_position_is_returned_to_visible_screen(self):
        original = QRect(1303, 1239, 440, 436)
        fitted = self.fit(original)
        self.assertTrue(self.main.contains(fitted))
        self.assertEqual(fitted.size(), original.size())

    def test_valid_negative_monitor_coordinates_are_preserved(self):
        original = QRect(-2200, 150, 440, 436)
        self.assertEqual(self.fit(original), original)

    def test_gap_between_monitors_returns_to_primary(self):
        self.assertTrue(self.main.contains(self.fit(QRect(-450, 60, 340, 400))))

    def test_partially_visible_oversized_and_removed_monitor_positions(self):
        for original in (QRect(1480, 900, 440, 436), QRect(100, 100, 4000, 2500),
                         QRect(4000, 200, 440, 436)):
            with self.subTest(rect=original):
                self.assertTrue(self.main.contains(self.fit(original)))

    def test_show_reopen_visible_recall_and_toggle_recover_window(self):
        app = QApplication.instance() or QApplication([])
        from local_tasks.desktop import install
        install()
        original_path = C.TASKS_PATH
        with tempfile.TemporaryDirectory() as directory:
            C.TASKS_PATH = str(Path(directory) / "tasks.json")
            Path(C.TASKS_PATH).write_text(json.dumps({"win": {"x": 1303, "y": 1239,
                "w": 440, "h": 436, "visible": True, "ontop": True}, "tasks": []}), encoding="utf-8")
            manager = sticky.TaskManager()
            try:
                manager.load_and_show()
                app.processEvents()
                def assert_visible():
                    self.assertTrue(manager.widget.isVisible())
                    self.assertTrue(any(s.availableGeometry().contains(manager.widget.geometry()) for s in app.screens()))
                assert_visible()
                manager.widget.move(5000, 5000)
                manager.show_or_raise()
                app.processEvents()
                assert_visible()
                manager.widget.move(5000, 5000)
                manager.toggle()
                app.processEvents()
                assert_visible()
                manager.widget.showMinimized()
                app.processEvents()
                manager.show_or_raise()
                app.processEvents()
                self.assertFalse(manager.widget.isMinimized())
                assert_visible()
                manager.hide_widget()
                manager.show_widget()
                app.processEvents()
                assert_visible()
                manager.save()
                saved = json.loads(Path(C.TASKS_PATH).read_text(encoding="utf-8"))["win"]
                self.assertEqual(saved["x"], manager.widget.x())
                self.assertEqual(saved["y"], manager.widget.y())
                self.assertTrue(saved["ontop"])
            finally:
                manager._date_timer.stop()
                manager._reminder_timer.stop()
                if manager.widget:
                    manager.widget._save_t.stop()
                    manager.widget.hide()
                    manager.widget.deleteLater()
                app.processEvents()
                C.TASKS_PATH = original_path


if __name__ == "__main__":
    unittest.main()
