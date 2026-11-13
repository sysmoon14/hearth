"""Rooms, the boiler, and the neoHub."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import (
    KITCHEN,
    KITCHEN_ZONE,
    NEST,
    OFFICE,
    OFFICE_SENSOR,
    OFFICE_TRV,
    hearth_entry,
)

ROOM = "climate.office"


async def _setup(hass, calls, zones, **options):
    entry = hearth_entry(hass, zones, **options)
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "climate", {})
    assert await async_setup_component(hass, "select", {})
    calls.patch_climate()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_radiator_room_calls_for_heat(hass: HomeAssistant, house, calls, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])

    room = hass.states.get(ROOM)
    # Picked up the valve's own schedule: weekdays 07:00 19°, 10:00 16°.
    assert room.attributes["schedule"]["weekdays"][0] == {"time": "07:00", "temp": 19.0}
    assert room.state == "auto"
    assert room.attributes["temperature"] == 19.0
    assert room.attributes["current_temperature"] == 18.0  # the room sensor, not the valve
    assert room.attributes["hvac_action"] == "heating"
    assert room.attributes["reason"].startswith("19° until 10:00")

    # The valve is fed the room's temperature and told to use it.
    assert calls.of("number", "set_value")[-1] == {"entity_id": "number.office_trv_external_temperature_input", "value": 18.0}
    assert hass.states.get("select.office_trv_temperature_sensor_select").state == "remote_temperature"
    # The boiler is on.
    assert hass.states.get(NEST).attributes["temperature"] == 25.0
    assert hass.states.get("binary_sensor.hearth_radiators_want_heat").state == "on"

    # Warm room: boiler off.
    hass.states.async_set(OFFICE_SENSOR, "19.5")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(NEST).attributes["temperature"] == 10.0


async def test_plus_minus_overrides_until_next_change(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    await hass.services.async_call("climate", "set_temperature", {"entity_id": ROOM, "temperature": 21}, blocking=True)
    await hass.async_block_till_done()
    room = hass.states.get(ROOM)
    assert room.state == "auto"
    assert room.attributes["temperature"] == 21
    assert hass.states.get(OFFICE_TRV).attributes["temperature"] == 21
    assert "until 10:00, then the schedule" in room.attributes["reason"]

    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T10:00:30+00:00")))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(ROOM).attributes["temperature"] == 16


async def test_turning_the_valve_by_hand(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    freezer.tick(timedelta(seconds=30))
    state = hass.states.get(OFFICE_TRV)
    hass.states.async_set(OFFICE_TRV, "heat", {**state.attributes, "temperature": 22})
    await hass.async_block_till_done()
    assert hass.states.get(ROOM).attributes["temperature"] == 22


async def test_modes_boost_and_house(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": ROOM, "hvac_mode": "heat"}, blocking=True)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": ROOM, "temperature": 20}, blocking=True)
    assert hass.states.get(ROOM).attributes["reason"] == "Fixed at 20°"

    await hass.services.async_call("hearth", "boost", {"entity_id": ROOM}, blocking=True)
    room = hass.states.get(ROOM)
    assert room.attributes["preset_mode"] == "boost"
    assert room.attributes["temperature"] == 22

    await hass.services.async_call("select", "select_option", {"entity_id": "select.hearth_house_mode", "option": "holiday"}, blocking=True)
    assert hass.states.get(ROOM).attributes["temperature"] == 10
    await hass.services.async_call("select", "select_option", {"entity_id": "select.hearth_house_mode", "option": "away"}, blocking=True)
    await hass.services.async_call("hearth", "cancel_boost", {"entity_id": ROOM}, blocking=True)
    room = hass.states.get(ROOM)
    assert room.attributes["temperature"] == 16
    assert room.attributes["reason"] == "Away: held at 16°"

    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": ROOM, "hvac_mode": "off"}, blocking=True)
    assert hass.states.get(ROOM).attributes["temperature"] == 7


async def test_set_schedule(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    await hass.services.async_call(
        "hearth", "set_schedule",
        {"entity_id": ROOM, "weekdays": [{"time": "11:00", "temp": 20.5}, {"time": "18:00", "temp": 17}],
         "weekends": [{"time": "09:00", "temp": 19}]},
        blocking=True,
    )
    assert hass.states.get(ROOM).attributes["temperature"] == 20.5


async def test_underfloor_writes_profiles(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    assert hass.states.get(room).attributes["temperature"] == 21.0  # the stat's own target
    assert hass.states.get(room).attributes["profile"] == "HA Kitchen"

    await hass.services.async_call(
        "hearth", "set_schedule",
        {"entity_id": room, "weekdays": [{"time": "06:30", "temp": 21}, {"time": "20:30", "temp": 18}],
         "weekends": [{"time": "07:00", "temp": 21}, {"time": "21:00", "temp": 18}]},
        blocking=True,
    )
    written = calls.of("heatmiserneo", "create_profile_two")[-1]
    assert written["name"] == "HA Kitchen"
    assert written["monday_times"] == ["06:30", "20:30"]
    assert written["monday_temperatures"] == ["21", "18"]
    assert calls.of("select", "select_option")[-1] == {"entity_id": "select.kitchen_active_profile", "option": "HA Kitchen"}

    # Fixed writes a one-level profile.
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "heat"}, blocking=True)
    written = calls.of("heatmiserneo", "create_profile_two")[-1]
    assert written["name"] == "HA Kitchen Fixed"
    assert written["monday_times"] == ["00:00"]

    # Boost holds on the stat; Away holds too, Holiday uses the hub.
    await hass.services.async_call("hearth", "boost", {"entity_id": room}, blocking=True)
    assert calls.of("heatmiserneo", "hold_on")[-1]["hold_temperature"] == 22.0
    await hass.services.async_call("select", "select_option", {"entity_id": "select.hearth_house_mode", "option": "holiday"}, blocking=True)
    assert calls.of("heatmiserneo", "set_away_mode")[-1] == {"entity_id": "binary_sensor.neohub_away", "away": True}

    # Underfloor rooms don't switch the boiler.
    assert hass.states.get(NEST).attributes["temperature"] == 18.0


async def test_unload_leaves_boiler_safe(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    entry = await _setup(hass, calls, [OFFICE])
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.states.get(NEST).attributes["temperature"] == 17.0
