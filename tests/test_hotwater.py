"""Hot water: heated in the gaps the radiators leave."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hearth import hotwater

from .conftest import OFFICE, OFFICE_SENSOR, OFFICE_TRV, RELAY, hearth_entry

HW = "switch.boiler_call_for_hot_water"
TANK = "water_heater.hearth_hot_water"
PROBE = "sensor.cylinder_temperature"
LONDON = ZoneInfo("Europe/London")


# --- the sums ----------------------------------------------------------------
def test_ready_times_are_read_and_ordered() -> None:
    assert hotwater.parse_times(["18:00", "06:30", "nonsense", "25:00", "06:30"]) == [time(6, 30), time(18, 0)]
    assert hotwater.parse_times(None) == []


def test_the_tank_is_due_inside_the_window_before_a_ready_time() -> None:
    times = [time(6, 30), time(18, 0)]
    window = timedelta(hours=6)
    at = lambda h, m: datetime(2026, 11, 16, h, m, tzinfo=LONDON)  # noqa: E731
    assert hotwater.due(at(0, 29), times, window) is None
    assert hotwater.due(at(0, 30), times, window) == at(6, 30)
    assert hotwater.due(at(6, 29), times, window) == at(6, 30)
    assert hotwater.due(at(6, 30), times, window) is None  # the next one is 18:00
    assert hotwater.due(at(12, 0), times, window) == at(18, 0)
    # Late evening: tomorrow's first time is the next one.
    assert hotwater.next_ready(at(23, 0), times) == datetime(2026, 11, 17, 6, 30, tzinfo=LONDON)
    assert hotwater.next_ready(at(23, 0), []) is None


def test_a_run_doesnt_start_just_before_a_warm_up() -> None:
    now = datetime(2026, 11, 16, 16, 45, tzinfo=LONDON)
    clear = timedelta(minutes=20)
    assert hotwater.may_start(True, None, now, clear)
    assert hotwater.may_start(True, now + timedelta(minutes=45), now, clear)
    assert not hotwater.may_start(True, now + timedelta(minutes=15), now, clear)
    assert not hotwater.may_start(False, None, now, clear)


# --- in the house ------------------------------------------------------------
async def _setup(hass, calls, **options):
    from homeassistant.setup import async_setup_component

    hass.states.async_set(HW, "off")
    entry = hearth_entry(hass, [OFFICE], hot_water_switch=HW, **options)
    assert await async_setup_component(hass, "climate", {})
    assert await async_setup_component(hass, "select", {})
    calls.patch_climate()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _pass(hass, freezer, seconds=35, times=1):
    for _ in range(times):
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _at(freezer, when: str) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime(when)))


async def test_heats_when_the_radiators_are_quiet_and_stops_when_one_asks(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T01:00:00+00:00")  # Monday, in the window before 06:30
    await _setup(hass, calls)
    # The office is on 16° overnight and reads 18°: nothing asks for heat.
    assert hass.states.get(RELAY).state == "off"
    assert hass.states.get(HW).state == "on"
    tank = hass.states.get(TANK)
    assert tank.state == "heating"
    assert tank.attributes["status"].startswith("Heating for 06:30")
    assert tank.attributes["set_by_hearth"] is True

    # The office gets cold and asks: the hot water gives way at once.
    hass.states.async_set(OFFICE_SENSOR, "15.0")
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(RELAY).state == "on"
    assert hass.states.get(HW).state == "off"
    tank = hass.states.get(TANK)
    assert tank.state == "waiting"
    assert tank.attributes["status"] == "Waiting for the radiators: Office is heating"

    # Warm again: after the radiators have settled, the hot water carries on.
    hass.states.async_set(OFFICE_SENSOR, "18.0")
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(RELAY).state == "off"
    assert hass.states.get(HW).state == "off"  # resting
    await _pass(hass, freezer, times=10)
    assert hass.states.get(HW).state == "on"


async def test_starts_while_the_boiler_is_still_held_on_for_the_radiators(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T01:00:00+00:00")
    hass.states.async_set(OFFICE_SENSOR, "15.0")  # the office is asking for heat
    await _setup(hass, calls, demand_off_delay=180)
    assert hass.states.get(RELAY).state == "on"
    assert hass.states.get(HW).state == "off"

    # The office is warm: Hearth holds the call for heat for three minutes,
    # and the hot water starts under it, so the boiler never stops.
    hass.states.async_set(OFFICE_SENSOR, "18.0")
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(RELAY).state == "on"
    assert hass.states.get(HW).state == "on"
    assert hass.states.get(TANK).state == "heating"

    # The hold ends and the hot water carries on.
    await _pass(hass, freezer, times=6)
    assert hass.states.get(RELAY).state == "off"
    assert hass.states.get(HW).state == "on"


async def test_stops_once_the_tank_has_had_its_minutes(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T01:00:00+00:00")
    entry = await _setup(hass, calls, hot_water_minutes=15)
    assert hass.states.get(HW).state == "on"
    await _pass(hass, freezer, seconds=30, times=32)
    assert hass.states.get(HW).state == "off"
    tank = hass.states.get(TANK)
    assert tank.state == "idle"
    assert tank.attributes["status"] == "Heated for 06:30"
    assert 15 <= entry.runtime_data.hot_water.state.delivered < 16.5
    # And it isn't started again for the same time.
    await _pass(hass, freezer, times=5)
    assert hass.states.get(HW).state == "off"


async def test_outside_the_window_it_waits_for_the_next_time(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T08:00:00+00:00")
    hass.states.async_set(OFFICE_SENSOR, "20.0")
    await _setup(hass, calls)
    assert hass.states.get(HW).state == "off"
    tank = hass.states.get(TANK)
    assert tank.state == "idle"
    assert tank.attributes["status"] == "Next heats from 12:00, for 18:00"


async def test_a_boost_runs_for_its_minutes_whatever_the_radiators(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T08:00:00+00:00")
    hass.states.async_set(OFFICE_SENSOR, "15.0")  # the office is asking for heat
    entry = await _setup(hass, calls)
    assert hass.states.get(RELAY).state == "on"
    await hass.services.async_call("hearth", "boost_hot_water", {"minutes": 45}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(HW).state == "on"
    await _pass(hass, freezer, times=3)
    assert hass.states.get(HW).state == "on"
    tank = hass.states.get(TANK)
    assert tank.state == "heating"
    assert tank.attributes["status"] == "On by hand until 08:45 · sharing the boiler with the radiators"
    assert tank.attributes["set_by_hearth"] is False
    assert tank.attributes["boost_until"] is not None

    # Its time is up: Hearth turns it off and carries on (not a hold).
    await _pass(hass, freezer, seconds=60, times=45)
    assert hass.states.get(HW).state == "off"
    st = entry.runtime_data.hot_water.state
    assert st.boost_until is None and not st.by_hand and st.held_since is None
    assert hass.states.get(TANK).state == "idle"


async def test_on_by_hand_without_a_sensor_runs_for_the_minutes(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T08:00:00+00:00")
    hass.states.async_set(OFFICE_SENSOR, "15.0")  # the office is asking for heat
    await _setup(hass, calls)
    hass.states.async_set(HW, "on")
    await hass.async_block_till_done()
    await _pass(hass, freezer, times=3)
    assert hass.states.get(HW).state == "on"
    assert hass.states.get(TANK).attributes["status"] == "On by hand until 09:00 · sharing the boiler with the radiators"
    await _pass(hass, freezer, seconds=60, times=60)
    assert hass.states.get(HW).state == "off"
    assert hass.states.get(TANK).attributes["off_by_hand_since"] is None


async def test_on_by_hand_heats_to_full_then_hearth_carries_on(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T08:00:00+00:00")
    hass.states.async_set(OFFICE_SENSOR, "15.0")  # the office is asking for heat
    hass.states.async_set(PROBE, "52.0", {"device_class": "temperature"})
    entry = await _setup(hass, calls, hot_water_sensor=PROBE, hot_water_reheat_below=50)
    assert hass.states.get(HW).state == "off"  # hot enough: nothing to do

    hass.states.async_set(HW, "on")
    await hass.async_block_till_done()
    await _pass(hass, freezer, times=3)
    assert hass.states.get(HW).state == "on"
    tank = hass.states.get(TANK)
    assert tank.attributes["status"] == "On by hand · heating to 55°, 52° now · sharing the boiler with the radiators"
    assert tank.attributes["on_by_hand"] is True

    # Full: it turns off, and that isn't taken as somebody turning it off.
    hass.states.async_set(PROBE, "55.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer, times=2)
    assert hass.states.get(HW).state == "off"
    st = entry.runtime_data.hot_water.state
    assert not st.by_hand and st.held_since is None
    assert hass.states.get(TANK).attributes["status"] == "Hot · 55°"


async def test_off_by_hand_stays_off_until_handed_back(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T01:00:00+00:00")
    hass.states.async_set(PROBE, "38.0", {"device_class": "temperature"})
    entry = await _setup(hass, calls, hot_water_sensor=PROBE)
    assert hass.states.get(HW).state == "on"  # Hearth is heating it

    hass.states.async_set(HW, "off")  # somebody turns it off
    await hass.async_block_till_done()
    await _pass(hass, freezer, times=10)
    assert hass.states.get(HW).state == "off"  # even below "too cold to wait"
    tank = hass.states.get(TANK)
    assert tank.state == "off"
    assert tank.attributes["status"] == "Off by hand since 01:00"
    assert tank.attributes["problem"] is None

    # A day on, it's a problem worth seeing.
    await _pass(hass, freezer, seconds=3600, times=24)
    assert hass.states.get(HW).state == "off"
    assert hass.states.get(TANK).attributes["problem"] == (
        "The hot water has been off by hand since Mon 01:00: hand it back to Hearth to heat the tank again")

    await hass.services.async_call("hearth", "resume_hot_water", {}, blocking=True)
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(HW).state == "on"
    assert entry.runtime_data.hot_water.state.held_since is None


async def test_stop_keeps_it_off_and_turning_it_on_hands_back(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T01:00:00+00:00")
    hass.states.async_set(PROBE, "43.0", {"device_class": "temperature"})
    entry = await _setup(hass, calls, hot_water_sensor=PROBE)
    assert hass.states.get(HW).state == "on"
    await hass.services.async_call("hearth", "stop_hot_water", {}, blocking=True)
    await hass.async_block_till_done()
    await _pass(hass, freezer, times=5)
    assert hass.states.get(HW).state == "off"

    # On by hand: it heats to full, then Hearth is back in charge.
    hass.states.async_set(HW, "on")
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    st = entry.runtime_data.hot_water.state
    assert st.by_hand and st.held_since is None
    hass.states.async_set(PROBE, "56.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer, times=2)
    assert hass.states.get(HW).state == "off"
    assert not st.by_hand and st.held_since is None


async def test_off_when_the_switch_is_unavailable(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T01:00:00+00:00")
    hass.states.async_set(HW, "unavailable")
    entry = hearth_entry(hass, [OFFICE], hot_water_switch=HW)
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "climate", {})
    assert await async_setup_component(hass, "select", {})
    calls.patch_climate()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    tank = hass.states.get(TANK)
    assert tank.state == "off"
    assert tank.attributes["problem"] == "Can't heat the hot water: the Call for hot water switch is unavailable"
    assert not calls.of("switch", "turn_on") or all(c["entity_id"] != HW for c in calls.of("switch", "turn_on"))


async def test_the_old_boost_setting_is_dropped(hass: HomeAssistant, house, calls) -> None:
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="hearth", title="Hearth", data={}, minor_version=2,
                            options={"boiler_switch": RELAY, "zones": [], "hot_water_boost": "select.nest_hot_water_boost"})
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "climate", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 3
    assert "hot_water_boost" not in entry.options
    assert entry.runtime_data.hot_water is None


async def test_doesnt_start_when_a_room_is_about_to_warm_up(hass: HomeAssistant, house, calls, freezer) -> None:
    # The office's own schedule goes to 19° at 17:00; it's at 16° now.
    _at(freezer, "2026-11-16T16:45:00+00:00")
    hass.states.async_set(OFFICE_SENSOR, "16.0")
    await _setup(hass, calls)
    assert hass.states.get(RELAY).state == "off"
    assert hass.states.get(HW).state == "off"
    tank = hass.states.get(TANK)
    assert tank.state == "waiting"
    assert tank.attributes["status"] == "Waiting: a room starts warming up at 17:00"


async def test_with_a_tank_sensor_it_heats_by_temperature(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T08:00:00+00:00")  # outside any window: the sensor decides
    hass.states.async_set(OFFICE_SENSOR, "20.0")
    hass.states.async_set(PROBE, "41.0", {"device_class": "temperature"})
    await _setup(hass, calls, hot_water_sensor=PROBE)
    assert hass.states.get(HW).state == "on"
    tank = hass.states.get(TANK)
    assert tank.state == "heating"
    assert tank.attributes["current_temperature"] == 41.0
    assert tank.attributes["status"] == "Heating to 55° · 41° now"

    # Between the two temperatures on the way up: it keeps going.
    hass.states.async_set(PROBE, "50.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(HW).state == "on"

    hass.states.async_set(PROBE, "55.5", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(HW).state == "off"
    assert hass.states.get(TANK).attributes["status"] == "Hot · 56°"

    # Between the two on the way down: it waits for the lower one.
    hass.states.async_set(PROBE, "48.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer, times=12)
    assert hass.states.get(HW).state == "off"


async def test_no_tank_entity_without_a_switch(hass: HomeAssistant, house, calls, freezer) -> None:
    from homeassistant.setup import async_setup_component

    _at(freezer, "2026-11-16T01:00:00+00:00")
    entry = hearth_entry(hass, [OFFICE])
    assert await async_setup_component(hass, "climate", {})
    assert await async_setup_component(hass, "select", {})
    calls.patch_climate()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TANK) is None
    assert entry.runtime_data.hot_water is None


async def test_a_room_that_stalls_stops_asking_and_learns_from_the_rise(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-13T07:30:00+00:00")  # the office wants 19°
    hass.states.async_set(OFFICE_SENSOR, "17.0")
    entry = hearth_entry(hass, [OFFICE])
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "climate", {})
    assert await async_setup_component(hass, "select", {})
    calls.patch_climate()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    zone = entry.runtime_data.zones["office"]
    assert hass.states.get(RELAY).state == "on"
    # The valve says it's open throughout, as Rosa's did.
    valve = hass.states.get(OFFICE_TRV)
    hass.states.async_set(OFFICE_TRV, valve.state, {**valve.attributes, "hvac_action": "heating"})

    # It warms to 18.8° over an hour, then gets no warmer.
    for temp in ("17.6", "18.2", "18.8"):
        await _pass(hass, freezer, seconds=30, times=40)
        hass.states.async_set(OFFICE_SENSOR, temp)
        await hass.async_block_till_done()
    await _pass(hass, freezer, seconds=30, times=20)
    assert hass.states.get(RELAY).state == "on"  # ten minutes without rising isn't a stall
    await _pass(hass, freezer, seconds=30, times=42)
    # Half an hour within 0.3° of its target and no warmer: it stops asking.
    assert hass.states.get(RELAY).state == "off"
    assert hass.states.get("climate.office").attributes["status"] == "Up to temperature"
    # What it learned is the rise, not the wait after it.
    (warmup,) = zone.state.warmups
    assert round(warmup.rise, 1) == 1.8
    assert 58 <= warmup.minutes <= 62


async def test_too_cold_to_wait_heats_alongside_the_radiators(hass: HomeAssistant, house, calls, freezer) -> None:
    _at(freezer, "2026-11-16T08:00:00+00:00")
    hass.states.async_set(OFFICE_SENSOR, "15.0")  # the office is asking for heat
    hass.states.async_set(PROBE, "44.0", {"device_class": "temperature"})
    await _setup(hass, calls, hot_water_sensor=PROBE, hot_water_reheat_below=50, hot_water_heat_anyway_below=40)
    # Below the top-up line but above "too cold": it waits for the radiators.
    assert hass.states.get(RELAY).state == "on"
    assert hass.states.get(HW).state == "off"
    assert hass.states.get(TANK).state == "waiting"

    # Below 40°: it heats at once, sharing the boiler.
    hass.states.async_set(PROBE, "39.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(HW).state == "on"
    assert hass.states.get(RELAY).state == "on"
    tank = hass.states.get(TANK)
    assert tank.state == "heating"
    assert tank.attributes["status"] == "Too cold to wait: heating to 50° alongside the radiators · 39° now"

    # Climbing past 40° it carries on, up to the top-up line.
    hass.states.async_set(PROBE, "46.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(HW).state == "on"

    # Back to 50°: the rest waits for a gap in the radiators.
    hass.states.async_set(PROBE, "50.0", {"device_class": "temperature"})
    await hass.async_block_till_done()
    await _pass(hass, freezer)
    assert hass.states.get(HW).state == "off"
    assert hass.states.get(TANK).state == "waiting"


def test_the_floor_stays_below_the_top_up_line() -> None:
    class Hub:
        settings = {"hot_water_switch": HW, "hot_water_target": 55, "hot_water_reheat_below": 45,
                    "hot_water_heat_anyway_below": 48}
        hass = None
    tank = hotwater.HotWater(Hub(), hotwater.TankState())
    assert tank.heat_anyway_below == 44
