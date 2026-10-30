"""Small, native-resolution controls for dual-column padding colors."""
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
import common as C
from polished_controls import SurfaceButton


def swatch(painter, rect, colors):
    path = QPainterPath()
    path.addEllipse(rect)
    painter.save()
    painter.setClipPath(path)
    half = rect.width()/2
    painter.fillRect(QRectF(rect.x(), rect.y(), half, rect.height()), QColor(*map(int, colors[0])))
    painter.fillRect(QRectF(rect.x()+half, rect.y(), half, rect.height()), QColor(*map(int, colors[1])))
    painter.restore()
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor('#8190a6'), 1))
    painter.drawEllipse(rect)


class FillButton(QPushButton):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setFixedSize(34, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName('双栏补齐底色')
        self.setToolTip('补齐底色 · 自动识别，点击调整左右栏')
        self.clicked.connect(owner.open_fill_colors)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.underMouse() or self.isDown():
            painter.setBrush(QColor('#34455d' if self.isDown() else '#27364a'))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(self.rect()), 7, 7)
        colors = [model.manual or (model.style.color if model.style else (230, 236, 245))
                  for model in self.owner._padding]
        swatch(painter, QRectF(8.5, 6.5, 17, 17), colors)
        if all(model.manual is None for model in self.owner._padding):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#65a7ff'))
            painter.drawEllipse(QRectF(23, 20, 5, 5))


class ColorChoice(QPushButton):
    def __init__(self, index, popup):
        super().__init__(popup)
        self.index, self.popup = index, popup
        self.setFixedSize(156, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(('左栏' if index == 0 else '右栏')+'补齐颜色，支持屏幕取色')
        self.setToolTip('选择颜色；可在颜色窗口使用“屏幕取色”')
        self.clicked.connect(lambda: popup.owner.choose_fill_color(index))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor('#263b58' if self.underMouse() else '#203047'))
        painter.setPen(QPen(QColor('#73adfc'), 1) if self.hasFocus() else Qt.NoPen)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 7, 7)
        model = self.popup.owner._padding[self.index]
        style = self.popup.owner.padding_style(self.index)
        swatch(painter, QRectF(10.5, 9.5, 15, 15), (style.color, style.color))
        color = QColor(*map(int, style.color)).name().upper()
        text = ('自动 · ' if model.manual is None else '')+color
        font = self.font()
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor('#deebff'))
        painter.drawText(self.rect().adjusted(34, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, text)


class FillColorPopup(QFrame):
    def __init__(self, owner):
        super().__init__(owner, Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.owner = owner
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(320)
        self.setAccessibleName('双栏补齐底色设置')
        self.setStyleSheet('QLabel{background:transparent;color:#dce8fa;font-size:12px;}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        title = QLabel('补齐底色', self)
        title.setStyleSheet('font-size:13px;font-weight:600;')
        layout.addWidget(title)
        self.choices, self.restore_buttons = [], []
        for index, label in enumerate(('左栏', '右栏')):
            row = QHBoxLayout()
            row.setSpacing(6)
            name = QLabel(label, self)
            name.setFixedWidth(30)
            row.addWidget(name)
            choice = ColorChoice(index, self)
            row.addWidget(choice)
            restore = SurfaceButton('恢复自动', self, variant='link', pixels=11)
            restore.setFixedSize(76, 34)
            restore.clicked.connect(lambda _=False, index=index: self.owner.set_fill_color(index, None))
            row.addWidget(restore)
            layout.addLayout(row)
            self.choices.append(choice)
            self.restore_buttons.append(restore)
        detail = QLabel('仅补齐较短一栏；选色后立即更新预览。', self)
        detail.setStyleSheet('font-size:11px;color:#8d9eb6;')
        layout.addWidget(detail)
        self.refresh()

    def refresh(self):
        for index in range(2):
            self.restore_buttons[index].setEnabled(self.owner._padding[index].manual is not None)
            self.choices[index].update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        stroke = max(1, round(self.devicePixelRatioF())) / self.devicePixelRatioF()
        painter.setPen(QPen(QColor('#3b4a60'), stroke))
        painter.setBrush(QColor('#151e2c'))
        rect = QRectF(self.rect()).adjusted(stroke/2, stroke/2, -stroke/2, -stroke/2)
        painter.drawRoundedRect(rect, 10, 10)

    def show_at_button(self):
        self.refresh()
        self.adjustSize()
        button = self.owner.bfill
        point = button.mapToGlobal(QPoint(0, button.height()+8))
        area = self.owner.screen().availableGeometry()
        x = max(area.left()+6, min(point.x(), area.right()-self.width()-5))
        y = point.y()
        if y+self.height() > area.bottom()-6:
            y = button.mapToGlobal(QPoint(0, 0)).y()-self.height()-8
        self.move(x, max(area.top()+6, min(y, area.bottom()-self.height()-5)))
        C.prepare_capture_window(self)
        self.show()
        C.exclude_from_capture(self)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.owner.fill_editor_closed()
