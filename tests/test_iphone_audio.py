import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton
from iphone_mirror import MirrorDialog, MirrorSession
from mirror_audio import AudioOutput, audio_sink, output_id, resolve_output

APP = QApplication.instance() or QApplication([])
SPEAKER = AudioOutput('speaker-id', '扬声器 (Realtek Audio)', True)
HEADPHONES = AudioOutput('headphones-id', '蓝牙耳机')


def until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return True
        time.sleep(.005)
    return False


class AudioOutputTests(unittest.TestCase):
    def setUp(self):
        # The OS endpoint inventory is external; the menu, state, persistence and
        # receiver command stay real. No production settings are changed.
        self.catalog = patch('iphone_mirror.enumerate_outputs', return_value=[SPEAKER, HEADPHONES])
        self.catalog.start()
        self.addCleanup(self.catalog.stop)

    def dialog(self, settings=None):
        dialog = MirrorDialog(MirrorSession(), settings or {})
        self.addCleanup(dialog.close)
        return dialog

    def test_output_can_be_selected_without_muting_phone_audio(self):
        dialog = self.dialog()
        button = dialog.findChild(QPushButton, 'mirrorAudioOutput')
        self.assertIsNotNone(button, 'The mirroring page must expose the computer playback device')
        self.assertTrue(button.isEnabled())
        self.assertTrue(dialog.audio.isChecked())

    def test_menu_selects_distinct_endpoint_even_with_duplicate_names(self):
        dialog = self.dialog()
        dialog._audio_outputs = [SPEAKER, AudioOutput('other-id', SPEAKER.name)]
        dialog._audio_scanned = True
        dialog._audio_scanning = True  # No concurrent OS refresh in this menu fixture.
        dialog.choose_audio_output()
        action = next(item for item in dialog._audio_menu.actions()
                      if item.data() == ('other-id', SPEAKER.name))
        action.trigger()
        dialog._audio_menu.hide()
        self.assertEqual(dialog._audio_output_id, 'other-id')
        self.assertTrue(dialog._preferences_pending)
        self.assertEqual(dialog._audio_output_name, SPEAKER.name)

    def test_selection_is_saved_and_used_for_next_connection(self):
        dialog = self.dialog()
        dialog._select_audio_output(HEADPHONES.id, HEADPHONES.name)
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder, \
                patch('iphone_mirror.C.data_dir', return_value=folder), \
                patch.object(dialog.session, 'start'):
            dialog.begin()
            data = json.loads((Path(folder)/'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(data['mirror_audio_output'], 'headphones-id')
        self.assertEqual(data['mirror_audio_output_name'], '蓝牙耳机')
        restored = self.dialog(data)
        self.assertEqual(restored._audio_output_id, 'headphones-id')
        self.assertIn('蓝牙耳机', restored.audio_output.toolTip())

    def test_disconnected_device_keeps_choice_and_explains_default_fallback(self):
        dialog = self.dialog({'mirror_audio_output': HEADPHONES.id,
                              'mirror_audio_output_name': HEADPHONES.name})
        dialog._audio_devices_ready([SPEAKER], '')
        self.assertEqual(dialog._audio_output_id, 'headphones-id')
        self.assertIn('未连接', dialog.audio_output.toolTip())
        self.assertIn('系统默认', dialog.audio_output.toolTip())
        dialog._audio_devices_ready([SPEAKER, HEADPHONES], '')
        self.assertNotIn('未连接', dialog.audio_output.toolTip())

    def test_default_and_muting_do_not_erase_preferred_device(self):
        dialog = self.dialog()
        dialog._select_audio_output(HEADPHONES.id, HEADPHONES.name)
        dialog.audio.setChecked(False)
        self.assertFalse(dialog.audio_output.isEnabled())
        self.assertEqual(dialog._audio_output_id, 'headphones-id')
        dialog.audio.setChecked(True)
        self.assertTrue(dialog.audio_output.isEnabled())
        dialog._select_audio_output('', '')
        self.assertEqual(dialog._audio_output_id, '')
        self.assertIn('跟随系统默认', dialog.audio_output.toolTip())

    def test_streaming_cannot_restart_receiver_due_to_device_selection(self):
        dialog = self.dialog()
        dialog.session.state = 'streaming'
        dialog.refresh('streaming', '正在投屏')
        self.assertFalse(dialog.audio_output.isEnabled())
        dialog._select_audio_output(HEADPHONES.id, HEADPHONES.name)
        self.assertEqual(dialog._audio_output_id, '')
        self.assertFalse(dialog._preferences_pending)
        dialog.session.state = 'ready'
        dialog.refresh('ready', '等待连接')
        self.assertTrue(dialog.audio_output.isEnabled())

    def test_endpoint_discovery_does_not_block_qt_event_loop(self):
        dialog = self.dialog()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow_devices():
            entered.set()
            release.wait(2)
            return [HEADPHONES]
        with patch('iphone_mirror.enumerate_outputs', side_effect=slow_devices):
            dialog.load_audio_outputs()
            self.assertTrue(entered.wait(1))
            ticks = []
            QTimer.singleShot(0, lambda: ticks.append(True))
            self.assertTrue(until(lambda: bool(ticks)))
            self.assertTrue(dialog._audio_scanning)
            release.set()
            self.assertTrue(until(lambda: not dialog._audio_scanning))
        self.assertEqual(dialog._audio_outputs, [HEADPHONES])

    def test_read_error_keeps_default_available(self):
        dialog = self.dialog()
        dialog._audio_devices_ready([], 'Audio query unavailable')
        self.assertTrue(dialog.audio_output.isEnabled())
        dialog._audio_scanning = True
        dialog.choose_audio_output()
        action = dialog._audio_menu.actions()[0]
        self.assertEqual(action.data(), ('', ''))
        self.assertTrue(action.isEnabled())
        dialog._audio_menu.hide()

    def test_very_long_name_cannot_expand_window(self):
        dialog = self.dialog()
        dialog.show()
        APP.processEvents()
        width = dialog.width()
        dialog._select_audio_output('long-id', '扬声器'*200)
        APP.processEvents()
        self.assertEqual(dialog.width(), width)
        self.assertLess(dialog.audio_output.fontMetrics().horizontalAdvance(dialog.audio_output.text()),
                        dialog.audio_output.width()-25)

    def test_sink_uses_render_endpoint_or_default_and_mute_is_explicit(self):
        self.assertEqual(audio_sink(True), 'autoaudiosink')
        self.assertEqual(audio_sink(True, 'headphones-id'), 'wasapi2sink device="headphones-id"')
        self.assertEqual(audio_sink(False, 'headphones-id'), '0')
        self.assertEqual(audio_sink(True, 'a"!fakesink name=escaped'),
                         'wasapi2sink device="a\\"!fakesink name=escaped"')
        self.assertEqual(audio_sink(True, 'a\\b'), 'wasapi2sink device="a\\\\b"')

    def test_missing_and_case_changed_endpoints_resolve_without_losing_intent(self):
        self.assertEqual(resolve_output('HEADPHONES-ID', [SPEAKER, HEADPHONES]), ('headphones-id', False))
        self.assertEqual(resolve_output('headphones-id', [SPEAKER]), ('', True))
        self.assertEqual(resolve_output('', [SPEAKER]), ('', False))
        for invalid in (None, 123, {'id':'speaker-id'}, 'x\n!fakesink', 'a'*2049):
            self.assertEqual(output_id(invalid), '')

    @unittest.skipUnless(os.name == 'nt', 'Receiver process setup uses Windows events')
    def test_actual_receiver_command_carries_chosen_device_and_mute(self):
        import iphone_mirror
        for enabled, selected, expected in ((True, 'headphones-id', 'wasapi2sink device="headphones-id"'),
                                             (True, '', 'autoaudiosink'), (False, 'headphones-id', '0')):
            with self.subTest(enabled=enabled, selected=selected), tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
                session = MirrorSession()
                session._options = {'name':'Synthetic receiver', 'audio':enabled, 'quality':'1080p',
                                    'fps':60, 'low_latency':True, 'audio_output':selected}
                session.audio_output_info = {'device':selected}
                commands = []
                def no_process(command, **kwargs):
                    commands.append(command)
                    raise OSError('External receiver intentionally not launched by regression')
                with patch('iphone_mirror.subprocess.Popen', side_effect=no_process), \
                        patch('iphone_mirror.component_dir', return_value=Path(folder)), \
                        patch('iphone_mirror.record_failure'):
                    session._launch(Path(folder), 0)
                self.assertEqual(commands[0][commands[0].index('-as')+1], expected)
                self.assertIsNone(session._event)


class SessionPreparationTests(unittest.TestCase):
    def prepare(self, selected, outputs):
        import iphone_mirror
        class InlineThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as folder:
            base = Path(folder)
            runtime, bundle = base/'runtime', base/'bundle'
            runtime.mkdir();bundle.mkdir()
            (runtime/'test.dll').write_bytes(b'runtime')
            (runtime/'ready.json').write_text(json.dumps({'runtimeSha256':'a'*64, 'files':{'test.dll':7}}))
            for name in ('manifest.json', 'source.zip', 'LICENSE'):
                (bundle/name).write_bytes(b'test metadata')
            session = MirrorSession()
            with patch('iphone_mirror.component_dir', return_value=runtime), \
                    patch('iphone_mirror.bundled_dir', return_value=bundle), \
                    patch('iphone_mirror.metadata', return_value={'runtimeSha256':'a'*64}), \
                    patch('iphone_mirror.ensure_receiver'), \
                    patch('iphone_mirror.threading.Thread', InlineThread), \
                    patch('iphone_mirror.enumerate_outputs', return_value=outputs), \
                    patch.object(session, '_launch'):
                session.start('Test receiver', audio_output=selected)
                APP.processEvents()
            return session

    def test_preparation_passes_current_endpoint_to_launch(self):
        session = self.prepare('HEADPHONES-ID', [SPEAKER, HEADPHONES])
        self.assertEqual(session._options['audio_output'], 'HEADPHONES-ID')
        self.assertEqual(session.audio_output_info['device'], 'headphones-id')
        self.assertFalse(session.audio_output_info['fallback'])

    def test_preparation_missing_endpoint_uses_default_without_overwriting_preference(self):
        session = self.prepare('headphones-id', [SPEAKER])
        self.assertEqual(session.audio_output_info['device'], '')
        self.assertTrue(session.audio_output_info['fallback'])
        self.assertEqual(session._options['audio_output'], 'headphones-id')


if __name__ == '__main__':
    unittest.main(verbosity=2)
