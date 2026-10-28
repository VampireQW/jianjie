"""截图美化：同一套几何与 Qt 绘制用于小预览和原分辨率导出。"""
import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetricsF, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)
import common as C

PRESETS = {
    'mist': ('雾蓝', '#edf2ff', '#c6d6f5'),
    'violet': ('暮紫', '#e7e0f7', '#b9c5e9'),
    'sage': ('青岚', '#e6f0eb', '#accfc7'),
    'sand': ('暖砂', '#fff3e0', '#eac7b1'),
    'white': ('纯白', '#ffffff', '#ffffff'),
    'gray': ('浅灰', '#eceff3', '#eceff3'),
    'dark': ('深色', '#242a35', '#111722'),
    'transparent': ('透明', None, None),
    'custom': ('自定义', None, None),
}
DEFAULT = dict(enabled=False, background='mist', color='#dce5f7', padding=64, radius=20, shadow=35, ratio='auto')
RATIOS = [('自动', 'auto'), ('1 : 1', '1:1'), ('4 : 3', '4:3'), ('16 : 9', '16:9'), ('3 : 4', '3:4'), ('9 : 16', '9:16')]
MAX_OUTPUT_PIXELS = 80_000_000


def normalize(value=None):
    value = value if isinstance(value, dict) else {}
    result = {**DEFAULT, **{k: v for k, v in value.items() if k in DEFAULT}}
    result['enabled'] = bool(result['enabled'])
    if not isinstance(result['background'], str) or result['background'] not in PRESETS:
        result['background'] = DEFAULT['background']
    if not isinstance(result['ratio'], str) or result['ratio'] not in {item[1] for item in RATIOS}:
        result['ratio'] = 'auto'
    color = QColor(str(result['color']))
    result['color'] = color.name() if color.isValid() else DEFAULT['color']
    for key, upper in (('padding', 160), ('radius', 80), ('shadow', 100)):
        try:
            result[key] = max(0, min(upper, int(result[key])))
        except (ValueError, TypeError, OverflowError):
            result[key] = DEFAULT[key]
    return result


def geometry(width, height, settings):
    settings = normalize(settings)
    if not settings['enabled']:
        return QSize(width, height), QRectF(0, 0, width, height)
    pad = settings['padding']
    ow, oh = width + 2 * pad, height + 2 * pad
    if settings['ratio'] != 'auto':
        a, b = map(int, settings['ratio'].split(':'))
        if ow / oh < a / b:
            ow = math.ceil(oh * a / b)
        else:
            oh = math.ceil(ow * b / a)
    return QSize(ow, oh), QRectF((ow - width) / 2, (oh - height) / 2, width, height)


def paint(painter, image, settings, original_size=None):
    settings = normalize(settings)
    original_size = original_size or image.size()
    size, rect = geometry(original_size.width(), original_size.height(), settings)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    if not settings['enabled']:
        if image is not None:
            if isinstance(image, QPixmap):
                painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
                painter.drawPixmap(rect, image, QRectF(image.rect()))
            else:
                painter.drawImage(rect, image)
        return
    key = settings['background']
    if key != 'transparent':
        _, first, second = PRESETS[key]
        first = first or settings['color']
        second = second or first
        gradient = QLinearGradient(0, 0, size.width(), size.height())
        gradient.setColorAt(0, QColor(first))
        gradient.setColorAt(1, QColor(second))
        painter.fillRect(QRectF(0, 0, size.width(), size.height()), gradient)
    radius = min(settings['radius'], rect.width() / 2, rect.height() / 2)
    if settings['shadow']:
        # Soft concentric edge strokes avoid a full-resolution blurred bitmap.
        blur = 30.0
        painter.setBrush(Qt.NoBrush)
        for index in range(18, 0, -1):
            spread = blur * index / 18
            alpha = round(settings['shadow'] * .35 * math.exp(-3 * (index / 18) ** 2))
            painter.setPen(QPen(QColor(0, 0, 0, max(1, alpha)), blur / 18 * 2))
            shadow = rect.translated(0, 7).adjusted(-spread, -spread, spread, spread)
            painter.drawRoundedRect(shadow, radius + spread, radius + spread)
    clip = QPainterPath()
    clip.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setClipPath(clip)
    if image is not None:
        if isinstance(image, QPixmap):
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawPixmap(rect, image, QRectF(image.rect()))
        else:
            painter.drawImage(rect, image)
    painter.restore()
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(20, 28, 45, 22), 1))
    painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), radius, radius)


def render(image, settings, max_side=None, original_size=None):
    settings = normalize(settings)
    original_size = original_size or image.size()
    size, _ = geometry(original_size.width(), original_size.height(), settings)
    if not max_side and size.width() * size.height() > MAX_OUTPUT_PIXELS:
        raise ValueError('美化后的图片过大，请减少留白或改为自动比例。')
    factor = min(1., max_side / max(size.width(), size.height())) if max_side else 1.
    output = QImage(max(1, round(size.width() * factor)), max(1, round(size.height() * factor)), QImage.Format_ARGB32_Premultiplied)
    if output.isNull():
        raise OSError('图片所需内存不足，请减少留白或改为自动比例。')
    output.fill(Qt.transparent)
    painter = QPainter(output)
    painter.scale(factor, factor)
    paint(painter, image, settings, original_size)
    painter.end()
    return output


class BeautyPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image, self.original_size = QImage(), QSize()
        self._canvas = None
        self.settings = normalize()
        pattern = QPixmap(16, 16)
        pattern.fill(QColor('#252c39'))
        painter = QPainter(pattern)
        painter.fillRect(0, 0, 8, 8, QColor('#202633'))
        painter.fillRect(8, 8, 8, 8, QColor('#202633'))
        painter.end()
        self._checker = QBrush(pattern)
        self.setMinimumSize(180, 160)

    def set_image(self, image, original_size):
        self.image, self.original_size = image, QSize(original_size)
        self.update()

    def set_canvas(self, canvas):
        self._canvas = canvas
        self.image = QImage()
        self.original_size = QSize(canvas._img_w, canvas._img_h)
        canvas.setParent(self)
        canvas.setStyleSheet('LongCanvas{background:transparent;}')
        self.refresh_layout()
        canvas.show()

    def release_canvas(self):
        canvas, self._canvas = self._canvas, None
        self.update()
        return canvas

    def set_settings(self, settings):
        self.settings = normalize(settings)
        self.refresh_layout()

    def _placement(self):
        size, rect = geometry(self.original_size.width(), self.original_size.height(), self.settings)
        scale = min(max(1, self.width()-48)/max(1, size.width()),
                    max(1, self.height()-48)/max(1, size.height()))
        return size, rect, scale, (self.width()-size.width()*scale)/2, (self.height()-size.height()*scale)/2

    def refresh_layout(self):
        canvas = self._canvas
        if canvas is not None:
            _, _, scale, left, top = self._placement()
            canvas.set_view_scale(scale, preview=True, beauty=self.settings)
            canvas.move(round(left), round(top))
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_layout()

    def mousePressEvent(self, event):
        if self._canvas is not None and event.button() == Qt.LeftButton:
            self._canvas.commit_text_editor()
            self._canvas.commit_text_input()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#0d1119'))
        if self._canvas is not None or not self.image.isNull():
            size, _, scale, left, top = self._placement()
            painter.translate(left, top)
            painter.scale(scale, scale)
            painter.fillRect(QRectF(0, 0, size.width(), size.height()), self._checker)
            if self._canvas is None:
                paint(painter, self.image, self.settings, self.original_size)
        painter.end()


class BeautyCombo(QComboBox):
    """Keep native combo keyboard/popup behavior, with a crisp painted field."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('QComboBox{border:none;min-height:36px;max-height:36px;padding:0 40px 0 12px;background:transparent;}'
                           'QComboBox::drop-down{subcontrol-origin:padding;subcontrol-position:top right;width:36px;border:none;}'
                           'QComboBox::down-arrow{image:none;}')
        view = self.view()
        view.setFrameShape(QFrame.NoFrame)
        view.setStyleSheet('QListView{background:#202735;color:#eef1f7;border:1px solid #465165;'
                          'border-radius:8px;padding:5px;outline:none;}'
                          'QListView::item{border:none;border-radius:5px;padding:7px 10px;}'
                          'QListView::item:selected{background:#304568;color:#eef1f7;}'
                          'QListView::item:hover{background:#293a55;}')
        popup = view.window()
        popup.setObjectName('beautyComboPopup')
        popup.setWindowFlag(Qt.FramelessWindowHint, True)
        popup.setAttribute(Qt.WA_TranslucentBackground, True)
        popup.setStyleSheet('QFrame#beautyComboPopup{background:transparent;border:none;}')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        enabled = self.isEnabled()
        border = '#60a5fa' if self.hasFocus() else '#596a83' if self.underMouse() else '#465165'
        painter.setPen(QPen(QColor(border if enabled else '#333e50'), 1))
        painter.setBrush(QColor('#202735'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 8, 8)
        left = 12
        icon = self.itemIcon(self.currentIndex())
        if not icon.isNull():
            icon.paint(painter, QRect(left, (self.height()-16)//2, 16, 16), Qt.AlignCenter,
                       QIcon.Normal if enabled else QIcon.Disabled)
            left += 24
        text_rect = QRect(left, 0, max(1, self.width()-left-40), self.height())
        painter.setFont(self.font())
        painter.setPen(QColor(C.UI_TEXT if enabled else '#8290a6'))
        metrics = QFontMetricsF(painter.font(), self)
        text = metrics.elidedText(self.currentText(), Qt.ElideRight, text_rect.width())
        ink = metrics.tightBoundingRect('国')  # Stable CJK baseline across both fields and selected values.
        painter.drawText(QPointF(left, self.height()/2-ink.center().y()), text)
        x, y = self.width()-20, self.height()/2
        arrow = QPainterPath(QPointF(x-4, y-2))
        arrow.lineTo(x, y+2)
        arrow.lineTo(x+4, y-2)
        painter.setPen(QPen(QColor('#c1cede' if enabled else '#69778b'), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(arrow)
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class BeautyPanel(QFrame):
    changed = Signal(dict)

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = normalize(settings)
        self.setObjectName('beautyPanel')
        self.setFixedWidth(244)
        self.setStyleSheet(f'''
        QFrame#beautyPanel {{background:{C.UI_BG_CARD};border-left:1px solid {C.UI_BORDER};}}
        QWidget#beautyOptions {{background:transparent;}}
        QLabel {{color:{C.UI_TEXT};background:transparent;}}
        QCheckBox {{color:{C.UI_TEXT};background:transparent;spacing:8px;}}
        QComboBox {{background:#202735;border:1px solid #465165;border-radius:7px;padding:6px 8px;color:{C.UI_TEXT};min-height:20px;}}
        QComboBox QAbstractItemView {{background:#202735;color:{C.UI_TEXT};selection-background-color:#304568;}}
        QPushButton {{background:#202735;color:{C.UI_TEXT};border:1px solid #465165;border-radius:7px;padding:6px 10px;}}
        QPushButton:hover {{background:#293447;}}
        QComboBox:disabled {{color:#69778b;border-color:#30394a;}}
        QPushButton:focus,QComboBox:focus {{border-color:#60a5fa;}}
        QSlider::groove:horizontal {{height:4px;background:#354052;border-radius:2px;}}
        QSlider::sub-page:horizontal {{background:#60a5fa;border-radius:2px;}}
        QSlider::handle:horizontal {{width:14px;margin:-5px 0;background:#dbeafe;border-radius:7px;}}
        ''')
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(16)
        title = QLabel('画布样式')
        title.setStyleSheet('font-size:15px;font-weight:600;')
        root.addWidget(title)
        self.enabled = QCheckBox('开启美化')
        self.enabled.setChecked(self.settings['enabled'])
        self.enabled.toggled.connect(lambda value: self._change('enabled', value))
        root.addWidget(self.enabled)
        self.options = QWidget()
        self.options.setObjectName('beautyOptions')
        options = QVBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(14)
        root.addWidget(self.options)
        options.addWidget(QLabel('背景'))
        self.background = BeautyCombo()
        for key, (label, first, second) in PRESETS.items():
            swatch = QPixmap(20, 20)
            swatch.fill(Qt.transparent)
            painter = QPainter(swatch)
            painter.setRenderHint(QPainter.Antialiasing, True)
            gradient = QLinearGradient(0, 0, 20, 20)
            gradient.setColorAt(0, QColor(first or '#eef1f7'))
            gradient.setColorAt(1, QColor(second or '#75849a'))
            painter.setBrush(gradient)
            painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
            painter.drawRoundedRect(QRectF(.5, .5, 19, 19), 4, 4)
            painter.end()
            self.background.addItem(QIcon(swatch), label, key)
        self.background.setCurrentIndex(self.background.findData(self.settings['background']))
        self.background.setAccessibleName('画布背景')
        self.background.currentIndexChanged.connect(lambda index: self._change('background', self.background.itemData(index)))
        options.addWidget(self.background)
        self.custom = QPushButton('选择自定义颜色')
        self.custom.clicked.connect(self._pick_color)
        options.addWidget(self.custom)
        options.addWidget(QLabel('画布比例'))
        self.ratio = BeautyCombo()
        for label, value in RATIOS:
            self.ratio.addItem(label, value)
        self.ratio.setCurrentIndex(self.ratio.findData(self.settings['ratio']))
        self.ratio.setAccessibleName('画布比例')
        self.ratio.currentIndexChanged.connect(lambda index: self._change('ratio', self.ratio.itemData(index)))
        options.addWidget(self.ratio)
        self.sliders = {}
        for key, label, upper, unit in (('padding', '留白', 160, 'px'), ('radius', '圆角', 80, 'px'), ('shadow', '阴影', 100, '%')):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addStretch()
            number = QLabel(f"{self.settings[key]} {unit}")
            number.setStyleSheet(f'color:{C.UI_TEXT2};font-size:12px;')
            row.addWidget(number)
            options.addLayout(row)
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, upper)
            slider.setValue(self.settings[key])
            slider.setAccessibleName(label)
            slider.valueChanged.connect(lambda value, k=key, n=number, u=unit: (n.setText(f'{value} {u}'), self._change(k, value)))
            options.addWidget(slider)
            self.sliders[key] = slider
        root.addStretch()
        reset = QPushButton('重置样式')
        reset.clicked.connect(self._reset)
        root.addWidget(reset)
        self.dimensions = QLabel()
        self.dimensions.setWordWrap(True)
        self.dimensions.setStyleSheet(f'color:{C.UI_TEXT2};font-size:11px;')
        root.addWidget(self.dimensions)
        self.options.setEnabled(self.settings['enabled'])
        self.custom.setVisible(self.settings['background'] == 'custom')

    def _change(self, key, value):
        self.settings = normalize({**self.settings, key: value})
        self.options.setEnabled(self.settings['enabled'])
        self.custom.setVisible(self.settings['background'] == 'custom')
        self.changed.emit(dict(self.settings))

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self.settings['color']), self, '选择画布颜色')
        if color.isValid():
            self._change('color', color.name())

    def _reset(self):
        self.settings = normalize({**DEFAULT, 'enabled': True})
        self.enabled.setChecked(True)
        self.background.setCurrentIndex(self.background.findData(self.settings['background']))
        self.ratio.setCurrentIndex(0)
        for key, slider in self.sliders.items():
            slider.setValue(self.settings[key])
        self._change('color', DEFAULT['color'])
