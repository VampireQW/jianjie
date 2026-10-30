import gc
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import weakref

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QColorDialog
import common as C
from longshot import LongShotBar, _ColumnState
from longshot_fill import ColumnFill, FillStyle, compose_columns, infer_background


def page(color, height=240, width=200, footer=None):
    image = np.full((height, width, 3), color, dtype=np.uint8)
    for y in range(20, height-20, 18):
        image[y:y+2, 25:width-28] = (42, 47, 58)
    image[:, -3:] = (110, 116, 128)
    if footer is not None:
        image[-28:] = footer
    return image


class FillAlgorithmTests(unittest.TestCase):
    def test_white_paper_and_dark_editor_fill_exact_own_background(self):
        for color in ((255, 255, 255), (248, 244, 230), (30, 34, 43), (0, 0, 0)):
            with self.subTest(color=color):
                short = page(color)
                long = page((225, 235, 248), 440)
                result = compose_columns(short, long)
                np.testing.assert_array_equal(result[:240, :200], short)
                np.testing.assert_array_equal(result[:, 200:], long)
                self.assertTrue(np.all(result[240:, :200] == color))
                self.assertIsNone(infer_background(short).edge)

    def test_fixed_toolbar_is_not_mistaken_for_page_background(self):
        image = page((255, 255, 255), footer=(28, 33, 44))
        inferred = infer_background(image)
        self.assertEqual(inferred.color, (255, 255, 255))
        self.assertEqual(infer_background(image, 28).color, (255, 255, 255))
        output = compose_columns(image, page((24, 28, 36), 360))
        np.testing.assert_array_equal(output[212:240, :200], image[212:])
        self.assertTrue(np.all(output[240:, :200] == 255))

    def test_low_noise_background_is_grouped_without_turning_into_average_gray(self):
        rng = np.random.default_rng(300)
        image = np.clip(np.full((240, 200, 3), (244, 239, 225), np.int16) +
                        rng.integers(-2, 3, (240, 200, 3)), 0, 255).astype(np.uint8)
        image[::15, 22:160] = 0
        style = infer_background(image)
        self.assertIsNone(style.edge)
        self.assertLessEqual(np.max(np.abs(np.array(style.color, int) - (244, 239, 225))), 2)

    def test_complex_picture_fades_only_new_pixels_and_keeps_memory_profile_small(self):
        rng = np.random.default_rng(300)
        image = rng.integers(20, 245, (240, 200, 3), dtype=np.uint8)
        style = infer_background(image)
        self.assertIsNotNone(style.edge)
        self.assertLessEqual(style.edge.shape[0], 64)
        original = image.copy()
        result = compose_columns(image, page((240, 240, 240), 500))
        np.testing.assert_array_equal(result[:240, :200], original)
        np.testing.assert_array_equal(image, original)
        self.assertTrue(np.all(result[-20:, :200] == style.color))
        self.assertTrue(result[240:, :200].any())

    def test_unequal_widths_and_right_short_column(self):
        left, right = page((250, 250, 250), 460, 80), page((35, 42, 52), 240, 320)
        result = compose_columns(left, right)
        self.assertEqual(result.shape, (460, 400, 3))
        np.testing.assert_array_equal(result[:, :80], left)
        np.testing.assert_array_equal(result[:240, 80:], right)
        self.assertTrue(np.all(result[240:, 80:] == (35, 42, 52)))

    def test_equal_height_does_not_analyze_or_change_pixels(self):
        left, right = page((255, 255, 255)), page((35, 42, 52))
        with patch('longshot_fill.infer_background', side_effect=AssertionError('Unnecessary analysis')):
            output = compose_columns(left, right)
        np.testing.assert_array_equal(output, np.hstack((left, right)))

    def test_manual_fill_changes_padding_only(self):
        left, right = page((255, 255, 255)), page((30, 34, 43), 400)
        output = compose_columns(left, right, (FillStyle((226, 234, 243)), None))
        np.testing.assert_array_equal(output[:240, :200], left)
        self.assertTrue(np.all(output[240:, :200] == (226, 234, 243)))

    def test_color_cache_stays_stable_but_accepts_clear_theme_change(self):
        model = ColumnFill()
        self.assertEqual(model.resolve(page((255, 255, 255))).color, (255, 255, 255))
        self.assertEqual(model.resolve(page((250, 250, 250), 260)).color, (255, 255, 255))
        self.assertEqual(model.resolve(page((28, 32, 42), 280)).color, (28, 32, 42))
        model.manual = (248, 244, 230)
        self.assertEqual(model.resolve(page((255, 255, 255))).color, model.manual)
        model.manual = None
        model.reset()
        self.assertEqual(model.resolve(page((255, 255, 255))).color, (255, 255, 255))

    def test_cache_does_not_retain_screenshot_arrays(self):
        model = ColumnFill()
        image = page((255, 255, 255))
        reference = weakref.ref(image)
        model.resolve(image)
        del image
        gc.collect()
        self.assertIsNone(reference())

    def test_tiny_arrays_are_supported(self):
        for height, width in ((1, 1), (2, 2), (3, 8)):
            short = np.full((height, width, 3), 245, np.uint8)
            result = compose_columns(short, np.full((height+3, width, 3), 60, np.uint8))
            self.assertTrue(np.all(result[height:, :width] == 245))


class FillToolbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patches = [patch('longshot.grab_array', return_value=np.hstack((page((255, 255, 255)), page((30, 34, 43))))),
                        patch('longshot.QCursor.setPos'), patch('longshot.C.force_foreground'),
                        patch('longshot.C.exclude_from_capture')]
        for item in self.patches:
            item.start()
        self.deliveries = []
        self.bar = LongShotBar(lambda *a, **k: self.deliveries.append(k['image']), lambda: None,
                               QRect(0, 0, 400, 240), self.app.primaryScreen(),
                               lambda arr, frames: self.deliveries.append(arr.copy()))
        self.bar.timer.stop()
        self.bar.bcols.click()
        self.bar._colL.long = page((255, 255, 255))
        self.bar._colR.long = page((30, 34, 43), 420)
        self.bar.long_arr = self.bar._compose()

    def tearDown(self):
        self.bar.cancel()
        self.bar.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()

    def test_palette_pauses_capture_and_restores_previous_timer_state(self):
        self.bar.timer.start()
        self.bar.open_fill_colors()
        self.assertFalse(self.bar.timer.isActive())
        self.assertTrue(self.bar._right_cancel._suspended)
        self.assertTrue(self.bar._fill_popup.isVisible())
        self.bar._fill_popup.hide()
        self.assertTrue(self.bar.timer.isActive())
        self.assertFalse(self.bar._right_cancel._suspended)

    def test_manual_and_restore_refresh_same_preview(self):
        self.bar.open_fill_colors()
        before = self.bar._colL.long.copy()
        self.bar.set_fill_color(0, (240, 228, 210))
        self.assertTrue(np.all(self.bar.long_arr[240:, :200] == (240, 228, 210)))
        self.assertTrue(self.bar._fill_popup.restore_buttons[0].isEnabled())
        self.assertIsNotNone(self.bar._preview._pm)
        self.bar._fill_popup.restore_buttons[0].click()
        self.assertTrue(np.all(self.bar.long_arr[240:, :200] == 255))
        self.assertFalse(self.bar._fill_popup.restore_buttons[0].isEnabled())
        np.testing.assert_array_equal(self.bar._colL.long, before)

    def test_screen_picker_cancel_keeps_capture_and_color(self):
        self.bar.timer.start()
        self.bar.open_fill_colors()
        with patch.object(QColorDialog, 'exec', return_value=QColorDialog.Rejected):
            self.bar.choose_fill_color(0)
        self.assertTrue(self.bar.timer.isActive())
        self.assertFalse(self.bar._right_cancel._suspended)
        self.assertIsNone(self.bar._padding[0].manual)

    def test_screen_picker_accept_and_editor_use_identical_padding(self):
        with patch.object(QColorDialog, 'exec', return_value=QColorDialog.Accepted), \
             patch.object(QColorDialog, 'selectedColor', return_value=QColor('#eef2f8')):
            self.bar.choose_fill_color(0)
        expected = self.bar.long_arr.copy()
        self.bar.edit()
        np.testing.assert_array_equal(self.deliveries[0], expected)
        self.assertTrue(np.all(expected[240:, :200] == (238, 242, 248)))

    def test_copy_image_pixels_equal_preview_without_disk_write(self):
        self.bar.set_fill_color(0, (244, 241, 235))
        expected = self.bar.long_arr.copy()
        with patch('longshot.C.save_image', side_effect=AssertionError('Copy must not save')):
            self.bar.finish(copy=True)
        image = self.deliveries[0].convertToFormat(QImage.Format_RGB888)
        array = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
        np.testing.assert_array_equal(array[:, :image.width()*3].reshape(image.height(), image.width(), 3), expected)

    def test_png_export_pixels_equal_preview(self):
        self.bar.set_fill_color(0, (250, 245, 230))
        expected = self.bar.long_arr.copy()
        with tempfile.TemporaryDirectory(prefix='JianjieFillExport-') as directory:
            with patch('longshot.pictures_dir', return_value=directory):
                self.bar.finish(copy=False)
            files = list(Path(directory).glob('*.png'))
            self.assertEqual(len(files), 1)
            image = QImage(str(files[0])).convertToFormat(QImage.Format_RGB888)
            data = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
            np.testing.assert_array_equal(data[:, :image.width()*3].reshape(expected.shape), expected)

    def test_switch_to_single_keeps_fill_and_hides_color_button(self):
        self.bar.set_fill_color(0, (238, 242, 248))
        expected = self.bar.long_arr.copy()
        self.bar.bcols.click()
        self.assertTrue(self.bar.bfill.isHidden())
        np.testing.assert_array_equal(self.bar.long_arr, expected)

    def test_right_click_while_palette_open_does_not_cancel_longshot(self):
        self.bar.open_fill_colors()
        control = self.bar._fill_popup.choices[0]
        event = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                            Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        self.assertFalse(self.bar._right_cancel.eventFilter(control, event))
        self.assertTrue(self.bar.isVisible())
        self.assertIsNotNone(self.bar._colL)

    def test_right_click_cancel_returns_after_palette_is_closed(self):
        self.bar.open_fill_colors()
        self.bar._fill_popup.hide()
        event = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                            Qt.RightButton, Qt.RightButton, Qt.NoModifier)
        self.assertTrue(self.bar._right_cancel.eventFilter(self.bar.bcols, event))
        self.assertFalse(self.bar.isVisible())
        self.assertIsNone(self.bar.long_arr)

    def test_escape_closes_palette_without_cancelling_screenshot(self):
        self.bar.timer.start()
        self.bar.open_fill_colors()
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        self.app.sendEvent(self.bar._fill_popup, event)
        self.assertFalse(self.bar._fill_popup.isVisible())
        self.assertTrue(self.bar.isVisible())
        self.assertTrue(self.bar.timer.isActive())

    def test_resuming_right_click_does_not_treat_held_button_as_new_press(self):
        cancel = self.bar._right_cancel
        cancel._live = True
        with patch.object(cancel, '_right_down', return_value=True):
            cancel.set_suspended(True)
            cancel.set_suspended(False)
            cancel._poll()
            self.assertTrue(self.bar.isVisible())
        cancel._timer.stop()


if __name__ == '__main__':
    unittest.main()
