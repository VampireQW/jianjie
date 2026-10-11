# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 文件搜索：Everything 式全盘文件名即时搜索。

索引持久化在用户数据目录（fsearch.db），跨会话复用，启动后后台校准，开窗复用：
1. 管理员：对照 USN 日志位标（meta 表存 journal_id + NextUsn），从上次位标起
   FSCTL_READ_USN_JOURNAL 追平错过的增删改（秒级）；日志回卷才 MFT 直读全量；
2. 非管理员：旧索引行保留可查，目录遍历（junction 不下钻防环）写临时表后原子换表。
索引建好后每卷启动实时监控线程：管理员走 USN 日志阻塞读（秒级），非管理员走
ReadDirectoryChangesW 递归监听（缓冲溢出丢事件则整卷重扫兜底）。
搜索为文件名子串 AND 匹配（大小写不敏感），输入即搜，单次最多显示 500 条。
"""
import ctypes
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QTextCharFormat, QTextLayout, QTextOption
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QProgressBar, QPushButton,
                               QSizePolicy, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QVBoxLayout, QWidget)

import common as C
import widgets as W
import search_native as N

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateFileW.restype = ctypes.c_void_p
_kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p]
_kernel32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                      ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

FSCTL_ENUM_USN_DATA = 0x000900B3
ERROR_HANDLE_EOF = 38
INVALID_HANDLE = 0xFFFFFFFFFFFFFFFF
_SHARE_RW = 1 | 2
_OPEN_EXISTING = 3
_USN_HDR = struct.Struct("<IHHQQQQIIIIHH")     # USN_RECORD_V2 头 60 字节（4 个 8 字节字段）
_HIGH_USN = 0x7FFFFFFFFFFFFFFF
_BATCH = 8192             # 索引线程每批最多暂存约 8k 行，避免 5 万行路径驻留
_RESULT_CAP = 500
_GENERIC_READ = 0x80000000
_FILE_REPARSE = 0x400                     # junction/符号链接目录：入索引但不下钻（否则环爆 3GB）
FSCTL_QUERY_USN_JOURNAL = 0x000900F4
FSCTL_READ_USN_JOURNAL = 0x000900BB
USN_FILE_CREATE = 0x00000100
USN_FILE_DELETE = 0x00000200
# Windows USN_REASON_* values: OLD_NAME=0x1000, NEW_NAME=0x2000.
# https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-read_usn_journal_data_v0
USN_RENAME_NEW = 0x00002000
USN_RENAME_OLD = 0x00001000
_USN_REASON_VERSION = "2"                  # R255：旧 USN 索引需要校准一次，纠正已写入的旧名称
USN_WATCH_MASK = (USN_FILE_CREATE | USN_FILE_DELETE | USN_RENAME_NEW | USN_RENAME_OLD)
_RJ_V1 = struct.Struct("<QIIQQQHH")       # READ_USN_JOURNAL_DATA_V1（BytesToWaitFor=1 阻塞等新记录）
_JOURNAL_DATA = struct.Struct("<QQQQQ")   # USN_JOURNAL_DATA：id/first/next/lowest/max
_FNI = struct.Struct("<III")              # FILE_NOTIFY_INFORMATION 头
FNI_ADDED, FNI_REMOVED, FNI_REN_OLD, FNI_REN_NEW = 1, 2, 4, 5
FILE_LIST_DIRECTORY = 0x0001
_ERROR_MORE_DATA = 234                    # 同步 ReadDirectoryChangesW 缓冲溢出

_kernel32.ReadDirectoryChangesW.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
    ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p, ctypes.c_void_p]

# 实时监控全局状态：变更计数（窗口轮询刷新）、每卷监控线程、换代序号、备注、重扫去重
_DIRTY = {"n": 0}
_monitors = {}
_stopping = threading.Event()
_cancel_path = None


def _check_running():
    if _stopping.is_set() or (_cancel_path and os.path.exists(_cancel_path)):
        raise RuntimeError("索引已停止")
_epoch = {}
_mon_note = {}
_indexing = threading.Event()   # 全量重建中：溢出重扫跳过（worker 正在整卷刷新，且换表长事务不再相撞）
# 应用级写锁：重建/重扫/监控的每个写事务段先抢它再落库——同进程内彻底串行，
# SQLite 锁只在跨进程时由 busy_timeout 兑底（目录改名平移等长事务不再把 worker 挂死）
_wlock = threading.RLock()
_rewalks = {}
_mon_lock = threading.Lock()
_native_lock = threading.Lock()
_native_thread = None
_native_pending = threading.Event()
_native_cancel_check = (0.0, False)


def _native_cancelled():
    global _native_cancel_check
    if _stopping.is_set():
        return True
    if _cancel_path:
        now = time.monotonic()
        if now >= _native_cancel_check[0]:
            _native_cancel_check = (now + 0.2, os.path.exists(_cancel_path))
        return _native_cancel_check[1]
    return False


def _schedule_native_sync(path=None):
    """Coalesce filesystem bursts; the native writer exits after draining its disk queue."""
    global _native_thread
    if _stopping.is_set() or _cancel_path:
        return
    path = path or db_path()
    with _native_lock:
        _native_pending.set()
        if _native_thread is None:
            _native_thread = threading.Thread(target=_native_sync_worker, args=(path,),
                                              name="jianjie-search-sync", daemon=True)
            _native_thread.start()


def _native_sync_worker(path):
    global _native_thread
    try:
        while not _stopping.wait(0.25):
            if _indexing.is_set():
                continue
            with _native_lock:
                _native_pending.clear()
            changed = N.sync(path, cancelled=_native_cancelled, limit=8192)
            if changed:
                _DIRTY["n"] += 1
            with _native_lock:
                if changed < 8192 and not _native_pending.is_set():
                    _native_thread = None
                    return
    except Exception as error:
        _mon_note["搜索"] = str(error)
    finally:
        with _native_lock:
            if _native_thread is threading.current_thread():
                _native_thread = None


def _bounded_db(con, writer=False, cache_kib=4096):
    """Keep SQLite's page cache small in long-lived monitor connections."""
    con.execute("PRAGMA cache_size=-{}".format(int(cache_kib)))
    con.execute("PRAGMA temp_store=FILE")
    con.execute("PRAGMA mmap_size=0")
    if writer:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA busy_timeout=30000")


def fixed_drives():
    """所有固定磁盘盘符（GetDriveTypeW=3），如 ['C:', 'D:']。"""
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    out = []
    for i in range(26):
        if (mask >> i) & 1:
            d = chr(65 + i) + ":"
            if ctypes.windll.kernel32.GetDriveTypeW(d + "\\") == 3:
                out.append(d)
    return out


def parse_usn_record_ex(buf, off):
    """解析一条 USN_RECORD_V2 → (frn, parent, name, is_dir, reason, reclen)；坏记录 None。"""
    (reclen, major, _minor, frn, parent, _usn, _ts, reason, _src, _sec,
     attrs, namelen, nameoff) = _USN_HDR.unpack_from(buf, off)
    if reclen <= 0 or major != 2 or off + reclen > len(buf) or nameoff + namelen > reclen:
        return None
    name = bytes(buf[off + nameoff: off + nameoff + namelen]).decode("utf-16-le", "ignore")
    return frn, parent, name, bool(attrs & 0x10), reason, reclen


def parse_usn_record(buf, off):
    r = parse_usn_record_ex(buf, off)
    return None if r is None else (r[0], r[1], r[2], r[3], r[5])


def usn_record_v2_bytes(frn, parent, attrs, name, reason=0):
    """合成一条 USN_RECORD_V2 字节（冒烟单测/增量落库自测用）。"""
    nb = name.encode("utf-16-le")
    return _USN_HDR.pack(60 + len(nb), 2, 0, frn, parent, 0, 0, reason, 0, 0,
                         attrs, len(nb), 60) + nb


def db_path():
    """索引库持久化在用户数据目录：跨会话复用，配合 USN 位标做到开窗秒级增量校准。"""
    return os.path.join(C.data_dir(), "fsearch.db")


class _IndexExclusions:
    """Exact index paths, plus their directory's NTFS identity for USN records."""

    def __init__(self, source):
        self.paths = frozenset(os.path.normcase(os.path.abspath(base + suffix))
                               for base in (source, N.index_path(source))
                               for suffix in ("", "-wal", "-shm", "-journal")) if source else frozenset()
        self.names = frozenset(os.path.basename(path) for path in self.paths)
        self.parents = {}
        if source:
            directory = os.path.dirname(os.path.abspath(source))
            handle = _kernel32.CreateFileW(directory, 0, 1 | 2 | 4, None,
                                          _OPEN_EXISTING, 0x02000000, None)
            if handle and handle != INVALID_HANDLE:
                try:
                    # BY_HANDLE_FILE_INFORMATION ends with the two file-index DWORDs.
                    info = ctypes.create_string_buffer(52)
                    get_info = _kernel32.GetFileInformationByHandle
                    get_info.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                    get_info.restype = ctypes.c_int
                    if get_info(handle, info):
                        high, low = struct.unpack_from("<II", info, 44)
                        self.parents[os.path.splitdrive(directory)[0].upper()] = (high << 32) | low
                finally:
                    _kernel32.CloseHandle(handle)

    def path(self, path):
        return os.path.basename(path).lower() in self.names \
            and os.path.normcase(os.path.abspath(path)) in self.paths

    def usn(self, volume, parent, name):
        return name.lower() in self.names and self.parents.get(volume.upper()) == parent


_exclusion_cache = {}


def _index_exclusions(con):
    source = next((path for _, name, path in con.execute("PRAGMA database_list")
                   if name == "main"), "")
    key = os.path.normcase(os.path.abspath(source)) if source else ""
    with _wlock:
        if key not in _exclusion_cache:
            if len(_exclusion_cache) >= 4:
                _exclusion_cache.pop(next(iter(_exclusion_cache)))
            _exclusion_cache[key] = _IndexExclusions(source)
        return _exclusion_cache[key]


def _purge_index_artifacts(con):
    """Remove old self-indexed rows; dirty triggers remove them from the native index."""
    excluded = _index_exclusions(con)
    removed = 0
    for path in excluded.paths:
        volume = os.path.splitdrive(path)[0].upper()
        removed += con.execute("DELETE FROM files WHERE v=?"
                               " AND path=? COLLATE NOCASE", (volume, path)).rowcount
    # Skip the scan entirely for directory-walk indexes (all their frn values are zero).
    for volume, parent in excluded.parents.items():
        if con.execute("SELECT 1 FROM files WHERE v=? AND frn>0 LIMIT 1", (volume,)).fetchone():
            placeholders = ",".join("?" for _ in excluded.names)
            removed += con.execute("DELETE FROM files WHERE v=? AND frn>0 AND parent=?"
                                   " AND name COLLATE NOCASE IN (" + placeholders + ")",
                                   (volume, parent, *excluded.names)).rowcount
    if removed:
        count = _meta_get(con, "count")
        if count is not None:
            _meta_set(con, "count", max(0, int(count) - removed))
    return excluded


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ensure_schema(con):
    con.execute("CREATE TABLE IF NOT EXISTS files(v TEXT, frn INTEGER, parent INTEGER,"
                " name TEXT, lname TEXT, isdir INT, path TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    # 部分索引只覆盖 USN 行（walk 行 frn 恒 0）：父链回溯/增量定位从全表扫变点查
    con.execute("CREATE INDEX IF NOT EXISTS ix_frn ON files(v, frn) WHERE frn > 0")
    # 按卷/按路径定位：监控每条事件都 path 点查/删（全表扫 166 万行曾是秒级长锁的根源），
    # 目录改名平移的 LIKE 'prefix%' 也走此索引变子树范围扫；ix_path 前缀覆盖 DELETE WHERE v=?
    con.execute("DROP INDEX IF EXISTS ix_v")
    con.execute("CREATE INDEX IF NOT EXISTS ix_path ON files(v, path COLLATE NOCASE)")
    # Append-only sequence avoids losing a second change to the same row during a native rebuild.
    con.execute("CREATE TABLE IF NOT EXISTS jj_search_dirty_v1("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT,fid INTEGER NOT NULL)")
    con.execute("INSERT OR IGNORE INTO meta(k,v) VALUES('native_source_id',lower(hex(randomblob(16))))")
    con.executescript("""
        CREATE TRIGGER IF NOT EXISTS jj_files_ai AFTER INSERT ON files BEGIN
            INSERT INTO jj_search_dirty_v1(fid) VALUES(new.rowid);
        END;
        CREATE TRIGGER IF NOT EXISTS jj_files_ad AFTER DELETE ON files BEGIN
            INSERT INTO jj_search_dirty_v1(fid) VALUES(old.rowid);
        END;
        CREATE TRIGGER IF NOT EXISTS jj_files_au AFTER UPDATE OF lname,rowid ON files
        WHEN old.lname IS NOT new.lname OR old.rowid<>new.rowid BEGIN
            INSERT INTO jj_search_dirty_v1(fid) VALUES(old.rowid);
            INSERT INTO jj_search_dirty_v1(fid) VALUES(new.rowid);
        END;
    """)
    con.commit()


def _meta_get(con, k):
    row = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else None


def _meta_set(con, k, val):
    con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?, ?)", (k, str(val)))


INDEX_TTL = 2 * 3600    # 索引新鲜阈值（秒）：超时后台全量追平——非管理员无法增量校准，监控只覆盖运行期


def _usn_index_needs_repair(con, drive=None):
    """只检查旧 USN 卷的元数据，避免启动时扫描文件表。"""
    if drive is not None:
        return (_meta_get(con, "jid:" + drive) is not None
                and _meta_get(con, "usn_reason_version:" + drive) != _USN_REASON_VERSION)
    return con.execute(
        "SELECT 1 FROM meta AS journal WHERE journal.k LIKE 'jid:%' "
        "AND NOT EXISTS (SELECT 1 FROM meta AS version "
        "WHERE version.k='usn_reason_version:' || substr(journal.k,5) AND version.v=?) LIMIT 1",
        (_USN_REASON_VERSION,)).fetchone() is not None


def _index_fresh(con, drive=None):
    """索引在 TTL 内视为新鲜：跳全量直接监控接管（重启应用不再白跑一趟全量遍历）。"""
    try:
        if _usn_index_needs_repair(con, drive):
            return False
        b = _meta_get(con, "built_at")
        return bool(b) and (time.time() - float(b)) < INDEX_TTL
    except (TypeError, ValueError):
        return False


def _open_volume(drive):
    h = _kernel32.CreateFileW("\\\\.\\{}".format(drive), _GENERIC_READ, _SHARE_RW,
                              None, _OPEN_EXISTING, 0, None)
    return h if h and h != INVALID_HANDLE else None


def _query_journal(h):
    """FSCTL_QUERY_USN_JOURNAL → (journal_id, next_usn)；日志未激活等返回 None。"""
    buf = ctypes.create_string_buffer(64)
    br = ctypes.c_uint32(0)
    if not _kernel32.DeviceIoControl(h, FSCTL_QUERY_USN_JOURNAL, None, 0,
                                     buf, 64, ctypes.byref(br), None):
        return None
    jid, _first, next_usn, _low, _max = _JOURNAL_DATA.unpack_from(buf)
    return jid, next_usn


def _like_escape(t):
    return t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _startfile(p):
    try:
        os.startfile(p)
    except OSError:
        pass


def _reveal(p):
    try:
        subprocess.Popen('explorer /select,"{}"'.format(p))
    except OSError:
        _startfile(os.path.dirname(p) or p)


# ---------------- 增量落库引擎（索引线程与实时监控线程共用） ----------------
def _apply_usn_records(con, v, buf, excluded=None):
    """把一段 USN 日志缓冲（首 Q 为 NextUsn）落进索引库 → (变更数, NextUsn)。
    只关心名字与存在性：CREATE 插入、DELETE 删除、RENAME_NEW 改名/移动；
    USN 行靠父链解析路径不存 path，所以旧名（RENAME_OLD）无需处理。"""
    if len(buf) < 8:
        return 0, None
    next_usn = struct.unpack_from("<Q", buf, 0)[0]
    excluded = excluded or _index_exclusions(con)
    ins, ups, dels = [], [], []
    off, end = 8, len(buf)
    while off + 60 <= end:
        r = parse_usn_record_ex(buf, off)
        if r is None:
            break
        off += r[5]
        frn, parent, name, isdir, reason = r[0], r[1], r[2], r[3], r[4]
        if not name or name[0] == "$":
            continue                            # MFT 元文件/回收站 $R/$I 等系统项
        if excluded.usn(v, parent, name):
            if reason & USN_RENAME_NEW and con.execute(
                    "SELECT 1 FROM files WHERE v=? AND frn=? AND frn>0 LIMIT 1", (v, frn)).fetchone():
                dels.append((v, frn))
            continue
        if reason & USN_FILE_CREATE:
            ins.append((v, frn, parent, name, name.lower(), int(isdir), None))
        elif reason & USN_FILE_DELETE:
            dels.append((v, frn))
        elif reason & USN_RENAME_NEW:
            ups.append((frn, parent, name, name.lower(), int(isdir)))
    n = len(ins) + len(ups) + len(dels)
    if dels:
        con.executemany("DELETE FROM files WHERE v=? AND frn=?", dels)
    for frn, parent, name, lname, isdir in ups:
        if con.execute("DELETE FROM files WHERE v=? AND frn=?", (v, frn)).rowcount:
            con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                        (v, frn, parent, name, lname, isdir, None))
        else:
            ins.append((v, frn, parent, name, lname, isdir, None))
    if ins:
        con.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?)", ins)
    return n, next_usn


def _apply_fni(con, v, action, rel, old_rel=None, excluded=None):
    """把一条目录监听事件落库（rel 为相对卷根路径）→ 是否动了库。"""
    full = v + "\\" + rel
    excluded = excluded or _index_exclusions(con)
    old_full = v + "\\" + old_rel if old_rel else None
    if excluded.path(full):
        if action == FNI_REN_NEW and old_full and not excluded.path(old_full):
            return bool(con.execute("DELETE FROM files WHERE v=? AND path=? COLLATE NOCASE",
                                    (v, old_full)).rowcount)
        return False
    if old_full and excluded.path(old_full):
        old_rel = None
    name = rel.rsplit("\\", 1)[-1]
    if action == FNI_REMOVED:
        con.execute("DELETE FROM files WHERE v=? AND path=? COLLATE NOCASE", (v, full))
        return True
    if action == FNI_ADDED:
        try:
            isdir = int(bool(os.path.isdir(full)))
        except OSError:
            return False
        con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                    (v, 0, 0, name, name.lower(), isdir, full))
        return True
    if action == FNI_REN_NEW:
        old_full = v + "\\" + old_rel if old_rel else None
        old_isdir = None
        if old_full:
            row = con.execute("SELECT isdir FROM files WHERE v=? AND path=? COLLATE NOCASE",
                              (v, old_full)).fetchone()
            old_isdir = row[0] if row else None
            con.execute("DELETE FROM files WHERE v=? AND path=? COLLATE NOCASE", (v, old_full))
        isdir = old_isdir
        if isdir is None:                       # 本体目录与否以旧库为准，FS 查询易有竞态
            try:
                isdir = int(bool(os.path.isdir(full)))
            except OSError:
                isdir = 0
        con.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?)",
                    (v, 0, 0, name, name.lower(), int(isdir), full))
        if old_full and isdir:                  # 目录改名/移动：子孙路径整段平移
            con.execute("UPDATE files SET path=? || SUBSTR(path, ?)"
                        " WHERE v=? AND path LIKE ? ESCAPE '\\'",
                        (full, len(old_full) + 1, v,
                         _like_escape(old_full + "\\") + "%"))
        return True
    return False


def _walk_rows(con, drive, table="files"):
    """目录遍历整卷写表；junction 入索引不下钻（防「Application Data」自指环）。"""
    stack = [drive + "\\"]
    rows = []
    total = 0
    excluded = _index_exclusions(con)
    while stack:
        _check_running()
        d = stack.pop()
        try:
            entries = os.scandir(d)
        except OSError:
            continue
        with entries:
            for e in entries:
                _check_running()
                name = e.name
                if name[0] == "$":
                    continue
                if excluded.path(e.path):
                    continue
                try:
                    isdir = e.is_dir(follow_symlinks=False)   # 符号链接本体不算目录
                except OSError:
                    continue
                if isdir:
                    try:
                        reparse = bool(e.stat(follow_symlinks=False)
                                       .st_file_attributes & _FILE_REPARSE)
                    except OSError:
                        reparse = False
                    if reparse:
                        rows.append((drive, 0, 0, name, name.lower(), 1, e.path))
                        continue              # junction 入索引但不下钻
                    stack.append(e.path)
                rows.append((drive, 0, 0, name, name.lower(), int(isdir), e.path))
                if len(rows) >= _BATCH:
                    with _wlock:            # 写事务段串行：与监控落库互斥但不互等 SQLite 锁
                        con.executemany("INSERT INTO {} VALUES(?,?,?,?,?,?,?)".format(table), rows)
                        con.commit()
                    total += len(rows)
                    rows = []
    if rows:
        with _wlock:
            con.executemany("INSERT INTO {} VALUES(?,?,?,?,?,?,?)".format(table), rows)
            con.commit()
        total += len(rows)
    return total


def _usn_monitor(v, jid, start_usn, ep):
    """管理员实时监控：READ_USN_JOURNAL 阻塞等新记录（BytesToWaitFor=1），
    增删改秒级落库并推进 USN 位标，下次开窗增量校准从这里续。"""
    h = _open_volume(v)
    if not h:
        _mon_note[v] = "无法打开卷，本卷无实时监控"
        return
    con = None
    try:
        con = sqlite3.connect(db_path(), timeout=30)
        _bounded_db(con, writer=True)
        excluded = _index_exclusions(con)
        mask = USN_WATCH_MASK
        inb = _RJ_V1.pack(start_usn, mask, 0, 0, 1, jid, 2, 2)
        buf = ctypes.create_string_buffer(1 << 20)
        br = ctypes.c_uint32(0)
        while True:
            if _stopping.is_set() or _epoch.get(v) != ep:
                return                          # 全量重建换代：旧监控自退，防新旧行混杂
            if not _kernel32.DeviceIoControl(h, FSCTL_READ_USN_JOURNAL, inb, len(inb),
                                             buf, len(buf), ctypes.byref(br), None):
                _mon_note[v] = "USN 日志中断（回卷/被清理），下次开窗全量重建"
                return
            n = br.value
            if n <= 8:
                continue                        # 空唤醒：按原位标继续阻塞等
            with _wlock:                        # 落库段串行（R47）：与重建/重扫互不踩 SQLite 锁
                moved, next_usn = _apply_usn_records(con, v, buf.raw[:n], excluded)
                con.commit()
                if next_usn:
                    _meta_set(con, "usn:" + v, next_usn)
                    con.commit()
            if next_usn:
                inb = _RJ_V1.pack(next_usn, mask, 0, 0, 1, jid, 2, 2)
            if moved:
                _DIRTY["n"] += 1
                _schedule_native_sync()
    except Exception as e:
        _mon_note[v] = "监控线程异常：" + str(e)
    finally:
        if con:
            try:
                con.close()
            except Exception:
                pass
        _kernel32.CloseHandle(h)
        with _mon_lock:
            if _monitors.get(v) is threading.current_thread():
                _monitors.pop(v, None)


def _dir_monitor(v, ep):
    """非管理员实时监控：ReadDirectoryChangesW 递归盯整卷文件/目录名变化。
    子树监听不进 junction（Vista+ 行为）；缓冲溢出丢事件则整卷重扫兜底。"""
    h = _kernel32.CreateFileW(v + "\\", FILE_LIST_DIRECTORY, 1 | 2 | 4,
                              None, _OPEN_EXISTING, 0x02000000, None)
    if not h or h == INVALID_HANDLE:
        _mon_note[v] = "无法监听卷目录，本卷无实时监控"
        return
    con = None
    buf = ctypes.create_string_buffer(1 << 20)   # 1MB：重建期间等锁积压的事件不轻易溢出丢批
    br = ctypes.c_uint32(0)
    try:
        con = sqlite3.connect(db_path(), timeout=30)
        _bounded_db(con, writer=True)
        excluded = _index_exclusions(con)
        pending_old = None
        while True:
            if _stopping.is_set() or _epoch.get(v) != ep:
                return
            br.value = 0
            if not _kernel32.ReadDirectoryChangesW(h, buf, len(buf), 1,
                                                   0x1 | 0x2, ctypes.byref(br),
                                                   None, None):
                err = ctypes.get_last_error()
                if err == _ERROR_MORE_DATA:     # 缓冲溢出：丢了一批事件，重扫兜底
                    _schedule_rewalk(v)
                    continue
                _mon_note[v] = "目录监听中断 err={}".format(err)
                return
            data = buf.raw[:br.value] if br.value else b""
            with _wlock:                        # 落库段串行：目录改名平移等长 UPDATE 不再挂死重建 worker
                off, moved = 0, 0
                while off + 12 <= len(data):
                    nxt, action, namelen = _FNI.unpack_from(data, off)
                    rel = data[off + 12: off + 12 + namelen].decode("utf-16-le", "ignore")
                    if action == FNI_REN_OLD:
                        if pending_old:         # 上一个旧名没等到配对新名：按删除收尾
                            moved += int(_apply_fni(con, v, FNI_REMOVED, pending_old, excluded=excluded))
                        pending_old = rel
                    elif action == FNI_REN_NEW:
                        moved += int(_apply_fni(con, v, action, rel, pending_old, excluded))
                        pending_old = None
                    else:
                        if pending_old:
                            moved += int(_apply_fni(con, v, FNI_REMOVED, pending_old, excluded=excluded))
                            pending_old = None
                        if action in (FNI_ADDED, FNI_REMOVED):
                            moved += int(_apply_fni(con, v, action, rel, excluded=excluded))
                    if not nxt:
                        break
                    off += nxt
                if con.in_transaction:
                    con.commit()   # 0 命中也会开隐式事务：不清就阻塞持锁，其他写者全被挂死
            if moved:
                _DIRTY["n"] += 1
                _schedule_native_sync()
    except Exception as e:
        _mon_note[v] = "监控线程异常：" + str(e)
    finally:
        if con:
            try:
                con.close()
            except Exception:
                pass
        _kernel32.CloseHandle(h)
        with _mon_lock:
            if _monitors.get(v) is threading.current_thread():
                _monitors.pop(v, None)


def _schedule_rewalk(v):
    """溢出兜底去重：同卷 5 分钟内最多重扫一次，且同卷不并发重扫；全量重建中直接跳过
    （worker 正在整卷刷新，重扫既冗余又会与换表长事务相撞把 worker 锁死）。"""
    if _indexing.is_set():
        return
    with _mon_lock:
        if time.time() - _rewalks.get(v + ":t", 0) < 300:
            return
        th = _rewalks.get(v)
        if th and th.is_alive():
            return
        th = threading.Thread(target=_rewalk_volume, args=(v,), daemon=True)
        _rewalks[v] = th
        _rewalks[v + ":t"] = time.time()
    th.start()


def _rewalk_volume(v):
    """变更洪水兜底：整卷目录重扫写临时表后原子换表（与后台索引同一套逻辑）。"""
    try:
        con = sqlite3.connect(db_path(), timeout=30)
        _bounded_db(con)
        con.execute("PRAGMA busy_timeout=30000")
        _ensure_schema(con)
        con.execute("DROP TABLE IF EXISTS temp.vol_tmp")
        con.execute("CREATE TEMP TABLE vol_tmp AS SELECT * FROM files WHERE 0")
        con.commit()
        _walk_rows(con, v, "temp.vol_tmp")
        with _wlock:                            # 换表原子段串行（同 _walk_swap）
            con.execute("DELETE FROM files WHERE v=?", (v,))
            con.execute("INSERT INTO files SELECT * FROM temp.vol_tmp")
            con.execute("DROP TABLE temp.vol_tmp")
            con.execute("DELETE FROM meta WHERE k IN (?, ?)", ("jid:" + v, "usn:" + v))
            con.commit()
        con.close()
        _DIRTY["n"] += 1
        _schedule_native_sync()
    except Exception as e:
        _mon_note[v] = "重扫失败：" + str(e)


def _start_monitor(v, mode, jid=None, usn=0):
    """每卷启动一个实时监控线程（本会话每卷只起一次；换代后旧线程自退）。"""
    with _mon_lock:
        if _stopping.is_set() or _cancel_path:
            return
        old = _monitors.get(v)
        if old and old.is_alive():
            return
        _epoch[v] = _epoch.get(v, 0) + 1
        ep = _epoch[v]
        if mode == "usn":
            th = threading.Thread(target=_usn_monitor, args=(v, jid, usn, ep), daemon=True)
        else:
            th = threading.Thread(target=_dir_monitor, args=(v, ep), daemon=True)
        _monitors[v] = th
    th.start()


class _IndexWorker(threading.Thread):
    """后台索引线程：持久化增量索引（旧库行全程可查，构建中不被破坏）。
    每卷依次：管理员先试 USN 增量校准，再试 MFT 直读全量，非管理员目录遍历换表；
    完成后启动该卷实时监控线程。"""

    def __init__(self, path, force=False):
        super().__init__(daemon=True)
        self.db = path
        self.force = force
        self.count = 0
        self.vol = ""
        self.mode = ""
        self.done = False
        self.error = None
        self.meta_err = None
        self.err_tb = None
        self.t0 = time.time()
        self.elapsed = 0.0

    def run(self):
        con = None
        _indexing.set()                             # 重建中：溢出重扫让位，长事务互不踩踏
        try:
            for suffix in ("", "-wal", "-shm"):     # 迁移清理：旧版索引库落在临时目录
                try:
                    os.remove(os.path.join(tempfile.gettempdir(),
                                           "JianJie_fsearch.db" + suffix))
                except OSError:
                    pass
            con = sqlite3.connect(self.db, timeout=30)   # 与常驻监控线程共存：写锁竞争等 30s，不再 5s 报 locked
            _bounded_db(con, writer=True)
            _ensure_schema(con)
            _purge_index_artifacts(con)
            con.commit()
            for d in fixed_drives():
                _check_running()
                self.vol = d
                self._volume(con, d)
            con.commit()
            _total = self.count
            try:                                 # 记录上次索引时间与总项数（供窗口信息条显示）
                _total = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
                _meta_set(con, "built_at", int(time.time()))
                _meta_set(con, "count", _total)
                con.commit()
            except Exception as _me:                 # meta 失败不致命，但必须可见（不再静默吞）
                self.meta_err = "{}: {}".format(type(_me).__name__, _me)
            self.vol = ""
            self.mode = "快速搜索索引"

            def native_progress(count):
                self.count = count

            N.build(self.db, force=self.force, cancelled=_native_cancelled, progress=native_progress)
            self.count = _total
        except Exception as e:                       # 后台线程兜底：状态可见不闪退
            import traceback as _tb
            self.error = str(e)
            self.err_tb = _tb.format_exc()
        finally:
            _indexing.clear()
            if con is not None:                      # 异常路径也要还连接，不泄漏
                try:
                    con.close()
                except Exception:
                    pass
        self.done = True
        self.elapsed = time.time() - self.t0
        if not self.error:
            _schedule_native_sync(self.db)

    # ---- 每卷策略：新鲜监控接管 → USN 增量校准 → MFT 直读全量 → 目录遍历换表 ----
    def _volume(self, con, drive):
        repair_usn = _usn_index_needs_repair(con, drive)
        if not self.force and _index_fresh(con, drive):
            # 索引新鲜：跳全量遍历/直读，按原模式直接起监控（重启应用秒就绪）
            self.mode = "监控接管"
            h = _open_volume(drive)
            j = _query_journal(h) if h else None
            if j and _meta_get(con, "jid:" + drive) == str(j[0]):
                try:
                    _u = int(_meta_get(con, "usn:" + drive) or 0)
                except ValueError:
                    _u = 0
                _start_monitor(drive, "usn", j[0], _u)
            else:
                _start_monitor(drive, "dir")
            if h:
                _kernel32.CloseHandle(h)
            return
        h = _open_volume(drive)
        j = _query_journal(h) if h else None
        if j and not self.force and not repair_usn:
            jid, _next = j
            stored = _meta_get(con, "jid:" + drive)
            if stored == str(jid):
                try:
                    start = int(_meta_get(con, "usn:" + drive) or 0)
                except ValueError:
                    start = 0
                self.mode = "USN 增量"
                if self._catchup(con, drive, h, jid, start):
                    _kernel32.CloseHandle(h)     # 校准成功路径原漏关卷句柄（每窗每卷漏一个）
                    _start_monitor(drive, "usn", jid,
                                   int(_meta_get(con, "usn:" + drive) or 0))
                    return
        usn_ok, j2 = False, None
        if h:
            try:
                self.mode = "NTFS 直读"
                con.execute("DELETE FROM files WHERE v=?", (drive,))
                con.commit()
                self._usn(con, drive, h)
                usn_ok = True
                j2 = _query_journal(h)
            except OSError:
                con.execute("DELETE FROM files WHERE v=?", (drive,))  # 直读半途而废的残行
                con.commit()
            finally:
                _kernel32.CloseHandle(h)
        if usn_ok:
            _meta_set(con, "usn_reason_version:" + drive, _USN_REASON_VERSION)
            if j2:
                _meta_set(con, "jid:" + drive, j2[0])
                _meta_set(con, "usn:" + drive, j2[1])
                con.commit()
                _start_monitor(drive, "usn", j2[0], j2[1])
            else:
                con.commit()
                _mon_note[drive] = "USN 日志未激活，本卷无实时监控"
            return
        self.mode = "目录遍历"
        self._walk_swap(con, drive)
        _meta_set(con, "usn_reason_version:" + drive, _USN_REASON_VERSION)
        con.commit()
        _start_monitor(drive, "dir")

    def _catchup(self, con, drive, h, jid, start_usn):
        """从上次 NextUsn 起追 USN 日志，把错过的增删改落库；成功 True。
        日志回卷/被清理（读失败）→ False，外层走全量直读重建。"""
        try:
            total = 0
            for _ in range(2000):                    # 一次追平的防御上限（百万级变更）
                _check_running()
                inb = _RJ_V1.pack(start_usn, USN_WATCH_MASK, 0, 0, 0, jid, 2, 2)
                buf = ctypes.create_string_buffer(1 << 20)
                br = ctypes.c_uint32(0)
                if not _kernel32.DeviceIoControl(h, FSCTL_READ_USN_JOURNAL, inb,
                                                 len(inb), buf, len(buf),
                                                 ctypes.byref(br), None):
                    return False
                n = br.value
                if n <= 8:
                    break                            # 追平：日志里没有更多新记录
                with _wlock:                    # 落库段串行（R47）：上会话遗留监控与本 worker 互不踩锁
                    moved, next_usn = _apply_usn_records(con, drive, buf.raw[:n])
                    con.commit()
                    total += moved
                    if next_usn:
                        _meta_set(con, "usn:" + drive, next_usn)
                        con.commit()
                if next_usn:
                    start_usn = next_usn
            self.count += total
            return True
        except Exception:
            return False

    def _walk_swap(self, con, drive):
        """非管理员：旧索引行保留可查，遍历写临时表后与主表原子换位。"""
        con.execute("DROP TABLE IF EXISTS temp.vol_tmp")
        con.execute("CREATE TEMP TABLE vol_tmp AS SELECT * FROM files WHERE 0")
        con.commit()
        self.count += _walk_rows(con, drive, "temp.vol_tmp")
        with _wlock:                            # 换表原子段：应用级串行，SQLite 锁只兑跨进程
            con.execute("DELETE FROM files WHERE v=?", (drive,))
            con.execute("INSERT INTO files SELECT * FROM temp.vol_tmp")
            con.execute("DROP TABLE temp.vol_tmp")
            con.execute("DELETE FROM meta WHERE k IN (?, ?)", ("jid:" + drive, "usn:" + drive))
            con.commit()

    # ---- 路径 1：NTFS 直读（卷句柄由 _volume 统一开关）----
    def _usn(self, con, drive, h):
        excluded = _index_exclusions(con)
        buf = ctypes.create_string_buffer(1 << 20)
        br = ctypes.c_uint32(0)
        med = struct.pack("<QQQ", 0, 0, _HIGH_USN)
        rows = []
        while True:
            _check_running()
            if not _kernel32.DeviceIoControl(h, FSCTL_ENUM_USN_DATA, med, len(med),
                                             buf, len(buf), ctypes.byref(br), None):
                err = ctypes.get_last_error()
                if err == ERROR_HANDLE_EOF:
                    break
                raise OSError("FSCTL 失败 err={}".format(err))
            n = br.value
            if n <= 8:
                break
            med = struct.pack("<QQQ", struct.unpack_from("<Q", buf)[0], 0, _HIGH_USN)
            off, end = 8, n
            while off + 60 <= end:
                r = parse_usn_record(buf, off)
                if r is None:
                    break
                frn, parent, name, isdir, reclen = r
                off += reclen
                if not name or name[0] == "$":
                    continue                    # MFT 元文件/回收站等系统项
                if excluded.usn(drive, parent, name):
                    continue
                rows.append((drive, frn, parent, name, name.lower(), int(isdir), None))
                if len(rows) >= _BATCH:
                    with _wlock:
                        con.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?)", rows)
                        con.commit()
                    self.count += len(rows)
                    rows = []
        if rows:
            with _wlock:
                con.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?)", rows)
                con.commit()
            self.count += len(rows)


def _index_status(path, worker):
    state = {"count": worker.count, "vol": worker.vol, "mode": worker.mode,
             "done": worker.done, "error": worker.error,
             "elapsed": worker.elapsed, "meta_err": worker.meta_err}
    pending = path + ".tmp"
    with open(pending, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False)
    os.replace(pending, path)


def run_index_process(db, status_path, force=False):
    """One-shot EXE mode: release the full-scan heap when this process exits."""
    global _cancel_path
    _cancel_path = status_path + ".cancel"
    worker = _IndexWorker(db, force=force)
    worker.start()
    while worker.is_alive():
        worker.join(0.4)
        _index_status(status_path, worker)
    _index_status(status_path, worker)
    return 1 if worker.error else 0


def _resume_monitors(db):
    con = sqlite3.connect(db, timeout=30)
    try:
        _bounded_db(con, writer=True)
        _purge_index_artifacts(con)
        con.commit()
        for drive in fixed_drives():
            h = _open_volume(drive)
            try:
                journal = _query_journal(h) if h else None
                if journal and _meta_get(con, "jid:" + drive) == str(journal[0]):
                    try:
                        usn = int(_meta_get(con, "usn:" + drive) or 0)
                    except ValueError:
                        usn = 0
                    _start_monitor(drive, "usn", journal[0], usn)
                else:
                    _start_monitor(drive, "dir")
            finally:
                if h:
                    _kernel32.CloseHandle(h)
    finally:
        con.close()

    _schedule_native_sync(db)


class _ExternalIndexWorker:
    """UI-facing snapshot of an indexer running in a short-lived EXE process."""

    def __init__(self, db, force=False):
        self.db = db
        self.count = 0
        self.vol = ""
        self.mode = ""
        self.done = False
        self.error = None
        self.meta_err = None
        self.elapsed = 0.0
        self._finalized = False
        self._refresh_lock = threading.Lock()
        self._status_dir = tempfile.mkdtemp(prefix="JianJieIndex_")
        self._status = os.path.join(self._status_dir, "status.json")
        try:
            child_env = os.environ.copy()
            child_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            self._process = subprocess.Popen(
                [sys.executable, "--fsearch-build", db, self._status, "1" if force else "0"],
                creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                               getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)), env=child_env)
        except Exception:
            os.rmdir(self._status_dir)
            raise

    def refresh(self):
        # 启动预热 watcher 与搜索窗口轮询可并发，完成接管/状态清理只执行一次。
        with self._refresh_lock:
            self._refresh_state()

    def _refresh_state(self):
        if self._finalized:
            return
        try:
            with open(self._status, "r", encoding="utf-8") as stream:
                state = json.load(stream)
            for key in ("count", "vol", "mode", "error", "elapsed", "meta_err"):
                setattr(self, key, state.get(key, getattr(self, key)))
        except (OSError, ValueError):
            state = {}
        code = self._process.poll()
        if code is None:
            return
        self.done = True
        if not state.get("done") and not self.error:
            self.error = "索引进程未正常完成"
        if code and not self.error:
            self.error = "索引进程退出码 {}".format(code)
        if not self.error and not _stopping.is_set():
            try:
                _resume_monitors(self.db)
            except Exception as exc:
                self.error = "索引已写入，但实时监控启动失败：{}".format(exc)
        for path in (self._status, self._status + ".tmp", self._status + ".cancel"):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(self._status_dir)
        except OSError:
            pass
        self._finalized = True

    def is_alive(self):
        self.refresh()
        return not self.done

    def join(self, timeout=None):
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return
        self.refresh()


# 搜索窗口精修样式：结果条去常驻底框（悬停轻高亮/选中圆角蓝），右键菜单圆角+悬停态；
# 搜索框加重（圆角描边/聚焦变蓝），重新索引改主色按钮（禁用态可见），索引进度为 6px 不定态滑块
FSEARCH_QSS = """   /* R146 mac 化：黑透无描边判例收敛（reidx 蓝边退役撑底色、srch 灰边语义深色版） */
QListWidget{outline:0;}
QListWidget::item{background:transparent;border-radius:9px;margin:0 4px;padding:0;}
QListWidget::item:hover{background:rgba(255,255,255,26);}
QListWidget::item:selected{background:rgba(59,130,246,105);}
QMenu{background:#20232f;border:1px solid rgba(255,255,255,32);border-radius:10px;padding:6px;}
QMenu::item{background:transparent;color:#eef1f7;padding:8px 28px 8px 14px;
border-radius:7px;font-size:12.5px;}
QMenu::item:selected{background:#3b82f6;color:#ffffff;}
QMenu::item:disabled{color:#6b7280;}
QLineEdit#srch{background:rgba(255,255,255,8);border:1px solid rgba(255,255,255,28);
border-radius:10px;padding:9px 14px;font-size:13.5px;}
QLineEdit#srch:focus{border:1px solid rgba(96,165,250,160);}
QPushButton#reidx{background:rgba(59,130,246,48);color:#a9c8ff;
border:none;border-radius:6px;padding:8px 16px;font-size:12.5px;font-weight:600;}
QPushButton#reidx:hover{background:rgba(59,130,246,80);color:#dbeafe;}
QPushButton#reidx:pressed{background:rgba(59,130,246,110);}
QPushButton#reidx:disabled{background:rgba(255,255,255,10);color:#6b7280;}
QProgressBar{background:rgba(255,255,255,14);border:none;border-radius:3px;max-height:6px;}
QProgressBar::chunk{background:#3b82f6;border-radius:3px;width:140px;}
"""


_NAME_ROLE = int(Qt.UserRole) + 1


class _SearchResultDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self._tokens = ()
        self._matches = None

    def set_tokens(self, tokens):
        if tokens != self._tokens:
            self._tokens = tokens
            self._matches = re.compile("|".join(re.escape(token) for token in tokens), re.IGNORECASE) if tokens else None
            self.parent().viewport().update()

    @staticmethod
    def _fonts(option):
        name_font = QFont(option.font)
        name_font.setPixelSize(13)
        name_font.setWeight(QFont.Normal)
        path_font = QFont(name_font)
        path_font.setPixelSize(11)
        return name_font, path_font

    def sizeHint(self, option, index):
        if index.data(_NAME_ROLE) is None:
            return QSize(0, 36)
        name_font, path_font = self._fonts(option)
        return QSize(0, max(48, QFontMetrics(name_font).height() + QFontMetrics(path_font).height() + 16))

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.widget.style().drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)
        painter.save()
        painter.setClipRect(option.rect)
        if not option.state & QStyle.State_Enabled:
            painter.setOpacity(0.5)
        rect = option.rect.adjusted(16, 6, -16, -6)
        name = index.data(_NAME_ROLE)
        name_font, path_font = self._fonts(option)
        if name is None:
            painter.setFont(name_font)
            painter.setPen(QColor("#9ca3af"))
            painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, index.data(Qt.DisplayRole) or "")
            painter.restore()
            return
        name_metrics = QFontMetrics(name_font)
        display_name = name_metrics.elidedText(name, Qt.ElideRight, max(1, rect.width()))
        layout = QTextLayout(display_name, name_font)
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.NoWrap)
        layout.setTextOption(text_option)
        formats = []
        if self._matches is not None:
            for match in self._matches.finditer(display_name):
                span = QTextLayout.FormatRange()
                span.start = len(display_name[:match.start()].encode("utf-16-le")) // 2
                span.length = len(match.group().encode("utf-16-le")) // 2
                span.format = QTextCharFormat()
                span.format.setForeground(QColor("#60a5fa"))
                formats.append(span)
        layout.setFormats(formats)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(max(1, rect.width()))
        layout.endLayout()
        foreground = index.data(Qt.ForegroundRole)
        painter.setPen(foreground.color() if foreground is not None else QColor("#eef1f7"))
        layout.draw(painter, QPointF(rect.left(), rect.top()))
        path_rect = rect.adjusted(0, name_metrics.height() + 3, 0, 0)
        painter.setFont(path_font)
        painter.setPen(QColor("#9ca3af"))
        path = QFontMetrics(path_font).elidedText(index.data(Qt.UserRole) or "", Qt.ElideMiddle, max(1, path_rect.width()))
        painter.drawText(path_rect, Qt.AlignLeft | Qt.AlignTop, path)
        if index.row() < index.model().rowCount(index.parent()) - 1:
            painter.setPen(QColor(255, 255, 255, 14))
            y = option.rect.bottom()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.restore()


class _QueryCancelled(Exception):
    pass


def _search_tokens(text):
    # 去重并先匹配长词；大小写、空格和词序变化不重复查询。
    return tuple(sorted(set(text.lower().split()), key=lambda token: (-len(token), token)))


def _check_query(cancelled):
    if cancelled.is_set():
        raise _QueryCancelled()


class _LatestQueryWorker:
    """单条执行、单条待办；空闲后退出线程，连接只活在当前查询中。"""

    def __init__(self, query, deliver):
        self._query = query
        self._deliver = deliver
        self._cv = threading.Condition()
        self._thread = None
        self._pending = None
        self._active = None
        self._closed = False

    @property
    def busy(self):
        with self._cv:
            return self._active is not None or self._pending is not None

    def _cancel_locked(self):
        if self._active is not None:
            self._active.set()
        if self._pending is not None:
            self._pending[2].set()
            self._pending = None

    def cancel(self):
        with self._cv:
            self._cancel_locked()
            self._cv.notify_all()

    def stop(self, _object=None):
        with self._cv:
            self._closed = True
            self._cancel_locked()
            self._cv.notify_all()

    def submit(self, gen, tokens):
        with self._cv:
            if self._closed:
                return
            self._cancel_locked()
            self._pending = (gen, tokens, threading.Event())
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="jianjie-file-query", daemon=True)
                self._thread.start()
            self._cv.notify_all()

    def _run(self):
        while True:
            with self._cv:
                if not self._cv.wait_for(lambda: self._closed or self._pending is not None, timeout=0.75):
                    self._thread = None
                    return
                if self._closed:
                    self._thread = None
                    return
                gen, tokens, cancelled = self._pending
                self._pending = None
                self._active = cancelled
            result = None
            try:
                result = self._query(tokens, cancelled)
            except _QueryCancelled:
                pass
            except Exception:
                if not cancelled.is_set():
                    result = ([], False)
            finally:
                with self._cv:
                    self._active = None
                    deliver = result is not None and not cancelled.is_set() and not self._closed
            if deliver:
                try:
                    self._deliver(gen, *result)
                except RuntimeError:
                    # 窗口可能在 SQL 返回后销毁，不能再向已删除的 Qt 对象发信号。
                    self.stop()


class FileSearchWindow(QWidget):
    """Everything 式文件搜索窗口：输入即搜、双击打开、右键所在文件夹/复制路径。
    索引持久化 + 实时监控：窗口关闭后监控线程仍随应用常驻，重开秒级增量校准。"""

    _built = False                          # 本次会话是否已建过全盘索引（重复开窗不重建）
    sig_hits = Signal(int, list, bool)      # 后台查询 → 主线程渲染（代次, 结果行, 截断）

    def __init__(self, auto_index=True, db=None):
        super().__init__(None)
        self.setWindowTitle("{} · 文件搜索".format(C.APP_NAME))
        self.resize(700, 540)
        # R146 mac 化：统一黑透插件壳（标题栏/钉×/拖动/Esc/八向缩放）
        root, _sub = W._plugin_shell(self, "文件搜索", min_size=(420, 320))
        self.setStyleSheet(W.PLUGIN_QSS + FSEARCH_QSS)   # 追加结果列表/右键菜单精修
        self.db = db or db_path()
        self._worker = None
        self._seen_dirty = _DIRTY["n"]              # 从开窗当前值起算：只追开窗后的新变动（不补放历史，首轮不多跑）
        self._last_replay = 0.0                 # 监控落库重放节流：距上次 ≥1.5s 才再放（R77 重放风暴）
        self._query_closed = False
        self._query_tokens = ()
        self._pending_result = None
        self._hits = None
        self._hits_truncated = False
        self._queries = _LatestQueryWorker(self._query_db, self.sig_hits.emit)
        self.destroyed.connect(self._queries.stop)

        root.setSpacing(8)

        top = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setObjectName("srch")
        self.edit.setPlaceholderText("搜索文件或文件夹（空格分隔多个关键词）")
        self.edit.setClearButtonEnabled(True)   # 框内一键清除（点击触发 textChanged 走正常重查）
        self.edit.textChanged.connect(self._on_edit)
        top.addWidget(self.edit, 1)
        self.refresh = QPushButton("重新索引")
        self.refresh.setObjectName("reidx")
        self.refresh.setCursor(Qt.PointingHandCursor)
        self.refresh.clicked.connect(lambda: self.reindex(force=True))
        top.addWidget(self.refresh)
        root.addLayout(top)

        self.bar = QProgressBar()               # 全量总数未知 → 不定态滑块，完成即隐藏
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        self.bar.hide()
        root.addWidget(self.bar)

        self.status = QLabel("")
        footer_style = "color:#6f7688;font-size:11px;background:transparent;border:none;"
        footer_policy = QSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        footer_policy.setHeightForWidth(True)
        self.status.setStyleSheet(footer_style)
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status.setSizePolicy(footer_policy)
        self.status.setMinimumHeight(16)

        self.listw = QListWidget()
        self._result_delegate = _SearchResultDelegate(self.listw)
        self.listw.setItemDelegate(self._result_delegate)
        self.listw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 路径两行显示/中间省略，不左右滚
        self.listw.itemDoubleClicked.connect(self._open_item)
        self.listw.setContextMenuPolicy(Qt.CustomContextMenu)
        self.listw.customContextMenuRequested.connect(self._ctx_menu)
        root.addWidget(self.listw, 1)

        self._list_hint = QLabel(self.listw.viewport())
        self._list_hint.setStyleSheet("color:#9ca3af;font-size:13px;background:transparent;border:none;")
        self._list_hint.setAlignment(Qt.AlignCenter)
        self._list_hint.setWordWrap(True)
        self._list_hint.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        hint_layout = QVBoxLayout(self.listw.viewport())
        hint_layout.setContentsMargins(24, 20, 24, 20)
        hint_layout.addWidget(self._list_hint)
        self._list_hint.hide()

        self.meta_lbl = QLabel("")
        self.meta_lbl.setStyleSheet(footer_style)
        self.meta_lbl.setWordWrap(True)
        self.meta_lbl.setSizePolicy(footer_policy)
        self.meta_lbl.setMinimumHeight(16)
        footer_widget = QWidget()
        footer_widget.setMinimumHeight(16)
        footer = QHBoxLayout(footer_widget)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(16)
        footer.addWidget(self.meta_lbl, 1)
        footer.addWidget(self.status, 1)
        root.addWidget(footer_widget)

        self._auto_index = auto_index
        self._qgen = 0                        # 查询代次：新查询发出后旧结果作废
        self.sig_hits.connect(self._show_hits)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(160)
        self._debounce.timeout.connect(self._run_query)
        self._layout_debounce = QTimer(self)
        self._layout_debounce.setSingleShot(True)
        self._layout_debounce.setInterval(40)
        self._layout_debounce.timeout.connect(self._resize_hits)
        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._poll_worker)
        self._poll.start()
        # 构造零磁盘：窗口立即显示；索引状态/首轮查询排到下一拍（点击秒见窗，不再「未响应」）
        QTimer.singleShot(0, self._post_show)

    def _post_show(self):
        if self._query_closed:
            return
        self._refresh_meta()
        if self._auto_index:
            self.reindex()
            if FileSearchWindow._built:
                alive = any(t.is_alive() for t in _monitors.values())
                self.status.setText("索引就绪 · 实时监控中（新文件/改名自动入索引）" if alive
                                    else "索引就绪 · 无实时监控（点「重新索引」恢复）")
        self._run_query()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if getattr(self, "_hits", None) is not None:
            self._layout_debounce.start()       # 只重绘文本，不读索引库

    def hideEvent(self, ev):
        self._qgen += 1
        self._pending_result = None
        self._queries.cancel()
        self._debounce.stop()
        self._layout_debounce.stop()
        self._poll.stop()
        self._hits = None
        self.listw.clear()
        super().hideEvent(ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        if hasattr(self, "_poll") and not self._query_closed:
            self._poll.start()
            if getattr(self, "_shown_once", False):
                QTimer.singleShot(0, self._run_query)
            self._shown_once = True

    def closeEvent(self, ev):
        self._query_closed = True
        self._queries.stop()
        super().closeEvent(ev)

    # ---- 索引 ----
    def reindex(self, force=False):
        if self._worker and self._worker.is_alive():
            return
        if FileSearchWindow._built and not force:
            return                          # 会话内已建过索引：直接查旧库，「重新索引」按钮才重建
        self._worker = ensure_worker(force=force, db=self.db)   # 预热单例复用：开窗不重复建
        if self._worker is None:
            return
        self._start_ui()
        self.status.setText("正在全量重建…" if force else "正在校准增量…")

    def _start_ui(self):
        """索引进行中的 UI 态：进度条滑块 + 按钮禁用改名（reindex 与轮询双入口共用）。"""
        self.bar.show()
        self.refresh.setEnabled(False)
        self.refresh.setText("索引中…")

    def _poll_worker(self):
        if _DIRTY["n"] != self._seen_dirty:      # 实时监控有落库：静默重放当前查询
            now = time.time()
            # R77 双闸门防重放风暴：C 盘变动极频繁，300ms 一拍重放→查询代次永远作废→
            # 结果页↔「搜索中…」来回跳。闸门不过就不同步基线，下一拍再试（最多迟 1.5s）。
            if (self._worker is None or self._worker.done) and self.edit.text().strip() \
                    and not self._queries.busy and self._pending_result is None \
                    and not self._debounce.isActive() \
                    and now - self._last_replay >= 1.5:
                self._seen_dirty = _DIRTY["n"]
                self._last_replay = now
                self._run_query(silent=True)   # 静默：保留当前结果页，回来直接替换，不闪 loading
                self.status.setText("实时监控中 · 新文件/改名已入索引")
        wk = self._worker
        if wk is None:
            return
        if isinstance(wk, _ExternalIndexWorker):
            wk.refresh()
        if wk.error:
            self.status.setText("索引失败：" + wk.error)
            self._finish_ui()
            self._worker = None
            return
        if not wk.done:
            self._start_ui()
            self.status.setText("正在索引 {}（{}）· 已 {:,} 项".format(
                wk.vol or "磁盘", wk.mode or "…", wk.count))
            return
        FileSearchWindow._built = True
        alive = any(t.is_alive() for t in _monitors.values())
        self.status.setText("索引完成：共 {:,} 项 · {:.1f}s · {} · {}".format(
            wk.count, wk.elapsed, wk.mode, "实时监控中" if alive else "无实时监控"))
        self._finish_ui()
        self._refresh_meta()
        self._worker = None
        self._run_query()

    def _finish_ui(self):
        self.bar.hide()
        self.refresh.setEnabled(True)
        self.refresh.setText("重新索引")

    def _refresh_meta(self):
        """底部信息条：上次索引时间与总项数（meta 表，开窗零查询成本）。"""
        try:
            con = sqlite3.connect(self.db)
            _bounded_db(con)
            try:
                built = _meta_get(con, "built_at")
                cnt = _meta_get(con, "count")
            finally:
                con.close()
        except sqlite3.Error:
            built = cnt = None
        if not built:
            self.meta_lbl.setText("尚未建立索引 · 点右上「重新索引」建立全盘索引")
            return
        txt = "上次索引 " + time.strftime("%Y-%m-%d %H:%M", time.localtime(float(built)))
        try:
            txt = "共 {:,} 项 · ".format(int(cnt)) + txt
        except (TypeError, ValueError):
            pass
        self.meta_lbl.setText(txt)

    # ---- 搜索 ----
    def _set_list_hint(self, text):
        self._list_hint.setText(text)
        self._list_hint.setVisible(bool(text))

    def _on_edit(self, text):
        tokens = _search_tokens(text)
        if tokens == self._query_tokens:
            return
        self._query_tokens = tokens
        self._qgen += 1                       # 按键当下作废，不能等 160ms 后才取消
        self._pending_result = None
        self._queries.cancel()
        self.listw.setEnabled(not tokens)     # 保留旧结果时不能误打开上一组文件
        if tokens:
            self._debounce.start()
        else:
            self._debounce.stop()
            self._run_query()

    def _run_query(self, silent=False):
        """空词同步提示；非空词交给单条查询调度，已有结果保留到新结果返回。"""
        if self._query_closed:
            return
        tokens = _search_tokens(self.edit.text())
        self._query_tokens = tokens
        self._qgen += 1
        self._pending_result = None
        self._queries.cancel()
        if not tokens:
            self._hits = None
            self.listw.setEnabled(True)
            self.listw.clear()
            self._set_list_hint("输入关键词即时搜索；双击打开，右键更多操作")
            return
        if not os.path.exists(self.db):
            self.listw.setEnabled(True)
            self._set_list_hint("索引尚未就绪")
            return                                  # 索引未建好，状态栏已有提示
        if not silent:
            self.listw.setEnabled(False)
        if not silent and self._hits is None:
            self.listw.clear()
            self._set_list_hint("搜索中…")
        self._pending_result = self._qgen
        self._queries.submit(self._qgen, tokens)

    def _query_db(self, tokens, cancelled=None):
        """C++ owns candidate lookup, exact matching and bounded parent-path resolution."""
        cancelled = cancelled if cancelled is not None else threading.Event()
        _check_query(cancelled)
        try:
            return N.query(self.db, tokens, cancelled, limit=_RESULT_CAP)
        except N.Cancelled:
            raise _QueryCancelled() from None

    def _show_hits(self, gen, rows, truncated):
        """主线程渲染：代次不符＝旧查询结果，直接丢弃（连击打字不被过期结果覆盖）。"""
        if gen == self._pending_result:
            self._pending_result = None
        if self._query_closed or gen != self._qgen:
            return
        self._result_delegate.set_tokens(self._query_tokens)
        self._set_list_hint("" if rows else "没有找到匹配的文件或文件夹")
        if rows == self._hits and truncated == self._hits_truncated:
            self.listw.setEnabled(True)
            return                             # 监控重放无变化：保留选中项和滚动位置
        selected = self.listw.currentItem()
        selected_path = selected.data(Qt.UserRole) if selected is not None else None
        scroll = self.listw.verticalScrollBar().value()
        self._hits = rows
        self._hits_truncated = truncated
        self.listw.setUpdatesEnabled(False)
        try:
            self.listw.clear()
            for name, isdir, p in rows:
                it = QListWidgetItem("{}\n{}".format(name, p))
                if isdir:
                    it.setForeground(QColor("#93c5fd"))
                it.setData(Qt.UserRole, p)
                it.setData(_NAME_ROLE, name)
                it.setToolTip(p)
                self.listw.addItem(it)
                if p == selected_path:
                    self.listw.setCurrentItem(it)
            if truncated:
                tail = QListWidgetItem("…结果过多，仅显示前 {} 条，请细化关键词".format(_RESULT_CAP))
                tail.setFlags(Qt.NoItemFlags)
                self.listw.addItem(tail)
            if selected_path is not None:
                self.listw.verticalScrollBar().setValue(scroll)
        finally:
            self.listw.setEnabled(True)
            self.listw.setUpdatesEnabled(True)

    def _resize_hits(self):
        if self._hits is None:
            return
        self.listw.viewport().update()

    def _resolve(self, con, v, frn, memo, cancelled=None):
        """USN 父链回溯完整路径；memo 缓存「该 frn 到根的完整后缀」。"""
        parts = []
        visited = []
        cur = frn
        while cur and cur != 5 and len(visited) < 64:
            if cancelled is not None:
                _check_query(cancelled)
            key = (v, cur)
            cached = memo.get(key)
            if cached is not None:
                parts.append(cached)
                break
            row = con.execute("SELECT parent, name FROM files WHERE v=? AND frn=? AND frn>0 LIMIT 1",
                              (v, cur)).fetchone()
            if row is None:
                break
            parent, name = row
            parts.append(name)
            visited.append((key, name))
            cur = parent
        for i, (key, _name) in enumerate(visited):  # 每个途经点都缓存到根的后缀
            memo[key] = "\\".join(reversed(parts[i:]))
        return v + "\\" + "\\".join(reversed(parts))

    # ---- 打开 / 右键 ----
    def _open_item(self, item):
        p = item.data(Qt.UserRole)
        if p:
            _startfile(p)

    def _ctx_menu(self, pos):
        it = self.listw.itemAt(pos)
        if it is None or it.data(Qt.UserRole) is None:
            return
        self.listw.setCurrentItem(it)      # 右键即选中：指哪亮哪（圆角高亮样式）
        p = it.data(Qt.UserRole)
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)   # 圆角菜单不露方角
        a1 = menu.addAction("打开")
        a2 = menu.addAction("打开所在文件夹")
        a3 = menu.addAction("复制路径")
        act = menu.exec(self.listw.mapToGlobal(pos))
        if act == a1:
            _startfile(p)
        elif act == a2:
            _reveal(p)
        elif act == a3:
            QGuiApplication.clipboard().setText(p)

    # ---- 冒烟用：同步种入假索引 ----
    def _seed(self, rows):
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE IF NOT EXISTS files(v TEXT, frn INTEGER, parent INTEGER,"
                    " name TEXT, lname TEXT, isdir INT, path TEXT)")
        con.execute("DELETE FROM files")
        con.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        self._run_query()


# ---------------- 启动预热：托盘常驻红利，开窗前索引已就绪 ----------------
_prewarm_worker = None        # 模块级 worker 单例（预热与窗口共享：开窗不重复建）


def ensure_worker(force=False, db=None):
    """索引 worker 单例：在跑直接复用；会话内已建（_built）且不 force 不再新建。"""
    global _prewarm_worker
    if _stopping.is_set():
        return None
    w = _prewarm_worker
    if w and w.is_alive():
        return w
    if w and w.done and not w.error:
        FileSearchWindow._built = True
    if FileSearchWindow._built and not force:
        return None
    path = db or db_path()
    external = bool(getattr(sys, "frozen", False) and "--smoke" not in sys.argv
                    and not any(t.is_alive() for t in _monitors.values()))
    if external and not force and os.path.exists(path):
        try:
            con = sqlite3.connect(path)
            try:
                external = not _index_fresh(con) or not N.ready(path)
            finally:
                con.close()
        except sqlite3.Error:
            pass
    if external:
        try:
            _prewarm_worker = _ExternalIndexWorker(path, force=force)
            return _prewarm_worker
        except OSError:
            pass                              # 子进程启动失败则保留原位索引能力
    _prewarm_worker = _IndexWorker(path, force=force)
    _prewarm_worker.start()
    return _prewarm_worker


def prewarm():
    """应用启动预热：索引新鲜 → worker 逐卷监控接管秒完；陈旧/缺失 → 后台静默全量。
    watcher 完成置 FileSearchWindow._built——之后开窗不再触发任何索引，秒开直查。"""
    try:
        w = ensure_worker()
        if w is None:
            return
        threading.Thread(target=_prewarm_watch, args=(w,), daemon=True).start()
    except Exception:
        pass


def _prewarm_watch(w):
    w.join()
    if w is _prewarm_worker and w.done and not w.error:
        FileSearchWindow._built = True


def shutdown(timeout=5):
    """协作停止扫描和阻塞监听，等待连接释放；不终止进程或线程。"""
    _stopping.set()
    worker = _prewarm_worker
    if isinstance(worker, _ExternalIndexWorker) and worker._process.poll() is None:
        with open(worker._status + ".cancel", "w", encoding="utf-8") as stream:
            stream.write("stop")
    threads = list(_monitors.values()) + [t for t in _rewalks.values() if isinstance(t, threading.Thread)]
    with _native_lock:
        if _native_thread is not None:
            threads.append(_native_thread)
    _kernel32.OpenThread.restype = ctypes.c_void_p
    _kernel32.OpenThread.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    _kernel32.CancelSynchronousIo.argtypes = [ctypes.c_void_p]
    for thread in threads:
        if thread.is_alive() and thread.native_id:
            handle = _kernel32.OpenThread(0x0001, False, thread.native_id)
            if handle:
                _kernel32.CancelSynchronousIo(handle)
                _kernel32.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    if worker is not None:
        worker.join(max(0, deadline - time.monotonic()))
    for thread in threads:
        thread.join(max(0, deadline - time.monotonic()))
    return not any(t.is_alive() for t in threads) and not (worker and worker.is_alive())
