"""Hearth in Home Assistant's sidebar: every room, its schedule, and the
house settings, in one place.

One JS module (frontend/hearth-panel.js, no build step) and a few
websocket commands. Rooms and house settings are saved to the entry's
options; schedules, modes, boosts and Home/Away/Holiday go through
Hearth's own actions and entities. Everyone in the house sees the panel;
only admins change rooms and house settings.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import slugify

from .const import (
    CARD_FILE,
    CONF_AWAY_TEMP,
    CONF_BOILER_SWITCH,
    CONF_BOOST_MINUTES,
    CONF_BOOST_MINUTES_UFH,
    CONF_BOOST_TEMP,
    CONF_HOLIDAY_TEMP,
    CONF_HW_SWITCH,
    CONF_HW_FLOOR,
    CONF_HW_MINUTES,
    CONF_HW_READY,
    CONF_HW_REHEAT,
    CONF_HW_SENSOR,
    CONF_HW_TARGET,
    CONF_KIND,
    CONF_NAME,
    CONF_OFF_DELAY,
    CONF_OFF_TEMP,
    CONF_ON_DELAY,
    CONF_PREHEAT_MAX,
    CONF_PROFILE,
    CONF_SENSOR,
    CONF_STAT,
    CONF_TRVS,
    CONF_WARM_EARLY,
    CONF_ZONE_ID,
    CONF_ZONES,
    DEFAULTS,
    DOMAIN,
    FRONTEND_URL,
    KIND_RADIATOR,
    KIND_UNDERFLOOR,
    VERSION,
)

PANEL = "hearth"
PANEL_FILE = "hearth-panel.js"

# House settings the panel can change, and what each accepts.
TEMPS = {
    CONF_BOOST_TEMP: (15, 28), CONF_AWAY_TEMP: (5, 22), CONF_HOLIDAY_TEMP: (5, 18), CONF_OFF_TEMP: (5, 15),
    CONF_HW_TARGET: (40, 65), CONF_HW_REHEAT: (30, 60), CONF_HW_FLOOR: (25, 55),
}
SECONDS = {CONF_ON_DELAY: (0, 900), CONF_OFF_DELAY: (0, 1800), CONF_BOOST_MINUTES: (15, 240),
           CONF_BOOST_MINUTES_UFH: (30, 480), CONF_PREHEAT_MAX: (0, 360), CONF_HW_MINUTES: (15, 180)}
OPTIONAL_ENTITIES = (CONF_BOILER_SWITCH, CONF_HW_SWITCH, CONF_HW_SENSOR)
READY_TIME = vol.Match(r"^([01]\d|2[0-3]):[0-5]\d$")
SWITCH_DOMAINS = ("switch", "input_boolean")


@callback
def async_setup(hass: HomeAssistant) -> None:
    """The panel's websocket commands, once."""
    websocket_api.async_register_command(hass, ws_info)
    websocket_api.async_register_command(hass, ws_save_room)
    websocket_api.async_register_command(hass, ws_remove_room)
    websocket_api.async_register_command(hass, ws_save_house)


async def async_show(hass: HomeAssistant) -> None:
    """The sidebar entry, while Hearth is set up."""
    if "frontend" not in hass.config.components:
        return
    if PANEL in hass.data.get(frontend.DATA_PANELS, {}):
        return
    from homeassistant.components import panel_custom  # noqa: PLC0415

    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL, webcomponent_name="hearth-panel", sidebar_title="Hearth",
        sidebar_icon="mdi:radiator", module_url=f"{FRONTEND_URL}/{PANEL_FILE}?v={VERSION}",
        require_admin=False, config={},
    )


@callback
def async_hide(hass: HomeAssistant) -> None:
    if "frontend" in hass.config.components:
        frontend.async_remove_panel(hass, PANEL, warn_if_unknown=False)


def _entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def _room_entity(hass: HomeAssistant, entry: ConfigEntry, zone_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("climate", DOMAIN, f"{entry.entry_id}_{zone_id}")


def _own(hass: HomeAssistant, entry: ConfigEntry, unique: str, domain: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{unique}")


def _info(hass: HomeAssistant, entry: ConfigEntry, is_admin: bool) -> dict[str, Any]:
    options = {**DEFAULTS, **entry.options}
    hub = entry.runtime_data if entry.state is ConfigEntryState.LOADED else None
    rooms = []
    for zone in options.get(CONF_ZONES, []):
        running = hub.zones.get(zone[CONF_ZONE_ID]) if hub else None
        rooms.append({
            "id": zone[CONF_ZONE_ID],
            "name": zone[CONF_NAME],
            "kind": zone.get(CONF_KIND, KIND_RADIATOR),
            "entity_id": _room_entity(hass, entry, zone[CONF_ZONE_ID]),
            "trvs": list(zone.get(CONF_TRVS) or []),
            "sensor": zone.get(CONF_SENSOR),
            "stat": zone.get(CONF_STAT),
            "profile": zone.get(CONF_PROFILE),
            "warm_early": bool(zone.get(CONF_WARM_EARLY, True)),
            "schedule": running.state.schedule if running else None,
        })
    return {
        "entry_id": entry.entry_id,
        "loaded": hub is not None,
        "version": VERSION,
        "is_admin": is_admin,
        "rooms": rooms,
        "settings": {k: options.get(k) for k in (*OPTIONAL_ENTITIES, *TEMPS, *SECONDS, CONF_HW_READY)},
        "hot_water": _own(hass, entry, "hot_water", "water_heater"),
        "holiday_until": hub.holiday_until.isoformat() if hub and hub.holiday_until else None,
        "house_mode": _own(hass, entry, "house_mode", "select"),
        "demand": _own(hass, entry, "radiator_demand", "binary_sensor"),
        "last_boiler_command": hub.last_boiler_command if hub else None,
        "learning_from_history": hub.seed_status if hub else None,
        "card": f"{FRONTEND_URL}/{CARD_FILE}?v={VERSION}",
    }


@websocket_api.websocket_command({vol.Required("type"): "hearth/info"})
@callback
def ws_info(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Hearth isn't set up")
        return
    connection.send_result(msg["id"], _info(hass, entry, connection.user.is_admin))


def _unique_id(zones: list[dict[str, Any]], name: str, keep: str | None) -> str:
    base = slugify(name) or "room"
    taken = {z[CONF_ZONE_ID] for z in zones if z[CONF_ZONE_ID] != keep}
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}_{n}", n + 1
    return candidate


ROOM = vol.Schema({
    vol.Optional("id"): vol.Any(None, str),
    vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=40)),
    vol.Required("kind"): vol.In([KIND_RADIATOR, KIND_UNDERFLOOR]),
    vol.Optional("trvs", default=[]): [cv.entity_id],
    vol.Optional("sensor"): vol.Any(None, cv.entity_id),
    vol.Optional("stat"): vol.Any(None, cv.entity_id),
    vol.Optional("profile"): vol.Any(None, vol.All(str, vol.Strip, vol.Length(max=40))),
    vol.Optional("warm_early", default=True): bool,
})


def _device_trouble(hass: HomeAssistant, zones: list[dict[str, Any]], room: dict[str, Any], keep: str | None) -> str | None:
    """Why a room's valves or stat can't be used, or None when they can: a
    valve has to be a valve, and a device heats one room."""
    registry = er.async_get(hass)
    devices = room["trvs"] if room["kind"] == KIND_RADIATOR else [room.get("stat")]
    if len(set(devices)) != len(devices):
        return "The same valve is listed twice"
    for entity_id in devices:
        entry = registry.async_get(entity_id)
        platform = entry.platform if entry else None
        if platform == DOMAIN:
            return f"{entity_id} is a Hearth room, not a valve or a stat"
        if room["kind"] == KIND_RADIATOR and platform == "heatmiserneo":
            return f"{entity_id} is a Heatmiser stat: add it as an underfloor room"
        for other in zones:
            if other[CONF_ZONE_ID] != keep and entity_id in [*(other.get(CONF_TRVS) or []), other.get(CONF_STAT)]:
                return f"{entity_id} already heats {other[CONF_NAME]}"
    return None


@websocket_api.websocket_command({vol.Required("type"): "hearth/save_room", vol.Required("room"): ROOM})
@websocket_api.require_admin
@callback
def ws_save_room(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Add a room, or change one's name and devices."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Hearth isn't set up")
        return
    room = msg["room"]
    zones = list(entry.options.get(CONF_ZONES, []))
    keep = room.get("id")
    if keep and not any(z[CONF_ZONE_ID] == keep for z in zones):
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "No such room")
        return
    if room["kind"] == KIND_RADIATOR and not room["trvs"]:
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "A radiator room needs at least one valve")
        return
    if room["kind"] == KIND_UNDERFLOOR and not room.get("stat"):
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, "An underfloor room needs its stat")
        return
    if trouble := _device_trouble(hass, zones, room, keep):
        connection.send_error(msg["id"], websocket_api.ERR_INVALID_FORMAT, trouble)
        return
    zone: dict[str, Any] = {CONF_ZONE_ID: keep or _unique_id(zones, room["name"], None),
                            CONF_KIND: room["kind"], CONF_NAME: room["name"]}
    if room.get("sensor"):
        zone[CONF_SENSOR] = room["sensor"]
    if room["kind"] == KIND_RADIATOR:
        zone[CONF_TRVS] = room["trvs"]
        if not room["warm_early"]:
            zone[CONF_WARM_EARLY] = False
    else:
        zone[CONF_STAT] = room["stat"]
        zone[CONF_PROFILE] = room.get("profile") or f"Hearth {room['name']}"
    zones = [zone if z[CONF_ZONE_ID] == zone[CONF_ZONE_ID] else z for z in zones]
    if not keep:
        zones.append(zone)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_ZONES: zones})
    connection.send_result(msg["id"], {"id": zone[CONF_ZONE_ID]})


@websocket_api.websocket_command({vol.Required("type"): "hearth/remove_room", vol.Required("room_id"): str})
@websocket_api.require_admin
@callback
def ws_remove_room(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Hearth isn't set up")
        return
    room_id = msg["room_id"]
    zones = [z for z in entry.options.get(CONF_ZONES, []) if z[CONF_ZONE_ID] != room_id]
    options = {**entry.options, CONF_ZONES: zones}
    hass.config_entries.async_update_entry(entry, options=options)
    # The room's entity goes with it, and its device when Hearth reloads.
    if entity_id := _room_entity(hass, entry, room_id):
        er.async_get(hass).async_remove(entity_id)
    connection.send_result(msg["id"], {})


HOUSE = {
    vol.Optional(CONF_BOILER_SWITCH): vol.Any(None, vol.All(cv.entity_id, cv.entity_domain(SWITCH_DOMAINS))),
    vol.Optional(CONF_HW_SWITCH): vol.Any(None, vol.All(cv.entity_id, cv.entity_domain(SWITCH_DOMAINS))),
    vol.Optional(CONF_HW_SENSOR): vol.Any(None, vol.All(cv.entity_id, cv.entity_domain("sensor"))),
    vol.Optional(CONF_HW_READY): vol.All([READY_TIME], vol.Length(max=4)),
    **{vol.Optional(k): vol.All(vol.Coerce(float), vol.Range(min=lo, max=hi)) for k, (lo, hi) in TEMPS.items()},
    **{vol.Optional(k): vol.All(vol.Coerce(int), vol.Range(min=lo, max=hi)) for k, (lo, hi) in SECONDS.items()},
}


@websocket_api.websocket_command({vol.Required("type"): "hearth/save_house", vol.Required("settings"): HOUSE})
@websocket_api.require_admin
@callback
def ws_save_house(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The house settings it sends; None clears the switch."""
    entry = _entry(hass)
    if entry is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Hearth isn't set up")
        return
    options = dict(entry.options)
    for key, value in msg["settings"].items():
        if value is None:
            options.pop(key, None)
        else:
            options[key] = value
    hass.config_entries.async_update_entry(entry, options=options)
    connection.send_result(msg["id"], {})
