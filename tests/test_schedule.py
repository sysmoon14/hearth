"""The schedule maths."""

from datetime import datetime

import pytest

from custom_components.hearth.schedule import (
    ScheduleError,
    next_change,
    normalise,
    normalise_day,
    parse_valve_day,
    temp_at,
)

SCHEDULE = normalise(
    {
        "weekdays": [{"time": "06:30", "temp": 19}, {"time": "09:00", "temp": 16}, {"time": "17:00", "temp": 19}, {"time": "22:30", "temp": 16}],
        "weekends": [{"time": "08:00", "temp": 20}, {"time": "23:00", "temp": 16}],
    }
)


def test_temp_at_and_carry_over() -> None:
    assert temp_at(SCHEDULE, datetime(2026, 11, 13, 7, 0)) == 19  # Friday morning
    assert temp_at(SCHEDULE, datetime(2026, 11, 13, 12, 0)) == 16
    # Saturday before its first change: Friday's last change still applies.
    assert temp_at(SCHEDULE, datetime(2026, 11, 14, 6, 0)) == 16
    assert temp_at(SCHEDULE, datetime(2026, 11, 14, 9, 0)) == 20


def test_next_change_crosses_days() -> None:
    when, temp = next_change(SCHEDULE, datetime(2026, 11, 13, 23, 0))
    assert (when, temp) == (datetime(2026, 11, 14, 8, 0), 20)


def test_normalise_merges_and_limits() -> None:
    day = normalise_day([{"time": "00:00", "temp": 16}, {"time": "07:00", "temp": 19}, {"time": "10:00", "temp": 19}, {"time": "23:00", "temp": 16}])
    assert day == [{"time": "07:00", "temp": 19.0}, {"time": "23:00", "temp": 16.0}]
    with pytest.raises(ScheduleError):
        normalise_day([{"time": f"{h:02d}:00", "temp": 16 + h % 2} for h in range(8)])
    with pytest.raises(ScheduleError):
        normalise_day([{"time": "25:00", "temp": 19}])
    with pytest.raises(ScheduleError):
        normalise_day([{"time": "06:00", "temp": 40}])


def test_valve_day() -> None:
    assert parse_valve_day("00:00/16 07:00/19 10:00/16 10:00/16 17:00/19 23:00/16") == [
        {"time": "07:00", "temp": 19.0}, {"time": "10:00", "temp": 16.0},
        {"time": "17:00", "temp": 19.0}, {"time": "23:00", "temp": 16.0},
    ]
