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
    SERVICE_CANCEL_BOOST,
    SERVICE_SET_SCHEDULE,
    WEEKDAYS,
    WEEKENDS,
)
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

    hass.services.async_register(DOMAIN, SERVICE_SET_SCHEDULE, set_schedule, SET_SCHEDULE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_BOOST, boost, BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_BOOST, cancel_boost, ENTITY_SCHEMA)
