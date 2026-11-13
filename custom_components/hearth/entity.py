"""Shared entity plumbing."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, VERSION
from .hub import SIGNAL_UPDATE, Hearth


class HearthEntity(Entity):
    """Refreshes whenever the house re-evaluates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: Hearth) -> None:
        self.hub = hub

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


def house_device(hub: Hearth) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, hub.entry.entry_id)},
        name="Hearth",
        manufacturer="Hearth",
        model="Heating",
        sw_version=VERSION,
    )
