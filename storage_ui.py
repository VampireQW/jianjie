"""统一存储设置与启动迁移进度。"""
import os
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton

import common as C
import storage
from widgets import CardDialog


class StorageDialog(CardDialog):
    def __init__(self, settings, parent=None):
        super().__init__("存", "存储位置", parent=parent)
        self.setFixedWidth(600)
        self.paths = {}
        locations = (("save_dir", "截图、长截图、GIF 与录屏", C.output_dir(False),
                      "成品直接保存到所选文件夹"),
                     ("data_dir", "应用数据", C.data_dir(),
                      "任务清单、设置、快记与文件索引"),
                     ("receive_dir", "收到的互传文件", C.transfers_dir(False),
                      "图片和文件自动保留，关闭软件后仍可查看"))
        for key, title, path, description in locations:
            label = QLabel(title)
            label.setStyleSheet("font-size:13px;font-weight:600;margin-top:8px;")
            self.content_lay.addWidget(label)
            row = QHBoxLayout()
            field = QLineEdit(path)
            field.setReadOnly(True)
            field.setAccessibleName(title + "位置")
            field.setToolTip(path)
            field.setStyleSheet(f"background:rgba(255,255,255,10);color:{C.UI_TEXT};"
                               "border:none;border-radius:7px;padding:9px;font-size:12px;")
            row.addWidget(field, 1)
            open_button = QPushButton("打开")
            open_button.setCursor(Qt.PointingHandCursor)
            open_button.clicked.connect(lambda _=False, f=field: self.open_directory(f.text()))
            row.addWidget(open_button)
            choose = QPushButton("更改")
            choose.setCursor(Qt.PointingHandCursor)
            choose.clicked.connect(lambda _=False, f=field, t=title: self.choose_directory(f, t))
            row.addWidget(choose)
            self.content_lay.addLayout(row)
            note = QLabel(description)
            note.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
            self.content_lay.addWidget(note)
            self.paths[key] = field
        behavior = QLabel("Enter / 对号：复制到剪贴板　　下载：保存文件")
        behavior.setStyleSheet(f"color:{C.UI_TEXT2};font-size:12px;margin-top:12px;")
        self.content_lay.addWidget(behavior)
        note = QLabel("更改位置会自动迁移历史数据，并重新启动简截。")
        note.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;margin-top:4px;")
        self.content_lay.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#fb7185;font-size:12px;")
        self.error.hide()
        self.content_lay.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.clicked.connect(self.accept)
        for button in (cancel, save):
            button.setCursor(Qt.PointingHandCursor)
            row.addWidget(button)
        self.content_lay.addLayout(row)

    def choose_directory(self, field, title):
        path = QFileDialog.getExistingDirectory(self, "选择" + title + "的位置", field.text())
        if path:
            field.setText(path)
            field.setToolTip(path)

    def open_directory(self, path):
        try:
            if not os.path.isdir(path):
                raise OSError("该目录尚未创建；首次保存文件时会自动创建")
            os.startfile(path)
        except OSError as error:
            self.error.setText(str(error))
            self.error.show()

    def values(self):
        return {**{key: field.text() for key, field in self.paths.items()},
                "storage_layout": 2}


def migrate_on_startup():
    while True:
        try:
            if not storage.pending_change():
                settings = C.load_settings()
                changes = {}
                output_sources = []
                transfer_source = None
                if storage.needs_brand_migration():
                    directory = storage.bootstrap_dir()
                    os.makedirs(directory, exist_ok=True)
                    changes["data_dir"] = directory
                    # 旧默认数据目录内部的保存、接收目录跟随迁移，不留下失效路径。
                    for key, path in (("save_dir", C.output_dir(False)),
                                      ("receive_dir", C.transfers_dir(False))):
                        if storage.nested_paths(C.data_dir(), path):
                            relative = os.path.relpath(path, C.data_dir())
                            if relative == os.pardir or relative.startswith(os.pardir + os.sep):
                                continue
                            relocated = os.path.join(directory, relative)
                            os.makedirs(relocated, exist_ok=True)
                            changes[key] = relocated
                            if key == "save_dir":
                                output_sources = C.historical_output_dirs()
                            else:
                                transfer_source = path
                if settings.get("storage_layout") != 2:
                    # 升级时自动找回旧分类目录里的成品；不创建“新建”文件夹。
                    if "save_dir" not in changes:
                        changes["save_dir"] = C.output_dir()
                    changes["storage_layout"] = 2
                    output_sources = C.historical_output_dirs()
                    transfer_source = C.transfers_dir(False)
                if not changes:
                    storage.adopt_locator()
                    storage.retire_legacy_bootstrap()
                    C.refresh_data_paths()
                    return
                storage.prepare_change(settings, changes, output_sources, transfer_source)
        except OSError as error:
            _migration_failed(error)
            C.refresh_data_paths()
            return
        if not _apply_pending_with_dialog():
            return


def _migration_failed(error):
    failure = CardDialog("存", "存储迁移未完成")
    failure.setFixedWidth(460)
    text = QLabel("原文件仍保留在原位置。\n" + str(error))
    text.setWordWrap(True)
    failure.content_lay.addWidget(text)
    button = QPushButton("使用原位置")
    button.clicked.connect(failure.accept)
    failure.content_lay.addWidget(button)
    failure.exec()


def _apply_pending_with_dialog():
    dialog = CardDialog("存", "正在整理存储位置")
    dialog.setFixedWidth(420)
    message = QLabel("正在准备迁移…")
    message.setWordWrap(True)
    message.setStyleSheet(f"color:{C.UI_TEXT2};font-size:12px;padding:12px 0;")
    dialog.content_lay.addWidget(message)
    # 迁移期间不允许关闭窗口或重复启动业务。
    from PySide6.QtWidgets import QToolButton
    for button in dialog.head.findChildren(QToolButton):
        button.hide()
    dialog.reject = lambda: None
    result = {"done": False, "message": "正在准备迁移…", "error": None}

    def run():
        try:
            storage.apply_pending(lambda text: result.update(message=text))
        except Exception as error:
            result["error"] = str(error)
        finally:
            result["done"] = True

    timer = QTimer(dialog)
    timer.setInterval(120)

    def tick():
        message.setText(result["message"])
        if result["done"]:
            timer.stop()
            dialog.accept()

    timer.timeout.connect(tick)
    threading.Thread(target=run, name="JianJieStorageMigration", daemon=True).start()
    timer.start()
    dialog.exec()
    if result["error"]:
        # 保留原位置与迁移计划；下一次可重试，绝不展示一套空任务。
        _migration_failed(result["error"])
    C.refresh_data_paths()
    return result["error"] is None
