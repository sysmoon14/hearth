"""Diagnostics download: everything about every room in one file."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import HearthConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HearthConfigEntry
) -> dict[str, Any]:
    return entry.runtime_data.diagnostics()
