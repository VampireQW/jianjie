"""按需创建的互传工作区；只展示小图，不保留已发送的原图。"""
import os
import time
import shutil
import threading
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QPlainTextEdit, QFileDialog, QSizePolicy)
import common as C


class ContentEdit(QPlainTextEdit):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setPlaceholderText("粘贴文字，或粘贴 / 拖入图片、文件")
        self.setAccessibleName("发送到接收端的内容")
        self.setFixedHeight(64)

    def insertFromMimeData(self, source):
        if source.hasUrls():
            paths = [u.toLocalFile() for u in source.urls() if u.isLocalFile()]
            self.owner.select_paths(paths)
        elif source.hasImage():
            image = source.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            self.owner.select_image(image)
        elif self.owner.image is None and self.owner.path is None:
            super().insertFromMimeData(source)
        else:
            self.owner.feedback.setText("请先移除图片，再粘贴文字")

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)


class ExchangePane(QFrame):
    saved = Signal(str)
    def __init__(self, callbacks, parent=None, separate_records=False):
        super().__init__(parent)
        self.store, self.send_callback = callbacks["store"], callbacks["send"]
        self.image = self.path = self.preview = None
        self.paths = []
        self.generic_file = False
        self.file_sending = False
        self.saved.connect(self.feedback_message)
        self.revision = None
        self.setObjectName("phoneGroup")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        self.header = QHBoxLayout()
        title = QLabel("电脑 → 接收端")
        title.setObjectName("phoneSection")
        self.header.addWidget(title)
        self.header.addStretch()
        root.addLayout(self.header)
        self.edit = ContentEdit(self)
        self.edit.setStyleSheet(f"QPlainTextEdit{{background:{C.UI_BG_CARD};color:{C.UI_TEXT};border:1px solid {C.UI_TEXT3};border-radius:6px;padding:6px;font-family:'Microsoft YaHei UI';font-size:12px;}} QPlainTextEdit:focus{{border-color:{C.UI_BADGE_FROM};}}")
        self.edit.textChanged.connect(self.update_controls)
        root.addWidget(self.edit)
        self.image_row = QWidget(self)
        image_layout = QHBoxLayout(self.image_row)
        image_layout.setContentsMargins(0, 0, 0, 0)
        self.thumb = QLabel()
        self.thumb.setFixedSize(64, 48)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.image_name = QLabel()
        self.image_name.setWordWrap(True)
        image_layout.addWidget(self.thumb)
        image_layout.addWidget(self.image_name, 1)
        self.remove = QPushButton("移除")
        self.remove.clicked.connect(self.clear_image)
        image_layout.addWidget(self.remove)
        root.addWidget(self.image_row)
        self.image_row.hide()
        buttons = QHBoxLayout()
        self.choose = QPushButton("选择图片 / 文件")
        self.choose.clicked.connect(self.choose_image)
        self.paste = QPushButton("粘贴内容")
        self.paste.clicked.connect(lambda: self.edit.insertFromMimeData(QGuiApplication.clipboard().mimeData()))
        self.submit = QPushButton("发送到接收端")
        self.submit.setObjectName("phoneSave")
        self.submit.clicked.connect(self.submit_content)
        for button in (self.choose, self.paste, self.submit, self.remove):
            button.setAutoDefault(False)
            button.setFixedHeight(30)
            button.setCursor(Qt.PointingHandCursor)
        buttons.addWidget(self.choose)
        buttons.addWidget(self.paste)
        buttons.addStretch()
        buttons.addWidget(self.submit)
        root.addLayout(buttons)
        self.feedback = QLabel("在这里准备内容并发送，Mac / 手机打开配对链接后即可复制或下载。")
        self.feedback.setObjectName("phoneHint")
        self.feedback.setWordWrap(True)
        root.addWidget(self.feedback)
        self.record_panel = QFrame(parent if separate_records else self)
        self.record_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        record_root = QVBoxLayout(self.record_panel)
        record_root.setSpacing(8)
        record_root.setContentsMargins(*((12, 12, 12, 12) if separate_records else (0, 0, 0, 0)))
        if separate_records:
            self.record_panel.setObjectName("phoneGroup")
        else:
            root.addWidget(self.record_panel)
        record_header = QHBoxLayout()
        label = QLabel("最近互传")
        label.setObjectName("phoneStepTitle")
        record_header.addWidget(label)
        record_header.addStretch()
        ttl = QLabel("最近 5 条 · 30 分钟后自动清除")
        ttl.setObjectName("phoneHint")
        record_header.addWidget(ttl)
        self.clear_records = QPushButton("清空记录")
        self.clear_records.setObjectName("phoneCopy")
        self.clear_records.setAutoDefault(False)
        self.clear_records.setCursor(Qt.PointingHandCursor)
        self.clear_records.setToolTip("清空互传记录，已保存的文件会保留")
        self.clear_records.clicked.connect(self.clear_history)
        record_header.addWidget(self.clear_records)
        record_root.addLayout(record_header)
        self.records = QVBoxLayout()
        self.records.setSpacing(6)
        record_root.addLayout(self.records)
        self.refresh()
        self.update_controls()

    def update_controls(self):
        self.submit.setEnabled(not self.file_sending and bool(self.edit.toPlainText().strip() or self.image is not None or self.path))
        self.choose.setEnabled(not self.file_sending)
        self.paste.setEnabled(not self.file_sending)
        self.remove.setEnabled(not self.file_sending)

    def choose_image(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图片或文件（最多 5 个）", "", "所有文件 (*);;图片 (*.jpg *.jpeg *.png)")
        if paths:
            self.select_paths(paths)

    def select_paths(self, paths):
        if self.file_sending:
            return
        if len(paths) == 1:
            return self.select_path(paths[0])
        if not 1 < len(paths) <= 5:
            self.feedback.setText("一次最多选择 5 个本地文件")
            return
        from phone_exchange import MAX_FILE_BYTES
        try:
            sizes = [os.path.getsize(path) for path in paths]
            if any(not os.path.isfile(path) or not 0 < size <= MAX_FILE_BYTES for path, size in zip(paths, sizes)):
                raise ValueError("请选择非空文件，每个最大 2 GiB")
        except (ValueError, OSError) as error:
            self.feedback.setText(str(error))
            return
        self.clear_image()
        self.path, self.paths = paths[0], list(paths)
        self.generic_file = True
        self.show_image(f"{len(paths)} 个文件 · 合计 {sum(sizes)/1048576:.2f} MiB")
        self.feedback.setText("按顺序准备发送，每个最大 2 GiB；临时记录保留最近 5 条。")

    def select_path(self, path):
        from phone_receive import read_phone_image
        try:
            from phone_exchange import MAX_IMAGE_BYTES, MAX_FILE_BYTES
            size = os.path.getsize(path)
            if not os.path.isfile(path) or not 0 < size <= MAX_FILE_BYTES:
                raise ValueError("请选择非空文件，单个文件最大 2 GiB")
            with open(path, "rb") as stream:
                magic = stream.read(16)
            is_image = size <= MAX_IMAGE_BYTES and (magic.startswith(b"\xff\xd8\xff") or magic.startswith(b"\x89PNG\r\n\x1a\n"))
            preview = read_phone_image(path, QSize(160, 160)) if is_image else None
        except (ValueError, OSError) as error:
            self.feedback.setText(str(error))
            return
        self.clear_image()
        self.path, self.preview = path, preview
        self.generic_file = not is_image
        self.show_image(os.path.basename(path))

    def select_image(self, image):
        if not isinstance(image, QImage) or image.isNull():
            self.feedback.setText("剪贴板中的图片无法读取")
            return
        self.clear_image()
        self.image = image
        self.preview = image.scaled(160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.show_image(f"剪贴板图片 · {image.width()} × {image.height()}")

    def show_image(self, name):
        self.edit.clear()
        self.edit.setReadOnly(True)
        self.edit.hide()
        self.image_row.show()
        if self.preview is not None:
            self.thumb.setPixmap(QPixmap.fromImage(self.preview).scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.thumb.setText("文件")
        self.image_name.setText(name)
        self.feedback.setText("原文件发送；单个最大 2 GiB。" if self.generic_file else "原图发送，不缩小分辨率；单张最大 32 MiB。")
        self.update_controls()

    def clear_image(self):
        self.image = self.path = self.preview = None
        self.paths = []
        self.generic_file = False
        self.thumb.clear()
        self.image_row.hide()
        self.edit.setReadOnly(False)
        self.edit.show()
        self.update_controls()

    def submit_content(self):
        self.submit.setEnabled(False)
        if self.generic_file:
            self.file_sending = True
            ok, message = (self.send_callback(file_paths=self.paths) if self.paths else self.send_callback(file_path=self.path))
            if message != "正在准备文件…":
                self.file_sending = False
        elif self.image is not None or self.path:
            ok, message = self.send_callback(image=self.image, path=self.path, preview=self.preview)
        else:
            ok, message = self.send_callback(text=self.edit.toPlainText())
        if ok:
            self.clear_image()
            self.edit.clear()
        self.feedback.setText(message)
        self.update_controls()
        self.refresh()

    def feedback_message(self, message):
        self.feedback.setText(message)

    def clear_history(self):
        try:
            self.store.clear()
            self.feedback.setText("互传记录已清空，已保存的文件会保留")
        except OSError as error:
            self.feedback.setText("清空记录失败：" + str(error))
        self.refresh()

    def finish_file_send(self, path, ok, message):
        self.file_sending = False
        if ok and self.path == path:
            self.clear_image()
            self.edit.clear()
        self.feedback.setText(message)
        self.update_controls()
        self.refresh()

    def save_file(self, record):
        target, _ = QFileDialog.getSaveFileName(self, "保存收到的文件", record["name"], "所有文件 (*)")
        if not target:
            return
        opened = self.store.open_image(record["id"])
        if opened is None:
            self.feedback.setText("记录已过期，请重新发送")
            return
        stream, _, _ = opened
        def save():
            try:
                with stream, open(target, "wb") as dest:
                    shutil.copyfileobj(stream, dest, 64 * 1024)
                message = "文件已保存"
            except OSError as error:
                message = "保存失败：" + str(error)
            try:
                self.saved.emit(message)
            except RuntimeError:
                pass
        threading.Thread(target=save, name="JianJieFileSave", daemon=True).start()

    def refresh(self):
        snapshot = self.store.snapshot(self.revision)
        if not snapshot["changed"]:
            return
        self.revision = snapshot["revision"]
        while self.records.count():
            item = self.records.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        records = self.store.local_records()
        self.clear_records.setEnabled(bool(records))
        if not records:
            empty = QLabel("还没有互传记录")
            empty.setObjectName("phoneHint")
            self.records.addWidget(empty)
        for record in records:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 2, 0, 2)
            layout.setSpacing(8)
            if record["kind"] == "image" and record.get("_preview"):
                thumbnail = QLabel()
                thumbnail.setFixedSize(40, 32)
                thumbnail.setPixmap(QPixmap(record["_preview"]).scaled(40, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                layout.addWidget(thumbnail)
            sender = "接收端 → 电脑" if record["sender"] == "phone" else "电脑 → 接收端"
            summary = (record["text"].replace("\n", " ")[:200] if record["kind"] == "text"
                       else (f"文件 · {record['name']}" if record["kind"] == "file" else f"图片 · {record['width']} × {record['height']}"))
            text = QLabel()
            text.setTextFormat(Qt.PlainText)
            text.setText(text.fontMetrics().elidedText(summary, Qt.ElideRight, 285))
            text.setToolTip(summary[:500])
            text.setMinimumWidth(0)
            layout.addWidget(text, 1)
            meta = QLabel(f"{sender} · {time.strftime('%H:%M', time.localtime(record['created']))}\n{record['status'].replace('手机', '接收端')}")
            meta.setObjectName("phoneHint")
            meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            layout.addWidget(meta)
            if record["kind"] == "file":
                save = QPushButton("保存")
                save.setAutoDefault(False)
                save.clicked.connect(lambda checked=False, r=record: self.save_file(r))
                layout.addWidget(save)
            self.records.addWidget(row)
