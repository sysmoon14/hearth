"""Hearth: every room's heating, the same way, whatever heats it."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CARD_FILE, CONF_BOILER_SWITCH, CONF_ZONES, DOMAIN, FRONTEND_URL, OLD_HW_BOOST, SETTINGS, VERSION
from . import panel
from .hub import Hearth
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CLIMATE, Platform.SELECT, Platform.WATER_HEATER]
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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.2: Hearth only calls for heat with a switch. The boiler thermostat
    and the temperatures it was set to go, with anything else left over.
    1.3: the hot water is a Call for hot water switch, not a boost select."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        options = {k: v for k, v in entry.options.items() if k in SETTINGS}
        dropped = sorted(set(entry.options) - set(options))
        if dropped:
            _LOGGER.info("Hearth no longer uses these settings: %s", ", ".join(dropped))
        if entry.options.get("boiler") and not options.get(CONF_BOILER_SWITCH):
            _LOGGER.warning(
                "Hearth now calls for heat only with a switch; %s is no longer used. "
                "Choose a Call for heat switch on the Hearth panel's House tab",
                entry.options["boiler"],
            )
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    if entry.minor_version < 3:
        options = dict(entry.options)
        if boost := options.pop(OLD_HW_BOOST, None):
            _LOGGER.warning(
                "Hearth now heats the hot water with a Call for hot water switch; %s is no longer used. "
                "Choose the switch on the Hearth panel's House tab",
                boost,
            )
        hass.config_entries.async_update_entry(entry, options=options, minor_version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> bool:
    hub = Hearth(hass, entry)
    entry.runtime_data = hub
    try:
        await hub.async_start()
    except Exception:
        _LOGGER.exception("Hearth couldn't start; turning the call for heat off")
        await hub.async_fail_safe()
        raise
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_old_rooms(hass, entry)
    await panel.async_show(hass)

    async def _on_stop(_event: Event) -> None:
        await hub.async_stop(final=True)

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


def _remove_old_rooms(hass: HomeAssistant, entry: HearthConfigEntry) -> None:
    """A room that's been removed takes its device and entity with it."""
    rooms = {f"{entry.entry_id}_{z['id']}" for z in entry.options.get(CONF_ZONES, [])}
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ids = {i for d, i in device.identifiers if d == DOMAIN}
        if entry.entry_id in ids or ids & rooms:
            continue
        registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> bool:
    await entry.runtime_data.async_stop(final=False)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HearthConfigEntry) -> None:
    """The sidebar entry goes with Hearth (a reload keeps it, so it doesn't
    vanish while the panel saves)."""
    panel.async_hide(hass)


async def _async_reload(hass: HomeAssistant, entry: HearthConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
