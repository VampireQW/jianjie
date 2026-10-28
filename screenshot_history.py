"""有界的可编辑截图历史：原图与 JSON 对象分离，PNG 在单个后台线程写入。"""
from collections import OrderedDict, Counter
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QIcon, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)
import common as C
import storage

LIMIT = 10
MAX_BYTES = 256 * 1024 * 1024
MAX_PENDING_BYTES = 96 * 1024 * 1024
ID = re.compile(r'^[a-f0-9]{32}$')
OPTION_KEYS = ('cur_num_style', 'cur_num_start', 'cur_high_shape', 'cur_high_dim', 'cur_arrow_style')


def encode(value):
    if isinstance(value, (QPoint, QPointF)):
        return {'$point': [value.x(), value.y()]}
    if isinstance(value, (QRect, QRectF)):
        return {'$rect': [value.x(), value.y(), value.width(), value.height()]}
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError('无法保存这条标注，请先复制或另存图片。')


def decode(value, depth=0):
    if depth > 12:
        raise ValueError('截图历史格式无效。')
    if isinstance(value, dict):
        if set(value) in ({'$point'}, {'$rect'}):
            key = next(iter(value))
            items = value[key]
            if not isinstance(items, list) or len(items) != (2 if key == '$point' else 4) or any(
                    not isinstance(n, (int, float)) or not math.isfinite(n) or abs(n) > 10_000_000 for n in items):
                raise ValueError('截图历史坐标无效。')
            return (QPoint if key == '$point' else QRect)(*[round(n) for n in items])
        return {key: decode(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item, depth + 1) for item in value]
    if value is None or isinstance(value, (str, bool, int)) or isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError('截图历史格式无效。')


def new_document(image, kind='screenshot'):
    base = QImage(image)
    base.setDevicePixelRatio(1.)
    return dict(id=uuid.uuid4().hex, base=base, kind=kind, created=time.time(), stamps=[], nums=[], options={}, beauty={})


def from_overlay(overlay):
    overlay.commit_text_editor()
    overlay.commit_text_input()
    k, selection = overlay.k, overlay.sel
    source = overlay.base.copy(QRect(round(selection.x()*k), round(selection.y()*k),
                                     round(selection.width()*k), round(selection.height()*k))).toImage()
    document = new_document(source)

    def point(value):
        return QPoint(round((value.x()-selection.x())*k), round((value.y()-selection.y())*k))

    def entry(value):
        result = dict(value)
        for key in ('pt', 'a', 'b', 'c'):
            if result.get(key) is not None:
                result[key] = point(result[key])
        if isinstance(result.get('r'), QRect):
            rect = result['r']
            result['r'] = QRect(point(rect.topLeft()), QSize(round(rect.width()*k), round(rect.height()*k)))
        if 'pts' in result:
            result['pts'] = [point(p) for p in result['pts']]
        if 'tiles' in result:
            result['tiles'] = [(round((x-selection.x())*k), round((y-selection.y())*k), color)
                               for x, y, color in result['tiles']]
        for key in ('tdx', 'tdy'):
            if key in result:
                result[key] = round(result[key]*k)
        for key in ('w', 'fs', 'asc', 'lh', 'bs', 'r'):
            if isinstance(result.get(key), (int, float)):
                result[key] = max(1, round(result[key]*k))
        return result

    document['stamps'] = [entry(s) for s in overlay.stamps if overlay._stamp_bbox(s).intersects(selection)]
    document['nums'] = [entry({**n, 'tdx': n.get('tdx', n['r']+6), 'tdy': n.get('tdy', -12)})
                        for n in overlay._nums]
    document['options'] = {key: getattr(overlay, key) for key in OPTION_KEYS}
    return document


def from_canvas(canvas, document=None, beauty=None):
    result = {**(document or new_document(canvas._base)), 'base': QImage(canvas._base),
              'stamps': [canvas._clone_entry(s) for s in canvas.stamps],
              'nums': [canvas._clone_num(n) for n in canvas._nums],
              'options': {key: getattr(canvas, key) for key in OPTION_KEYS}, 'beauty': dict(beauty or {})}
    return result


class HistoryStore(QObject):
    changed = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._files = threading.RLock()
        self._pending = OrderedDict()
        self._active = None
        self._thread = None
        self._epoch = 0
        self._closed = False
        self._opened = Counter()
        self._deleted = set()

    @property
    def root(self):
        return Path(C.data_dir())/'screenshot-history'

    def records(self):
        value = storage._read(str(self.root/'index.json'), [])
        if not isinstance(value, list):
            return []
        records = []
        for item in value:
            if not isinstance(item, dict) or not ID.fullmatch(str(item.get('id', ''))):
                continue
            if any(not isinstance(item.get(key), int) or item[key] <= 0 for key in ('width', 'height', 'bytes')):
                continue
            if any(not isinstance(item.get(key), (int, float)) or not math.isfinite(item[key])
                   or not 0 < item[key] < 253402214400 for key in ('created', 'updated')):
                continue
            records.append(item)
        return records

    def mark_open(self, identifier):
        self._opened[identifier] += 1

    def mark_closed(self, identifier):
        self._opened[identifier] -= 1
        if self._opened[identifier] <= 0:
            self._opened.pop(identifier, None)

    def save(self, document, preview):
        identifier = document['id']
        if not ID.fullmatch(identifier) or document['base'].isNull():
            raise ValueError('截图历史数据无效。')
        metadata = {key: encode(value) for key, value in document.items() if key != 'base'}
        metadata.update(schema=1, width=document['base'].width(), height=document['base'].height(), updated=time.time())
        serialized = json.dumps(metadata, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(serialized) > 16*1024*1024:
            raise ValueError('标注数据过大，未写入历史；原图仍可复制或保存。')
        job = dict(id=identifier, base=QImage(document['base']), preview=QImage(preview).scaled(360, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation),
                   serialized=serialized, metadata=metadata, epoch=self._epoch)
        with self._lock:
            if self._closed:
                raise OSError('截图历史正在结束，请稍后重试。')
            self._pending.pop(identifier, None)
            self._pending[identifier] = job
            skipped = False
            while len(self._pending) > 1 and (len(self._pending) > LIMIT or
                    sum(item['base'].sizeInBytes() for item in self._pending.values()) > MAX_PENDING_BYTES):
                self._pending.popitem(last=False)
                skipped = True
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='JianJieScreenshotHistory', daemon=True)
                self._thread.start()
        if skipped:
            self.failed.emit('截图历史繁忙，部分较早的记录未保留；复制或导出的图片仍可使用。')
        return identifier

    def _run(self):
        while True:
            with self._lock:
                if not self._pending:
                    self._active = None
                    self._thread = None
                    return
                _, job = self._pending.popitem(last=False)
                self._active = job
            try:
                self._write(job)
            except Exception as error:
                self.failed.emit('截图历史未保存 · '+str(error))
            finally:
                with self._lock:
                    self._active = None

    def _write(self, job):
        root, identifier = self.root, job['id']
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.write-', dir=root) as temporary:
            staging = Path(temporary)
            base = root/(identifier+'.png')
            if not base.is_file():
                if not job['base'].save(str(staging/'base.png'), 'PNG'):
                    raise OSError('原图写入失败，请检查应用数据目录。')
            if not job['preview'].save(str(staging/'preview.png'), 'PNG'):
                raise OSError('预览写入失败，请检查应用数据目录。')
            (staging/'document.json').write_bytes(job['serialized'])
            total = (base.stat().st_size if base.is_file() else (staging/'base.png').stat().st_size) + len(job['serialized']) + (staging/'preview.png').stat().st_size
            if total > MAX_BYTES:
                raise OSError('该图超过历史容量上限，复制或导出的图片仍可正常使用。')
            with self._files:
                if job['epoch'] != self._epoch or identifier in self._deleted:
                    return
                if (staging/'base.png').exists():
                    os.replace(staging/'base.png', base)
                os.replace(staging/'preview.png', root/(identifier+'-preview.png'))
                os.replace(staging/'document.json', root/(identifier+'.json'))
                records = [item for item in self.records() if item['id'] != identifier]
                meta = job['metadata']
                record = dict(id=identifier, created=meta['created'], updated=meta['updated'], width=meta['width'], height=meta['height'],
                              objects=len(meta.get('stamps', []))+len(meta.get('nums', [])), beauty=bool(meta.get('beauty', {}).get('enabled')), bytes=total)
                records.insert(0, record)
                removed = []
                while len(records) > LIMIT or sum(item.get('bytes', 0) for item in records) > MAX_BYTES:
                    removed.append(records.pop())
                storage.atomic_json(str(root/'index.json'), records)
                for old in removed:
                    self._unlink(old['id'])
        self.changed.emit()

    def load(self, identifier):
        if not ID.fullmatch(identifier):
            raise ValueError('截图历史不存在。')
        if identifier in self._deleted:
            raise ValueError('这条截图历史已删除。')
        with self._lock:
            job = self._pending.get(identifier)
            if job is None and self._active is not None and self._active['id'] == identifier:
                job = self._active
            if job is not None:
                return {**decode(json.loads(job['serialized'])), 'base': QImage(job['base'])}
        path = self.root/(identifier+'.json')
        if not path.is_file() or path.stat().st_size > 16*1024*1024:
            raise OSError('原图或编辑记录不存在，请从导出的图片重新编辑。')
        meta = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(meta, dict) or meta.get('schema') != 1 or meta.get('id') != identifier:
            raise ValueError('截图历史版本不受支持。')
        if not isinstance(meta.get('stamps'), list) or not isinstance(meta.get('nums'), list) or not isinstance(meta.get('options'), dict):
            raise ValueError('截图历史格式无效。')
        image = QImage(str(self.root/(identifier+'.png')))
        if image.isNull():
            raise OSError('原图读取失败，请检查应用数据目录。')
        return {**decode(meta), 'base': image}

    def _unlink(self, identifier):
        if ID.fullmatch(identifier):
            for suffix in ('.png', '.json', '-preview.png'):
                try:
                    (self.root/(identifier+suffix)).unlink(missing_ok=True)
                except OSError:
                    pass

    def remove(self, identifier=None):
        if (any(self._opened.values()) if identifier is None else self._opened.get(identifier, 0)):
            raise ValueError('请先关闭正在编辑的截图，再删除历史。')
        with self._files, self._lock:
            records = self.records()
            removed = records if identifier is None else [item for item in records if item['id'] == identifier]
            if identifier is None:
                self._epoch += 1
                self._pending.clear()
            else:
                self._deleted.add(identifier)
                self._pending.pop(identifier, None)
            storage.atomic_json(str(self.root/'index.json'), [] if identifier is None else [item for item in records if item['id'] != identifier])
            for item in removed:
                self._unlink(item['id'])
        self.changed.emit()

    def flush(self):
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join()

    def shutdown(self):
        with self._lock:
            self._closed = True
        self.flush()


class HistoryWindow(QWidget):
    def __init__(self, store, on_open):
        super().__init__()
        import widgets as W
        self.store, self.on_open = store, on_open
        self.setWindowTitle('截图历史 · 简截')
        self.setWindowIcon(QIcon(C.app_icon()))
        self.setStyleSheet(W.BASE_QSS)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        self.setScreen(screen)
        bounds = screen.availableGeometry()
        self.resize(min(820, bounds.width()-64), min(620, bounds.height()-64))
        self.move(bounds.center()-self.rect().center())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel('截图历史')
        title.setStyleSheet(f'font-size:20px;font-weight:600;color:{C.UI_TEXT};')
        header.addWidget(title)
        header.addStretch()
        self.clear = QPushButton('清空历史')
        self.clear.clicked.connect(lambda: self.remove())
        header.addWidget(self.clear)
        root.addLayout(header)
        self.summary = QLabel()
        self.summary.setStyleSheet(f'color:{C.UI_TEXT2};font-size:12px;')
        root.addWidget(self.summary)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        root.addWidget(self.scroll, 1)
        self.status = QLabel('原图与标注保存在本机，导出的图片不会随历史清理。')
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f'color:{C.UI_TEXT2};font-size:11px;')
        root.addWidget(self.status)
        store.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        scroll_position = self.scroll.verticalScrollBar().value()
        records = self.store.records()
        self.clear.setEnabled(bool(records))
        size = sum(item.get('bytes', 0) for item in records)/1048576
        self.summary.setText(f'最近 {LIMIT} 条 · 本地占用 {size:.1f} MiB / 256 MiB')
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        if not records:
            empty = QLabel('完成一次截图后，可在这里继续编辑\n原图、标注和画布样式会一起保留')
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f'color:{C.UI_TEXT2};font-size:13px;line-height:1.6;')
            layout.addStretch()
            layout.addWidget(empty)
        for item in records:
            row = QFrame()
            row.setStyleSheet(f'QFrame{{background:#191f2b;border:1px solid {C.UI_BORDER};border-radius:10px;}}QLabel{{border:none;background:transparent;}}')
            line = QHBoxLayout(row)
            line.setContentsMargins(14, 12, 14, 12)
            line.setSpacing(14)
            preview = QLabel()
            preview.setFixedSize(96, 76)
            preview.setAlignment(Qt.AlignCenter)
            pixmap = QPixmap.fromImage(QImage(str(self.store.root/(item['id']+'-preview.png'))))
            preview.setPixmap(pixmap.scaled(96, 76, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            line.addWidget(preview)
            text = QVBoxLayout()
            title = QLabel(time.strftime('截图 · %m月%d日 %H:%M', time.localtime(item['created'])))
            title.setStyleSheet(f'font-size:13px;font-weight:600;color:{C.UI_TEXT};')
            text.addWidget(title)
            detail = QLabel(f"{item['width']} × {item['height']} · {item.get('objects',0)} 个标注"+(' · 已美化' if item.get('beauty') else ''))
            detail.setStyleSheet(f'color:{C.UI_TEXT2};font-size:11px;')
            text.addWidget(detail)
            line.addLayout(text, 1)
            edit = QPushButton('继续编辑')
            edit.setStyleSheet('background:#2563eb;color:white;border:none;border-radius:7px;padding:8px 12px;')
            edit.clicked.connect(lambda _=False, identifier=item['id']: self.open(identifier))
            line.addWidget(edit)
            remove = QPushButton('删除')
            remove.clicked.connect(lambda _=False, identifier=item['id']: self.remove(identifier))
            line.addWidget(remove)
            layout.addWidget(row)
        layout.addStretch()
        old = self.scroll.takeWidget()
        self.scroll.setWidget(body)
        if old is not None:
            old.deleteLater()
        layout.activate()
        self.scroll.verticalScrollBar().setValue(scroll_position)

    def open(self, identifier):
        try:
            self.on_open(self.store.load(identifier))
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status.setText(str(error))

    def remove(self, identifier=None):
        if QMessageBox.question(self, '清理截图历史', '删除这条可编辑记录？导出的图片会保留。' if identifier else
                                '清空所有可编辑截图历史？导出的图片会保留。', QMessageBox.Yes|QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        try:
            self.store.remove(identifier)
        except (OSError, ValueError) as error:
            self.status.setText(str(error))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
