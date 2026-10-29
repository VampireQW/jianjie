"""截图美化：同一套几何与 Qt 绘制用于小预览和原分辨率导出。"""
import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)
import common as C
from polished_controls import SurfaceButton, SurfaceCheckBox

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


class BeautyChoice(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setAutoDefault(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet('QPushButton{background:transparent;border:none;padding:0;}')

    def sizeHint(self):
        return QSize(72, self.minimumHeight())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def label_font(self, painter):
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.DemiBold if self.isChecked() else QFont.Normal)
        painter.setFont(font)


class ColorSwatch(BeautyChoice):
    def __init__(self, key, custom_color, parent=None):
        super().__init__(PRESETS[key][0], parent)
        self.key, self.custom_color = key, custom_color
        self.setFixedHeight(54)
        self.setAccessibleName('背景 · '+self.text())
        self.setToolTip('选择自定义背景颜色' if key == 'custom' else self.text())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self.isEnabled():
            painter.setOpacity(.38)
        stroke = max(1, round(self.devicePixelRatioF())) / self.devicePixelRatioF()
        rect = QRectF(4, 4, self.width()-8, 29)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        painter.save()
        painter.setClipPath(path)
        _, first, second = PRESETS[self.key]
        if self.key == 'transparent':
            painter.fillRect(rect, QColor('#d8dee8'))
            for row in range(5):
                for col in range(math.ceil(rect.width()/7)):
                    if (row+col) % 2 == 0:
                        painter.fillRect(QRectF(rect.x()+col*7, rect.y()+row*7, 7, 7), QColor('#a8b3c4'))
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0, QColor(first or self.custom_color))
            gradient.setColorAt(1, QColor(second or first or self.custom_color))
            painter.fillRect(rect, gradient)
        painter.restore()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor('#66768f' if self.underMouse() else '#465268'), stroke))
        painter.drawPath(path)
        if self.isChecked() or self.hasFocus():
            painter.setPen(QPen(QColor('#83b9ff' if self.isChecked() else '#a8caff'), stroke*1.5))
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 8, 8)
        if self.key == 'custom':
            color = QColor('#28384f' if QColor(self.custom_color).lightnessF() > .6 else '#f1f5ff')
            painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap))
            x, y = rect.center().x(), rect.center().y()
            painter.drawLine(QPointF(x-4, y), QPointF(x+4, y))
            painter.drawLine(QPointF(x, y-4), QPointF(x, y+4))
        if self.isChecked():
            center = QPointF(rect.right()-8, rect.top()+8)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#3279df'))
            painter.drawEllipse(center, 5.5, 5.5)
            tick = QPainterPath(QPointF(center.x()-2.5, center.y()))
            tick.lineTo(center.x()-.5, center.y()+2)
            tick.lineTo(center.x()+3, center.y()-2)
            painter.setPen(QPen(QColor('#ffffff'), 1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(tick)
        self.label_font(painter)
        painter.setPen(QColor('#b9d7ff' if self.isChecked() else '#b5c1d3'))
        painter.drawText(QRectF(0, 36, self.width(), 18), Qt.AlignCenter, self.text())
        painter.end()


class RatioChoice(BeautyChoice):
    def __init__(self, label, value, parent=None):
        super().__init__(label, parent)
        self.value = value
        self.setFixedHeight(48)
        self.setAccessibleName('画布比例 · '+label)
        self.setToolTip('跟随原图与留白' if value == 'auto' else label+' 画布')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self.isEnabled():
            painter.setOpacity(.38)
        stroke = max(1, round(self.devicePixelRatioF())) / self.devicePixelRatioF()
        inset = stroke/2
        rect = QRectF(inset, inset, self.width()-stroke, self.height()-stroke)
        active = self.isChecked()
        painter.setBrush(QColor('#20324d' if active else '#1d2736' if self.underMouse() else '#171e2b'))
        painter.setPen(QPen(QColor('#83b9ff' if active or self.hasFocus() else '#586b85' if self.underMouse() else '#344155'), stroke))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor('#a8ceff' if active else '#8395b0'), stroke))
        if self.value == 'auto':
            x, y = self.width()/2, 15
            corners = QPainterPath()
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                corners.moveTo(x+sx*4, y+sy*5)
                corners.lineTo(x+sx*8, y+sy*5)
                corners.lineTo(x+sx*8, y+sy*1)
            painter.drawPath(corners)
        else:
            a, b = map(int, self.value.split(':'))
            factor = min(22/a, 14/b)
            w, h = a*factor, b*factor
            painter.drawRoundedRect(QRectF((self.width()-w)/2, 15-h/2, w, h), 1.5, 1.5)
        self.label_font(painter)
        painter.setPen(QColor('#d6e7ff' if active else '#b5c1d3'))
        painter.drawText(QRectF(0, 27, self.width(), 17), Qt.AlignCenter, self.text())
        painter.end()


class BeautyPanel(QFrame):
    changed = Signal(dict)

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = normalize(settings)
        self.setObjectName('beautyPanel')
        self.setFixedWidth(276)
        self.setStyleSheet(f'''
        QFrame#beautyPanel {{background:{C.UI_BG_CARD};border-left:1px solid {C.UI_BORDER};}}
        QWidget#beautyOptions {{background:transparent;}}
        QLabel {{color:{C.UI_TEXT};background:transparent;}}
        QCheckBox {{color:{C.UI_TEXT};background:transparent;spacing:8px;}}
        QSlider {{background:transparent;}}
        QSlider::groove:horizontal {{height:3px;background:#344155;border-radius:1px;}}
        QSlider::sub-page:horizontal {{background:#80b4fc;border-radius:1px;}}
        QSlider::handle:horizontal {{width:12px;margin:-5px 0;background:#dbeafe;border:1px solid #263b58;border-radius:6px;}}
        ''')
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)
        heading = QHBoxLayout()
        title = QLabel('画布样式')
        title.setStyleSheet('font-size:14px;font-weight:600;')
        heading.addWidget(title)
        heading.addStretch()
        self.enabled = SurfaceCheckBox('开启美化')
        self.enabled.setStyleSheet('font-size:11px;')
        self.enabled.setChecked(self.settings['enabled'])
        self.enabled.toggled.connect(lambda value: self._change('enabled', value))
        heading.addWidget(self.enabled)
        root.addLayout(heading)
        self.options = QWidget()
        self.options.setObjectName('beautyOptions')
        options = QVBoxLayout(self.options)
        options.setContentsMargins(0, 0, 0, 0)
        options.setSpacing(16)
        root.addWidget(self.options)
        self.background_group = QButtonGroup(self)
        self.background_buttons = {}
        palette = self._section(options, '背景')
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for index, key in enumerate(PRESETS):
            button = ColorSwatch(key, self.settings['color'])
            self.background_group.addButton(button)
            self.background_buttons[key] = button
            grid.addWidget(button, index//3, index%3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        palette.addLayout(grid)
        self.background_group.buttonClicked.connect(self._choose_background)
        self.ratio_group = QButtonGroup(self)
        self.ratio_buttons = {}
        ratio_section = self._section(options, '画布比例')
        ratio_grid = QGridLayout()
        ratio_grid.setContentsMargins(0, 0, 0, 0)
        ratio_grid.setSpacing(8)
        for index, (label, value) in enumerate(RATIOS):
            button = RatioChoice(label, value)
            self.ratio_group.addButton(button)
            self.ratio_buttons[value] = button
            ratio_grid.addWidget(button, index//3, index%3)
        for col in range(3):
            ratio_grid.setColumnStretch(col, 1)
        ratio_section.addLayout(ratio_grid)
        self.ratio_group.buttonClicked.connect(lambda button: self._change('ratio', button.value))
        details = self._section(options, '细节')
        details.setSpacing(6)
        self.sliders = {}
        self._numbers = {}
        for key, label, upper, unit in (('padding', '留白', 160, 'px'), ('radius', '圆角', 80, 'px'), ('shadow', '阴影', 100, '%')):
            row = QHBoxLayout()
            row.setSpacing(9)
            caption = QLabel(label)
            caption.setFixedWidth(30)
            caption.setStyleSheet('font-size:12px;')
            row.addWidget(caption)
            number = QLabel(f"{self.settings[key]} {unit}")
            number.setFixedWidth(42)
            number.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            number.setStyleSheet(f'color:{C.UI_TEXT2};font-size:12px;')
            slider = QSlider(Qt.Horizontal)
            slider.setMinimumHeight(28)
            slider.setRange(0, upper)
            slider.setValue(self.settings[key])
            slider.setAccessibleName(label)
            slider.valueChanged.connect(lambda value, k=key: self._change(k, value))
            row.addWidget(slider, 1)
            row.addWidget(number)
            details.addLayout(row)
            self.sliders[key] = slider
            self._numbers[key] = (number, unit)
        root.addStretch()
        reset = SurfaceButton('重置样式', pixels=12)
        reset.clicked.connect(self._reset)
        root.addWidget(reset)
        self.dimensions = QLabel()
        self.dimensions.setWordWrap(True)
        self.dimensions.setStyleSheet(f'color:{C.UI_TEXT2};font-size:11px;')
        root.addWidget(self.dimensions)
        self._sync_controls()

    @staticmethod
    def _section(parent_layout, text):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel(text)
        title.setStyleSheet('font-size:12px;font-weight:600;color:#bfcde0;')
        layout.addWidget(title)
        parent_layout.addLayout(layout)
        return layout

    def _sync_controls(self):
        self.enabled.blockSignals(True)
        self.enabled.setChecked(self.settings['enabled'])
        self.enabled.blockSignals(False)
        self.options.setEnabled(self.settings['enabled'])
        for key, button in self.background_buttons.items():
            button.setChecked(key == self.settings['background'])
            button.custom_color = self.settings['color']
            button.update()
        for key, button in self.ratio_buttons.items():
            button.setChecked(key == self.settings['ratio'])
        for key, slider in self.sliders.items():
            slider.blockSignals(True)
            slider.setValue(self.settings[key])
            slider.blockSignals(False)
            number, unit = self._numbers[key]
            number.setText(f"{self.settings[key]} {unit}")

    def _apply(self, values):
        self.settings = normalize({**self.settings, **values})
        self._sync_controls()
        self.changed.emit(dict(self.settings))

    def _change(self, key, value):
        self._apply({key: value})

    def _choose_background(self, button):
        if button.key == 'custom':
            self._pick_color()
        else:
            self._change('background', button.key)

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self.settings['color']), self, '选择画布颜色')
        if color.isValid():
            self._apply({'background': 'custom', 'color': color.name()})
        else:
            self._sync_controls()

    def _reset(self):
        self._apply({**DEFAULT, 'enabled': True})
