# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 主面板：无边框 Fluent 风格启动器（动作，搜索/直达键）。"""
import ctypes
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

import common as C
import icons
from jj_variant import LITE
from panel_lifecycle import PanelLaunchSession

G1 = "内置简截工具"
G3 = "系统"

# (group, name, glyph, bg, desc, hotkey, controller_method) —— 单字字形图标 + 块状网格，密度优先
ACTIONS_FULL = [
    (G1, "截图", "截", "#3b82f6", "框选 · 标注 · 复制/钉图", "A", "shot"),
    (G1, "长截图", "长", "#10b981", "滚动拼接 · 可编辑标注长图", "S", "long"),
    (G1, "录屏", "录", "#0ea5e9", "录制选区 MP4", "R", "rec"),   # R136：去红（用户点名「录屏的图标默认不要用红色」）
    (G1, "屏幕取色", "色", "#d946ef", "放大镜取色 · 色值入板", "C", "colorpick"),   # R103
    (G1, "提取文字", "文", "#f59e0b", "Windows 离线 OCR", "O", "ocr"),
    (G1, "剪贴板历史", "贴", "#64748b", "最近复制记录", None, "clip"),
    (G1, "简捷互传", "传", C.UI_BADGE_TO, "手机与电脑 · 图片 / 文字 / 文件", None, "phone_receive"),
    (G1, "iPhone 投屏", "投", "#6366f1", "AirPlay · 手机画面与声音", None, "iphone_mirror"),
    (G1, "一键快剪", "剪", "#f97316", "按秒截取片段", None, "trim"),
    (G1, "一键改图", "图", "#14b8a6", "画图打开最近截图", None, "imgedit"),
    (G1, "二维码生成", "生", "#78716c", "文本生成二维码", None, "qr_gen"),
    (G1, "日历", "历", "#e11d48", "节假日与调休", "K", "calendar"),
    (G1, "网速", "速", "#2dd4bf", "悬浮网速 + 一键测速", "N", "netspeed"),
    (G1, "番茄钟", "钟", "#fb7185", "专注 25 分钟", "B", "pomodoro"),
    (G1, "文件搜索", "搜", "#22c55e", "Everything 式全盘秒搜", None, "fsearch"),
    (G1, "任务清单", "务", "#eab308", "随手记 · 勾选完成", None, "note"),   # R87b 面板入口
    (G3, "锁屏", "锁", "#6366f1", "立即锁定电脑", "L", "lock"),   # R124 由 G1 挪入系统组
    (G3, "快速回桌", "桌", "#8b5cf6", "模拟 Win+D", "W", "showdesk"),
    # R139：「明暗主题切换」块退役（用户点名「面板最下方的那个切换主题就可以去掉了」，
    # 右上角头部已有 sun-moon 钮 toggle_theme_placeholder，功能重复）；Controller.theme 保留
]

# R107/R108 轻量版（截图+长截图+GIF）：只留核心动作 + 系统组（设置性质不算功能入口）
# GIF 无面板块，入口在截图工具条（capture.py），与全量版同式
_ACTIONS_LITE_KEEP = {"截图", "长截图"}
ACTIONS = ([a for a in ACTIONS_FULL if a[0] == G3 or a[1] in _ACTIONS_LITE_KEEP]
           if LITE else ACTIONS_FULL)

# R133 lucide 图标映射（meth → lucide name，design.md §3；ACTIONS 的 glyph 单字字段随之退役）
ACT_LUCIDE = {
    "shot": "scissors", "long": "scroll", "rec": "monitor-play", "colorpick": "pipette",
    "ocr": "scan-text", "clip": "clipboard-list", "trim": "film", "imgedit": "paintbrush",
    "qr_gen": "qr-code", "phone_receive": "smartphone", "calendar": "calendar-days", "netspeed": "gauge",
    "pomodoro": "timer", "fsearch": "file-search", "note": "list-todo",
    "lock": "lock", "showdesk": "monitor", "iphone_mirror": "airplay",
}   # R139：theme 映射随面板块退役（头部 sun-moon 钮为硬编码图标，不走本表）


class _HoverIconBtn(QPushButton):
    """R159 头部图标钮 hover 图标变亮（用户点名「关闭等那 3 个按钮悬浮还是暗的，图标应该变亮」）：
    QSS 的 color 管不到 QIcon 已着色像素——预生成常态(tx2)/悬停(tx) 双图标，enter/leave 切换；
    深色 a7adbc→eef1f7 变亮、浅色 4c5364→1d1d2b 变深，同向主文本色收＝对比增强。
    retheme 只刷 QSS 不重建图标，retint(pal) 供其重生成双图标（防跨主题残色）。"""

    def __init__(self, name, size, sw, pal, parent=None):
        super().__init__(parent)
        self._ic_name, self._ic_size, self._ic_sw = name, size, sw
        self.retint(pal)

    def _apply(self, ic):
        self._cur = ic                      # 自存引用：PySide icon() 返回值类型不保证同一对象
        self.setIcon(ic)

    def retint(self, pal):
        self._ic_n = icons.lucide_icon(self._ic_name, pal['tx2'], self._ic_size, sw=self._ic_sw)
        self._ic_h = icons.lucide_icon(self._ic_name, pal['tx'], self._ic_size, sw=self._ic_sw)
        self._apply(self._ic_h if self.underMouse() else self._ic_n)

    def enterEvent(self, e):
        self._apply(self._ic_h)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._apply(self._ic_n)
        super().leaveEvent(e)


class Panel(QWidget):
    actionTriggered = Signal(str, str)   # (method, arg)

    def __init__(self, settings):
        super().__init__(None)
        # 不用 WindowStaysOnTop：呼出时置前，点其他窗口时正常被盖住（同普通应用）
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setWindowTitle("简截 Lite" if LITE else "简截")
        self._storage_paths = {}
        self._storage_text = ""             # R116 单行显示文本（由 refresh_storage 填）
        self._storage_state = "unset"       # ready / unset / broken（决定路径行配色）
        self.setWindowIcon(C.app_qicon())    # 任务栏按钮用应用图标，不落回 pythonw 默认
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.S = settings
        self.launch_session = PanelLaunchSession(self)
        self.pal = C.palette(settings.get("theme", "light"))   # R133 默认浅色 mac 风
        self.resize(680, 640)
        self.setMinimumSize(560, 460)              # R127：主面板支持八向缩放
        self._edge_resizer = C.EdgeResizer(self, 560, 460)
        self.build_ui()
        self.setStyleSheet(self.qss())

    # ---------- QSS ----------
    def qss(self):
        p = self.pal
        # R134 浅色卡片去硬边（用户点名「边缘紧紧挨在一起不符合标准」）：
        # 描边退役，柔和投影托底（make_block/storageCard 处挂 QGraphicsDropShadowEffect）；
        # R159 深色同退（用户点名「那些应用的外边框都去掉，保存位置那条也去掉」）：
        # DARK line=rgba(255,255,255,20) 浅白线高分屏抗锯齿发虚，深色分区靠 card 14→hover 24
        # 底色梯度（PLUGIN_QSS 无描边判例同族）；窗口壳 root 1px 边保留＝无边框窗边缘界定
        # R145 暗色头部钮去描边（用户点名「右上角的图标，边框有点模糊」）——1px 半透明描边
        # 抗锯齿发虚，与 R134/R141「深色悬浮条无描边」判例同根收敛；底色 14→18 微提撑形态、
        # hover 24→28，深浅分区靠底色梯度不再靠描边。浅色保留 1px 细边（白钮浮灰底需要边界）
        _dark = self.pal is C.DARK
        ic_bg = "rgba(255,255,255,18)" if _dark else p['card']
        ic_hv = "rgba(255,255,255,28)" if _dark else p['card_hover']
        ic_bd = "none" if _dark else f"1px solid {p['line']}"
        return f"""
        QWidget {{ color:{p['tx']}; font-family:'Segoe UI Variable Text','Segoe UI','Microsoft YaHei UI'; }}
        #root {{ background:{p['panel']}; border:1px solid {p['line']}; border-radius:10px; }}   /* R146 圆角收 mac 档（HIG 唯一裁决：窗 10/卡 10） */
        #pillBtn {{ background:{p['card']}; color:{p['tx2']}; border:none; border-radius:10px;
                    padding:6px 16px; font-size:13px; }}
        #pillBtn:checked {{ background:{p['accent']}; color:#fff; font-weight:600; }}
        #iconBtn {{ background:{ic_bg}; color:{p['tx2']}; border:{ic_bd}; border-radius:8px;
                    padding:5px 8px; font-size:12.5px; }}
        #iconBtn:hover {{ background:{ic_hv}; color:{p['tx']}; }}
        QScrollArea {{ border:none; background:transparent; }}
        QScrollArea > QWidget {{ background:transparent; }}
        #body {{ background:{p['panel_solid']}; border-radius:10px; }}   /* R136 灰底圆角；R146 收 10 */
        #actBlock {{ background:{p['card']}; border:none; border-radius:10px; }}
        #actBlock:hover {{ background:{p['card_hover']}; }}
        #tile {{ background:{p['card']}; border:none; border-radius:10px; }}
        #tile:hover {{ background:{p['card_hover']}; }}
        QLabel {{ background:transparent; }}
        #nameLbl {{ font-size:12.5px; font-weight:600; color:{p['tx']}; }}
        /* R131 辅助说明「发虚」根治：弱文本去 Segoe Variable（细字重小字号发虚）
           回常规 Segoe UI + 色提一档（tx3→#8a93a6）+ 字号微升 */
        #descLbl {{ font-size:11.5px; color:{p['tx2']};
                    font-family:'Segoe UI','Microsoft YaHei UI'; }}
        #keyLbl {{ color:#8a93a6; font-size:11px;
                   font-family:'Segoe UI','Microsoft YaHei UI'; }}
        #footLbl {{ color:#8a93a6; font-size:11.5px;
                    font-family:'Segoe UI','Microsoft YaHei UI'; }}
        #emLbl {{ color:#fff; font-size:15px; font-weight:700; }}
        #emLbl[lucide="true"] {{ background:transparent; }}   /* R133 lucide 图标块：着色在 pixmap */
        #storageCard {{ background:{p['card']}; border:none; border-radius:{C.UI_RADIUS}px; }}
        #storageChip {{ background:#0ea5e9; color:#fff; border-radius:5px; font-size:9px; font-weight:700; }}
        #storagePath {{ font-size:11.5px; color:{p['tx2']};
                        font-family:'Segoe UI','Microsoft YaHei UI'; }}
        #storageSetup {{ background:{C.UI_ACCENT}; color:#fff; border:2px solid transparent; border-radius:6px;
                           padding:3px 10px; font-size:11px; }}
        #storageSetup:hover {{ background:{p['accent2']}; }}
        #storageSetup:focus {{ border:2px solid {p['tx']}; }}
        #storageOpen {{ background:transparent; color:{p['accent2']}; border:none;
                          border-radius:6px; padding:3px 8px; font-size:11px; }}
        #storageOpen:hover, #storageOpen:focus {{ background:{p['card_hover']}; }}
        #storageError {{ color:#ef4444; font-size:11px; }}
        """

    def build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        root = QWidget()
        root.setObjectName("root")
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        # 头部
        head = QHBoxLayout()
        self._logo = QLabel()                # 头部品牌标：与托盘/任务栏同一程序化图标源
        _logo_dpr = self.devicePixelRatioF()      # R140：按屏 DPR 物理渲染（用户点名「左上角不太精致」——
        _logo_pm = C.app_icon(round(34 * _logo_dpr))   # 34px 逻辑图在 125%/150% 屏被拉伸发糊是主凶）
        _logo_pm.setDevicePixelRatio(_logo_dpr)
        self._logo.setPixmap(_logo_pm)
        self._logo.setFixedSize(34, 34)
        self._logo.setAlignment(Qt.AlignCenter)
        title = QLabel("简截")
        title.setStyleSheet("font-size:16px;font-weight:800;")
        title.setAttribute(Qt.WA_TransparentForMouseEvents)   # R127 穿透：标题区可拖拽窗口
        ver = QLabel("JianJie for Windows")
        ver.setObjectName("descLbl")
        ver.setAttribute(Qt.WA_TransparentForMouseEvents)     # R127 同上
        head.addWidget(self._logo)
        head.addSpacing(4)
        head.addWidget(title)
        head.addWidget(ver)
        head.addStretch()
        settings = _HoverIconBtn("settings", 16, sw=2.0, pal=self.pal)
        settings.setObjectName("iconBtn")
        settings.setIconSize(QSize(16, 16))
        settings.setToolTip("快捷键、提示位置、开机自启动与截图指针")
        settings.setAccessibleName("设置")
        settings.setCursor(Qt.PointingHandCursor)
        settings.clicked.connect(lambda: self.fire("settings", ""))
        head.addWidget(settings)
        gear = _HoverIconBtn("sun-moon", 15, sw=2.4, pal=self.pal)  # R145 同上
        gear.setObjectName("iconBtn")
        gear.setIconSize(QSize(15, 15))
        gear.setToolTip("切换明暗主题")
        gear.clicked.connect(self.toggle_theme_placeholder)
        head.addWidget(gear)
        closeb = _HoverIconBtn("x", 18, sw=2.4, pal=self.pal)   # R137：叉 15→18 补偿（lucide x 天然 bbox 仅 50%，与键盘/主题钮视觉拉齐）；R145 sw 2.4
        closeb.setObjectName("iconBtn")
        closeb.setIconSize(QSize(18, 18))
        closeb.setToolTip("关闭面板（托盘图标仍在）")
        closeb.clicked.connect(self.hide)
        head.addWidget(closeb)
        lay.addLayout(head)

        # R116 存放条：单行紧凑（芯片＋名目＋路径＋打开/更改），分类明细收进 tooltip；
        # 错误行是瞬时的第二行，仅在设置/读取失败时出现
        self.storage_card = QFrame()
        self.storage_card.setObjectName("storageCard")
        self._soft_shadow(self.storage_card)      # R134 浅色柔影（深色主题内部跳过）
        box = QVBoxLayout(self.storage_card)
        box.setContentsMargins(10, 6, 10, 6)
        box.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        chip = QLabel()
        chip.setObjectName("storageChip")
        chip.setFixedSize(16, 16)
        chip.setAlignment(Qt.AlignCenter)
        chip.setPixmap(icons.lucide("folder", "#ffffff", 10, sw=2.4))   # R133 lucide 替单字
        chip.setToolTip("截图、录屏、任务、索引与互传文件的存储位置")
        row.addWidget(chip)
        self.storage_title = QLabel("存放位置")
        self.storage_title.setObjectName("descLbl")
        row.addWidget(self.storage_title)
        self.storage_value = QLabel()
        self.storage_value.setObjectName("storagePath")
        self.storage_value.setTextFormat(Qt.PlainText)
        self.storage_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.storage_value.setAccessibleName("文件存放位置")
        row.addWidget(self.storage_value, 1)
        self.storage_open = QPushButton("打开")
        self.storage_open.setObjectName("storageOpen")
        self.storage_open.setCursor(Qt.PointingHandCursor)
        self.storage_open.setAccessibleName("打开存放目录")
        self.storage_open.clicked.connect(lambda: self.fire("open_save_dir", "root"))
        row.addWidget(self.storage_open)
        self.storage_setup = QPushButton("设置")
        self.storage_setup.setObjectName("storageSetup")
        self.storage_setup.setCursor(Qt.PointingHandCursor)
        self.storage_setup.clicked.connect(lambda: self.fire("save_dir", ""))
        row.addWidget(self.storage_setup)
        box.addLayout(row)
        self.storage_error = QLabel()
        self.storage_error.setObjectName("storageError")
        self.storage_error.setTextFormat(Qt.PlainText)
        self.storage_error.setWordWrap(True)
        self.storage_error.hide()
        box.addWidget(self.storage_error)
        lay.addWidget(self.storage_card)

        # 主体滚动区（body 显式底色，防止 viewport 透出黑色）
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 动作网格不得横向滚动
        body = QWidget()
        body.setObjectName("body")
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(6, 4, 6, 6)   # R134：柔影需要呼吸边距（原 2px 会裁影）
        self.body_lay.setSpacing(6)
        self.scroll.setWidget(body)
        lay.addWidget(self.scroll, 1)

        # 底部
        foot = QHBoxLayout()
        hk = self.S.get("hotkey_panel", "Ctrl+Alt+Space")
        f = QLabel(f"呼出 {hk} · Esc 隐藏面板")   # R97：搜索框已去（一屏看全），提示同步
        f.setObjectName("footLbl")
        f.setAttribute(Qt.WA_TransparentForMouseEvents)       # R127 底部也可拖窗
        foot.addWidget(f)
        foot.addStretch()
        v = QLabel("v" + C.APP_VERSION)
        v.setObjectName("footLbl")
        v.setAttribute(Qt.WA_TransparentForMouseEvents)
        foot.addWidget(v)
        lay.addLayout(foot)

        self.rows = []          # (block, name, desc)
        self.refresh()

    # ---------- 渲染 ----------
    COLS = 3                     # 块状网格列数：一屏多列，减少滚动

    def refresh(self):
        while self.body_lay.count():
            it = self.body_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self.rows = []
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        cur_group = None
        r = c = 0
        for g, name, glyph, bg, desc, hk, meth in ACTIONS:
            if g != cur_group:
                cur_group = g
                if c:                     # 上一组末行未满：先换行，组标题独占一行
                    r += 1
                    c = 0
                gl = QLabel(g)
                gl.setStyleSheet(f"color:{self.pal['tx3']};font-size:11px;padding:2px 2px 0 2px;")
                grid.addWidget(gl, r, 0, 1, self.COLS)
                r += 1
            b = self.make_block(name, glyph, bg, desc, hk, meth)
            grid.addWidget(b, r, c)
            self.rows.append((b, name, desc))
            c += 1
            if c >= self.COLS:
                r += 1
                c = 0
        self.body_lay.addWidget(holder)
        self.body_lay.addStretch()

    def _soft_shadow(self, w):
        """R134 mac 浅卡：硬边描边退役，柔和投影托底（blur 12 / y+2 / 8% 黑——边缘不再「紧紧挨在一起）。"""
        if self.pal is not C.LIGHT:
            return
        se = QGraphicsDropShadowEffect(w)
        se.setBlurRadius(12)
        se.setOffset(0, 2)
        se.setColor(QColor(30, 32, 44, 20))
        w.setGraphicsEffect(se)

    def make_block(self, name, glyph, bg, desc, hk, meth):
        b = QFrame()
        b.setObjectName("actBlock")
        self._soft_shadow(b)                      # R134 浅色柔影
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(f"{name}（{desc}）" + (f" · 直达 {hk}" if hk else ""))
        lay = QHBoxLayout(b)
        lay.setContentsMargins(9, 8, 10, 8)
        lay.setSpacing(9)
        em = QLabel()
        em.setObjectName("emLbl")
        em.setProperty("lucide", "true")          # R133：取消 #emLbl 白字规则（图标非文字）
        em.setFixedSize(30, 30)
        em.setAlignment(Qt.AlignCenter)
        _c = QColor(bg)                           # mac/iOS 范式：主题色 12% 淡底 + 同色 lucide glyph
        em.setStyleSheet(f"background:rgba({_c.red()},{_c.green()},{_c.blue()},30);border-radius:8px;")
        em.setPixmap(icons.lucide(ACT_LUCIDE.get(meth, "scissors"), bg, 15))
        col = QVBoxLayout()
        col.setSpacing(0)
        n = QLabel(name)
        n.setObjectName("nameLbl")
        d = QLabel(desc)
        d.setObjectName("descLbl")
        for lbl in (n, d):                # 文本不参与最小宽度：三列始终等宽，杜绝横向溢出
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(n)
        col.addWidget(d)
        lay.addWidget(em)
        lay.addLayout(col, 1)
        b.fire = lambda m=meth, a=name: self.fire(m, a)
        b.mousePressEvent = lambda ev, w=b: w.fire() if ev.button() == Qt.LeftButton else None
        return b

    def fire(self, meth, arg):
        self.actionTriggered.emit(meth, arg)

    def toggle_theme_placeholder(self):
        # 设置页精简：主题切换直接生效
        self.fire("theme", "")

    def refresh_storage(self):
        try:
            configured, ready, self._storage_paths = C.storage_locations()
            self.S["save_dir"] = configured
            if ready:
                self.storage_title.setText("存放位置")
                self.storage_setup.setText("更改位置")
                self._storage_text = configured
                self._storage_state = "ready"
            else:
                self.storage_title.setText("先设置存放位置")
                self.storage_setup.setText("设置")
                self._storage_text = ("原位置不可用，请更改" if configured
                                      else self._storage_paths["pictures"])
                self._storage_state = "broken" if configured else "unset"
            self._render_storage_paths()
            self.storage_error.hide()
        except OSError as e:
            self.show_storage_error("无法读取保存目录：" + str(e))

    def _render_storage_paths(self):
        """R116：单行中间省略 + tooltip 全量明细（每类一行）；宽度变化/主题切换时重刷。"""
        kinds = [("pictures", "截图 / 长截图 / GIF / 录屏"),
                 ("data", "任务 / 设置 / 文件索引"), ("receive", "收到的互传文件")]
        detail = "\n".join(f"{text}：{self._storage_paths.get(kind, '')}" for kind, text in kinds)
        v = self.storage_value
        v.setText(v.fontMetrics().elidedText(self._storage_text, Qt.ElideMiddle, max(80, v.width())))
        v.setToolTip(detail)
        v.setAccessibleDescription(detail)
        color = {"ready": "", "unset": self.pal["tx3"], "broken": "#ef4444"}[self._storage_state]
        v.setStyleSheet(f"color:{color};" if color else "")

    def show_storage_error(self, message):
        self.storage_error.setText(message)
        self.storage_error.show()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._render_storage_paths()

    # ---------- 定位与拖动 ----------
    def popup(self):
        self.setAttribute(Qt.WA_DontShowOnScreen, False)
        try:
            scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            if scr is not None:
                g = scr.geometry()
                pos = self.S.get("panel_pos")
                if isinstance(pos, (list, tuple)) and len(pos) == 2:
                    x = max(g.left() + 8, min(int(pos[0]), g.right() - self.width() - 8))
                    y = max(g.top() + 8, min(int(pos[1]), g.bottom() - self.height() - 8))
                    self.move(x, y)
                else:
                    self.move(g.center().x() - self.width() // 2,
                              g.center().y() - self.height() // 2)
        except Exception:
            pass
        self.showNormal()
        self.raise_()
        self.activateWindow()
        try:
            self.refresh_storage()
        except Exception:
            pass
        try:                     # Windows 前台锁：自启后首次呼出可能被压在别的窗口后面
            ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(int(self.winId())))
        except Exception:
            pass

    def prewarm(self):
        """启动预热：首点慢的根因是首次 show 才付的原生窗创建/DWM 分层/字体引擎开销。
        WA_DontShowOnScreen 屏外走完 show 管线（polish/QSS/布局），winId() 强制建原生窗，
        之后 popup() 只是复用既有窗口映射上屏，托盘首点即开。不抢焦点、不上屏。"""
        if self.isVisible():
            return
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.show()
        self.winId()                     # 强制创建原生平台窗口（show 管线不含显式建窗）
        self.hide()
        self.setAttribute(Qt.WA_DontShowOnScreen, False)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._drag_off = ev.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        off = getattr(self, "_drag_off", None)
        if off and (ev.buttons() & Qt.LeftButton):
            self.move(ev.globalPosition().toPoint() - off)
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if getattr(self, "_drag_off", None):
            self._drag_off = None
            self.S["panel_pos"] = [self.x(), self.y()]
            C.save_settings(self.S)
        super().mouseReleaseEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.hide()
        super().keyPressEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        C.wire_screen_adaptive(self, 560, 460)     # R127 主面板跨屏自适应（同任务清单）

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))
        p.end()
