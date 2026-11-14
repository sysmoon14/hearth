"""Whether any radiator room wants the boiler."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HearthConfigEntry
from .entity import HearthEntity, house_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HearthConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([RadiatorDemand(entry.runtime_data)])


class RadiatorDemand(HearthEntity, BinarySensorEntity):
    _attr_translation_key = "radiator_demand"
    _attr_device_class = BinarySensorDeviceClass.HEAT

    def __init__(self, hub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.entry.entry_id}_radiator_demand"
        self._attr_device_info = house_device(hub)

    @property
    def is_on(self) -> bool:
        return self.hub.demand

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "rooms_asking": self.hub.zones_calling(),
            "switch": self.hub.boiler_switch,
            "last_command": self.hub.last_boiler_command,
        }
