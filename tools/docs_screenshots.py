"""Render current application widgets with isolated demo data for the GitHub README."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
STATE = ROOT / "build" / "docs-screenshots-data"
OUT.mkdir(parents=True, exist_ok=True)
STATE.mkdir(parents=True, exist_ok=True)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = str(STATE / "roaming")
os.environ["LOCALAPPDATA"] = str(STATE / "local")
sys.path.insert(0, str(ROOT))

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

import common as C
import widgets as W
from panel import Panel

app = QApplication([])
for name in ("msyh.ttc", "msyhbd.ttc"):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)
C.apply_ui_font(app)
SETTINGS = {**C.DEFAULTS, "theme": "dark", "toast_corner": "top_left"}
C.load_settings = lambda: dict(SETTINGS)
C.save_settings = lambda _: None
C.save_tasks = lambda _: None
C.storage_locations = lambda: ("D:\\简截\\截图", True, {
    "pictures": "D:\\简截\\截图", "data": "D:\\简截\\数据", "receive": "D:\\简截\\互传"})
# Offscreen documentation views have no desktop interaction or real capture source.
C.prepare_capture_window = lambda _: None
C.exclude_from_capture = lambda _: None
C.force_foreground = lambda _: None
C.snapshot_input_locale = lambda: None

FILES = []


def settle():
    for _ in range(4):
        app.processEvents()


def save_window(window, name, background="#111723", margin=14):
    window.show()
    settle()
    image = QImage(window.width() + margin * 2, window.height() + margin * 2, QImage.Format_ARGB32)
    image.fill(QColor(background))
    painter = QPainter(image)
    window.render(painter, QPoint(margin, margin))
    painter.end()
    image.save(str(OUT / name))
    FILES.append({"file": name, "width": image.width(), "height": image.height()})


def board(width=800, height=680):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#eef2f8"))
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRect(0, 0, width, 66)
    p.setFont(QFont("Microsoft YaHei UI", 18, QFont.Bold))
    p.setPen(QColor("#202b40"))
    p.drawText(36, 42, "项目工作台")
    p.setFont(QFont("Microsoft YaHei UI", 10))
    p.setPen(QColor("#6a7790"))
    p.drawText(530, 41, "概览   ·   资料   ·   任务")
    cards = [
        (36, "本周计划", ["整理项目资料", "准备产品演示", "收集使用反馈", "完善功能说明", "更新发布记录"]),
        (408, "讨论纪要", ["截图标注：让重点更清楚", "长图拼接：保留完整上下文", "资料互传：从分享菜单发送", "文件搜索：按关键词查找", "任务提醒：安排截止时间"]),
    ]
    for x, title, rows in cards:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(x, 96, 350, 445, 12, 12)
        p.setFont(QFont("Microsoft YaHei UI", 15, QFont.Bold))
        p.setPen(QColor("#263650"))
        p.drawText(x + 24, 140, title)
        for i, text in enumerate(rows):
            y = 180 + i * 66
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#e8f0fe" if i == 0 else "#f4f6fa"))
            p.drawRoundedRect(x + 18, y - 22, 314, 51, 7, 7)
            p.setFont(QFont("Microsoft YaHei UI", 11))
            p.setPen(QColor("#294b80" if i == 0 else "#556176"))
            p.drawText(x + 32, y + 9, text)
    p.setPen(QColor("#8792a4"))
    p.setFont(QFont("Microsoft YaHei UI", 10))
    p.drawText(36, 590, "把常用操作放在一起，继续手头的工作。")
    p.end()
    return image


main = Panel(dict(SETTINGS))
main.refresh_storage()
main.resize(680, 640)
save_window(main, "main-panel.png")
main.hide()

settings = W.AppSettingsDialog(SETTINGS, autostart=True)
save_window(settings, "settings.png")
settings.hide()

from sticky import TaskManager, TaskWidget
manager = TaskManager()
manager._loaded = True
manager.win.update(w=430, h=495)
manager.tasks = {
    str(i): dict(id=str(i), text=text, prio=prio, order=i, due_date=due, done=False)
    for i, (text, prio, due) in enumerate([
        ("整理本周项目资料", 3, "2026-10-16"),
        ("准备演示截图与操作说明，检查长截图和手机互传的使用流程", 2, "2026-10-17"),
        ("收集大家的使用反馈", 1, None),
        ("更新 GitHub 项目说明", 0, None),
    ])
}
tasks = TaskWidget(manager)
manager.widget = tasks
save_window(tasks, "tasks.png", background="#34444b")
tasks._save_t.stop()
manager._reminder_timer.stop()
tasks.hide()

import fsearch


class DocumentationSearch(fsearch.FileSearchWindow):
    def _post_show(self):
        pass


search = DocumentationSearch(auto_index=False, db=str(STATE / "search.db"))
search.resize(760, 480)
search._poll.stop()
search.edit.blockSignals(True)
search.edit.setText("项目")
search._query_tokens = ("项目",)
search._show_hits(0, [
    ("项目计划.docx", False, "D:\\工作资料\\项目计划.docx"),
    ("项目讨论纪要.md", False, "D:\\工作资料\\会议记录\\项目讨论纪要.md"),
    ("项目演示.pptx", False, "D:\\工作资料\\演示\\项目演示.pptx"),
    ("项目截图", True, "D:\\工作资料\\项目截图"),
    ("项目需求清单.xlsx", False, "D:\\工作资料\\项目需求清单.xlsx"),
], False)
search.meta_lbl.setText("共 5 项 · 上次索引 2026-10-11 09:00")
search.status.setText("实时监控中 · 新文件/改名已入索引")
save_window(search, "file-search.png")
search.close()

from capture import ShotOverlay
base = board()
overlay = ShotOverlay(QPixmap.fromImage(base), screen=app.primaryScreen())
overlay._color_track.stop()
overlay.showNormal()
overlay.resize(base.size())
overlay.anno = QPixmap(base.size())
overlay.anno.fill(Qt.transparent)
overlay.phase = "edit"
overlay.sel = QRect(30, 90, 734, 459)
overlay.stamps = [
    {"kind": "rect", "r": QRect(52, 151, 318, 60), "color": "#ef4444", "w": 3},
    {"kind": "arrow", "a": QPoint(425, 294), "b": QPoint(519, 258), "color": "#ef4444", "w": 4, "style": "solid"},
]
font = QFont("Microsoft YaHei UI", 15, QFont.Bold)
fm = QFontMetrics(font)
overlay.stamps.append({"kind": "text", "lines": ["重点：支持分栏拼接"], "color": "#ef4444", "fs": 15,
                       "asc": fm.ascent(), "lh": fm.lineSpacing(), "r": QRect(427, 485, 310, fm.lineSpacing() + 2)})
overlay._rerender_anno()
overlay.show_toolbar()
overlay.cur_tool = "arrow"
for key, button in overlay.tool_btns.items():
    button.setChecked(key == "arrow")
overlay.props_pop.hide()
save_window(overlay, "screenshot-editor.png", margin=0)
overlay.close_silent()

import longshot
rgba = base.convertToFormat(QImage.Format_RGBA8888)
rgb = np.frombuffer(rgba.bits(), dtype=np.uint8).reshape(rgba.height(), rgba.width(), 4)[:, :, :3].copy()
region = QRect(36, 96, 722, 445)
selection = rgb[96:541, 36:758].copy()


class DocumentationLongShot(longshot.LongShotBar):
    def start(self):
        self.long_arr = selection.copy()
        self._prev = self.long_arr.copy()
        self.frames = 1
        self._preview.set_arr(self.long_arr)
        self.b_save.setEnabled(True)
        self.b_fin.setEnabled(True)
        self.update_stat()


long = DocumentationLongShot(lambda *_: None, lambda: None, region, app.primaryScreen())
long.bcols.setChecked(True)
long.toggle_dual()
long.msg.setText("等待滚动…")
long._preview.hide()
long.dot.set_active(False)
long.adjustSize()
long.move(max(6, 758 - long.width()), 554)
settle()
image = QImage(base)
p = QPainter(image)
for widget in (long._halo, long._divider, long):
    if widget is not None:
        widget.render(p, widget.pos())
p.end()
image.save(str(OUT / "long-screenshot.png"))
FILES.append({"file": "long-screenshot.png", "width": image.width(), "height": image.height()})
long.timer.stop()
long.close()

from phone_exchange import ExchangeStore
store = ExchangeStore(str(STATE))
store.add_text("项目讨论纪要已整理好，请查收。", sender="phone")
store.add_text("这是一条从电脑发送到手机的消息。")
config = dict(enabled=True, url="http://192.168.1.100:5034", user="jianjie",
              password="demo-only", addresses=["192.168.1.100"], message="",
              android_url="http://192.168.1.100:5034/android#token=demo-readme-only")
phone = W.PhoneReceiveDialog(config, lambda enabled: {**config, "enabled": enabled},
                             lambda *_: (True, config), lambda: None,
                             {"store": store, "send": lambda **_: (True, "内容已准备发送")})
phone.exchange_pane.edit.setPlainText("把项目说明发到手机，随时查看。")
save_window(phone, "phone-transfer.png")
phone._select_phone_platform(0)
phone._scroll_phone_setup()
save_window(phone, "iphone-shortcut.png")
phone.close()

(STATE / "manifest.json").write_text(json.dumps({"build": C.APP_BUILD_ID, "demo_data": True,
                                                  "source": "Current PySide6 application widgets",
                                                  "screenshots": FILES}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(FILES, ensure_ascii=False))
