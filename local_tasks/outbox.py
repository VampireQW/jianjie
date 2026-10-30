"""Durable, account-local MCP outbox. Only the authenticated GUI applies tasks."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from .core import TaskError, check_keys, request_digest, today, validate_write
from .paths import home


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


class Outbox:
    def __init__(self, path=None):
        self.path = Path(path) if path else home() / "mcp-outbox.sqlite3"

    @contextmanager
    def database(self):
        conn = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=3)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
                digest TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT,
                submitted_day TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                created REAL NOT NULL, updated REAL NOT NULL, result TEXT, error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, uncertain INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, lease TEXT, lease_until REAL NOT NULL DEFAULT 0)""")
            conn.execute("CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state,seq)")
            with conn:
                yield conn
        except (OSError, sqlite3.Error):
            raise TaskError("待提交队列无法保存或读取，请检查应用数据目录和磁盘空间", "queue_storage_error") from None
        finally:
            if conn is not None:
                conn.close()

    def enqueue(self, name, arguments):
        validate_write(name, arguments)
        payload = encoded(arguments)
        if len(payload.encode("utf-8")) > 120000:
            raise TaskError("单次请求过大，请拆成较小批次")
        rid, digest = arguments["request_id"], request_digest(name, arguments)
        now = time.time()
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest FROM jobs WHERE request_id=?", (rid,)).fetchone()
            if row:
                if row["digest"] != digest:
                    raise TaskError("同一 request_id 已用于其他内容", "request_id_reused")
                return rid
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('pending','delivering','conflict','failed')").fetchone()[0]
            if count >= 256:
                raise TaskError("待处理请求已达 256 条，请先处理队列中的请求", "queue_full")
            db.execute("""INSERT INTO jobs(request_id,digest,tool,arguments,submitted_day,created,updated)
                          VALUES(?,?,?,?,?,?,?)""", (rid, digest, name, payload, today(), now, now))
        return rid

    def counts(self):
        values = {state: 0 for state in ("pending", "delivering", "conflict", "failed", "applied", "cancelled")}
        if self.path.exists():
            with self.database() as db:
                values.update({row[0]: row[1] for row in db.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state")})
        return values

    @staticmethod
    def public(row, full=False):
        value = {key: row[key] for key in ("request_id", "tool", "state", "submitted_day", "created", "updated", "attempts")}
        value["delivery_uncertain"] = bool(row["uncertain"])
        if row["error"]:
            value["last_error"] = json.loads(row["error"])
        if full:
            for key in ("arguments", "result"):
                value[key] = json.loads(row[key]) if row[key] else None
        return value

    def get(self, rid):
        with self.database() as db:
            row = db.execute("SELECT * FROM jobs WHERE request_id=?", (rid,)).fetchone()
        if row is None:
            raise TaskError("找不到此待提交请求", "not_found")
        return self.public(row, True)

    def claim(self, force=False):
        if not self.path.exists():
            return None
        now = time.time()
        with self.database() as db:
            db.execute("BEGIN IMMEDIATE")
            # Across all Agent processes and the GUI there is only one delivery.
            row = db.execute("SELECT * FROM jobs WHERE state IN ('pending','delivering') ORDER BY seq LIMIT 1").fetchone()
            if row is None or (row["state"] == "delivering" and row["lease_until"] > now):
                return None
            if row["state"] == "pending" and not force and row["next_attempt"] > now:
                return None
            lease = uuid.uuid4().hex
            db.execute("""UPDATE jobs SET state='delivering',lease=?,lease_until=?,updated=?,
                          uncertain=CASE WHEN state='delivering' THEN 1 ELSE uncertain END
                          WHERE request_id=?""", (lease, now + 45, now, row["request_id"]))
            return {**dict(row), "lease": lease,
                    "uncertain": bool(row["uncertain"] or row["state"] == "delivering")}

    def drain_once(self, invoke=None, descriptor=None, force=False):
        job = self.claim(force)
        if job is None:
            return False
        if invoke is None:
            from .client import call_online
            invoke = lambda name, args, submitted: call_online(name, args, descriptor, submitted)
        try:
            result = invoke(job["tool"], json.loads(job["arguments"]), job["submitted_day"])
            if not isinstance(result, dict) or not result.get("ok"):
                raise TaskError("尚未确认任务保存，将使用原请求重试", "internal_error", {"delivery_uncertain": True})
            state, failure, uncertain, delay = "applied", None, False, 0
        except TaskError as error:
            result = None
            failure = {"code": error.code, "error": str(error)}
            if error.details is not None:
                failure["details"] = error.details
            details = error.details if isinstance(error.details, dict) else {}
            uncertain = bool(job["uncertain"] or details.get("delivery_uncertain", False))
            retryable = error.code in ("app_unavailable", "busy", "upgrade_required", "internal_error")
            state = ("pending" if retryable else "conflict" if error.code == "conflict" else "failed")
            if error.code == "internal_error" and job["attempts"] >= 4:
                state = "failed"
            delay = min(30, 2 ** min(job["attempts"] + 1, 5))
        now = time.time()
        with self.database() as db:
            db.execute("""UPDATE jobs SET state=?,result=?,error=?,uncertain=?,attempts=attempts+1,
                          next_attempt=?,lease=NULL,lease_until=0,updated=? WHERE request_id=? AND lease=?""",
                       (state, encoded(result) if result else None, encoded(failure) if failure else None,
                        int(uncertain), now + delay, now, job["request_id"], job["lease"]))
            # Keep full payloads/results for the latest 200 completed requests.
            # Compact ID/digest tombstones still prevent an old request being resubmitted.
            db.execute("""UPDATE jobs SET arguments=NULL,result=NULL,error=NULL
                          WHERE state IN ('applied','cancelled') AND seq NOT IN
                          (SELECT seq FROM jobs WHERE state IN ('applied','cancelled') ORDER BY seq DESC LIMIT 200)
                          AND arguments IS NOT NULL""")
        return state == "applied"

    def submit(self, name, arguments, descriptor=None):
        rid = self.enqueue(name, arguments)
        self.drain_once(descriptor=descriptor)
        record = self.get(rid)
        if record["state"] == "applied":
            return {**(record["result"] or {"ok": True, "receipt_expired": True}),
                    "delivery_status": "applied", "request_id": rid}
        if record["state"] in ("conflict", "failed"):
            error = record["last_error"]
            raise TaskError(error["error"], error["code"], {"request_id": rid, "state": record["state"],
                            "cause": error.get("details"), "delivery_uncertain": record["delivery_uncertain"]})
        if record["state"] == "cancelled":
            raise TaskError("此请求已取消；新操作请使用新的 request_id", "cancelled")
        return {"ok": True, "delivery_status": "queued", "queued": True, "request_id": rid,
                "state": record["state"], "message": "已保存到本机待提交队列，尚未确认写入任务清单。简截启动并开启本地 MCP 后会自动处理；用 jianjie_queue 查询结果。"}

    def manage(self, args):
        check_keys(args, ("action", "request_id", "limit"))
        action = args.get("action", "list")
        if action == "list":
            if "request_id" in args:
                raise TaskError("查看单条请求请使用 action=get")
            limit = args.get("limit", 30)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise TaskError("limit 须为 1—100")
            with self.database() as db:
                rows = db.execute("""SELECT * FROM jobs ORDER BY
                    CASE WHEN state IN ('conflict','failed') THEN 0 WHEN state IN ('pending','delivering') THEN 1 ELSE 2 END,
                    seq DESC LIMIT ?""", (limit,)).fetchall()
            return {"ok": True, "counts": self.counts(), "requests": [self.public(row) for row in rows]}
        if action not in ("get", "cancel", "retry"):
            raise TaskError("action 须为 list/get/cancel/retry")
        rid = args.get("request_id")
        if not isinstance(rid, str) or not 8 <= len(rid) <= 128 or "limit" in args:
            raise TaskError("此操作需要有效 request_id，且不接受 limit")
        if action != "get":
            with self.database() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM jobs WHERE request_id=?", (rid,)).fetchone()
                if row is None:
                    raise TaskError("找不到此待提交请求", "not_found")
                if action == "cancel":
                    if row["state"] != "cancelled":
                        if row["state"] not in ("pending", "failed", "conflict") or row["uncertain"]:
                            raise TaskError("请求正在提交或可能已保存，不能确认取消；请先查询任务和请求结果", "delivery_uncertain")
                        db.execute("UPDATE jobs SET state='cancelled',updated=? WHERE request_id=?", (time.time(), rid))
                else:
                    if row["state"] == "conflict":
                        raise TaskError("请先重新查询任务版本，用新的 request_id 提交修改；不能覆盖冲突", "conflict")
                    if row["state"] not in ("pending", "failed"):
                        raise TaskError("只有待提交或失败的请求可重试", "invalid_state")
                    db.execute("UPDATE jobs SET state='pending',next_attempt=0,updated=? WHERE request_id=?", (time.time(), rid))
        return {"ok": True, "request": self.get(rid)}


class OutboxWorker:
    def __init__(self, outbox=None, interval=2):
        self.outbox = outbox or Outbox()
        self.interval = interval
        self.stopped = threading.Event()
        self.last_error = None
        self.thread = threading.Thread(target=self.run, name="JianjieMcpOutbox", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        # Never join here: the GUI may be servicing a delivery on its own thread.
        self.stopped.set()

    def run(self):
        first = True
        while not self.stopped.is_set():
            try:
                delivered = self.outbox.drain_once(force=first)
                self.last_error = None
            except (TaskError, ValueError, TypeError) as error:
                self.last_error = str(error)
                delivered = False
            first = False
            if not delivered:
                self.stopped.wait(self.interval)
