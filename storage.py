"""存储位置与可恢复迁移。切换前复制、校验；配置提交后才清理旧文件。"""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing


def bootstrap_dir():
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "jianjie")


def legacy_bootstrap_dir():
    # 仅用于识别旧版本数据并迁移，新的数据和定位配置都写入 jianjie。
    return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "FloGravityWin")


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {} if default is None else default


def atomic_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path),
                                         prefix=".jianjie-", suffix=".tmp", delete=False) as stream:
            pending = stream.name
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        if pending and os.path.exists(pending):
            os.remove(pending)


def locator_path():
    return os.path.join(bootstrap_dir(), "storage-location.json")


def read_locator():
    if os.path.isfile(locator_path()):
        return _read(locator_path())
    legacy = legacy_bootstrap_dir()
    state = _read(os.path.join(legacy, "storage-location.json"))
    if state or any(os.path.isfile(os.path.join(legacy, name)) for name in
                    ("settings.json", "tasks.json", "todos.json", "fsearch.db", "stickies.json")):
        return {"data_dir": legacy, **state}
    return {}


def data_dir():
    directory = read_locator().get("data_dir") or bootstrap_dir()
    # 已设置的磁盘离线时不创建另一份空数据，避免看起来像任务丢失。
    if not os.path.isdir(directory) and os.path.normcase(directory) != os.path.normcase(bootstrap_dir()):
        raise OSError("数据目录不可用：" + directory)
    os.makedirs(directory, exist_ok=True)
    return directory


def same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def needs_brand_migration():
    return same_path(data_dir(), legacy_bootstrap_dir())


def adopt_locator():
    """用户自选目录继续生效，只把定位配置切换到新的品牌目录。"""
    if not os.path.isfile(locator_path()):
        state = read_locator()
        state["data_dir"] = data_dir()
        atomic_json(locator_path(), state)


def retire_legacy_bootstrap():
    """迁移提交后清理旧定位文件和空目录，不删除剩余数据。"""
    if not os.path.isfile(locator_path()) or pending_change() or needs_brand_migration():
        return
    legacy = legacy_bootstrap_dir()
    if os.path.islink(legacy) or os.path.isjunction(legacy):
        return
    try:
        os.remove(os.path.join(legacy, "storage-location.json"))
    except OSError:
        pass
    # 旧版把管理员操作的临时回执放进应用数据。迁到 TEMP，保留其内容，
    # 不让文件级权限阻挡任务、索引迁移或让旧品牌目录长期残留。
    if os.path.isdir(legacy):
        for name in os.listdir(legacy):
            if not name.startswith("phone_firewall_"):
                continue
            source = os.path.join(legacy, name)
            if not os.path.isdir(source) or os.path.islink(source) or os.path.isjunction(source):
                continue
            target = None
            try:
                if not set(os.listdir(source)).issubset({"configure.ps1", "result.json"}):
                    continue
                target = tempfile.mkdtemp(prefix="JianJiePhoneFirewall_")
                resolved_source = os.path.normcase(os.path.realpath(source))
                resolved_legacy = os.path.normcase(os.path.realpath(legacy))
                resolved_target = os.path.normcase(os.path.realpath(target))
                resolved_temp = os.path.normcase(os.path.realpath(tempfile.gettempdir()))
                if os.path.commonpath((resolved_source, resolved_legacy)) != resolved_legacy or \
                        os.path.commonpath((resolved_target, resolved_temp)) != resolved_temp:
                    continue
                os.rmdir(target)  # 仅移除刚创建的空占位目录。
                os.rename(source, target)
            except OSError:
                pass
            finally:
                if target:
                    try:
                        os.rmdir(target)  # 非空的回执目录会保留。
                    except OSError:
                        pass
    empty_candidates = []
    for root, dirs, _files_left in os.walk(legacy, followlinks=False):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))
                   and not os.path.isjunction(os.path.join(root, name))]
        empty_candidates.append(root)
    resolved_legacy = os.path.normcase(os.path.realpath(legacy))
    for root in reversed(empty_candidates):
        resolved_root = os.path.normcase(os.path.realpath(root))
        if os.path.commonpath((resolved_legacy, resolved_root)) != resolved_legacy:
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass


def nested_paths(a, b):
    a, b = os.path.normcase(os.path.realpath(a)), os.path.normcase(os.path.realpath(b))
    try:
        return os.path.commonpath((a, b)) in (a, b)
    except ValueError:
        return False  # 不同磁盘


def validate_directory(path):
    if not os.path.isabs(path) or not os.path.isdir(path):
        raise OSError("所选文件夹不存在或无法访问")
    with tempfile.TemporaryFile(prefix=".jianjie-write-", dir=path) as stream:
        stream.write(b"1")
        stream.flush()


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _files(directory, skip_runtime=False):
    if not os.path.isdir(directory):
        return
    for root, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(root, name))
                   and not os.path.isjunction(os.path.join(root, name))
                   and not (skip_runtime and root == directory and name.startswith("phone_firewall_"))]
        for name in files:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                yield path, os.path.relpath(path, directory)


def prepare_change(settings, changes, output_sources, transfer_source):
    """只登记迁移；应用正常退出后，在下一次启动时执行，数据库没有写入者。"""
    old = data_dir()
    destination = changes.get("data_dir", old)
    for path in (destination, changes.get("save_dir"), changes.get("receive_dir")):
        if path:
            validate_directory(path)
    if not same_path(old, destination):
        if nested_paths(old, destination):
            raise OSError("数据目录不能放在原数据目录内部或其上级目录")
        # 任务和索引不能通过覆盖另一套数据库来“合并”。要求干净的数据目标。
        if any(os.path.exists(os.path.join(destination, name)) for name in
               ("settings.json", "tasks.json", "todos.json", "fsearch.db", "exchange-history.json")):
            raise OSError("该文件夹已有简截数据，请选择空文件夹，避免覆盖历史数据")
    target = changes.get("receive_dir")
    if target and transfer_source and not same_path(target, transfer_source) and nested_paths(target, transfer_source):
        raise OSError("互传文件夹不能放在原目录内部或其上级目录")
    state = read_locator()
    state["pending"] = dict(id=uuid.uuid4().hex, source_data=old, data_dir=destination,
                             settings=settings, changes=changes, output_sources=output_sources,
                             transfer_source=transfer_source)
    atomic_json(locator_path(), state)


def pending_change():
    return read_locator().get("pending")


def cancel_change():
    state = read_locator()
    state.pop("pending", None)
    atomic_json(locator_path(), state)


def _copy(source, target, plan_id, strict=False):
    """同名同内容去重；不同内容改名。原子写，失败不留下半个成品。"""
    original_hash = digest(source)
    if os.path.exists(target):
        if digest(target) == original_hash:
            return target, original_hash
        if strict:
            raise OSError("目标已有不同的数据文件：" + target)
        stem, ext = os.path.splitext(target)
        target = stem + "_迁移_" + plan_id[:8] + ext
        if os.path.exists(target):
            if digest(target) == original_hash:
                return target, original_hash
            raise OSError("迁移文件同名冲突：" + target)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".jianjie-moving-", dir=os.path.dirname(target))
        with os.fdopen(fd, "wb") as output, open(source, "rb") as input_file:
            shutil.copyfileobj(input_file, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if digest(temporary) != original_hash or digest(source) != original_hash:
            raise OSError("源文件在迁移期间发生变化：" + source)
        os.replace(temporary, target)
        temporary = None
        return target, original_hash
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)


def apply_pending(progress=None):
    state = read_locator()
    plan = state.get("pending")
    if not plan:
        return 0
    old, new = plan["source_data"], plan["data_dir"]
    changes = plan["changes"]
    new_settings = {**plan["settings"], **_read(os.path.join(old, "settings.json")),
                    **{k: v for k, v in changes.items() if k != "data_dir"}}
    old_files = []
    transfer_names = {}
    database_sources = []
    total = 0
    # 成品只迁移本软件命名的文件，不挪动用户文件夹里的其他文件。
    roots = [(root, changes.get("save_dir"), False) for root in plan["output_sources"]]
    roots.append((plan.get("transfer_source"), changes.get("receive_dir"), True))
    seen = set()
    for source_dir, target_dir, all_files in roots:
        if not source_dir or not target_dir or same_path(source_dir, target_dir):
            continue
        for source, rel in _files(source_dir):
            if not all_files and (os.path.dirname(rel) or not os.path.basename(source).startswith(
                    ("截图_", "长截图_", "GIF_", "录屏_", "快剪_", "二维码_"))):
                continue
            if os.path.realpath(source) in seen:
                continue
            seen.add(os.path.realpath(source))
            if progress:
                progress("正在迁移文件 · " + os.path.basename(source))
            target, checksum = _copy(source, os.path.join(target_dir, rel), plan["id"])
            if all_files:
                transfer_names[rel] = os.path.relpath(target, target_dir)
            old_files.append((source, target, checksum, checksum))
            total += 1
    if not same_path(old, new):
        for source, rel in _files(old, skip_runtime=True):
            if rel in ("storage-location.json", "settings.json", "exchange-history.json") \
                    or rel.endswith(("-wal", "-shm", ".tmp")) or source in seen:
                continue
            target = os.path.join(new, rel)
            if progress:
                progress("正在迁移数据 · " + rel)
            if rel in ("fsearch.db", "fsearch-fast.db"):
                # SQLite backup 合并 WAL；绝不裸复制正在使用的主库。
                fd, temporary = tempfile.mkstemp(prefix=".jianjie-index-", dir=new)
                os.close(fd)
                try:
                    with closing(sqlite3.connect(source)) as checkpoint:
                        if checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                            raise OSError("文件索引仍在写入，暂不能迁移")
                    before = {p: digest(p) for p in (source, source + "-wal") if os.path.isfile(p)}
                    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
                        src.backup(dst, pages=512, sleep=0.02)
                        if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            raise OSError("文件索引校验失败")
                    after = {p: digest(p) for p in (source, source + "-wal") if os.path.isfile(p)}
                    if before != after:
                        raise OSError("文件索引仍在写入，请停止索引后重试")
                    os.replace(temporary, target)
                    database_sources.append((source, before, target, digest(target)))
                finally:
                    if os.path.exists(temporary):
                        os.remove(temporary)
            else:
                target, checksum = _copy(source, target, plan["id"], strict=True)
                old_files.append((source, target, checksum, checksum))
            total += 1
    history_path = os.path.join(new, "exchange-history.json")
    history_source = os.path.join(old, "exchange-history.json")
    if os.path.isfile(history_source) and (transfer_names or not same_path(old, new)):
        history = _read(history_source, [])
        if isinstance(history, list):
            for record in history:
                if isinstance(record, dict) and record.get("_archive_name") in transfer_names:
                    record["_archive_name"] = transfer_names[record["_archive_name"]]
            atomic_json(history_path, history)
            if not same_path(old, new):
                old_files.append((history_source, history_path, digest(history_source), digest(history_path)))
    atomic_json(os.path.join(new, "settings.json"), new_settings)
    old_settings_path = os.path.join(old, "settings.json")
    if not same_path(old, new) and os.path.isfile(old_settings_path):
        old_files.append((old_settings_path, os.path.join(new, "settings.json"),
                          digest(old_settings_path), digest(os.path.join(new, "settings.json"))))
    rewritten = {os.path.join(new, "settings.json"), history_path}
    old_files = [(source, target, source_hash,
                  digest(target) if target in rewritten else target_hash)
                 for source, target, source_hash, target_hash in old_files]
    state.pop("pending", None)
    state["data_dir"] = new
    state["last_migration"] = {"files": total, "source_data": old, "data_dir": new}
    atomic_json(locator_path(), state)
    # 已提交配置，且双方仍与校验值相同，才删除具体源文件；不删除用户选的目录。
    for source, target, checksum, target_hash in old_files:
        try:
            if not same_path(source, target) and digest(source) == checksum and digest(target) == target_hash:
                os.remove(source)
        except OSError:
            pass  # 无法清理时只留下旧副本，不影响新目录。
    for source, checksums, target, target_hash in database_sources:
        try:
            current = {p: digest(p) for p in (source, source + "-wal") if os.path.isfile(p)}
            if current == checksums and digest(target) == target_hash:
                for path in (source, source + "-wal", source + "-shm"):
                    if os.path.isfile(path):
                        os.remove(path)
        except OSError:
            pass
    for root in plan["output_sources"]:
        if os.path.basename(root) in ("截图", "录屏", "新建"):
            try:
                os.rmdir(root)
                os.rmdir(os.path.dirname(root))
            except OSError:
                pass
    return total
