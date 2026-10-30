"""Local task MCP connection details; copying never enables the service."""
import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget)

import common as C
from polished_controls import SurfaceButton, crisp_window_shadow
from widgets import CardDialog


def mcp_configuration(executable=None, frozen=None, main_path=None):
    executable = Path(executable or sys.executable).resolve()
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    arguments = ['--mcp']
    if not frozen:
        console = executable.with_name('python.exe')
        if executable.name.lower() == 'pythonw.exe' and console.is_file():
            executable = console
        arguments.insert(0, str(Path(main_path or Path(__file__).resolve().parents[1]/'main.pyw').resolve()))
    return {'mcpServers': {'jianjie-local-tasks': {
        'type': 'stdio', 'command': str(executable), 'args': arguments, 'disabled': False}}}


def agent_setup_text(configuration, enabled):
    readiness = ('简截的本地 MCP 已开启。' if enabled else
                 '如简截的本地 MCP 尚未开启，请提醒我在简截托盘菜单勾选“本地 MCP（允许 Agent 管理任务）”。')
    return ('请将下面的“简截任务”stdio MCP 服务接入当前 Agent，按当前 Agent 的配置格式添加；'
            '如果已有同名服务，请更新该服务，避免重复添加，并保留其他已有 MCP 服务。\n\n'
            '```json\n'+json.dumps(configuration, ensure_ascii=False, indent=2)+'\n```\n\n'
            +readiness+'接入后调用 jianjie_status 检查连接。\n'
            'Agent 可以先于简截启动。写入返回 delivery_status=queued 时，只告知“已排队，等待简截启动并开启本地 MCP”，'
            '不要报告已添加；之后会自动提交，使用 jianjie_queue 查询最终结果、取消未提交请求或查看冲突。'
            '只有 applied 才表示已写入清单。后台完成不会自动唤醒 Agent；下次交互时可查询结果。\n'
            '管理任务前先查询真实 ID 和 version；没有指定今天的新增任务默认放入待办清单。'
            '重试保留 request_id，冲突需先查最新版本，不可直接覆盖。')


class McpInfoDialog(CardDialog):
    def __init__(self, manager, parent=None):
        super().__init__('M', '本地 MCP', 'Agent 接入', on_top=False, parent=parent)
        crisp_window_shadow(self, self.body)
        self.manager = manager
        self.configuration = mcp_configuration()
        self._write_clipboard = QApplication.clipboard().setText
        area = (parent.screen() if parent else QApplication.primaryScreen()).availableGeometry()
        self.setFixedWidth(min(540, max(300, area.width()-32)))
        self.setMinimumHeight(min(340, max(280, area.height()-32)))
        self.resize(self.width(), min(520, area.height()-32))
        self.content_lay.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea{background:transparent;border:none;}'
                             'QScrollArea>QWidget>QWidget{background:transparent;}'
                             'QScrollBar:vertical{width:6px;background:transparent;}'
                             'QScrollBar::handle:vertical{background:#39465a;border-radius:3px;min-height:24px;}'
                             'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}'
                             'QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}')
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 4, 4, 4)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel('让 Agent 直接管理简截任务')
        title.setStyleSheet('font-size:14px;font-weight:600;color:#edf2fa;')
        heading.addWidget(title, 1)
        self.status = QLabel()
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setFixedSize(62, 26)
        heading.addWidget(self.status)
        layout.addLayout(heading)
        self.readiness = QLabel()
        self.readiness.setWordWrap(True)
        self.readiness.setStyleSheet('font-size:12px;color:#aab8ca;')
        layout.addWidget(self.readiness)
        caption = QLabel('STDIO 配置 · 自动使用当前程序路径')
        caption.setStyleSheet('font-size:11px;color:#8190a6;')
        layout.addWidget(caption)
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.code.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        font = QFont('Consolas')
        font.setPixelSize(12)
        self.code.setFont(font)
        self.code.setAccessibleName('MCP JSON 配置')
        self.code.setPlainText(json.dumps(self.configuration, ensure_ascii=False, indent=2))
        self.code.setMinimumHeight(188)
        self.code.setStyleSheet('QPlainTextEdit{background:#0b111b;color:#cbdaf0;border:none;'
                                'border-radius:8px;padding:10px;selection-background-color:#315f9c;}'
                                'QScrollBar:horizontal{height:6px;background:#101722;}'
                                'QScrollBar::handle:horizontal{background:#39465a;border-radius:3px;min-width:24px;}'
                                'QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0;}'
                                'QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal{background:transparent;}')
        layout.addWidget(self.code, 1)
        hint = QLabel('支持读取、新增、完成、转移与撤销。离线写入先排队，服务就绪后自动提交；冲突保留待处理。移动程序后，请重新复制配置。')
        hint.setWordWrap(True)
        hint.setStyleSheet('font-size:11px;color:#8190a6;')
        layout.addWidget(hint)
        scroll.setWidget(content)
        self.content_lay.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.copy_json = SurfaceButton('复制 JSON', pixels=12)
        self.copy_agent = SurfaceButton('复制给 Agent', variant='primary', pixels=12)
        for button in (self.copy_json, self.copy_agent):
            button.setFixedHeight(40)
        self.copy_json.setToolTip('粘贴到使用 mcpServers JSON 的 Agent 配置中')
        self.copy_agent.setToolTip('复制连接说明和配置，让 Agent 按自身格式完成接入')
        self.copy_json.clicked.connect(lambda: self.copy_configuration(False))
        self.copy_agent.clicked.connect(lambda: self.copy_configuration(True))
        actions.addWidget(self.copy_json, 1)
        actions.addWidget(self.copy_agent, 1)
        self.content_lay.addLayout(actions)
        self.feedback = QLabel('复制后可直接粘贴，无需填写端口或密钥。')
        self.feedback.setWordWrap(True)
        self.feedback.setStyleSheet('font-size:11px;color:#8190a6;')
        self.content_lay.addWidget(self.feedback)
        self.refresh_status()

    def refresh_status(self):
        enabled = bool(getattr(self.manager, 'agent_enabled', lambda: False)())
        self.status.setText('已开启' if enabled else '未开启')
        self.status.setStyleSheet('font-size:11px;border-radius:13px;'
                                  +('color:#8edbc1;background:#15342f;' if enabled else
                                    'color:#a7b2c4;background:#232d3b;'))
        self.readiness.setText('服务已开启。Agent 可以先启动；简截重新打开后会自动补交待处理请求。' if enabled else
                               'Agent 可以先连接并保存待提交请求。开启托盘菜单“本地 MCP（允许 Agent 管理任务）”后，才会写入任务清单。')

    def copy_configuration(self, for_agent):
        enabled = bool(getattr(self.manager, 'agent_enabled', lambda: False)())
        text = (agent_setup_text(self.configuration, enabled) if for_agent else
                json.dumps(self.configuration, ensure_ascii=False, indent=2))
        try:
            self._write_clipboard(text)
        except (OSError, RuntimeError):
            self.feedback.setText('复制失败，请选中上方配置后手动复制。')
            return
        self.feedback.setText('已复制，粘贴到 Agent 对话中即可。' if for_agent else
                              'JSON 已复制，粘贴到 Agent 的 MCP 配置中即可。')
