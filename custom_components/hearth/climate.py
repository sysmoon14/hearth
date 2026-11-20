"""One climate entity per room, the same whatever heats it.

Auto is the schedule, Heat is a fixed temperature, Off keeps the room
frost-safe. Boost is a preset.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_BOOST,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HearthConfigEntry
from .const import DOMAIN, MAX_TEMP, MIN_TEMP, MODE_FIXED, MODE_OFF, MODE_SCHEDULE, TEMP_STEP, VERSION
from .entity import HearthEntity
from .hub import Hearth
from .zone import Zone

MODE_TO_HVAC = {MODE_SCHEDULE: HVACMode.AUTO, MODE_FIXED: HVACMode.HEAT, MODE_OFF: HVACMode.OFF}
HVAC_TO_MODE = {v: k for k, v in MODE_TO_HVAC.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HearthConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    hub = entry.runtime_data
    async_add_entities(HearthClimate(hub, zone_id) for zone_id in _zone_ids(hub))


def _zone_ids(hub: Hearth) -> list[str]:
    return [z["id"] for z in hub.settings.get("zones", [])]


class _HearthClimate(HearthEntity, ClimateEntity):
    """A room."""

    _attr_name = None
    _attr_translation_key = "zone"
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = [PRESET_NONE, PRESET_BOOST]
    _attr_target_temperature_step = TEMP_STEP
    # The History tab reads temperatures, hvac_action and next_change_at;
    # the rest is settings that would only fill the recorder.
    _unrecorded_attributes = frozenset({
        "schedule", "devices", "room_sensor", "room_sensor_stale", "problem", "zone_id", "kind", "profile",
        "warm_up_rate", "warm_ups", "warm_ups_from_history", "preheat_from", "minutes_to_target",
        # The sensors record these; on the room they're for the card.
        "heated_today", "heated_week", "cost_today", "cost_week",
    })
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, hub: Hearth, zone_id: str) -> None:
        super().__init__(hub)
        self.zone_id = zone_id
        config = next(z for z in hub.settings["zones"] if z["id"] == zone_id)
        self._attr_unique_id = f"{hub.entry.entry_id}_{zone_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{hub.entry.entry_id}_{zone_id}")},
            name=config["name"],
            manufacturer="Hearth",
            model="Underfloor zone" if config.get("kind") == "underfloor" else "Radiator zone",
            sw_version=VERSION,
        )

    @property
    def zone(self) -> Zone:
        return self.hub.zones[self.zone_id]

    @property
    def available(self) -> bool:
        return self.zone_id in self.hub.zones

    @property
    def hvac_mode(self) -> HVACMode:
        return MODE_TO_HVAC[self.zone.state.mode]

    @property
    def hvac_action(self) -> HVACAction:
        if self.zone.calling:
            if self.zone.preheating(dt_util.utcnow()):
                return HVACAction.PREHEATING
            return HVACAction.HEATING
        if self.zone.state.mode == MODE_OFF:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str:
        return PRESET_BOOST if self.zone.boost_active(dt_util.utcnow()) else PRESET_NONE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.zone.attributes(dt_util.utcnow())

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get("hvac_mode")) is not None:
            await self.async_set_hvac_mode(mode)
        if (temp := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self.zone.async_set_target(float(temp))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.zone.async_set_mode(HVAC_TO_MODE[HVACMode(hvac_mode)])

    async def async_turn_on(self) -> None:
        await self.zone.async_set_mode(MODE_SCHEDULE)

    async def async_turn_off(self) -> None:
        await self.zone.async_set_mode(MODE_OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode == PRESET_BOOST:
            await self.zone.async_boost()
        else:
            await self.zone.async_cancel_boost()


# Home Assistant 2026.11 moved climate temperatures to native_* names; older
# versions only know the plain ones. Each gets the names it expects.
if hasattr(ClimateEntity, "native_target_temperature"):

    class HearthClimate(_HearthClimate):
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS

        @property
        def native_current_temperature(self) -> float | None:
            return self.zone.room_temp()

        @property
        def native_target_temperature(self) -> float | None:
            return self.zone.target()

else:

    class HearthClimate(_HearthClimate):  # type: ignore[no-redef]
        _attr_temperature_unit = UnitOfTemperature.CELSIUS

        @property
        def current_temperature(self) -> float | None:
            return self.zone.room_temp()

        @property
        def target_temperature(self) -> float | None:
            return self.zone.target()
