"""Agent 无关的任务业务层。调用方负责在简截 GUI 线程串行执行。"""
import copy
import hashlib
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone


class TaskError(Exception):
    def __init__(self, message, code="invalid_arguments", details=None):
        super().__init__(message)
        self.code, self.details = code, details


def today():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def check_keys(value, allowed):
    if not isinstance(value, dict):
        raise TaskError("参数必须是对象")
    unknown = set(value) - set(allowed)
    if unknown:
        raise TaskError("不支持的参数：" + ", ".join(sorted(unknown)))


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2000:
        raise TaskError("任务内容须为 1—2000 字")
    return value.strip()


def day(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TaskError("日期须为 YYYY-MM-DD")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise TaskError("日期须为有效的 YYYY-MM-DD") from None
    return value


def fingerprint(task):
    return json.dumps({k: v for k, v in task.items() if k not in ("version", "updated")},
                      sort_keys=True, ensure_ascii=False)


WRITE_TOOLS = ("jianjie_create_tasks", "jianjie_update_tasks", "jianjie_undo")


def request_digest(name, args):
    return hashlib.sha256(json.dumps([name, args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def validate_write(name, args):
    """Validate offline requests without loading or changing any task data."""
    if name not in WRITE_TOOLS:
        raise TaskError("未知写入工具", "unknown_tool")
    check_keys(args, {"jianjie_create_tasks": ("request_id", "tasks"),
                      "jianjie_update_tasks": ("request_id", "updates"),
                      "jianjie_undo": ("request_id", "batch_id")}[name])
    rid = args.get("request_id")
    if not isinstance(rid, str) or not 8 <= len(rid) <= 128:
        raise TaskError("写入操作需要 8—128 字符的 request_id；重试使用同一值")
    if name == "jianjie_undo":
        if not isinstance(args.get("batch_id"), str) or not 1 <= len(args["batch_id"]) <= 128:
            raise TaskError("batch_id 须为有效的操作 ID")
        return
    rows = args.get("tasks" if name == "jianjie_create_tasks" else "updates")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise TaskError("每批须包含 1—50 条任务")
    seen = set()
    for row in rows:
        if name == "jianjie_create_tasks":
            TaskService.fields(row, True)
        else:
            check_keys(row, ("id", "version", "patch"))
            tid, version = row.get("id"), row.get("version")
            if not isinstance(tid, str) or not 1 <= len(tid) <= 128:
                raise TaskError("id 须为查询返回的真实任务 ID")
            if tid in seen:
                raise TaskError("同一批不能重复修改同一任务")
            seen.add(tid)
            if type(version) is not int or version < 1:
                raise TaskError("version 须为查询返回的正整数")
            if not TaskService.fields(row.get("patch")):
                raise TaskError("patch 不能为空")


def public(task):
    return {"id": task["id"], "text": task["text"], "priority": task.get("prio", 0),
            "plan_date": task.get("plan_date"), "due_date": task.get("due_date") or None,
            "done": bool(task.get("done")), "deleted": bool(task.get("deleted")),
            "version": task.get("version", 1), "source": task.get("source", "desktop")}


class TaskService:
    def __init__(self, manager):
        self.manager = manager

    def execute(self, name, args, reference_day=None):
        if reference_day is not None:
            reference_day = day(reference_day)
        m = self.manager
        m._load()
        m.refresh_versions()
        if name == "jianjie_status":
            check_keys(args, ())
            return {"ok": True, "today": today(), "timezone": "Asia/Shanghai",
                    "application": "简截", "api_version": 1}
        if name == "jianjie_list_tasks":
            return self.list_tasks(args)
        if name not in WRITE_TOOLS:
            raise TaskError("未知工具", "unknown_tool")
        validate_write(name, args)
        if m.widget and (getattr(m.widget, "_editing", None) is not None or
                         any(getattr(r, "_completing", False) for r in m.widget.rows())):
            raise TaskError("简截正在编辑或完成任务，请稍后用原 request_id 重试", "busy")
        request_id = args.get("request_id")
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise TaskError("写入操作需要 8—128 字符的 request_id；重试使用同一值")
        digest = request_digest(name, args)
        cached = m.agent_meta.setdefault("receipts", {}).get(request_id)
        if cached:
            if cached["digest"] != digest:
                raise TaskError("同一 request_id 已用于其他内容", "request_id_reused")
            return {**copy.deepcopy(cached["result"]), "replayed": True}
        before = copy.deepcopy(m.tasks)
        meta_before = copy.deepcopy(m.agent_meta)
        try:
            if name == "jianjie_create_tasks":
                result = self.create(args, reference_day)
            elif name == "jianjie_update_tasks":
                result = self.update(args, reference_day)
            else:
                result = self.undo(args)
            # 版本在返回前固定，UI 编辑也使用同一版本递增规则。
            m.refresh_versions()
            if "tasks" in result:
                result["tasks"] = [public(m.tasks[t["id"]]) for t in result["tasks"]]
            if name != "jianjie_undo":
                batch_id = uuid.uuid4().hex
                changed = {tid: {"before": before.get(tid), "after_version": task.get("version", 1)}
                           for tid, task in m.tasks.items() if task != before.get(tid)}
                m.agent_meta.setdefault("undo", {})[batch_id] = {"at": time.time(), "tasks": changed}
                result["batch_id"] = batch_id
            result.update(ok=True, today=today())
            m.agent_meta["receipts"][request_id] = {"digest": digest, "result": copy.deepcopy(result)}
            # 只保留最近 200 次操作的重试回执与撤销记录。
            for key in ("receipts", "undo"):
                entries = m.agent_meta.setdefault(key, {})
                while len(entries) > 200:
                    del entries[next(iter(entries))]
            m.save()  # 先原子保存成功，再告知 Agent。
        except Exception:
            m.tasks, m.agent_meta = before, meta_before
            m.reset_versions()
            raise
        m._arm_reminders()
        if m.widget:
            m.widget.rebuild()
        return result

    def list_tasks(self, args):
        check_keys(args, ("list", "status", "query", "include_deleted", "offset", "limit"))
        scope, status = args.get("list", "all"), args.get("status", "open")
        if scope not in ("today", "backlog", "all") or status not in ("open", "done", "all"):
            raise TaskError("list 须为 today/backlog/all，status 须为 open/done/all")
        query = args.get("query", "")
        if not isinstance(query, str) or len(query) > 2000:
            raise TaskError("query 须为文本")
        offset, limit = args.get("offset", 0), args.get("limit", 100)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise TaskError("offset 须非负，limit 须为 1—200")
        if type(args.get("include_deleted", False)) is not bool:
            raise TaskError("include_deleted 须为布尔值")
        rows = []
        for task in self.manager.tasks.values():
            if task.get("deleted") and not args.get("include_deleted"):
                continue
            planned = task.get("plan_date")
            # 今日页保留此前未完成项，避免跨日后任务消失。
            if scope == "today" and (not planned or planned > today() or
                                      (task.get("done") and planned != today())):
                continue
            if scope == "backlog" and planned and planned <= today():
                continue
            if status != "all" and bool(task.get("done")) != (status == "done"):
                continue
            if query.casefold() not in task.get("text", "").casefold():
                continue
            rows.append(task)
        rows.sort(key=lambda t: (bool(t.get("done")), t.get("order", 0), -t.get("created", 0)))
        return {"ok": True, "today": today(), "timezone": "Asia/Shanghai", "total": len(rows),
                "tasks": [public(t) for t in rows[offset:offset + limit]],
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    @staticmethod
    def fields(value, creating=False, reference_day=None):
        check_keys(value, ("text", "priority", "target", "plan_date", "due_date", "done", "deleted"))
        result = {}
        if creating or "text" in value:
            result["text"] = text(value.get("text"))
        if "priority" in value:
            p = value["priority"]
            if type(p) is not int or p not in range(4):
                raise TaskError("priority 须为 0—3")
            result["prio"] = p
        for k in ("done", "deleted"):
            if k in value:
                if type(value[k]) is not bool:
                    raise TaskError(k + " 须为布尔值")
                result[k] = value[k]
        if "target" in value:
            if value["target"] not in ("today", "backlog"):
                raise TaskError("target 须为 today 或 backlog")
            if "plan_date" in value:
                raise TaskError("target 与 plan_date 只能传一个")
            result["plan_date"] = (reference_day or today()) if value["target"] == "today" else None
        elif "plan_date" in value:
            result["plan_date"] = day(value["plan_date"])
        elif creating:
            result["plan_date"] = None
        if "due_date" in value:
            result["due_date"] = day(value["due_date"]) or ""
            result["reminded_at"] = 0
            result["remind_at"] = ""
            result["remind_offset"] = None
        return result

    def create(self, args, reference_day=None):
        check_keys(args, ("request_id", "tasks"))
        tasks = args.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 50:
            raise TaskError("tasks 须包含 1—50 条任务")
        values = [self.fields(t, True, reference_day) for t in tasks]
        order = min((t.get("order", 0) for t in self.manager.tasks.values()), default=0) - len(values)
        created = []
        for i, fields in enumerate(values):
            task = {"id": uuid.uuid4().hex, "prio": 0, "done": False, "deleted": False,
                    "created": time.time(), "updated": time.time(), "done_at": 0,
                    "order": order + i, "source": "agent", **fields}
            if task["done"]:
                task["done_at"] = time.time()
            self.manager.tasks[task["id"]] = task
            created.append(task)
        return {"tasks": created}

    def update(self, args, reference_day=None):
        check_keys(args, ("request_id", "updates"))
        updates = args.get("updates")
        if not isinstance(updates, list) or not 1 <= len(updates) <= 50:
            raise TaskError("updates 须包含 1—50 条修改")
        prepared, seen = [], set()
        for update in updates:
            check_keys(update, ("id", "version", "patch"))
            tid, version = update.get("id"), update.get("version")
            if not isinstance(tid, str) or tid not in self.manager.tasks:
                raise TaskError("任务不存在；请先查询取得真实 ID", "not_found")
            if tid in seen:
                raise TaskError("同一批不能重复修改同一任务")
            seen.add(tid)
            task = self.manager.tasks[tid]
            if type(version) is not int or version != task.get("version", 1):
                raise TaskError("任务已变化，请重新查询后修改", "conflict", public(task))
            fields = self.fields(update.get("patch"), reference_day=reference_day)
            if not fields:
                raise TaskError("patch 不能为空")
            if task.get("deleted") and fields.get("deleted") is not False:
                raise TaskError("该任务已删除；先通过 deleted=false 恢复", "deleted")
            prepared.append((task, fields))
        for task, fields in prepared:
            if "done" in fields:
                fields["done_at"] = time.time() if fields["done"] else 0
            task.update(fields)
        return {"tasks": [t for t, _ in prepared]}

    def undo(self, args):
        check_keys(args, ("request_id", "batch_id"))
        record = self.manager.agent_meta.setdefault("undo", {}).get(args.get("batch_id"))
        if not record:
            raise TaskError("找不到可撤销记录", "not_found")
        if record.get("undone"):
            raise TaskError("这批操作已经撤销", "already_undone")
        for tid, item in record["tasks"].items():
            task = self.manager.tasks.get(tid)
            if not task or task.get("version", 1) != item["after_version"]:
                raise TaskError("任务之后已被修改，不能覆盖这些修改", "conflict")
        rows = []
        for tid, item in record["tasks"].items():
            current = self.manager.tasks[tid]
            if item["before"] is None:
                current["deleted"] = True
            else:
                # 版本只增不减；提醒、日期和顺序随任务一起恢复。
                version = current.get("version", 1)
                current.clear()
                current.update(copy.deepcopy(item["before"]), version=version)
            rows.append(current)
        record["undone"] = True
        return {"tasks": rows}
