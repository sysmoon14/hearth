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
    RELAY,
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
    # The boiler is asked for heat.
    assert hass.states.get(RELAY).state == "on"
    assert hass.states.get("binary_sensor.hearth_radiators_want_heat").state == "on"

    # Warm room: boiler off.
    hass.states.async_set(OFFICE_SENSOR, "19.5")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "off"


async def test_plus_minus_overrides_until_next_change(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    await hass.services.async_call("climate", "set_temperature", {"entity_id": ROOM, "temperature": 21}, blocking=True)
    await hass.async_block_till_done()
    room = hass.states.get(ROOM)
    assert room.state == "auto"
    assert room.attributes["temperature"] == 21
    assert hass.states.get(OFFICE_TRV).attributes["temperature"] == 21
    assert room.attributes["reason"] == "21° until 10:00, then 16°"

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

    # Off is the stat's own standby; back to Schedule leaves it.
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "off"}, blocking=True)
    assert hass.states.get(KITCHEN).attributes["preset_mode"] == "standby"
    assert hass.states.get(room).attributes["reason"].startswith("Off")
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "auto"}, blocking=True)
    assert hass.states.get(KITCHEN).attributes["preset_mode"] == "home"
    assert calls.of("select", "select_option")[-1]["option"] == "HA Kitchen"

    # Boost holds on the stat; Away holds too, Holiday uses the hub.
    await hass.services.async_call("hearth", "boost", {"entity_id": room}, blocking=True)
    assert calls.of("heatmiserneo", "hold_on")[-1]["hold_temperature"] == 22.0
    await hass.services.async_call("select", "select_option", {"entity_id": "select.hearth_house_mode", "option": "holiday"}, blocking=True)
    assert calls.of("heatmiserneo", "set_away_mode")[-1] == {"entity_id": "binary_sensor.neohub_away", "away": True}

    # Underfloor rooms don't call for heat through Hearth.
    assert hass.states.get(RELAY).state == "off"


async def test_the_switch_follows_the_rooms(hass: HomeAssistant, house, calls, freezer) -> None:
    """On while a room wants heat, off otherwise; unavailable, nothing to do."""
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    assert calls.of("switch", "turn_on") == [{"entity_id": RELAY}]

    hass.states.async_set(OFFICE_SENSOR, "19.5")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "off"

    hass.states.async_set(RELAY, "unavailable")
    hass.states.async_set(OFFICE_SENSOR, "17.0")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "unavailable"

    hass.states.async_set(RELAY, "off")
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "on"


async def test_a_reload_keeps_calling_for_heat(hass: HomeAssistant, house, calls, freezer) -> None:
    """Saving a setting reloads Hearth: the call for heat carries on, with no
    wait for the on delay, rather than dropping out."""
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    entry = await _setup(hass, calls, [OFFICE], demand_on_delay=60)
    freezer.tick(timedelta(seconds=65))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "on"
    offs = len(calls.of("switch", "turn_off"))

    hass.config_entries.async_update_entry(entry, options={**entry.options, "away_temp": 15})
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "on"
    freezer.tick(timedelta(seconds=40))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "on"
    assert len(calls.of("switch", "turn_off")) == offs


async def test_unloading_turns_the_switch_off_unless_hearth_comes_back(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    entry = await _setup(hass, calls, [OFFICE])
    assert hass.states.get(RELAY).state == "on"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.states.get(RELAY).state == "on"  # a moment to come back
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "off"


async def test_home_assistant_stopping_turns_the_switch_off(hass: HomeAssistant, house, calls, freezer) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    assert hass.states.get(RELAY).state == "on"
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert hass.states.get(RELAY).state == "off"


async def test_underfloor_shows_what_was_asked_while_the_stat_catches_up(hass: HomeAssistant, house, calls, freezer) -> None:
    """The neoHub reports a stat's setpoint late: leaving Off isn't taken for
    a change on the stat, and + shows at once, until the stat catches up."""
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "off"}, blocking=True)

    def lagging():
        stat = hass.states.get(KITCHEN)
        hass.states.async_set(KITCHEN, stat.state, {**stat.attributes, "temperature": 12.0})

    # Back to Schedule: the stat still reports its frost level.
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "auto"}, blocking=True)
    lagging()
    await hass.async_block_till_done()
    state = hass.states.get(room)
    assert not state.attributes["reason"].startswith("Changed on the thermostat"), state.attributes["reason"]
    scheduled = state.attributes["temperature"]
    assert scheduled != 12.0

    # + : shown straight away though the stat hasn't reported it yet.
    await hass.services.async_call("climate", "set_temperature", {"entity_id": room, "temperature": scheduled + 1}, blocking=True)
    lagging()
    await hass.async_block_till_done()
    assert hass.states.get(room).attributes["temperature"] == scheduled + 1

    # The stat dropped it: Hearth sends it again, and the stat takes it.
    freezer.tick(timedelta(seconds=130))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(KITCHEN).attributes["temperature"] == scheduled + 1
    assert hass.states.get(room).attributes["temperature"] == scheduled + 1


async def test_underfloor_taps_reach_the_stat_as_one_write(hass: HomeAssistant, house, calls, freezer) -> None:
    """A run of + taps answers at once and sends the stat only the last."""
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    start = hass.states.get(room).attributes["temperature"]
    before = len(calls.of("climate", "set_temperature"))
    for n in range(1, 9):
        await hass.services.async_call("climate", "set_temperature", {"entity_id": room, "temperature": start + n / 2})
    await hass.async_block_till_done()
    to_stat = [c for c in calls.of("climate", "set_temperature")[before:] if c["entity_id"] == KITCHEN]
    assert [c["temperature"] for c in to_stat] == [start + 4]
    assert hass.states.get(room).attributes["temperature"] == start + 4


async def test_every_room_says_how_long_it_will_take(hass: HomeAssistant, house, calls, freezer) -> None:
    from custom_components.hearth.preheat import WarmUp

    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    entry = await _setup(hass, calls, [OFFICE])
    room = hass.states.get(ROOM)
    assert room.attributes["status"] == "Heating to 19° · still learning how long it takes"
    assert room.attributes["minutes_to_target"] is None

    zone = entry.runtime_data.zones["office"]
    for n in range(2):
        zone.learn(WarmUp(1.0, 60, dt_util.utcnow() - timedelta(days=n + 1), "seen"))
    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    room = hass.states.get(ROOM)
    # 1° to go at 1° an hour.
    assert room.attributes["status"] == "Heating to 19° · about 1 h"
    assert room.attributes["minutes_to_target"] == 60

    hass.states.async_set(OFFICE_SENSOR, "19.5")
    freezer.tick(timedelta(seconds=35))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    room = hass.states.get(ROOM)
    assert room.attributes["status"] == "Comfortable"
    assert room.attributes["minutes_to_target"] is None

    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": ROOM, "hvac_mode": "off"}, blocking=True)
    assert hass.states.get(ROOM).attributes["status"] == "Off"


async def test_an_underfloor_room_warming_up_early_says_so(hass: HomeAssistant, house, calls, freezer) -> None:
    """The stat's own early start reads the same as a radiator room's."""
    # Friday 15:00; the stat's level is 19° and the schedule goes to 21° at 16:30.
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T15:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    await hass.services.async_call(
        "hearth", "set_schedule",
        {"entity_id": room, "weekdays": [{"time": "06:30", "temp": 21}, {"time": "08:00", "temp": 19}, {"time": "16:30", "temp": 21}],
         "weekends": [{"time": "07:00", "temp": 21}]},
        blocking=True,
    )
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=130))  # past the settle time
    hass.states.async_set(KITCHEN, "heat", {"temperature": 19.0, "current_temperature": 18.5, "hvac_action": "preheating"})
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get(room)
    assert state.attributes["hvac_action"] == "preheating"
    assert state.attributes["preheating"] is True
    assert state.attributes["status"] == "Warming up early · 21° by 16:30"
    assert state.attributes["reason"] == "Warming up early for 21° by 16:30"

    # It learns from the warm-up, as radiator rooms do.
    hass.states.async_set(KITCHEN, "heat", {"temperature": 21.0, "current_temperature": 21.0, "hvac_action": "idle"})
    freezer.tick(timedelta(minutes=50))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(room).attributes["warm_ups"] == 1


async def test_removing_a_room_removes_its_device(hass: HomeAssistant, house, calls, freezer) -> None:
    from homeassistant.helpers import device_registry as dr

    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    entry = await _setup(hass, calls, [OFFICE, KITCHEN_ZONE])
    registry = dr.async_get(hass)
    office = registry.async_get_device(identifiers={("hearth", f"{entry.entry_id}_office")})
    assert office is not None
    hass.config_entries.async_update_entry(entry, options={**entry.options, "zones": [KITCHEN_ZONE]})
    await hass.async_block_till_done()
    assert registry.async_get_device(identifiers={("hearth", f"{entry.entry_id}_office")}) is None
    assert registry.async_get_device(identifiers={("hearth", f"{entry.entry_id}_kitchen")}) is not None
    assert registry.async_get_device(identifiers={("hearth", entry.entry_id)}) is not None


async def _tick(hass: HomeAssistant, freezer, **delta) -> None:
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _valve(hass: HomeAssistant, action: str) -> None:
    trv = hass.states.get(OFFICE_TRV)
    hass.states.async_set(OFFICE_TRV, trv.state, {**trv.attributes, "hvac_action": action})


async def test_a_room_stops_asking_at_its_target(hass: HomeAssistant, house, calls, freezer) -> None:
    # 19° from 07:00. Calling from 18°; at 19.0° exactly it stops, not at 19.2°.
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    assert hass.states.get(ROOM).attributes["calling_for_heat"]
    hass.states.async_set(OFFICE_SENSOR, "19.0")
    await _tick(hass, freezer, seconds=35)
    assert not hass.states.get(ROOM).attributes["calling_for_heat"]
    assert hass.states.get(RELAY).state == "off"


async def test_a_room_whose_valves_shut_stops_asking(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    # Warming, nearly there (18.8° for 19°): still asking while the valve's open.
    _valve(hass, "heating")
    hass.states.async_set(OFFICE_SENSOR, "18.8")
    await _tick(hass, freezer, seconds=35)
    assert hass.states.get(ROOM).attributes["calling_for_heat"]

    # The valve shuts: a few minutes' grace, then the room stops asking.
    _valve(hass, "idle")
    await _tick(hass, freezer, seconds=35)
    await _tick(hass, freezer, minutes=3)
    assert hass.states.get(ROOM).attributes["calling_for_heat"]
    await _tick(hass, freezer, minutes=3)
    assert not hass.states.get(ROOM).attributes["calling_for_heat"]
    assert hass.states.get(RELAY).state == "off"

    # Cooling past the band asks again, valve shut or not.
    hass.states.async_set(OFFICE_SENSOR, "18.6")
    await _tick(hass, freezer, seconds=35)
    assert hass.states.get(ROOM).attributes["calling_for_heat"]


def _stat_preset(hass: HomeAssistant, preset: str) -> None:
    stat = hass.states.get(KITCHEN)
    hass.states.async_set(KITCHEN, stat.state, {**stat.attributes, "preset_mode": preset})


async def test_underfloor_turned_off_and_on_at_the_stat(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    _stat_preset(hass, "home")
    await _tick(hass, freezer, minutes=3)  # past anything Hearth sent at start
    assert hass.states.get(room).attributes["mode"] == "schedule"
    writes = len(calls.of("heatmiserneo", "create_profile_two"))

    # Someone presses power on the stat: the room is Off straight away, and
    # Hearth doesn't fight it.
    _stat_preset(hass, "standby")
    await hass.async_block_till_done()
    state = hass.states.get(room)
    assert state.state == "off" and state.attributes["mode"] == "off"
    assert state.attributes["reason"] == "Off: turned off on the thermostat"
    assert state.attributes["status"] == "Off"
    assert len(calls.of("heatmiserneo", "create_profile_two")) == writes

    # And back on at the stat: the room's on its schedule again, profile rewritten.
    await _tick(hass, freezer, minutes=1)
    _stat_preset(hass, "home")
    await hass.async_block_till_done()
    state = hass.states.get(room)
    assert state.attributes["mode"] == "schedule"
    assert len(calls.of("heatmiserneo", "create_profile_two")) == writes + 1
    assert calls.of("select", "select_option")[-1]["option"] == "HA Kitchen"


async def test_underfloor_off_from_hearth_isnt_taken_for_the_stat(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "off"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(room).attributes["reason"] == "Off: the thermostat keeps frost protection"
    # Hearth turns it back on; the stat's late report of standby is ignored.
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "auto"}, blocking=True)
    _stat_preset(hass, "standby")
    await hass.async_block_till_done()
    assert hass.states.get(room).attributes["mode"] == "schedule"


def _sensor(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(OFFICE_SENSOR, value, {"device_class": "temperature"})


async def test_a_flickering_sensor_isnt_sent_to_the_valve_each_time(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    hub = (await _setup(hass, calls, [OFFICE])).runtime_data
    sent = lambda: [c["value"] for c in calls.of("number", "set_value")]  # noqa: E731
    assert sent() == [18.0]

    async def reads(value: str, seconds: int) -> None:
        freezer.tick(timedelta(seconds=seconds))
        _sensor(hass, value)
        await hub.async_evaluate()

    # 0.1° either way, every few seconds: nothing goes to the valve.
    for value in ("18.1", "18.0", "18.1", "18.0", "18.1"):
        await reads(value, 5)
    assert sent() == [18.0]
    assert hass.states.get(ROOM).attributes["current_temperature"] == 18.0  # nor shown, nor recorded
    # It stays moved: taken once it has settled.
    await reads("18.1", 65)
    assert sent() == [18.0, 18.1]
    # A real move goes straight away.
    await reads("18.3", 5)
    assert sent() == [18.0, 18.1, 18.3]


async def test_a_sensor_that_stops_reporting_is_let_go(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    _sensor(hass, "18.0")  # its last report, at 07:30
    await _setup(hass, calls, [OFFICE])
    select = "select.office_trv_temperature_sensor_select"
    assert hass.states.get(select).state == "remote_temperature"
    assert hass.states.get(ROOM).attributes["current_temperature"] == 18.0
    assert hass.states.get(RELAY).state == "on"

    # Its battery dies at 18°: seven hours on it still says 18°.
    await _tick(hass, freezer, minutes=7 * 60)
    room = hass.states.get(ROOM)
    assert room.attributes["room_sensor_stale"] is True
    assert room.attributes["current_temperature"] == 22.7  # the valve's own reading
    assert hass.states.get(RELAY).state == "off"
    # Still gone a quarter of an hour later: the valve goes back to its own sensor.
    assert hass.states.get(select).state == "remote_temperature"
    await _tick(hass, freezer, minutes=16)
    assert hass.states.get(select).state == "local_temperature"

    # A new battery: the room's sensor is used again.
    _sensor(hass, "18.2")
    await _tick(hass, freezer, seconds=35)
    room = hass.states.get(ROOM)
    assert room.attributes["room_sensor_stale"] is False
    assert room.attributes["current_temperature"] == 18.2
    assert hass.states.get(select).state == "remote_temperature"


async def test_plus_during_an_underfloor_boost_changes_the_hold(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    await hass.services.async_call("hearth", "boost", {"entity_id": room}, blocking=True)
    assert calls.of("heatmiserneo", "hold_on")[-1]["hold_duration"] == "01:00:00"
    await _tick(hass, freezer, minutes=20)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": room, "temperature": 23.5}, blocking=True)
    hold = calls.of("heatmiserneo", "hold_on")[-1]
    assert hold["hold_temperature"] == 23.5
    assert hold["hold_duration"] == "00:40:00"  # what's left of the boost
    state = hass.states.get(room)
    assert state.attributes["reason"].startswith("Boost to 23.5°")
    assert state.attributes["temperature"] == 23.5


async def test_away_on_underfloor_lasts_and_follows_the_schedule(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE], away_temp=18)
    room = "climate.kitchen_2"
    holds = lambda: calls.of("heatmiserneo", "hold_on")  # noqa: E731
    await hass.services.async_call("select", "select_option", {"entity_id": "select.hearth_house_mode", "option": "away"}, blocking=True)
    # The schedule's 16° is already cooler than away's 18°: held there.
    assert [h["hold_temperature"] for h in holds()] == [16.0]
    # 17:00, the schedule goes to 19°: held at 18°.
    await _tick(hass, freezer, minutes=5 * 60 + 1)
    assert [h["hold_temperature"] for h in holds()] == [16.0, 18.0]
    # Still away the next morning: the hold has been set again, not left to run out.
    before = len(holds())
    await _tick(hass, freezer, minutes=13 * 60)
    assert len(holds()) > before

    # Turned off and back on while away: the room is held again.
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "off"}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "auto"}, blocking=True)
    await hass.async_block_till_done()
    before = len(holds())
    await _tick(hass, freezer, seconds=35)
    assert len(holds()) == before + 1

    # Home again: the hold comes off and isn't put back.
    await hass.services.async_call("select", "select_option", {"entity_id": "select.hearth_house_mode", "option": "home"}, blocking=True)
    assert calls.of("heatmiserneo", "hold_off")
    before = len(holds())
    await _tick(hass, freezer, minutes=2)
    assert len(holds()) == before


async def test_a_stat_catching_up_with_the_schedule_isnt_a_change_on_the_stat(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T16:50:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    stat = hass.states.get(KITCHEN)
    hass.states.async_set(KITCHEN, stat.state, {**stat.attributes, "temperature": 16.0})
    await _tick(hass, freezer, minutes=5)
    assert hass.states.get(room).attributes["reason"].startswith("16° until 17:00")
    # 17:00:30: the schedule says 19°, the stat hasn't reported it yet.
    await _tick(hass, freezer, minutes=5, seconds=30)
    assert hass.states.get(room).attributes["reason"].startswith("19° until")
    # Ten minutes on and still 16°: that is someone at the stat.
    await _tick(hass, freezer, minutes=10)
    assert hass.states.get(room).attributes["reason"].startswith("Changed on the thermostat to 16°")


async def test_a_hearth_that_has_stopped_leaves_the_house_alone(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    entry = await _setup(hass, calls, [OFFICE])
    old = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not old and old.stopped
    before = len(calls.log)
    await old.async_evaluate()  # as a profile write finishing late would
    assert len(calls.log) == before


async def test_history_warm_ups_from_before_are_dropped(hass: HomeAssistant, house, calls, freezer, hass_storage) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    ups = [
        {"rise": 1.0, "minutes": 110, "at": "2026-11-10T06:00:00+00:00", "source": "history"},
        {"rise": 1.0, "minutes": 105, "at": "2026-11-11T06:00:00+00:00", "source": "history"},
        {"rise": 2.0, "minutes": 100, "at": "2026-11-12T06:00:00+00:00", "source": "seen"},
    ]
    hass_storage["hearth"] = {
        "version": 1, "minor_version": 1, "key": "hearth",
        "data": {"house_mode": "home", "zones": {"office": {"warmups": ups, "seeded": True, "learned_with": OFFICE_SENSOR}}},
    }
    await _setup(hass, calls, [OFFICE])
    room = hass.states.get(ROOM)
    assert room.attributes["warm_ups"] == 1 and room.attributes["warm_ups_from_history"] == 0
    assert room.attributes["warm_up_rate"] is None  # learning again, from what it sees


async def test_a_room_with_no_valve_online_doesnt_ask(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE])
    assert hass.states.get(RELAY).state == "on"
    assert hass.states.get(ROOM).attributes["problem"] is None

    # The valve's battery dies with the room still cold.
    valve = hass.states.get(OFFICE_TRV)
    hass.states.async_set(OFFICE_TRV, "unavailable")
    await _tick(hass, freezer, seconds=35)
    room = hass.states.get(ROOM)
    assert room.attributes["problem"] == "Can't heat: the valve is offline"
    assert room.attributes["calling_for_heat"] is False
    assert hass.states.get(RELAY).state == "off"

    # Back again: the room asks for heat as before.
    hass.states.async_set(OFFICE_TRV, valve.state, valve.attributes)
    await _tick(hass, freezer, seconds=35)
    assert hass.states.get(ROOM).attributes["problem"] is None
    assert hass.states.get(RELAY).state == "on"


async def test_boosting_an_underfloor_room_thats_off(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [KITCHEN_ZONE])
    room = "climate.kitchen_2"
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": room, "hvac_mode": "off"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(KITCHEN).attributes["preset_mode"] == "standby"

    # The stat ignores a hold in standby, so it comes out for the boost.
    await hass.services.async_call("hearth", "boost", {"entity_id": room}, blocking=True)
    assert hass.states.get(KITCHEN).attributes["preset_mode"] == "home"
    assert calls.of("heatmiserneo", "hold_on")[-1]["hold_temperature"] == 22.0
    state = hass.states.get(room)
    assert state.state == "off" and state.attributes["preset_mode"] == "boost"

    # And goes back when the boost runs out: the room is still Off.
    await _tick(hass, freezer, minutes=61)
    assert hass.states.get(KITCHEN).attributes["preset_mode"] == "standby"
    state = hass.states.get(room)
    assert state.state == "off" and state.attributes["status"] == "Off"


async def test_an_offline_stat_or_sensor_is_a_problem(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T12:00:00+00:00")))
    await _setup(hass, calls, [OFFICE, KITCHEN_ZONE])
    hass.states.async_set(KITCHEN, "unavailable")
    hass.states.async_set(OFFICE_SENSOR, "unavailable")
    await _tick(hass, freezer, seconds=35)
    assert hass.states.get("climate.kitchen_2").attributes["problem"] == "The thermostat is offline"
    assert hass.states.get(ROOM).attributes["problem"] == "The room sensor isn't reporting: using the valve's own reading"


async def test_one_room_going_wrong_doesnt_stop_the_house(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    entry = await _setup(hass, calls, [OFFICE, KITCHEN_ZONE])
    assert hass.states.get(RELAY).state == "on"

    def broken(_now):
        raise RuntimeError("the kitchen stat sent something Hearth can't read")

    entry.runtime_data.zones["kitchen"].update_demand = broken
    # The office warms up: the boiler is still turned off.
    _sensor(hass, "19.5")
    await _tick(hass, freezer, seconds=35)
    assert hass.states.get(RELAY).state == "off"
