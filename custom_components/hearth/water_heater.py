"""The hot water cylinder, when Hearth heats it."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import WaterHeaterEntity, WaterHeaterEntityFeature
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HearthConfigEntry
from .entity import HearthEntity, house_device


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HearthConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.hot_water is not None:
        async_add_entities([HotWaterTank(entry.runtime_data)])


class HotWaterTank(HearthEntity, WaterHeaterEntity):
    """Says what the hot water is doing. It's run from Hearth's settings and
    actions, so there's nothing to set here."""

    _attr_translation_key = "hot_water"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = WaterHeaterEntityFeature(0)
    _attr_min_temp = 30
    _attr_max_temp = 65

    def __init__(self, hub) -> None:
        super().__init__(hub)
        self._attr_unique_id = f"{hub.entry.entry_id}_hot_water"
        self._attr_device_info = house_device(hub)

    @property
    def current_operation(self) -> str:
        return self.hub.hot_water.operation

    @property
    def native_current_temperature(self) -> float | None:
        return self.hub.hot_water.tank_temp()

    @property
    def native_target_temperature(self) -> float | None:
        tank = self.hub.hot_water
        return tank.target if tank.tank_temp() is not None else None

    if not hasattr(WaterHeaterEntity, "native_current_temperature"):
        # Home Assistant before the temperatures were renamed.
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
        current_temperature = native_current_temperature
        target_temperature = native_target_temperature

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.hub.hot_water.attributes(dt_util.utcnow())
