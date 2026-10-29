import copy
import json
from pathlib import Path
import tempfile
import unittest
from local_tasks.migration import merge_legacy
from test_local_tasks import Manager, MemoryCommon


class ImportTests(unittest.TestCase):
    def test_merge_preserves_ids_and_newest_changes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            common = MemoryCommon()
            common.TASKS_PATH = str(Path(directory) / "tasks.json")
            official = {"id": "existing", "text": "正式原任务", "updated": 10, "done": False}
            common.saved["tasks"] = [official]
            Path(common.TASKS_PATH).write_text(json.dumps(common.saved), encoding="utf-8")
            source = Path(directory) / "legacy.json"
            legacy = {"tasks": [{**official, "text": "个人版后来的编辑", "updated": 20, "plan_date": "2026-10-29"},
                                 {"id": "added", "text": "个人版新增", "updated": 21}]}
            source.write_text(json.dumps(legacy), encoding="utf-8")
            original_bytes = source.read_bytes()
            manager = Manager(common)
            manager._load()
            report = merge_legacy(manager, source)
            self.assertEqual((report["added"], report["updated"]), (1, 1))
            self.assertEqual(manager.tasks["existing"]["text"], "个人版后来的编辑")
            self.assertEqual(manager.tasks["existing"]["plan_date"], "2026-10-29")
            manager.tasks["existing"]["text"] = "合并后继续编辑"
            merge_legacy(manager, source)
            self.assertEqual(manager.tasks["existing"]["text"], "合并后继续编辑")
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertTrue(Path(directory, "tasks-before-mcp-unification.json").is_file())

    def test_newer_official_data_and_deletion_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            common = MemoryCommon()
            common.TASKS_PATH = str(Path(directory) / "tasks.json")
            common.saved["tasks"] = [{"id": "existing", "text": "新内容", "updated": 30, "deleted": True}]
            source = Path(directory) / "legacy.json"
            source.write_text(json.dumps({"tasks": [{"id": "existing", "text": "旧内容", "updated": 20}]}), encoding="utf-8")
            manager = Manager(common)
            manager._load()
            merge_legacy(manager, source)
            self.assertTrue(manager.tasks["existing"]["deleted"])
            self.assertEqual(manager.tasks["existing"]["text"], "新内容")

    def test_failed_save_restores_memory_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            common = MemoryCommon()
            common.TASKS_PATH = str(Path(directory) / "tasks.json")
            common.fail = True
            source = Path(directory) / "legacy.json"
            source.write_text(json.dumps({"tasks": [{"id": "new", "text": "不能丢的内容"}]}), encoding="utf-8")
            manager = Manager(common)
            manager._load()
            with self.assertRaises(OSError):
                merge_legacy(manager, source)
            self.assertEqual(manager.tasks, {})
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
