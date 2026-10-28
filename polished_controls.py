"""Small raster controls; window shadows never rasterize their foreground children."""
import math

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QFrame, QGraphicsDropShadowEffect, QLineEdit, QPushButton


def _surface(painter, widget, fill, border=None, radius=8):
    scale = widget.devicePixelRatioF()
    pixels = max(1, math.floor(scale + .5))
    inset = pixels / scale / 2
    rect = QRectF(inset, inset, math.floor(widget.width() * scale) / scale - inset * 2,
                  math.floor(widget.height() * scale) / scale - inset * 2)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(fill))
    painter.setPen(QPen(QColor(border), pixels / scale) if border else Qt.NoPen)
    painter.drawRoundedRect(rect, radius, radius)


class CrispLineEdit(QLineEdit):
    def __init__(self, text="", parent=None, pixels=13.5, padding="9px 14px"):
        super().__init__(text, parent)
        self.setFrame(False)
        self.setStyleSheet("QLineEdit{background:transparent;border:none;border-radius:0;"
                          f"padding:{padding};font-size:{pixels}px;color:#edf2fa;"
                          "selection-background-color:#315f9c;}"
                          "QLineEdit:focus{border:none;}"
                          "QLineEdit:disabled{color:#7d8ba1;}")

    def paintEvent(self, event):
        painter = QPainter(self)
        _surface(painter, self, "#181e2a", "#73adfc" if self.hasFocus() else "#394456", 9)
        painter.end()
        super().paintEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()


class SurfaceButton(QPushButton):
    def __init__(self, text, parent=None, variant="secondary", dropdown=False, pixels=11):
        super().__init__(text, parent)
        self.variant, self.dropdown = variant, dropdown
        self.setAutoDefault(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"QPushButton{{background:transparent;border:none;padding:0;"
                          f"font-size:{pixels}px;font-weight:{600 if variant == 'primary' else 500};}}")
        self.setMinimumHeight(30 if variant != "primary" else 52)

    def sizeHint(self):
        self.ensurePolished()
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + (34 if self.dropdown else 24),
                     52 if self.variant == "primary" else 32)

    def paintEvent(self, event):
        painter = QPainter(self)
        active = self.isDown() or self.isChecked()
        enabled = self.isEnabled()
        if self.variant == "primary":
            fill = "#2b3443" if not enabled else "#2869d3" if active else "#4b90f7" if self.underMouse() else "#3b82f6"
            color = "#8291a7" if not enabled else "#ffffff"
        elif self.variant in ("link", "tab"):
            fill = "#1b2638" if enabled and (active or self.underMouse()) else "#10141e"
            color = "#6f7c90" if not enabled else "#a8ceff" if active or self.underMouse() else "#9daec5"
        else:
            fill = "#1b2536" if not enabled else "#203858" if active else "#2b405e" if self.underMouse() else "#22324b"
            color = "#71829b" if not enabled else "#cbe0ff"
        _surface(painter, self, fill, "#73adfc" if self.hasFocus() and enabled else None, 7)
        painter.setFont(self.font())
        painter.setPen(QColor(color))
        rect = self.rect().adjusted(10, 0, -24 if self.dropdown else -10, 0)
        painter.drawText(rect, Qt.AlignCenter, self.text())
        if self.dropdown:
            x, y = self.width() - 14, self.height() / 2
            pen = QPen(QColor(color), 1.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(QPolygonF([QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5)]))
        if self.variant == "tab" and self.isChecked():
            scale = self.devicePixelRatioF()
            painter.setPen(QPen(QColor("#7bb4ff"), 2 / scale))
            y = (math.floor(self.height() * scale) - 1) / scale
            painter.drawLine(QPointF(10, y), QPointF(self.width() - 10, y))


class SurfaceCheckBox(QCheckBox):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        y = (self.height() - 16) / 2
        box = QRectF(.5, y + .5, 15, 15)
        checked, enabled = self.isChecked(), self.isEnabled()
        painter.setBrush(QColor("#49b5ed" if enabled and checked else "#29394e" if checked else "#131a25"))
        painter.setPen(Qt.NoPen if checked else QPen(QColor("#7c8b9e"), 1))
        painter.drawRoundedRect(box, 4, 4)
        if checked:
            painter.setPen(QPen(QColor("#ffffff" if enabled else "#8a9ab1"), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(QPolygonF([QPointF(4, y + 8), QPointF(7, y + 11), QPointF(12, y + 5)]))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#8fc2ff"), 1))
            painter.drawRoundedRect(box.adjusted(-.5, -.5, .5, .5), 4, 4)
        painter.setFont(self.font())
        painter.setPen(QColor("#e5edf9" if enabled else "#7e8da3"))
        painter.drawText(self.rect().adjusted(23, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, self.text())


class _ShadowUnderlay(QFrame):
    def __init__(self, body, owner, radius):
        super().__init__(owner)
        self.body = body
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.NoFocus)
        self.setObjectName("surfaceShadow")
        self.setStyleSheet(f"QFrame#surfaceShadow{{background:#10141e;border:none;border-radius:{radius}px;}}")
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(26)
        effect.setOffset(0, 5)
        effect.setColor(QColor(0, 0, 0, 130))
        self.setGraphicsEffect(effect)
        body.installEventFilter(self)
        self.sync()

    def sync(self):
        self.setGeometry(self.body.geometry())
        self.setVisible(self.body.isVisible())
        self.stackUnder(self.body)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide):
            self.sync()
        return False


def crisp_window_shadow(owner, body, radius=10):
    """Keep the soft shadow on a sibling; paint text and strokes at native DPR."""
    body.setGraphicsEffect(None)
    shadow = _ShadowUnderlay(body, owner, radius)
    owner._crisp_shadow = shadow
    return shadow
