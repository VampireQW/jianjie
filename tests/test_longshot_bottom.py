import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from longshot import LongShotBar, _ColumnState, compose_columns, stitch_pair


class LongShotBottomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        cursor = patch("longshot.QCursor.setPos")
        cursor.start()
        self.addCleanup(cursor.stop)
        rng = np.random.default_rng(205)
        self.left_page = rng.integers(0, 256, (1200, 220, 3), dtype=np.uint8)
        self.right_page = rng.integers(0, 256, (1200, 220, 3), dtype=np.uint8)
        self.fixed_panel = rng.integers(0, 256, (240, 220, 3), dtype=np.uint8)

    def right_frame(self, offset, bottom_height):
        return np.vstack((self.right_page[offset:offset + 360 - bottom_height],
                          self.fixed_panel[:bottom_height]))

    def test_right_column_preserves_selected_bottom_once(self):
        for bottom_height in (90, 180, 240):
            with self.subTest(bottom_height=bottom_height):
                state = _ColumnState(self.right_frame(0, bottom_height))
                for offset in (40, 80, 120):
                    state.feed(self.right_frame(offset, bottom_height))
                expected = np.vstack((self.right_page[:360 - bottom_height + 120],
                                      self.fixed_panel[:bottom_height]))
                np.testing.assert_array_equal(state.long, expected)

    def test_independent_right_scroll_keeps_left_and_lower_panel(self):
        left = _ColumnState(self.left_page[:360].copy())
        right = _ColumnState(self.right_frame(0, 180))
        for offset in (40, 80, 120):
            left.feed(self.left_page[:360])
            right.feed(self.right_frame(offset, 180))
        result = compose_columns(left.long, right.long)
        np.testing.assert_array_equal(result[:360, :220], self.left_page[:360])
        np.testing.assert_array_equal(result[:, 220:], np.vstack((
            self.right_page[:300], self.fixed_panel[:180])))
        self.assertEqual(result.shape, (480, 440, 3))
        self.assertTrue(result[360:, :220].any())

    def test_scrolling_column_without_bottom_panel_still_matches(self):
        state = _ColumnState(self.left_page[:360].copy())
        for offset in (60, 120, 180):
            state.feed(self.left_page[offset:offset + 360])
        np.testing.assert_array_equal(state.long, self.left_page[:540])
        before = state.long.copy()
        state.feed(self.left_page[180:540])
        np.testing.assert_array_equal(state.long, before)

    def test_large_fixed_header_and_bottom_surrounding_scroll_area(self):
        header = self.fixed_panel[:120]
        footer = self.fixed_panel[120:]
        def frame(offset):
            return np.vstack((header, self.right_page[offset:offset + 120], footer))
        state = _ColumnState(frame(0))
        for offset in (30, 60, 90):
            state.feed(frame(offset))
        np.testing.assert_array_equal(state.long, np.vstack((
            header, self.right_page[:210], footer)))

    def test_columns_scroll_at_different_times_with_complete_bottom(self):
        left = _ColumnState(self.left_page[:360].copy())
        right = _ColumnState(self.right_frame(0, 180))
        for left_y, right_y in ((60, 0), (120, 0), (120, 40), (120, 80), (180, 120)):
            left.feed(self.left_page[left_y:left_y + 360])
            right.feed(self.right_frame(right_y, 180))
        result = compose_columns(left.long, right.long)
        np.testing.assert_array_equal(result[:, :220], self.left_page[:540])
        np.testing.assert_array_equal(result[:480, 220:], np.vstack((
            self.right_page[:300], self.fixed_panel[:180])))
        self.assertTrue(result[480:, 220:].any())

    def test_matching_content_after_footer_removal_does_not_skip_rows(self):
        footer = self.fixed_panel[:40]
        previous = np.vstack((self.left_page[:320], footer))
        accumulated = previous.copy()
        for offset in (100, 200):
            frame = np.vstack((self.left_page[offset:offset + 320], footer))
            s, e, score, bh = stitch_pair(accumulated, frame, prev=previous,
                                         strip_footer=offset == 100)
            self.assertGreater(score, 0.9)
            self.assertEqual(bh, 40)
            if offset == 100:
                accumulated = accumulated[:-bh]
            accumulated = np.vstack((accumulated, frame[s:e]))
            previous = frame
        np.testing.assert_array_equal(accumulated, self.left_page[:520])

    def test_toolbar_single_column_delivers_complete_selected_bottom_to_editor(self):
        frames = [self.right_frame(offset, 180) for offset in (0, 40, 80, 120)]
        delivered = []
        with patch("longshot.grab_array", side_effect=frames), \
                patch("longshot.C.force_foreground"), patch("longshot.C.exclude_from_capture"):
            bar = LongShotBar(lambda *args, **kwargs: None, lambda: None,
                              QRect(0, 0, 220, 360), self.app.primaryScreen(),
                              lambda image, count: delivered.append(image))
            bar.timer.stop()
            try:
                for _ in range(3):
                    bar.grab()
                bar.edit()
                np.testing.assert_array_equal(delivered[0], np.vstack((
                    self.right_page[:300], self.fixed_panel[:180])))
                self.assertIsNone(bar.long_arr)
            finally:
                bar.cancel()

    def test_toolbar_dual_column_delivers_complete_right_bottom_to_editor(self):
        frames = [np.hstack((self.left_page[:360], self.right_frame(offset, 180)))
                  for offset in (0, 40, 80, 120)]
        delivered = []
        with patch("longshot.grab_array", side_effect=frames), \
                patch("longshot.C.force_foreground"), patch("longshot.C.exclude_from_capture"):
            bar = LongShotBar(lambda *args, **kwargs: None, lambda: None,
                              QRect(0, 0, 440, 360), self.app.primaryScreen(),
                              lambda image, count: delivered.append(image))
            bar.timer.stop()
            try:
                bar.bcols.click()
                for _ in range(3):
                    bar.grab()
                bar.edit()
                np.testing.assert_array_equal(delivered[0][:, 220:], np.vstack((
                    self.right_page[:300], self.fixed_panel[:180])))
                self.assertEqual(delivered[0].shape, (480, 440, 3))
                self.assertIsNone(bar._colR)
            finally:
                bar.cancel()


if __name__ == "__main__":
    unittest.main()
