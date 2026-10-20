"""iPhone AirPlay: optional media runtime, owned receiver process, modeless controls."""
import ctypes
import hashlib
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlsplit
import zipfile

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QProgressBar, QPushButton, QVBoxLayout)

import common as C
from widgets import CardDialog, _WinHttpConnection


def bundled_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "native" / "airplay"


def component_dir():
    return Path(C.data_dir()) / "mirroring" / "airplay-1"


def metadata():
    return json.loads((bundled_dir() / "manifest.json").read_text(encoding="utf-8"))


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_runtime(archive, cancelled=None, progress=None):
    """Validate the pinned upstream ZIP; extract only the receiver's media runtime."""
    meta = metadata()
    archive = Path(archive)
    if archive.stat().st_size != meta["runtimeBytes"] or _sha(archive) != meta["runtimeSha256"]:
        raise ValueError("组件包不匹配，请使用此版本提供的原始组件包。")
    root = component_dir()
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".airplay-", dir=root.parent) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(archive) as z:
            selected = []
            for item in z.infolist():
                name = item.filename.replace("\\", "/")
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts or ":" in name:
                    raise ValueError("组件包包含无效路径。")
                keep = ("/" not in name and name.lower().endswith(".dll")
                        and not name.lower().startswith(("qt6", "q"))
                        and name.lower() != "dnssd.dll")
                keep = keep or name.startswith("lib/gstreamer-1.0/") or name == "LICENSE.rtf"
                if keep and not item.is_dir():
                    selected.append((item, relative))
            required = {"libgstreamer-1.0-0.dll", "libplist-2.0.dll"}
            if not required.issubset({p.name for _, p in selected}):
                raise ValueError("组件包缺少投屏运行库。")
            if shutil.disk_usage(root.parent).free < sum(i.file_size for i, _ in selected) + 8 * 1024**2:
                raise OSError("应用数据目录空间不足，请更改存储位置后重试。")
            for index, (item, relative) in enumerate(selected):
                if cancelled and cancelled.is_set():
                    raise InterruptedError()
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
                if progress:
                    progress(90 + int((index + 1) * 9 / len(selected)))
        for name in ("receiver.exe", "LICENSE", "source.zip", "manifest.json"):
            shutil.copy2(bundled_dir() / name, staging / name)
        if _sha(staging / "receiver.exe") != meta["engineSha256"]:
            raise ValueError("投屏引擎校验失败。")
        if cancelled and cancelled.is_set():
            raise InterruptedError()
        # The receiver is stopped before installation; files remain inside its private directory.
        root.mkdir(exist_ok=True)
        for source in staging.iterdir():
            target = root / source.name
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                os.replace(source, target)
        (root / "ready.json").write_text(json.dumps({
            "runtimeSha256": meta["runtimeSha256"],
            "files": {str(relative): item.file_size for item, relative in selected}
        }), encoding="utf-8")
    return root


class _DownloadConnection(_WinHttpConnection):
    _NO_PROXY = 4  # WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY: use Windows proxy configuration.


class MirrorSession(QObject):
    changed = Signal(str, str)
    progress = Signal(int)
    prepared = Signal(int, object)
    output = Signal(int, str)
    exited = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.detail = "接收服务会自动准备，请从 iPhone 的「屏幕镜像」发起连接。"
        self.pin = ""
        self.process = None
        self._event = None
        self._generation = 0
        self._cancel = None
        self._preparing = False
        self._stopping = False
        self._closing = False
        self._tail = []
        self._options = {}
        self._failure = ""
        self.discovery = {}
        self.video = {}
        self.prepared.connect(self._prepared)
        self.output.connect(self._output)
        self.exited.connect(self._exited)

    def _state(self, state, text):
        self.state, self.detail = state, text
        try:
            path = component_dir().parent / "session-status.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_suffix(".pending")
            pending.write_text(json.dumps({"build": C.APP_BUILD_ID, "state": state,
                                          "detail": text,
                                          "discovery": self.discovery,
                                          "video": self.video,
                                          "pid": self.process.pid if self.process else None},
                                         ensure_ascii=False), encoding="utf-8")
            os.replace(pending, path)
        except OSError:
            pass
        if not self._closing:
            self.changed.emit(state, text)

    def start(self, name, audio=True, archive="", quality="1080p", fps=60, low_latency=True):
        if self.process is not None or self._preparing:
            return
        name = name.strip()
        if not name or len(name.encode("utf-8")) > 60 or any(ord(c) < 32 for c in name):
            self._state("error", "投屏名称太长或包含无效字符，请缩短后重试。")
            return
        self._generation += 1
        generation = self._generation
        self._stopping = False
        self._failure = ""
        self._options = {"name": name, "audio": bool(audio),
                         "quality": quality if quality in ("720p", "1080p") else "1080p",
                         "fps": fps if fps in (30, 60) else 60, "low_latency": bool(low_latency)}
        self.discovery = {}
        self.video = {}
        self.pin = str(secrets.randbelow(9000) + 1000)
        self._cancel = threading.Event()
        self._preparing = True
        self._state("preparing", "正在准备接收服务，已安装的组件会直接复用。")

        def prepare():
            result = None
            temporary = None
            try:
                root, meta = component_dir(), metadata()
                ready = root / "ready.json"
                cached = json.loads(ready.read_text()) if not archive and ready.is_file() else {}
                if archive:
                    result = install_runtime(archive, self._cancel, self.progress.emit)
                elif (cached.get("runtimeSha256") == meta["runtimeSha256"]
                        and cached.get("files") and all(
                            (root / name).is_file() and (root / name).stat().st_size == size
                            for name, size in cached["files"].items())):
                    for filename in ("receiver.exe", "manifest.json", "source.zip", "LICENSE"):
                        origin, target = bundled_dir() / filename, root / filename
                        if not target.is_file() or _sha(target) != _sha(origin):
                            shutil.copy2(origin, target)
                    result = root
                else:
                    root.parent.mkdir(parents=True, exist_ok=True)
                    fd, filename = tempfile.mkstemp(prefix=".airplay-download-", suffix=".zip",
                                                    dir=root.parent)
                    os.close(fd)
                    temporary = Path(filename)
                    url = urlsplit(meta["runtimeUrl"])
                    connection = _DownloadConnection(url.hostname, 443, 20, True)
                    try:
                        connection.request("GET", url.path, headers={"User-Agent": "JianJie-AirPlay"})
                        response = connection.getresponse()
                        if response.status != 200:
                            raise OSError("下载服务暂不可用，请重试或导入本地组件包。")
                        received = 0
                        with temporary.open("wb") as target:
                            while not self._cancel.is_set():
                                chunk = response.read(256 * 1024)
                                if not chunk:
                                    break
                                target.write(chunk)
                                received += len(chunk)
                                if received > meta["runtimeBytes"]:
                                    raise ValueError("组件包大小异常。")
                                self.progress.emit(min(89, int(received * 89 / meta["runtimeBytes"])))
                        if self._cancel.is_set():
                            raise InterruptedError()
                    finally:
                        connection.close()
                    result = install_runtime(temporary, self._cancel, self.progress.emit)
            except InterruptedError:
                result = None
            except Exception as error:
                result = str(error)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
                if not self._closing:
                    self.prepared.emit(generation, result)

        threading.Thread(target=prepare, name="JianJieAirPlayPrepare", daemon=True).start()

    def _prepared(self, generation, result):
        if generation != self._generation or self._closing:
            return
        self._preparing = False
        if self._cancel.is_set() or result is None:
            self.pin = ""
            self._state("idle", "已取消准备。重新打开投屏窗口后，可从 iPhone 发起连接。")
        elif isinstance(result, str):
            self.pin = ""
            self._state("error", result)
        else:
            self._launch(result, generation)

    def _launch(self, root, generation):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateEventW.restype = ctypes.c_void_p
        kernel.SetEvent.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        event_name = "Local\\JianJieAirPlay-" + secrets.token_hex(12)
        handle = kernel.CreateEventW(None, True, False, event_name)
        if not handle:
            self._state("error", "无法创建投屏会话，请重试。")
            return
        self._event = (kernel, handle)
        environment = os.environ.copy()
        environment["PATH"] = str(root) + os.pathsep + os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        environment["GST_PLUGIN_PATH_1_0"] = str(root / "lib/gstreamer-1.0")
        environment["GST_PLUGIN_SYSTEM_PATH_1_0"] = str(root / "lib/gstreamer-1.0")
        environment["GST_REGISTRY_1_0"] = str(root / "gst-registry.bin")
        environment["GST_REGISTRY_FORK"] = "no"
        # Prefer the renderer's Direct3D API; keep decodebin's other hardware/software fallbacks.
        environment["GST_PLUGIN_FEATURE_RANK"] = "d3d11h264dec:512,d3d11h265dec:512"
        config = root / "receiver.conf"
        config.write_text("", encoding="utf-8")
        environment["UXPLAYRC"] = str(config)
        environment["JIANJIE_AIRPLAY_STOP_EVENT"] = event_name
        environment["JIANJIE_AIRPLAY_PARENT_PID"] = str(os.getpid())
        dimensions = "1920x1080" if self._options["quality"] == "1080p" else "1280x720"
        command = [str(root / "receiver.exe"), "-n", self._options["name"], "-nh",
                   "-pin", self.pin, "-vs", "d3d11videosink", "-vc", "d3d11convert",
                   "-s", dimensions + "@" + str(self._options["fps"]),
                   "-fps", str(self._options["fps"])]
        if self._options["low_latency"]:
            command += ["-vsync", "no"]
        if not self._options["audio"]:
            command += ["-as", "0"]
        try:
            self.process = subprocess.Popen(command, cwd=root, env=environment,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
        except OSError as error:
            self._close_event()
            self._state("error", "投屏启动失败：" + str(error))
            return
        try:
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.AllowSetForegroundWindow.argtypes = [ctypes.c_uint]
            user.AllowSetForegroundWindow.restype = ctypes.c_int
            user.AllowSetForegroundWindow(self.process.pid)
        except (OSError, AttributeError):
            pass
        self._tail = []
        self._state("starting", "正在开启接收服务，稍后可在 iPhone 上选择此电脑。")
        process = self.process

        def read():
            try:
                for raw in iter(lambda: process.stdout.readline(2048), b""):
                    if not self._closing:
                        self.output.emit(generation, raw.decode("utf-8", errors="replace").strip())
            finally:
                process.stdout.close()
                code = process.wait()
                if not self._closing:
                    self.exited.emit(generation, code)

        threading.Thread(target=read, name="JianJieAirPlayOutput", daemon=True).start()
        QTimer.singleShot(15000, lambda: self._startup_timeout(generation))

    def _startup_timeout(self, generation):
        if generation == self._generation and self.state == "starting":
            self._failure = "接收器未能就绪，请检查组件或局域网连接。"
            self.stop()

    def _output(self, generation, line):
        if generation != self._generation or self._stopping:
            return
        self._tail.append(line)
        self._tail = self._tail[-20:]
        if line.startswith("JJ_AIRPLAY_VIDEO_SIZE "):
            try:
                _marker, width, height = line.split()
                width, height = int(width), int(height)
                if 0 < width <= 16384 and 0 < height <= 16384:
                    self.video.update(width=width, height=height)
                    self._state(self.state, self._streaming_text() if self.state == "streaming" else self.detail)
            except ValueError:
                pass
        elif line.startswith("JJ_AIRPLAY_DECODER "):
            self.video["decoder"] = line.split(" ", 1)[1]
            self._state(self.state, self.detail)
        elif line == "JJ_AIRPLAY_WINDOW_BRANDED":
            self.video["windowBranded"] = True
            self._state(self.state, self.detail)
        elif line.startswith("JJ_AIRPLAY_WINDOW_FITTED "):
            try:
                _marker, width, height = line.split()
                self.video.update(windowFitted=True, windowWidth=int(width), windowHeight=int(height))
                self._state(self.state, self.detail)
            except ValueError:
                pass
        elif line.startswith("JJ_AIRPLAY_WINDOW_PRESENTED "):
            self.video["windowPresented"] = True
            self.video["foregroundAtPresentation"] = line.endswith(" 1")
            self._state(self.state, self.detail)
        elif line.startswith("JJ_AIRPLAY_FIRST_FRAME_MS "):
            try:
                self.video["codecToFirstSinkMs"] = float(line.split()[1])
                self._state(self.state, self.detail)
            except (ValueError, IndexError):
                pass
        if line.startswith("JJ_AIRPLAY_DISCOVERY "):
            try:
                _marker, address, ttl = line.split()
                self.discovery = {"address": str(IPv4Address(address)), "port": 5353, "ttl": int(ttl)}
                self._state(self.state, self.detail)
            except ValueError:
                pass
        if line == "JJ_AIRPLAY_READY":
            self.video = {}
            self._state("ready", "在 iPhone 的「屏幕镜像」中选择上方名称。")
        elif line == "JJ_AIRPLAY_CONNECTED":
            self._state("connected", "iPhone 已连接，正在准备画面…")
        elif line == "JJ_AIRPLAY_STREAMING":
            self._state("streaming", self._streaming_text())

    def _streaming_text(self):
        width, height = self.video.get("width"), self.video.get("height")
        size = f" · {width} × {height}" if width and height else ""
        return f"正在投屏{size} · Alt+Enter 切换全屏。"

    def _exited(self, generation, code):
        if generation != self._generation:
            return
        self.process = None
        self._close_event()
        stopped = self._stopping
        self._stopping = False
        self.pin = ""
        self.discovery = {}
        self.video = {}
        if self._failure:
            self._state("error", self._failure)
        elif stopped or code == 0:
            self._state("idle", "投屏已关闭，请从 iPhone 的「屏幕镜像」重新发起连接。")
        else:
            detail = next((s for s in reversed(self._tail) if "error" in s.lower()
                           or "failed" in s.lower()), "")
            self._state("error", "投屏接收器退出，请重试。" + (" " + detail[:160] if detail else ""))

    def _close_event(self):
        if self._event:
            self._event[0].CloseHandle(self._event[1])
            self._event = None

    def stop(self):
        if self._cancel:
            self._cancel.set()
        if self._preparing:
            self._state("stopping", "正在取消组件准备…")
            return
        process = self.process
        if process is None:
            return
        self._stopping = True
        self._state("stopping", "正在停止投屏…")
        if self._event:
            self._event[0].SetEvent(self._event[1])

        def finish():
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()  # Only this session's owned receiver.
        threading.Thread(target=finish, name="JianJieAirPlayStop", daemon=True).start()

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.stop()
        if self.process is not None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=2)
        self._close_event()
        self.process = None
        self.pin = ""
        self.discovery = {}
        self.video = {}
        self._state("idle", "接收已关闭。重新打开投屏窗口后，可从 iPhone 发起连接。")


class MirrorDialog(CardDialog):
    def __init__(self, session, settings, parent=None):
        super().__init__("投", "iPhone 投屏", "AirPlay · 同一局域网", on_top=False, parent=parent)
        self.session = session
        self.settings = settings
        self._closing_window = False
        self._receive_after_stop = session.state == "stopping"
        self._pending_archive = ""
        self._choosing_package = False
        self._preferences_pending = False
        self._preferences_timer = QTimer(self)
        self._preferences_timer.setSingleShot(True)
        self._preferences_timer.setInterval(350)
        self._preferences_timer.timeout.connect(self.apply_preferences)
        self.setFixedWidth(560)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.content_lay.setSpacing(16)
        hero = QFrame()
        hero.setObjectName("mirrorHero")
        hero.setStyleSheet("QFrame#mirrorHero{background:transparent;border:none;}")
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(0, 12, 0, 8)
        hero_lay.setSpacing(12)
        heading = QHBoxLayout()
        name_label = QLabel("投屏名称")
        name_label.setStyleSheet("font-size:12px;color:#a9bbd2;")
        heading.addWidget(name_label)
        heading.addStretch()
        self.status = QLabel()
        heading.addWidget(self.status)
        hero_lay.addLayout(heading)
        identity = QHBoxLayout()
        identity.setSpacing(18)
        self.name = QLineEdit(str(settings.get("mirror_name", "简截投屏")))
        self.name.setMaxLength(20)
        self.name.setAccessibleName("在 iPhone 上显示的投屏名称")
        self.name.setMinimumHeight(52)
        self.name.setStyleSheet("QLineEdit{background:#181d2a;color:#f1f5fb;"
                               "border:1px solid #303747;border-radius:9px;padding:8px 12px;"
                               "font-size:21px;font-weight:600;selection-background-color:#3b82f6;}"
                               "QLineEdit:focus{border-color:#60a5fa;}")
        identity.addWidget(self.name, 1)
        code_lay = QVBoxLayout()
        code_lay.setSpacing(0)
        code_label = QLabel("连接配对码")
        code_label.setAlignment(Qt.AlignCenter)
        code_label.setStyleSheet("color:#a9bbd2;font-size:11px;")
        code_label.ensurePolished()
        code_label.setFixedHeight(code_label.fontMetrics().height())
        code_lay.addWidget(code_label)
        self.pin = QLabel("— — — —")
        self.pin.setFixedWidth(124)
        self.pin.setAlignment(Qt.AlignCenter)
        self.pin.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.pin.setAccessibleName("连接配对码")
        self.pin.setStyleSheet("font-family:'Consolas';font-size:32px;font-weight:600;"
                              "color:#bfdbfe;")
        self.pin.ensurePolished()
        self.pin.setFixedHeight(self.pin.fontMetrics().height())
        code_lay.addWidget(self.pin)
        identity.addLayout(code_lay)
        hero_lay.addLayout(identity)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(34)
        self.detail.setStyleSheet("color:#a9bbd2;font-size:12px;")
        hero_lay.addWidget(self.detail)
        self.audio = QCheckBox("播放手机声音")
        self.audio.setChecked(settings.get("mirror_audio", True))
        self.low_latency = QCheckBox("优先低延迟")
        self.low_latency.setChecked(settings.get("mirror_low_latency", True))
        self.low_latency.setToolTip("画面尽快显示；关闭后优先保持声画同步。")
        for checkbox in (self.audio, self.low_latency):
            checkbox.setCursor(Qt.PointingHandCursor)
            checkbox.setStyleSheet("QCheckBox{color:#e5edf9;font-size:12px;spacing:7px;}"
                                  "QCheckBox:disabled{color:#7e8da3;}")
        self._quality = settings.get("mirror_quality", "1080p")
        if self._quality not in ("720p", "1080p"):
            self._quality = "1080p"
        self._fps = settings.get("mirror_fps", 60)
        if self._fps not in (30, 60):
            self._fps = 60
        preferences = QHBoxLayout()
        preferences.setSpacing(14)
        preferences.addWidget(self.audio)
        preferences.addWidget(self.low_latency)
        preferences.addStretch()
        profile = QHBoxLayout()
        profile.setSpacing(6)
        quality_label = QLabel("画质")
        quality_label.setStyleSheet("font-size:11px;color:#a9bbd2;")
        profile.addWidget(quality_label)
        self.quality_button = QPushButton(self._quality + " ▾")
        self.quality_button.clicked.connect(lambda: self.choose_profile("quality"))
        profile.addWidget(self.quality_button)
        profile.addSpacing(12)
        fps_label = QLabel("最高帧率")
        fps_label.setStyleSheet("font-size:11px;color:#a9bbd2;")
        profile.addWidget(fps_label)
        self.fps_button = QPushButton(f"{self._fps} 帧 ▾")
        self.fps_button.clicked.connect(lambda: self.choose_profile("fps"))
        profile.addWidget(self.fps_button)
        for button in (self.quality_button, self.fps_button):
            button.setStyleSheet("QPushButton{background:#23324a;color:#c9ddfa;border:none;"
                                "border-radius:6px;font-size:11px;padding:5px 10px;}"
                                "QPushButton:hover{background:#2e4260;}"
                                "QPushButton:disabled{background:#1b293e;color:#7e8da3;}")
        preferences.addLayout(profile)
        hero_lay.addLayout(preferences)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        bar_policy = self.bar.sizePolicy()
        bar_policy.setRetainSizeWhenHidden(True)
        self.bar.setSizePolicy(bar_policy)
        self.bar.setTextVisible(False)
        self.bar.setValue(0)
        self.bar.hide()
        self.bar.setStyleSheet("QProgressBar{background:#252f42;border:none;border-radius:3px;}"
                              "QProgressBar::chunk{background:#60a5fa;border-radius:3px;}")
        hero_lay.addWidget(self.bar)
        self.stop_button = QPushButton("关闭投屏")
        self.stop_button.setObjectName("primary")
        self.stop_button.setMinimumHeight(52)
        self.stop_button.setStyleSheet("QPushButton{background:#3b82f6;color:#ffffff;"
                                       "border:none;border-radius:10px;font-size:16px;"
                                       "font-weight:600;padding:10px 16px;}"
                                       "QPushButton:hover{background:#4b91fa;}"
                                       "QPushButton:pressed{background:#2563eb;}"
                                       "QPushButton:disabled{background:#2b323e;color:#8994a4;}")
        self.stop_button.clicked.connect(self.close_projection)
        hero_lay.addWidget(self.stop_button)
        self.content_lay.addWidget(hero)
        main_separator = QFrame()
        main_separator.setFixedHeight(1)
        main_separator.setStyleSheet("background:#343b48;border:none;")
        self.content_lay.addWidget(main_separator)
        help_title = QLabel("在 iPhone 上连接")
        help_title.setStyleSheet("font-size:12px;font-weight:600;color:#d5dce8;")
        self.content_lay.addWidget(help_title)
        steps = QVBoxLayout()
        steps.setSpacing(7)
        for number, text in enumerate((
                "手机与电脑连接同一 Wi-Fi 或局域网。",
                "打开控制中心 → 屏幕镜像 → 选择上方投屏名称。",
                "按提示输入配对码；断开后需在手机上重新选择。"), 1):
            step = QLabel(f"<span style='color:#60a5fa'>{number}</span>　{text}")
            step.setWordWrap(True)
            step.setStyleSheet("font-size:12px;color:#9ca3af;")
            steps.addWidget(step)
        self.network = QLabel()
        self.network.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
        self.network.setTextInteractionFlags(Qt.TextSelectableByMouse)
        network_policy = self.network.sizePolicy()
        network_policy.setRetainSizeWhenHidden(True)
        self.network.setSizePolicy(network_policy)
        steps.addWidget(self.network)
        self.content_lay.addLayout(steps)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background:transparent;border:none;"
                          "border-top:1px dashed rgba(156,163,175,38);")
        self.content_lay.addWidget(line)
        components = QVBoxLayout()
        components.setSpacing(8)
        component_heading = QHBoxLayout()
        component_title = QLabel("首次使用与组件")
        component_title.setStyleSheet("font-size:12px;font-weight:600;color:#d5dce8;")
        component_heading.addWidget(component_title)
        component_heading.addStretch()
        self.import_button = QPushButton("导入本地组件包")
        self.import_button.setToolTip("导入已下载的 ZIP 组件包并开启接收。")
        self.import_button.setStyleSheet("QPushButton{background:transparent;color:#93c5fd;"
                                        "border:none;border-radius:6px;font-size:11px;padding:4px 8px;}"
                                        "QPushButton:hover{background:#202a3b;}"
                                        "QPushButton:disabled{color:#6b7280;}")
        self.import_button.clicked.connect(self.import_package)
        component_heading.addWidget(self.import_button)
        components.addLayout(component_heading)
        note = QLabel("打开窗口会自动准备接收。首次下载约 114 MB 组件，此后本地复用。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{C.UI_TEXT3};font-size:11px;")
        components.addWidget(note)
        links = QLabel("<a href='network' style='color:#9ca3af'>允许局域网连接</a>"
                       "　·　<a href='download' style='color:#9ca3af'>组件下载</a>"
                       "　·　<a href='folder' style='color:#9ca3af'>组件位置</a>")
        links.linkActivated.connect(self.open_link)
        links.setStyleSheet("font-size:11px;padding-top:2px;")
        components.addWidget(links)
        closing_note = QLabel("关闭此窗口会结束投屏并关闭接收。")
        closing_note.setStyleSheet(f"color:{C.UI_TEXT3};font-size:11px;")
        components.addWidget(closing_note)
        self.content_lay.addLayout(components)
        for button in (self.import_button, self.quality_button, self.fps_button):
            button.setCursor(Qt.PointingHandCursor)
        self.name.editingFinished.connect(self.preferences_changed)
        self.audio.toggled.connect(self.preferences_changed)
        self.low_latency.toggled.connect(self.preferences_changed)
        for control in (self.name, self.audio, self.quality_button, self.fps_button):
            control.setToolTip("等待手机连接时可修改，接收服务会自动应用设置。")
        session.changed.connect(self.refresh)
        session.progress.connect(self.update_progress)
        self.refresh(session.state, session.detail)

    def update_progress(self, value):
        self.bar.setValue(value)
        self.bar.setVisible(self.session.state == "preparing" and 0 < value < 100)

    def refresh(self, state, detail):
        names = {"idle": "接收未开启", "preparing": "正在准备组件", "starting": "正在开启接收",
                 "ready": "等待 iPhone", "connected": "iPhone 已连接",
                 "streaming": "正在投屏", "stopping": "正在关闭", "error": "接收未就绪"}
        self.status.setText(names.get(state, "iPhone 投屏"))
        connected = state in ("connected", "streaming")
        color = "#86efac" if connected else "#93c5fd" if state == "ready" else "#fca5a5" if state == "error" else "#a9bbd2"
        self.status.setStyleSheet(f"color:{color};background:#213047;border-radius:9px;"
                                 "font-size:11px;padding:3px 9px;")
        self.detail.setText(detail)
        address = self.session.discovery.get("address", "")
        self.network.setText(f"电脑局域网地址 · {address}")
        self.network.setVisible(bool(address))
        show_pin = state in ("starting", "ready", "connected", "streaming")
        self.pin.setText(self.session.pin if show_pin and self.session.pin else "— — — —")
        self.pin.setStyleSheet("font-family:'Consolas';font-weight:600;color:#bfdbfe;"
                              f"font-size:{32 if show_pin and self.session.pin else 22}px;")
        busy = state in ("preparing", "starting", "connected", "streaming", "stopping")
        self.name.setReadOnly(busy)
        self.audio.setEnabled(not busy)
        self.low_latency.setEnabled(not busy)
        self.quality_button.setEnabled(not busy)
        self.fps_button.setEnabled(not busy)
        self.import_button.setEnabled(state in ("idle", "error", "ready")
                                      and not self._choosing_package and not self._closing_window)
        self.import_button.setToolTip("投屏过程中不能替换组件，请先关闭投屏。" if connected
                                      else "导入已下载的 ZIP 组件包并重新准备接收。")
        self.bar.setVisible(state == "preparing" and 0 < self.bar.value() < 100)
        self.stop_button.setEnabled(connected)
        self.stop_button.setCursor(Qt.PointingHandCursor if connected else Qt.ArrowCursor)
        self.stop_button.setToolTip("结束当前手机投屏" if connected else "手机连接成功后可关闭投屏")
        if self._closing_window:
            return
        if state == "error" and self._pending_archive:
            self._pending_archive = ""
            self._receive_after_stop = False
        if state == "idle" and self._receive_after_stop and not self._choosing_package:
            self._receive_after_stop = False
            archive = self._pending_archive
            self._pending_archive = ""
            QTimer.singleShot(0, lambda: self.begin(archive))
        elif state == "ready" and self._preferences_pending and not self._choosing_package:
            self._preferences_timer.start()

    def begin(self, archive=""):
        if (self._closing_window or self._choosing_package
                or self.session.process is not None or self.session._preparing):
            return
        self._receive_after_stop = False
        self._pending_archive = ""
        self._preferences_timer.stop()
        self._preferences_pending = False
        self.settings["mirror_name"] = self.name.text().strip()
        self.settings["mirror_audio"] = self.audio.isChecked()
        self.settings["mirror_quality"] = self._quality
        self.settings["mirror_fps"] = self._fps
        self.settings["mirror_low_latency"] = self.low_latency.isChecked()
        C.save_settings(self.settings)
        self.bar.setValue(0)
        self.session.start(self.name.text(), self.audio.isChecked(), archive,
                           self._quality, self._fps, self.low_latency.isChecked())

    def preferences_changed(self):
        if self._closing_window:
            return
        values = {"name": self.name.text().strip(), "audio": self.audio.isChecked(),
                  "quality": self._quality, "fps": self._fps,
                  "low_latency": self.low_latency.isChecked()}
        if values == self.session._options:
            self._preferences_pending = False
            self._preferences_timer.stop()
            return
        self._preferences_pending = True
        self._preferences_timer.start()

    def apply_preferences(self):
        if self._closing_window or self._choosing_package or not self._preferences_pending:
            return
        if self.session.state in ("connected", "streaming", "stopping"):
            return
        name = self.name.text().strip()
        if not name or len(name.encode("utf-8")) > 60 or any(ord(c) < 32 for c in name):
            self.detail.setText("投屏名称太长或包含无效字符，请缩短后重试。")
            return
        if self.session.process is not None or self.session._preparing:
            self._receive_after_stop = True
            self.session.stop()
        else:
            self.begin()

    def choose_profile(self, kind):
        button = self.quality_button if kind == "quality" else self.fps_button
        menu = QMenu(self)
        menu.setStyleSheet(C.UI_MENU_QSS)
        values = ("720p", "1080p") if kind == "quality" else (30, 60)
        selected = self._quality if kind == "quality" else self._fps
        for value in values:
            label = value if kind == "quality" else f"{value} 帧"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == selected)
            action.setData(value)
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen is not None:
            value = chosen.data()
            if kind == "quality":
                self._quality = value
                button.setText(value + " ▾")
            else:
                self._fps = value
                button.setText(f"{value} 帧 ▾")
            self.preferences_changed()
        menu.deleteLater()

    def close_projection(self):
        if self._closing_window or self.session.state not in ("connected", "streaming"):
            return
        self._preferences_timer.stop()
        self._receive_after_stop = True
        self.session.stop()

    def import_package(self):
        if (self._closing_window or self._choosing_package
                or self.session.state not in ("idle", "error", "ready")):
            return
        self._choosing_package = True
        self._preferences_timer.stop()
        self.import_button.setEnabled(False)
        try:
            path, _ = QFileDialog.getOpenFileName(self, "选择投屏组件包", "", "投屏组件 (*.zip)")
        finally:
            self._choosing_package = False
        if self._closing_window:
            return
        if path and self.session.state in ("idle", "error", "ready"):
            self._preferences_pending = False
            if self.session.process is not None or self.session._preparing:
                self._pending_archive = path
                self._receive_after_stop = True
                self.session.stop()
            else:
                self.begin(path)
        else:
            self.refresh(self.session.state, self.session.detail)
            if path:
                self.detail.setText("接收状态已变化，请关闭当前投屏后再导入组件包。")
            elif self._preferences_pending:
                self._preferences_timer.start()

    def open_link(self, link):
        if link == "folder":
            component_dir().mkdir(parents=True, exist_ok=True)
            os.startfile(str(component_dir()))
        elif link == "download":
            os.startfile(metadata()["runtimeUrl"])
        elif link == "network":
            script = bundled_dir() / "configure_firewall.ps1"
            program = component_dir() / "receiver.exe"
            if not program.is_file():
                self.detail.setText("请先准备投屏组件，再设置局域网连接。")
                return
            arguments = subprocess.list2cmdline([
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-Program", str(program)])
            shell = ctypes.WinDLL("shell32", use_last_error=True)
            shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                           ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
            shell.ShellExecuteW.restype = ctypes.c_void_p
            result = shell.ShellExecuteW(None, "runas", "powershell.exe", arguments, None, 0)
            self.detail.setText("请确认 Windows 管理员提示，随后重新选择屏幕镜像。"
                                if result and result > 32 else "未完成防火墙设置，可稍后重试。")

    def reject(self):
        self._closing_window = True
        self._receive_after_stop = False
        self._pending_archive = ""
        self._preferences_timer.stop()
        self.session.stop()
        super().reject()

    def closeEvent(self, event):
        self._closing_window = True
        self._receive_after_stop = False
        self._pending_archive = ""
        self._preferences_timer.stop()
        self.session.stop()
        super().closeEvent(event)
