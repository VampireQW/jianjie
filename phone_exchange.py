"""互传的有界临时记录；原图落盘，网络线程只读取分块文件。"""
import os
import tempfile
import threading
import time
import uuid
import re
import shutil
import math

MAX_TEXT_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
FILE_LIMIT_LABEL = "2 GiB"
FILE_CHUNK_BYTES = 256 * 1024
DISK_RESERVE_BYTES = 16 * 1024 * 1024


def safe_filename(name):
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:180]
    if not name or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)), *("LPT" + str(i) for i in range(1, 10))}:
        name = "文件_" + name
    return name


def validate_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请输入要发送的文字")
    if "\0" in text:
        raise ValueError("文字不能包含空字符")
    try:
        size = len(text.encode("utf-8"))
    except UnicodeError:
        raise ValueError("文字编码无效") from None
    if size > MAX_TEXT_BYTES:
        raise ValueError("文字最多 64 KiB，请分段发送")
    return text


class ExchangeStore:
    LIMIT = 9
    TTL = 30 * 60

    def __init__(self, directory, on_change=None, archive_dir=None, history_path=None):
        self.directory = directory
        self.on_change = on_change
        self._lock = threading.RLock()
        self._records = []
        self._garbage = []
        self._revision = 0
        self._epoch = 0
        self.archive_dir = archive_dir
        self.history_path = history_path
        if archive_dir and history_path:
            import storage
            history = storage._read(history_path(), [])
            root = archive_dir()
            for item in history if isinstance(history, list) else []:
                name = item.get("_archive_name", "") if isinstance(item, dict) else ""
                path = os.path.join(root, name)
                if (name and name == os.path.basename(name) and os.path.isfile(path)
                        and not self._expired(item, time.time())):
                    self._records.append({**item, "created": float(item["created"]),
                                          "_path": path, "_preview": None, "_keep": True})
            self._records = self._records[:self.LIMIT]
            self._persist()

    @classmethod
    def _expired(cls, record, now):
        try:
            created = float(record["created"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return True
        return not math.isfinite(created) or created > now + cls.TTL or now >= created + cls.TTL

    def _persist(self):
        if self.history_path:
            import storage
            storage.atomic_json(self.history_path(),
                                [{k: v for k, v in r.items() if k not in ("_path", "_preview")}
                                 for r in self._records if r.get("_keep")])

    def _cache_path(self, path):
        if not path:
            return False
        root = os.path.normcase(os.path.realpath(self.directory))
        target = os.path.normcase(os.path.realpath(path))
        try:
            return target != root and os.path.commonpath((root, target)) == root
        except ValueError:
            return False

    def _delete(self, record):
        keys = ("_preview",) if record.get("_keep") else ("_path", "_preview")
        self._garbage.extend(record[k] for k in keys if self._cache_path(record.get(k)))

    def _sweep(self):
        now = time.time()
        expired = [r for r in self._records if self._expired(r, now)]
        if expired:
            for record in expired:
                self._delete(record)
                self._records.remove(record)
            self._revision += 1
            self._persist()
        remaining = []
        for path in self._garbage:
            try:
                os.remove(path)
                if os.path.basename(os.path.dirname(path)).startswith("exchange_file_"):
                    try:
                        os.rmdir(os.path.dirname(path))
                    except OSError:
                        pass
            except FileNotFoundError:
                pass
            except OSError:
                remaining.append(path)
        self._garbage = remaining

    def expire(self):
        with self._lock:
            self._sweep()

    def next_expiry_delay(self):
        with self._lock:
            if not self._records:
                return None
            expiry = min(float(r["created"]) + self.TTL for r in self._records)
            return max(1, min(self.TTL * 1000, math.ceil((expiry - time.time()) * 1000)))

    def _changed(self):
        if self.on_change is not None:
            self.on_change()

    def _add(self, record):
        if record.get("sender") == "phone" and record.get("_path") and self.archive_dir:
            import storage
            root = self.archive_dir()
            name = safe_filename(record["name"])
            target, _ = storage._copy(record["_path"], os.path.join(root, name), uuid.uuid4().hex)
            # 只删除自己管理的互传缓存副本；接收成品不参与过期清理。
            temporary = record["_path"]
            record.update(_path=target, _archive_name=os.path.basename(target), _keep=True)
            if not storage.same_path(temporary, target):
                os.remove(temporary)
                if os.path.basename(os.path.dirname(temporary)).startswith("exchange_file_"):
                    os.rmdir(os.path.dirname(temporary))
        record.update(id=uuid.uuid4().hex, created=time.time(), status=(("电脑已接收" if record["kind"] == "file" else "已复制到电脑") if record["sender"] == "phone" else "待手机获取"))
        with self._lock:
            self._records.insert(0, record)
            for old in self._records[self.LIMIT:]:
                self._delete(old)
            del self._records[self.LIMIT:]
            self._revision += 1
            self._sweep()
            self._persist()
        self._changed()
        return self._public(record)

    def add_text(self, text, sender="desktop"):
        return self._add(dict(kind="text", text=validate_text(text), sender=sender))

    def add_file(self, source, name=None, sender="desktop", move=False):
        with self._lock:
            epoch = self._epoch
        size = os.path.getsize(source)
        if not 0 < size <= MAX_FILE_BYTES:
            raise ValueError(f"请选择非空文件，单个文件最大 {FILE_LIMIT_LABEL}")
        if move:
            if os.path.realpath(os.path.dirname(source)) != os.path.realpath(self.directory):
                raise ValueError("接收文件必须位于互传临时目录")
        elif shutil.disk_usage(self.directory).free < size + DISK_RESERVE_BYTES:
            raise OSError("临时磁盘空间不足，请清理后重试")
        name = safe_filename(name or os.path.basename(source))
        folder = tempfile.mkdtemp(prefix="exchange_file_", dir=self.directory)
        path = os.path.join(folder, name)
        try:
            if move:
                # 网络文件已完整落盘，同目录原子移入记录，避免大文件再拷贝一次。
                os.replace(source, path)
            else:
                with open(source, "rb") as stream, open(path, "xb") as target:
                    remaining = size
                    while remaining:
                        chunk = stream.read(min(FILE_CHUNK_BYTES, remaining))
                        if not chunk:
                            raise ValueError("文件已变化，请重新选择")
                        target.write(chunk)
                        remaining -= len(chunk)
                    if stream.read(1):
                        raise ValueError("文件已变化，请重新选择")
            with self._lock:
                if self._epoch != epoch:
                    raise ValueError("配对或互传状态已变化，请重新发送")
                return self._add(dict(kind="file", name=name, size=size, mime="application/octet-stream",
                                      sender=sender, _path=path))
        except BaseException:
            if os.path.exists(path):
                os.remove(path)
            if os.path.isdir(folder):
                os.rmdir(folder)
            raise

    def add_image(self, source, width, height, sender="desktop", preview=None):
        size = os.path.getsize(source)
        if not 0 < size <= MAX_IMAGE_BYTES:
            raise ValueError("单张图片最大 32 MiB")
        with open(source, "rb") as stream:
            magic = stream.read(16)
        if magic.startswith(b"\xff\xd8\xff"):
            ext, mime = "jpg", "image/jpeg"
        elif magic.startswith(b"\x89PNG\r\n\x1a\n"):
            ext, mime = "png", "image/png"
        else:
            raise ValueError("支持 JPEG、PNG；其他图片请先转换")
        fd, path = tempfile.mkstemp(prefix="exchange_", suffix="." + ext, dir=self.directory)
        thumbnail = None
        try:
            with os.fdopen(fd, "wb") as target, open(source, "rb") as stream:
                remaining = size
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise ValueError("图片文件已变化，请重新发送")
                    target.write(chunk)
                    remaining -= len(chunk)
                if stream.read(1):
                    raise ValueError("图片文件已变化，请重新发送")
            if os.path.getsize(path) != size:
                raise ValueError("图片文件已变化，请重新发送")
            if preview is not None and not preview.isNull():
                fd, thumbnail = tempfile.mkstemp(prefix="exchange_preview_", suffix=".png", dir=self.directory)
                os.close(fd)
                if not preview.save(thumbnail, "PNG"):
                    raise OSError("图片预览保存失败")
            record = dict(kind="image", sender=sender, width=width, height=height,
                          size=size, mime=mime, name="简截图片." + ext, _path=path, _preview=thumbnail)
            result = self._add(record)
            path = thumbnail = None
            return result
        finally:
            for temporary in (path, thumbnail):
                if temporary:
                    try:
                        os.remove(temporary)
                    except OSError:
                        pass

    @staticmethod
    def _public(record):
        return {k: v for k, v in record.items() if not k.startswith("_")}

    def snapshot(self, after=None):
        with self._lock:
            self._sweep()
            changed = after != self._revision
            result = dict(revision=self._revision, changed=changed)
            if changed:
                result["items"] = [self._public(r) for r in self._records]
            return result

    def local_records(self):
        with self._lock:
            self._sweep()
            return [dict(r) for r in self._records]

    def latest_desktop(self):
        with self._lock:
            self._sweep()
            return next((self._public(r) for r in self._records if r["sender"] == "desktop"), None)

    def open_image(self, record_id, preview=False):
        with self._lock:
            self._sweep()
            record = next((r for r in self._records if r["id"] == record_id and r["kind"] in ("image", "file")), None)
            if record is None:
                return None
            path = record.get("_preview") if preview else record["_path"]
            if not path:
                return None
            try:
                stream = open(path, "rb")
            except OSError:
                return None
            return stream, os.fstat(stream.fileno()).st_size, ("image/png" if preview else record["mime"])

    def acknowledge(self, record_id, state):
        # 网络回传只确认“获取/下载”，不冒充手机已存相册或已复制。
        if state not in ("received", "downloaded"):
            raise ValueError("接收状态无效")
        with self._lock:
            self._sweep()
            record = next((r for r in self._records if r["id"] == record_id and r["sender"] == "desktop"), None)
            if record is None:
                return False
            label = ("手机已下载文件" if record["kind"] == "file" else "手机已下载原图") if state == "downloaded" else "手机已获取"
            if record["status"] == label or record["status"] == "手机已下载原图":
                return True
            record["status"] = label
            self._revision += 1
        self._changed()
        return True

    def clear(self, preserve_history=False):
        with self._lock:
            self._sweep()
            if preserve_history:
                self._persist()
            elif self.history_path:
                # Commit the empty history before changing the displayed records.
                import storage
                storage.atomic_json(self.history_path(), [])
            self._epoch += 1
            for record in self._records:
                self._delete(record)
            self._records.clear()
            self._revision += 1
            self._sweep()
        self._changed()
