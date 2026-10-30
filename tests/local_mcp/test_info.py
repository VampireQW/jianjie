import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
import common as C
from local_tasks.desktop import install
from local_tasks.info import McpInfoDialog, agent_setup_text, mcp_configuration


class ConfigurationTests(unittest.TestCase):
    def test_frozen_executable_uses_current_path_and_stdio_argument(self):
        path = str(Path('C:/Tools/简截 工作台/JianJie.exe').resolve())
        config = mcp_configuration(path, True)
        server = json.loads(json.dumps(config, ensure_ascii=False))['mcpServers']['jianjie-local-tasks']
        self.assertEqual(server['command'], path)
        self.assertEqual(server['args'], ['--mcp'])
        self.assertEqual(server['type'], 'stdio')
        self.assertFalse(server['disabled'])
        self.assertNotIn('token', server)
        self.assertNotIn('port', server)

    def test_source_launch_keeps_script_path_separate_from_command(self):
        config = mcp_configuration('C:/Python/python.exe', False, 'C:/源码/简截/main.pyw')
        server = config['mcpServers']['jianjie-local-tasks']
        self.assertEqual(server['args'], [str(Path('C:/源码/简截/main.pyw').resolve()), '--mcp'])
        self.assertTrue(server['command'].endswith('python.exe'))

    def test_agent_instructions_include_config_and_actual_readiness(self):
        config = mcp_configuration('C:/Tools/JianJie.exe', True)
        self.assertIn(json.dumps(config, ensure_ascii=False, indent=2), agent_setup_text(config, False))
        self.assertIn('托盘菜单', agent_setup_text(config, False))
        self.assertIn('本地 MCP 已开启', agent_setup_text(config, True))
        self.assertIn('保留其他已有 MCP 服务', agent_setup_text(config, True))


class InfoDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.Manager, cls.Widget = install()

    def setUp(self):
        self.patches = [patch.object(C, 'load_tasks', return_value={'tasks': [], 'win': {}}),
                        patch.object(C, 'save_tasks'), patch.object(C, 'force_foreground')]
        for item in self.patches:
            item.start()
        self.manager = self.Manager()
        self.window = self.Widget(self.manager)
        self.manager.widget = self.window

    def tearDown(self):
        self.manager._date_timer.stop()
        self.manager._reminder_timer.stop()
        self.window._save_t.stop()
        if self.window._mcp_info is not None:
            self.window._mcp_info.close()
        self.window.close()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        for item in reversed(self.patches):
            item.stop()

    def test_header_order_keeps_pin_minimize_and_close_adjacent(self):
        layout = self.window.head.layout()
        buttons = [self.window.mcp_button, self.window.btn_pin,
                   self.window.btn_minimize, self.window.btn_hide]
        positions = [layout.indexOf(button) for button in buttons]
        self.assertEqual(positions, list(range(positions[0], positions[0]+4)))

    def test_copy_buttons_copy_correct_payload_without_enabling_service(self):
        self.window.show_mcp_info()
        dialog = self.window._mcp_info
        copied = []
        dialog._write_clipboard = copied.append
        dialog.copy_json.click()
        self.assertEqual(json.loads(copied[-1]), dialog.configuration)
        dialog.copy_agent.click()
        self.assertIn('jianjie_status', copied[-1])
        self.assertIn('```json', copied[-1])
        self.assertIn('已复制', dialog.feedback.text())
        self.assertFalse(self.manager.agent_enabled())

    def test_status_refresh_and_reopening_reuse_same_dialog(self):
        self.window.show_mcp_info()
        dialog = self.window._mcp_info
        self.assertEqual(dialog.status.text(), '未开启')
        with patch.object(self.manager, 'agent_enabled', return_value=True):
            self.window.refresh_mcp_status()
            self.assertEqual(dialog.status.text(), '已开启')
            self.assertIn('已开启', self.window.mcp_button.toolTip())
        self.window.show_mcp_info()
        self.assertIs(self.window._mcp_info, dialog)

    def test_close_disposes_dialog_and_preserves_task_draft(self):
        self.window.inp.setPlainText('还没有提交的任务')
        self.window.show_mcp_info()
        self.window._mcp_info.close()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertIsNone(self.window._mcp_info)
        self.assertEqual(self.window.inp.toPlainText(), '还没有提交的任务')


if __name__ == '__main__':
    unittest.main()
