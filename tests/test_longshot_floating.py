import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from longshot import LongShotBar, _ColumnState


def table_page(height=1600, width=960):
    page = np.full((height, width, 3), 255, np.uint8)
    rng = np.random.default_rng(311)
    for y in range(0, height - 43, 44):
        page[y] = (221, 230, 250)
        for x, span in ((24, 175), (480, 34), (650, 64), (755, 125)):
            # Repeated table rules and link columns outweigh the distinct glyphs.
            glyphs = rng.integers(0, 2, (11, span), dtype=np.uint8) if x < 755 else None
            if glyphs is None:
                glyphs = np.tile(np.arange(span) % 5 < 2, (11, 1))
            page[y + 16:y + 27, x:x + span][glyphs != 0] = (45, 60, 115)
    return page


class FloatingControlTests(unittest.TestCase):
    def setUp(self):
        self.page = table_page()
        self.height = 480

    def frame(self, offset, *, animate=False):
        frame = self.page[offset:offset + self.height].copy()
        yy, xx = np.ogrid[:54, :54]
        button = (yy - 27)**2 + (xx - 27)**2 < 26**2
        frame[-70:-16, -65:-11][button] = (147, 147, 147)
        frame[-50:-43, -43:-32] = (248, 248, 248)
        if animate:
            frame[-50:-43, -43:-32] = (245, 245, 245)
        return frame

    def test_fixed_corner_button_appears_once_in_single_column_export(self):
        app = QApplication.instance() or QApplication([])
        frames = [self.frame(offset) for offset in (0, 19, 57, 163, 429, 560)]
        delivered = []
        with patch('longshot.grab_array', side_effect=frames), patch('longshot.QCursor.setPos'), \
                patch('longshot.C.force_foreground'), patch('longshot.C.exclude_from_capture'):
            bar = LongShotBar(lambda *args, **kwargs: None, lambda: None,
                              QRect(0, 0, 960, self.height), app.primaryScreen(),
                              lambda image, count: delivered.append(image))
            bar.timer.stop()
            try:
                for _ in frames[1:]:
                    bar.grab()
                bar.edit()
            finally:
                bar.cancel()
        expected = self.page[:1040].copy()
        expected[-self.height:] = frames[-1]
        np.testing.assert_array_equal(delivered[0], expected)

    def test_going_back_and_holding_at_bottom_does_not_repeat_rows(self):
        state = _ColumnState(self.frame(0))
        state.feed(self.frame(120))
        state.feed(self.frame(290))
        for offset in (220, 80, 289, 290, 290):
            state.feed(self.frame(offset, animate=True))
            self.assertEqual(len(state.long), 770)
        state.feed(self.frame(395))
        self.assertEqual(len(state.long), 875)

    def test_animated_bottom_does_not_override_verified_table_motion(self):
        rng = np.random.default_rng(44)

        def frame(offset):
            current = self.frame(offset)
            current[-64:, 120:600] = rng.integers(0, 256, (64, 480, 3), dtype=np.uint8)
            return current

        state = _ColumnState(frame(0))
        for offset in (90, 180, 270):
            self.assertEqual(state.feed(frame(offset))[0], 90)
        self.assertEqual(len(state.long), 750)
        np.testing.assert_array_equal(state.long[:, :100], self.page[:750, :100])


if __name__ == '__main__':
    unittest.main()
