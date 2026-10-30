"""Pure scheduling rules for the optional today-task nudge."""
from datetime import datetime, timedelta, timezone
import copy
import math
import random

ZONE = timezone(timedelta(hours=8))
PREFIX = "task_nudge_"
DEFAULTS = {"enabled": False, "interval": 60, "start": "09:00", "end": "18:00"}
DAILY_LIMIT = 6


def configuration(settings):
    values = {key: settings.get(PREFIX + key, value) for key, value in DEFAULTS.items()}
    values["enabled"] = values["enabled"] is True
    if type(values["interval"]) is not int or values["interval"] not in (30, 60, 120):
        values["interval"] = 60
    try:
        start, end = minutes(values["start"]), minutes(values["end"])
        if start >= end:
            raise ValueError()
    except (TypeError, ValueError):
        values.update(start="09:00", end="18:00")
    return values


def minutes(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError("时间须为 HH:mm")
    hour, minute = int(value[:2]), int(value[3:])
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError("时间无效")
    return hour * 60 + minute


def day(now):
    return datetime.fromtimestamp(now, ZONE).date().isoformat()


def today_tasks(tasks, now):
    current = day(now)
    return sorted((task for task in tasks if not task.get("done") and not task.get("deleted")
                   and isinstance(task.get("plan_date"), str) and task["plan_date"]
                   and task["plan_date"] <= current),
                  key=lambda task: (-task.get("prio", 0), task.get("order", 0), task.get("id", "")))


def timestamp(value, default=0):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else default


class NudgePolicy:
    def __init__(self, settings, state=None, uniform=None):
        self.config = configuration(settings)
        self.state = copy.deepcopy(state) if isinstance(state, dict) else {}
        self.uniform = uniform or random.uniform

    def roll(self, now):
        if self.state.get("day") != day(now):
            self.state = {"day": day(now), "count": 0, "next_at": 0, "mute_day": "", "cursors": {}}
        count = self.state.get("count", 0)
        self.state["count"] = min(DAILY_LIMIT, max(0, count)) if type(count) is int else 0
        self.state["next_at"] = timestamp(self.state.get("next_at"))
        if self.state["next_at"] > now + 172800:
            self.state["next_at"] = 0
        if not isinstance(self.state.get("cursors"), dict):
            self.state["cursors"] = {}

    def fit(self, stamp):
        value = datetime.fromtimestamp(stamp, ZONE)
        midnight = value.replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight + timedelta(minutes=minutes(self.config["start"]))
        end = midnight + timedelta(minutes=minutes(self.config["end"]))
        if value < start:
            return start.timestamp()
        if value >= end:
            return (start + timedelta(days=1)).timestamp()
        return stamp

    def delay(self):
        interval = self.config["interval"] * 60
        return self.uniform(interval * 5 / 6, interval * 7 / 6)

    def available(self, now, has_tasks):
        self.roll(now)
        return (self.config["enabled"] and has_tasks and self.state.get("mute_day") != day(now)
                and self.state["count"] < DAILY_LIMIT)

    def refresh(self, now, has_tasks, startup=False):
        if not self.available(now, has_tasks):
            self.state["next_at"] = 0
            return None
        next_at = self.state["next_at"]
        if not next_at or (startup and next_at <= now):
            next_at = now + 600
        self.state["next_at"] = self.fit(next_at)
        current_window = self.fit(now)
        if current_window > now:
            self.state["next_at"] = max(self.state["next_at"], current_window)
        return self.state["next_at"]

    def postpone(self, now, seconds, at_least=False):
        self.roll(now)
        stamp = max(self.state["next_at"], now + seconds) if at_least else now + seconds
        self.state["next_at"] = self.fit(stamp)

    def review(self, now):
        self.postpone(now, self.delay())

    def mute(self, now):
        self.roll(now)
        self.state.update(mute_day=day(now), next_at=0)

    def select(self, tasks, advance=False):
        selected = []
        for priority in (3, 2, 1, 0):
            group = [task for task in tasks if task.get("prio", 0) == priority]
            if not group:
                continue
            cursor = self.state.get("cursors", {}).get(str(priority), 0)
            cursor = cursor if type(cursor) is int else 0
            count = min(2 - len(selected), len(group))
            selected.extend(group[(cursor + i) % len(group)] for i in range(count))
            if advance:
                self.state["cursors"][str(priority)] = (cursor + count) % len(group)
            if len(selected) == 2:
                break
        return selected

    def consume(self, now, tasks):
        if not self.available(now, bool(tasks)):
            return []
        selected = self.select(tasks, advance=True)
        self.state["count"] += 1
        self.state["last_shown"] = now
        self.postpone(now, self.delay())
        if self.state["count"] >= DAILY_LIMIT:
            self.state["next_at"] = 0
        return selected
