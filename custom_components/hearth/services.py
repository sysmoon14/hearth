"""Actions the card (and automations) use."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import (
    DOMAIN,
    SERVICE_BOOST,
    HOUSE_HOLIDAY,
    SERVICE_ADD_WARM_UP,
    SERVICE_BOOST_HOT_WATER,
    SERVICE_RESUME_HOT_WATER,
    SERVICE_STOP_HOT_WATER,
    SERVICE_CANCEL_BOOST,
    SERVICE_SET_HOLIDAY,
    SERVICE_SET_SCHEDULE,
    WEEKDAYS,
    WEEKENDS,
)
from homeassistant.util import dt as dt_util

from . import preheat
from .preheat import WarmUp
from .schedule import ScheduleError
from .zone import Zone

CHANGE = vol.Schema({vol.Required("time"): cv.string, vol.Required("temp"): vol.Coerce(float)})

SET_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(WEEKDAYS): [CHANGE],
        vol.Required(WEEKENDS): [CHANGE],
    }
)
BOOST_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=5, max=720)),
        vol.Optional("temperature"): vol.Coerce(float),
    }
)
ENTITY_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_ids})
WARM_UP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("rise"): vol.All(vol.Coerce(float), vol.Range(min=preheat.MIN_RISE, max=15)),
        vol.Required("minutes"): vol.All(vol.Coerce(float), vol.Range(min=preheat.MIN_MINUTES, max=preheat.MAX_MINUTES)),
    }
)
HOT_WATER_SCHEMA = vol.Schema({vol.Optional("minutes", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=240))})
HOLIDAY_SCHEMA = vol.Schema({vol.Optional("until"): cv.datetime})


def _zones(hass: HomeAssistant, call: ServiceCall) -> list[Zone]:
    registry = er.async_get(hass)
    zones = []
    for entity_id in call.data[ATTR_ENTITY_ID]:
        entry = registry.async_get(entity_id)
        if entry is None or entry.platform != DOMAIN or entry.domain != "climate":
            raise ServiceValidationError(f"{entity_id} isn't a Hearth room")
        config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
        if config_entry is None or config_entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError("Hearth isn't running")
        zone_id = entry.unique_id.removeprefix(f"{config_entry.entry_id}_")
        if (zone := config_entry.runtime_data.zones.get(zone_id)) is None:
            raise ServiceValidationError(f"{entity_id} isn't a Hearth room any more")
        zones.append(zone)
    return zones


def async_setup_services(hass: HomeAssistant) -> None:
    async def set_schedule(call: ServiceCall) -> None:
        schedule = {WEEKDAYS: call.data[WEEKDAYS], WEEKENDS: call.data[WEEKENDS]}
        for zone in _zones(hass, call):
            try:
                await zone.async_set_schedule(schedule)
            except ScheduleError as err:
                raise ServiceValidationError(str(err)) from err

    async def boost(call: ServiceCall) -> None:
        for zone in _zones(hass, call):
            await zone.async_boost(call.data.get("minutes"), call.data.get("temperature"))

    async def cancel_boost(call: ServiceCall) -> None:
        for zone in _zones(hass, call):
            await zone.async_cancel_boost()

    async def add_warm_up(call: ServiceCall) -> None:
        """Tell a room about a warm-up it didn't time itself, so it can start
        early before it has seen two of its own."""
        for zone in _zones(hass, call):
            zone.learn(WarmUp(call.data["rise"], call.data["minutes"], dt_util.utcnow(), "told"))
            await zone.hub.async_evaluate()

    hass.services.async_register(DOMAIN, SERVICE_ADD_WARM_UP, add_warm_up, WARM_UP_SCHEMA)

    async def set_holiday(call: ServiceCall) -> None:
        entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
        if not entries:
            raise ServiceValidationError("Hearth isn't running")
        until = call.data.get("until")
        if until is not None:
            until = dt_util.as_utc(until if until.tzinfo else until.replace(tzinfo=dt_util.get_default_time_zone()))
            if until <= dt_util.utcnow():
                raise ServiceValidationError("A holiday has to end some time from now")
        await entries[0].runtime_data.async_set_house_mode(HOUSE_HOLIDAY, until)

    def _hot_water():
        entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
        if not entries:
            raise ServiceValidationError("Hearth isn't running")
        if (tank := entries[0].runtime_data.hot_water) is None:
            raise ServiceValidationError("Hearth isn't heating the hot water: choose its Call for hot water switch on the House tab")
        return tank

    async def boost_hot_water(call: ServiceCall) -> None:
        """Heat the hot water now, whatever the radiators are doing."""
        tank = _hot_water()
        await tank.async_boost(call.data["minutes"])
        await tank.hub.async_evaluate()

    async def stop_hot_water(call: ServiceCall) -> None:
        tank = _hot_water()
        await tank.async_stop()
        await tank.hub.async_evaluate()

    hass.services.async_register(DOMAIN, SERVICE_BOOST_HOT_WATER, boost_hot_water, HOT_WATER_SCHEMA)
    async def resume_hot_water(call: ServiceCall) -> None:
        tank = _hot_water()
        await tank.async_resume()
        await tank.hub.async_evaluate()

    hass.services.async_register(DOMAIN, SERVICE_STOP_HOT_WATER, stop_hot_water, vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_RESUME_HOT_WATER, resume_hot_water, vol.Schema({}))
    hass.services.async_register(DOMAIN, SERVICE_SET_HOLIDAY, set_holiday, HOLIDAY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_SCHEDULE, set_schedule, SET_SCHEDULE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_BOOST, boost, BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_BOOST, cancel_boost, ENTITY_SCHEMA)
