# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — GIF 录制：框选区域 → 定时抓帧（合成鼠标指针）→ Pillow 合成动图。"""
import hashlib
import os
import tempfile

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

import common as C
import icons
from capture import RegionDim, RegionFrame
from common import copy_file_to_clipboard, pictures_dir
from longshot import grab_array
from recorder import poll_click, stamp_click_fx, stamp_cursor

FPS = 10
FRAME_MS = 1000 // FPS


def missing_deps():
    try:
        import PIL.Image          # noqa: F401
        return []
    except Exception:
        return ["Pillow"]


def save_gif(frames, durs, path):
    """P-mode 帧序列 → GIF。durs 为每帧展示毫秒数（与帧等长）。"""
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=durs, loop=0)


class _GifStream:
    """Write one quantized frame at a time instead of retaining the whole animation."""

    def __init__(self):
        self._file = tempfile.NamedTemporaryFile(
            mode="wb", prefix="._jianjie_gif_", suffix=".part",
            dir=C.clipboard_cache_dir(), delete=False)
        self.path = self._file.name
        C.retain_clipboard_media(self.path)
        self._started = False

    def append(self, frame, duration):
        from PIL import GifImagePlugin
        if not self._started:
            header, _palette = GifImagePlugin.getheader(frame, info={"loop": 0})
            self._file.writelines(header)
            self._started = True
        self._file.writelines(GifImagePlugin.getdata(
            frame, duration=duration, include_color_table=True))

    def finish(self, path):
        if not self._file.closed:
            self._file.write(b";")
            self._file.close()
        os.replace(self.path, path)
        C.release_clipboard_media(self.path)
        self.path = None

    def cancel(self):
        if not self._file.closed:
            self._file.close()
        if self.path:
            try:
                os.remove(self.path)
            except OSError:
                pass
            C.release_clipboard_media(self.path)
            self.path = None


class GifBar(QWidget):
    """GIF 录制控制条：框选确认后先就绪，点「开始录制」开录；
    下载保存到指定目录；对号只复制 GIF 文件引用到剪贴板。"""
    def __init__(self, region, screen, on_done, on_cancel):
        super().__init__(None)
        self.region = region          # 逻辑 QRect；None=整屏
        self._screen = screen
        self._on_done = on_done
        self._on_cancel = on_cancel
        self.frames = []              # 只存帧计数标记；像素数据逐帧写临时文件
        self.durs = []                # 每帧时长（静止段累加，保时序）
        self._pending_frame = None    # 最后一帧待定时长，只保留这一帧
        self._stream = None
        self._last_bytes = None
        self._size = None             # 首帧尺寸（选区缩放后归一，GIF 帧必须同幅）
        self._saved_path = None
        self._completed = False
        self._dim = None
        self._frame = None
        self.elapsed = 0.0
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
        self._toolbar_screen = _scr
        dpr = (_scr.devicePixelRatio() or 1.0) if _scr else None
        self.dot = icons.PulseDot()                # 就绪灰点；点「开始录制」后转红呼吸
        self.msg = QLabel(f"GIF 就绪 · {FPS} fps")
        self.stat = QLabel("00:00 · 0 帧")
        self.stat.setObjectName("dim")
        self.bstop = icons.tool_button("save", "停止并保存 GIF", self.stop, color="#60a5fa", dpr=dpr)
        self.bstop.setEnabled(False)               # 就绪态无帧可存：停用，开录后解禁
        self.bok = icons.tool_button("confirm", "复制 GIF 到剪贴板 (Enter)：粘贴到聊天窗即动图",
                                     self.copy_gif, color="#2ecc71", dpr=dpr)
        self.bok.setEnabled(False)
        self.bx = icons.tool_button("close", "取消 (Esc)", self.cancel, color="#f4645f", dpr=dpr)
        self.brec = icons.tool_button("rec", "开始录制", self.start, color="#ef4444", dpr=dpr)
        lay.addWidget(self.dot)
        lay.addWidget(self.msg)
        lay.addWidget(self.stat)
        sep()
        lay.addWidget(self.brec)    # ●开始：就绪态独有，开录即隐藏
        lay.addWidget(self.bstop)   # ■停止并保存：录制态可用，仅存盘不复制
        lay.addWidget(self.bx)      # × 在前，对号收尾最右——与长截图条/截图工具条「完成」位置一致
        lay.addWidget(self.bok)
        self.timer = QTimer(self)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self.grab)
        self._right_cancel = C.CaptureRightCancel(self, self.cancel, live=True)
        self.adjustSize()
        self._place_toolbar()
        C.prepare_capture_window(self)
        self.show()
        if self.region:
            self._open_live()         # 选区录制：压暗层 + 可拖动/缩放边框
        self.raise_()
        self.activateWindow()
        C.force_foreground(self)       # R121：热键后台触发时抢到键盘焦点，Esc 才能关条
        C.exclude_from_capture(self)       # 控制条不烙进 GIF（呼吸点/帧数每拍都算新内容）

    def _place_toolbar(self):
        scr = self._screen or getattr(self, "_toolbar_screen", None)
        if scr is None:
            return
        if self.region:
            C.place_toolbar_near_region(self, self.region, scr, gap=16, edge=6)
        else:
            g = scr.geometry()
            self.move(g.center().x() - self.width() // 2, g.top() + 16)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._place_toolbar()

    # ---------- 录制中的选区实时框 ----------
    def _open_live(self):
        self._close_live()
        scr = self._screen or (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        self._dim = RegionDim(scr, lambda: self.region)
        self._frame = RegionFrame(scr, self.region, self._on_region_move)
        self.raise_()
        self.activateWindow()

    def _close_live(self):
        for attr in ("_dim", "_frame"):
            w = getattr(self, attr, None)
            if w:
                w.close()
                w.deleteLater()
            setattr(self, attr, None)

    def _on_region_move(self, r):
        self.region = QRect(r)
        self._place_toolbar()
        if self._dim:
            self._dim.update()    # 压暗层事件驱动绘制：黑框条拖动须显式触发重画，否则洞与绿线留在原地分离

    def start(self):
        """「开始录制」：就绪态 → 录制态（呼吸点亮、开始钮隐藏、停止钮解禁、定时器开抓）。"""
        if self.timer.isActive():
            return
        self.dot.set_active(True)
        self.msg.setText(f"GIF 录制中 · {FPS} fps")
        self.brec.hide()
        self.bstop.setEnabled(True)
        self.bok.setEnabled(True)
        self.adjustSize()
        self._place_toolbar()
        self.raise_()
        self.activateWindow()
        self.timer.start()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key_Escape:
            self.cancel()
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and (self.timer.isActive() or self.frames or self._saved_path):
            if not ev.isAutoRepeat():
                self.copy_gif()          # 复制失败后也允许回车重试
            return
        super().keyPressEvent(ev)

    def grab(self):
        if not self.frames:
            if self._stream:
                self._stream.cancel()
                self._stream = None
            self._pending_frame = None
        try:
            arr = grab_array(self._screen, self.region)
        except Exception:
            return
        if self._size is None:
            self._size = (arr.shape[1], arr.shape[0])
        # mss/GDI 抓帧不含光标：先按物理坐标贴指针再缩放（指针随内容动，
        # 且贴在 bytes 去重之前——只有指针在动时也会产出新帧，GIF 才不冻帧）
        scr = self._screen or QGuiApplication.primaryScreen()
        d = scr.devicePixelRatio() or 1.0
        g = scr.geometry()
        poll_click()                            # R109 点击边沿检测：与抓帧同节拍
        # Windows Qt 屏幕原点已是原生桌面坐标；只缩放屏内裁剪偏移。
        stamp_cursor(arr, g.x(), g.y(),
                     ox=int(self.region.x() * d) if self.region else 0,
                     oy=int(self.region.y() * d) if self.region else 0,
                     bgr_order=False)
        stamp_click_fx(arr, g.x(), g.y(),   # R109 GIF 同享点击高亮（无开关默认开）
                       ox=int(self.region.x() * d) if self.region else 0,
                       oy=int(self.region.y() * d) if self.region else 0,
                       bgr_order=False)
        if (arr.shape[1], arr.shape[0]) != self._size:
            from PIL import Image as _Image
            import numpy as _np
            # R108 轻量版无 cv2：PIL 双线性 ≡ cv2.resize 默认 INTER_LINEAR，尺寸同为 (w,h)
            arr = _np.asarray(_Image.fromarray(arr, "RGB").resize(self._size, _Image.BILINEAR))
        digest = hashlib.blake2b(arr.tobytes(), digest_size=16).digest()
        if digest == self._last_bytes:
            # 画面未变：不叠帧，延长上一帧展示时长（Pillow 会丢弃相同连续帧，
            # 且丢帧会破坏时序；延时累加才能保住 GIF 时长）
            if self.durs:
                self.durs[-1] += FRAME_MS
            self.elapsed += FRAME_MS / 1000.0
            self.stat.setText(f"{int(self.elapsed // 60):02d}:{int(self.elapsed % 60):02d} · {len(self.frames)} 帧")
            return
        self._last_bytes = digest
        from PIL import Image
        im = Image.fromarray(arr, "RGB")
        if self._pending_frame is not None:
            try:
                if self._stream is None:
                    self._stream = _GifStream()
                self._stream.append(self._pending_frame, self.durs[-1])
            except Exception as exc:
                self.timer.stop()
                self.msg.setText("GIF 写入失败：" + str(exc))
                return
        self._pending_frame = im.convert("P", palette=Image.ADAPTIVE, colors=256)
        self.frames.append(None)
        self.durs.append(FRAME_MS)
        self.elapsed += FRAME_MS / 1000.0
        self.stat.setText(f"{int(self.elapsed // 60):02d}:{int(self.elapsed % 60):02d} · {len(self.frames)} 帧")

    def _finish(self, copy):
        if self._completed:
            return
        self.timer.stop()
        self.dot.set_active(False)
        self._close_live()
        if not self.frames and not self._saved_path:
            self.msg.setText("未捕获到画面，请重新录制")
            self.brec.show()
            self.adjustSize()
            return
        if not self._saved_path:
            path = None
            try:
                path = C.clipboard_media_path("GIF", ".gif")
                if self._stream is None:
                    self._stream = _GifStream()
                if self._pending_frame is not None:
                    self._stream.append(self._pending_frame, self.durs[-1])
                    self._pending_frame = None
                self._stream.finish(path)
                self._stream = None
            except Exception:
                C.release_clipboard_media(path)
                self.msg.setText("GIF 生成失败，请重试；录制内容已保留")
                self.adjustSize()
                return
            self._saved_path = path
            self.frames = []
            self.durs = []
            self._last_bytes = None
        copied = bool(copy and copy_file_to_clipboard(self._saved_path))
        if copy and not copied:
            self.msg.setText("复制失败，请再点对号重试；GIF 已保留")
            self.adjustSize()
            return
        if not copy:
            try:
                self._saved_path = C.publish_media_file(self._saved_path, pictures_dir())
            except Exception:
                self.msg.setText("保存失败，请检查目录后重试；GIF 已保留")
                self.adjustSize()
                return
        C.release_clipboard_media(self._saved_path)
        self._completed = True
        self.close()
        self._on_done(self._saved_path, copied)

    def stop(self):
        """「停止并保存」：存盘，不复制。"""
        self._finish(copy=False)

    def copy_gif(self):
        """对号只复制 GIF 文件引用，临时载体留在内部缓存。"""
        self._finish(copy=True)

    def cancel(self):
        self.close()
        self._on_cancel()

    def closeEvent(self, ev):
        self.timer.stop()
        self._close_live()
        self.frames = []
        self.durs = []
        if self._stream:
            self._stream.cancel()
            self._stream = None
        if not self._completed and self._saved_path:
            try:
                os.remove(self._saved_path)
            except OSError:
                pass
            C.release_clipboard_media(self._saved_path)
            self._saved_path = None
        self._pending_frame = None
        self._last_bytes = None
        ev.accept()
