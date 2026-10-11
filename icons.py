# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 截图工具条矢量图标：QPainter 程序化绘制，按屏 DPR 渲染保持锐利。"""
import math

from PySide6.QtCore import QByteArray, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QIcon, QGuiApplication, QPainter, QPainterPath,
                           QPen, QPixmap, QPolygonF)
from PySide6.QtSvg import QSvgRenderer   # R133 lucide SVG 渲染（spec 已放行 Qt6Svg.dll）
from PySide6.QtWidgets import QPushButton, QWidget

S = 24.0          # 逻辑绘制空间
STROKE = "#dfe4ef"


def _painter(pm):
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return p


def _pen(color, w):
    pen = QPen(QColor(color), w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def pixmap(kind, color=STROKE, size=20, dpr=None):
    if dpr is None:
        scr = QGuiApplication.primaryScreen()
        dpr = (scr.devicePixelRatio() if scr else 1.0) or 1.0
    dpr = dpr or 1.0
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = _painter(pm)
    p.scale(size / S, size / S)   # 逻辑坐标：QPainter 对带 DPR 的 pixmap 已自动补偿物理像素，
    #                              若再用 px/S 会双重放大 → 200% 屏下图标被裁成左上角四分之一
    _draw(kind, p, color)
    p.end()
    return pm


def _draw(kind, p, c):
    p.setBrush(Qt.NoBrush)
    if kind == "rect":
        p.setPen(_pen(c, 1.9))
        p.drawRoundedRect(QRectF(4, 6, 16, 12), 2.5, 2.5)
    elif kind == "ellipse":
        p.setPen(_pen(c, 1.9))
        p.drawEllipse(QRectF(4.5, 6.5, 15, 11))
    elif kind == "arrow":
        p.setPen(_pen(c, 2.1))
        p.drawLine(QPointF(5.5, 18.5), QPointF(17.5, 6.5))
        p.drawLine(QPointF(19, 5), QPointF(12.6, 6.9))
        p.drawLine(QPointF(19, 5), QPointF(17.1, 11.4))
    elif kind == "pen":
        body = QPolygonF([QPointF(15.5, 4), QPointF(19.4, 7.9), QPointF(9.8, 17.5),
                          QPointF(5.9, 13.6)])
        p.setPen(_pen(c, 1.7))
        p.drawPolygon(body)
        tip = QPolygonF([QPointF(5.9, 13.6), QPointF(9.8, 17.5), QPointF(4.2, 19.8)])
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(tip)
    elif kind == "marker":                        # 荧光笔：斜杆粗笔杆 + 实心楔形凿头 + 底部划线
        body = QPolygonF([QPointF(14.8, 2.0), QPointF(18.8, 5.2), QPointF(11.8, 14.0),
                          QPointF(7.8, 10.8)])
        p.setPen(_pen(c, 1.7))
        p.drawPolygon(body)
        tip = QPolygonF([QPointF(7.8, 10.8), QPointF(11.8, 14.0), QPointF(8.6, 16.0),
                         QPointF(6.6, 14.4)])
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(tip)
        p.setPen(_pen(c, 2.7))
        p.drawLine(QPointF(3.6, 19.4), QPointF(10.4, 19.4))
    elif kind == "text":
        p.setPen(_pen(c, 2.3))
        p.drawLine(QPointF(4.8, 6.6), QPointF(19.2, 6.6))
        p.drawLine(QPointF(12, 6.6), QPointF(12, 19.4))
    elif kind == "num":
        p.setBrush(c)                                 # 实心圆标与画布上的序号徽章同构
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(4.5, 4.5, 15, 15))
        p.setPen(_pen("#16181f", 2.3))                # 几何「1」：竖笔+旗笔，小尺寸也清晰
        p.drawLine(QPointF(12, 7.4), QPointF(12, 16.6))
        p.drawLine(QPointF(9.6, 9.6), QPointF(12, 7.4))
    elif kind == "mosaic":
        p.setPen(Qt.NoPen)
        for (x, y, a) in ((4.5, 4.5, 1.0), (12.7, 4.5, 0.5), (4.5, 12.7, 0.5), (12.7, 12.7, 0.85)):
            p.setOpacity(a)
            p.setBrush(c)
            p.drawRect(QRectF(x, y, 6.8, 6.8))
        p.setOpacity(1.0)
    elif kind == "chevl":                          # 单左尖（上一月）：粗线折角，20px 下依然精神
        p.setPen(_pen(c, 2.3))
        p.drawLine(QPointF(15, 5.5), QPointF(9, 12))
        p.drawLine(QPointF(9, 12), QPointF(15, 18.5))
    elif kind == "chevr":                          # 单右尖（下一月）：chevl 镜像
        p.setPen(_pen(c, 2.3))
        p.drawLine(QPointF(9, 5.5), QPointF(15, 12))
        p.drawLine(QPointF(15, 12), QPointF(9, 18.5))
    elif kind == "chevl2":                         # 双左尖（上一年）：两列折角
        p.setPen(_pen(c, 2.3))
        p.drawLine(QPointF(11.5, 5.5), QPointF(5.5, 12))
        p.drawLine(QPointF(5.5, 12), QPointF(11.5, 18.5))
        p.drawLine(QPointF(18.5, 5.5), QPointF(12.5, 12))
        p.drawLine(QPointF(12.5, 12), QPointF(18.5, 18.5))
    elif kind == "chevr2":                         # 双右尖（下一年）：chevl2 镜像
        p.setPen(_pen(c, 2.3))
        p.drawLine(QPointF(12.5, 5.5), QPointF(18.5, 12))
        p.drawLine(QPointF(18.5, 12), QPointF(12.5, 18.5))
        p.drawLine(QPointF(5.5, 5.5), QPointF(11.5, 12))
        p.drawLine(QPointF(11.5, 12), QPointF(5.5, 18.5))
    elif kind == "undo":                          # 撤销：逆时针近全圆弧，开口朝左，箭头在上唇指向左下
        p.setPen(_pen(c, 2.1))
        arc = QRectF(6.1, 5.5, 13.0, 13.0)         # 圆心 (12.6,12.0) r=6.5
        path = QPainterPath()
        path.arcMoveTo(arc, 240)                   # 尾在左下唇 (θ=240°)
        path.arcTo(arc, 240, 240)                  # 逆时针绕底→右→顶，到上唇 (θ=120°)
        p.drawPath(path)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(10.75, 8.8), QPointF(7.95, 3.95), QPointF(5.4, 8.7)]))  # 沿末切线指左下
    elif kind == "redo":                          # 重做：undo 镜像（顺时针弧，开口朝右，箭头指右下）
        p.setPen(_pen(c, 2.1))
        arc = QRectF(4.9, 5.5, 13.0, 13.0)         # 圆心 (11.4,12.0) r=6.5
        path = QPainterPath()
        path.arcMoveTo(arc, -60)                   # 尾在右下唇 (θ=-60°)
        path.arcTo(arc, -60, -240)                 # 顺时针绕底→左→顶，到上唇 (θ=60°)
        p.drawPath(path)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(13.25, 8.8), QPointF(16.05, 3.95), QPointF(18.6, 8.7)]))  # 沿末切线指左下
    elif kind == "copy":
        p.setPen(_pen(c, 1.8))
        p.drawRoundedRect(QRectF(8.8, 4.2, 10.6, 12.4), 2.2, 2.2)
        p.setBrush(QColor("#101218"))
        p.drawRoundedRect(QRectF(4.6, 7.6, 10.6, 12.4), 2.2, 2.2)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4.6, 7.6, 10.6, 12.4), 2.2, 2.2)
    elif kind == "save":
        p.setPen(_pen(c, 2.1))
        p.drawLine(QPointF(12, 4.4), QPointF(12, 12.8))
        p.drawLine(QPointF(12, 12.8), QPointF(7.8, 8.8))
        p.drawLine(QPointF(12, 12.8), QPointF(16.2, 8.8))
        path = QPainterPath(QPointF(4.4, 14.8))
        path.lineTo(4.4, 19.4)
        path.lineTo(19.6, 19.4)
        path.lineTo(19.6, 14.8)
        p.drawPath(path)
    elif kind == "pin":                           # 钉图：梯形钉头（顶窄底宽）+斜肩收腰+细线针（仿参考图）
        hp = QPainterPath(QPointF(10.4, 13.0))     # 腰左
        hp.lineTo(7.2, 11.6)
        hp.quadTo(QPointF(6.6, 11.2), QPointF(6.9, 10.4))
        hp.lineTo(8.9, 5.1)
        hp.quadTo(QPointF(9.2, 4.4), QPointF(9.9, 4.4))
        hp.lineTo(14.1, 4.4)
        hp.quadTo(QPointF(14.8, 4.4), QPointF(15.1, 5.1))
        hp.lineTo(17.1, 10.4)
        hp.quadTo(QPointF(17.4, 11.2), QPointF(16.8, 11.6))
        hp.lineTo(13.6, 13.0)
        hp.closeSubpath()                          # 腰线
        p.setPen(_pen(c, 1.7))
        p.setBrush(Qt.NoBrush)
        p.drawPath(hp)
        p.setPen(_pen(c, 2.0))
        p.drawLine(QPointF(12.0, 13.0), QPointF(12.0, 19.0))       # 针：独立细线，朝下
    elif kind == "close":
        p.setPen(_pen(c, 2.3))
        p.drawLine(QPointF(6.8, 6.8), QPointF(17.2, 17.2))
        p.drawLine(QPointF(17.2, 6.8), QPointF(6.8, 17.2))
    elif kind == "confirm":
        p.setPen(_pen(c, 2.5))
        path = QPainterPath(QPointF(5, 12.6))
        path.lineTo(10.2, 17.6)
        path.lineTo(19, 6.6)
        p.drawPath(path)
    elif kind == "rec":                           # 开始录制：实心圆点（录制经典符号；R136 用户点名「工具条用那种小红点的」回归）
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12, 12), 6.2, 6.2)
    elif kind == "pause":                         # 暂停：双竖条
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(7.0, 5.5, 3.6, 13.0), 1.4, 1.4)
        p.drawRoundedRect(QRectF(13.4, 5.5, 3.6, 13.0), 1.4, 1.4)
    elif kind == "play":                          # 继续：右向三角
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(8.5, 5.5), QPointF(18.5, 12.0), QPointF(8.5, 18.5)]))
    elif kind == "clickfx":                       # 点击高亮：同心圆环 + 中心点
        p.setPen(_pen(c, 1.8))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(12, 12), 7.8, 7.8)
        p.drawEllipse(QPointF(12, 12), 4.2, 4.2)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12, 12), 1.6, 1.6)
    elif kind in ("dot1", "dot2", "dot3"):
        r = {"dot1": 2.6, "dot2": 4.0, "dot3": 5.6}[kind]
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12, 12), r, r)
    elif kind == "high":                          # 高光：四角星光
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        sp = QPainterPath(QPointF(12.0, 3.4))
        sp.quadTo(QPointF(13.0, 11.0), QPointF(20.6, 12.0))
        sp.quadTo(QPointF(13.0, 13.0), QPointF(12.0, 20.6))
        sp.quadTo(QPointF(11.0, 13.0), QPointF(3.4, 12.0))
        sp.quadTo(QPointF(11.0, 11.0), QPointF(12.0, 3.4))
        p.drawPath(sp)
    elif kind == "ocr":                           # 提取：扫描框角 + 字母 A
        p.setPen(_pen(c, 1.8))
        for (x1, y1, x2, y2) in ((4.4, 8.4, 4.4, 4.4), (4.4, 4.4, 8.4, 4.4),
                                 (15.6, 4.4, 19.6, 4.4), (19.6, 4.4, 19.6, 8.4),
                                 (19.6, 15.6, 19.6, 19.6), (19.6, 19.6, 15.6, 19.6),
                                 (8.4, 19.6, 4.4, 19.6), (4.4, 19.6, 4.4, 15.6)):
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        p.setPen(_pen(c, 1.9))
        p.drawLine(QPointF(12.0, 9.2), QPointF(9.4, 16.2))     # 「A」左斜笔（缩小，别顶满扫描框）
        p.drawLine(QPointF(12.0, 9.2), QPointF(14.6, 16.2))    # 「A」右斜笔
        p.drawLine(QPointF(10.5, 14.0), QPointF(13.5, 14.0))   # 「A」横笔
    elif kind == "qr":                            # 识码：三个定位角 + 右下数据点
        for (x, y) in ((4.4, 4.4), (14.0, 4.4), (4.4, 14.0)):
            p.setPen(_pen(c, 1.8))
            p.drawRect(QRectF(x, y, 5.6, 5.6))
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawRect(QRectF(x + 1.9, y + 1.9, 1.8, 1.8))
        p.setBrush(c)
        p.drawRect(QRectF(14.4, 14.4, 2.6, 2.6))
        p.setOpacity(0.55)
        p.drawRect(QRectF(18.2, 14.4, 1.4, 1.4))
        p.drawRect(QRectF(14.4, 18.2, 1.4, 1.4))
        p.setOpacity(1.0)
    elif kind == "gif":                           # GIF：几何字母 G/I/F（字体在缩放画布下发糊）
        p.setPen(_pen(c, 2.0))
        gp = QPainterPath(QPointF(6.4, 12.2))                      # G：方块字，C 弧加小钩在小尺寸读成 C
        gp.lineTo(8.8, 12.2)                                       #   横杠
        gp.lineTo(8.8, 16.2)                                       #   右下竖
        gp.lineTo(3.8, 16.2)                                       #   底横
        gp.lineTo(3.8, 7.8)                                        #   左竖
        gp.lineTo(8.8, 7.8)                                        #   顶横
        p.drawPath(gp)
        p.drawLine(QPointF(12.2, 7.6), QPointF(12.2, 16.4))        # I
        p.drawLine(QPointF(16.2, 16.4), QPointF(16.2, 7.6))        # F 竖笔
        p.drawLine(QPointF(16.2, 7.6), QPointF(20.7, 7.6))         # F 上横
        p.drawLine(QPointF(16.2, 12.2), QPointF(19.9, 12.2))       # F 中横
    elif kind == "longshot":                      # 长截图：虚线感页框 + 上下双箭头（微信同式）
        p.setPen(_pen(c, 1.8))
        p.setBrush(Qt.NoBrush)
        _fp = QPainterPath()
        _fp.moveTo(7.5, 9.0)                                   # 顶部框「∩」：左右竖边中段断开
        _fp.lineTo(7.5, 6.0)
        _fp.quadTo(7.5, 3.5, 10.5, 3.5)
        _fp.lineTo(13.5, 3.5)
        _fp.quadTo(16.5, 3.5, 16.5, 6.0)
        _fp.lineTo(16.5, 9.0)
        _fp.moveTo(7.5, 15.0)                                  # 底部框「∪」
        _fp.lineTo(7.5, 18.0)
        _fp.quadTo(7.5, 20.5, 10.5, 20.5)
        _fp.lineTo(13.5, 20.5)
        _fp.quadTo(16.5, 20.5, 16.5, 18.0)
        _fp.lineTo(16.5, 15.0)
        p.drawPath(_fp)
        _ap = _pen(c, 2.0)
        _ap.setCapStyle(Qt.RoundCap)
        _ap.setJoinStyle(Qt.RoundJoin)
        p.setPen(_ap)
        p.drawLine(QPointF(9.8, 6.9), QPointF(14.2, 6.9))      # 上短横
        p.drawLine(QPointF(9.8, 17.1), QPointF(14.2, 17.1))    # 下短横
        p.drawLine(QPointF(12.0, 9.4), QPointF(12.0, 14.6))    # 双箭头竖杆
        p.drawLine(QPointF(9.7, 11.9), QPointF(12.0, 9.4))     # 上箭头翼
        p.drawLine(QPointF(14.3, 11.9), QPointF(12.0, 9.4))
        p.drawLine(QPointF(9.7, 12.1), QPointF(12.0, 14.6))    # 下箭头翼
        p.drawLine(QPointF(14.3, 12.1), QPointF(12.0, 14.6))
    elif kind == "cols":                         # 双栏长截图：左右两页框 + 各自下滚箭头
        p.setPen(_pen(c, 1.7))
        p.drawRoundedRect(QRectF(3.5, 4.5, 7, 15), 1.6, 1.6)
        p.drawRoundedRect(QRectF(13.5, 4.5, 7, 15), 1.6, 1.6)
        _ap = _pen(c, 2.0)
        _ap.setCapStyle(Qt.RoundCap)
        _ap.setJoinStyle(Qt.RoundJoin)
        p.setPen(_ap)
        for _ax in (7.0, 17.0):                           # 两栏各自的下箭头
            p.drawLine(QPointF(_ax, 8.5), QPointF(_ax, 14.5))
            p.drawLine(QPointF(_ax - 2.1, 12.4), QPointF(_ax, 14.5))
            p.drawLine(QPointF(_ax + 2.1, 12.4), QPointF(_ax, 14.5))
    elif kind == "stop":                          # 停止：实心圆角方
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(7.6, 7.6, 8.8, 8.8), 1.8, 1.8)
    elif kind == "region":                        # 范围：裁切标记
        p.setPen(_pen(c, 2.0))
        cp = QPainterPath(QPointF(7.2, 3.4))
        cp.lineTo(7.2, 16.8)
        cp.lineTo(20.6, 16.8)
        p.drawPath(cp)
        cp2 = QPainterPath(QPointF(3.4, 7.2))
        cp2.lineTo(16.8, 7.2)
        cp2.lineTo(16.8, 20.6)
        p.drawPath(cp2)
    elif kind == "cursor":                        # 指针：鼠标箭头轮廓
        pp = QPainterPath(QPointF(7.4, 4.4))
        pp.lineTo(7.4, 18.6)
        pp.lineTo(10.9, 15.2)
        pp.lineTo(13.1, 19.7)
        pp.lineTo(15.3, 18.7)
        pp.lineTo(13.2, 14.3)
        pp.lineTo(17.9, 14.3)
        pp.closeSubpath()
        p.setPen(_pen(c, 1.8))
        p.setBrush(Qt.NoBrush)
        p.drawPath(pp)
    elif kind == "autoscroll":                   # 自动滚动：鼠标+滚轮下箭头（滚轮自己在转）
        p.setPen(_pen(c, 1.8))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(7.8, 3.6, 8.4, 16.8), 4.2, 4.2)
        apen = _pen(c, 2.0)
        apen.setCapStyle(Qt.RoundCap)
        apen.setJoinStyle(Qt.RoundJoin)
        p.setPen(apen)
        p.drawLine(QPointF(12.0, 6.2), QPointF(12.0, 9.0))
        p.drawLine(QPointF(10.5, 7.7), QPointF(12.0, 9.3))
        p.drawLine(QPointF(13.5, 7.7), QPointF(12.0, 9.3))
    elif kind == "manual":                       # 手动：上下双向箭头（自己来回滚）
        mpen = _pen(c, 2.0)
        mpen.setCapStyle(Qt.RoundCap)
        mpen.setJoinStyle(Qt.RoundJoin)
        p.setPen(mpen)
        p.setBrush(Qt.NoBrush)
        mp = QPainterPath(QPointF(9.6, 8.8))      # 上箭头
        mp.lineTo(12.0, 6.2)
        mp.lineTo(14.4, 8.8)
        p.drawPath(mp)
        p.drawLine(QPointF(12.0, 6.4), QPointF(12.0, 17.6))   # 主轴
        md = QPainterPath(QPointF(9.6, 15.2))     # 下箭头
        md.lineTo(12.0, 17.8)
        md.lineTo(14.4, 15.2)
        p.drawPath(md)


def icon(kind, color=STROKE, size=20, dpr=None):
    return QIcon(pixmap(kind, color, size, dpr))


# ---------- R133 lucide 线性图标管线（https://lucide.dev/，ISC 协议） ----------
# SVG inner 数据内嵌（24 画布 / stroke 2 / round cap&join），QSvgRenderer 按屏 DPR 渲染。
# 选用纪律见 design-system/jianjie-desktop/design.md §3。
_LUCIDE_TPL = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
               'fill="none" stroke="{color}" stroke-width="{w}" stroke-linecap="round" '
               'stroke-linejoin="round">{inner}</svg>')

LUCIDE = {
    "settings": '<path d="M9 3h6l.5 3 2 1.2 2.8-1 3 5.2-2.3 2-.1 2.3 2.4 1.8-3 5.2-2.8-1-2 1.2-.5 3H9l-.5-3-2-1.2-2.8 1-3-5.2 2.3-2 .1-2.3L.7 11.4l3-5.2 2.8 1 2-1.2z" transform="translate(2 0) scale(.84)"/><circle cx="12" cy="12" r="3"/>',
    "crosshair": '<circle cx="12" cy="12" r="7"/><path d="M12 2v6M12 16v6M2 12h6M16 12h6"/>',
    "mouse-pointer": '<path d="M4 3v17l4.7-4.5 3.1 6 3.2-1.6-3.1-6 6.4-.9z"/>',
    "hand": '<path d="M8 13V4a2 2 0 0 1 4 0v8-5a2 2 0 0 1 4 0v5-3a2 2 0 0 1 4 0v6c0 4-2.5 7-6 7h-2c-2 0-3.4-1.1-4.5-2.6L3 13.5a2 2 0 0 1 3-2.6L8 13z"/>',
    "toast-position": '<rect x="3" y="4" width="18" height="16" rx="2"/><rect x="12" y="12" width="6" height="5" rx="1"/>',
    "smartphone": '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
    "scissors": '<circle cx="6" cy="6" r="3"/><path d="M8.12 8.12 12 12"/><path d="M20 4 8.12 15.88"/><circle cx="6" cy="18" r="3"/><path d="M14.8 14.8 20 20"/>',
    "scroll": '<path d="M19 17V5a2 2 0 0 0-2-2H4"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/>',
    "monitor-play": '<path d="M15.033 9.44a.647.647 0 0 1 0 1.12l-4.065 2.352a.645.645 0 0 1-.968-.56V7.648a.645.645 0 0 1 .967-.56z"/><path d="M12 17v4"/><path d="M8 21h8"/><rect x="2" y="3" width="20" height="14" rx="2"/>',
    # R137：日历导航四箭头 lucide 化（程序化 chevl/chevr 系在小窗退役，样式统一；capture 工具条仍用 _draw 系不受影响）
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevrons-left": '<path d="m11 17-5-5 5-5"/><path d="m18 17-5-5 5-5"/>',
    "chevrons-right": '<path d="m6 17 5-5-5-5"/><path d="m13 17 5-5-5-5"/>',
    "pipette": '<path d="m12 9-8.414 8.414A2 2 0 0 0 3 18.828v1.344a2 2 0 0 1-.586 1.414A2 2 0 0 1 3.828 21h1.344a2 2 0 0 0 1.414-.586L15 12"/><path d="m18 9 .4.4a1 1 0 1 1-3 3l-3.8-3.8a1 1 0 1 1 3-3l.4.4 3.4-3.4a1 1 0 1 1 3 3z"/><path d="m2 22 .414-.414"/>',
    "scan-text": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 8h8"/><path d="M7 12h10"/><path d="M7 16h6"/>',
    "clipboard-list": '<rect width="8" height="4" x="8" y="2" rx="1" ry="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M12 11h4"/><path d="M12 16h4"/><path d="M8 11h.01"/><path d="M8 16h.01"/>',
    "film": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>',
    "paintbrush": '<path d="m14.622 17.897-10.68-2.913"/><path d="M18.376 2.622a1 1 0 1 1 3.002 3.002L17.36 9.643a.5.5 0 0 0 0 .707l.944.944a2.41 2.41 0 0 1 0 3.408l-.944.944a.5.5 0 0 1-.707 0L8.354 7.348a.5.5 0 0 1 0-.707l.944-.944a2.41 2.41 0 0 1 3.408 0l.944.944a.5.5 0 0 0 .707 0z"/><path d="M9 8c-1.804 2.71-3.97 3.46-6.583 3.948a.507.507 0 0 0-.302.819l7.32 8.883a1 1 0 0 0 1.185.204C12.735 20.405 16 16.792 16 15"/>',
    "qr-code": '<rect width="5" height="5" x="3" y="3" rx="1"/><rect width="5" height="5" x="16" y="3" rx="1"/><rect width="5" height="5" x="3" y="16" rx="1"/><path d="M21 16h-3a2 2 0 0 0-2 2v3"/><path d="M21 21v.01"/><path d="M12 7v3a2 2 0 0 1-2 2H7"/><path d="M3 12h.01"/><path d="M12 3h.01"/><path d="M12 16v.01"/><path d="M16 12h1"/><path d="M21 12v.01"/><path d="M12 21v-1"/>',
    "calendar-days": '<path d="M8 2v3"/><path d="M16 2v3"/><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M8 13h.01"/><path d="M12 13h.01"/><path d="M16 13h.01"/><path d="M8 17h.01"/><path d="M12 17h.01"/><path d="M16 17h.01"/>',
    "ellipsis": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "alarm-clock": '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M3 4l3-2M18 2l3 2M5 20l-2 2M19 20l2 2"/>',
    "trash-2": '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "timer": '<line x1="10" x2="14" y1="2" y2="2"/><line x1="12" x2="15" y1="14" y2="11"/><circle cx="12" cy="14" r="8"/>',
    "file-search": '<path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"/><path d="M14 2v5a1 1 0 0 0 1 1h5"/><circle cx="11.5" cy="14.5" r="2.5"/><path d="M13.3 16.3 15 18"/>',
    "list-todo": '<path d="M13 5h8"/><path d="M13 12h8"/><path d="M13 19h8"/><path d="m3 17 2 2 4-4"/><rect x="3" y="4" width="6" height="6" rx="1"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "monitor": '<rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>',
    "sun-moon": '<path d="M12 2v2"/><path d="M14.837 16.385a6 6 0 1 1-7.223-7.222c.624-.147.97.66.715 1.248a4 4 0 0 0 5.26 5.259c.589-.255 1.396.09 1.248.715"/><path d="M16 12a4 4 0 0 0-4-4"/><path d="m19 5-1.256 1.256"/><path d="M20 12h2"/>',
    "keyboard": '<path d="M10 8h.01"/><path d="M12 12h.01"/><path d="M14 8h.01"/><path d="M16 12h.01"/><path d="M18 8h.01"/><path d="M6 8h.01"/><path d="M7 16h10"/><path d="M8 12h.01"/><rect width="20" height="16" x="2" y="4" rx="2"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "pin": '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
    "pencil": '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "flag": '<path d="M4 22V4a1 1 0 0 1 .4-.8A6 6 0 0 1 8 2c3 0 5 2 7.333 2q2 0 3.067-.8A1 1 0 0 1 20 4v10a1 1 0 0 1-.4.8A6 6 0 0 1 16 16c-3 0-5-2-8-2a6 6 0 0 0-4 1.528"/>',
}

_lucide_cache = {}


def lucide(name, color, size=20, dpr=None, sw=2.0):
    """lucide 线性图标：SVG inner + QSvgRenderer 按屏 DPR 渲染（同 pixmap() 的 DPR 纪律）。"""
    if dpr is None:
        scr = QGuiApplication.primaryScreen()
        dpr = (scr.devicePixelRatio() if scr else 1.0) or 1.0
    dpr = dpr or 1.0
    key = (name, color, size, dpr, sw)
    if key in _lucide_cache:
        return _lucide_cache[key]
    svg = _LUCIDE_TPL.format(color=color, w=sw, inner=LUCIDE[name])
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = _painter(pm)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    _lucide_cache[key] = pm
    return pm


def lucide_icon(name, color, size=20, dpr=None, sw=2.0):
    return QIcon(lucide(name, color, size, dpr, sw))


def tool_button(kind, tip, cb, color=None, checkable=False, w=34, h=30, dpr=None, lucide=None):
    """图标按钮工厂：录屏/长截图/GIF 控制条与截图工具条共用同一套视觉。
    R137：lucide 参数——传 lucide 名走 lucide 管线（sw 2.4 半档加粗，细折线箭头类补偿），小窗图标同源。"""
    b = QPushButton()
    b.setFixedSize(w, h)
    b.setIconSize(QSize(20, 20))
    b.setIcon(lucide_icon(lucide, color or STROKE, 20, dpr, sw=2.4) if lucide
              else icon(kind, color or STROKE, 20, dpr))
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setCheckable(checkable)
    b.setStyleSheet(
        "QPushButton{border:none;border-radius:8px;padding:0px;background:transparent;}"
        "QPushButton:hover{background:rgba(255,255,255,30);}"
        "QPushButton:checked{background:rgba(59,130,246,120);}"
        "QPushButton:pressed{background:rgba(255,255,255,50);}")
    b.clicked.connect(cb)
    return b


class PulseDot(QWidget):
    """状态呼吸点：激活时红色呼吸，空闲灰色常亮（替代字体「●」，像素清晰）。"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(14, 14)
        self._on = False
        self._ph = 0.0
        self._t = QTimer(self)
        self._t.setInterval(110)
        self._t.timeout.connect(self._beat)

    def set_active(self, on):
        if on == self._on:
            return
        self._on = on
        if on:
            self._t.start()
        else:
            self._t.stop()
        self.update()

    def _beat(self):
        self._ph += 0.55
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._on:
            col = QColor("#ef4444")
            col.setAlphaF(0.4 + 0.6 * (0.5 + 0.5 * math.sin(self._ph)))
            # R144 激活态外圈线形圆环（用户点名「单用一个小红点有点单调，给小红点外层
            # 加一个线形的小圆边框」）——环恒显不呼吸（呼吸由点承担），圈+点＝REC 灯经典造型
            ring = QColor("#ef4444")
            ring.setAlpha(210)
            p.setPen(QPen(ring, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(self.width() / 2, self.height() / 2),
                          (self.width() - 2.4) / 2, (self.height() - 2.4) / 2)
        else:
            col = QColor("#6b7280")
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(self.width() / 2, self.height() / 2), 4.0, 4.0)
        p.end()
