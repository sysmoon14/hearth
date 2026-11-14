"""The sidebar panel's websocket API."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from .conftest import KITCHEN, KITCHEN_ZONE, OFFICE, OFFICE_TRV, RELAY, hearth_entry


async def _setup(hass: HomeAssistant, calls, zones, **options):
    entry = hearth_entry(hass, zones, **options)
    assert await async_setup_component(hass, "climate", {})
    assert await async_setup_component(hass, "select", {})
    calls.patch_climate()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_info(hass: HomeAssistant, house, calls, hass_ws_client) -> None:
    entry = await _setup(hass, calls, [OFFICE, KITCHEN_ZONE])
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hearth/info"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    info = msg["result"]
    assert info["loaded"]
    assert [r["id"] for r in info["rooms"]] == ["office", "kitchen"]
    office = info["rooms"][0]
    assert office["entity_id"] == "climate.office"
    assert office["trvs"] == [OFFICE_TRV]
    assert office["schedule"]["weekdays"]
    assert info["rooms"][1]["stat"] == KITCHEN
    assert info["settings"]["boiler_switch"] == RELAY
    assert info["is_admin"]
    assert info["house_mode"] == "select.hearth_house_mode"
    assert info["demand"] == "binary_sensor.hearth_radiators_want_heat"
    assert entry.entry_id == info["entry_id"]


async def test_add_change_and_remove_a_room(hass: HomeAssistant, house, calls, hass_ws_client) -> None:
    entry = await _setup(hass, calls, [OFFICE])
    ws = await hass_ws_client(hass)

    await ws.send_json({"id": 1, "type": "hearth/save_room", "room": {"name": "Kitchen", "kind": "underfloor", "stat": KITCHEN}})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"]["id"] == "kitchen"
    await hass.async_block_till_done()
    kitchen = next(z for z in entry.options["zones"] if z["id"] == "kitchen")
    assert kitchen["profile"] == "Hearth Kitchen"
    assert hass.states.get("climate.kitchen_2") is not None or hass.states.get("climate.kitchen_3") is not None

    # A name that's taken gets its own id.
    await ws.send_json({"id": 2, "type": "hearth/save_room", "room": {"name": "Office", "kind": "radiator", "trvs": [OFFICE_TRV]}})
    msg = await ws.receive_json()
    assert msg["result"]["id"] == "office_2"
    await hass.async_block_till_done()

    # Changing a room keeps its id; clearing its sensor removes it.
    await ws.send_json({"id": 3, "type": "hearth/save_room",
                        "room": {"id": "office", "name": "Study", "kind": "radiator", "trvs": [OFFICE_TRV], "sensor": None}})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    office = next(z for z in entry.options["zones"] if z["id"] == "office")
    assert office["name"] == "Study"
    assert "sensor" not in office

    await ws.send_json({"id": 4, "type": "hearth/remove_room", "room_id": "office_2"})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()
    assert [z["id"] for z in entry.options["zones"]] == ["office", "kitchen"]


async def test_room_needs_its_devices(hass: HomeAssistant, house, calls, hass_ws_client) -> None:
    await _setup(hass, calls, [OFFICE])
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hearth/save_room", "room": {"name": "Hall", "kind": "radiator", "trvs": []}})
    msg = await ws.receive_json()
    assert not msg["success"]
    await ws.send_json({"id": 2, "type": "hearth/save_room", "room": {"name": "Hall", "kind": "underfloor"}})
    assert not (await ws.receive_json())["success"]
    await ws.send_json({"id": 3, "type": "hearth/save_room", "room": {"id": "nope", "name": "Hall", "kind": "radiator", "trvs": [OFFICE_TRV]}})
    assert not (await ws.receive_json())["success"]


async def test_house_settings(hass: HomeAssistant, house, calls, hass_ws_client) -> None:
    entry = await _setup(hass, calls, [OFFICE, KITCHEN_ZONE], boiler_switch="switch.boiler_relay")
    ws = await hass_ws_client(hass)

    await ws.send_json({"id": 1, "type": "hearth/save_house", "settings": {"away_temp": 15.5, "boost_minutes": 90, "boiler_switch": None}})
    msg = await ws.receive_json()
    assert msg["success"], msg
    await hass.async_block_till_done()
    assert entry.options["away_temp"] == 15.5
    assert entry.options["boost_minutes"] == 90
    assert "boiler_switch" not in entry.options
    assert entry.options["zones"]

    # Out of range.
    await ws.send_json({"id": 3, "type": "hearth/save_house", "settings": {"away_temp": 40}})
    assert not (await ws.receive_json())["success"]

    # Removing a room.
    await ws.send_json({"id": 4, "type": "hearth/remove_room", "room_id": "office"})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()


async def test_everyone_sees_the_panel_but_only_admins_change_it(
    hass: HomeAssistant, house, calls, hass_ws_client, hass_read_only_access_token
) -> None:
    await _setup(hass, calls, [OFFICE])
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    await ws.send_json({"id": 1, "type": "hearth/info"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert msg["result"]["is_admin"] is False
    await ws.send_json({"id": 2, "type": "hearth/save_house", "settings": {"away_temp": 15}})
    msg = await ws.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "unauthorized"
    await ws.send_json({"id": 3, "type": "hearth/remove_room", "room_id": "office"})
    assert not (await ws.receive_json())["success"]


async def test_the_switch_must_be_a_switch(hass: HomeAssistant, house, calls, hass_ws_client) -> None:
    await _setup(hass, calls, [OFFICE])
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hearth/save_house", "settings": {"boiler_switch": "climate.nest"}})
    assert not (await ws.receive_json())["success"]
    await ws.send_json({"id": 2, "type": "hearth/save_house", "settings": {"boiler_switch": "input_boolean.call_for_heat"}})
    assert (await ws.receive_json())["success"]
