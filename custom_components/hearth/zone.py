"""Zones: one model for every room, whatever heats it.

Each zone has a mode (Schedule, Fixed or Off), a weekday/weekend schedule,
an optional boost, and an optional "until the next change" override. What
differs is only how that reaches the hardware:

- RadiatorZone: Home Assistant runs the schedule. It sets the valves'
  targets, feeds them the room's real temperature, and tells the house
  whether the boiler is wanted.
  Rooms learn how fast they warm and start early for a warmer change, so
  it's reached on time (see preheat.py).
- UnderfloorZone: the Heatmiser stat runs the schedule itself, written to
  the neoHub as a profile, so the stat keeps its own optimum start and
  carries on if Home Assistant is down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import asyncio
import logging
import time as monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AWAY_TEMP,
    CONF_BOOST_MINUTES,
    CONF_BOOST_TEMP,
    CONF_HOLIDAY_TEMP,
    CONF_KIND,
    CONF_NAME,
    CONF_OFF_TEMP,
    CONF_PREHEAT_MAX,
    CONF_PROFILE,
    CONF_SENSOR,
    CONF_STAT,
    CONF_TRVS,
    CONF_ZONE_ID,
    DEFAULT_SCHEDULE,
    DEMAND_ABOVE,
    DEMAND_BELOW,
    EXTERNAL_REFRESH_SECONDS,
    HOUSE_AWAY,
    HOUSE_HOLIDAY,
    HOUSE_HOME,
    KIND_RADIATOR,
    KIND_UNDERFLOOR,
    MAX_TEMP,
    MIN_TEMP,
    MODE_FIXED,
    MODE_OFF,
    MODE_SCHEDULE,
    WEEKDAYS,
    WEEKENDS,
)
from . import preheat
from .preheat import WarmUp
from .schedule import (
    Schedule,
    ScheduleError,
    next_change,
    normalise,
    parse_valve_day,
    temp_at,
)

if TYPE_CHECKING:
    from .hub import Hearth

_LOGGER = logging.getLogger(__name__)

BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, None, "")
# A valve reporting a target we didn't send this long after our last
# command was turned by hand.
MANUAL_GRACE = 15
# The neoHub reports a stat's setpoint every half minute or so: for this long
# after Hearth sends something, show what was asked rather than what the stat
# last reported, and don't take its old setpoint for a change on the stat.
STAT_SETTLE = 120
# The neoHub drops commands sent close together, so a stat's setpoint goes
# one write at a time, only the latest, and again if the stat hasn't taken it.
STAT_RECHECK = 45
STAT_TRIES = 3


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(temp: float) -> float:
    return max(MIN_TEMP, min(MAX_TEMP, round(temp * 2) / 2))


def _hhmm(moment: datetime) -> str:
    return dt_util.as_local(moment).strftime("%H:%M")


def _when(moment: datetime, now: datetime) -> str:
    """'17:00', or 'Mon 06:30' when it isn't today."""
    local, today = dt_util.as_local(moment), dt_util.as_local(now)
    if local.date() == today.date():
        return local.strftime("%H:%M")
    if local.date() == (today + timedelta(days=1)).date():
        return f"tomorrow {local.strftime('%H:%M')}"
    return local.strftime("%a %H:%M")


def _deg(temp: float) -> str:
    return f"{temp:g}°"


def _duration(minutes: float) -> str:
    """'35 min', '1 h', '1 h 20 min', to the nearest 5 minutes."""
    m = max(5, int(round(minutes / 5) * 5))
    hours, mins = divmod(m, 60)
    if not hours:
        return f"{mins} min"
    return f"{hours} h {mins} min" if mins else f"{hours} h"


@dataclass
class ZoneState:
    """What a zone remembers across restarts."""

    mode: str = MODE_SCHEDULE
    fixed: float = 19.0
    schedule: Schedule = field(default_factory=lambda: normalise(DEFAULT_SCHEDULE))
    override_temp: float | None = None
    override_until: datetime | None = None
    boost_until: datetime | None = None
    boost_temp: float | None = None
    # How fast the room warms: its recent warm-ups, the sensor they were
    # measured with, and whether the recorder's history has been read.
    warmups: list[WarmUp] = field(default_factory=list)
    learned_with: str | None = None
    seeded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "fixed": self.fixed,
            "schedule": self.schedule,
            "override_temp": self.override_temp,
            "override_until": self.override_until.isoformat() if self.override_until else None,
            "boost_until": self.boost_until.isoformat() if self.boost_until else None,
            "boost_temp": self.boost_temp,
            "warmups": [w.as_dict() for w in self.warmups],
            "learned_with": self.learned_with,
            "seeded": self.seeded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ZoneState:
        state = cls()
        if not data:
            return state
        state.mode = data.get("mode", MODE_SCHEDULE)
        if state.mode not in (MODE_SCHEDULE, MODE_FIXED, MODE_OFF):
            state.mode = MODE_SCHEDULE
        state.fixed = _float(data.get("fixed")) or state.fixed
        try:
            state.schedule = normalise(data.get("schedule") or DEFAULT_SCHEDULE)
        except ScheduleError:
            _LOGGER.warning("A stored schedule couldn't be read; using the default")
        state.override_temp = _float(data.get("override_temp"))
        state.override_until = dt_util.parse_datetime(data["override_until"]) if data.get("override_until") else None
        state.boost_until = dt_util.parse_datetime(data["boost_until"]) if data.get("boost_until") else None
        state.boost_temp = _float(data.get("boost_temp"))
        state.warmups = [w for d in data.get("warmups") or [] if (w := WarmUp.from_dict(d))]
        state.learned_with = data.get("learned_with")
        state.seeded = bool(data.get("seeded"))
        return state


class Zone:
    """What every zone has in common."""

    kind: str

    def __init__(self, hub: Hearth, config: dict[str, Any], state: ZoneState) -> None:
        self.hub = hub
        self.hass: HomeAssistant = hub.hass
        self.config = config
        self.id: str = config[CONF_ZONE_ID]
        self.name: str = config[CONF_NAME]
        self.state = state
        self.calling = False
        self.context = Context()
        # A warm-up being timed: when it started, from what, and to what.
        self._warming: tuple[datetime, float, float] | None = None

    # --- settings -----------------------------------------------------
    def _setting(self, key: str) -> float:
        return float(self.hub.settings[key])

    @property
    def sensor(self) -> str | None:
        return self.config.get(CONF_SENSOR) or None

    def watched_entities(self) -> list[str]:
        return [self.sensor] if self.sensor else []

    # --- reading ----------------------------------------------------------
    def _sensor_temp(self) -> float | None:
        if not self.sensor:
            return None
        state = self.hass.states.get(self.sensor)
        return None if state is None or state.state in BAD_STATES else _float(state.state)

    def room_temp(self) -> float | None:
        return self._sensor_temp()

    def _base(self, now: datetime) -> tuple[float, str]:
        """Target and reason from the zone's own mode, before house modes."""
        st = self.state
        if st.mode == MODE_OFF:
            off = self._setting(CONF_OFF_TEMP)
            return off, f"Off: kept above {_deg(off)}"
        if st.mode == MODE_FIXED:
            return st.fixed, f"Fixed at {_deg(st.fixed)}"
        if st.override_temp is not None and st.override_until and now < st.override_until:
            back = temp_at(st.schedule, dt_util.as_local(st.override_until))
            return st.override_temp, f"{_deg(st.override_temp)} until {_when(st.override_until, now)}, then {_deg(back)}"
        temp = temp_at(st.schedule, dt_util.as_local(now))
        upcoming = next_change(st.schedule, dt_util.as_local(now))
        if upcoming:
            return temp, f"{_deg(temp)} until {_when(upcoming[0], now)}, then {_deg(upcoming[1])}"
        return temp, f"{_deg(temp)} all week"

    def desired(self, now: datetime) -> tuple[float, str]:
        """What the zone should aim for now, and a line saying why."""
        house = self.hub.house_mode
        if house == HOUSE_HOLIDAY:
            temp = self._setting(CONF_HOLIDAY_TEMP)
            return temp, f"Holiday Mode: kept above {_deg(temp)}"
        if self.boost_active(now):
            temp = self.state.boost_temp or self._setting(CONF_BOOST_TEMP)
            return temp, f"Boost to {_deg(temp)} until {_hhmm(self.state.boost_until)}"
        base, reason = self._base(now)
        if house == HOUSE_AWAY and self.state.mode != MODE_OFF:
            away = self._setting(CONF_AWAY_TEMP)
            if base > away:
                return away, f"Away: held at {_deg(away)}"
        return base, reason

    def boost_active(self, now: datetime) -> bool:
        return bool(self.state.boost_until and now < self.state.boost_until)

    def next_scheduled(self, now: datetime) -> tuple[datetime, float] | None:
        return next_change(self.state.schedule, dt_util.as_local(now))

    def target(self) -> float | None:
        return self.desired(dt_util.utcnow())[0]

    def hvac_action_heating(self) -> bool:
        return self.calling

    def preheating(self, now: datetime) -> bool:
        """Whether the room is warming up early for its next change."""
        return False

    def update_demand(self, now: datetime) -> bool:
        """Whether the room is heating now; each kind decides."""
        return self.calling

    # --- how fast the room warms ------------------------------------------
    def _time_warm_up(self, now: datetime, was: bool, target: float) -> None:
        """Time each warm-up, from asking for heat to reaching the target, by
        the same reading the room decides it's warm by."""
        room = self.room_temp()
        if room is None:
            self._warming = None
            return
        if self.calling and not was:
            self._warming = (now, room, target)
            return
        if self._warming is None:
            return
        start, start_temp, aim = self._warming
        minutes = (now - start).total_seconds() / 60
        if minutes > preheat.MAX_MINUTES:
            self._warming = None  # something else was going on: a window open, a valve shut
            return
        if target != aim:
            # The target moved before the room got there (a schedule change,
            # a tap on − or +). How far it rose so far is still a fair measure.
            if preheat.counts(room - start_temp, minutes):
                self.learn(WarmUp(room - start_temp, minutes, start, "seen"))
            self._warming = (now, room, target) if self.calling else None
            return
        if not self.calling:
            self._warming = None
            if preheat.counts(room - start_temp, minutes):
                self.learn(WarmUp(room - start_temp, minutes, start, "seen"))

    def learn(self, warmup: WarmUp) -> None:
        self.state.warmups = preheat.add(self.state.warmups, warmup)
        self.hub.async_save()

    def seed(self, warmups: list[WarmUp]) -> None:
        """Warm-ups read from the recorder's history, once."""
        for warmup in warmups:
            self.state.warmups = preheat.add(self.state.warmups, warmup)
        self.state.seeded = True
        self.hub.async_save()

    def warm_rate(self) -> float | None:
        """Degrees an hour, once the room has seen enough warm-ups."""
        return preheat.rate(self.state.warmups)

    def heating_to(self, now: datetime) -> float | None:
        """The temperature the room is warming towards: its target, or the
        next change's while it's warming up early."""
        target = self.target()
        if self.preheating(now) and (upcoming := self.next_scheduled(now)):
            return max(upcoming[1], target) if target is not None else upcoming[1]
        return target

    def minutes_to_target(self, now: datetime) -> float | None:
        """While the room heats, about how long until it's there, from how
        fast it has warmed before."""
        if not self.calling:
            return None
        room, goal, rate = self.room_temp(), self.heating_to(now), self.warm_rate()
        if room is None or goal is None or not rate or rate <= 0 or goal <= room:
            return None
        return (goal - room) / rate * 60

    def status(self, now: datetime) -> str:
        """What the room is doing, in the same words in every room."""
        if self.state.mode == MODE_OFF and self.hub.house_mode != HOUSE_HOLIDAY and not self.boost_active(now):
            return "Off"
        goal = self.heating_to(now)
        if self.preheating(now):
            upcoming = self.next_scheduled(now)
            by = f" by {_when(upcoming[0], now)}" if upcoming else ""
            return f"Warming up early · {_deg(goal)}{by}" if goal is not None else "Warming up early"
        if self.calling:
            to = f"Heating to {_deg(goal)}" if goal is not None else "Heating"
            minutes = self.minutes_to_target(now)
            if minutes is not None:
                return f"{to} · about {_duration(minutes)}"
            if self.warm_rate() is None:
                return f"{to} · still learning how long it takes"
            return to
        return "Comfortable"

    # --- changing ---------------------------------------------------------
    async def async_set_mode(self, mode: str) -> None:
        self.state.mode = mode
        self.state.override_temp = None
        self.state.override_until = None
        await self._changed(sync=True)

    async def async_set_target(self, temp: float) -> None:
        """What + and - do: change the fixed temperature, or override the
        schedule until its next change."""
        temp = _clamp(temp)
        now = dt_util.utcnow()
        if self.boost_active(now):
            self.state.boost_temp = temp
            await self._changed(sync=False)
            return
        if self.state.mode == MODE_SCHEDULE:
            upcoming = self.next_scheduled(now)
            self.state.override_temp = temp
            self.state.override_until = upcoming[0] if upcoming else now + timedelta(hours=12)
            await self._override(temp)
            return
        self.state.mode = MODE_FIXED
        self.state.fixed = temp
        await self._changed(sync=True)

    async def async_set_schedule(self, schedule: dict[str, Any]) -> None:
        self.state.schedule = normalise(schedule)
        self.state.override_temp = None
        self.state.override_until = None
        await self._changed(sync=True)

    async def async_boost(self, minutes: int | None = None, temp: float | None = None) -> None:
        minutes = int(minutes or self.hub.settings[CONF_BOOST_MINUTES])
        self.state.boost_until = dt_util.utcnow() + timedelta(minutes=minutes)
        self.state.boost_temp = _clamp(temp) if temp else None
        await self._start_boost(minutes)
        await self._changed(sync=False)

    async def async_cancel_boost(self) -> None:
        if self.state.boost_until is None:
            return
        self.state.boost_until = None
        self.state.boost_temp = None
        await self._end_boost()
        await self._changed(sync=False)

    async def _changed(self, sync: bool) -> None:
        self.hub.async_save()
        if sync:
            await self.async_sync()
        await self.hub.async_evaluate()

    # Hooks each kind fills in
    async def async_sync(self) -> None:
        """Push the zone's mode and schedule to the hardware."""

    async def _override(self, temp: float) -> None:
        await self._changed(sync=False)

    async def _start_boost(self, minutes: int) -> None:
        """Hardware side of a boost, where the hardware runs its own."""

    async def _end_boost(self) -> None:
        """Hardware side of ending a boost."""

    async def async_tick(self, now: datetime) -> None:
        """Expire boosts and overrides, then push what the zone wants."""
        st = self.state
        if st.boost_until and now >= st.boost_until:
            st.boost_until = None
            st.boost_temp = None
            self.hub.async_save()
            await self._boost_expired()
        if st.override_until and now >= st.override_until:
            st.override_temp = None
            st.override_until = None
            self.hub.async_save()

    async def _boost_expired(self) -> None:
        """Hook: a boost ran out on its own."""

    async def async_house_changed(self, old: str, new: str) -> None:
        """Hook: Home, Away or Holiday changed."""

    async def async_start(self) -> None:
        """Start learning afresh if the room's temperature now comes from
        somewhere else."""
        source = self.sensor or self._own_reading
        if self.state.learned_with != source:
            self.state.warmups, self.state.seeded = [], False
            self.state.learned_with = source
            self.hub.async_save()

    # Where the room's temperature comes from without a sensor of its own.
    _own_reading = "device"

    def handle_state_change(self, entity_id: str, old: State | None, new: State | None) -> None:
        """Hook: a watched entity changed."""

    def attributes(self, now: datetime) -> dict[str, Any]:
        upcoming = self.next_scheduled(now)
        return {
            "kind": self.kind,
            "zone_id": self.id,
            "mode": self.state.mode,
            "reason": self.desired(now)[1],
            "schedule": self.state.schedule,
            "next_change_at": upcoming[0].isoformat() if upcoming else None,
            "next_change_temp": upcoming[1] if upcoming else None,
            "boost_until": self.state.boost_until.isoformat() if self.boost_active(now) else None,
            "override_until": self.state.override_until.isoformat() if self.state.override_until else None,
            "calling_for_heat": self.calling,
            "status": self.status(now),
            "preheating": self.preheating(now),
            "minutes_to_target": round(m) if (m := self.minutes_to_target(now)) is not None else None,
            "warm_up_rate": round(rate, 2) if (rate := self.warm_rate()) is not None else None,
            "warm_ups": len(self.state.warmups),
            "warm_ups_from_history": sum(1 for w in self.state.warmups if w.source == "history"),
            "room_sensor": self.sensor,
        }

    async def _call(self, domain: str, service: str, data: dict[str, Any]) -> bool:
        try:
            await self.hass.services.async_call(
                domain, service, data, blocking=True, context=self.context
            )
        except Exception as err:  # noqa: BLE001 - one bad device mustn't stop the house
            _LOGGER.warning("%s: %s.%s failed: %s", self.name, domain, service, err)
            return False
        return True


class RadiatorZone(Zone):
    """Valves on radiators: Home Assistant runs the schedule."""

    kind = KIND_RADIATOR
    _own_reading = "valves"

    def __init__(self, hub: Hearth, config: dict[str, Any], state: ZoneState) -> None:
        super().__init__(hub, config, state)
        self.trvs: list[str] = list(config.get(CONF_TRVS) or [])
        self._sent_target: dict[str, tuple[float, float]] = {}
        self._sent_external: dict[str, tuple[float, float]] = {}
        # The schedule change the room is warming up early for.
        self._early_for: datetime | None = None

    def watched_entities(self) -> list[str]:
        return [*self.trvs, *super().watched_entities()]

    def room_temp(self) -> float | None:
        temp = self._sensor_temp()
        if temp is not None:
            return temp
        readings = [
            _float(s.attributes.get("current_temperature"))
            for e in self.trvs
            if (s := self.hass.states.get(e)) is not None
        ]
        readings = [r for r in readings if r is not None]
        return round(sum(readings) / len(readings), 1) if readings else None

    def valves_heating(self) -> bool:
        return any(
            (s := self.hass.states.get(e)) is not None and s.attributes.get("hvac_action") == "heating"
            for e in self.trvs
        )

    def update_demand(self, now: datetime) -> bool:
        """Whether this room wants the boiler, with a little hysteresis."""
        target = self.desired(now)[0]
        room = self.room_temp()
        was = self.calling
        if room is None:
            self.calling = self.valves_heating()
        elif room < target - DEMAND_BELOW:
            self.calling = True
        elif room >= target + DEMAND_ABOVE:
            self.calling = False
        self._time_warm_up(now, was, target)
        return self.calling

    # --- warming up early -------------------------------------------------
    def warm_from(self, now: datetime, upcoming: tuple[datetime, float] | None = None) -> datetime | None:
        """When the room should start warming for its next change."""
        upcoming = upcoming or self.next_scheduled(now)
        room = self.room_temp()
        if upcoming is None or room is None:
            return None
        lead = preheat.lead_minutes(upcoming[1] - room, self.warm_rate(), self._setting(CONF_PREHEAT_MAX))
        return None if lead is None else upcoming[0] - timedelta(minutes=lead)

    def _warm_early(self, now: datetime, base: float) -> tuple[float, str] | None:
        st = self.state
        upcoming = self.next_scheduled(now)
        # A + or - on the card lasts until the next change, so it doesn't
        # stop the room warming in time for that change. Anything longer does.
        overridden = st.override_until is not None and now < st.override_until
        if (
            st.mode != MODE_SCHEDULE
            or self.hub.house_mode != HOUSE_HOME
            or self.boost_active(now)
            or (overridden and (upcoming is None or st.override_until != upcoming[0]))
        ):
            self._early_for = None
            return None
        if upcoming is None or upcoming[1] <= base:
            return None
        at, temp = upcoming
        if self._early_for != at:
            start = self.warm_from(now, upcoming)
            if start is None or now < start:
                return None
            # Once started, keep going: as the room warms, the lead shrinks.
            self._early_for = at
        return temp, f"Warming up early for {_deg(temp)} by {_when(at, now)}"

    def desired(self, now: datetime) -> tuple[float, str]:
        temp, reason = super().desired(now)
        return self._warm_early(now, temp) or (temp, reason)

    def preheating(self, now: datetime) -> bool:
        self.desired(now)
        upcoming = self.next_scheduled(now)
        return bool(upcoming and self._early_for == upcoming[0])

    def _companion(self, trv: str, suffix: str, domain: str) -> str | None:
        """Find an entity on the valve's own device, by the end of its id."""
        registry = er.async_get(self.hass)
        entry = registry.async_get(trv)
        if entry is None or entry.device_id is None:
            return None
        for other in er.async_entries_for_device(registry, entry.device_id):
            if other.domain == domain and other.entity_id.endswith(suffix):
                return other.entity_id
        return None

    async def async_apply(self, now: datetime) -> None:
        target = self.desired(now)[0]
        for trv in self.trvs:
            await self._apply_target(trv, target)
            await self._apply_external(trv)

    async def _apply_target(self, trv: str, target: float) -> None:
        state = self.hass.states.get(trv)
        if state is None or state.state in BAD_STATES:
            return
        current = _float(state.attributes.get(ATTR_TEMPERATURE))
        # The valve keeps its own weekly schedule in "auto"; Hearth wants
        # it in plain "heat" so only Hearth's schedule runs.
        if current == target and state.state == "heat":
            self._sent_target.setdefault(trv, (target, 0.0))
            return
        sent = self._sent_target.get(trv)
        if sent and sent[0] == target and monotonic.monotonic() - sent[1] < 60:
            return  # sent a moment ago; the valve is still catching up
        self._sent_target[trv] = (target, monotonic.monotonic())
        await self._call(
            "climate", "set_temperature",
            {"entity_id": trv, ATTR_TEMPERATURE: target, "hvac_mode": "heat"},
        )

    async def _apply_external(self, trv: str) -> None:
        room = self._sensor_temp()
        if room is None:
            return
        select = self._companion(trv, "_temperature_sensor_select", "select")
        number = self._companion(trv, "_external_temperature_input", "number")
        if not select or not number:
            return
        now = monotonic.monotonic()
        sent = self._sent_external.get(trv)
        if sent is None or abs(sent[0] - room) >= 0.1 or now - sent[1] > EXTERNAL_REFRESH_SECONDS:
            if await self._call("number", "set_value", {"entity_id": number, "value": room}):
                self._sent_external[trv] = (room, now)
        sel = self.hass.states.get(select)
        if sel is not None and sel.state != "remote_temperature" and "remote_temperature" in (sel.attributes.get("options") or []):
            await self._call("select", "select_option", {"entity_id": select, "option": "remote_temperature"})

    def handle_state_change(self, entity_id: str, old: State | None, new: State | None) -> None:
        if entity_id not in self.trvs or new is None or old is None:
            return
        if new.context.id == self.context.id or new.context.parent_id == self.context.id:
            return
        temp = _float(new.attributes.get(ATTR_TEMPERATURE))
        before = _float(old.attributes.get(ATTR_TEMPERATURE))
        sent = self._sent_target.get(entity_id)
        if temp is None or temp == before or sent is None:
            return
        if temp == sent[0] or monotonic.monotonic() - sent[1] < MANUAL_GRACE:
            return
        # Someone turned the valve: treat it like + or - on the card.
        _LOGGER.debug("%s turned by hand to %s", entity_id, temp)
        self.hass.async_create_task(self.async_set_target(temp))

    async def async_set_target(self, temp: float) -> None:
        self._sent_target.clear()  # the next push is a fresh command
        await super().async_set_target(temp)

    async def async_start(self) -> None:
        """Give a new zone with the default schedule the valve's own."""
        await super().async_start()
        await self._adopt_valve_schedule()

    async def _adopt_valve_schedule(self) -> None:
        if self.state.schedule != normalise(DEFAULT_SCHEDULE) or not self.trvs:
            return
        days = {}
        for day_type, day in ((WEEKDAYS, "monday"), (WEEKENDS, "saturday")):
            text = self._companion(self.trvs[0], f"_weekly_schedule_{day}", "text")
            state = self.hass.states.get(text) if text else None
            if state is None or state.state in BAD_STATES:
                return
            try:
                days[day_type] = parse_valve_day(state.state)
            except ScheduleError:
                return
        self.state.schedule = normalise(days)
        self.hub.async_save()

    def attributes(self, now: datetime) -> dict[str, Any]:
        attrs = {**super().attributes(now), "devices": self.trvs}
        upcoming = self.next_scheduled(now)
        start = None
        if not attrs["preheating"] and upcoming and upcoming[1] > self.desired(now)[0]:
            start = self.warm_from(now, upcoming)
        attrs["preheat_from"] = start.isoformat() if start and start < upcoming[0] else None
        return attrs


class UnderfloorZone(Zone):
    """A Heatmiser stat: the stat runs the schedule, written as a profile."""

    kind = KIND_UNDERFLOOR
    _own_reading = "stat"

    def __init__(self, hub: Hearth, config: dict[str, Any], state: ZoneState) -> None:
        super().__init__(hub, config, state)
        self.stat: str = config[CONF_STAT]
        self.profile: str = config.get(CONF_PROFILE) or f"Hearth {self.name}"
        self._held_for_away = False
        self._settle_until = 0.0
        self._want: float | None = None  # the setpoint the stat should take
        self._writing: asyncio.Task | None = None
        self._written_at = 0.0
        self._tries = 0
        # Writing the profile to the neoHub can take a minute: it runs in the
        # background, once more if something changed meanwhile.
        self._syncing: asyncio.Task | None = None
        self._sync_again = False

    def _sent(self) -> None:
        self._settle_until = monotonic.monotonic() + STAT_SETTLE

    @property
    def _settling(self) -> bool:
        return monotonic.monotonic() < self._settle_until

    def watched_entities(self) -> list[str]:
        return [self.stat, *super().watched_entities()]

    @property
    def _stat_state(self) -> State | None:
        return self.hass.states.get(self.stat)

    def room_temp(self) -> float | None:
        temp = self._sensor_temp()
        if temp is not None:
            return temp
        state = self._stat_state
        return _float(state.attributes.get("current_temperature")) if state else None

    def target(self) -> float | None:
        """The stat's own target is the truth: it runs the schedule (once it
        has caught up with what Hearth last sent)."""
        if self._settling:
            return super().target()
        state = self._stat_state
        stat = _float(state.attributes.get(ATTR_TEMPERATURE)) if state else None
        return stat if stat is not None else super().target()

    def update_demand(self, now: datetime) -> bool:
        state = self._stat_state
        was = self.calling
        self.calling = bool(state and state.attributes.get("hvac_action") in ("heating", "preheating"))
        target = self.target()
        if target is not None:
            self._time_warm_up(now, was, target)
        return self.calling

    def preheating(self, now: datetime) -> bool:
        """The stat warms the floor early by itself (its optimum start)."""
        state = self._stat_state
        return bool(state and state.attributes.get("hvac_action") == "preheating")

    @property
    def syncing(self) -> bool:
        return self._syncing is not None and not self._syncing.done()

    def status(self, now: datetime) -> str:
        if self.syncing:
            return "Sending the change to the thermostat…"
        return super().status(now)

    def _profile_for_mode(self) -> str:
        if self.state.mode == MODE_FIXED:
            return f"{self.profile} Fixed"
        return self.profile

    def _registry_find(self, domain: str, suffix: str, same_device: bool) -> str | None:
        registry = er.async_get(self.hass)
        stat = registry.async_get(self.stat)
        if stat is None:
            return None
        for entry in registry.entities.values():
            if entry.platform != "heatmiserneo" or entry.domain != domain:
                continue
            if same_device and entry.device_id != stat.device_id:
                continue
            if entry.entity_id.endswith(suffix):
                return entry.entity_id
        return None

    @property
    def select(self) -> str | None:
        return self._registry_find("select", "_active_profile", same_device=True)

    @property
    def profile_sensor(self) -> str | None:
        return self._registry_find("sensor", "_profile_format", same_device=False)

    @property
    def hub_away(self) -> str | None:
        return self._registry_find("binary_sensor", "_away", same_device=False)

    def _levels(self) -> tuple[dict[str, list[str]], str]:
        """The schedule as neoHub levels."""
        st = self.state
        if st.mode == MODE_SCHEDULE:
            week, weekend = st.schedule[WEEKDAYS], st.schedule[WEEKENDS]
        else:
            week = weekend = [{"time": "00:00", "temp": st.fixed}]
        data = {
            "monday_times": [c["time"] for c in week],
            "monday_temperatures": [f"{c['temp']:g}" for c in week],
            "sunday_times": [c["time"] for c in weekend],
            "sunday_temperatures": [f"{c['temp']:g}" for c in weekend],
        }
        return data, self._profile_for_mode()

    @property
    def in_standby(self) -> bool:
        state = self._stat_state
        return bool(state and state.attributes.get("preset_mode") == "standby")

    async def async_sync(self) -> None:
        """Write the profile for the current mode and make the stat run it,
        in the background: the neoHub can take a minute."""
        self._sent()
        self._sync_again = True
        if not self.syncing:
            self._syncing = self.hass.async_create_task(self._sync_loop(), f"hearth {self.id} profile")

    async def _sync_loop(self) -> None:
        try:
            while self._sync_again:
                self._sync_again = False
                await self._sync_now()
        finally:
            self._syncing = None
            await self.hub.async_evaluate()

    async def _sync_now(self) -> None:
        """Off is the stat's own standby, which keeps frost protection."""
        self._sent()
        if self.state.mode == MODE_OFF:
            if not self.in_standby:
                await self._call("climate", "set_preset_mode", {"entity_id": self.stat, "preset_mode": "standby"})
            return
        if self.in_standby:
            await self._call("climate", "set_preset_mode", {"entity_id": self.stat, "preset_mode": "home"})
        sensor, select = self.profile_sensor, self.select
        if not sensor or not select:
            _LOGGER.warning("%s: can't find the neoHub's profile entities", self.name)
            return
        levels, name = self._levels()
        ok = await self._call(
            "heatmiserneo", "create_profile_two",
            {"entity_id": sensor, "name": name, "mode": "upsert", **levels},
        )
        if not ok:
            return
        await self._call("homeassistant", "update_entity", {"entity_id": select})
        for _ in range(30):
            options = (self.hass.states.get(select) or State(select, "")).attributes.get("options") or []
            if name in options:
                break
            await _sleep(2)
            await self._call("homeassistant", "update_entity", {"entity_id": select})
        else:
            _LOGGER.warning("%s: profile %s didn't appear on the neoHub", self.name, name)
            return
        # Selecting it again is what makes the stat pick up new levels.
        await self._call("select", "select_option", {"entity_id": select, "option": name})

    async def _override(self, temp: float) -> None:
        """The stat's own setpoint lasts until its next level: exactly an
        'until the next change' override."""
        self._sent()
        self._want, self._tries = temp, 0
        self._write_setpoint()
        await self._changed(sync=False)

    def _write_setpoint(self) -> None:
        """Start writing the wanted setpoint unless a write is under way
        (which picks up the latest when it's done)."""
        if self._writing is None or self._writing.done():
            self._writing = self.hass.async_create_task(self._writer(), f"hearth {self.id} setpoint")

    async def _writer(self) -> None:
        for _ in range(3):
            await asyncio.sleep(0)  # let taps already on their way land first
        sent = None
        while self._want is not None and self._want != sent:
            sent = self._want
            await self._call("climate", "set_temperature", {"entity_id": self.stat, ATTR_TEMPERATURE: sent})
            self._written_at = monotonic.monotonic()

    async def async_tick(self, now: datetime) -> None:
        await super().async_tick(now)
        if self._want is None:
            return
        state = self._stat_state
        stat_target = _float(state.attributes.get(ATTR_TEMPERATURE)) if state else None
        if self.state.override_until is None or self.state.mode != MODE_SCHEDULE or stat_target == self._want:
            self._want = None
            return
        if (self._writing and not self._writing.done()) or monotonic.monotonic() - self._written_at < STAT_RECHECK:
            return
        if self._tries >= STAT_TRIES:
            _LOGGER.warning("%s: the stat didn't take %s°", self.name, self._want)
            self._want = None
            return
        self._tries += 1
        self._sent()
        self._write_setpoint()

    async def _hold(self, temp: float, minutes: int) -> None:
        hours, mins = divmod(minutes, 60)
        await self._call(
            "heatmiserneo", "hold_on",
            {"entity_id": self.stat, "hold_temperature": temp, "hold_duration": f"{hours:02d}:{mins:02d}:00"},
        )

    async def _start_boost(self, minutes: int) -> None:
        await self._hold(self.state.boost_temp or self._setting(CONF_BOOST_TEMP), minutes)

    async def _end_boost(self) -> None:
        await self._call("heatmiserneo", "hold_off", {"entity_id": self.stat})
        await self._reapply_away()

    async def _boost_expired(self) -> None:
        await self._reapply_away()

    async def _reapply_away(self) -> None:
        if self.hub.house_mode == HOUSE_AWAY and self.state.mode != MODE_OFF:
            await self._hold(self._setting(CONF_AWAY_TEMP), 99 * 60)
            self._held_for_away = True

    async def async_house_changed(self, old: str, new: str) -> None:
        if new == HOUSE_AWAY:
            await self._reapply_away()
        elif old == HOUSE_AWAY:
            await self._call("heatmiserneo", "hold_off", {"entity_id": self.stat})
            self._held_for_away = False

    async def async_start(self) -> None:
        """Adopt whatever the stat is already running."""
        await super().async_start()
        if self.in_standby:
            if self.state.mode != MODE_OFF:
                self.state.mode = MODE_OFF
                self.hub.async_save()
            return
        select = self.select
        state = self.hass.states.get(select) if select else None
        if state is None or state.state in BAD_STATES:
            return
        if state.state != self._profile_for_mode():
            if state.state == f"{self.profile} Fixed":
                self.state.mode = MODE_FIXED
                stat = self._stat_state
                self.state.fixed = _float(stat.attributes.get(ATTR_TEMPERATURE)) if stat else self.state.fixed
            elif state.state == self.profile:
                self.state.mode = MODE_SCHEDULE
            self.hub.async_save()

    def desired(self, now: datetime) -> tuple[float, str]:
        if self.state.mode == MODE_OFF and self.hub.house_mode != HOUSE_HOLIDAY and not self.boost_active(now):
            return self._setting(CONF_OFF_TEMP), "Off: the thermostat keeps frost protection"
        temp, reason = super().desired(now)
        upcoming = self.next_scheduled(now)
        if (
            self.preheating(now)
            and self.state.mode == MODE_SCHEDULE
            and self.hub.house_mode == HOUSE_HOME
            and not self.boost_active(now)
            and self.state.override_until is None
            and upcoming
            and upcoming[1] > temp
        ):
            return temp, f"Warming up early for {_deg(upcoming[1])} by {_when(upcoming[0], now)}"
        stat = self._stat_state
        stat_target = _float(stat.attributes.get(ATTR_TEMPERATURE)) if stat else None
        if (
            self.state.mode == MODE_SCHEDULE
            and self.hub.house_mode not in (HOUSE_AWAY, HOUSE_HOLIDAY)
            and not self.boost_active(now)
            and self.state.override_until is None
            and not self._settling
            and stat_target is not None
            and stat_target != temp
        ):
            upcoming = self.next_scheduled(now)
            when = f" until {_when(upcoming[0], now)}" if upcoming else ""
            return stat_target, f"Changed on the thermostat to {_deg(stat_target)}{when}"
        return temp, reason

    def attributes(self, now: datetime) -> dict[str, Any]:
        return {**super().attributes(now), "devices": [self.stat],
                "profile": "standby" if self.state.mode == MODE_OFF else self._profile_for_mode()}


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def make_zone(hub: Hearth, config: dict[str, Any], state: ZoneState) -> Zone:
    if config.get(CONF_KIND) == KIND_UNDERFLOOR:
        return UnderfloorZone(hub, config, state)
    return RadiatorZone(hub, config, state)
