"""Optional iPhone HID session; input is bounded and never retained in logs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from collections import deque

from PySide6.QtCore import QObject, Signal
from runtime_diagnostics import control_failure, record_failure


def bundled_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "native" / "iphone_control"


def normalize_input(command):
    """Accept complete HID states only, with conservative movement limits."""
    if not isinstance(command, dict):
        raise ValueError("无效输入")
    kind = command.get("cmd")
    if kind in ("release", "quit"):
        return {"cmd": kind}
    if kind == "mouse":
        values = {name: int(command.get(name, 0)) for name in ("buttons", "dx", "dy", "wheel")}
        if not 0 <= values["buttons"] <= 7 or any(abs(values[key]) > 32767 for key in ("dx", "dy", "wheel")):
            raise ValueError("鼠标输入超出范围")
        return {"cmd": kind, **values}
    if kind == "key":
        modifier = int(command.get("mod", 0))
        keys = command.get("keys", [])
        if not isinstance(keys, (list, tuple)) or len(keys) > 6:
            raise ValueError("键盘输入超出范围")
        keys = [int(key) for key in keys]
        if not 0 <= modifier <= 255 or any(not 0 <= key <= 231 for key in keys):
            raise ValueError("键盘输入超出范围")
        return {"cmd": kind, "mod": modifier, "keys": keys}
    raise ValueError("未知输入类型")


def mouse_reports(command):
    """Split large raw movements without reversing or truncating either axis."""
    remaining = [command[key] for key in ("dx", "dy", "wheel")]
    while True:
        step = [max(-127, min(127, value)) for value in remaining]
        yield dict(cmd="mouse", buttons=command["buttons"], dx=step[0], dy=step[1], wheel=step[2])
        remaining = [value - part for value, part in zip(remaining, step)]
        if not any(remaining):
            return


class ControlSession(QObject):
    changed = Signal(str, str)
    availabilityChanged = Signal(bool)
    _event = Signal(int, object)
    _ended = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.detail = "连接辅助触控后，在投屏画面按 Ctrl+Alt+M（或 F8）操作，Esc 返回电脑。"
        self.device_name = os.environ.get("COMPUTERNAME", "这台电脑")
        self.process = None
        self.mouse_subscribed = False
        self.keyboard_subscribed = False
        self._generation = 0
        self._closing = False
        self._stop_requested = False
        self._failure = ""
        self._last_available = False
        self._sequence = 0
        self._ack_sequence = 0
        self._input_epoch = 0
        self._condition = threading.Condition()
        self._pending = deque()
        self._sent_at = {}
        self._samples = {name: deque(maxlen=128) for name in ("queue", "ack", "helper")}
        self._counts = {"reportsSent": 0, "reportsAcked": 0, "maxQueueDepth": 0}
        self._event.connect(self._receive)
        self._ended.connect(self._finish)

    @property
    def available(self):
        return self.mouse_subscribed and self.state == "connected" and not self._stop_requested

    def metrics(self):
        """Timing and counts only; never retain any mouse coordinates or keys."""
        with self._condition:
            result = dict(self._counts, queueDepth=len(self._pending))
            for name, samples in self._samples.items():
                values = sorted(samples)
                result[name + "P95Ms"] = round(values[min(len(values)-1, int(len(values)*.95))], 2) if values else None
            return result

    def _pairing_text(self):
        return f"开启 iPhone 辅助触控，在该页面的「设备 → 蓝牙设备」连接这台电脑（{self.device_name}）。"

    def _state(self, state, detail):
        self.state, self.detail = state, detail
        if not self._closing:
            self.changed.emit(state, detail)
            if self._last_available != self.available:
                self._last_available = self.available
                self.availabilityChanged.emit(self.available)

    def start(self):
        if self.process is not None or self._closing:
            return
        self._generation += 1
        generation = self._generation
        self._stop_requested = False
        self._failure = ""
        self.mouse_subscribed = self.keyboard_subscribed = False
        with self._condition:
            self._pending.clear()
            self._sent_at.clear()
            for sample in self._samples.values():
                sample.clear()
            self._counts.update(reportsSent=0, reportsAcked=0, maxQueueDepth=0)
        try:
            root = bundled_dir()
            meta = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
            program = root / "control.exe"
            digest = hashlib.sha256(program.read_bytes()).hexdigest()
            expected = meta.get("sha256") or meta.get("engineSha256")
            if not expected or digest != expected:
                raise ValueError("蓝牙控制组件校验失败，请重新下载简截。")
            self.process = subprocess.Popen([str(program)], cwd=root, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            creationflags=subprocess.CREATE_NO_WINDOW,
                                            bufsize=0)
        except (OSError, ValueError) as error:
            self.process = None
            record_failure("control_start", error)
            self._state("error", control_failure(str(error)))
            return
        self._state("starting", "正在检测蓝牙并准备电脑控制…")
        process = self.process

        def read():
            try:
                for line in iter(lambda: process.stdout.readline(8192), b""):
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict):
                            if value.get("type") == "inputAck":
                                with self._condition:
                                    sequence = int(value["seq"])
                                    self._ack_sequence = max(self._ack_sequence, sequence)
                                    sent_at = self._sent_at.pop(sequence, None)
                                    if sent_at is not None:
                                        self._counts["reportsAcked"] += 1
                                        if sent_at[1] in ("mouse", "key"):
                                            self._samples["ack"].append((time.perf_counter() - sent_at[0]) * 1000)
                                            if isinstance(value.get("elapsedMs"), (int, float)):
                                                self._samples["helper"].append(max(0, min(60000, value["elapsedMs"])))
                                    self._condition.notify_all()
                            else:
                                self._event.emit(generation, value)
                    except (ValueError, UnicodeError, TypeError, KeyError):
                        continue
            finally:
                process.stdout.close()
                self._ended.emit(generation, process.wait())

        def write():
            try:
                while True:
                    with self._condition:
                        self._condition.wait_for(lambda: bool(self._pending) or process.poll() is not None)
                        if process.poll() is not None:
                            return
                        command = self._pending.popleft()
                        epoch = self._input_epoch
                        queued_at = command.pop("_queued_at", None)
                        if queued_at is not None and command["cmd"] in ("mouse", "key"):
                            self._samples["queue"].append((time.perf_counter() - queued_at) * 1000)
                    commands = mouse_reports(command) if command["cmd"] == "mouse" else (command,)
                    for part in commands:
                        with self._condition:
                            if epoch != self._input_epoch:
                                break
                            self._sequence += 1
                            sequence = self._sequence
                            self._sent_at[sequence] = (time.perf_counter(), part["cmd"])
                            self._counts["reportsSent"] += 1
                            while len(self._sent_at) > 4:
                                del self._sent_at[next(iter(self._sent_at))]
                        part = dict(part, seq=sequence)
                        process.stdin.write((json.dumps(part, separators=(",", ":")) + "\n").encode("ascii"))
                        with self._condition:
                            acknowledged = self._condition.wait_for(
                                lambda: self._ack_sequence >= sequence or epoch != self._input_epoch
                                or process.poll() is not None, timeout=3)
                        if not acknowledged:
                            self._event.emit(generation, {"type": "error", "message": "蓝牙输入没有及时响应，已退出控制。"})
                            return
                    if command["cmd"] == "quit":
                        return
            except (OSError, ValueError):
                if not self._stop_requested:
                    self._event.emit(generation, {"type": "error", "message": "蓝牙输入连接已中断。"})
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        threading.Thread(target=read, name="JianJieControlRead", daemon=True).start()
        threading.Thread(target=write, name="JianJieControlWrite", daemon=True).start()

    def send(self, command):
        if self.process is None or self._stop_requested:
            return False
        command = normalize_input(command)
        if command["cmd"] not in ("release", "quit") and not self.available:
            return False
        if command["cmd"] == "key" and not self.keyboard_subscribed:
            return False
        with self._condition:
            if command["cmd"] == "release":
                self._input_epoch += 1
                self._pending.clear()
            if command["cmd"] == "mouse" and self._pending:
                previous = self._pending[-1]
                if previous["cmd"] == "mouse" and previous["buttons"] == command["buttons"]:
                    for key in ("dx", "dy", "wheel"):
                        previous[key] = max(-2048, min(2048, previous[key] + command[key]))
                    return True
            if len(self._pending) >= 128:
                self._pending.clear()
                self._pending.append({"cmd": "release"})
                self._condition.notify()
                self._event.emit(self._generation, {"type": "error", "message": "输入发送过慢，已释放控制，请重新连接。"})
                return False
            self._pending.append(command)
            command["_queued_at"] = time.perf_counter()
            self._counts["maxQueueDepth"] = max(self._counts["maxQueueDepth"], len(self._pending))
            self._condition.notify()
        return True

    def _receive(self, generation, value):
        if generation != self._generation or self._closing or self._stop_requested:
            return
        kind = value.get("type")
        if kind == "ready":
            if not value.get("inputAckSupported"):
                self._failure = "蓝牙组件版本不匹配，请重新下载简截。"
                self.stop()
                return
            self.device_name = str(value.get("computerName") or self.device_name)
            self._state("waiting", self._pairing_text())
        elif kind == "peer" and value.get("name") and self.available:
            self._state("connected", f"已连接 {str(value['name'])[:40]} · Ctrl+Alt+M（或 F8）操作，Esc 返回电脑。")
        elif kind in ("subscribed", "connected", "disconnected"):
            self.mouse_subscribed = bool(value.get("mouseSubscribed", False))
            self.keyboard_subscribed = bool(value.get("keyboardSubscribed", False))
            if self.mouse_subscribed:
                detail = "输入已连接 · Ctrl+Alt+M（或 F8）操作，Esc 返回电脑。"
                if not self.keyboard_subscribed:
                    detail = "鼠标已连接，键盘未连接 · Ctrl+Alt+M（或 F8）操作，Esc 返回电脑。"
                self._state("connected", detail)
            else:
                self._state("waiting", self._pairing_text())
            if not self.mouse_subscribed:
                self.send({"cmd": "release"})
        elif kind == "error":
            if value.get("stage") == "input" and value.get("code") == "NO_SUBSCRIBERS":
                if value.get("report") == "key":
                    self.keyboard_subscribed = False
                    self._state(self.state, "键盘未连接，可继续使用鼠标控制。")
                else:
                    self.mouse_subscribed = False
                    self._state("waiting", "鼠标输入已断开，请在 iPhone 辅助触控中重新连接。")
                return
            record_failure("control", RuntimeError(str({key: value.get(key) for key in ("stage", "code", "hresult", "message")})))
            self._failure = control_failure(value.get("message", ""))
            if self.process is not None:
                self.stop()
            else:
                self._state("error", self._failure)

    def _finish(self, generation, code):
        if generation != self._generation:
            return
        self.process = None
        self.mouse_subscribed = self.keyboard_subscribed = False
        with self._condition:
            self._pending.clear()
            self._condition.notify_all()
        if self._failure:
            self._state("error", self._failure)
        elif self._stop_requested:
            self._state("idle", "电脑控制已关闭，投屏画面不受影响。")
        elif self.state != "error":
            record_failure("control_exit", RuntimeError(f"exit={code}"))
            self._state("error", "电脑控制已中断，请重新开启。")

    def stop(self):
        process = self.process
        if process is None:
            self._state("idle", "电脑控制已关闭。")
            return
        if self._stop_requested:
            return
        self._stop_requested = True
        self.mouse_subscribed = self.keyboard_subscribed = False
        self._state("stopping", "正在关闭电脑控制…")
        with self._condition:
            self._input_epoch += 1
            self._pending.clear()
            self._pending.extend(({"cmd": "release"}, {"cmd": "quit"}))
            self._condition.notify_all()

        def finish():
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.terminate()  # This session's own helper only.

        threading.Thread(target=finish, name="JianJieControlStop", daemon=True).start()

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.stop()
