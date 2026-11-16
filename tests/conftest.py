"""Fixtures for Hearth tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import custom_components

_REPO_COMPONENTS = str(Path(__file__).parents[1] / "custom_components")
if _REPO_COMPONENTS not in custom_components.__path__:
    custom_components.__path__.append(_REPO_COMPONENTS)

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

RELAY = "switch.boiler_relay"
OFFICE_TRV = "climate.office_trv"
OFFICE_SENSOR = "sensor.office_temperature"
KITCHEN = "climate.kitchen"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Enable custom integrations in all tests."""


class Calls:
    """Records service calls to stand-in domains and mirrors them into states."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.log: list[tuple[str, str, dict]] = []
        # More stand-in entities a test wants calls recorded for.
        self.extra: set[str] = set()

    def register(self, domain: str, service: str, apply=None) -> None:
        async def handler(call: ServiceCall) -> None:
            data = dict(call.data)
            self.log.append((domain, service, data))
            if apply:
                apply(data)

        self.hass.services.async_register(domain, service, handler)

    def patch_climate(self) -> None:
        """Once the real climate and select services exist, send calls for the
        stand-in devices here and the rest (Hearth's own entities) to the real ones."""
        fakes = {OFFICE_TRV, KITCHEN, "select.office_trv_temperature_sensor_select",
                 "select.kitchen_active_profile", *self.extra}
        for domain, services in (
            ("climate", ("set_temperature", "set_preset_mode", "set_hvac_mode")),
            ("select", ("select_option",)),
        ):
            for service in services:
                self._wrap(domain, service, fakes)

    def _wrap(self, domain: str, service: str, fakes: set[str]) -> None:
        original = self.hass.services._services[domain][service]  # noqa: SLF001
        apply = _set_climate if domain == "climate" else _select_option

        async def handler(call: ServiceCall) -> None:
            ids = call.data.get("entity_id") or []
            ids = ids if isinstance(ids, list) else [ids]
            if ids and all(i in fakes for i in ids):
                data = dict(call.data)
                data["entity_id"] = ids[0]
                self.log.append((domain, service, data))
                apply(self.hass, data)
                return
            result = original.job.target(call)
            if hasattr(result, "__await__"):
                await result

        self.hass.services.async_register(domain, service, handler, original.schema)

    def of(self, domain: str, service: str) -> list[dict]:
        return [d for (dom, srv, d) in self.log if dom == domain and srv == service]


def _select_option(hass: HomeAssistant, data: dict) -> None:
    st = hass.states.get(data["entity_id"])
    hass.states.async_set(data["entity_id"], data["option"], st.attributes)


def _set_climate(hass: HomeAssistant, data: dict) -> None:
    ids = data["entity_id"]
    for entity_id in ids if isinstance(ids, list) else [ids]:
        state = hass.states.get(entity_id)
        attrs = dict(state.attributes)
        if "temperature" in data:
            attrs["temperature"] = data["temperature"]
        if data.get("preset_mode"):
            attrs["preset_mode"] = data["preset_mode"]
        hass.states.async_set(entity_id, data.get("hvac_mode", state.state), attrs)


@pytest.fixture
async def calls(hass: HomeAssistant) -> Calls:
    calls = Calls(hass)
    calls.register("climate", "set_temperature", lambda d: _set_climate(hass, d))
    calls.register("climate", "set_preset_mode", lambda d: _set_climate(hass, d))

    def _set_value(d):
        hass.states.async_set(d["entity_id"], d["value"])

    calls.register("number", "set_value", _set_value)
    calls.register("select", "select_option", lambda d: _select_option(hass, d))
    calls.register("homeassistant", "update_entity")
    calls.register("switch", "turn_on", lambda d: hass.states.async_set(RELAY, "on"))
    calls.register("switch", "turn_off", lambda d: hass.states.async_set(RELAY, "off"))
    for service in ("create_profile_two", "hold_on", "hold_off", "set_away_mode"):
        calls.register("heatmiserneo", service)
    return calls


@pytest.fixture
async def house(hass: HomeAssistant) -> None:
    """A call for heat relay, one Sonoff valve with its companions, a room sensor, and a Heatmiser stat."""
    await hass.config.async_set_time_zone("Europe/London")
    hass.states.async_set(RELAY, "off")
    hass.states.async_set(OFFICE_SENSOR, "18.0", {"device_class": "temperature"})

    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    z2m = MockConfigEntry(domain="mqtt")
    z2m.add_to_hass(hass)
    device = dev_reg.async_get_or_create(config_entry_id=z2m.entry_id, identifiers={("mqtt", "office_trv")})
    for domain, object_id in (
        ("climate", "office_trv"),
        ("select", "office_trv_temperature_sensor_select"),
        ("number", "office_trv_external_temperature_input"),
        ("text", "office_trv_weekly_schedule_monday"),
        ("text", "office_trv_weekly_schedule_saturday"),
    ):
        ent_reg.async_get_or_create(domain, "mqtt", object_id, suggested_object_id=object_id,
                                    device_id=device.id, config_entry=z2m)
    hass.states.async_set(OFFICE_TRV, "heat", {"temperature": 19.0, "current_temperature": 22.7, "hvac_action": "idle"})
    hass.states.async_set("select.office_trv_temperature_sensor_select", "local_temperature",
                          {"options": ["local_temperature", "remote_temperature", "remote_source_offline"]})
    hass.states.async_set("number.office_trv_external_temperature_input", "23.3")
    hass.states.async_set("text.office_trv_weekly_schedule_monday", "00:00/16 07:00/19 10:00/16 10:00/16 17:00/19 23:00/16")
    hass.states.async_set("text.office_trv_weekly_schedule_saturday", "00:00/16 07:00/19 10:00/19 10:00/19 10:00/19 23:00/16")

    neo = MockConfigEntry(domain="heatmiserneo")
    neo.add_to_hass(hass)
    stat = dev_reg.async_get_or_create(config_entry_id=neo.entry_id, identifiers={("heatmiserneo", "kitchen")})
    hubdev = dev_reg.async_get_or_create(config_entry_id=neo.entry_id, identifiers={("heatmiserneo", "hub")})
    for domain, object_id, dev in (
        ("climate", "kitchen", stat),
        ("select", "kitchen_active_profile", stat),
        ("sensor", "neohub_profile_format", hubdev),
        ("binary_sensor", "neohub_away", hubdev),
    ):
        ent_reg.async_get_or_create(domain, "heatmiserneo", object_id, suggested_object_id=object_id,
                                    device_id=dev.id, config_entry=neo)
    hass.states.async_set(KITCHEN, "heat", {"temperature": 21.0, "current_temperature": 20.4, "hvac_action": "idle"})
    hass.states.async_set("select.kitchen_active_profile", "HA Kitchen",
                          {"options": ["HA Kitchen", "HA Kitchen Fixed"]})


def hearth_entry(hass: HomeAssistant, zones: list[dict], **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="hearth", title="Hearth", data={},
        minor_version=2,
        options={"boiler_switch": RELAY, "demand_on_delay": 0, "demand_off_delay": 0, "zones": zones, **options},
    )
    entry.add_to_hass(hass)
    return entry


OFFICE = {"id": "office", "kind": "radiator", "name": "Office", "trvs": [OFFICE_TRV], "sensor": OFFICE_SENSOR}
KITCHEN_ZONE = {"id": "kitchen", "kind": "underfloor", "name": "Kitchen", "stat": KITCHEN, "profile": "HA Kitchen"}
