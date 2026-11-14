"""Weekday/weekend schedules, the same shape for every zone.

A day is a list of changes: from each time, the room aims for that
temperature. Before a day's first change, the previous day's last change
still applies, the same way the Heatmiser stats run their levels.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from .const import (
    DAY_TYPES,
    MAX_CHANGES_PER_DAY,
    MAX_TEMP,
    MIN_TEMP,
    WEEKDAYS,
    WEEKENDS,
)

Change = dict[str, Any]  # {"time": "HH:MM", "temp": float}
Schedule = dict[str, list[Change]]


class ScheduleError(ValueError):
    """A schedule that can't be used."""


def day_type(day: datetime) -> str:
    """Weekdays for Monday to Friday, weekends for Saturday and Sunday."""
    return WEEKENDS if day.weekday() >= 5 else WEEKDAYS


def _parse_time(value: str) -> time:
    try:
        hour, minute = (int(part) for part in str(value).split(":")[:2])
        return time(hour, minute)
    except (ValueError, TypeError) as err:
        raise ScheduleError(f"{value!r} isn't a time like 06:30") from err


def _round_temp(value: Any) -> float:
    try:
        temp = round(float(value) * 2) / 2
    except (TypeError, ValueError) as err:
        raise ScheduleError(f"{value!r} isn't a temperature") from err
    if not MIN_TEMP <= temp <= MAX_TEMP:
        raise ScheduleError(f"{temp}° is outside {MIN_TEMP:g}° to {MAX_TEMP:g}°")
    return temp


def normalise_day(changes: list[Any]) -> list[Change]:
    """Sort a day's changes, round temperatures and drop repeats.

    A change to the temperature already in force does nothing, so it is
    dropped; that also keeps days inside the six-change limit.
    """
    out = _tidy_day(changes)
    _check_length(out)
    return out


def _tidy_day(changes: list[Any]) -> list[Change]:
    parsed: dict[str, float] = {}
    for change in changes or []:
        if not isinstance(change, dict):
            raise ScheduleError("each change needs a time and a temperature")
        moment = _parse_time(change.get("time"))
        parsed[moment.strftime("%H:%M")] = _round_temp(change.get("temp"))
    if not parsed:
        raise ScheduleError("a day needs at least one change")
    ordered = sorted(parsed.items())
    out: list[Change] = []
    for when, temp in ordered:
        if out and out[-1]["temp"] == temp:
            continue
        out.append({"time": when, "temp": temp})
    return out


def _check_length(day: list[Change]) -> None:
    if len(day) > MAX_CHANGES_PER_DAY:
        raise ScheduleError(
            f"a day can have at most {MAX_CHANGES_PER_DAY} changes; this one has {len(day)}"
        )


def normalise(schedule: dict[str, Any]) -> Schedule:
    """Check and tidy a whole schedule."""
    if not isinstance(schedule, dict):
        raise ScheduleError("a schedule needs weekdays and weekends")
    days = {key: _tidy_day(schedule.get(key) or []) for key in DAY_TYPES}
    # A day's first change that only repeats what the night before ended on
    # does nothing. A weekday follows a weekday or a Sunday, and a weekend day
    # a Friday or a Saturday, so that's only true when both kinds of day end
    # on that temperature.
    ends = {day[-1]["temp"] for day in days.values()}
    for key, day in days.items():
        if len(day) > 1 and ends == {day[0]["temp"]}:
            days[key] = day = day[1:]
        _check_length(day)
    return days


def parse_valve_day(text: str) -> list[Change]:
    """Read a Sonoff valve's day, written like '00:00/16 07:00/19 10:00/16'."""
    changes = []
    for part in (text or "").split():
        when, _, temp = part.partition("/")
        changes.append({"time": when, "temp": temp})
    return normalise_day(changes)


def _moments(schedule: Schedule, start: datetime, days: int):
    """Every change from the day before start to `days` after, in order."""
    first = (start - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(days + 2):
        day = first + timedelta(days=offset)
        for change in schedule[day_type(day)]:
            moment = _parse_time(change["time"])
            yield day.replace(hour=moment.hour, minute=moment.minute), change["temp"]


def temp_at(schedule: Schedule, when: datetime) -> float:
    """The temperature the schedule asks for at a moment."""
    current = None
    for moment, temp in _moments(schedule, when, 0):
        if moment > when:
            break
        current = temp
    if current is None:  # cannot happen: the day before always has a change
        current = schedule[day_type(when)][-1]["temp"]
    return current


def next_change(schedule: Schedule, when: datetime) -> tuple[datetime, float] | None:
    """When the schedule next asks for a different temperature, and what."""
    now_temp = temp_at(schedule, when)
    for moment, temp in _moments(schedule, when, 8):
        if moment > when and temp != now_temp:
            return moment, temp
    return None
