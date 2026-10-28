import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from PySide6.QtCore import QRect,Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QLabel,QToolButton
from iphone_mirror import MirrorDialog,MirrorSession,_PreparationFailure
from polished_controls import CrispLineEdit
from runtime_diagnostics import component_failure,control_failure,record_failure

APP=QApplication.instance() or QApplication([])


class ProductTests(unittest.TestCase):
    def test_component_io_errors_never_become_error_codes_or_paths_in_ui(self):
        error=OSError(22,'Invalid argument',r'C:\Users\example\AppData\Roaming\jianjie\mirroring\airplay-1\receiver.exe')
        message,security=component_failure(error)
        self.assertTrue(security)
        self.assertIn('安全防护',message)
        for raw in ('Errno','Invalid argument','C:\\','receiver.exe'):
            self.assertNotIn(raw,message)
        error=OSError('blocked');error.winerror=225
        self.assertTrue(component_failure(error)[1])

    def test_prepare_failure_keeps_recovery_state_and_safe_copy(self):
        session=MirrorSession();session._cancel=threading.Event()
        failure=_PreparationFailure(OSError(22,'Invalid argument','receiver.exe'))
        with patch.object(session,'_state') as state:
            session._prepared(0,failure)
        self.assertEqual(state.call_args.args[0],'error')
        self.assertNotIn('Errno',state.call_args.args[1])
        self.assertTrue(session.security_blocked)

    def test_disconnect_returns_to_waiting_without_runtime_diagnostics(self):
        session=MirrorSession();session.state='streaming'
        with patch.object(session,'_state') as state:
            session._output(0,'JJ_AIRPLAY_READY')
        self.assertEqual(state.call_args.args[0],'ready')
        self.assertEqual(session.video,{})

    def test_native_control_errors_are_localized(self):
        self.assertNotIn('0x',control_failure('HRESULT 0x80000001'))
        self.assertEqual(control_failure('蓝牙输入连接已中断。'),'蓝牙输入连接已中断。')

    def test_failure_records_are_bounded_and_local(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as temp,patch('runtime_diagnostics.C.data_dir',return_value=temp):
            path=Path(temp)/'mirroring/diagnostics.jsonl';path.parent.mkdir();path.write_bytes(b'x'*(100*1024))
            record_failure('prepare',OSError(22,'sample failure','receiver.exe'))
            self.assertLess(path.stat().st_size,4096)
            self.assertIn('sample failure',path.read_text(encoding='utf-8'))

    def test_control_guide_and_keyboard_access_are_present(self):
        class Screen:
            def availableGeometry(self):return QRect(0,0,1920,1080)
        with patch.object(MirrorDialog,'screen',lambda self:Screen()),patch.object(MirrorSession,'enable_control'):
            dialog=MirrorDialog(MirrorSession(),{})
            self.assertEqual(dialog.control_toggle.text(),'电脑控制')
            dialog.control_toggle.setChecked(True)
            self.assertEqual(dialog.guide_stack.currentIndex(),1)
            copy=' '.join(label.text() for label in dialog.guide_stack.findChildren(QLabel))
            for value in ('辅助功能','触控','辅助触控','设备 → 蓝牙设备','F8','Ctrl+Alt+M','Esc','圆形指针'):
                self.assertIn(value,copy)
            self.assertIsNone(dialog.body.graphicsEffect())
            self.assertIsNotNone(dialog._crisp_shadow.graphicsEffect())
            dialog.close()

    def test_crisp_editor_keeps_ime_and_clear_button(self):
        editor=CrispLineEdit();editor.resize(360,40);editor.setClearButtonEnabled(True)
        editor.show();editor.setFocus();APP.processEvents()
        event=QInputMethodEvent();event.setCommitString('任务清单')
        QApplication.sendEvent(editor,event)
        self.assertEqual(editor.text(),'任务清单')
        buttons=editor.findChildren(QToolButton)
        self.assertTrue(buttons)
        QTest.mouseClick(buttons[0],Qt.LeftButton)
        self.assertEqual(editor.text(),'')
        editor.close()

    def test_small_screen_uses_vertical_scroll_and_preserves_guides(self):
        class Screen:
            def availableGeometry(self):return QRect(0,0,640,480)
        with patch.object(MirrorDialog,'screen',lambda self:Screen()):
            dialog=MirrorDialog(MirrorSession(),{})
            dialog.show_guide(1);dialog.show();APP.processEvents()
            self.assertLessEqual(dialog.height(),456)
            self.assertLessEqual(dialog.width(),616)
            self.assertEqual(dialog.guide_stack.count(),2)
            dialog.close()


if __name__=='__main__':unittest.main(verbosity=2)
