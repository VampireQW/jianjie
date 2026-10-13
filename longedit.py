# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 长截图编辑器：滚动画布 + 截图工具条同款精简标注。

工具集：矩形/椭圆/箭头/画笔/荧光笔/文字/序号/马赛克/高光（交互同截图侧：
马赛克笔刷化一划成片、文字点哪儿写哪儿+双击再编辑、序号右键删除自动重排）；
撤销重做 + 六色 + 三档粗细。渲染契约直接复用 capture.ShotOverlay 的 _paint_shape
（同一份对象字典、同一个绘制函数），完成＝全分辨率渲染落盘并回调主流程进剪贴板。
坐标约定：标注对象全部存「图坐标」；画布只按 k=显示/图 缩放呈现，导出零缩放逐像素。"""
import math

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon,
                           QImage, QPainter, QPainterPath, QPen, QPixmap, QTextCursor)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

import capture
import common as C
import icons
import widgets as W
from common import pictures_dir, timestamp

SIZES = [(2, "细"), (4, "中"), (7, "粗")]


def _clone_stamp(s):
    """条目浅拷贝 + 内部容器换新（撤销栈各持一份，互不共享可变对象）。"""
    c = dict(s)
    if c.get("kind") == "stroke":
        c["pts"] = [QPoint(p) for p in c["pts"]]
    elif "tiles" in c:
        c["tiles"] = list(c["tiles"])
    elif "r" in c:
        c["r"] = QRect(c["r"])
    return c


class LongCanvas(QWidget):
    """长图画布：整图按宽缩放显示（超高滚动），鼠标事件换算成图坐标进出。"""

    def __init__(self, arr, view_w):
        super().__init__(None)
        h, w = arr.shape[:2]
        self._img_w, self._img_h = w, h
        self._base = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self._s = min(4.0, max(0.8, w / 1080.0))       # 粗细/字号/网格随图宽等比（1080 基准同截图）
        self._k = min(1.0, max(0.05, (view_w - 48) / max(w, 1)))
        self.setFixedSize(round(w * self._k), round(h * self._k))
        # R86 底图缓存：k 缩放一次性付清；paintEvent 纯 blit——超高图（如 1920×35000）
        # 每次滚动/hover 重绘都对全图重采样，单帧数百 ms，是长图编辑器卡顿主因。
        # 先缩放 RGB 底稿，只转换预览尺寸，避免额外生成全尺寸 QPixmap。
        self._base_pm = QPixmap.fromImage(self._base.scaled(
            self.width(), self.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        self.sel = QRect(0, 0, self.width(), self.height())   # 复用 _fit_anno_editor 的边界
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)   # R76：悬浮 hover 光标前提——不开此项 Qt 只在按住键时才发 move 事件
        self.stamps = []
        self._nums = []                                 # 序号层（图坐标，同截图侧对象结构）
        self.undo_stack = [([], [])]                    # 快照式（stamps,_nums）：每动作前入栈，栈底＝初始空
        self.redo_stack = []
        self.cur_tool = "rect"
        self.cur_color = capture.COLORS[0]
        self.cur_size = 2
        # R100 标注属性（与截图侧同名同语义；_paint_nums/_paint_shape 委托 capture 版时按 self 解析）
        self.cur_num_style = "num"         # 序号样式：num/upper/lower/uroman/lroman
        self.cur_num_start = 1             # 序号起始（1~9）
        self.cur_high_shape = "rect"       # 聚光形态：rect/ellipse
        self.cur_arrow_style = "line"      # R134 箭头样式同步截图侧：line 经典线箭 / solid 实心锥形
        self.cur_high_dim = capture.HIGH_DIM_DEFAULT   # R102：聚光遮罩暗度（空处单击循环加深）
        self.stroke_pts = []
        self._drawing = False
        self._live = None
        self._mosa_cache = None                         # 马赛克增量缓存（同截图侧）
        self.text_editor = None
        self.text_edit = None
        self._text_edit_idx = None                # 再编辑中的文字对象下标：浮层接管期间底层不画（R79 防重影，同截图侧）
        self._num_edit_idx = None                 # R104：再编辑中的序号下标（附言让位防重影；圆标保留）
        self.sel_stamp = None                         # 选中标注 (tag, i)：tag="num"/"shape"（同截图侧）
        self._drag_num = None
        self._drag_shape = None
        self._shape_orig = None
        self._drag_pos0 = None
        self._drag_off = None
        self._drag_moved = False
        self._rs = None                               # 选中标注的 8 向缩放会话

    # ---- 对象化交互借用截图侧（类体绑定：方法内 self 多态解析到本类属性与覆写）----
    _seg_dist = capture.ShotOverlay._seg_dist
    _stamp_bbox = capture.ShotOverlay._stamp_bbox
    _sel_bbox = capture.ShotOverlay._sel_bbox
    _hit_shape = capture.ShotOverlay._hit_shape
    _move_shape = capture.ShotOverlay._move_shape
    _map_pt = capture.ShotOverlay._map_pt
    _begin_stamp_resize = capture.ShotOverlay._begin_stamp_resize
    _handle_drag_rect = capture.ShotOverlay._handle_drag_rect
    _select_and_drag = capture.ShotOverlay._select_and_drag
    _delete_selected = capture.ShotOverlay._delete_selected
    _sel_scalable = capture.ShotOverlay._sel_scalable   # R74：选中态分级门槛（文字/序号不可缩放）
    _delete_hover = capture.ShotOverlay._delete_hover   # R78：悬浮即删（命中走本类 _hit_num/_hit_text/_hit_shape）
    _tool_idle_cursor = capture.ShotOverlay._tool_idle_cursor   # R84：空白光标随工具（无副作用）
    _num_label = capture.ShotOverlay._num_label         # R100：序号样式标号（_paint_nums 委托链内部按 self 解析）
    _num_note_rect = capture.ShotOverlay._num_note_rect  # R104：附言命中矩形（capture 版 _hit_num 内部按 self 调用）
    _arrow_solid = capture.ShotOverlay._arrow_solid      # R134：实心箭头（_paint_shape 委托链按 self 解析）
    # R100：_num_label 内部按 self 解析 _roman/_alpha，须随绑；
    # 源是 staticmethod，取出后是裸函数，须重新包 staticmethod 否则 self 被当首参传入
    _roman = staticmethod(capture.ShotOverlay._roman)
    _alpha = staticmethod(capture.ShotOverlay._alpha)

    def _clone_entry(self, s):
        return _clone_stamp(s)                        # 借用链克隆重名适配（QPoint 只整体替换不原地改，浅共享安全）

    def _rerender_anno(self):
        """截图侧 stamps 画进 anno 位图须重渲；长图 paintEvent 现画无此层——适配借用链的空实现。"""

    def _resize_to_bbox(self, s, ob, nb):
        """标注从原包围盒 ob 映射到新包围盒 nb（同截图侧算法；字号 clamp 随图宽 _s，
        截图侧的 8~72 是逻辑坐标——大图 _s=4 时字号上限 76，照搬会把文字钳小）。"""
        s = self._clone_entry(s)
        k = s["kind"]
        if k in ("rect", "ellipse", "high"):
            s["r"] = QRect(nb)
        elif k == "text":
            ratio = min(nb.width() / max(1, ob.width()), nb.height() / max(1, ob.height()))
            s["fs"] = max(round(8 * self._s), min(round(72 * self._s), round(s["fs"] * ratio)))
            fm = QFontMetrics(QFont("Microsoft YaHei UI", s["fs"], QFont.Bold))
            w = max((fm.horizontalAdvance(ln) for ln in s["lines"]), default=0)
            s["asc"], s["lh"] = fm.ascent(), fm.lineSpacing()
            s["r"] = QRect(nb.left(), nb.top(), w + 2, fm.lineSpacing() * len(s["lines"]) + 2)
        elif k == "arrow":
            s["a"] = self._map_pt(s["a"], ob, nb)
            s["b"] = self._map_pt(s["b"], ob, nb)
            if s.get("c") is not None:                   # R100：控制点同比例映射（同截图侧）
                s["c"] = self._map_pt(s["c"], ob, nb)
        elif k == "stroke":
            s["pts"] = [self._map_pt(p, ob, nb) for p in s["pts"]]
            s["w"] = max(1, round(s["w"] * min(nb.width() / max(1, ob.width()),
                                                nb.height() / max(1, ob.height()))))
        elif k == "mosaic":
            bs = s["bs"]
            s["tiles"] = [
                (nb.left() + round((tx - ob.left()) / max(1, ob.width()) * nb.width() / bs) * bs,
                 nb.top() + round((ty - ob.top()) / max(1, ob.height()) * nb.height() / bs) * bs,
                 col)
                for tx, ty, col in s["tiles"]]
        return s

    def _apply_stamp_resize(self, pos):
        """选中标注缩放（同截图侧；序号半径 clamp 随图宽 _s，截图侧 6~60 是逻辑坐标）。"""
        rs = self._rs
        if rs["handle"] in ("a", "b", "m") and self.sel_stamp[0] == "shape" \
                and self.stamps[self.sel_stamp[1]]["kind"] == "arrow":
            if not rs["pushed"]:
                self.push_undo()               # 首次实际拖动才记快照
                rs["pushed"] = True
            s = self._clone_entry(rs["snap"])
            if rs["handle"] == "m":            # R100：中点手柄＝拉曲线；拖回中点恢复直线（同截图侧）
                tol_m = 6.0 / self._k
                mx = (s["a"].x() + s["b"].x()) / 2
                my = (s["a"].y() + s["b"].y()) / 2
                if math.hypot(pos.x() - mx, pos.y() - my) <= tol_m:
                    s.pop("c", None)
                else:
                    s["c"] = QPoint(pos)
            else:
                old = QPoint(s[rs["handle"]])
                s[rs["handle"]] = QPoint(pos)      # R74 对齐 R72：箭头端点手柄＝直接改端点（非包围盒缩放）
                if s.get("c") is not None:         # 弯箭头拖端点：控制点随位移一半漂移，弯势保持
                    s["c"] = QPoint(round(s["c"].x() + (pos.x() - old.x()) / 2),
                                    round(s["c"].y() + (pos.y() - old.y()) / 2))
            self.stamps[self.sel_stamp[1]] = s
            self.update()
            return
        nb = self._handle_drag_rect(rs["handle"], pos)
        if nb.width() < 10 and nb.height() < 10:
            return                         # 两维都太小才拒绝：细线（高度≈1 的笔画）也要能沿长
        if not rs["pushed"]:
            self.push_undo()               # 首次实际缩放才记快照
            rs["pushed"] = True
        if self.sel_stamp[0] == "num":
            n = self._clone_entry(rs["snap"])
            ratio = min(nb.width() / rs["ob"].width(), nb.height() / rs["ob"].height())
            n["r"] = max(round(6 * self._s), min(round(60 * self._s), round(n["r"] * ratio)))
            self._nums[self.sel_stamp[1]] = n
        else:
            self.stamps[self.sel_stamp[1]] = self._resize_to_bbox(rs["snap"], rs["ob"], nb)
        self.update()

    def _hit_one_shape(self, s, pos):
        """图坐标命中：容差 ÷k＝显示等效（截图侧容差即显示像素，长图坐标系大 1/k 倍）。"""
        u = 1.0 / self._k
        k = s["kind"]
        if k == "stroke":
            return any(self._seg_dist(pos, s["pts"][i - 1], s["pts"][i]) <= s["w"] / 2 + 5 * u
                       for i in range(1, len(s["pts"])))
        if k == "arrow":
            # R74 对齐 R73：容差放宽到 w/2+8（显示等效 ÷k）——悬到线段上即给十字
            tol = s["w"] / 2 + 8 * u
            c = s.get("c")                      # R100：弯箭头按贝塞尔采样折线命中（同截图侧）
            if c is not None:
                pts = capture.ShotOverlay._quad_pts(s["a"], c, s["b"])
                return any(self._seg_dist(pos, pts[i - 1], pts[i]) <= tol
                           for i in range(1, len(pts)))
            return self._seg_dist(pos, s["a"], s["b"]) <= tol
        if k == "ellipse":
            # R74 对齐 R73：按椭圆弧本身命中（此前用矩形边框环，弧斜部悬不中）
            r = s["r"]
            rx, ry = max(1.0, r.width() / 2), max(1.0, r.height() / 2)
            cx, cy = r.left() + rx, r.top() + ry
            d = math.hypot((pos.x() - cx) / rx, (pos.y() - cy) / ry)
            return abs(d - 1) * min(rx, ry) <= 6 * u + self.cur_size
        if k == "rect":
            tol = round(6 * u + self.cur_size)
            outer = QRect(s["r"]).adjusted(-tol, -tol, tol, tol)
            inner = QRect(s["r"]).adjusted(tol, tol, -tol, -tol)
            return outer.contains(pos) and not inner.contains(pos)   # 边框环命中；内部留给新形状
        if k == "high":
            if s.get("shape") == "ellipse":            # R100：椭圆聚光按填充区命中（同截图侧）
                r = s["r"]
                rx, ry = max(1.0, r.width() / 2), max(1.0, r.height() / 2)
                d = math.hypot((pos.x() - (r.left() + rx)) / rx, (pos.y() - (r.top() + ry)) / ry)
                return d <= 1.0
            return QRect(s["r"]).contains(pos)
        if k == "text":
            return QRect(s["r"]).contains(pos)
        if k == "mosaic":
            bs = s["bs"]
            return any(QRect(tx, ty, bs, bs).contains(pos) for tx, ty, _ in s["tiles"])
        return False

    def _stamp_handles(self):
        if not self.sel_stamp or not self._sel_scalable():
            return {}                          # R74 对齐：文字/序号无缩放手柄（行为与视觉一致）
        tag, i = self.sel_stamp
        if tag == "shape" and self.stamps[i]["kind"] == "arrow":
            s = self.stamps[i]                 # R104：单一样式恒三点（两端点＋中点，拖中点拉弯）
            m = s.get("c") or QPoint((s["a"].x() + s["b"].x()) // 2,
                                     (s["a"].y() + s["b"].y()) // 2)
            return {"a": s["a"], "m": m, "b": s["b"]}
        return capture.ShotOverlay._handle_pts(self, self._sel_bbox())

    def _hit_stamp_handle(self, pos):
        tol = 6.0 / self._k                # 手柄 6 显示 px 的图坐标命中半径
        for name, pt in self._stamp_handles().items():
            if abs(pos.x() - pt.x()) <= tol and abs(pos.y() - pt.y()) <= tol:
                return name
        return None

    def _draw_stamp_sel(self, p):
        """选中标注：绿虚线框 + 白手柄（只上屏不进导出），尺寸 ÷k＝显示等效。
        R104：箭头选中不画方框——两端点＋中点三个圆点连成一线，拖拽语义不变。"""
        u = 1.0 / self._k
        tag, i = self.sel_stamp
        is_arrow = tag == "shape" and self.stamps[i]["kind"] == "arrow"
        if not is_arrow:
            p.setPen(QPen(QColor(capture.SEL_GREEN), max(1.0, 1.2 * u), Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(self._sel_bbox()).adjusted(-2 * u, -2 * u, 2 * u, 2 * u))
        p.setPen(QPen(QColor(capture.SEL_GREEN), max(1.0, 1.2 * u)))
        p.setBrush(QColor("#ffffff"))
        hb = 3 * u
        for pt in self._stamp_handles().values():
            if is_arrow:
                p.drawEllipse(QPointF(pt), 4 * u, 4 * u)   # R104：箭头选中＝三点圆点，不画方框
            else:
                p.drawRect(QRectF(pt.x() - hb, pt.y() - hb, hb * 2, hb * 2))

    def _num_del_pt(self, n):
        """× 删除手柄圆心：圆标右上角外侧（偏移 ÷k＝显示等效），不遮标号。"""
        u = 4.0 / self._k
        return QPoint(round(n["pt"].x() + n["r"] + u), round(n["pt"].y() - n["r"] - u))

    def _paint_num_del(self, p):
        """× 删除手柄：num 工具下可见（只上屏不进导出），尺寸 ÷k＝显示等效。"""
        u = 1.0 / self._k
        rr, dd = round(7 * u), round(3 * u)
        for n in self._nums:
            c = self._num_del_pt(n)
            p.setPen(QPen(QColor(0, 0, 0, 190), max(1, round(1 * u))))
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(c, rr, rr)
            p.setPen(QPen(QColor(43, 47, 56), max(1, round(2 * u)), Qt.SolidLine, Qt.RoundCap))
            p.drawLine(c.x() - dd, c.y() - dd, c.x() + dd, c.y() + dd)
            p.drawLine(c.x() - dd, c.y() + dd, c.x() + dd, c.y() - dd)

    def _hit_num_del(self, pos):
        """× 手柄命中（须先于圆标拖动检测），半径 ÷k＝9 显示 px。"""
        r9 = 9.0 / self._k
        for i in range(len(self._nums) - 1, -1, -1):
            c = self._num_del_pt(self._nums[i])
            dx, dy = pos.x() - c.x(), pos.y() - c.y()
            if dx * dx + dy * dy <= r9 * r9:
                return i
        return None

    # ---- 复用截图渲染契约（一份对象字典一份绘制代码，capture 冒烟即回归锁）----
    def _arrow(self, p, s, e, w, c=None):
        capture.ShotOverlay._arrow(self, p, s, e, w, c)   # R104：单一样式（经典），签名随 capture

    def _arrow_solid(self, p, s, e, w, c=None):
        # R134：实心箭头（_paint_shape 借用链内部按 self 解析本方法，缺则画 solid 即崩）；R142：c 透传（弯锥）
        capture.ShotOverlay._arrow_solid(self, p, s, e, w, c)

    def _high_dim_rect(self):
        """R121：压暗覆盖范围＝整图（图坐标）。capture 版用全屏部件尺寸，
        长图导出无 scale 时只盖左上角——本覆写让显示与导出共用整图范围。"""
        return QRect(0, 0, self._img_w, self._img_h)

    # R121 聚光并集原语委托（LongCanvas 非继承 ShotOverlay，方法级借用须逐个委托；
    # _paint_high_dim 内部按 self 回调 _fill_high_dim/_high_dim_rect，缺一不可）
    def _fill_high_dim(self, p, regs, dim):
        return capture.ShotOverlay._fill_high_dim(self, p, regs, dim)

    def _paint_high_dim(self, p, stamps, live=None):
        return capture.ShotOverlay._paint_high_dim(self, p, stamps, live)

    def _draw_high_border(self, p, r, shape="rect"):
        return capture.ShotOverlay._draw_high_border(self, p, r, shape)

    def _draw_highlight(self, p, r, shape="rect", dim=capture.HIGH_DIM_DEFAULT):
        return capture.ShotOverlay._draw_highlight(self, p, r, shape, dim)

    def _draw_one_num(self, p, pos, tpos, label, text, color, r):
        return capture.ShotOverlay._draw_one_num(self, p, pos, tpos, label, text, color, r)

    def _paint_nums(self, p):
        capture.ShotOverlay._paint_nums(self, p)   # 序号层对象化现画：标号=顺序+1，删除自动重排

    def _hit_num(self, pos):
        return capture.ShotOverlay._hit_num(self, pos)

    def _hit_text(self, pos):
        return capture.ShotOverlay._hit_text(self, pos)

    def _smooth_path(self, pts):
        return capture.ShotOverlay._smooth_path(pts)   # 静态方法：画笔曲线拟合与截图侧同一实现（必须返回路径）

    def _layout_lines(self, text, font, width):
        return capture.ShotOverlay._layout_lines(self, text, font, width)

    def _natural_width(self, t):
        return capture.ShotOverlay._natural_width(self, t)

    def _fit_anno_editor(self, t):
        capture.ShotOverlay._fit_anno_editor(self, t)

    # ---- 坐标换算 ----
    def _to_img(self, pos):
        return QPoint(round(pos.x() / self._k), round(pos.y() / self._k))

    # ---- 马赛克（笔刷化：与截图侧同款算法，坐标换图坐标） ----
    def _mosaic_bs(self):
        """马赛克网格（图坐标）：随截图侧粗细档位（细 4/中 7/粗 11），再按图宽等比。"""
        return max(3, round(capture.ShotOverlay._mosaic_bs(self) * self._s))

    def _mosaic_brush_r(self):
        """马赛克笔刷半径（图坐标）：随截图侧档位再按图宽等比，一划覆盖成片。"""
        return (8 + self.cur_size * 4) * self._s       # 细/中/粗 → 16/24/36 ×_s

    def _mosaic_tiles(self, pts):
        """笔刷半径内覆盖到的网格（去重），每格取底图均色（图坐标，算法同截图侧）。
        圆形刷头：格中心距路径 ≤ 半径即入刷；路径按 bs/2 步进插值，快速拖动不漏格
        ——此前逐采样点取格，一划只有点线，用户要来回涂很多次才盖住一片。
        增量缓存：拖动中每次只算新段。"""
        bs, img = self._mosaic_bs(), self._base
        rad = self._mosaic_brush_r()
        cache = self._mosa_cache
        if cache is not None and cache[0] == id(pts) and cache[1] <= len(pts):
            start, out, seen = cache[1] - 1, cache[2], set(cache[3])
        else:
            start, out, seen = 0, [], set()
        if not pts:
            return out
        half = bs / 2.0
        r2 = rad * rad
        iw, ih = self._img_w, self._img_h

        def emit(px, py):
            for ty in range(int(math.floor((py - rad) / bs)) * bs,
                            int(math.floor((py + rad) / bs)) * bs + 1, bs):
                for tx in range(int(math.floor((px - rad) / bs)) * bs,
                                int(math.floor((px + rad) / bs)) * bs + 1, bs):
                    if (tx, ty) in seen:
                        continue
                    dx = tx + half - px
                    dy = ty + half - py
                    if dx * dx + dy * dy > r2:
                        continue
                    seen.add((tx, ty))
                    x0 = min(max(tx, 0), max(iw - 1, 0))
                    y0 = min(max(ty, 0), max(ih - 1, 0))
                    r = g = b = n = 0
                    for yy in range(y0, min(y0 + bs, ih)):
                        for xx in range(x0, min(x0 + bs, iw)):
                            c = img.pixelColor(xx, yy)
                            r += c.red()
                            g += c.green()
                            b += c.blue()
                            n += 1
                    if n:
                        out.append((tx, ty, QColor(r // n, g // n, b // n).name()))

        if start == 0:
            emit(pts[0].x(), pts[0].y())
        for i in range(max(start, 1), len(pts)):
            a, bp = pts[i - 1], pts[i]
            dist = math.hypot(bp.x() - a.x(), bp.y() - a.y())
            steps = max(1, int(dist // (bs / 2)))
            for s_ in range(1, steps + 1):
                f = s_ / steps
                emit(a.x() + (bp.x() - a.x()) * f, a.y() + (bp.y() - a.y()) * f)
        self._mosa_cache = (id(pts), len(pts), out, list(seen))
        return out

    def _live_stamp(self):
        if not self.stroke_pts:
            return None
        t = self.cur_tool
        if t in ("pen", "marker"):
            if len(self.stroke_pts) < 2:
                return None
            return {"kind": "stroke", "pts": [QPoint(p) for p in self.stroke_pts],
                    "color": self.cur_color,
                    "w": max(2, round(self.cur_size * 6 * self._s)) if t == "marker"
                         else max(1, round(self.cur_size * self._s)),
                    "op": 0.35 if t == "marker" else None}
        if t == "mosaic":
            tiles = self._mosaic_tiles(self.stroke_pts)
            return {"kind": "mosaic", "tiles": tiles, "bs": self._mosaic_bs()} if tiles else None
        if t in ("text", "num"):
            return None                       # 文字/序号走浮层或落标，不进拖拽管线
        r = QRect(self.stroke_pts[0], self.stroke_pts[-1]).normalized()
        if r.isNull():
            return None
        if t == "high":
            # R102：近点单击不落对象（小点痕迹），松手转暗度循环；
            # 补 R100 遗漏：shape 键此前从未进 live 路径，椭圆形态钮对新画聚光不生效
            if r.width() < 8 and r.height() < 8:
                return None
            return {"kind": "high", "r": QRect(r), "shape": self.cur_high_shape,
                    "dim": self.cur_high_dim}   # 高光无颜色/笔宽（同截图侧对象）
        if t == "arrow":
            if self.stroke_pts[0] == self.stroke_pts[-1]:
                return None                   # 原地单击不成箭头
            return {"kind": "arrow", "a": QPoint(self.stroke_pts[0]),
                    "b": QPoint(self.stroke_pts[-1]), "r": QRect(r),
                    "color": self.cur_color, "w": max(2, round((self.cur_size + 1) * self._s)),
                    "style": self.cur_arrow_style}      # R134 样式随对象（同截图侧）
        return {"kind": t, "r": QRect(r), "color": self.cur_color,
                "w": max(2, round((self.cur_size + 1) * self._s))}

    # ---- 绘制 ----
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawPixmap(0, 0, self._base_pm)      # R86：底图设备坐标 1:1 blit（零采样，Qt 只拷可见区）
        p.save()
        p.scale(self._k, self._k)              # 标注层仍走图坐标
        for _i, s in enumerate(self.stamps):
            if _i == self._text_edit_idx and s["kind"] == "text":
                continue                          # 再编辑中：浮层在画，底层这帧让位（R79 防重影）
            if s["kind"] == "high":
                continue                          # R121：聚光不逐对象画——并集压暗+边框统一在后
            capture.ShotOverlay._paint_shape(self, p, s)
        self._paint_high_dim(p, self.stamps,
                             self._live if self._drawing else None)   # R121：并集一次压暗
        for s in self.stamps:
            if s["kind"] == "high":
                self._draw_high_border(p, s["r"], s.get("shape", "rect"))
        if self._nums:
            self._paint_nums(p)               # 序号层对象化现画（同截图侧）
        if self.cur_tool == "num":
            self._paint_num_del(p)            # × 删除手柄只上屏，不进导出（同截图侧）
        if self.sel_stamp and self._sel_scalable():
            self._draw_stamp_sel(p)           # 选中框只给可缩放对象（R74 对齐 R71：文字/序号无框）
        if self._drawing and self._live is not None:
            capture.ShotOverlay._paint_shape(self, p, self._live)   # high live＝纯边框（洞已并入并集）
        p.restore()
        p.end()

    # ---- 鼠标（图坐标；交互同截图侧）----
    def mousePressEvent(self, ev):
        pos = self._to_img(ev.position().toPoint())
        if ev.button() == Qt.RightButton and hasattr(self.window(), "_cancel_like_escape"):
            self.window()._cancel_like_escape()
            ev.accept()
            return
        if ev.button() == Qt.RightButton:
            had_edit = self.text_editor is not None or self.text_edit is not None
            self.commit_text_editor()
            self.commit_text_input()
            if had_edit:
                return                     # 失焦那一下只落字：不顺手删刚落的标注（同截图侧）
            hit = self._hit_num(pos)
            if hit is not None:
                self.push_undo()
                del self._nums[hit]           # 右键圆标删除；标号按剩余顺序自动重排
                self.sel_stamp = None
                self.update()
                return
            hit = self._hit_shape(pos)
            if hit is not None:            # 右键形状/文字/马赛克＝删除（同截图侧）
                self.push_undo()
                del self.stamps[hit]
                self.sel_stamp = None
                self.update()
            return
        if ev.button() != Qt.LeftButton:
            return
        had_edit = self.text_editor is not None or self.text_edit is not None
        self.commit_text_editor()          # 文字浮层：点任意处＝落字（失焦即提交，同截图侧）
        self.commit_text_input()           # 序号附言：点任意处＝落字（失焦即提交，同截图侧）
        if had_edit:
            return                         # 失焦那一下只落字：同一击不再继续，再点一下才行
        if self.cur_tool == "num":
            dx = self._hit_num_del(pos)
            if dx is not None:             # 点 × 手柄＝删除（先于选中手柄：两者相距仅几像素）
                self.push_undo()
                del self._nums[dx]
                self.sel_stamp = None
                self.update()
                return
        h = self._hit_stamp_handle(pos)    # 选中标注的 8 向缩放手柄
        if h is not None:
            self._rs = self._begin_stamp_resize(h)
            self.setCursor(Qt.SizeAllCursor)   # 与截图侧统一：按住拖拽＝十字移动（R72 用户否决握拳）
            return
        if self.cur_tool == "num":
            hit = self._hit_num(pos)
            if hit is not None:            # 按在已有圆标上＝选中拖动（圆标+附言整体跟手）
                self._select_and_drag("num", hit, pos)
                return
            hit = self._hit_shape(pos)     # R99：按在文字/形状上＝拖动它（同截图侧：对象优先，
            if hit is not None:            # 不再误当空白又落新标——标完序号按住文本拖不动的根因）
                self._select_and_drag("shape", hit, pos)
                return
            self.sel_stamp = None          # 落新标＝旧选中失焦（同 select 点空白）
            self.update()
            self.show_num_input(pos)
            return
        if self.cur_tool == "select":
            hit = self._hit_num(pos)
            if hit is not None:
                self._select_and_drag("num", hit, pos)
                return
            hit = self._hit_shape(pos)
            if hit is not None:
                self._select_and_drag("shape", hit, pos)
                return
            self.sel_stamp = None          # 选择工具点空白＝取消选中
            self.update()
            return
        if self.cur_tool == "text":
            hit = self._hit_text(pos)
            if hit is not None:            # 文字工具点到已有文字＝选中拖动
                self._select_and_drag("shape", hit, pos)
                return
            hit = self._hit_num(pos)       # R99：按在圆标/形状上＝拖动它（与 num 工具对称）
            if hit is not None:
                self._select_and_drag("num", hit, pos)
                return
            hit = self._hit_shape(pos)
            if hit is not None:
                self._select_and_drag("shape", hit, pos)
                return
            self.sel_stamp = None          # 开新文字浮层＝旧选中失焦
            self.update()
            self.show_text_editor(pos)
            return
        if self.cur_tool in ("rect", "ellipse", "arrow", "high"):
            hit = self._hit_shape(pos)
            if hit is not None:            # 形状工具按在已有形状边框上＝选中拖动
                self._select_and_drag("shape", hit, pos)
                return
        self.sel_stamp = None              # 起笔画新元素＝旧选中失焦（rect/ellipse/arrow/pen/mosaic 共用落点）
        self.update()
        self.stroke_pts = [pos]
        self._drawing = True
        self._live = None
        self._mosa_cache = None            # 新笔画：增量缓存重置（同截图侧）
        self.update()

    def mouseDoubleClickEvent(self, ev):
        pos = self._to_img(ev.position().toPoint())
        hit = self._hit_text(pos)
        if hit is not None:
            s = self.stamps[hit]
            self.show_text_editor(QPoint(s["r"].left(), s["r"].top()),
                                  preset="\n".join(s["lines"]), idx=hit,
                                  fs=s["fs"], color=s["color"])   # 双击文字＝原地再编辑（原字号/颜色，同截图侧）
            return
        hit = self._hit_num(pos)           # R104：双击序号（圆标或附言）＝附言原地再编辑
        if hit is not None:
            self._reedit_num(hit)
            return
        super().mouseDoubleClickEvent(ev)

    def mouseMoveEvent(self, ev):
        pos = self._to_img(ev.position().toPoint())
        self._hover_pt = pos               # R78：记录悬浮点（图坐标），Backspace/Delete 悬浮即删用
        if self._drag_num is not None:
            if not self._drag_moved:
                self.push_undo()           # 首次实际移动才记快照：只点击不拖不占撤销步
                self._drag_moved = True
            n = self._nums[self._drag_num]
            n["pt"] = QPoint(pos.x() - self._drag_off.x(), pos.y() - self._drag_off.y())
            self.update()
            return
        if self._drag_shape is not None:
            if not self._drag_moved:
                self.push_undo()
                self._drag_moved = True
            self.stamps[self._drag_shape] = self._move_shape(
                self._shape_orig, pos - self._drag_pos0)   # 每次从快照重建，不原地累积
            self.update()
            return
        if self._rs is not None:
            self._apply_stamp_resize(pos)  # 选中标注的 8 向缩放（内部已 update）
            return
        if self._drawing:
            if self.stroke_pts and pos == self.stroke_pts[-1]:
                return
            self.stroke_pts.append(pos)
            self._live = self._live_stamp()
            self.update()
            return
        # 悬停光标（无拖拽/绘制中）：R74 对齐 R72——全工具覆盖且与 press 可拖范围一一对应
        h = self._hit_stamp_handle(pos)
        if h:
            self.setCursor(capture.ShotOverlay._SEL_CURSORS.get(h, Qt.SizeAllCursor))  # 端点无方向→十字兜底
        elif self.cur_tool == "num":
            if self._hit_num_del(pos) is not None:
                self.setCursor(Qt.PointingHandCursor)        # ×钮＝删除手型
            else:
                # R99：圆标与文字/形状都可拖＝十字（与 press 可拖范围一一对应）
                on = self._hit_num(pos) is not None or self._hit_shape(pos) is not None
                self.setCursor(Qt.SizeAllCursor if on else self._tool_idle_cursor())
        elif self.cur_tool == "select":
            on = self._hit_num(pos) is not None or self._hit_shape(pos) is not None
            self.setCursor(Qt.SizeAllCursor if on else self._tool_idle_cursor())  # R84
        elif self.cur_tool == "text":
            # R99：文字/圆标/形状都可拖（与 press 泛化同步）
            on = (self._hit_text(pos) is not None or self._hit_num(pos) is not None
                  or self._hit_shape(pos) is not None)
            self.setCursor(Qt.SizeAllCursor if on else self._tool_idle_cursor())  # R84：空白 I 型
        elif self.cur_tool in ("rect", "ellipse", "arrow", "high"):
            self.setCursor(Qt.SizeAllCursor if self._hit_shape(pos) is not None else self._tool_idle_cursor())  # R84：空白纯十字
        else:
            self.setCursor(self._tool_idle_cursor())  # pen/marker/mosaic 不可拖；R84 绘制类＝纯十字

    def mouseReleaseEvent(self, ev):
        self.setCursor(self._tool_idle_cursor())  # R84 拖拽结束随工具恢复；悬停由后续 move 重设
        if self._drag_num is not None:
            self._drag_num = None
            return
        if self._drag_shape is not None:
            self._drag_shape = None
            self._shape_orig = None
            return
        if self._rs is not None:
            self._rs = None
            return
        if ev.button() != Qt.LeftButton or not self._drawing:
            return
        self._drawing = False
        self._live = None
        s = self._live_stamp()
        self.stroke_pts = []
        if s is not None:
            self.push_undo()
            self.stamps.append(s)
        elif self.cur_tool == "high":
            self._cycle_high_dim()       # R102：近点单击＝聚光遮罩暗度循环加深
        self.update()

    def _cycle_high_dim(self):
        """R102：空处单击把聚光遮罩暗度推进一档（满黑后回到默认档）。
        作用于全部已存在聚光对象并同步后续默认档；可撤销。
        与截图侧差异：长图侧撤销快照记在松手提交时，故本方法须自行 push_undo。"""
        steps = capture.HIGH_DIM_STEPS
        nxt = steps[(steps.index(self.cur_high_dim) + 1) % len(steps)] \
            if self.cur_high_dim in steps else steps[0]
        self.cur_high_dim = nxt
        if any(s["kind"] == "high" for s in self.stamps):
            self.push_undo()
            for i, s in enumerate(self.stamps):
                if s["kind"] == "high":
                    s = self._clone_entry(s)
                    s["dim"] = nxt
                    self.stamps[i] = s
            self.update()

    # ---- 文字/序号附言浮层（复用截图 WYSIWYG；字号按图坐标存，显示字号=k 换算）----
    def show_text_editor(self, img_pos, preset=None, idx=None, fs=None, color=None):
        self.commit_text_editor()
        self.commit_text_input()
        # fs/color 缺省＝当前工具值；双击再编辑传入原对象值（同截图侧 _reedit_text：保真不跳样）
        fs_img = max(12, round((12 + self.cur_size) * self._s)) if fs is None else fs
        color = self.cur_color if color is None else color
        dx = max(0, min(img_pos.x() * self._k, self.width() - 60))
        dy = max(0, min(img_pos.y() * self._k, self.height() - 30))
        t = capture.ShotOverlay._wysiwyg_editor(self, QPoint(round(dx), round(dy)),
                                                max(9, round(fs_img * self._k)), color)
        t._fs_img, t._color = fs_img, color
        t._idx = idx                              # 双击再编辑：提交时原位替换该对象
        self.text_editor = t
        if idx is not None:
            self._text_edit_idx = idx             # 再编辑期间底层文字让位（R79 防重影），提交/取消恢复
            self.update()
        if preset:
            t.setPlainText(preset)
            c = t.textCursor()
            c.movePosition(QTextCursor.MoveOperation.End)
            t.setTextCursor(c)
        self._fit_anno_editor(t)
        t.show()
        t.setFocus()
        t.installEventFilter(self)

    def commit_text_editor(self):
        """失焦/确认：浮层文字落成文字对象（空＝丢弃）。回车换行已发生在编辑中。"""
        t = self.text_editor
        if not t:
            return
        self.text_editor = None           # 先摘钩子，防 FocusOut 重入
        sel = self.sel or QRect(0, 0, self.width(), self.height())
        avail = max(48, sel.right() - t.x() + 1)
        # 与浮层同一排版引擎拆行：长图 k 缩放下浮层视口窄、撞右缘软换行频发，
        # toPlainText 只有硬换行——直接 split 会把多行连回一行（非所见即所得主因）
        lines = self._layout_lines(t.toPlainText(), t.font(), avail)
        while lines and not lines[-1].strip():
            lines.pop()
        top = self._to_img(QPoint(t.x(), t.y()))
        fs_img, color = t._fs_img, t._color
        idx = getattr(t, "_idx", None)
        t.deleteLater()
        self.setFocus()
        if not "".join(lines).strip():
            self._text_edit_idx = None            # 空提交＝丢弃新内容：原文字恢复上屏（R79）
            self.update()
            return
        fm = QFontMetrics(QFont("Microsoft YaHei UI", fs_img, QFont.Bold))
        w = max((fm.horizontalAdvance(ln) for ln in lines), default=0)
        s = {"kind": "text", "lines": lines, "color": color, "fs": fs_img,
             "asc": fm.ascent(), "lh": fm.lineSpacing(),
             "r": QRect(top.x(), top.y(), w + 2, fm.lineSpacing() * len(lines) + 2)}
        self._text_edit_idx = None                # 提交后底层恢复上屏（R79）
        self.push_undo()
        if idx is not None and 0 <= idx < len(self.stamps) and self.stamps[idx]["kind"] == "text":
            self.stamps[idx] = s           # 再编辑：原位替换（撤销可回退）
        else:
            self.stamps.append(s)
        self.update()

    def cancel_text_editor(self):
        t = self.text_editor
        if not t:
            return
        self.text_editor = None
        self._text_edit_idx = None                # Esc 放弃：原文字恢复上屏（R79）
        t.deleteLater()
        self.setFocus()
        self.update()

    # ---- 序号（与截图同语义：点击落标 → 附言所见即所得；右键圆标删除自动重排）----
    def num_radius(self):
        return round((12 + self.cur_size) * self._s)

    def show_num_input(self, img_pos):
        """点击落标（追加 _nums，画面即现圆标），右侧所见即所得浮层输附言；
        失焦那一下只落字（同文字工具）；Esc 取消落标；右键圆标删除，删除后自动重排。"""
        self.commit_text_editor()
        self.commit_text_input()
        self.push_undo()
        self._nums.append({"pt": QPoint(img_pos), "text": "",
                           "color": self.cur_color, "r": self.num_radius()})
        n = self._nums[-1]
        fs_img = max(12, round((12 + self.cur_size) * self._s))
        dx = max(0, min(img_pos.x() * self._k + n["r"] * self._k + 6, self.width() - 60))
        dy = max(0, min(img_pos.y() * self._k - 12, self.height() - 30))
        t = capture.ShotOverlay._wysiwyg_editor(self, QPoint(round(dx), round(dy)),
                                                max(9, round(fs_img * self._k)), self.cur_color)
        t._fs_img, t._color = fs_img, self.cur_color
        t._num = True
        t._entry = n
        self.text_edit = t
        self._fit_anno_editor(t)
        t.show()
        t.setFocus()
        t.installEventFilter(self)
        self.update()

    def _reedit_num(self, i):
        """R104：双击序号（圆标/附言）＝附言原地浮层再编辑（k 换算版，语义同截图侧）：
        预填原文、失焦落字；整个会话＝一步撤销（开场压快照，Esc 走 cancel_text_edit 回滚）；
        再编辑期间底层附言让位（_num_edit_idx，capture 版 _paint_nums 按 self 读取），圆标保留。"""
        n = self._nums[i]
        self.commit_text_editor()
        self.commit_text_input()
        self.push_undo()
        r = n["r"]
        tx = n["pt"].x() + n.get("tdx", round(r + 6 / self._k))
        ty = n["pt"].y() + n.get("tdy", round(-12 / self._k))
        fs_img = max(12, r)
        dx = max(0, min(tx * self._k, self.width() - 60))
        dy = max(0, min(ty * self._k, self.height() - 30))
        t = capture.ShotOverlay._wysiwyg_editor(self, QPoint(round(dx), round(dy)),
                                                max(9, round(fs_img * self._k)), n["color"])
        t._fs_img, t._color = fs_img, n["color"]
        t._num = True
        t._entry = n
        self.text_edit = t
        self._num_edit_idx = i
        if n.get("text"):
            t.setPlainText(n["text"])
            c = t.textCursor()
            c.movePosition(QTextCursor.MoveOperation.End)
            t.setTextCursor(c)
        self._fit_anno_editor(t)
        t.show()
        t.setFocus()
        t.installEventFilter(self)
        self.update()

    def commit_text_input(self):
        """序号附言浮层落字（同文字工具：失焦落字、Esc 丢弃；空字＝只留圆标）。
        附言原位锚点存图坐标差（tdx/tdy）：渲染按提交时编辑器位置落字，确认后不跳位。"""
        t = self.text_edit
        if not t:
            return
        self.text_edit = None             # 先摘钩子，防 FocusOut 重入
        entry = getattr(t, "_entry", None)
        if entry is not None:
            sel = self.sel or QRect(0, 0, self.width(), self.height())
            avail = max(48, sel.right() - t.x() + 1)
            txt = t.toPlainText().strip()
            top = self._to_img(QPoint(t.x(), t.y()))
            # 附言同文字工具：按浮层字体+可用宽拆行（序号层逐行画＝所见即所得）
            entry["text"] = "\n".join(self._layout_lines(txt, t.font(), avail)) if txt else ""
            entry["tdx"] = top.x() - entry["pt"].x()
            entry["tdy"] = top.y() - entry["pt"].y()
        self._num_edit_idx = None           # R104：提交后底层附言恢复上屏
        t.deleteLater()
        self.setFocus()
        self.update()

    def cancel_text_edit(self):
        """Esc：关闭浮层；序号则连同点击时落的圆标一并取消（回退快照）。"""
        t = self.text_edit
        if not t:
            return
        self.text_edit = None
        if getattr(t, "_num", False) and len(self.undo_stack) > 1:
            snaps, nums = self.undo_stack[-1]     # 回到落标/再编辑前：圆标与附言一并回滚
            self.stamps = [_clone_stamp(s) for s in snaps]
            self._nums = [self._clone_num(n) for n in nums]
            self.undo_stack.pop()
        self._num_edit_idx = None           # R104：放弃后底层附言恢复上屏
        t.deleteLater()
        self.setFocus()
        self.update()

    def _clone_num(self, n):
        c = dict(n)
        c["pt"] = QPoint(c["pt"])
        return c

    def eventFilter(self, obj, ev):
        if obj is self.text_editor:
            if ev.type() == QEvent.Type.FocusOut:
                # R122 同步提交，与截图侧一致：FocusOut 先于 press 派发，press 里 had_edit=False，
                # 同一击继续走工具分支——文本工具下＝落旧字同时新位置直接开输入框（B2 用户要求
                # 与截图侧统一；R81 的延迟提交曾让此击只落字、再点才开输入，行为分叉于此回摆）
                self.commit_text_editor()
                return False
            if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key_Escape:
                self.cancel_text_editor()
                return True
        elif obj is self.text_edit:
            if ev.type() == QEvent.Type.FocusOut:
                # 同 R122：同步提交（序号附言与文字浮层同语义，同截图侧）
                self.commit_text_input()
                return False
            if ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key_Escape:
                self.cancel_text_edit()
                return True
        return super().eventFilter(obj, ev)

    # ---- 撤销/重做（快照＝(stamps,_nums) 二元组，序号层同样可回退）----
    def _snapshot(self):
        return ([_clone_stamp(s) for s in self.stamps],
                [self._clone_num(n) for n in self._nums])

    def push_undo(self):
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)
        self.undo_stack.append(self._snapshot())
        self.redo_stack.clear()

    def undo(self):
        if self.text_editor or self.text_edit:
            self.commit_text_editor()     # 先落字再撤：刚落的字被这次撤销回退
            self.commit_text_input()
        if len(self.undo_stack) > 1:
            self.redo_stack.append(self._snapshot())   # 当前态进重做栈
            snaps, nums = self.undo_stack[-1]
            self.stamps = [_clone_stamp(s) for s in snaps]
            self._nums = [self._clone_num(n) for n in nums]
            self.undo_stack.pop()         # 同截图侧：先恢复栈顶快照再弹出（先弹后恢会多退一级）
            self.update()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._snapshot())   # 当前态退回撤销栈
            snaps, nums = self.redo_stack.pop()
            self.stamps = [_clone_stamp(s) for s in snaps]
            self._nums = [self._clone_num(n) for n in nums]
            self.update()

    # ---- 键盘 ----
    def keyPressEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Z:
            self.redo() if ev.modifiers() & Qt.ShiftModifier else self.undo()
            return
        if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Y:
            self.redo()
            return
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.sel_stamp:
                self._delete_selected()    # 选中标注 Del/退格＝删除（同截图侧）
            else:
                self._delete_hover()       # R78：悬浮即删（免先点选，同截图侧）
            return
        super().keyPressEvent(ev)

    # ---- 导出 ----
    def render_final(self):
        """全分辨率渲染：底图 + 标注 + 序号层走同一份绘制代码，零缩放逐像素。"""
        img = QImage(self._base)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        for s in self.stamps:
            if s["kind"] == "high":
                continue                          # R121：聚光并集统一渲染（同 paintEvent）
            capture.ShotOverlay._paint_shape(self, p, s)
        self._paint_high_dim(p, self.stamps)
        for s in self.stamps:
            if s["kind"] == "high":
                self._draw_high_border(p, s["r"], s.get("shape", "rect"))
        if self._nums:
            self._paint_nums(p)
        p.end()
        return img


class LongEditor(QWidget):
    """长截图编辑器窗口：单行工具条（工具/撤销重做/颜色/粗细/取消完成），下方滚动画布。"""

    def __init__(self, arr, frames=0, on_done=None, on_cancel=None, save_dir=None, topmost=False):
        super().__init__(None)
        C.snapshot_input_locale()   # R83：前台若非我们则刷新记录的用户输入法语言
        self.setWindowTitle("长截图编辑 · 简截 — 滚轮翻页 · 拖动即画 · 文字点哪儿写哪儿")
        # R121 topmost：剪贴板历史窗是常驻置顶——编辑窗不置顶会被压在下层点不到
        self.setWindowFlags(Qt.Window | (Qt.WindowStaysOnTopHint if topmost else Qt.Widget))
        self.setWindowIcon(QIcon(C.app_icon()))
        self.setStyleSheet(W.BASE_QSS)
        self._frames = frames
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._save_dir = save_dir
        self._done = False

        scr = QGuiApplication.primaryScreen().availableGeometry()
        vw = max(880, min(980, scr.width() - 80))   # 下限＝主工具条（工具钮＋动作钮）舒适容纳宽
        vh = max(360, min(680, scr.height() - 120))
        self.resize(vw, vh)

        self.canvas = LongCanvas(arr, vw)
        self._right_cancel = C.CaptureRightCancel(self, self._cancel_like_escape)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        self._tool_btns = {}
        for kind in capture.TOOL_LIST:               # 与截图侧同一工具序列（含 select 选择/移动）
            b = icons.tool_button("cursor" if kind == "select" else kind,
                                  capture.TOOL_NAMES[kind],
                                  lambda _=False, k=kind: self.set_tool(k), checkable=True)
            self._tool_btns[kind] = b
            row1.addWidget(b)
        self._tool_btns["select"].setToolTip("选择 · 点标注拖动/缩放，Del 或右键删除")
        self._tool_btns["text"].setToolTip("文字 · 点击落字直接输入，回车换行，点空白完成")
        self._tool_btns["num"].setToolTip("序号 · 点击落标，按住拖动，点 × 或右键删除（自动重排）")
        self._tool_btns["rect"].setChecked(True)
        row1.addWidget(self._sep())
        row1.addWidget(icons.tool_button("undo", "撤销 (Ctrl+Z)", self.canvas.undo))
        row1.addWidget(icons.tool_button("redo", "重做 (Ctrl+Y)", self.canvas.redo))
        # R134：粗细/颜色/工具级选项（箭头样式/序号样式起始/聚光形态）全部迁入辐条
        # （复用截图侧 capture.ToolPropsPop，同一底色 QSS）——主工具条只留工具/动作，
        # 贴身属性钮范式退役（同步截图侧 R134 改造：用户点名选项「都应该出现在下方的辐条上」）
        row1.addStretch(1)
        row1.addWidget(icons.tool_button("close", "放弃编辑", self.cancel, color="#f4645f"))
        self.b_save = icons.tool_button("save", "保存到本地目录", lambda: self.finish(copy=False),
                                       color="#60a5fa")
        row1.addWidget(self.b_save)
        self.b_finish = icons.tool_button("confirm", "复制到剪贴板 (Enter)", lambda: self.finish(copy=True),
                                           color="#2ecc71")
        row1.addWidget(self.b_finish)
        self._row1 = row1                     # R134：冒烟断言主条无属性钮用（原 R101 贴身断言已随范式退役）
        root.addLayout(row1)
        # R134 辐条行：截图侧同款 ToolPropsPop 固定行嵌入（浮窗范式不适合可移动/可缩放的编辑器窗）
        self.props_pop = capture.ToolPropsPop(self)
        _props_row = QHBoxLayout()
        _props_row.setContentsMargins(0, 0, 0, 0)
        _props_row.addWidget(self.props_pop)
        _props_row.addStretch(1)
        root.addLayout(_props_row)
        if self.canvas.cur_tool in capture.ShotOverlay._PROPS_TOOLS:   # 初始工具 rect：弹粗细/颜色
            self.props_pop.set_tool(self.canvas.cur_tool)
            self.props_pop.show()
        else:
            self.props_pop.hide()
        self._status = QLabel()
        self._status.setStyleSheet("color:#fca5a5;")
        self._status.hide()
        root.addWidget(self._status)

        sa = QScrollArea()
        sa.setWidget(self.canvas)
        sa.setWidgetResizable(False)
        sa.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        sa.setStyleSheet("QScrollArea{border:1px solid rgba(255,255,255,22);border-radius:8px;"
                         "background:rgba(0,0,0,120);}")
        root.addWidget(sa, 1)
        C.prepare_capture_window(self)
        self.show()
        if topmost:                              # 同级置顶拼 z 序：后激活者在上，提起压过历史窗
            self.raise_()
            self.activateWindow()

    def _sep(self):
        f = QFrame()
        f.setFixedSize(1, 18)
        f.setStyleSheet("background:rgba(255,255,255,28);border:none;")
        return f

    def set_tool(self, kind):
        self.canvas.commit_text_editor()
        self.canvas.commit_text_input()   # 序号附言同样失焦落字（同截图侧切工具语义）
        self.canvas.cur_tool = kind
        self.canvas.setCursor(self.canvas._tool_idle_cursor())   # R84：切完即生效
        for k, b in self._tool_btns.items():
            b.setChecked(k == kind)
        # R134：辐条随工具显隐（选项区换内容；select/mosaic 无属性即收，同截图侧 _sync_props_pop）
        if hasattr(self, "props_pop"):
            if kind in capture.ShotOverlay._PROPS_TOOLS:
                self.props_pop.set_tool(kind)
                self.props_pop.show()
            else:
                self.props_pop.hide()

    def set_color(self, c):
        self.canvas.cur_color = c
        if getattr(self, "props_pop", None):      # R134：色点已迁入辐条，选中态由 pop 同步
            self.props_pop.sync()

    def set_size(self, v):
        self.canvas.cur_size = v
        if getattr(self, "props_pop", None):
            self.props_pop.sync()

    # ---- R134 辐条 ov 协议（capture.ToolPropsPop 按 ov.* 解析，默认值/标签转发 canvas）----
    @property
    def cur_size(self):
        return self.canvas.cur_size

    @property
    def cur_color(self):
        return self.canvas.cur_color

    @property
    def cur_num_start(self):
        return self.canvas.cur_num_start

    @property
    def cur_high_shape(self):
        return self.canvas.cur_high_shape

    @property
    def cur_arrow_style(self):
        return self.canvas.cur_arrow_style

    def _num_label(self, i):
        return self.canvas._num_label(i)

    # ---- R100 工具属性钮（操作画布默认值/选中对象，语义同截图侧）----
    def _refresh_prop_btns(self):
        """R134：选项钮已迁入辐条（复用 capture.ToolPropsPop），刷新统一委托 pop.sync。"""
        pop = getattr(self, "props_pop", None)
        if pop:
            pop.sync()

    def _pick_num_style(self):
        """序号样式下拉（R98 QMenu 范式：不透顶层窗自带 QSS）。"""
        cv = self.canvas
        m = QMenu(self)
        m.setStyleSheet(C.UI_MENU_QSS)
        for v, label in (("num", "1, 2, 3"), ("upper", "A, B, C"), ("lower", "a, b, c"),
                         ("uroman", "I, II, III"), ("lroman", "i, ii, iii")):
            act = m.addAction(label)
            act.setCheckable(True)
            act.setChecked(v == cv.cur_num_style)
            act.triggered.connect(lambda _=False, vv=v: self._set_num_style(vv))
        m.exec(QCursor.pos())

    def _set_num_style(self, v):
        self.canvas.cur_num_style = v
        self._refresh_prop_btns()
        self.canvas.update()               # 序号层现画：全体标号即时换样式

    def _cycle_num_start(self):
        cv = self.canvas
        cv.cur_num_start = cv.cur_num_start % 9 + 1          # 1~9 循环
        self._refresh_prop_btns()
        cv.update()

    def _cycle_high_shape(self):
        """选中聚光＝改该对象；未选中＝改新画默认。"""
        cv = self.canvas
        if cv.sel_stamp and cv.sel_stamp[0] == "shape" \
                and cv.stamps[cv.sel_stamp[1]]["kind"] == "high":
            cv.push_undo()
            s = cv._clone_entry(cv.stamps[cv.sel_stamp[1]])
            s["shape"] = "ellipse" if s.get("shape", "rect") == "rect" else "rect"
            cv.stamps[cv.sel_stamp[1]] = s
            cv._rerender_anno()
            cv.update()
        else:
            cv.cur_high_shape = "ellipse" if cv.cur_high_shape == "rect" else "rect"
        self._refresh_prop_btns()

    def _cycle_arrow_style(self):
        """R134 箭头样式两档循环（同截图侧）：选中箭头＝改该对象（压撤销）；未选中＝改新画默认。
        长图 paintEvent 现画无 anno 位图层，改对象后 update() 即重画。"""
        cv = self.canvas
        if cv.sel_stamp and cv.sel_stamp[0] == "shape" \
                and cv.stamps[cv.sel_stamp[1]]["kind"] == "arrow":
            cv.push_undo()
            s = cv._clone_entry(cv.stamps[cv.sel_stamp[1]])
            s["style"] = "solid" if s.get("style", "line") == "line" else "line"
            cv.stamps[cv.sel_stamp[1]] = s
            cv.update()
        else:
            cv.cur_arrow_style = "solid" if cv.cur_arrow_style == "line" else "line"
        self._refresh_prop_btns()

    def finish(self, copy=True):
        if self._done:
            return
        self.canvas.commit_text_editor()
        self.canvas.commit_text_input()   # 序号附言未确认也落字，保证不丢
        img = self.canvas.render_final()
        path = None
        if not copy:
            try:
                path = "{}\\长截图_{}.png".format(self._save_dir or pictures_dir(), timestamp())
                C.save_image(img, path)
            except OSError:
                self._status.setText("保存失败，请检查存放位置后重试；编辑内容已保留")
                self._status.show()
                return
        if self._on_done and self._on_done(path, self._frames, copy=copy, image=img) is False:
            self._status.setText("复制失败，请再点对号重试；编辑内容已保留")
            self._status.show()
            return
        self._done = True
        self.close()

    def cancel(self):
        self._done = True
        self.close()
        if self._on_cancel:
            self._on_cancel()

    def closeEvent(self, ev):
        if not self._done and self._on_cancel:
            self._on_cancel()             # 右上角 × ＝放弃编辑
        super().closeEvent(ev)

    def _cancel_like_escape(self):
        if self.canvas.text_editor:
            self.canvas.cancel_text_editor()
        elif self.canvas.text_edit:
            self.canvas.cancel_text_edit()
        else:
            self.cancel()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self._cancel_like_escape()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not ev.isAutoRepeat():
                self.finish()
            return
        if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Z:
            self.canvas.redo() if ev.modifiers() & Qt.ShiftModifier else self.canvas.undo()
            return
        if ev.modifiers() & Qt.ControlModifier and ev.key() == Qt.Key_Y:
            self.canvas.redo()
            return
        super().keyPressEvent(ev)
