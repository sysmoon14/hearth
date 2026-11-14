"""Warming up early: learning how fast a room warms, and starting in time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hearth import preheat
from custom_components.hearth.preheat import WarmUp

from .conftest import OFFICE, OFFICE_SENSOR, OFFICE_TRV, THERMOSTAT
from .test_zones import ROOM, _setup

T0 = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_firing_periods_join_flickers_and_drop_short_ones() -> None:
    spans = [(at(0), at(12)), (at(14), at(40)), (at(100), at(110)), (at(200), at(260))]
    assert preheat.firing_periods(spans) == [(at(0), at(40)), (at(200), at(260))]


def test_spans_where_a_thermostat_was_firing() -> None:
    changes = [(at(0), False), (at(5), True), (at(6), True), (at(30), False), (at(50), True)]
    assert preheat.spans_where(changes, at(90)) == [(at(5), at(30)), (at(50), at(90))]


def test_warmups_from_history() -> None:
    readings = [(at(-30), 16.0), (at(20), 16.6), (at(50), 17.8), (at(70), 18.0), (at(80), 18.0), (at(100), 17.6)]
    # Rose 2° in 70 minutes, to the warmest reading (its first time at 18°).
    found = preheat.warmups_from_history([(at(0), at(60))], readings)
    assert [(w.rise, w.minutes, w.source) for w in found] == [(2.0, 70.0, "history")]
    # A room that barely moved (its valve was shut) teaches nothing.
    flat = [(at(-5), 19.0), (at(30), 19.2), (at(60), 19.3)]
    assert preheat.warmups_from_history([(at(0), at(60))], flat) == []
    # Nor does a period with no reading before it.
    assert preheat.warmups_from_history([(at(0), at(60))], readings[1:]) == []


def test_rate_and_lead() -> None:
    ups = [WarmUp(2, 60, at(0), "seen")]
    assert preheat.rate(ups) is None  # one isn't enough
    ups = preheat.add(ups, WarmUp(1, 60, at(10), "seen"))
    ups = preheat.add(ups, WarmUp(3, 60, at(20), "history"))
    assert preheat.rate(ups) == 2.0  # the median, in degrees an hour
    assert preheat.lead_minutes(3, 2.0, 180) == 100  # 90 minutes, plus the margin
    assert preheat.lead_minutes(10, 2.0, 180) == 180  # never earlier than the cap
    assert preheat.lead_minutes(3, 2.0, 0) is None  # turned off
    assert preheat.lead_minutes(0, 2.0, 180) is None  # already warm enough
    for n in range(20):
        ups = preheat.add(ups, WarmUp(1, 30, at(100 + n), "seen"))
    assert len(ups) == preheat.KEEP and ups[0].at == at(119)


async def _tick(hass: HomeAssistant, freezer, to: str) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime(to)))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _teach(hass: HomeAssistant, entry, rate_per_hour: float = 2.0) -> None:
    zone = entry.runtime_data.zones["office"]
    for n in range(3):
        zone.learn(WarmUp(rate_per_hour, 60, T0 - timedelta(days=n + 1), "seen"))


async def test_a_room_warms_up_early_and_learns_from_it(hass: HomeAssistant, house, calls, freezer) -> None:
    # Friday: 16° until 07:00, then 19° (the valve's schedule).
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T05:00:00+00:00")))
    hass.states.async_set(OFFICE_SENSOR, "16.0")
    entry = await _setup(hass, calls, [OFFICE])
    room = hass.states.get(ROOM)
    assert room.attributes["warm_up_rate"] is None
    assert room.attributes["temperature"] == 16

    _teach(hass, entry)
    await _tick(hass, freezer, "2026-11-13T05:00:30+00:00")
    room = hass.states.get(ROOM)
    assert room.attributes["warm_up_rate"] == 2.0
    # 3° at 2° an hour is 90 minutes, plus 10: from 05:20.
    assert room.attributes["preheat_from"] == "2026-11-13T05:20:00+00:00"
    assert room.attributes["temperature"] == 16 and not room.attributes["preheating"]

    await _tick(hass, freezer, "2026-11-13T05:21:00+00:00")
    room = hass.states.get(ROOM)
    assert room.attributes["temperature"] == 19
    assert room.attributes["reason"] == "Warming up early for 19° by 07:00"
    assert room.attributes["preheating"] and room.attributes["hvac_action"] == "preheating"
    assert hass.states.get(OFFICE_TRV).attributes["temperature"] == 19
    assert hass.states.get(THERMOSTAT).attributes["temperature"] == 25.0

    # Halfway there the lead has shrunk, but it keeps going.
    hass.states.async_set(OFFICE_SENSOR, "17.6")
    await _tick(hass, freezer, "2026-11-13T06:00:00+00:00")
    assert hass.states.get(ROOM).attributes["temperature"] == 19

    # Warm by 06:40: it stops asking, and that warm-up is learned.
    hass.states.async_set(OFFICE_SENSOR, "19.3")
    await _tick(hass, freezer, "2026-11-13T06:40:00+00:00")
    room = hass.states.get(ROOM)
    assert not room.attributes["calling_for_heat"]
    assert room.attributes["warm_ups"] == 4
    newest = entry.runtime_data.zones["office"].state.warmups[0]
    assert (round(newest.rise, 1), newest.minutes, newest.source) == (3.3, 79.0, "seen")

    # At 07:00 the schedule takes over with the same 19°.
    await _tick(hass, freezer, "2026-11-13T07:00:30+00:00")
    room = hass.states.get(ROOM)
    assert room.attributes["temperature"] == 19 and not room.attributes["preheating"]
    assert room.attributes["reason"].startswith("19° until 10:00")


async def test_no_early_start_when_away_or_turned_off(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T05:30:00+00:00")))
    hass.states.async_set(OFFICE_SENSOR, "16.0")
    entry = await _setup(hass, calls, [OFFICE], preheat_max_minutes=0)
    _teach(hass, entry)
    await _tick(hass, freezer, "2026-11-13T05:30:30+00:00")
    room = hass.states.get(ROOM)
    assert room.attributes["temperature"] == 16 and room.attributes["preheat_from"] is None

    hass.config_entries.async_update_entry(entry, options={**entry.options, "preheat_max_minutes": 180})
    await hass.async_block_till_done()
    await _tick(hass, freezer, "2026-11-13T05:31:00+00:00")
    assert hass.states.get(ROOM).attributes["temperature"] == 19  # learning survived the reload

    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.hearth_house_mode", "option": "away"}, blocking=True)
    await _tick(hass, freezer, "2026-11-13T05:31:30+00:00")
    assert hass.states.get(ROOM).attributes["temperature"] == 16


async def test_a_new_room_learns_from_the_boilers_history(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T05:00:00+00:00")))
    hass.config.components.add("recorder")
    day = dt_util.parse_datetime("2026-11-12T06:00:00+00:00")

    def state(entity_id: str, value: str, minutes: float, **attrs) -> SimpleNamespace:
        """Shaped like the recorder's light states: no domain, no object_id."""
        when = day + timedelta(minutes=minutes)
        return SimpleNamespace(entity_id=entity_id, state=value, attributes=attrs,
                               last_changed=when, last_updated=when)

    history = {
        THERMOSTAT: [state(THERMOSTAT, "heat", 0, hvac_action="idle"), state(THERMOSTAT, "heat", 10, hvac_action="heating"),
                     state(THERMOSTAT, "heat", 100, hvac_action="idle"), state(THERMOSTAT, "heat", 600, hvac_action="heating"),
                     state(THERMOSTAT, "heat", 660, hvac_action="idle")],
        OFFICE_SENSOR: [state(OFFICE_SENSOR, "16.0", 0), state(OFFICE_SENSOR, "17.0", 40), state(OFFICE_SENSOR, "18.5", 90),
                        state(OFFICE_SENSOR, "18.0", 300), state(OFFICE_SENSOR, "19.0", 640)],
    }
    recorder = MagicMock()

    async def run(job):
        return job()

    recorder.async_add_executor_job = run
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=recorder),
        patch("homeassistant.components.recorder.history.get_significant_states", return_value=history),
    ):
        entry = await _setup(hass, calls, [OFFICE])
        await hass.async_block_till_done()
    zone = entry.runtime_data.zones["office"]
    assert zone.state.seeded
    assert entry.runtime_data.seed_status.startswith("done: 2 times the boiler fired; warm-ups Office 2")
    # 2.5° in 80 minutes, and 1° in 40.
    assert [(w.rise, w.minutes) for w in zone.state.warmups] == [(1.0, 40.0), (2.5, 80.0)]
    room = hass.states.get(ROOM)
    assert room.attributes["warm_ups_from_history"] == 2
    assert room.attributes["warm_up_rate"] == 1.69


async def test_a_room_on_its_valves_reading_learns_but_isnt_seeded(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T06:50:00+00:00")))
    hass.config.components.add("recorder")
    hass.states.async_set(OFFICE_TRV, "heat", {"temperature": 16.0, "current_temperature": 16.0, "hvac_action": "idle"})
    valve_only = {k: v for k, v in OFFICE.items() if k != "sensor"}
    with patch("homeassistant.components.recorder.get_instance") as recorder:
        entry = await _setup(hass, calls, [valve_only])
    recorder.assert_not_called()  # no sensor, no history to read
    zone = entry.runtime_data.zones["office"]
    assert zone.state.learned_with == "valves" and not zone.state.seeded
    assert entry.runtime_data.seed_status == "not needed"

    await _tick(hass, freezer, "2026-11-13T07:00:30+00:00")
    assert hass.states.get(ROOM).attributes["calling_for_heat"]
    attrs = dict(hass.states.get(OFFICE_TRV).attributes, current_temperature=19.3)
    hass.states.async_set(OFFICE_TRV, "heat", attrs)
    await _tick(hass, freezer, "2026-11-13T07:30:30+00:00")
    assert [(round(w.rise, 1), w.minutes, w.source) for w in zone.state.warmups] == [(3.3, 30.0, "seen")]
    assert hass.states.get(ROOM).attributes["warm_ups"] == 1


async def test_a_warm_up_cut_short_by_the_schedule_still_counts(hass: HomeAssistant, house, calls, freezer) -> None:
    # 19° from 07:00 to 10:00; the room only gets to 17.5° before it drops to 16°.
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T06:59:00+00:00")))
    hass.states.async_set(OFFICE_SENSOR, "16.0")
    entry = await _setup(hass, calls, [OFFICE])
    zone = entry.runtime_data.zones["office"]
    await _tick(hass, freezer, "2026-11-13T07:00:30+00:00")
    assert hass.states.get(ROOM).attributes["calling_for_heat"]

    hass.states.async_set(OFFICE_SENSOR, "17.5")
    await _tick(hass, freezer, "2026-11-13T09:00:00+00:00")
    assert zone.state.warmups == []  # still on its way

    await _tick(hass, freezer, "2026-11-13T10:00:30+00:00")
    assert not hass.states.get(ROOM).attributes["calling_for_heat"]
    assert [(w.rise, w.minutes, w.source) for w in zone.state.warmups] == [(1.5, 180.0, "seen")]

    # A rise too small to tell anything is still left out.
    hass.states.async_set(OFFICE_SENSOR, "16.0")
    await _tick(hass, freezer, "2026-11-13T17:00:30+00:00")  # 19° again
    hass.states.async_set(OFFICE_SENSOR, "16.3")
    await _tick(hass, freezer, "2026-11-13T23:00:30+00:00")  # 16° at 23:00
    assert len(zone.state.warmups) == 1
