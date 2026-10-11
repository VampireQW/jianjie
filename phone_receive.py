# -*- coding: utf-8 -*-
"""局域网单张图片接收：兼容 Clipboard EX / 安卓网页上传，不启动额外进程。"""

import base64
import hmac
import ipaddress
import json
import os
import socket
import tempfile
import threading
import time
import re
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, quote, urlsplit, parse_qs
from phone_exchange import MAX_FILE_BYTES, FILE_LIMIT_LABEL, FILE_CHUNK_BYTES, DISK_RESERVE_BYTES, safe_filename


FILE_IO_TIMEOUT = 60
DOWNLOAD_TICKET_TTL = 10 * 60
PORT = 5034
SHORTCUT_URL = "https://www.icloud.com/shortcuts/321944d62831416f9d2ba7917917686a"
SHORTCUT_NAME = "Clipboard EX"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 8 * 1024
_LAN_RANGES = tuple(ipaddress.ip_network(net) for net in
                    ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"))


def read_phone_image(path, scaled_size=None):
    """原图不按像素数拒收；按可用内存预检，局部提高 Qt 解码额度并恢复。"""
    import psutil
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImageReader, QImageIOHandler
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid():
        raise ValueError("图片无效，请发送 JPEG 或 PNG")
    kind = bytes(reader.format()).lower()
    if kind not in (b"jpeg", b"jpg", b"png"):
        raise ValueError("图片读取失败，请发送 JPEG 或 PNG")
    with open(path, "rb") as stream:
        header = stream.read(32)
    # 8-bit 处理峰值计入解码、方向变换、BGR 和系统 DIB；16-bit PNG 留更大余量。
    depth = 8 if kind == b"png" and len(header) > 24 and header[24] == 16 else 4
    pixels = size.width() * size.height()
    working_pixels = pixels
    if scaled_size is not None:
        target = size.scaled(scaled_size, Qt.KeepAspectRatio)
        reader.setScaledSize(target)
        if kind in (b"jpeg", b"jpg") and reader.supportsOption(QImageIOHandler.ScaledSize):
            # JPEG 可在解码时缩小，DCT 最小工作尺寸为原宽高的 1/8。
            working_pixels = max(target.width() * target.height(),
                                 ((size.width() + 7) // 8) * ((size.height() + 7) // 8))
    needed = working_pixels * (24 if depth == 8 else 16) + 16 * 1024 * 1024
    available = psutil.virtual_memory().available
    if needed > available - max(256 * 1024 * 1024, available // 3):
        raise ValueError("电脑当前可用内存不足以处理这张原图，请关闭占用内存的程序后重试")
    previous = reader.allocationLimit()
    required_limit = (pixels * depth + 1048575) // 1048576 + 16
    changed = previous != 0 and previous < required_limit
    if changed:
        reader.setAllocationLimit(required_limit)
    try:
        image = reader.read()
    finally:
        if changed:
            reader.setAllocationLimit(previous)
    if image.isNull():
        raise ValueError("图片读取失败，请确认图片完整且为 JPEG 或 PNG")
    return image


def validate_config(url, user, password, addresses):
    try:
        parsed = urlsplit(url.strip())
        port = 80 if parsed.port is None else parsed.port
        if (parsed.scheme != "http" or parsed.hostname not in addresses or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ("", "/") or parsed.query or parsed.fragment or
                not 1 <= port <= 65535):
            raise ValueError()
    except ValueError:
        raise ValueError("URL 请填写本机局域网地址与端口，例如 http://192.168.1.10:5034") from None
    user = user.strip()
    if not user or len(user) > 64 or ":" in user or any(ord(c) < 32 for c in user):
        raise ValueError("User 请输入 1–64 个字符，不能包含冒号或换行")
    if not password or len(password) > 128 or any(ord(c) < 32 for c in password):
        raise ValueError("Password 请输入 1–128 个字符，不能包含换行")
    return {"url": f"http://{parsed.hostname}:{port}", "user": user,
            "password": password, "port": port}


def request_firewall_access(program, host, port, directory):
    """用户点击放行按钮后申请管理员授权；只更新本程序与本地子网的端口规则。"""
    import ctypes
    import subprocess
    root = tempfile.mkdtemp(prefix="JianJiePhoneFirewall_")
    script = os.path.join(root, "configure.ps1")
    result = os.path.join(root, "result.json")

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    content = f"""$ErrorActionPreference = 'Stop'
$programPath = {literal(os.path.abspath(program))}
$resultPath = {literal(result)}
try {{
    $network = Get-NetIPAddress -AddressFamily IPv4 -IPAddress {literal(host)} | Select-Object -First 1
    if (-not $network) {{ throw '未找到该局域网地址，请检查 URL' }}
    $ruleParams = @{{ Name='JianJiePhoneReceiveLan'; Enabled='True'; Direction='Inbound'; Action='Allow'; Profile='Any'; InterfaceAlias=$network.InterfaceAlias; Program=$programPath; Protocol='TCP'; LocalPort={int(port)}; RemoteAddress='LocalSubnet' }}
    if (Get-NetFirewallRule -Name 'JianJiePhoneReceiveLan' -ErrorAction SilentlyContinue) {{
        Set-NetFirewallRule @ruleParams | Out-Null
    }} else {{
        New-NetFirewallRule @ruleParams -DisplayName '简截手机传图（仅局域网）' | Out-Null
    }}
    @{{ok=$true;message='局域网连接已修复，可在手机上重新发送图片。'}} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
}} catch {{
    @{{ok=$false;message=('局域网修复失败：' + $_.Exception.Message)}} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
}}
"""
    with open(script, "w", encoding="utf-8-sig") as stream:
        stream.write(content)
    # OWNER RIGHTS 权限随文件所有者变化，结果须由普通用户先创建。
    # 管理员仅写入已有文件，避免新建的结果仅管理员可读。
    with open(result, "w", encoding="utf-8"):
        pass
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                             "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    shell = ctypes.windll.shell32
    shell.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                    ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    shell.ShellExecuteW.restype = ctypes.c_void_p
    status = shell.ShellExecuteW(None, "runas", powershell,
                               subprocess.list2cmdline(["-NoProfile", "-NonInteractive",
                                                        "-WindowStyle", "Hidden", "-File", script]), root, 0)
    if not status or status <= 32:
        os.remove(script)
        os.remove(result)
        os.rmdir(root)
        raise OSError("未获得管理员权限，请在 Windows 提示中点“是”；取消或系统拒绝时可重试。")
    return root, result


def local_addresses():
    """只用于界面展示；监听仍由防火墙和来源地址共同限制。"""
    addresses = []
    try:
        import psutil
        for interface, items in psutil.net_if_addrs().items():
            if not psutil.net_if_stats().get(interface).isup:
                continue
            for item in items:
                if item.family != socket.AF_INET:
                    continue
                address = ipaddress.ip_address(item.address)
                if address.is_loopback or not any(address in network for network in _LAN_RANGES):
                    continue
                addresses.append(str(address))
    except (OSError, ValueError, AttributeError):
        pass
    return sorted(set(addresses))


class _BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, handler):
        # 网页会并行取 CSS、脚本、图标和 manifest；上传另限两份，避免页面资源被挤掉。
        self._slots = threading.BoundedSemaphore(8)
        super().__init__(address, handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class PhoneReceiver:
    """只接受一份未提交的上传；文件名始终由本机生成。"""

    def __init__(self, user, password, directory, accept_image, port=PORT, exchange=None, accept_text=None, accept_file=None, on_activity=None):
        self.user = user
        self.password = password
        self._credential_bytes = (user + ":" + password).encode("utf-8")
        self.directory = directory
        self.accept_image = accept_image
        self.exchange = exchange
        self.accept_text = accept_text
        self.accept_file = accept_file
        self.on_activity = on_activity
        self.port = port
        self._pending = None
        self._lock = threading.Lock()
        self._image_slots = threading.BoundedSemaphore(2)
        self._server = None
        self._thread = None
        self._stopping = False

    def update_credentials(self, user, password):
        changed = (user, password) != (self.user, self.password)
        self.user, self.password = user, password
        self._credential_bytes = (user + ":" + password).encode("utf-8")
        if changed and self.exchange is not None:
            self.exchange.clear()

    @property
    def browser_token(self):
        return hmac.new(self._credential_bytes, b"JianJieAndroidUpload-v1", "sha256").hexdigest()

    def download_ticket(self, record_id):
        expiry = str(int(time.time()) + DOWNLOAD_TICKET_TTL)
        payload = (record_id + "." + expiry).encode("ascii")
        signature = hmac.new(self.browser_token.encode("ascii"), payload, "sha256").hexdigest()
        return expiry + "." + signature

    def valid_download_ticket(self, record_id, ticket):
        if not re.fullmatch(r"[a-f0-9]{32}", record_id) or not isinstance(ticket, str):
            return False
        match = re.fullmatch(r"([0-9]{10})\.([a-f0-9]{64})", ticket)
        if not match:
            return False
        expiry, signature = match.groups()
        now = int(time.time())
        if not now <= int(expiry) <= now + DOWNLOAD_TICKET_TTL:
            return False
        expected = hmac.new(self.browser_token.encode("ascii"),
                            (record_id + "." + expiry).encode("ascii"), "sha256").hexdigest()
        return hmac.compare_digest(signature, expected)

    def start(self):
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def setup(self):
                super().setup()
                self.connection.settimeout(12)

            def log_message(self, *args):
                pass  # 地址、文件名与 Authorization 不写入日志

            def _reply(self, status, message):
                if receiver.on_activity is not None and self.command in ("PUT", "POST") and urlsplit(self.path).path != "/exchange/ack":
                    receiver.on_activity(message, status >= 400)
                body = json.dumps({"message": message}, ensure_ascii=False).encode("utf-8")
                self._reply_body(status, body, "application/json; charset=utf-8")

            def _reply_body(self, status, body, content_type, headers=None):
                # Windows 关闭尚有未读请求体的连接可能丢掉错误响应并发送 RST。
                # 只收尾小请求；大文件/Expect 请求仍直接拒绝，不吞完未经授权的原图。
                if status >= 400 and not getattr(self, "_body_consumed", False) and not self.headers.get("Expect") and not self.headers.get("Transfer-Encoding"):
                    length = self.headers.get("Content-Length", "")
                    if len(length) <= 10 and length.isascii() and length.isdecimal() and 0 < int(length) <= 64 * 1024:
                        self._body_consumed = True
                        self.rfile.read(int(length))
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Content-Type-Options", "nosniff")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                self.close_connection = True

            def _authorize(self, browser=False):
                peer = ipaddress.ip_address(self.client_address[0])
                if not any(peer in network for network in _LAN_RANGES):
                    self._reply(403, "仅限局域网访问")
                    return False
                auth = self.headers.get("Authorization", "")
                if browser:
                    supplied = auth[7:].encode("utf-8") if auth.startswith("Bearer ") else b""
                    if hmac.compare_digest(supplied, receiver.browser_token.encode("ascii")):
                        return True
                    self._reply(401, "连接已失效，请重新扫描电脑上的二维码")
                    return False
                try:
                    supplied = base64.b64decode(auth[6:], validate=True) if auth[:6].lower() == "basic " else b""
                except ValueError:
                    supplied = b""
                if not hmac.compare_digest(supplied, receiver._credential_bytes):
                    self._reply(401, "配对信息不正确")
                    return False
                return True

            def _with_image_slot(self, callback, *args, **kwargs):
                if not receiver._image_slots.acquire(blocking=False):
                    return self._reply(429, "电脑正在处理图片，请稍后重试")
                try:
                    return callback(*args, **kwargs)
                finally:
                    receiver._image_slots.release()

            def _json(self, payload):
                return self._reply_body(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                        "application/json; charset=utf-8")

            def _download(self, record_id, preview=False, acknowledge=False):
                opened = receiver.exchange.open_image(record_id, preview)
                if opened is None:
                    return self._reply(404, "记录已过期，请重新发送")
                stream, size, mime = opened
                with stream:
                    start, end, partial = 0, size - 1, False
                    ranges = self.headers.get_all("Range", [])
                    if ranges and not preview:
                        match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", ranges[0]) if len(ranges) == 1 and len(ranges[0]) < 80 else None
                        if match and any(match.groups()):
                            first, last = match.groups()
                            if first:
                                start = int(first)
                                end = min(int(last), size - 1) if last else size - 1
                            else:
                                suffix = int(last)
                                start = max(0, size - suffix)
                            partial = 0 <= start <= end < size
                        if not partial:
                            return self._reply_body(416, b"", "application/octet-stream",
                                                    {"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
                    self.connection.settimeout(FILE_IO_TIMEOUT)
                    self.send_response(206 if partial else 200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(end - start + 1))
                    self.send_header("Accept-Ranges", "bytes")
                    if partial:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    if not preview:
                        record = next((r for r in receiver.exchange.snapshot().get("items", []) if r["id"] == record_id), None)
                        if record:
                            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(record["name"]))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if self.command == "HEAD":
                        return
                    stream.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = stream.read(min(FILE_CHUNK_BYTES, remaining))
                        if not chunk:
                            self.close_connection = True
                            return
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                if acknowledge and not preview and not partial:
                    receiver.exchange.acknowledge(record_id, "downloaded")

            def _exchange_get(self, path):
                if path == "/SyncClipboard.json":
                    if not self._authorize():
                        return
                    record = receiver.exchange.latest_desktop()
                    if record is None:
                        return self._json({"type": "Text", "text": "", "hasData": False})
                    if record["kind"] == "text":
                        receiver.exchange.acknowledge(record["id"], "received")
                        return self._json({"type": "Text", "text": record["text"], "hasData": False})
                    name = record["id"] + (".bin" if record["kind"] == "file" else (".jpg" if record["mime"] == "image/jpeg" else ".png"))
                    return self._json({"type": "File" if record["kind"] == "file" else "Image", "text": name, "dataName": name,
                                       "hasData": True, "size": record["size"]})
                if path.startswith("/file/"):
                    if not self._authorize():
                        return
                    name = path[6:]
                    records = receiver.exchange.snapshot().get("items", [])
                    record = next((r for r in records if r["sender"] == "desktop" and
                                   r["kind"] in ("image", "file") and name == r["id"] +
                                   (".bin" if r["kind"] == "file" else (".jpg" if r["mime"] == "image/jpeg" else ".png"))), None)
                    if record is None:
                        return self._reply(404, "记录已过期，请重新发送")
                    return self._download(record["id"], acknowledge=True)
                parts = path.split("/")
                if len(parts) == 4 and parts[2] == "download":
                    ticket = parse_qs(urlsplit(self.path).query).get("ticket", [""])[0]
                    if not receiver.valid_download_ticket(parts[3], ticket):
                        return self._reply(401, "下载链接已失效，请在网页重新点击下载")
                    return self._with_image_slot(self._download, parts[3], acknowledge=True)
                if not self._authorize(browser=True):
                    return
                if len(parts) == 5 and parts[2] == "file" and parts[4] == "download-link":
                    record = next((r for r in receiver.exchange.snapshot().get("items", [])
                                   if r["id"] == parts[3] and r["kind"] == "file"), None)
                    if record is None:
                        return self._reply(404, "记录已过期，请重新发送")
                    return self._json({"url": "/exchange/download/" + record["id"] +
                                      "?ticket=" + receiver.download_ticket(record["id"])})
                if path == "/exchange/records":
                    after = parse_qs(urlsplit(self.path).query).get("after", [""])[0]
                    if after and (len(after) > 12 or not after.isdecimal()):
                        return self._reply(400, "记录序号无效")
                    return self._json(receiver.exchange.snapshot(int(after) if after else None))
                parts = path.split("/")
                if len(parts) in (4, 5) and parts[2] in ("image", "file") and (len(parts) == 4 or (parts[2] == "image" and parts[4] == "preview")):
                    return self._with_image_slot(self._download, parts[3], preview=len(parts) == 5)
                return self._reply(404, "不支持此地址")

            def _text(self, text):
                from phone_exchange import validate_text
                try:
                    text = validate_text(text)
                except ValueError as error:
                    return self._reply(422, str(error))
                if receiver.accept_text is None:
                    return self._reply(422, "文字传输尚未启用")
                try:
                    ok, message = receiver.accept_text(text)
                except Exception:
                    ok, message = False, "电脑写入剪贴板失败"
                return self._reply(200 if ok else 422, message)

            def do_GET(self):
                try:
                    peer = ipaddress.ip_address(self.client_address[0])
                    if not any(peer in network for network in _LAN_RANGES):
                        return self._reply(403, "仅限局域网访问")
                    path = urlsplit(self.path).path
                    if receiver.exchange is not None and (path.startswith("/exchange/") or
                            path == "/SyncClipboard.json" or path.startswith("/file/")):
                        return self._exchange_get(path)
                    if path == "/android/status":
                        if self._authorize(browser=True):
                            return self._reply(200, "接收已开启")
                        return
                    from phone_android_web import ASSETS
                    asset = ASSETS.get(path)
                    if asset is None:
                        return self._reply(404, "不支持此地址")
                    return self._reply_body(200, *asset, {
                        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; manifest-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
                        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    self.close_connection = True

            def do_HEAD(self):
                self.do_GET()

            def do_POST(self):
                try:
                    if not self._authorize(browser=True):
                        return
                    path = urlsplit(self.path).path
                    if path not in ("/android/image", "/exchange/file", "/exchange/text", "/exchange/ack"):
                        return self._reply(404, "不支持此地址")
                    origin = self.headers.get("Origin")
                    if origin is not None and origin != "http://" + self.headers.get("Host", ""):
                        return self._reply(403, "请从电脑二维码打开传图页")
                    if self.headers.get("Transfer-Encoding"):
                        return self._reply(411, "需要 Content-Length")
                    raw_size = self.headers.get("Content-Length", "")
                    if len(raw_size) > 10 or not raw_size.isascii() or not raw_size.isdecimal():
                        return self._reply(411, "需要 Content-Length")
                    size = int(raw_size)
                    if path in ("/exchange/text", "/exchange/ack"):
                        if receiver.exchange is None:
                            return self._reply(404, "不支持此地址")
                        if not 0 < size <= (512 * 1024 if path.endswith("text") else 8192):
                            return self._reply(413, "内容过大")
                        try:
                            self._body_consumed = True
                            payload = json.loads(self.rfile.read(size))
                            if not isinstance(payload, dict):
                                raise ValueError()
                            if path.endswith("text"):
                                return self._text(payload.get("text"))
                            ok = receiver.exchange.acknowledge(payload.get("id"), payload.get("state"))
                        except (ValueError, UnicodeError):
                            return self._reply(400, "内容无效")
                        return self._reply(200 if ok else 404, "已更新" if ok else "记录已过期")
                    if path == "/exchange/file":
                        if not 0 < size <= MAX_FILE_BYTES:
                            return self._reply(413, f"单个文件最大 {FILE_LIMIT_LABEL}")
                        name = unquote(self.headers.get("X-File-Name", "文件"))
                        return self._with_image_slot(self._file, name, size, direct=True, generic=True)
                    if not 0 < size <= MAX_IMAGE_BYTES:
                        return self._reply(413, "单张图片最大 32 MiB")
                    return self._with_image_slot(self._file, "", size, direct=True)
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    self.close_connection = True

            def do_PUT(self):
                try:
                    if not self._authorize():
                        return
                    path = urlsplit(self.path).path
                    if path == "/SyncClipboard.json":
                        return self._with_image_slot(self._manifest)
                    if self.headers.get("Transfer-Encoding"):
                        return self._reply(411, "需要 Content-Length")
                    raw_size = self.headers.get("Content-Length", "")
                    if len(raw_size) > 10 or not raw_size.isascii() or not raw_size.isdecimal():
                        return self._reply(411, "需要 Content-Length")
                    size = int(raw_size)
                    if path.startswith("/file/"):
                        if not 0 < size <= (MAX_FILE_BYTES if receiver.accept_file else MAX_IMAGE_BYTES):
                            return self._reply(413, "文件过大")
                        name = unquote(path[6:]).replace(":", "-")
                        if not 0 < len(name) <= 1024 or "\x00" in name:
                            return self._reply(400, "图片名称无效")
                        return self._with_image_slot(self._file, name, size)
                    return self._reply(404, "不支持此地址")
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    self.close_connection = True

            def _file(self, name, size, direct=False, generic=False):
                if shutil.disk_usage(receiver.directory).free < size + DISK_RESERVE_BYTES:
                    return self._reply(507, "临时磁盘空间不足，请在电脑清理后重试")
                self.connection.settimeout(FILE_IO_TIMEOUT)
                self._body_consumed = True
                fd, path = tempfile.mkstemp(prefix="phone_", suffix=".img", dir=receiver.directory)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        remaining = size
                        while remaining:
                            chunk = self.rfile.read(min(FILE_CHUNK_BYTES, remaining))
                            if not chunk:
                                return self._reply(400, "文件上传中断，请重新发送")
                            stream.write(chunk)
                            remaining -= len(chunk)
                    with open(path, "rb") as stream:
                        magic = stream.read(16)
                    image = magic.startswith(b"\xff\xd8\xff") or magic.startswith(b"\x89PNG\r\n\x1a\n")
                    if not image and not generic and receiver.accept_file is None:
                        return self._reply(415, "支持 JPEG、PNG；其他图片请先转换为 JPEG")
                    if image and not generic and size > MAX_IMAGE_BYTES:
                        return self._reply(413, "单张图片最大 32 MiB；可通过网页选择文件发送原始文件")
                    if direct:
                        if not self._authorize(browser=True):
                            return
                        with receiver._lock:
                            if receiver._stopping:
                                return self._reply(409, "接收已关闭")
                        try:
                            if generic:
                                ok, message = receiver.accept_file(path, name) if receiver.accept_file else (False, "文件传输尚未启用")
                            else:
                                ok, message = receiver.accept_image(path)
                        except Exception:
                            ok, message = False, "电脑写入剪贴板失败"
                        if ok and not generic:
                            path = None
                        return self._reply(200 if ok else 422, message)
                    with receiver._lock:
                        if receiver._stopping:
                            return self._reply(409, "接收已关闭")
                        old = receiver._pending
                        receiver._pending = (name, path)
                    path = None
                    if old:
                        try:
                            os.remove(old[1])
                        except OSError:
                            pass
                    return self._reply(200, "图片已接收")
                finally:
                    if path:
                        try:
                            os.remove(path)
                        except OSError:
                            pass

            def _read_manifest_body(self):
                """快捷指令 JSON 可带长度或按 HTTP chunked 发送；只缓冲小描述。"""
                limit = 512 * 1024 if receiver.accept_text else _MAX_MANIFEST_BYTES
                encodings = self.headers.get_all("Transfer-Encoding", [])
                lengths = self.headers.get_all("Content-Length", [])
                if encodings:
                    if lengths:
                        self._reply(400, "请求长度不明确")
                        return None
                    if len(encodings) != 1 or encodings[0].strip().lower() != "chunked":
                        self._reply(501, "不支持此传输编码")
                        return None
                else:
                    if len(lengths) > 1:
                        self._reply(400, "请求长度不明确")
                        return None
                    length = lengths[0] if lengths else ""
                    if len(length) > 10 or not length.isascii() or not length.isdecimal():
                        self._reply(411, "需要 Content-Length 或 chunked")
                        return None
                    size = int(length)
                    if not 0 < size <= limit:
                        self._reply(413, "内容描述过大")
                        return None
                self._body_consumed = True
                try:
                    if not encodings:
                        body = self.rfile.read(size)
                        if len(body) != size:
                            raise ValueError()
                        return body
                    body = bytearray()
                    while True:
                        line = self.rfile.readline(1025)
                        if len(line) > 1024 or not line.endswith(b"\r\n"):
                            raise ValueError()
                        number = line[:-2].split(b";", 1)[0]
                        if not 0 < len(number) <= 16 or any(c not in b"0123456789abcdefABCDEF" for c in number):
                            raise ValueError()
                        size = int(number, 16)
                        if size > limit - len(body):
                            self._reply(413, "内容描述过大")
                            return None
                        if size == 0:
                            trailer_size = 0
                            while True:
                                line = self.rfile.readline(1025)
                                trailer_size += len(line)
                                if len(line) > 1024 or trailer_size > 8192 or not line.endswith(b"\r\n"):
                                    raise ValueError()
                                if line == b"\r\n":
                                    return body
                                if b":" not in line[:-2] or line.startswith((b" ", b"\t")):
                                    raise ValueError()
                        chunk = self.rfile.read(size)
                        if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                            raise ValueError()
                        body.extend(chunk)
                except ValueError:
                    self._reply(400, "内容上传不完整或分块格式无效")
                except TimeoutError:
                    self._reply(408, "内容上传超时，请重试")
                return None

            def _manifest(self):
                body = self._read_manifest_body()
                if body is None:
                    return
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeError):
                    return self._reply(400, "图片描述无效")
                if not isinstance(payload, dict):
                    return self._reply(400, "图片描述无效")
                kind = payload.get("type", payload.get("Type"))
                if kind == "Text" and payload.get("hasData") is False and receiver.accept_text is not None:
                    return self._text(payload.get("text"))
                name = payload.get("dataName")
                if kind not in ("File", "Image") or payload.get("hasData") is not True or not isinstance(name, str):
                    return self._reply(422, "内容描述无效，请重新分享单个内容")
                with receiver._lock:
                    pending = receiver._pending
                    if not pending or pending[0] != name.replace(":", "-"):
                        return self._reply(409, "请先上传图片或文件")
                    receiver._pending = None
                try:
                    is_image = False
                    with open(pending[1], "rb") as stream:
                        magic = stream.read(16)
                    is_image = magic.startswith(b"\xff\xd8\xff") or magic.startswith(b"\x89PNG\r\n\x1a\n")
                    if is_image:
                        ok, message = receiver.accept_image(pending[1])
                    else:
                        ok, message = receiver.accept_file(pending[1], safe_filename(name)) if receiver.accept_file else (False, "文件传输尚未启用")
                except Exception:
                    ok, message = False, "电脑写入剪贴板失败"
                if not ok or not is_image:
                    try:
                        os.remove(pending[1])
                    except OSError:
                        pass
                return self._reply(200 if ok else 422, message)

        self._server = _BoundedServer(("0.0.0.0", self.port), Handler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="JianJiePhoneReceive", daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            self._stopping = True
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        with self._lock:
            pending, self._pending = self._pending, None
        if pending:
            try:
                os.remove(pending[1])
            except OSError:
                pass
