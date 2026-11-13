"""Set up Hearth, then add rooms from Configure."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_AWAY_TEMP,
    CONF_BACKUP_ZONE,
    CONF_BOILER,
    CONF_BOILER_SWITCH,
    CONF_BOILER_OFF,
    CONF_BOILER_ON,
    CONF_BOOST_MINUTES,
    CONF_BOOST_TEMP,
    CONF_FALLBACK,
    CONF_HOLIDAY_TEMP,
    CONF_KIND,
    CONF_NAME,
    CONF_OFF_DELAY,
    CONF_OFF_TEMP,
    CONF_ON_DELAY,
    CONF_PROFILE,
    CONF_SENSOR,
    CONF_STAT,
    CONF_TRVS,
    CONF_ZONE_ID,
    CONF_ZONES,
    DEFAULTS,
    DOMAIN,
    KIND_RADIATOR,
    KIND_UNDERFLOOR,
)

CLIMATE = selector.EntitySelector(selector.EntitySelectorConfig(domain="climate"))
TEMP_SENSOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
)


def _temp(minimum: float, maximum: float) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=0.5, unit_of_measurement="°C",
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _seconds(maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=maximum, step=10, unit_of_measurement="s",
                                      mode=selector.NumberSelectorMode.BOX)
    )


class HearthConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Hearth", data={}, options={**user_input, CONF_ZONES: []})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_BOILER): CLIMATE}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HearthOptionsFlow()


class HearthOptionsFlow(OptionsFlow):
    """Add, change and remove rooms, and the house settings."""

    def __init__(self) -> None:
        self._editing: dict[str, Any] | None = None

    @property
    def _options(self) -> dict[str, Any]:
        return {**DEFAULTS, **self.config_entry.options}

    @property
    def _zones(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_ZONES, []))

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["add_radiator", "add_underfloor"]
        if self._zones:
            menu += ["edit_zone", "remove_zone"]
        menu.append("settings")
        return self.async_show_menu(step_id="init", menu_options=menu)

    # --- rooms ------------------------------------------------------------
    def _unique_id(self, name: str) -> str:
        base = slugify(name) or "room"
        taken = {z[CONF_ZONE_ID] for z in self._zones if z is not self._editing}
        candidate, n = base, 2
        while candidate in taken:
            candidate, n = f"{base}_{n}", n + 1
        return candidate

    def _store_zone(self, zone: dict[str, Any]) -> ConfigFlowResult:
        zones = [z for z in self._zones if not self._editing or z[CONF_ZONE_ID] != self._editing[CONF_ZONE_ID]]
        zones.append(zone)
        return self._save(**{CONF_ZONES: zones})

    def _radiator_schema(self, current: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=current.get(CONF_NAME, "")): str,
                vol.Required(CONF_TRVS, default=current.get(CONF_TRVS, [])): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate", multiple=True)
                ),
                vol.Optional(CONF_SENSOR, description={"suggested_value": current.get(CONF_SENSOR)}): TEMP_SENSOR,
            }
        )

    def _underfloor_schema(self, current: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_NAME, default=current.get(CONF_NAME, "")): str,
                vol.Required(CONF_STAT, default=current.get(CONF_STAT)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate", integration="heatmiserneo")
                ),
                vol.Optional(CONF_PROFILE, description={"suggested_value": current.get(CONF_PROFILE)}): str,
                vol.Optional(CONF_SENSOR, description={"suggested_value": current.get(CONF_SENSOR)}): TEMP_SENSOR,
            }
        )

    def _zone_from(self, kind: str, user_input: dict[str, Any]) -> dict[str, Any]:
        zone_id = self._editing[CONF_ZONE_ID] if self._editing else self._unique_id(user_input[CONF_NAME])
        return {CONF_ZONE_ID: zone_id, CONF_KIND: kind, **user_input}

    async def async_step_add_radiator(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._store_zone(self._zone_from(KIND_RADIATOR, user_input))
        return self.async_show_form(
            step_id="add_radiator", data_schema=self._radiator_schema(self._editing or {})
        )

    async def async_step_add_underfloor(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._store_zone(self._zone_from(KIND_UNDERFLOOR, user_input))
        return self.async_show_form(
            step_id="add_underfloor", data_schema=self._underfloor_schema(self._editing or {})
        )

    def _zone_picker(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_ZONE_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=z[CONF_ZONE_ID], label=z[CONF_NAME])
                            for z in self._zones
                        ]
                    )
                )
            }
        )

    async def async_step_edit_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._editing = next(z for z in self._zones if z[CONF_ZONE_ID] == user_input[CONF_ZONE_ID])
            if self._editing.get(CONF_KIND) == KIND_UNDERFLOOR:
                return await self.async_step_add_underfloor()
            return await self.async_step_add_radiator()
        return self.async_show_form(step_id="edit_zone", data_schema=self._zone_picker())

    async def async_step_remove_zone(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            zones = [z for z in self._zones if z[CONF_ZONE_ID] != user_input[CONF_ZONE_ID]]
            return self._save(**{CONF_ZONES: zones})
        return self.async_show_form(step_id="remove_zone", data_schema=self._zone_picker())

    # --- the house ------------------------------------------------------------
    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(**user_input)
        o = self._options
        schema = vol.Schema(
            {
                vol.Optional(CONF_BOILER, description={"suggested_value": o.get(CONF_BOILER)}): CLIMATE,
                vol.Optional(CONF_BOILER_SWITCH, description={"suggested_value": o.get(CONF_BOILER_SWITCH)}):
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="switch", integration="fledge")),
                vol.Optional(CONF_BACKUP_ZONE, description={"suggested_value": o.get(CONF_BACKUP_ZONE)}):
                    selector.SelectSelector(selector.SelectSelectorConfig(options=[
                        selector.SelectOptionDict(value=z[CONF_ZONE_ID], label=z[CONF_NAME])
                        for z in self._zones if z.get(CONF_KIND) == KIND_RADIATOR
                    ])),
                vol.Required(CONF_BOILER_ON, default=o[CONF_BOILER_ON]): _temp(15, 32),
                vol.Required(CONF_BOILER_OFF, default=o[CONF_BOILER_OFF]): _temp(5, 18),
                vol.Required(CONF_FALLBACK, default=o[CONF_FALLBACK]): _temp(5, 22),
                vol.Required(CONF_ON_DELAY, default=o[CONF_ON_DELAY]): _seconds(900),
                vol.Required(CONF_OFF_DELAY, default=o[CONF_OFF_DELAY]): _seconds(1800),
                vol.Required(CONF_BOOST_TEMP, default=o[CONF_BOOST_TEMP]): _temp(15, 28),
                vol.Required(CONF_BOOST_MINUTES, default=o[CONF_BOOST_MINUTES]): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=15, max=240, step=15, unit_of_measurement="min",
                                                  mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_AWAY_TEMP, default=o[CONF_AWAY_TEMP]): _temp(5, 22),
                vol.Required(CONF_HOLIDAY_TEMP, default=o[CONF_HOLIDAY_TEMP]): _temp(5, 18),
                vol.Required(CONF_OFF_TEMP, default=o[CONF_OFF_TEMP]): _temp(5, 15),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
