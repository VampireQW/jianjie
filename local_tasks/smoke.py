"""正式 EXE 的 --task-smoke 检查；使用临时数据，不触碰用户任务。"""
def run():
    import json
    import tempfile
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    import common as C
    import sticky
    from .desktop import install
    install()
    from .core import TaskService, today
    app = QApplication.instance() or QApplication([])
    original = C.TASKS_PATH
    try:
        with tempfile.TemporaryDirectory(prefix="JianjiePersonalSmoke-") as directory:
            C.TASKS_PATH = str(Path(directory) / "tasks.json")
            manager = sticky.TaskManager()
            manager.show_widget()
            service = TaskService(manager)
            first = service.execute("jianjie_create_tasks", {"request_id": "smoke-create", "tasks": [
                {"text": "今日验收", "target": "today", "due_date": "2030-01-01"},
                {"text": "待办验收", "target": "backlog"}]})
            assert len(manager.widget.rows()) == 1
            manager.widget.select_tab("backlog")
            assert len(manager.widget.rows()) == 1
            task = first["tasks"][0]
            moved = service.execute("jianjie_update_tasks", {"request_id": "smoke-move", "updates": [
                {"id": task["id"], "version": task["version"], "patch": {"target": "backlog"}}]})
            assert len(manager.widget.rows()) == 2
            assert moved["tasks"][0]["due_date"] == "2030-01-01"
            service.execute("jianjie_undo", {"request_id": "smoke-undo", "batch_id": moved["batch_id"]})
            assert manager.tasks[task["id"]]["plan_date"] == today()
            manager.widget.select_tab("today")
            manager.widget.inp.setPlainText("界面手动新增")
            manager.widget.add_from_input()
            assert len(manager.widget.rows()) == 2
            manager.save()
            saved = json.loads(Path(C.TASKS_PATH).read_text(encoding="utf-8"))
            assert len(saved["tasks"]) == 3 and saved["agent"]["receipts"]
            manager.widget.hide()
            manager.widget.deleteLater()
            manager._date_timer.stop()
            manager._reminder_timer.stop()
            app.processEvents()
        print("LOCAL_TASK_SMOKE_PASS", flush=True)
        return 0
    finally:
        C.TASKS_PATH = original
