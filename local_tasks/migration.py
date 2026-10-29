"""一次性把旧个人版任务归并回正式版；旧源文件保留用于恢复。"""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
from .core import fingerprint


def legacy_tasks_path():
    root = Path(os.environ.get("APPDATA", Path.home())) / "jianjie-personal"
    locator = root / "storage-location.json"
    state = json.loads(locator.read_text(encoding="utf-8")) if locator.exists() else {}
    return Path(state.get("data_dir", root)) / "tasks.json"


def merge_legacy(manager, source=None):
    if manager.agent_meta.get("legacy_personal_merged"):
        return manager.agent_meta["legacy_personal_merged"]
    path = Path(source) if source else legacy_tasks_path()
    if not path.exists():
        return None
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError("旧个人版任务格式异常，未进行归并")
    old_tasks = copy.deepcopy(manager.tasks)
    old_meta = copy.deepcopy(manager.agent_meta)
    added = updated = conflicts = 0
    try:
        for task in data["tasks"]:
            if not isinstance(task, dict) or not isinstance(task.get("id"), str):
                raise ValueError("旧个人版任务缺少标识，未进行归并")
            tid = task["id"]
            existing = manager.tasks.get(tid)
            if existing is None:
                manager.tasks[tid] = copy.deepcopy(task)
                added += 1
            elif fingerprint(existing) != fingerprint(task):
                if float(task.get("updated", 0)) > float(existing.get("updated", 0)):
                    manager.tasks[tid] = copy.deepcopy(task)
                    updated += 1
                elif float(task.get("updated", 0)) == float(existing.get("updated", 0)):
                    # 同时刻出现不同内容时保留正式版，旧文件留存，不静默覆盖。
                    conflicts += 1
        previous_agent = data.get("agent", {})
        for key in ("receipts", "undo"):
            entries = manager.agent_meta.setdefault(key, {})
            for rid, value in previous_agent.get(key, {}).items():
                entries.setdefault(rid, copy.deepcopy(value))
            while len(entries) > 200:
                del entries[next(iter(entries))]
        report = {"source": str(path), "source_sha256": hashlib.sha256(raw).hexdigest(),
                  "added": added, "updated": updated, "same_time_conflicts": conflicts, "at": time.time()}
        manager.agent_meta["legacy_personal_merged"] = report
        # 正式任务被修改前保留一次恢复快照；不是另一份运行中的任务库。
        original_path = Path(manager._task_common.TASKS_PATH)
        backup = original_path.with_name("tasks-before-mcp-unification.json")
        if original_path.exists() and not backup.exists():
            with backup.open("xb") as stream:
                stream.write(original_path.read_bytes())
        manager.save()
        return report
    except Exception:
        manager.tasks, manager.agent_meta = old_tasks, old_meta
        manager.reset_versions()
        raise
