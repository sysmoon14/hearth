"""How long each room has heated today and this week, and what today cost.

Three sensors a room, on the room's device. The cost sensor is only there
when Hearth has been told the boiler's output and a gas price (see the
House tab), since without them there's nothing to work it out from.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HearthConfigEntry
from .const import DOMAIN
from .entity import HearthEntity
from .hub import Hearth


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HearthConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    costed = hub.boiler_kw() is not None and hub.settings_price_given()
    entities: list[SensorEntity] = []
    for zone_id in hub.zones:
        entities += [HeatedToday(hub, zone_id), HeatedWeek(hub, zone_id)]
        if costed:
            entities.append(CostToday(hub, zone_id))
    async_add_entities(entities)


class _RoomSensor(HearthEntity, SensorEntity):
    _key: str

    def __init__(self, hub: Hearth, zone_id: str) -> None:
        super().__init__(hub)
        self.zone_id = zone_id
        self._attr_unique_id = f"{hub.entry.entry_id}_{zone_id}_{self._key}"
        self._attr_translation_key = self._key
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, f"{hub.entry.entry_id}_{zone_id}")})

    @property
    def available(self) -> bool:
        return self.zone_id in self.hub.zones

    def _heated(self) -> dict[str, float | None]:
        return self.hub.zones[self.zone_id].heated(dt_util.utcnow())


class HeatedToday(_RoomSensor):
    _key = "heated_today"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float:
        return round(self._heated()["today"] or 0, 1)


class HeatedWeek(_RoomSensor):
    _key = "heated_week"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float:
        return round(self._heated()["week"] or 0, 1)


class CostToday(_RoomSensor):
    _key = "cost_today"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    @property
    def native_unit_of_measurement(self) -> str:
        return self.hass.config.currency if self.hass else "GBP"

    @property
    def native_value(self) -> float | None:
        cost = self._heated()["cost_today"]
        return round(cost, 2) if cost is not None else None

    @property
    def last_reset(self):
        return dt_util.start_of_local_day()
