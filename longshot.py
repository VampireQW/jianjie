# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 长截图：浮动工具条 + 手动滚动拼接 + 编辑器 + numpy 列压缩 NCC 像素级无缝拼接。

R107：模板匹配由 cv2 换成纯 numpy 实现——轻量包（截图+长截图）因此可整体裁掉 cv2（41.5MB）。
匹配仅垂直滑动（search/tpl 同宽切片），水平方向列均值压缩不损失垂直定位精度，
实测与 cv2.TM_CCOEFF_NORMED 定位坐标全场景一致，且耗时更低（列压缩少 16 倍乘加）。

R117/R119：双栏长截图——选区分左右两栏各跑一套独立拼接状态机（左栏滚时只有左半帧变，
滚动归属天然按帧内容区分，不猜光标）；合成时两栏顶对齐，短栏底部按用户指定用黑色填充。
R119 收编进长截图本体：拼接条上切换钮即时切单/双栏（保留已拼内容），独立入口全撤。"""
import numpy as np
from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QIcon, QImage, QPainter,
                           QPainterPath, QPixmap, QPen)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

import common as C
import icons
from common import pictures_dir, timestamp


_MIN_COLUMN_FRACTION = 0.1


def grab_array(scr=None, region=None):
    """目标屏抓帧 → numpy RGB 数组（物理分辨率）。region 为逻辑 QRect 时裁剪到该范围。"""
    scr = scr or QGuiApplication.primaryScreen()
    pm = scr.grabWindow(0)
    img = pm.toImage().convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    ptr = img.constBits()
    buf = np.frombuffer(ptr, dtype=np.uint8)
    arr = buf.reshape(h, bpl)[:, :w * 3].reshape(h, w, 3).copy()
    if region is not None and region.width() > 4 and region.height() > 4:
        d = scr.devicePixelRatio() or 1.0
        x0, y0 = int(region.x() * d), int(region.y() * d)
        x1, y1 = int((region.x() + region.width()) * d), int((region.y() + region.height()) * d)
        arr = arr[max(0, y0):min(h, y1), max(0, x0):min(w, x1)].copy()
    return arr


def _static_rows(a, b, from_top=True, tol=2.0):
    """两帧间完全相同的顶部/底部行数（粘性标题栏/底栏高度）。"""
    if a is None or b is None or a.shape != b.shape:
        return 0
    h = a.shape[0]
    n = 0
    rows = range(h) if from_top else range(a.shape[0] - 1, a.shape[0] - 1 - h, -1)
    for r in rows:
        if float(np.mean(np.abs(a[r].astype(np.int16) - b[r].astype(np.int16)))) < tol:
            n += 1
        else:
            break
    return n


def _gray_f(arr):
    """RGB uint8 → 灰度 float32（BT.601 权重，与 cv2.COLOR_RGB2GRAY 同式）。"""
    return (arr[..., 0].astype(np.float32) * 0.299
            + arr[..., 1].astype(np.float32) * 0.587
            + arr[..., 2].astype(np.float32) * 0.114)


def _col_compress(g, k):
    """(h,w) 灰度 → (h, w//k) 列均值：水平不滑动故可压缩（垂直定位精度无损），乘加量缩 k 倍。"""
    h, w = g.shape
    w2 = w // k * k
    return g[:, :w2].reshape(h, w2 // k, k).mean(axis=2)


def _ncc_locate(search, tpl):
    """numpy 版 matchTemplate(TM_CCOEFF_NORMED)+minMaxLoc（仅垂直滑动）：返回 (score, y)。
    探针 probe_ncc 实测：六场景定位坐标与 cv2 完全一致，1920 宽压测 23ms（cv2 31ms）。"""
    k = max(4, tpl.shape[1] // 128)
    t = _col_compress(tpl, k)
    s = _col_compress(search, k)
    th, tw = t.shape
    sh = s.shape[0]
    if sh < th:
        return -1.0, 0
    t_c = t - t.mean()
    t_dn = float(np.sqrt((t_c * t_c).sum()))
    if t_dn < 1e-6:
        return -1.0, 0
    n = sh - th + 1
    cs = np.vstack([np.zeros((1, tw), np.float32), s.cumsum(axis=0, dtype=np.float64)])
    cq = np.vstack([np.zeros((1, tw), np.float32), (s * s).cumsum(axis=0, dtype=np.float64)])
    wsum = (cs[th:th + n] - cs[:n]).sum(axis=1)          # 窗口像素和（垂直 cumsum O(1)）
    wsq = (cq[th:th + n] - cq[:n]).sum(axis=1)
    npix = th * tw
    wden = np.sqrt(np.maximum(wsq - wsum * wsum / npix, 0.0))
    best_s, best_y = -2.0, 0
    for y in range(n):                                   # Σt_c=0 ⇒ NCC 分子退化为点积
        d = float(wden[y])
        if d < 1e-6:                                     # 纯色窗：NCC 未定义（cv2 记 0）——
            continue                                     # 不跳过则分母≈0 score 爆表错选（锁场景实锤）
        sc = float((t_c * s[y:y + th]).sum()) / (t_dn * d)
        if sc > best_s:
            best_s, best_y = sc, y
    return best_s, best_y


def stitch_pair(long_arr, frame, prev=None, strip_footer=False, band_ratio=0.3,
                min_score=0.55, foot_guard=0.12):
    """模板匹配定位长图尾部内容在新帧中的行位置（像素级，numpy 列压缩 NCC）。
    返回 (start, end, score, bot_h)：start>0 → 追加 frame[start:end]；start=0 → 无新内容（未滚动/到底）；
    start=-1 → 重叠不足（滚过快或画面不连续）。bot_h 为实测粘性底栏高度。
    strip_footer=True 表示长图底部还烙着首帧的粘性底栏（调用方会同步剔除），匹配起点按 bot_h 回退；
    底栏剔除后必须传 False，否则每条接缝都会重复 bot_h 行。
    尾部模板纯色（页面底部空白/懒加载占位）时自动上移取样带，匹配失败再减半重试。prev 为上一帧。"""
    fh, fw = frame.shape[:2]
    lh, lw = long_arr.shape[:2]
    if lw != fw or lh < 40 or fh < 60:
        return -1, -1, 0.0, 0
    if prev is not None and prev.shape == frame.shape:
        diff = np.abs(prev.astype(np.int16) - frame.astype(np.int16))
        if float(diff.mean()) < 0.5:
            return 0, 0, 1.0, 0                    # 画面完全未变：未滚动/已到底
    x0 = int(fw * 0.05)
    x1 = fw - max(20, int(fw * 0.02))              # 右缘排除滚动条
    top_h = _static_rows(prev, frame, True)
    bot_h = _static_rows(prev, frame, False)       # 实测粘性底栏高度
    content_h = fh - top_h - bot_h
    if content_h < 32:
        return 0, 0, 1.0, bot_h                 # 变化区太小，不能从固定面板里取模板
    band_h = max(24, min(int(content_h * band_ratio), lh - 8, 500))
    gray_l = _gray_f(long_arr)
    gray_f = _gray_f(frame)
    guard = int(content_h * foot_guard)
    if strip_footer:
        guard = max(guard, bot_h)               # 首帧仍含底栏时避开；内容图已去底栏则无需再次回退
    bh_try = band_h
    while bh_try >= 24:
        for shift in (0, bh_try // 2, bh_try):     # 尾部纯色→取样带上移
            tpl_top = lh - guard - bh_try - shift
            if tpl_top < 8:
                break
            tpl = gray_l[tpl_top: tpl_top + bh_try, x0:x1]
            if float(tpl.std()) < 2.0:
                continue                           # 纯色带换一段取样
            y_from = min(top_h, fh - bh_try - 4)
            y_to = max(y_from + 8, fh - bot_h)
            search = gray_f[y_from: max(y_to, y_from + bh_try + 8), x0:x1]
            if search.shape[0] <= bh_try:
                continue
            score, ly = _ncc_locate(search, tpl)
            if float(score) < min_score:
                continue
            y = ly + y_from
            # 模板底以下还有 guard+shift 行；底栏仍在长图里时按 bot_h 回退，
            # 已剔除后长图底部是纯内容，再回退就会在接缝处重复 bot_h 行
            start = y + bh_try + (guard + shift) - (bot_h if strip_footer else 0)
            end = fh - bot_h                       # 粘性底栏不追加
            if start >= end - 4:
                return 0, 0, float(score), bot_h   # 无新内容
            return start, end, float(score), bot_h
        bh_try //= 2                               # 匹配失败→减半模板再试（抗动图/轮播干扰）
    return -1, -1, 0.0, bot_h


def tail_dup(long_arr, frame, s, e, tol=1.0, shift=3):
    """假新内容判定：frame[s:e] 与长图尾部在 ±shift 行对齐内几乎逐像素一致。
    到底后画面静止、匹配位置抖 1~3px 时 stitch_pair 仍会返回 s>0——不拦截
    就会每拍重复追加一小条，长图无限变长。真滚动的新内容不可能与长图尾部
    对齐重合。"""
    h = e - s
    if h <= 0 or long_arr.ndim != 3 or long_arr.shape[0] < h + 2 * shift \
            or long_arr.shape[1:] != frame.shape[1:]:
        return False
    blk = frame[s:e].astype(np.int16)
    tail = long_arr[-(h + 2 * shift):].astype(np.int16)
    for k in range(2 * shift + 1):
        if float(np.mean(np.abs(blk - tail[k:k + h]))) < tol:
            return True
    return False


_HALO_GREEN = "#22c55e"            # 同 capture.RegionFrame 选区绿


class RegionHalo(QWidget):
    """长截图选区高亮框：与录屏 RegionFrame 同款绿框（画在选区外沿，不进拼接画面）。

    与录屏的区别：不可拖动/缩放——拼接期间区域必须静止，模板匹配才不炸；
    整窗点击穿透（WindowTransparentForInput），不挡页面滚轮与鼠标。"""

    def __init__(self, screen, region):
        super().__init__(None)
        self._scr = QRect(screen.geometry())
        self._region = QRect(region).normalized()
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setGeometry(self._scr)
        C.prepare_capture_window(self)
        self.show()
        self.raise_()
        C.exclude_from_capture(self)       # 框不烙进长图（真平台生效；离屏降级无害）

    def paintEvent(self, ev):
        r = self._region
        if r.width() < 4 or r.height() < 4:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(_HALO_GREEN), 2))
        p.drawRect(r.adjusted(-2, -2, 2, 2))   # 高亮线画在选区外沿，与录屏一致
        p.end()


class LongPreview(QWidget):
    """长截图右侧实时预览（微信式）：整图缩略悬浮窗，拼接出新一屏就整图刷新。

    点击穿透（不挡页面滚动），且从屏幕捕获中排除——预览若烙进帧里，
    长图会拍到自己的缩略图，模板匹配立刻自反馈炸掉。"""
    PW = 170                                # 面板固定宽（逻辑 px）

    def __init__(self, screen, region):
        super().__init__(None)
        self._scr = QRect(screen.geometry())
        sel = self._scr if region is None else QRect(region).normalized().translated(self._scr.topLeft())
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        ph = int(min(self._scr.height() * 0.82, max(300, sel.height() * 0.92)))
        self.resize(self.PW, ph)
        self._pm = None
        x = sel.right() + 14
        if x + self.PW > self._scr.right() - 4:
            left = sel.left() - 14 - self.PW
            if left >= self._scr.left() + 4:
                x = left   # 右侧贴屏幕边：翻到选区左侧，不叠高亮区
            else:
                x = self._scr.right() - self.PW - 8   # 两侧都放不下：叠在选区右缘上（同微信浮在页面上）
        y = max(self._scr.top() + 8, min(sel.center().y() - ph // 2, self._scr.bottom() - ph - 8))
        self.move(x, y)
        C.prepare_capture_window(self)
        self.show()
        self.raise_()
        C.exclude_from_capture(self)

    def set_arr(self, arr):
        """拼接出一版新长图就整图缩略一帧（QImage 只借用缓冲，arr 由长图管线持有）。"""
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
        self._pm = QPixmap.fromImage(img.scaled(
            self.width() - 12, self.height() - 12,
            Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        p.setBrush(QColor(16, 18, 28, 235))
        p.drawPath(path)
        if self._pm is not None:
            p.drawPixmap((self.width() - self._pm.width()) // 2,
                         (self.height() - self._pm.height()) // 2, self._pm)
        else:
            p.setPen(QPen(QColor(139, 147, 167), 1))
            p.drawText(self.rect(), Qt.AlignCenter, "开始滚动\n实时预览")
        p.end()


class LongShotBar(QWidget):
    """顶部悬浮控制条：弹出即手动滚动拼接（无「开始」钮）/ 编辑标注 / ⬇下载保存 / ✓复制剪贴板（最右）/ 取消。"""
    MISS_LIMIT = 25                    # 连续失联容忍度（25×400ms≈10s）：手动模式唯一自动收尾，防挂着不关

    def __init__(self, on_finish, on_cancel, region=None, screen=None, on_edit=None):
        super().__init__(None)
        C.snapshot_input_locale()   # R83：抢前台前记下用户正在用的输入法语言
        self.region = region          # 逻辑 QRect；None=整屏
        self._screen = screen
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowIcon(QIcon(C.app_icon()))
        self.setStyleSheet(
            "QWidget{background:rgba(16,18,28,235);border:none;border-radius:10px;}"   # R134：白色描边退役；R146 圆角收 mac 档
            "QLabel{color:#dfe4ef;font-size:12.5px;background:transparent;border:none;}"
            "QLabel#dim{color:#8b93a7;font-size:12px;background:transparent;border:none;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(8)

        def sep():
            f = QFrame()
            f.setFixedSize(1, 18)
            f.setStyleSheet("background:rgba(255,255,255,28);border:none;")
            lay.addWidget(f)

        _scr = self._screen or (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        dpr = (_scr.devicePixelRatio() or 1.0) if _scr else None
        self.dot = icons.PulseDot()
        self.msg = QLabel("请向下慢滚，简截自动拼接")
        self.stat = QLabel("0 屏")
        self.stat.setObjectName("dim")
        bedit = icons.tool_button("pen", "编辑：停止拼接，进编辑器标注长图", self.edit,
                                  color="#f59e0b", dpr=dpr)
        bcols = icons.tool_button("cols", "双栏模式：左右两栏各自滚动拼一张（短栏补黑）",
                                  self.toggle_dual, color="#38bdf8", checkable=True, dpr=dpr)   # R119
        bsave = icons.tool_button("save", "完成并保存到本地目录（不复制）",
                                  lambda: self.finish(copy=False), color="#60a5fa", dpr=dpr)
        b2 = icons.tool_button("close", "取消 (Esc)", self.cancel, color="#f4645f", dpr=dpr)
        b3 = icons.tool_button("confirm", "复制长图到剪贴板 (Enter)",
                               lambda: self.finish(copy=True), color="#2ecc71", dpr=dpr)
        bsave.setEnabled(False)                  # 无图可存：开始拼接后解禁（同 GIF 就绪语义）
        b3.setEnabled(False)
        self.bedit, self.b_cancel, self.b_fin, self.b_save = bedit, b2, b3, bsave
        self.bcols = bcols
        lay.addWidget(self.dot)
        lay.addWidget(self.msg)
        lay.addWidget(self.stat)
        sep()
        lay.addWidget(bcols)                           # R119 单/双栏切换（checked=双栏态）
        lay.addWidget(bedit)
        lay.addWidget(bsave)                           # ⬇ 直接存本地目录
        lay.addWidget(b2)                              # × 在前，对号收尾最右
        lay.addWidget(b3)                              # ✓ 复制到剪贴板，最右（同 GIF 条）
        self.timer = QTimer(self)
        self.timer.setInterval(400)    # R105：800→400——滚动与拍点错开是「预览偶尔不实时」的主因；
                                       # 节拍加密后两拍滚动量减半，重叠不足（匹配失败丢内容）概率同降
        self.timer.timeout.connect(self.grab)
        self._right_cancel = C.CaptureRightCancel(self, self.cancel, live=True)
        self.running = False
        self.long_arr = None
        self._prev = None
        self.frames = 0
        self._miss = 0                     # 连续重叠识别失败帧数（唯一自动收尾依据）
        self._foot_stripped = False        # 首帧烙进的粘性底栏是否已剔除
        self._footer_h = 0                 # 输出中保留的固定底部高度
        self._appended = False             # 是否已成功追加过内容（长图底部还是首帧原样）
        self._dual = False                 # R119 双栏模式：条上切换钮即时切，默认单栏
        self._div = 0.5                    # 分栏位置（逻辑宽占比），双栏未拼内容时可拖
        self._colL = None                  # 双栏态左/右栏状态机（_ColumnState）
        self._colR = None
        self._divider = None               # 双栏态分栏拖柄窗（_DividerHandle）
        self._prev_full = None             # 双栏态最近整帧（切回单栏恢复 prev 用）
        self._saved_pos = None
        self._completed = False
        self._on_finish = on_finish
        self._on_cancel = on_cancel
        self._on_edit = on_edit
        # 贴选区定位（优先传入屏，否则鼠标所在屏）；屏幕边缘自动翻到上方并钳制
        scr = self._screen or (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        self._toolbar_screen = scr
        self.adjustSize()
        self._place_toolbar()
        C.prepare_capture_window(self)
        self.show()
        self.raise_()
        self.activateWindow()
        C.force_foreground(self)       # R121：框选条闭合后前台已让出，抢回焦点 Esc 才能取消
        C.exclude_from_capture(self)       # 控制条不烙进长图首帧（呼吸点/文字每拍都算新内容）
        # 选区高亮框：与录屏一致，框选完成后立即出现；拼按结束随条一起关闭
        self._halo = RegionHalo(scr, self.region) if self.region else None
        # 微信式右侧实时预览：整图缩略随拼接更新，随条一起关闭
        self._preview = LongPreview(scr, self.region)
        self.start()                         # 进条即手动滚动态：无「开始」钮，首帧/光标定位/计时器一步到位

    def _place_toolbar(self):
        scr = self._screen or getattr(self, "_toolbar_screen", None)
        if scr is None:
            return
        if C.place_toolbar_near_region(self, self.region, scr, gap=10, edge=6):
            return
        g = scr.geometry()                   # 整屏模式保留原来的屏顶居中位置
        self.move(g.center().x() - self.width() // 2, g.top() + 16)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_toolbar()                # 状态文案变宽时仍限制在目标屏内

    def _close_halo(self):
        if getattr(self, "_halo", None):
            self._halo.close()
            self._halo = None

    def closeEvent(self, ev):
        self._close_halo()
        self._close_divider()                    # R119 分栏拖柄随条关闭
        if getattr(self, "_preview", None):
            self._preview.close()
            self._preview = None
        super().closeEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.cancel()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not ev.isAutoRepeat():
                self.finish(copy=True)
            return
        super().keyPressEvent(ev)

    def _restore_cursor(self):
        if self._saved_pos is not None:
            QCursor.setPos(self._saved_pos)
            self._saved_pos = None

    def start(self):
        self.long_arr = grab_array(self.screen(), self.region)
        self._prev = self.long_arr.copy()
        self._preview.set_arr(self.long_arr)     # 预览先亮出首帧
        self.frames = 1
        self._miss = 0
        self._foot_stripped = False        # 重新开始：剔除/追加状态一并复位
        self._footer_h = 0                 # 固定底部保留在输出中，匹配时单独跳过
        self._appended = False
        self.running = True
        self.dot.set_active(True)
        self.b_save.setEnabled(True)       # 有图可存：⬇下载/✓对号解禁（进条即滚动态，构造即解禁）
        self.b_fin.setEnabled(True)
        self._saved_pos = QCursor.pos()
        scr = self.screen()
        g = scr.geometry()
        reg = self.region
        if reg is not None and reg.width() > 4:
            cx = g.left() + reg.x() + reg.width() // 2
            cy = g.top() + reg.y() + reg.height() // 2
        else:
            cx, cy = g.center().x(), g.center().y()
        QCursor.setPos(cx, cy)             # 光标先落进区域：紧接着的滚轮才会滚到页面上
        self._dual = False                 # 每次进条默认单栏，双栏由切换钮即时开（R119）
        self.bcols.setChecked(False)
        self._colL = None
        self._colR = None
        self._prev_full = None
        self._close_divider()
        self.timer.start()
        self.msg.setText("请向下慢滚，简截自动拼接")
        self.update_stat()

    def grab(self):
        if not self.running or (self._divider is not None and self._divider._dragging):
            return
        try:
            frame = grab_array(self.screen(), self.region)
        except Exception:
            frame = None
        if frame is None:
            self._miss += 1                        # 抓帧瞬时失败按失联计，不中断
            if self._miss >= self.MISS_LIMIT:
                self.finish()
            return
        if self._dual:
            self._grab_dual(frame)                 # R119 双栏态走分栏拼接
            return
        try:
            body = self.long_arr[:-self._footer_h] if self._footer_h else self.long_arr
            s, e, score, bh = stitch_pair(body, frame, prev=self._prev,
                                          strip_footer=not self._appended)
        except Exception:
            s, e, score, bh = -1, -1, 0.0, 0
        if s > 0 and bh > 0 and not self._appended:
            body = body[:-bh]
        if s > 0 and tail_dup(body, frame, s, e):
            s = 0                                  # 假新内容（到底后匹配抖动）：按无新内容处理，
                                                   # 否则每拍重复追加、长图无限变长
        if s > 0:
            # 旧底部只从匹配内容中移除，最新帧的完整下沿在输出末尾保留一次。
            self.long_arr = np.vstack([body, frame[s:]])
            self._footer_h = bh
            self._foot_stripped = bh > 0
            self._appended = True
            self.frames += 1
            self._miss = 0
            self._prev = frame.copy()
            self._preview.set_arr(self.long_arr)  # 新内容落进右侧预览
            self.msg.setText(f"拼接中 · 重叠匹配 {score:.2f}")
        elif s == 0:
            self._miss = 0
            self.msg.setText("等待滚动…")
        else:
            self._miss += 1
            self._prev = frame.copy()
            self.msg.setText("重叠匹配暂失败，继续尝试…（底部动图/轮播不影响拼接）")
        self.update_stat()
        if self._should_autofinish():
            self.finish()                          # 仅连续失联才自动收尾；到底与否由用户判断

    def _should_autofinish(self):
        """手动模式没有「到底」概念（暂停阅读是常态）：唯一自动收尾＝连续失联防挂死。"""
        return self._miss >= self.MISS_LIMIT

    # ---- R119 双栏模式（条上切换钮即时切，保留已拼内容）----
    def toggle_dual(self):
        """单/双栏切换：单→双按分栏线纵切长图与最近帧重建两套状态机（拼接进度语义继承）；
        双→单两栏顶对齐合成回整图（短栏补黑随图带走），分栏拖柄随模式出现/关闭。"""
        if self.long_arr is None or self._prev is None:
            return
        if self.bcols.isChecked():
            self._dual = True
            dx = self._dx()
            self._colL = _ColumnState(self.long_arr[:, :dx].copy())
            self._colR = _ColumnState(self.long_arr[:, dx:].copy())
            for col in (self._colL, self._colR):           # 继承单栏拼接进度语义
                col.frames = self.frames
                col.appended = self._appended
                col.foot_stripped = self._foot_stripped
                col.footer_h = self._footer_h
            self._colL.prev = self._prev[:, :dx].copy()
            self._colR.prev = self._prev[:, dx:].copy()
            self._prev_full = self._prev
            scr = self._screen or QGuiApplication.primaryScreen()
            if scr is not None:
                region = self.region
                if region is None:
                    g = scr.geometry()
                    region = QRect(0, 0, g.width(), g.height())
                self._divider = _DividerHandle(scr, region, self._div, self)
            self.msg.setText("双栏模式 · 左右栏各自向下慢滚，分栏线可拖")
        else:
            self._dual = False
            if self._colL is not None:
                self._appended = self._colL.appended or self._colR.appended  # 合成图已是拼接态
                self._footer_h = (self._colL.footer_h
                                  if self._colL.footer_h == self._colR.footer_h
                                  and self._colL.long.shape[0] == self._colR.long.shape[0]
                                  else 0)
                self.long_arr = self._compose()
                self.frames = self._colL.frames + self._colR.frames - 1
                self._colL = None
                self._colR = None
            if self._prev_full is not None:
                self._prev = self._prev_full
                self._prev_full = None
            self._close_divider()
            self._preview.set_arr(self.long_arr)
            self.msg.setText("单栏模式 · 请向下慢滚，简截自动拼接")
        self.update_stat()

    def _grab_dual(self, frame):
        """双栏态抓帧：左右栏各喂半帧——哪半变了哪半推进，静止半栏 prev 未变早退，不猜光标。"""
        dx = self._dx()
        la, _ls = self._colL.feed(frame[:, :dx])
        ra, _rs = self._colR.feed(frame[:, dx:])
        if la > 0 or ra > 0:
            self._miss = 0
            side = "双栏" if la > 0 and ra > 0 else ("左栏" if la > 0 else "右栏")
            self.msg.setText(f"拼接中 · {side} +{max(la, ra)} 行")
            self.long_arr = self._compose()
            self._preview.set_arr(self.long_arr)
        elif la == 0 and ra == 0:
            self._miss = 0
            self.msg.setText("等待滚动…")
        else:
            self._miss += 1
            self.msg.setText("重叠匹配暂失败，继续尝试…（底部动图/轮播不影响拼接）")
        self._prev_full = frame.copy()               # 切回单栏时恢复 prev 用
        self.frames = self._colL.frames + self._colR.frames - 1
        self.update_stat()
        if self._divider is not None:
            self._divider.refresh_locked()
        if self._should_autofinish():
            self.finish()

    def _dx(self):
        """分栏物理像素 x：帧为物理分辨率，_div 是逻辑占比——同 DPR 下比例一致直接乘。"""
        w = self.long_arr.shape[1]
        return min(w - 8, max(8, round(w * self._div)))

    def _compose(self):
        return compose_columns(self._colL.long, self._colR.long)

    def divider_locked(self):
        return bool(self._colL is not None
                    and (self._colL.appended or self._colR.appended))

    def commit_divider(self, frac):
        """拖柄松手提交：未拼内容才允许重切——当前长图按新分栏重建两套状态机。"""
        if self.divider_locked():
            self.msg.setText("已有拼接内容，分栏线不可再调")
            return False
        self._div = min(1 - _MIN_COLUMN_FRACTION, max(_MIN_COLUMN_FRACTION, frac))
        dx = self._dx()
        prev = self._prev_full if self._prev_full is not None else self._prev
        self._colL = _ColumnState(self.long_arr[:, :dx].copy())
        self._colR = _ColumnState(self.long_arr[:, dx:].copy())
        self._colL.prev = prev[:, :dx].copy()
        self._colR.prev = prev[:, dx:].copy()
        self.frames = 1
        self._miss = 0
        self.update_stat()
        self.msg.setText("分栏位置已调整 · 左右栏各自向下慢滚")
        return True

    def _close_divider(self):
        if self._divider is not None:
            self._divider.close()
            self._divider = None

    def update_stat(self):
        if self._dual and self._colL is not None:            # R119 双栏态分栏计数
            lh = self._colL.long.shape[0]
            rh = self._colR.long.shape[0]
            self.stat.setText(f"左 {self._colL.frames} 屏 · 右 {self._colR.frames} 屏 · "
                              f"{self.long_arr.shape[1]}×{max(lh, rh)}px")
            return
        self.stat.setText(f"{self.frames} 屏 · {self.long_arr.shape[1]}×{self.long_arr.shape[0]}px")

    def _require_arr(self):
        """编辑/完成都要求已捕获内容；没开始就点＝条上提示，不打断。"""
        if self.long_arr is None:
            self.msg.setText("尚未拼到内容，请向下慢滚")
            return False
        return True

    def _release_images(self):
        self.long_arr = None
        self._prev = None
        self._prev_full = None
        self._colL = None
        self._colR = None
        self._footer_h = 0
        if self._preview is not None:
            self._preview._pm = None

    def finish(self, copy=True):
        """对号只复制图片，下载才写入所选目录。"""
        if self._dual and self._colL is not None:
            self.long_arr = self._compose()                  # R119 双栏态交付前以两栏终态合成
        if self._completed or not self._require_arr():
            return
        self.timer.stop()
        self.running = False
        self.dot.set_active(False)
        self._restore_cursor()
        h, w = self.long_arr.shape[:2]
        img = QImage(self.long_arr.data, w, h, self.long_arr.strides[0], QImage.Format_RGB888).copy()
        path = None
        if not copy:
            try:
                path = f"{pictures_dir()}\\长截图_{timestamp()}.png"
                C.save_image(img, path)
            except OSError:
                self.msg.setText("保存失败，请检查存放位置后重试")
                self.adjustSize()
                return
        if self._on_finish(path, self.frames, copy, image=img) is False:
            self.msg.setText("复制失败，请再点对号重试；图片已保留")
            self.adjustSize()
            return
        self._completed = True
        self.close()
        self._release_images()

    def edit(self):
        """编辑：停止拼接，把当前长图交给编辑器标注（微信式）。"""
        if self._dual and self._colL is not None:
            self.long_arr = self._compose()                  # R119 双栏态先合成再进编辑器
        if not self._require_arr():
            return
        self.timer.stop()
        self.running = False
        self._restore_cursor()
        self.close()
        image = self.long_arr
        try:
            self._on_edit(image, self.frames)
        finally:
            self._release_images()

    def cancel(self):
        self.timer.stop()
        self.running = False
        self._restore_cursor()
        self.close()
        self._release_images()
        self._on_cancel()


class _ColumnState:
    """单栏拼接状态机：匹配跳过固定底部，输出保留一次；每栏独立更新与去重。"""

    __slots__ = ("long", "prev", "frames", "foot_stripped", "appended", "footer_h")

    def __init__(self, first_frame):
        self.long = first_frame
        self.prev = first_frame.copy()
        self.frames = 1
        self.foot_stripped = False
        self.appended = False
        self.footer_h = 0

    def feed(self, frame):
        """喂一帧 → (追加行数, score)；0=无新内容；-1=重叠识别失败。"""
        try:
            body = self.long[:-self.footer_h] if self.footer_h else self.long
            s, e, score, bh = stitch_pair(body, frame, prev=self.prev,
                                          strip_footer=not self.appended)
        except Exception:
            s, e, score, bh = -1, -1, 0.0, 0
        if s > 0 and bh > 0 and not self.appended:
            body = body[:-bh]
        if s > 0 and tail_dup(body, frame, s, e):
            s = 0                              # 假新内容（到底后匹配抖动）：按无新内容处理
        if s > 0:
            self.long = np.vstack([body, frame[s:]])
            self.footer_h = bh
            self.foot_stripped = bh > 0
            self.appended = True
            self.frames += 1
            self.prev = frame.copy()
            return e - s, score
        if s == 0:
            return 0, score
        self.prev = frame.copy()
        return -1, score


def compose_columns(left, right):
    """R117：左右栏顶对齐合成一张长图；短栏底部用黑色填充（用户指定语义）。"""
    lh, lw = left.shape[:2]
    rh, rw = right.shape[:2]
    out = np.zeros((max(lh, rh), lw + rw, 3), dtype=np.uint8)
    out[:lh, :lw] = left
    out[:rh, lw:] = right
    return out


class _DividerHandle(QWidget):
    """分栏拖柄：20px 鼠标区域包住 2px 竖线，拼接前可左右拖动调整分栏位置。

    选区高亮框整窗点击穿透（不挡滚轮），分栏线因此做成独立小窗——只有这条线吃鼠标；
    一旦任一栏追加过内容即锁定（宽度重切会让 stitch_pair 宽高不匹配全失联）。"""

    W = 20
    GRIP_W = 8

    def __init__(self, screen, region, frac, owner):
        super().__init__(None)
        self._owner = owner
        self._scr = QRect(screen.geometry())
        self._region = QRect(region).normalized()
        self._frac = frac
        self._dragging = False
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.refresh_locked()
        self._place(frac)
        C.prepare_capture_window(self)
        self.show()
        self.raise_()
        C.exclude_from_capture(self)       # 分栏线不烙进长图

    def _place(self, frac):
        self._frac = frac
        x = self._scr.left() + self._region.x() + int(self._region.width() * frac) - self.W // 2
        self.setGeometry(x, self._scr.top() + self._region.y(), self.W, self._region.height())

    def paintEvent(self, ev):
        p = QPainter(self)
        # Windows 分层窗口会穿透 alpha=0 的像素；保留几乎不可见的底色让整条命中区可拖。
        hit_color = QColor(_HALO_GREEN)
        hit_color.setAlpha(1)
        p.fillRect(self.rect(), hit_color)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx = self.width() // 2
        p.setPen(QPen(QColor(_HALO_GREEN), 2))
        p.drawLine(cx, 0, cx, self.height())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(_HALO_GREEN))
        p.drawRoundedRect(QRectF(cx - self.GRIP_W / 2, 6, self.GRIP_W, 22), 3, 3)
        p.end()

    def refresh_locked(self):
        locked = self._owner.divider_locked()
        self.setCursor(Qt.ForbiddenCursor if locked else Qt.SizeHorCursor)
        self.setToolTip("已有拼接内容，分栏位置已固定" if locked else "左右拖动调整分栏位置")

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            if self._owner.divider_locked():
                self._owner.msg.setText("已有拼接内容，分栏线不可再调")
            else:
                self._dragging = True
                self.grabMouse()
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._dragging:
            gx = ev.globalPosition().toPoint().x()
            frac = (gx - self._scr.left() - self._region.x()) / max(1, self._region.width())
            self._place(min(1 - _MIN_COLUMN_FRACTION, max(_MIN_COLUMN_FRACTION, frac)))
            # 只动视觉，松手才提交；与实际分栏使用相同的 10% 最小宽度。
            ev.accept()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._dragging:
            self.mouseMoveEvent(ev)
            try:
                if not self._owner.commit_divider(self._frac):
                    self._place(self._owner._div)
            finally:
                self._dragging = False
                self.releaseMouse()
                self.refresh_locked()
            ev.accept()

    def event(self, ev):
        if ev.type() == QEvent.UngrabMouse and getattr(self, "_dragging", False):
            self._dragging = False
            self._place(self._owner._div)
        return super().event(ev)

    def closeEvent(self, ev):
        self._dragging = False
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        super().closeEvent(ev)
