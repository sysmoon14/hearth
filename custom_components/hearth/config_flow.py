"""Set up Hearth. Rooms and house settings live in the Hearth panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_BOILER_SWITCH, CONF_ZONES, DOMAIN

SWITCH = selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "input_boolean"]))


class HearthConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
    # 2: the call for heat is only ever a switch; old thermostat settings dropped.
    MINOR_VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Hearth", data={}, options={**user_input, CONF_ZONES: []})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Optional(CONF_BOILER_SWITCH): SWITCH}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HearthOptionsFlow()


class HearthOptionsFlow(OptionsFlow):
    """Everything is in the panel: one place to change it."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="use_panel")
