import copy
import json
from datetime import datetime
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QTime, Qt
from PySide6.QtWidgets import QApplication, QLineEdit
from task_nudge_policy import NudgePolicy, ZONE, configuration, today_tasks
from task_nudges import NudgeSettings, TaskNudgeCard, TodayNudges
from test_local_tasks import Manager, MemoryCommon


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZONE).timestamp()


SETTINGS = {"task_nudge_enabled": True, "task_nudge_interval": 60,
            "task_nudge_start": "09:00", "task_nudge_end": "18:00", "toast_corner": "bottom_right"}


def rows():
    return [{"id": str(i), "text": f"今日待办任务 {i}", "plan_date": "2026-10-30", "prio": 3 if i < 4 else 0}
            for i in range(1, 7)]


class PolicyTests(unittest.TestCase):
    def policy(self, time="2026-10-30T10:00:00", state=None, settings=None):
        now = stamp(time)
        policy = NudgePolicy(settings or SETTINGS, state, lambda a, b: (a+b)/2)
        policy.refresh(now, True, startup=True)
        return policy, now

    def test_scope_matches_today_and_includes_previous_unfinished(self):
        tasks = rows() + [dict(id="old", plan_date="2026-10-29", text="此前未完成"),
                          dict(id="future", plan_date="2026-10-31"), dict(id="backlog", plan_date=None),
                          dict(id="done", plan_date="2026-10-30", done=True),
                          dict(id="deleted", plan_date="2026-10-30", deleted=True)]
        result = today_tasks(tasks, stamp("2026-10-30T10:00:00"))
        self.assertEqual({t["id"] for t in result}, {str(i) for i in range(1, 7)} | {"old"})

    def test_opt_in_default_and_invalid_settings(self):
        self.assertFalse(configuration({})["enabled"])
        value = configuration({"task_nudge_enabled": "true", "task_nudge_interval": True,
                               "task_nudge_start": "20:00", "task_nudge_end": "02:00"})
        self.assertEqual(value, {"enabled": False, "interval": 60, "start": "09:00", "end": "18:00"})

    def test_first_ten_minutes_and_interval_range(self):
        policy, now = self.policy()
        self.assertEqual(policy.state["next_at"], now + 600)
        for fraction in (0, 1):
            policy.uniform = lambda a, b: (a, b)[fraction]
            self.assertEqual(policy.delay(), (50, 70)[fraction] * 60)

    def test_working_hours_and_late_timer_never_show_after_end(self):
        policy, now = self.policy("2026-10-30T08:00:00")
        self.assertEqual(policy.state["next_at"], stamp("2026-10-30T09:00:00"))
        policy.state["next_at"] = stamp("2026-10-30T17:59:59")
        next_at = policy.refresh(stamp("2026-10-30T18:00:05"), True)
        self.assertEqual(next_at, stamp("2026-10-31T09:00:00"))

    def test_restart_preserves_deadline_count_and_mute(self):
        policy, now = self.policy()
        policy.consume(now + 600, rows())
        saved = copy.deepcopy(policy.state)
        restarted, _ = self.policy("2026-10-30T10:20:00", saved)
        self.assertEqual(restarted.state["next_at"], saved["next_at"])
        self.assertEqual(restarted.state["count"], 1)
        restarted.mute(now)
        again, _ = self.policy(state=restarted.state)
        self.assertIsNone(again.refresh(now, True))
        self.assertEqual(again.state["count"], 1)

    def test_midnight_resets_mute_and_budget(self):
        policy, now = self.policy()
        policy.consume(now, rows())
        policy.mute(now)
        policy.refresh(stamp("2026-10-31T08:00:00"), True)
        self.assertEqual(policy.state["count"], 0)
        self.assertEqual(policy.state["mute_day"], "")
        self.assertEqual(policy.state["next_at"], stamp("2026-10-31T09:00:00"))

    def test_budget_six_and_empty_tasks_stop(self):
        policy, now = self.policy()
        for _ in range(6):
            self.assertTrue(policy.consume(now, rows()))
        self.assertFalse(policy.consume(now, rows()))
        self.assertIsNone(policy.refresh(now, True))
        fresh, now = self.policy()
        self.assertIsNone(fresh.refresh(now, False))
        self.assertEqual(fresh.state["next_at"], 0)

    def test_priority_and_round_robin(self):
        policy, now = self.policy()
        first = policy.consume(now, rows())
        second = policy.consume(now + 3600, rows())
        self.assertEqual([t["id"] for t in first], ["1", "2"])
        self.assertEqual([t["id"] for t in second], ["3", "1"])
        self.assertTrue(all(t["prio"] == 3 for t in first + second))

    def test_review_and_exact_reminder_postpone(self):
        policy, now = self.policy()
        policy.review(now + 300)
        self.assertEqual(policy.state["next_at"], now + 3900)
        policy.postpone(now + 3800, 900, at_least=True)
        self.assertEqual(policy.state["next_at"], now + 4700)


class NudgeGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.common = MemoryCommon()
        self.manager = Manager(self.common)
        self.manager._load()
        self.manager.tasks = {t["id"]: t for t in rows()}
        self.manager.reset_versions()
        self.now = stamp("2026-10-30T10:00:00")
        self.ctl = SimpleNamespace(S=copy.deepcopy(SETTINGS), settings=lambda arg: None)
        self.nudges = TodayNudges(self.manager, self.ctl, lambda: self.now, lambda a, b: (a+b)/2)
        self.lock = patch("task_nudges.desktop_locked", return_value=False)
        self.lock.start()

    def tearDown(self):
        self.nudges.stop()
        if self.nudges.card:
            self.nudges.card.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.lock.stop()

    def trigger(self):
        self.now = self.nudges.policy.state["next_at"]
        self.nudges.tick()
        self.app.processEvents()

    def test_toast_snooze_mute_and_restart_persist_without_task_revision(self):
        original = copy.deepcopy(self.manager.tasks)
        self.trigger()
        self.assertTrue(self.nudges.card.isVisible())
        self.assertEqual(self.common.saved["win"]["today_nudge"]["count"], 1)
        self.nudges.card.snooze_button.click()
        self.assertFalse(self.nudges.card.isVisible())
        self.assertEqual(self.nudges.policy.state["next_at"], self.now + 1800)
        self.trigger()
        self.nudges.card.dismiss("mute")
        self.assertFalse(self.nudges.timer.isActive())
        saved = copy.deepcopy(self.common.saved["win"]["today_nudge"])
        self.common.saved = json.loads(json.dumps(self.common.saved))
        restarted_manager = Manager(self.common)
        restarted_manager._load()
        second = TodayNudges(restarted_manager, self.ctl, lambda: self.now)
        try:
            self.assertFalse(second.timer.isActive())
            self.assertEqual(second.policy.state["mute_day"], saved["mute_day"])
        finally:
            second.stop()
        self.assertEqual(self.manager.tasks, original)

    def test_all_completed_closes_card_and_stops_timer(self):
        self.trigger()
        for task in self.manager.tasks.values():
            task["done"] = True
        self.nudges.changed()
        self.app.processEvents()
        self.assertFalse(self.nudges.card.isVisible())
        self.assertFalse(self.nudges.timer.isActive())

    def test_capture_lock_and_regular_toast_defer_without_consuming_budget(self):
        for key, value in (("shot_overlays", [object()]), ("rec_bar", SimpleNamespace(isVisible=lambda: True)),
                           ("_toast", SimpleNamespace(isVisible=lambda: True))):
            with self.subTest(key=key):
                setattr(self.ctl, key, value)
                self.trigger()
                self.assertEqual(self.nudges.policy.state["count"], 0)
                self.assertIsNone(self.nudges.card)
                delattr(self.ctl, key)
        with patch("task_nudges.desktop_locked", return_value=True):
            self.trigger()
            self.assertEqual(self.nudges.policy.state["count"], 0)

    def test_sleep_lateness_defers_instead_of_replaying(self):
        self.now = self.nudges.policy.state["next_at"] + 1800
        self.nudges.tick()
        self.assertIsNone(self.nudges.card)
        self.assertEqual(self.nudges.policy.state["next_at"], self.now + 600)

    def test_close_only_dismisses_current_and_disable_stops_everything(self):
        self.trigger()
        deadline = self.nudges.policy.state["next_at"]
        self.nudges.card.close_button.click()
        self.assertFalse(self.nudges.card.isVisible())
        self.assertEqual(self.nudges.policy.state["next_at"], deadline)
        self.assertTrue(self.nudges.timer.isActive())
        self.ctl.S["task_nudge_enabled"] = False
        self.nudges.configure()
        self.assertFalse(self.nudges.timer.isActive())
        self.assertFalse(self.nudges.card.isVisible())

    def test_hover_pauses_and_resumes_remaining_timeout(self):
        self.trigger()
        card = self.nudges.card
        card.pause_timeout()
        self.assertFalse(card.timer.isActive())
        self.assertGreater(card.remaining, 0)
        with patch.object(card, "underMouse", return_value=False):
            card.resume_timeout()
        self.assertTrue(card.timer.isActive())

    def test_leaving_today_window_resets_interval_from_last_review(self):
        widget = SimpleNamespace(win_state=lambda: {})
        self.manager.widget = widget
        try:
            self.now += 500
            self.nudges.eventFilter(widget, QEvent(QEvent.WindowDeactivate))
            self.assertEqual(self.nudges.policy.state["next_at"], self.now + 3600)
        finally:
            self.manager.widget = None

    def test_exact_reminder_hides_card_and_has_fifteen_minute_priority(self):
        self.trigger()
        self.nudges.policy.state["next_at"] = self.now + 60
        self.nudges.exact_reminder()
        self.assertFalse(self.nudges.card.isVisible())
        self.assertEqual(self.nudges.policy.state["next_at"], self.now + 900)

    def test_four_global_corners_stay_within_work_area(self):
        self.trigger()
        card = self.nudges.card
        area = self.app.primaryScreen().availableGeometry()
        for key in ("top_left", "top_right", "bottom_left", "bottom_right"):
            self.ctl.S["toast_corner"] = key
            card.reposition()
            self.assertTrue(area.contains(card.geometry()))
            self.assertEqual(card.x(), area.left()+24 if key.endswith("left") else area.right()-card.width()-23)
            self.assertEqual(card.y(), area.top()+24 if key.startswith("top") else area.bottom()-card.height()-23)

    def test_no_notice_or_budget_consumed_when_runtime_cannot_save(self):
        self.common.fail = True
        self.trigger()
        self.assertIsNone(self.nudges.card)
        self.assertEqual(self.nudges.policy.state["count"], 0)

    def test_settings_keep_invalid_time_unsaved_and_emit_complete_time_pair(self):
        from widgets import AppSettingsDialog
        dialog = AppSettingsDialog(SETTINGS)
        try:
            control = dialog.nudge_controls
            changes = []
            control.changed.connect(changes.append)
            control.frequencies[120].click()
            self.assertEqual(changes[-1], {"task_nudge_interval": 120})
            changes.clear()
            control.start.setTime(QTime(19, 0))
            control.time_changed()
            self.assertFalse(changes)
            self.assertFalse(control.error.isHidden())
            control.end.setTime(QTime(21, 0))
            control.time_changed()
            self.assertEqual(changes[-1], {"task_nudge_start": "19:00", "task_nudge_end": "21:00"})
            control.set_configuration({})
            self.assertFalse(control.toggle.isChecked())
            self.assertFalse(control.options.isEnabled())
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
