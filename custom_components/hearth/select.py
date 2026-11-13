"""Home, Away or Holiday, for the whole house."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HearthConfigEntry
from .const import HOUSE_MODES
from .entity import HearthEntity, house_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HearthConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HouseMode(entry.runtime_data)])


class HouseMode(HearthEntity, SelectEntity):
    _attr_translation_key = "house_mode"
    _attr_options = HOUSE_MODES

    def __init__(self, hub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.entry.entry_id}_house_mode"
        self._attr_device_info = house_device(hub)

    @property
    def current_option(self) -> str:
        return self.hub.house_mode

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_set_house_mode(option)
