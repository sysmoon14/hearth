"""Hearth: every room's heating, the same way, whatever heats it."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CARD_FILE, DOMAIN, FRONTEND_URL, VERSION
from . import panel
from .hub import Hearth
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SELECT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type HearthConfigEntry = ConfigEntry[Hearth]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the card and register the actions once."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(Path(__file__).parent / "frontend"), False)]
    )
    # Loaded on every dashboard, so there's no resource to add by hand.
    if "frontend" in hass.config.components:
        add_extra_js_url(hass, f"{FRONTEND_URL}/{CARD_FILE}?v={VERSION}")
    async_setup_services(hass)
    panel.async_setup(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> bool:
    hub = Hearth(hass, entry)
    entry.runtime_data = hub
    await hub.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await panel.async_show(hass)

    async def _on_stop(_event: Event) -> None:
        await hub.async_stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> bool:
    await entry.runtime_data.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> None:
    """The sidebar entry goes with Hearth (a reload keeps it, so it doesn't
    vanish while the panel saves)."""
    panel.async_hide(hass)


async def _async_reload(hass: HomeAssistant, entry: HearthConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
