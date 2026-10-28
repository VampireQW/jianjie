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
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

import common as C
from widgets import CardDialog, _WinHttpConnection
from polished_controls import CrispLineEdit, SurfaceButton, SurfaceCheckBox, crisp_window_shadow
from runtime_diagnostics import component_failure, record_failure


class _PreparationFailure:
    def __init__(self, error):
        self.message, self.security_blocked = component_failure(error)


def decoder_preferences(low_latency):
    # Some screen streams leave reorder capacity unspecified: DXVA then holds
    # pictures for its DPB, even when the appsrc and raw queues are empty.
    # FFmpeg's existing decoder avoids that delay without relaxing H.264 order.
    hardware = "d3d11h264dec:512,d3d11h265dec:512"
    return "avdec_h264:600," + hardware if low_latency else hardware


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



def ensure_receiver(root, meta):
    """Reuse an installed engine only when it matches the pinned release digest."""
    root = Path(root)
    expected = str(meta.get("engineSha256", "")).lower()
    size = meta.get("engineBytes")
    if (len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected)
            or type(size) is not int or size <= 0):
        raise ValueError("投屏引擎版本信息无效，请重新安装简截。")
    target = root / "receiver.exe"
    if target.is_file() and target.stat().st_size == size and _sha(target) == expected:
        return target
    origin = bundled_dir() / "receiver.exe"
    if origin.stat().st_size != size or _sha(origin) != expected:
        raise ValueError("投屏引擎校验失败，请重新安装简截。")
    root.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        fd, filename = tempfile.mkstemp(prefix=".receiver-", suffix=".pending", dir=root)
        os.close(fd)
        pending = Path(filename)
        shutil.copyfile(origin, pending)
        if pending.stat().st_size != size or _sha(pending) != expected:
            raise ValueError("投屏引擎校验失败，请重新安装简截。")
        os.replace(pending, target)
        return target
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


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
        for name in ("LICENSE", "source.zip", "manifest.json"):
            shutil.copy2(bundled_dir() / name, staging / name)
        if cancelled and cancelled.is_set():
            raise InterruptedError()
        # The receiver is stopped before installation; files remain inside its private directory.
        root.mkdir(exist_ok=True)
        ensure_receiver(root, meta)
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
    controlChanged = Signal(str, str)
    progress = Signal(int)
    prepared = Signal(int, object)
    output = Signal(int, str)
    exited = Signal(int, int)
    inputFailed = Signal(int)

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
        self.security_blocked = False
        self.discovery = {}
        self.video = {}
        self.control = None
        self._control_event = None
        self._control_captured = False
        self._input_lock = threading.RLock()
        self._input_live = False
        self._input_captured = False
        self._control_metrics_timer = QTimer(self)
        self._control_metrics_timer.setInterval(2000)
        self._control_metrics_timer.timeout.connect(lambda: self._state(self.state, self.detail))
        self.prepared.connect(self._prepared)
        self.output.connect(self._output)
        self.exited.connect(self._exited)
        self.inputFailed.connect(self._input_failed)

    def enable_control(self, enabled):
        if enabled:
            if self.control is None:
                from iphone_control import ControlSession
                self.control = ControlSession(self)
                self.control.changed.connect(self._control_changed)
                self.control.availabilityChanged.connect(self._control_ready)
            self.control.start()
        elif self.control is not None:
            self._control_ready(False)
            self.control.stop()

    def _control_changed(self, state, detail):
        self._control_ready(self.control.available if self.control else False)
        self._state(self.state, self.detail)
        self.controlChanged.emit(state, detail)

    def _control_ready(self, ready):
        with self._input_lock:
            if not ready:
                self._input_live = False
                self._input_captured = False
                self._control_captured = False
                if self.control is not None:
                    self.control.send({"cmd": "release"})
            elif self.process is not None and not self._closing and not self._stopping:
                self._input_live = True
            if self._control_event:
                kernel, handle = self._control_event
                (kernel.SetEvent if ready else kernel.ResetEvent)(handle)
        if not ready:
            self._control_metrics_timer.stop()

    def _input_failed(self, generation):
        if generation == self._generation and not self._closing:
            self._control_ready(False)

    def _receiver_line(self, generation, line):
        """Forward foreground HID input on the reader thread; queue only UI messages."""
        invalid = False
        raw_input = line.startswith("JJ_AIRPLAY_INPUT ")
        with self._input_lock:
            if generation != self._generation or self._closing or self._stopping:
                return
            control = self.control
            if line.startswith("JJ_AIRPLAY_CONTROL "):
                if line == "JJ_AIRPLAY_CONTROL capture 1":
                    self._input_captured = bool(self._input_live and control and control.available)
                elif line in ("JJ_AIRPLAY_CONTROL capture 0", "JJ_AIRPLAY_CONTROL unavailable"):
                    self._input_captured = False
                    if self._input_live and control:
                        control.send({"cmd": "release"})
            elif raw_input:
                # Input never enters the GUI event queue, receiver tail or diagnostic log.
                if not self._input_live or not control:
                    return
                parts = line.split()[1:]
                try:
                    if parts == ["release"]:
                        self._input_captured = False
                        control.send({"cmd": "release"})
                    elif self._input_captured and control.available:
                        if len(parts) == 5 and parts[0] == "mouse":
                            values = list(map(int, parts[1:]))
                            values[1:] = [max(-2048, min(2048, value)) for value in values[1:]]
                            control.send(dict(cmd="mouse", **dict(zip(("buttons", "dx", "dy", "wheel"), values))))
                        elif 2 <= len(parts) <= 8 and parts[0] == "key":
                            control.send(dict(cmd="key", mod=int(parts[1]), keys=list(map(int, parts[2:]))))
                        else:
                            raise ValueError("Invalid foreground input report")
                except (ValueError, IndexError, TypeError, OverflowError):
                    self._input_captured = False
                    self._input_live = False
                    control.send({"cmd": "release"})
                    invalid = True
        if invalid:
            self.inputFailed.emit(generation)
        if not raw_input:
            self.output.emit(generation, line)

    def _control_line(self, line):
        if line.startswith("JJ_AIRPLAY_CONTROL "):
            if line in ("JJ_AIRPLAY_CONTROL capture 1", "JJ_AIRPLAY_CONTROL capture 0"):
                with self._input_lock:
                    captured = bool(self._input_captured and self._input_live and self.control and self.control.available)
                # A queued capture-on message must not reopen a capture already released by the reader.
                self._control_captured = captured
                if captured:
                    self._control_metrics_timer.start()
                    self.controlChanged.emit("capturing", "正在操作 iPhone · Esc、Ctrl+Alt+M 或 F8 返回电脑。")
                else:
                    self._control_metrics_timer.stop()
                    if self.control:
                        self.controlChanged.emit(self.control.state, self.control.detail)
            elif line == "JJ_AIRPLAY_CONTROL unavailable":
                self.controlChanged.emit("hint", "先在 iPhone 辅助触控中连接电脑，再按 Ctrl+Alt+M（或 F8）。")
            self._state(self.state, self.detail)
            return True
        return line.startswith("JJ_AIRPLAY_INPUT ")

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
                                          "control": {"state": self.control.state if self.control else "idle",
                                                      "mouseSubscribed": bool(self.control and self.control.mouse_subscribed),
                                                      "keyboardSubscribed": bool(self.control and self.control.keyboard_subscribed),
                                                      "captured": self._control_captured,
                                                      "timing": self.control.metrics() if self.control and hasattr(self.control, 'metrics') else {},
                                                      "pid": self.control.process.pid if self.control and self.control.process else None},
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
        with self._input_lock:
            self._input_live = self._input_captured = False
            self._generation += 1
            generation = self._generation
            self._stopping = False
        self._failure = ""
        self.security_blocked = False
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
                    ensure_receiver(root, meta)
                    for filename in ("manifest.json", "source.zip", "LICENSE"):
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
                record_failure("prepare", error)
                result = _PreparationFailure(error)
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
        elif isinstance(result, _PreparationFailure):
            self.pin = ""
            self.security_blocked = result.security_blocked
            self._state("error", result.message)
        else:
            self._launch(result, generation)

    def _launch(self, root, generation):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateEventW.restype = ctypes.c_void_p
        kernel.SetEvent.argtypes = [ctypes.c_void_p]
        kernel.ResetEvent.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        event_name = "Local\\JianJieAirPlay-" + secrets.token_hex(12)
        handle = kernel.CreateEventW(None, True, False, event_name)
        if not handle:
            self._state("error", "无法创建投屏会话，请重试。")
            return
        self._event = (kernel, handle)
        control_event_name = "Local\\JianJieControl-" + secrets.token_hex(12)
        control_handle = kernel.CreateEventW(None, True, bool(self.control and self.control.available), control_event_name)
        if not control_handle:
            self._close_event()
            self._state("error", "无法创建电脑控制会话，请重试。")
            return
        self._control_event = (kernel, control_handle)
        environment = os.environ.copy()
        environment["PATH"] = str(root) + os.pathsep + os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        environment["GST_PLUGIN_PATH_1_0"] = str(root / "lib/gstreamer-1.0")
        environment["GST_PLUGIN_SYSTEM_PATH_1_0"] = str(root / "lib/gstreamer-1.0")
        environment["GST_REGISTRY_1_0"] = str(root / "gst-registry.bin")
        environment["GST_REGISTRY_FORK"] = "no"
        environment["GST_PLUGIN_FEATURE_RANK"] = decoder_preferences(self._options["low_latency"])
        config = root / "receiver.conf"
        config.write_text("", encoding="utf-8")
        environment["UXPLAYRC"] = str(config)
        environment["JIANJIE_AIRPLAY_STOP_EVENT"] = event_name
        environment["JIANJIE_AIRPLAY_PARENT_PID"] = str(os.getpid())
        environment["JIANJIE_AIRPLAY_CONTROL_READY_EVENT"] = control_event_name
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
            record_failure("launch", error)
            message, self.security_blocked = component_failure(error)
            self._state("error", message)
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
        with self._input_lock:
            self._input_live = True
            self._input_captured = False

        def read():
            try:
                for raw in iter(lambda: process.stdout.readline(2048), b""):
                    if not self._closing:
                        self._receiver_line(generation, raw.decode("utf-8", errors="replace").strip())
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
        if self._control_line(line):
            return
        if line == "JJ_AIRPLAY_NETWORK_UNAVAILABLE":
            self._failure = "未找到可用于局域网投屏的 Wi-Fi 或有线网络，请连接后重新打开投屏。"
        self._tail.append(line)
        self._tail = self._tail[-20:]
        if line.startswith("JJ_AIRPLAY_VIDEO_STATS "):
            try:
                (_marker, fps, average, maximum, gap, encoded, decoded, matches, decoder, width, height) = line.split()
                fps, average, maximum, gap = map(float, (fps, average, maximum, gap))
                encoded, decoded, matches, width, height = map(int, (encoded, decoded, matches, width, height))
                if not (0 <= fps <= 1000 and -1 <= average < 60000 and -1 <= maximum < 60000
                        and 0 <= gap < 3600000 and 0 <= encoded <= 10000 and 0 <= decoded <= 10000
                        and 0 <= matches <= 100000 and 0 <= width <= 16384 and 0 <= height <= 16384):
                    return
                self.video.update(fps=fps, receiverToSinkAvgMs=average if matches else None,
                                  receiverToSinkMaxMs=maximum if matches else None,
                                  frameGapMaxMs=gap, encodedQueue=encoded, decodedQueue=decoded,
                                  matchedFrames=matches, decoder=decoder[:64])
                if width and height:
                    self.video.update(width=width, height=height)
                self._state(self.state, self._streaming_text() if self.state == "streaming" else self.detail)
            except ValueError:
                pass
        elif line.startswith("JJ_AIRPLAY_DISPLAY_QUEUE "):
            try:
                _marker, applied, previous, actual = line.split()
                applied, previous, actual = map(int, (applied, previous, actual))
                if applied not in (0, 1) or not (0 <= previous <= 16 and 0 <= actual <= 16):
                    return
                if applied and actual != 1:
                    return
                self.video.update(displayQueueLimited=bool(applied),
                                  displayQueuePrevious=previous, displayQueueFrames=actual)
                self._state(self.state, self.detail)
            except ValueError:
                pass
        elif line.startswith("JJ_AIRPLAY_VIDEO_SIZE "):
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
                elapsed = float(line.split()[1])
                if 0 <= elapsed < 60000:
                    self.video["codecToFirstSinkMs"] = elapsed
                    self._state("streaming", self._streaming_text())
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
            if self.state != "streaming":
                self._state("connected", "iPhone 已连接，正在准备画面…")

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
            record_failure("receiver_exit", RuntimeError(f"exit={code}; tail={self._tail[-4:]}"))
            self._state("error", "投屏接收已中断，请重新打开投屏窗口。")

    def _close_event(self):
        with self._input_lock:
            self._input_live = self._input_captured = False
        self._control_ready(False)
        if self._control_event:
            self._control_event[0].CloseHandle(self._control_event[1])
            self._control_event = None
        if self._event:
            self._event[0].CloseHandle(self._event[1])
            self._event = None

    def stop(self):
        with self._input_lock:
            self._input_live = self._input_captured = False
        self._control_ready(False)
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
        if self.control:
            self.control.shutdown()
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
        crisp_window_shadow(self, self.body)
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
        self.name = CrispLineEdit(str(settings.get("mirror_name", "简截投屏")), pixels=21, padding="8px 12px")
        self.name.setMaxLength(20)
        self.name.setAccessibleName("在 iPhone 上显示的投屏名称")
        self.name.setMinimumHeight(52)
        name_font = self.name.font()
        name_font.setBold(True)
        self.name.setFont(name_font)
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
        detail_row = QHBoxLayout()
        detail_row.addWidget(self.detail, 1)
        self.security_button = SurfaceButton("Windows 安全中心", variant="link")
        self.security_button.clicked.connect(lambda: os.startfile("windowsdefender:"))
        self.security_button.hide()
        detail_row.addWidget(self.security_button)
        hero_lay.addLayout(detail_row)
        self.audio = SurfaceCheckBox("播放手机声音")
        self.audio.setChecked(settings.get("mirror_audio", True))
        self.low_latency = SurfaceCheckBox("优先低延迟")
        self.low_latency.setChecked(settings.get("mirror_low_latency", True))
        self.low_latency.setToolTip("画面尽快显示，可能增加 CPU 占用；关闭后优先保持声画同步。")
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
        self.quality_button = SurfaceButton(self._quality, dropdown=True)
        self.quality_button.setAccessibleName("画质")
        self.quality_button.clicked.connect(lambda: self.choose_profile("quality"))
        profile.addWidget(self.quality_button)
        profile.addSpacing(12)
        fps_label = QLabel("最高帧率")
        fps_label.setStyleSheet("font-size:11px;color:#a9bbd2;")
        profile.addWidget(fps_label)
        self.fps_button = SurfaceButton(f"{self._fps} 帧", dropdown=True)
        self.fps_button.setAccessibleName("最高帧率")
        self.fps_button.clicked.connect(lambda: self.choose_profile("fps"))
        profile.addWidget(self.fps_button)
        preferences.addLayout(profile)
        hero_lay.addLayout(preferences)
        control_row = QHBoxLayout()
        self.control_toggle = SurfaceCheckBox("电脑控制")
        self.control_toggle.setCursor(Qt.PointingHandCursor)
        self.control_toggle.setStyleSheet("QCheckBox{color:#e5edf9;font-size:12px;spacing:7px;}"
                                         "QCheckBox:disabled{color:#7e8da3;}")
        self.control_toggle.setToolTip("通过蓝牙操作 iPhone，需要开启辅助触控并完成配对。")
        self.control_toggle.toggled.connect(session.enable_control)
        self.control_toggle.toggled.connect(lambda checked: self.show_guide(1) if checked else None)
        control_row.addWidget(self.control_toggle)
        control_row.addStretch()
        self.control_status = QLabel("未开启")
        self.control_status.setStyleSheet("font-size:11px;color:#7e8da3;")
        control_row.addWidget(self.control_status)
        hero_lay.addLayout(control_row)
        self.control_note = QLabel("开启后，通过 iPhone 的辅助触控连接电脑。连接方法见下方指南。")
        self.control_note.setTextFormat(Qt.PlainText)
        self.control_note.setWordWrap(True)
        self.control_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.control_note.setStyleSheet("font-size:11px;color:#91a2bc;")
        self.control_note.ensurePolished()
        self.control_note.setFixedHeight(self.control_note.fontMetrics().lineSpacing() * 2 + 4)
        hero_lay.addWidget(self.control_note)
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
        self.stop_button = SurfaceButton("关闭投屏", variant="primary", pixels=16)
        self.stop_button.setObjectName("primary")
        self.stop_button.setMinimumHeight(52)
        self.stop_button.clicked.connect(self.close_projection)
        hero_lay.addWidget(self.stop_button)
        self.content_lay.addWidget(hero)
        main_separator = QFrame()
        main_separator.setFixedHeight(1)
        main_separator.setStyleSheet("background:#343b48;border:none;")
        self.content_lay.addWidget(main_separator)
        guide_tabs = QHBoxLayout()
        guide_tabs.setSpacing(8)
        self.guide_group = QButtonGroup(self)
        self.guide_group.setExclusive(True)
        for index, caption in enumerate(("连接投屏", "连接电脑控制")):
            button = SurfaceButton(caption, variant="tab", pixels=12)
            button.setCheckable(True)
            self.guide_group.addButton(button, index)
            guide_tabs.addWidget(button)
        guide_tabs.addStretch()
        self.content_lay.addLayout(guide_tabs)
        self.guide_stack = QStackedWidget()
        self.guide_stack.setStyleSheet("QStackedWidget{background:transparent;border:none;}")
        screen_guide = self._guide_page((
            "手机与电脑连接同一 Wi-Fi 或局域网。",
            "打开 iPhone 控制中心 → 屏幕镜像，选择上方投屏名称。",
            "按提示输入配对码。结束后，再次连接需从手机发起。"))
        self.guide_stack.addWidget(screen_guide)
        device_name = os.environ.get("COMPUTERNAME", "这台电脑")
        control_guide = self._guide_page((
            "开启上方“电脑控制”，打开电脑与 iPhone 的蓝牙。",
            "iPhone：设置 → 辅助功能 → 触控 → 辅助触控，开启辅助触控。",
            f"进入辅助触控的“设备 → 蓝牙设备”，选择电脑“{device_name}”并确认配对。手机出现圆形指针后即可使用。"),
            "完成普通蓝牙配对后，仍需从辅助触控的设备列表连接电脑。")
        shortcut = QLabel("点击电脑上的投屏画面 → F8 / Ctrl+Alt+M 进入控制　·　Esc 返回电脑")
        shortcut.setWordWrap(True)
        shortcut.setStyleSheet("color:#a9c8f2;font-size:11px;padding-top:4px;")
        control_guide.layout().addWidget(shortcut)
        self.guide_stack.addWidget(control_guide)
        self.guide_group.idClicked.connect(self.show_guide)
        self.show_guide(0)
        self.content_lay.addWidget(self.guide_stack)
        self.network = QLabel()
        self.network.setStyleSheet(f"color:{C.UI_TEXT2};font-size:11px;")
        self.network.setTextInteractionFlags(Qt.TextSelectableByMouse)
        network_policy = self.network.sizePolicy()
        network_policy.setRetainSizeWhenHidden(True)
        self.network.setSizePolicy(network_policy)
        self.content_lay.addWidget(self.network)
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
        self.import_button = SurfaceButton("导入本地组件包", variant="link")
        self.import_button.setToolTip("导入已下载的 ZIP 组件包并开启接收。")
        self.import_button.clicked.connect(self.import_package)
        component_heading.addWidget(self.import_button)
        components.addLayout(component_heading)
        note = QLabel("打开窗口会自动准备接收。首次下载约 114 MB 组件，此后本地复用。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{C.UI_TEXT3};font-size:11px;")
        components.addWidget(note)
        components.addWidget(self.bar)
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
        session.controlChanged.connect(self.refresh_control)
        session.progress.connect(self.update_progress)
        self.refresh(session.state, session.detail)
        self._fit_to_screen()

    def _guide_page(self, instructions, note=""):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(9)
        for number, text in enumerate(instructions, 1):
            row = QHBoxLayout()
            row.setSpacing(10)
            index = QLabel(str(number))
            index.setFixedWidth(16)
            index.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            index.setStyleSheet("color:#7eb5ff;font-size:12px;font-weight:600;")
            label = QLabel(text)
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setStyleSheet("color:#a7b4c8;font-size:11.5px;")
            row.addWidget(index)
            row.addWidget(label, 1)
            layout.addLayout(row)
        if note:
            hint = QLabel(note)
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#7f8ea5;font-size:11px;")
            layout.addWidget(hint)
        return page

    def show_guide(self, index):
        self.guide_stack.setCurrentIndex(index)
        self.guide_group.button(index).setChecked(True)

    def _fit_to_screen(self):
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.setFixedWidth(min(560, max(320, area.width() - 24)))
        guide_width = max(240, self.width() - 48)
        self.guide_stack.setFixedHeight(max(
            self.guide_stack.widget(index).layout().heightForWidth(guide_width)
            for index in range(self.guide_stack.count())))
        maximum = max(260, area.height() - 24)
        if self.sizeHint().height() <= maximum:
            return
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(self.content_lay.spacing())
        def reparent(item):
            if item.widget():
                item.widget().setParent(page)
            elif item.layout():
                for index in range(item.layout().count()):
                    reparent(item.layout().itemAt(index))
        while self.content_lay.count():
            item = self.content_lay.takeAt(0)
            reparent(item)
            layout.addItem(item)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        scroll.setStyleSheet("QScrollArea,QScrollArea>QWidget>QWidget{background:transparent;border:none;}"
                            "QScrollBar:vertical{background:transparent;width:5px;}"
                            "QScrollBar::handle:vertical{background:#394458;border-radius:2px;min-height:28px;}"
                            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
                            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:none;}")
        self.content_lay.addWidget(scroll)
        self.setFixedHeight(maximum)

    def refresh_control(self, state, detail):
        names = {"idle": "未开启", "starting": "正在准备蓝牙", "waiting": "等待辅助触控连接",
                 "connected": "蓝牙已连接", "capturing": "正在操作手机", "stopping": "正在关闭",
                 "error": "控制未就绪", "hint": "等待蓝牙连接"}
        self.control_status.setText(names.get(state, "电脑控制"))
        color = "#86efac" if state in ("connected", "capturing") else "#fca5a5" if state == "error" else "#91a2bc"
        self.control_status.setStyleSheet(f"font-size:11px;color:{color};")
        self.control_note.setText(detail)
        if self.session.control is not None and state in ("waiting", "starting"):
            self.show_guide(1)
        if state in ("idle", "error"):
            self.control_toggle.blockSignals(True)
            self.control_toggle.setChecked(False)
            self.control_toggle.blockSignals(False)
        self.control_toggle.setEnabled(state != "stopping")

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
        self.security_button.setVisible(state == "error" and self.session.security_blocked)
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
                button.setText(value)
            else:
                self._fps = value
                button.setText(f"{value} 帧")
            self.preferences_changed()
        menu.deleteLater()

    def close_projection(self):
        if self._closing_window or self.session.state not in ("connected", "streaming"):
            return
        self._preferences_timer.stop()
        self._receive_after_stop = True
        self.session.enable_control(False)
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
        self.session.enable_control(False)
        self.session.stop()
        super().reject()

    def closeEvent(self, event):
        self._closing_window = True
        self._receive_after_stop = False
        self._pending_archive = ""
        self._preferences_timer.stop()
        self.session.enable_control(False)
        self.session.stop()
        super().closeEvent(event)
