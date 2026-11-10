"""Horizontal admin tables: frozen columns, shadows and hover repainting."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from pathlib import Path
import unittest
import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import longshot as ls

APP = QApplication.instance() or QApplication([])


def table_image(width, height=320, fixed=False):
    image = QImage(width, height, QImage.Format_RGB888)
    image.fill(QColor('white'))
    painter = QPainter(image)
    painter.setFont(QFont('Microsoft YaHei', 10))
    cell = width if fixed else 120
    for row in range(8):
        for col in range((width + cell - 1) // cell):
            rect = QRect(col*cell, row*40, cell, 40)
            painter.fillRect(rect, QColor('#fafafa' if row == 0 else '#ffffff'))
            painter.setPen(QColor('#ececec')); painter.drawRect(rect)
            painter.setPen(QColor('#555555'))
            text = (f'学校 {row}' if fixed else
                    (f'学科 {col+1} 扫描量' if row == 0 else str((col*193+row*71) % 19999)))
            painter.drawText(rect.adjusted(10, 0, -5, 0), Qt.AlignVCenter, text)
            # Sandboxed/offscreen Qt can render every glyph as the same box.
            # Keep explicit column IDs distinguishable even without system fonts.
            if not fixed:
                for bit in range(7):
                    if ((col+1)*13+row*7) & (1<<bit):
                        painter.fillRect(col*cell+100+bit*2,row*40+16,1,8,QColor('#555555'))
    painter.end()
    return np.frombuffer(image.bits(), np.uint8).reshape(height, image.bytesPerLine())[:, :width*3].reshape(height, width, 3).copy()


class TableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = table_image(2400)
        cls.frozen = table_image(180, fixed=True)
        cls.right = table_image(60, fixed=True)

    def frame(self, x, hover=None, shadow=False):
        frame = np.hstack((self.frozen, self.page[:, x:x+600], self.right))
        if shadow:
            # Fixed column shadow overlays the moving content on both sides.
            for i in range(8):
                frame[:, 180+i] = frame[:, 180+i] * (0.84 + i*.02)
                frame[:, 779-i] = frame[:, 779-i] * (0.84 + i*.02)
        if hover is not None:
            strip = frame[hover:hover+38].astype(np.float32)
            frame[hover:hover+38] = strip * .9 + np.array([190, 232, 253]) * .1
        return frame

    def test_hover_repainting_does_not_disable_frozen_column_stitching(self):
        state = ls._HorizontalState(self.frame(0, hover=81))
        for x, hover in ((73, 161), (146, 201), (219, 121)):
            self.assertEqual(state.feed(self.frame(x, hover))[0], 73)
        expected = np.hstack((self.frozen, self.page[:, :819], self.right))
        self.assertEqual(state.long.shape, expected.shape)
        # Untouched rows must remain pixel exact across every seam.
        np.testing.assert_array_equal(state.long[2:75], expected[2:75])

    def test_fixed_column_shadows_do_not_enter_the_registration(self):
        state = ls._HorizontalState(self.frame(0, shadow=True))
        for x in (73, 146, 219):
            self.assertEqual(state.feed(self.frame(x, shadow=True))[0], 73)
        self.assertEqual(state.long.shape[1], 1059)
        # Left shadow is retained once; no shadow stripe may be appended inside.
        np.testing.assert_array_equal(state.long[:, 200:980], self.page[:, 20:800])

    def test_non_scrolling_controls_above_table_do_not_prevent_matching(self):
        top = table_image(840, 80)
        def frame(x): return np.vstack((top, self.frame(x)))
        state = ls._HorizontalState(frame(0))
        for x in (73, 146):
            self.assertEqual(state.feed(frame(x))[0], 73)
        np.testing.assert_array_equal(state.long[80:320, 200:900], self.page[:240, 20:720])
        np.testing.assert_array_equal(state.long[:80, :840], top)
        background = np.median(top,axis=1).astype(np.uint8)
        np.testing.assert_array_equal(state.long[:80,840:],np.repeat(background[:,None,:],146,axis=1))

    def test_hover_without_scrolling_never_appends_columns(self):
        state = ls._HorizontalState(self.frame(0, hover=81))
        before = state.long.copy()
        self.assertLessEqual(state.feed(self.frame(0, hover=161))[0], 0)
        np.testing.assert_array_equal(state.long,before)

    def test_table_backtracking_never_invents_forward_progress(self):
        state = ls._HorizontalState(self.frame(146, hover=81))
        self.assertLessEqual(state.feed(self.frame(73, hover=161))[0], 0)
        self.assertEqual(state.long.shape[1],840)

    def test_vertical_scroll_is_not_horizontal_progress(self):
        state = ls._HorizontalState(self.frame(0, hover=81))
        frame = np.roll(self.frame(0, hover=161),40,axis=0)
        self.assertLessEqual(state.feed(frame)[0],0)
        self.assertEqual(state.long.shape[1],840)

    def test_double_dpi_shadow_does_not_become_an_internal_stripe(self):
        def frame(x):return self.frame(x,shadow=True).repeat(2,axis=0).repeat(2,axis=1)
        state = ls._HorizontalState(frame(0))
        for x in (73,146,219):self.assertEqual(state.feed(frame(x))[0],146)
        expected = self.page[:,20:800].repeat(2,axis=0).repeat(2,axis=1)
        np.testing.assert_array_equal(state.long[:,400:1960],expected)

    def test_several_column_boundaries_with_hover_keep_progress(self):
        state = ls._HorizontalState(self.frame(0,hover=81,shadow=True))
        for index,x in enumerate((173,346,519),1):
            self.assertEqual(state.feed(self.frame(x,81+index*40,True))[0],173)

    def test_backtracking_beyond_viewport_does_not_fall_back_to_grid_match(self):
        state = ls._HorizontalState(self.frame(0,hover=81,shadow=True))
        for index,x in enumerate((313,626,939),1):
            self.assertEqual(state.feed(self.frame(x,81+index*40,True))[0],313)
        before = state.long.copy()
        self.assertLessEqual(state.feed(self.frame(313,201,True))[0],0)
        self.assertLessEqual(state.feed(self.frame(939,121,True))[0],0)
        np.testing.assert_array_equal(state.long,before)

    def test_css_background_hover_does_not_turn_frozen_columns_into_motion(self):
        def frame(x,hover):
            image=self.frame(x,shadow=True)
            strip=image[hover:hover+38];mask=strip.mean(axis=2)>220
            strip[mask]=[230,247,255]
            return image
        state=ls._HorizontalState(frame(0,81))
        for x,y in ((73,161),(146,201),(219,121)):
            self.assertEqual(state.feed(frame(x,y))[0],73)

    def test_cursor_change_does_not_invent_one_pixel_progress(self):
        first=self.frame(0);changed=first.copy();changed[120:143,65:86]=15
        state=ls._HorizontalState(first)
        self.assertLessEqual(state.feed(changed)[0],0)
        self.assertEqual(state.long.shape,first.shape)

    def test_async_table_header_recovers_from_a_mixed_reference(self):
        def frame(body,header):
            image=self.frame(body,shadow=True)
            image[:40]=self.frame(header,shadow=True)[:40]
            return image
        state=ls._HorizontalState(frame(0,0))
        self.assertEqual(state.feed(frame(80,80))[0],80)
        self.assertGreaterEqual(state.feed(frame(84,80))[0],0)
        position=state.long.shape[1]-840
        self.assertEqual(state.feed(frame(186,186))[0],186-position)
        self.assertEqual(state.long.shape[1],1026)
        np.testing.assert_array_equal(state.long[:,200:900],self.page[:,20:720])

    def test_header_catching_up_does_not_append_duplicate_columns(self):
        def frame(body,header):
            image=self.frame(body,shadow=True)
            image[:40]=self.frame(header,shadow=True)[:40]
            return image
        state=ls._HorizontalState(frame(0,0))
        for body,header in ((80,80),(84,80),(186,186),(200,196)):
            state.feed(frame(body,header))
        before=state.long.shape[1]
        self.assertEqual(state.feed(frame(200,200))[0],0)
        self.assertEqual(state.long.shape[1],before)
        self.assertEqual(before,1040)
        np.testing.assert_array_equal(state.long[:,200:960],self.page[:,20:780])


if __name__ == '__main__':
    unittest.main()
