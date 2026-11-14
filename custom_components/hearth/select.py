"""Home, Away or Holiday, for the whole house."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HearthConfigEntry
from .const import CONF_AWAY_TEMP, CONF_BOOST_MINUTES, CONF_BOOST_TEMP, CONF_HOLIDAY_TEMP, HOUSE_MODES
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What a card's Boost button does, for cards that can't read the
        house settings."""
        return {
            "boost_minutes": int(self.hub.settings[CONF_BOOST_MINUTES]),
            "boost_temp": float(self.hub.settings[CONF_BOOST_TEMP]),
            "away_temp": float(self.hub.settings[CONF_AWAY_TEMP]),
            "holiday_temp": float(self.hub.settings[CONF_HOLIDAY_TEMP]),
            "holiday_until": self.hub.holiday_until.isoformat() if self.hub.holiday_until else None,
        }

    async def async_select_option(self, option: str) -> None:
        await self.hub.async_set_house_mode(option)
