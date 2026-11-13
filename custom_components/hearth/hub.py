"""The house: zones, Home/Away/Holiday, and the boiler for the radiators."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import time as monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BOILER,
    CONF_BOILER_OFF,
    CONF_BOILER_ON,
    CONF_FALLBACK,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_ZONE_ID,
    CONF_ZONES,
    DEFAULTS,
    DOMAIN,
    HOUSE_AWAY,
    HOUSE_HOLIDAY,
    HOUSE_HOME,
    HOUSE_MODES,
    KIND_RADIATOR,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .zone import RadiatorZone, UnderfloorZone, Zone, ZoneState, make_zone

_LOGGER = logging.getLogger(__name__)

SIGNAL_UPDATE = f"{DOMAIN}_update"
TICK = timedelta(seconds=30)


class Hearth:
    """Runs every zone and decides when the radiators need the boiler."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.settings: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        self.zones: dict[str, Zone] = {}
        self.house_mode = HOUSE_HOME
        self.store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.context = Context()
        self._unsubs: list[Callable[[], None]] = []
        self._eval_pending: Callable[[], None] | None = None
        # Boiler demand, debounced.
        self.demand = False
        self._raw_demand = False
        self._raw_since = monotonic.monotonic()
        self._boiler_sent: tuple[float, float] | None = None
        self.last_boiler_command: str | None = None

    @property
    def boiler(self) -> str | None:
        return self.settings.get(CONF_BOILER) or None

    @property
    def radiator_zones(self) -> list[RadiatorZone]:
        return [z for z in self.zones.values() if isinstance(z, RadiatorZone)]

    @property
    def underfloor_zones(self) -> list[UnderfloorZone]:
        return [z for z in self.zones.values() if isinstance(z, UnderfloorZone)]

    # --- lifecycle --------------------------------------------------------
    async def async_start(self) -> None:
        stored = await self.store.async_load() or {}
        self.house_mode = stored.get("house_mode", HOUSE_HOME)
        if self.house_mode not in HOUSE_MODES:
            self.house_mode = HOUSE_HOME
        saved = stored.get("zones", {})
        for config in self.settings.get(CONF_ZONES, []):
            zone = make_zone(self, config, ZoneState.from_dict(saved.get(config[CONF_ZONE_ID])))
            self.zones[zone.id] = zone
        for zone in self.zones.values():
            await zone.async_start()

        watched = {e for z in self.zones.values() for e in z.watched_entities()}
        if self.boiler:
            watched.add(self.boiler)
        if watched:
            self._unsubs.append(
                async_track_state_change_event(self.hass, list(watched), self._state_changed)
            )
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, TICK))
        self.async_save()
        await self.async_evaluate()

    async def async_stop(self) -> None:
        """Hand the boiler back to the Nest's own judgement at a safe level."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._eval_pending:
            self._eval_pending()
        if self.boiler and self.radiator_zones:
            await self._send_boiler(float(self.settings[CONF_FALLBACK]), force=True)

    # --- storage ------------------------------------------------------------
    @callback
    def async_save(self) -> None:
        self.store.async_delay_save(self._data, 2)

    def _data(self) -> dict[str, Any]:
        return {
            "house_mode": self.house_mode,
            "zones": {zid: z.state.as_dict() for zid, z in self.zones.items()},
        }

    # --- events ---------------------------------------------------------------
    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        for zone in self.zones.values():
            if entity_id in zone.watched_entities():
                zone.handle_state_change(entity_id, event.data["old_state"], event.data["new_state"])
        self._schedule_evaluate()

    @callback
    def _schedule_evaluate(self) -> None:
        if self._eval_pending is not None:
            return

        @callback
        def _run(_now: datetime) -> None:
            self._eval_pending = None
            self.hass.async_create_task(self.async_evaluate())

        self._eval_pending = async_call_later(self.hass, 2, _run)

    async def _tick(self, now: datetime) -> None:
        for zone in self.zones.values():
            await zone.async_tick(dt_util.utcnow())
        await self.async_evaluate()

    # --- the house -------------------------------------------------------------
    async def async_set_house_mode(self, mode: str) -> None:
        if mode not in HOUSE_MODES or mode == self.house_mode:
            return
        old, self.house_mode = self.house_mode, mode
        self.async_save()
        if self.underfloor_zones:
            if mode == HOUSE_HOLIDAY or old == HOUSE_HOLIDAY:
                await self._hub_away(mode == HOUSE_HOLIDAY)
            for zone in self.underfloor_zones:
                await zone.async_house_changed(old, mode)
        await self.async_evaluate()

    async def _hub_away(self, away: bool) -> None:
        sensor = self.underfloor_zones[0].hub_away
        if not sensor:
            return
        try:
            await self.hass.services.async_call(
                "heatmiserneo", "set_away_mode", {"entity_id": sensor, "away": away},
                blocking=True, context=self.context,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Couldn't set the neoHub's away mode: %s", err)

    async def async_evaluate(self) -> None:
        """Push every radiator zone's target and decide on the boiler."""
        now = dt_util.utcnow()
        for zone in self.zones.values():
            zone.update_demand(now)
        for zone in self.radiator_zones:
            await zone.async_apply(now)
        await self._update_boiler()
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def zones_calling(self) -> list[str]:
        return [z.name for z in self.radiator_zones if z.calling]

    async def _update_boiler(self) -> None:
        if not self.boiler or not self.radiator_zones:
            return
        raw = any(z.calling for z in self.radiator_zones)
        clock = monotonic.monotonic()
        if raw != self._raw_demand:
            self._raw_demand, self._raw_since = raw, clock
        held = clock - self._raw_since
        if raw and not self.demand and held >= float(self.settings[CONF_ON_DELAY]):
            self.demand = True
        elif not raw and self.demand and held >= float(self.settings[CONF_OFF_DELAY]):
            self.demand = False
        temp = float(self.settings[CONF_BOILER_ON] if self.demand else self.settings[CONF_BOILER_OFF])
        await self._send_boiler(temp)

    async def _send_boiler(self, temp: float, force: bool = False) -> None:
        state = self.hass.states.get(self.boiler)
        if state is None:
            return
        current = state.attributes.get(ATTR_TEMPERATURE)
        eco = state.attributes.get("preset_mode") == "eco"
        if not force and current == temp and state.state == "heat" and not eco:
            return
        if not force and self._boiler_sent and self._boiler_sent[0] == temp and monotonic.monotonic() - self._boiler_sent[1] < 60:
            return
        self._boiler_sent = (temp, monotonic.monotonic())
        self.last_boiler_command = f"{temp:g}° at {dt_util.now().strftime('%H:%M:%S')}"
        try:
            if eco:
                await self.hass.services.async_call(
                    "climate", "set_preset_mode", {"entity_id": self.boiler, "preset_mode": "none"},
                    blocking=True, context=self.context,
                )
            await self.hass.services.async_call(
                "climate", "set_temperature",
                {"entity_id": self.boiler, ATTR_TEMPERATURE: temp, "hvac_mode": "heat"},
                blocking=True, context=self.context,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Couldn't set the boiler thermostat to %s: %s", temp, err)

    def diagnostics(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        return {
            "house_mode": self.house_mode,
            "boiler": self.boiler,
            "demand": self.demand,
            "last_boiler_command": self.last_boiler_command,
            "settings": {k: v for k, v in self.settings.items() if k != CONF_ZONES},
            "zones": {
                zid: {
                    "config": z.config,
                    "state": z.state.as_dict(),
                    "room_temp": z.room_temp(),
                    "target": z.target(),
                    **z.attributes(now),
                }
                for zid, z in self.zones.items()
            },
        }


__all__ = ["Hearth", "SIGNAL_UPDATE", "KIND_RADIATOR", "HOUSE_AWAY"]
