"""The house: zones, Home/Away/Holiday, and the call for heat for the radiators.

Hearth asks the boiler for heat one way only: it turns a switch on while any
radiator room wants heat, and off when none does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
import logging
import time as monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BOILER_SWITCH,
    CONF_OFF_DELAY,
    CONF_ON_DELAY,
    CONF_ZONE_ID,
    CONF_ZONES,
    DEFAULTS,
    DOMAIN,
    HANDOVER_SECONDS,
    HOUSE_AWAY,
    HOUSE_HOLIDAY,
    HOUSE_HOME,
    HOUSE_MODES,
    KIND_RADIATOR,
    PROBLEM_AFTER_SECONDS,
    SEED_DAYS,
    STORAGE_KEY,
    STORAGE_MINOR_VERSION,
    STORAGE_VERSION,
)
from . import preheat
from .zone import RadiatorZone, UnderfloorZone, Zone, ZoneState, make_zone

_LOGGER = logging.getLogger(__name__)

SIGNAL_UPDATE = f"{DOMAIN}_update"
# The longest the recorder may take to answer before learning gives up.
SEED_TIMEOUT = 120
TICK = timedelta(seconds=30)


class HearthStore(Store):
    """What Hearth remembers, brought up to date when it's older."""

    async def _async_migrate_func(self, old_major: int, old_minor: int, data: dict[str, Any]) -> dict[str, Any]:
        if old_major == 1 and old_minor < 2:
            for zone in (data.get("zones") or {}).values():
                zone["warmups"] = [w for w in zone.get("warmups") or [] if w.get("source") != "history"]
        return data


class Hearth:
    """Runs every zone and decides when the radiators need the boiler."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.settings: dict[str, Any] = {**DEFAULTS, **entry.data, **entry.options}
        self.zones: dict[str, Zone] = {}
        self.house_mode = HOUSE_HOME
        self.store: Store = HearthStore(hass, STORAGE_VERSION, STORAGE_KEY, minor_version=STORAGE_MINOR_VERSION)
        # Set once Hearth has stopped: anything of its still running (a
        # profile on its way to the neoHub) must leave the house alone.
        self.stopped = False
        # One look at the house at a time: a second one waits its turn.
        self._evaluating = asyncio.Lock()
        self._switch_missing = False
        # When a Holiday ends by itself, if it was given a day to.
        self.holiday_until: datetime | None = None
        # Problems seen, since when, and the ones raised as repairs.
        self._problem_since: dict[str, float] = {}
        self._issues: set[str] = set()
        self.context = Context()
        self._unsubs: list[Callable[[], None]] = []
        self._eval_pending: Callable[[], None] | None = None
        # The call for heat, debounced.
        self.demand = False
        self._raw_demand = False
        self._raw_since = monotonic.monotonic()
        self._switch_sent: tuple[bool, float] | None = None
        self.last_boiler_command: str | None = None
        self._seeding: asyncio.Task | None = None
        self.seed_status = "not needed"

    @property
    def boiler_switch(self) -> str | None:
        return self.settings.get(CONF_BOILER_SWITCH) or None

    @property
    def radiator_zones(self) -> list[RadiatorZone]:
        return [z for z in self.zones.values() if isinstance(z, RadiatorZone)]

    @property
    def underfloor_zones(self) -> list[UnderfloorZone]:
        return [z for z in self.zones.values() if isinstance(z, UnderfloorZone)]

    # --- lifecycle --------------------------------------------------------
    async def async_start(self) -> None:
        # Hearth reloading: carry on from the call for heat it left, rather
        # than dropping it and waiting out the on delay.
        if cancel := self.hass.data.setdefault(DOMAIN, {}).pop("handover", None):
            cancel()
        switch = self.hass.states.get(self.boiler_switch) if self.boiler_switch else None
        if switch is not None and switch.state == "on":
            self.demand = self._raw_demand = True
        stored = await self.store.async_load() or {}
        self.house_mode = stored.get("house_mode", HOUSE_HOME)
        if self.house_mode not in HOUSE_MODES:
            self.house_mode = HOUSE_HOME
        until = stored.get("holiday_until")
        self.holiday_until = dt_util.parse_datetime(until) if until and self.house_mode == HOUSE_HOLIDAY else None
        saved = stored.get("zones", {})
        for config in self.settings.get(CONF_ZONES, []):
            zone = make_zone(self, config, ZoneState.from_dict(saved.get(config[CONF_ZONE_ID])))
            self.zones[zone.id] = zone
        for zone in self.zones.values():
            try:
                await zone.async_start()
            except Exception:  # noqa: BLE001 - one room mustn't stop the rest
                _LOGGER.exception("%s couldn't start cleanly", zone.name)

        watched = {e for z in self.zones.values() for e in z.watched_entities()}
        if self.boiler_switch:
            watched.add(self.boiler_switch)
        if watched:
            self._unsubs.append(
                async_track_state_change_event(self.hass, list(watched), self._state_changed)
            )
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, TICK))
        self.async_save()
        await self.async_evaluate()
        if any(z.sensor and not z.state.seeded for z in self.radiator_zones):
            self.seed_status = "starting"
            self._seeding = self.hass.async_create_background_task(self._async_seed(), "hearth: learn warm-ups")

    async def async_stop(self, final: bool = True) -> None:
        """Leave the boiler safe: nothing will turn the switch off once Hearth
        has gone. When Home Assistant stops, that's straight away; when Hearth
        only unloads (to reload, say), a little later, unless it's back by then."""
        self.stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._eval_pending:
            self._eval_pending()
        if self._seeding and not self._seeding.done():
            self._seeding.cancel()
        for issue in self._issues:
            ir.async_delete_issue(self.hass, DOMAIN, issue)
        self._issues.clear()
        if not self.radiator_zones or not self.boiler_switch:
            return
        if final:
            await self._send_switch(False, force=True)
            return
        switch = self.boiler_switch

        async def _off(_now: datetime) -> None:
            self.hass.data.get(DOMAIN, {}).pop("handover", None)
            state = self.hass.states.get(switch)
            if state is not None and state.state == "on":
                _LOGGER.info("Hearth didn't come back: turning %s off", switch)
                await self.hass.services.async_call(
                    switch.split(".")[0], "turn_off", {"entity_id": switch}, blocking=True, context=self.context
                )

        data = self.hass.data.setdefault(DOMAIN, {})
        if cancel := data.pop("handover", None):
            cancel()
        data["handover"] = async_call_later(self.hass, HANDOVER_SECONDS, _off)

    # --- storage ------------------------------------------------------------
    @callback
    def async_save(self) -> None:
        self.store.async_delay_save(self._data, 2)

    def _data(self) -> dict[str, Any]:
        return {
            "house_mode": self.house_mode,
            "holiday_until": self.holiday_until.isoformat() if self.holiday_until else None,
            "zones": {zid: z.state.as_dict() for zid, z in self.zones.items()},
        }

    async def async_fail_safe(self) -> None:
        """Hearth couldn't start: nothing is watching the call for heat, so
        it's turned off rather than left as it was."""
        self.stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self.boiler_switch:
            await self._send_switch(False, force=True)

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
        if self.house_mode == HOUSE_HOLIDAY and self.holiday_until and dt_util.utcnow() >= self.holiday_until:
            _LOGGER.info("The holiday is over: the house is back to Home")
            await self.async_set_house_mode(HOUSE_HOME)
        for zone in self.zones.values():
            try:
                await zone.async_tick(dt_util.utcnow())
            except Exception:  # noqa: BLE001 - one room mustn't stop the rest
                _LOGGER.exception("%s: its regular check failed", zone.name)
        await self.async_evaluate()

    # --- learning from history ----------------------------------------------------
    async def _async_seed(self) -> None:
        """Read each new room's warm-ups from the recorder: every time the
        boiler fired, how far and how fast the room's sensor rose."""
        try:
            await self._seed()
        except asyncio.CancelledError:
            self.seed_status = "stopped before it finished"
            raise
        except Exception as err:  # noqa: BLE001 - learning live still works
            self.seed_status = f"failed: {err!r}"
            _LOGGER.warning("Couldn't learn how fast rooms warm from the history: %r", err)
        _LOGGER.info("Learning from history: %s", self.seed_status)

    async def _seed(self) -> None:
        rooms = [z for z in self.radiator_zones if z.sensor and not z.state.seeded]
        firing = [self.boiler_switch] if self.boiler_switch else []
        if not rooms:
            self.seed_status = "no rooms to learn"
            return
        if not firing:
            self.seed_status = "no call for heat switch to read"
            return
        if "recorder" not in self.hass.config.components:
            self.seed_status = "the recorder isn't running"
            return
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        end = dt_util.utcnow()
        start = end - timedelta(days=SEED_DAYS)
        ids = list(dict.fromkeys([*firing, *(z.sensor for z in rooms)]))
        self.seed_status = f"reading the history of {', '.join(ids)}"
        _LOGGER.debug("Learning from history: %s", self.seed_status)
        async with asyncio.timeout(SEED_TIMEOUT):
            found: dict[str, list[State]] = await get_instance(self.hass).async_add_executor_job(
                partial(
                    history.get_significant_states, self.hass, start, end, ids,
                    significant_changes_only=False, minimal_response=False, no_attributes=False,
                )
            )
        counts = {e: len(found.get(e, [])) for e in ids}
        _LOGGER.debug("Learning from history: rows %s", counts)
        spans = []
        for entity_id in firing:
            changes = [(s.last_updated, _firing(entity_id, s)) for s in found.get(entity_id, [])]
            spans += preheat.spans_where(changes, end)
        periods = preheat.firing_periods(spans)
        learned = {}
        for zone in rooms:
            if not zone.sensor or zone.state.seeded or zone.id not in self.zones:
                continue
            readings = [(s.last_updated, v) for s in found.get(zone.sensor, []) if (v := _number(s.state)) is not None]
            warmups = preheat.warmups_from_history(periods, readings)
            zone.seed(warmups)
            learned[zone.name] = len(warmups)
        self.seed_status = (
            f"done: {len(periods)} times the boiler fired; warm-ups "
            + ", ".join(f"{name} {n}" for name, n in learned.items())
            + f"; rows read {counts}"
        )
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # --- the house -------------------------------------------------------------
    async def async_set_house_mode(self, mode: str, until: datetime | None = None) -> None:
        """Home, Away or Holiday. A Holiday can be given the moment it ends,
        when the house goes back to Home by itself."""
        if mode not in HOUSE_MODES:
            return
        until = until if mode == HOUSE_HOLIDAY else None
        if mode == self.house_mode:
            if until != self.holiday_until:
                self.holiday_until = until
                self.async_save()
                await self.async_evaluate()
            return
        old, self.house_mode = self.house_mode, mode
        self.holiday_until = until
        self.async_save()
        if mode == HOUSE_HOLIDAY:
            # A boost wouldn't show under Holiday, and would still be
            # running on a stat: it ends here.
            for zone in self.zones.values():
                if zone.state.boost_until is not None:
                    zone.state.boost_until = zone.state.boost_temp = None
                    await zone._end_boost()  # noqa: SLF001
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
        async with self._evaluating:
            if self.stopped:
                return
            now = dt_util.utcnow()
            # One room going wrong mustn't stop the others, or the boiler
            # being turned off when nothing wants it.
            for zone in self.zones.values():
                try:
                    zone.update_demand(now)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("%s: couldn't work out whether it wants heat", zone.name)
                    zone.calling = False
            for zone in self.radiator_zones:
                try:
                    await zone.async_apply(now)
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("%s: couldn't set its valves", zone.name)
            await self._update_boiler()
            self._update_issues()
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    def _update_issues(self) -> None:
        """Raise a repair for each problem that has lasted, and clear it when
        the problem goes: a dead valve shouldn't need someone to open Hearth."""
        problems: dict[str, tuple[str, dict[str, str]]] = {}
        for zone in self.zones.values():
            try:
                problem = zone.problem()
            except Exception:  # noqa: BLE001
                problem = None
            if problem:
                problems[f"room_{zone.id}"] = ("room_problem", {"room": zone.name, "problem": problem})
        if self.boiler_switch and self.radiator_zones and self._switch_missing:
            problems["switch_unavailable"] = ("switch_unavailable", {"switch": self.boiler_switch})
        clock = monotonic.monotonic()
        for key in list(self._problem_since):
            if key not in problems:
                del self._problem_since[key]
        for key in list(self._issues):
            if key not in problems:
                ir.async_delete_issue(self.hass, DOMAIN, key)
                self._issues.discard(key)
        for key, (translation, words) in problems.items():
            since = self._problem_since.setdefault(key, clock)
            if clock - since < PROBLEM_AFTER_SECONDS:
                continue
            # Created again each time, so its words follow the problem.
            ir.async_create_issue(
                self.hass, DOMAIN, key, is_fixable=False, is_persistent=False,
                severity=ir.IssueSeverity.WARNING, translation_key=translation, translation_placeholders=words,
            )
            self._issues.add(key)

    def zones_calling(self) -> list[str]:
        return [z.name for z in self.radiator_zones if z.calling]

    async def _update_boiler(self) -> None:
        if not self.boiler_switch or not self.radiator_zones:
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
        state = self.hass.states.get(self.boiler_switch)
        if state is not None and state.state in ("on", "off"):
            self._switch_missing = False
        await self._send_switch(self.demand)

    async def _send_switch(self, on: bool, force: bool = False) -> None:
        """Call for heat by turning the switch on, or stop by turning it off."""
        state = self.hass.states.get(self.boiler_switch)
        if state is None or state.state not in ("on", "off"):
            if not self._switch_missing:
                _LOGGER.warning("%s is unavailable: Hearth can't ask for heat, or stop asking, until it's back", self.boiler_switch)
            self._switch_missing = True
            return
        self._switch_missing = False
        if not force and state.state == ("on" if on else "off"):
            return
        if not force and self._switch_sent and self._switch_sent[0] == on and monotonic.monotonic() - self._switch_sent[1] < 60:
            return
        self._switch_sent = (on, monotonic.monotonic())
        self.last_boiler_command = f"{'on' if on else 'off'} at {dt_util.now().strftime('%H:%M:%S')}"
        try:
            await self.hass.services.async_call(
                self.boiler_switch.split(".")[0], "turn_on" if on else "turn_off", {"entity_id": self.boiler_switch},
                blocking=True, context=self.context,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Couldn't turn %s %s: %s", self.boiler_switch, "on" if on else "off", err)

    def diagnostics(self) -> dict[str, Any]:
        now = dt_util.utcnow()
        return {
            "house_mode": self.house_mode,
            "holiday_until": self.holiday_until.isoformat() if self.holiday_until else None,
            "boiler_switch": self.boiler_switch,
            "demand": self.demand,
            "last_boiler_command": self.last_boiler_command,
            "learning_from_history": self.seed_status,
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


def _firing(entity_id: str, state: State) -> bool:
    """The call for heat switch was on.

    History rows are the recorder's light states: they have the state and
    attributes, not everything a live State has (no domain, for one)."""
    return state.state == "on"


def _number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["Hearth", "SIGNAL_UPDATE", "KIND_RADIATOR", "HOUSE_AWAY"]

