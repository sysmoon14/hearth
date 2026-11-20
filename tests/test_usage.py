"""How long rooms heat, and what it costs."""

from __future__ import annotations

from datetime import date, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.hearth import usage
from custom_components.hearth.zone import ZoneState

from .conftest import KITCHEN, KITCHEN_ZONE, OFFICE, OFFICE_SENSOR
from .test_zones import _setup


def test_totals_by_day() -> None:
    day = date(2026, 11, 20)
    days = usage.add({}, day, 30, 15)
    days = usage.add(days, day, 10, 10)
    assert usage.today(days, day) == (40, 25)
    days = usage.add(days, day - timedelta(days=3), 60, 60)
    assert usage.week(days, day) == (100, 85)
    # Eight days kept; the week is seven, today included.
    days = usage.add(days, day - timedelta(days=7), 99, 99)
    assert usage.week(days, day) == (100, 85)
    later = usage.add(days, day + timedelta(days=1), 1, 1)
    assert (day - timedelta(days=7)).isoformat() not in later


def test_cost_and_prices() -> None:
    # An hour of a 12 kW boiler at 7.9p a kWh.
    assert round(usage.cost(60, 12, 7.9), 3) == 0.948
    assert usage.cost(60, None, 7.9) is None
    assert usage.cost(60, 12, None) is None
    assert round(usage.pence("0.079053", "GBP/kWh"), 4) == 7.9053
    assert usage.pence("7.9", "p/kWh") == 7.9
    assert usage.pence(7.9, None) == 7.9
    assert usage.pence("unknown", "GBP/kWh") is None


def test_stored_totals_survive_and_bad_ones_go() -> None:
    state = ZoneState.from_dict({"heated": {"2026-11-20": [12, 6], "rubbish": [1, 1], "2026-11-19": "x"}})
    assert state.heated == {"2026-11-20": [12.0, 6.0]}
    assert ZoneState.from_dict(state.as_dict()).heated == state.heated


async def test_rooms_heating_together_share_the_boiler(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE, KITCHEN_ZONE], boiler_kw=12, gas_price=10)
    # The office is cold (it asks), and the kitchen's floor is heating too.
    hass.states.async_set(KITCHEN, "heat", {"temperature": 21.0, "current_temperature": 20.4, "hvac_action": "heating"})
    await hass.async_block_till_done()
    for _ in range(20):  # ten minutes, a look every 30 s
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    office = hass.states.get("sensor.office_heated_today")
    kitchen = hass.states.get("sensor.kitchen_heated_today")
    assert 9 <= float(office.state) <= 10.5
    assert 9 <= float(kitchen.state) <= 10.5
    assert office.attributes["unit_of_measurement"] == "min"
    # Two rooms at once: each pays for half the boiler's time.
    # ~10 min / 2 × 12 kW × 10p = ~10p each.
    cost = float(hass.states.get("sensor.office_heating_cost_today").state)
    assert 0.08 <= cost <= 0.11
    assert hass.states.get("sensor.office_heated_this_week").state == office.state
    room = hass.states.get("climate.office")
    assert room.attributes["heated_today"] >= 9
    assert room.attributes["cost_today"] is not None

    # Warm: the office stops asking and stops counting.
    hass.states.async_set(OFFICE_SENSOR, "19.5")
    await hass.async_block_till_done()
    for _ in range(4):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    before = float(hass.states.get("sensor.office_heated_today").state)
    for _ in range(10):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert float(hass.states.get("sensor.office_heated_today").state) == before


async def test_no_cost_sensor_without_a_boiler_output(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    await _setup(hass, calls, [OFFICE], gas_price=7.9)
    assert hass.states.get("sensor.office_heated_today") is not None
    assert hass.states.get("sensor.office_heating_cost_today") is None
    assert hass.states.get("climate.office").attributes["cost_today"] is None


async def test_price_from_a_sensor(hass: HomeAssistant, house, calls, freezer) -> None:
    freezer.move_to(dt_util.as_utc(dt_util.parse_datetime("2026-11-13T07:30:00+00:00")))
    hass.states.async_set("sensor.gas_rate", "0.05", {"unit_of_measurement": "GBP/kWh"})
    entry = await _setup(hass, calls, [OFFICE], boiler_kw=10, gas_price=99, gas_price_entity="sensor.gas_rate")
    assert entry.runtime_data.gas_price() == 5.0
    hass.states.async_set("sensor.gas_rate", "unavailable")
    assert entry.runtime_data.gas_price() == 99
