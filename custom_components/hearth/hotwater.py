"""Hot water: heat the cylinder while the radiators don't want the boiler.

Hearth heats the tank one way: it turns on a Call for hot water switch (Fledge's,
on a Nest). Fledge keeps the thermostat's hot water on while the switch is on,
on a short lease, so if Hearth or Home Assistant stops, the hot water stops
soon after.

When the tank wants heat:

- With no tank sensor, ahead of each "ready by" time. From a few hours
  before it, the tank is owed so many minutes of gas, and takes them whenever
  the radiators are quiet. The cylinder's own thermostat stops the boiler
  when the tank is hot, so minutes it didn't need cost nothing.
- With a tank sensor, whenever the tank is below its reheat temperature,
  until it's back at its target.

When it may have it: while no radiator room is asking for heat. A run doesn't
start when a room is about to start warming, and a run that's going is
stopped the moment a room asks. Hot water somebody turned on by hand is left
alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HW_MINUTES,
    CONF_HW_READY,
    CONF_HW_REHEAT,
    CONF_HW_SENSOR,
    CONF_HW_SWITCH,
    CONF_HW_TARGET,
    HOUSE_HOLIDAY,
    HW_CLEAR_MINUTES,
    HW_REST_MINUTES,
    HW_SENSOR_STALE_MINUTES,
    HW_WINDOW_HOURS,
)

if TYPE_CHECKING:
    from .hub import Hearth

_LOGGER = logging.getLogger(__name__)

BAD_STATES = (STATE_UNAVAILABLE, STATE_UNKNOWN, None, "")
# The longest gap between two looks that still counts as time heating: more
# than this and Hearth wasn't watching (a restart), so it doesn't guess.
MAX_STEP_SECONDS = 120

HEATING = "heating"
WAITING = "waiting"
IDLE = "idle"
OFF = "off"


# --- the sums, with no Home Assistant in them --------------------------------
def parse_times(values: Any) -> list[time]:
    """The "ready by" times, in order, leaving out anything that isn't one."""
    found = set()
    for value in values or []:
        try:
            hours, minutes = str(value).split(":")[:2]
            found.add(time(int(hours), int(minutes)))
        except (ValueError, TypeError):
            continue
    return sorted(found)


def next_ready(now: datetime, times: list[time]) -> datetime | None:
    """The next "ready by" moment after now (local time in, local time out)."""
    for day in (0, 1):
        for at in times:
            moment = datetime.combine(now.date() + timedelta(days=day), at, now.tzinfo)
            if moment > now:
                return moment
    return None


def due(now: datetime, times: list[time], window: timedelta) -> datetime | None:
    """The "ready by" moment the tank is being heated for now, if it's within
    the window before one."""
    moment = next_ready(now, times)
    return moment if moment is not None and now >= moment - window else None


def may_start(quiet: bool, radiators_due: datetime | None, now: datetime, clear: timedelta) -> bool:
    """A run may start while the radiators are quiet and look like staying so."""
    return quiet and (radiators_due is None or radiators_due - now > clear)


@dataclass
class TankState:
    """What the hot water remembers across restarts."""

    # The "ready by" moment being heated for, and the minutes it's had.
    heating_for: datetime | None = None
    delivered: float = 0.0
    # The switch is on because Hearth turned it on, not somebody by hand.
    mine: bool = False
    # A boost asked for with hearth.boost_hot_water runs until then.
    boost_until: datetime | None = None
    # With a sensor: on its way up to the target, rather than waiting to
    # fall below the reheat temperature.
    reheating: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "heating_for": self.heating_for.isoformat() if self.heating_for else None,
            "delivered": round(self.delivered, 1),
            "mine": self.mine,
            "reheating": self.reheating,
            "boost_until": self.boost_until.isoformat() if self.boost_until else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TankState:
        state = cls()
        if not data:
            return state
        try:
            state.heating_for = dt_util.parse_datetime(data["heating_for"]) if data.get("heating_for") else None
            state.delivered = float(data.get("delivered") or 0)
        except (TypeError, ValueError):
            state.heating_for, state.delivered = None, 0.0
        state.mine = bool(data.get("mine"))
        state.reheating = bool(data.get("reheating"))
        try:
            state.boost_until = dt_util.parse_datetime(data["boost_until"]) if data.get("boost_until") else None
        except (TypeError, ValueError):
            state.boost_until = None
        return state


def _hhmm(moment: datetime) -> str:
    return dt_util.as_local(moment).strftime("%H:%M")


class HotWater:
    """The cylinder, heated in the gaps the radiators leave."""

    def __init__(self, hub: Hearth, state: TankState) -> None:
        self.hub = hub
        self.hass = hub.hass
        self.state = state
        self.operation = IDLE
        self.status = "Starting"
        self._last_look: datetime | None = None
        self._rest_until: datetime | None = None
        self._stale_logged = False

    # --- settings --------------------------------------------------------
    @property
    def switch(self) -> str:
        return self.hub.settings[CONF_HW_SWITCH]

    @property
    def sensor(self) -> str | None:
        return self.hub.settings.get(CONF_HW_SENSOR) or None

    @property
    def target(self) -> float:
        return float(self.hub.settings[CONF_HW_TARGET])

    @property
    def reheat_below(self) -> float:
        return min(float(self.hub.settings[CONF_HW_REHEAT]), self.target - 1)

    @property
    def minutes(self) -> float:
        return float(self.hub.settings[CONF_HW_MINUTES])

    def watched_entities(self) -> list[str]:
        return [e for e in (self.switch, self.sensor) if e]

    # --- reading ---------------------------------------------------------
    def switch_state(self) -> str | None:
        """"on", "off", or None when the switch can't be reached."""
        state = self.hass.states.get(self.switch)
        return None if state is None or state.state in BAD_STATES else state.state

    def on(self) -> bool:
        return self.switch_state() == "on"

    def tank_temp(self) -> float | None:
        """The tank's temperature, if it has a sensor that's still reporting."""
        state = self.hass.states.get(self.sensor) if self.sensor else None
        if state is None or state.state in BAD_STATES:
            return None
        try:
            temp = float(state.state)
        except ValueError:
            return None
        stale = dt_util.utcnow() - state.last_reported > timedelta(minutes=HW_SENSOR_STALE_MINUTES)
        if stale and not self._stale_logged:
            _LOGGER.warning(
                "%s hasn't reported for %s minutes: heating the hot water by time until it does",
                self.sensor, HW_SENSOR_STALE_MINUTES,
            )
        self._stale_logged = stale
        return None if stale else temp

    def problem(self) -> str | None:
        if self.switch_state() is None:
            return "Can't heat the hot water: the Call for hot water switch is unavailable"
        if self.sensor and self.tank_temp() is None:
            return "The tank sensor isn't reporting: heating the hot water by time"
        return None

    # --- deciding --------------------------------------------------------
    def _ready_by(self, now: datetime) -> datetime | None:
        """The moment the tank is being heated for, in UTC. On Holiday that's
        the end of the holiday; otherwise the next "ready by" time."""
        window = timedelta(hours=HW_WINDOW_HOURS)
        if self.hub.house_mode == HOUSE_HOLIDAY:
            back = self.hub.holiday_until
            return back if back is not None and back - window <= now < back else None
        local = dt_util.as_local(now)
        moment = due(local, parse_times(self.hub.settings.get(CONF_HW_READY)), window)
        return dt_util.as_utc(moment) if moment else None

    def _count(self, now: datetime, ready: datetime | None) -> None:
        """Keep the minutes of gas the tank has had towards this "ready by"
        time, whoever turned it on."""
        st = self.state
        if ready != st.heating_for:
            st.heating_for, st.delivered = ready, 0.0
            self.hub.async_save()
        last, self._last_look = self._last_look, now
        if ready is None or last is None or not self.on():
            return
        step = (now - last).total_seconds()
        if 0 < step <= MAX_STEP_SECONDS:
            st.delivered += step / 60

    def _wants(self, now: datetime, ready: datetime | None) -> tuple[bool, str]:
        """Whether the tank wants heat, and what to say when it doesn't."""
        temp = self.tank_temp()
        if temp is not None:
            st = self.state
            if self.hub.house_mode == HOUSE_HOLIDAY and ready is None:
                st.reheating = False
                return False, "Holiday: the hot water is off"
            if temp >= self.target:
                st.reheating = False
            elif temp < self.reheat_below:
                st.reheating = True
            if st.reheating:
                return True, ""
            return False, f"Hot · {temp:.0f}°"
        if ready is None:
            if self.hub.house_mode == HOUSE_HOLIDAY:
                return False, "Holiday: the hot water is off"
            upcoming = next_ready(dt_util.as_local(now), parse_times(self.hub.settings.get(CONF_HW_READY)))
            if upcoming is None:
                return False, "No times set to have hot water ready by"
            start = upcoming - timedelta(hours=HW_WINDOW_HOURS)
            return False, f"Next heats from {_hhmm(start)}, for {_hhmm(upcoming)}"
        if self.state.delivered >= self.minutes:
            return False, f"Heated for {_hhmm(ready)}"
        return True, ""

    async def async_update(self, now: datetime) -> None:
        """Look at the tank and the radiators, and turn the hot water on or off."""
        st = self.state
        switch = self.switch_state()
        if switch is None:
            self.operation, self.status = OFF, "The Call for hot water switch is unavailable"
            self._last_look = None
            return
        if switch == "off" and (st.mine or st.boost_until):
            # Turned off by somebody else: Hearth decides afresh.
            st.mine, st.boost_until = False, None
            self.hub.async_save()
        if st.boost_until is not None and now >= st.boost_until:
            # A boost asked for by hand has had its time.
            st.boost_until = None
            await self._send(False)
            switch = "off"
            self.hub.async_save()
        ready = self._ready_by(now)
        self._count(now, ready)
        wants, idle_words = self._wants(now, ready)
        asking = self.hub.zones_calling()
        # Quiet as soon as no room is asking. Hearth keeps calling for heat
        # for a minute or so after that, and the hot water starts under it:
        # the boiler goes from the radiators to the tank without stopping.
        quiet = not asking
        temp = self.tank_temp()

        if switch == "on" and not st.mine:
            # Somebody's own: it runs whatever the radiators are doing.
            self.operation = HEATING
            until = f" until {_hhmm(st.boost_until)}" if st.boost_until else ""
            self.status = f"Boosted by hand{until}" + (": sharing the boiler with the radiators" if not quiet else "")
            return

        if st.mine:
            if not wants or not quiet:
                await self._send(False)
                st.mine = False
                self.hub.async_save()
                if wants:
                    self._rest_until = now + timedelta(minutes=HW_REST_MINUTES)
            else:
                self.operation = HEATING
                self.status = self._heating_words(temp, ready)
                return

        if not wants:
            self.operation = OFF if self.hub.house_mode == HOUSE_HOLIDAY and ready is None else IDLE
            self.status = idle_words
            return
        if not quiet:
            self.operation = WAITING
            self.status = f"Waiting for the radiators: {', '.join(asking)} {'is' if len(asking) == 1 else 'are'} heating"
            return
        due_at = self.hub.radiators_due(now)
        if not may_start(True, due_at, now, timedelta(minutes=HW_CLEAR_MINUTES)):
            self.operation = WAITING
            self.status = f"Waiting: a room starts warming up at {_hhmm(due_at)}"
            return
        if self._rest_until is not None and now < self._rest_until:
            self.operation, self.status = WAITING, "Waiting for the radiators to settle"
            return
        if await self._send(True):
            st.mine = True
            self.hub.async_save()
        self.operation = HEATING
        self.status = self._heating_words(temp, ready)

    def _heating_words(self, temp: float | None, ready: datetime | None) -> str:
        if temp is not None:
            return f"Heating to {self.target:.0f}° · {temp:.0f}° now"
        left = max(0.0, self.minutes - self.state.delivered)
        for_words = f" for {_hhmm(ready)}" if ready else ""
        return f"Heating{for_words} · {round(left)} min to go"

    async def _send(self, on: bool) -> bool:
        """Turn the switch on or off. Fledge keeps the thermostat's hot water
        going while it's on."""
        try:
            await self.hass.services.async_call(
                self.switch.split(".")[0], "turn_on" if on else "turn_off", {"entity_id": self.switch},
                blocking=True, context=self.hub.context,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Couldn't turn %s %s: %s", self.switch, "on" if on else "off", err)
            return False
        return True

    # --- by hand ---------------------------------------------------------
    async def async_boost(self, minutes: int) -> None:
        """A boost somebody asked for: it runs for so many minutes, whatever
        the radiators want."""
        self.state.mine = False
        self.state.boost_until = dt_util.utcnow() + timedelta(minutes=minutes)
        await self._send(True)
        self.hub.async_save()

    async def async_stop(self) -> None:
        """Stop the hot water, whoever started it."""
        self.state.mine, self.state.boost_until = False, None
        if self.on():
            await self._send(False)
        self.hub.async_save()

    async def async_shutdown(self) -> None:
        """Home Assistant is stopping: hot water Hearth turned on is turned
        off rather than left with nobody watching the radiators. A boost
        asked for by hand runs on, and is ended on time after the restart."""
        if self.state.mine and self.on():
            await self._send(False)
            self.state.mine = False

    def attributes(self, now: datetime) -> dict[str, Any]:
        local = dt_util.as_local(now)
        upcoming = next_ready(local, parse_times(self.hub.settings.get(CONF_HW_READY)))
        return {
            "status": self.status,
            "heating": self.on(),
            "set_by_hearth": self.state.mine,
            "boost_until": self.state.boost_until.isoformat() if self.state.boost_until else None,
            "by": "temperature" if self.tank_temp() is not None else "time",
            "tank_sensor": self.sensor,
            "ready_by": list(self.hub.settings.get(CONF_HW_READY) or []),
            "next_ready_by": upcoming.isoformat() if upcoming else None,
            "minutes_heated": round(self.state.delivered) if self.state.heating_for else None,
            "minutes_wanted": int(self.minutes),
            "problem": self.problem(),
        }
