import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QMenu

import beautify
import capture
import longedit
import screenshot_history as history


class AnnotationTextBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def shot(self, scale=1):
        screen = self.app.primaryScreen()
        base = QPixmap(screen.geometry().size() * scale)
        base.fill(QColor('#38516b'))
        overlay = capture.ShotOverlay(base, screen=screen)
        overlay.phase = 'edit'
        overlay.sel = QRect(10, 10, 470, 300)
        overlay.cur_tool = 'text'
        overlay.show()
        self.addCleanup(overlay.close)
        return overlay

    def test_default_transparent_and_colored_screenshot_export_at_double_scale(self):
        overlay = self.shot(scale=2)
        overlay.show_text_editor(QPoint(55, 42), '备注\nSecond line')
        overlay.commit_text_editor()
        text = overlay.stamps[0]
        color_probe = (text['r'].topLeft() - overlay.sel.topLeft() + QPoint(-2, 5)) * 2
        self.assertNotIn('bg', text)
        self.assertEqual(overlay.composite_crop().toImage().pixelColor(color_probe).name(), '#38516b')
        overlay.sel_stamp = ('shape', 0)
        overlay.set_text_background('#fde68a')
        output = overlay.composite_crop().toImage()
        self.assertEqual(output.pixelColor(color_probe).name(), '#fde68a')
        corner = (capture._text_background_rect(text['r'], text['fs']).topLeft() - overlay.sel.topLeft()) * 2
        self.assertEqual(output.pixelColor(corner).name(), '#38516b')
        self.assertEqual(text['color'], '#ffffff')
        self.assertEqual(overlay._hit_text(text['r'].topLeft() + QPoint(-2, 5)), 0)
        overlay.undo()
        self.assertNotIn('bg', overlay.stamps[0])

    def test_menu_updates_live_editor_without_committing_and_can_remove_background(self):
        overlay = self.shot()
        overlay.show_toolbar()
        overlay.activateWindow()
        self.app.processEvents()
        props = overlay.props_pop
        overlay.show_text_editor(QPoint(55, 42), '备注\nSecond line')
        editor = overlay.text_editor

        def pick(label):
            clicked = []

            def choose():
                menu = self.app.activePopupWidget()
                if isinstance(menu, QMenu):
                    action = next((a for a in menu.actions() if a.text() == label), None)
                    if action is not None:
                        clicked.append(label)
                        menu.setActiveAction(action)
                        QTest.keyClick(menu, Qt.Key_Return)
                    else:
                        menu.close()

            QTimer.singleShot(0, choose)
            QTest.mouseClick(props._btn_text_background, Qt.LeftButton)
            self.assertEqual(clicked, [label])

        for color, label in zip(capture.COLORS, ('红色', '橙色', '绿色', '蓝色', '白色', '黑色')):
            pick(label)
            self.assertEqual(editor.background_color, color)
            foreground = '#111111' if color == '#ffffff' else '#ffffff'
            self.assertEqual(editor._color, foreground)
            self.assertTrue(props._swatches[foreground].isChecked())
        pick('橙色')
        self.assertIs(overlay.text_editor, editor)
        self.assertEqual(editor.toPlainText(), '备注\nSecond line')
        self.assertEqual(overlay.stamps, [])
        self.app.processEvents()
        point = editor.pos() + QPoint(-2, 5)
        self.assertEqual(overlay.grab().toImage().pixelColor(point).name(), '#f59e0b')

        def accept_custom_color():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, QColorDialog):
                dialog.setCurrentColor(QColor('#b9f7ec'))
                dialog.accept()

        QTimer.singleShot(100, accept_custom_color)
        pick('自定义颜色…')
        self.assertIs(overlay.text_editor, editor)
        self.assertEqual(editor.background_color, '#b9f7ec')
        self.assertEqual(editor._color, '#ffffff')
        pick('无底色')
        self.assertIs(overlay.text_editor, editor)
        self.assertTrue(editor.background_rect.isEmpty())
        overlay.commit_text_editor()
        self.assertNotIn('bg', overlay.stamps[0])

    def test_history_reopen_and_beautify_keep_background_on_the_same_text(self):
        overlay = self.shot()
        overlay.set_text_background('#bfdbfe')
        overlay.show_text_editor(QPoint(55, 42), '备注\nSecond line')
        document = history.from_overlay(overlay)
        document['stamps'] = history.decode(history.encode(document['stamps']))
        window = longedit.LongEditor(document['base'], document=document, beauty=True)
        self.addCleanup(window.close)
        window.set_tool('text')
        canvas = window.canvas
        self.assertIsNone(canvas.cur_text_background)
        stamp = canvas.stamps[0]
        top = stamp['r'].topLeft()
        canvas.show_text_editor(top, preset='备注\nEdited', idx=0, fs=stamp['fs'], color=stamp['color'])
        self.assertEqual(canvas.text_editor.background_color, '#bfdbfe')
        canvas.commit_text_editor()
        settings = window.beauty_settings
        _, image_rect = beautify.geometry(canvas._img_w, canvas._img_h, settings)
        stamp = canvas.stamps[0]
        point = image_rect.topLeft().toPoint() + stamp['r'].topLeft() + QPoint(-2, 5)
        self.assertEqual(canvas.render_final(beauty=settings).pixelColor(point).name(), '#bfdbfe')
        canvas.sel_stamp = ('shape', 0)
        canvas.set_text_background(None)
        self.assertNotEqual(canvas.render_final(beauty=settings).pixelColor(point).name(), '#bfdbfe')
        canvas.undo()
        self.assertEqual(canvas.render_final(beauty=settings).pixelColor(point).name(), '#bfdbfe')

    def test_new_text_and_selected_text_share_contrast_and_undo_together(self):
        overlay = self.shot()
        overlay.set_text_background('#ffffff')
        overlay.show_text_editor(QPoint(55, 42), '白底文字')
        self.assertEqual(overlay.text_editor._color, '#111111')
        overlay.commit_text_editor()
        stamp = overlay.stamps[0]
        self.assertEqual((stamp['bg'], stamp['color']), ('#ffffff', '#111111'))
        overlay.sel_stamp = ('shape', 0)
        overlay.set_text_background('#3b82f6')
        self.assertEqual((stamp['bg'], stamp['color']), ('#3b82f6', '#ffffff'))
        overlay.undo()
        stamp = overlay.stamps[0]
        self.assertEqual((stamp['bg'], stamp['color']), ('#ffffff', '#111111'))
        overlay._reedit_text(0)
        overlay.set_text_background('#ef4444')
        overlay.cancel_text_editor()
        self.assertEqual((stamp['bg'], stamp['color']), ('#ffffff', '#111111'))

        image = overlay.composite_crop().toImage()
        window = longedit.LongEditor(image, beauty=True)
        self.addCleanup(window.close)
        window.set_tool('text')
        canvas = window.canvas
        canvas.set_text_background('#ffffff')
        canvas.show_text_editor(QPoint(55, 42), '白底文字')
        self.assertEqual(canvas.text_editor._color, '#111111')
        canvas.set_color('#ef4444')
        self.assertEqual(canvas.text_editor._color, '#ef4444')
        canvas.set_text_background('#22c55e')
        self.assertEqual(canvas.text_editor._color, '#ffffff')
        canvas.commit_text_editor()
        self.assertEqual(canvas.stamps[0]['color'], '#ffffff')


if __name__ == '__main__':
    unittest.main()
