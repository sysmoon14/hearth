"""Hot water: heat the cylinder while the radiators don't want the boiler.

Hearth heats the tank one way: it sets a boost on the boiler's thermostat (a
select whose options are "off" and lengths in minutes). A boost ends by
itself, so if Hearth or Home Assistant stops, the hot water stops soon after.

When the tank wants heat:

- With no tank sensor, ahead of each "ready by" time. From a few hours
  before it, the tank is owed so many minutes of gas, and takes them whenever
  the radiators are quiet. The cylinder's own thermostat stops the boiler
  when the tank is hot, so minutes it didn't need cost nothing.
- With a tank sensor, whenever the tank is below its reheat temperature,
  until it's back at its target.

When it may have it: while no radiator room is asking for heat. A run doesn't
start when a room is about to start warming, and a run that's going is
stopped the moment a room asks. A boost somebody set by hand is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import logging
import time as monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from .const import (
    CONF_HW_BOOST,
    CONF_HW_MINUTES,
    CONF_HW_READY,
    CONF_HW_REHEAT,
    CONF_HW_SENSOR,
    CONF_HW_TARGET,
    HOUSE_HOLIDAY,
    HW_BOOST_OPTION,
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
# How long the thermostat is given to show a boost Hearth has just set or
# stopped, before what it shows is taken as somebody else's doing.
SETTLE_SECONDS = 90

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
    # The boost that's running is one Hearth set, not somebody's own.
    mine: bool = False
    # With a sensor: on its way up to the target, rather than waiting to
    # fall below the reheat temperature.
    reheating: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "heating_for": self.heating_for.isoformat() if self.heating_for else None,
            "delivered": round(self.delivered, 1),
            "mine": self.mine,
            "reheating": self.reheating,
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
        self._sent: tuple[str, float] | None = None
        self._rest_until: datetime | None = None
        self._stale_logged = False

    # --- settings --------------------------------------------------------
    @property
    def boost(self) -> str:
        return self.hub.settings[CONF_HW_BOOST]

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
        return [e for e in (self.boost, self.sensor) if e]

    # --- reading ---------------------------------------------------------
    def boost_state(self) -> str | None:
        """What the boost is set to: "off", a length in minutes, or None when
        the thermostat can't be reached."""
        state = self.hass.states.get(self.boost)
        return None if state is None or state.state in BAD_STATES else state.state

    def on(self) -> bool:
        return self.boost_state() not in (None, "off")

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
        if self.boost_state() is None:
            return "Can't heat the hot water: the boiler's hot water boost is unavailable"
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
        time, whoever set the boost."""
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
        """Look at the tank and the radiators, and set or stop the boost."""
        st = self.state
        boost = self.boost_state()
        if boost is None:
            self.operation, self.status = OFF, "The boiler's hot water boost is unavailable"
            self._last_look = None
            return
        # The thermostat takes a moment to show what it was last sent.
        settling = self._sent is not None and monotonic.monotonic() - self._sent[1] < SETTLE_SECONDS
        if settling and (boost == "off") != (self._sent[0] == "off"):
            self.operation = HEATING if self._sent[0] != "off" else WAITING
            self.status = "Starting" if self._sent[0] != "off" else "Stopping"
            return
        if st.mine and boost != HW_BOOST_OPTION:
            # The boost Hearth set has run out or been stopped, or somebody
            # has set a different one, which is theirs now.
            st.mine = False
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

        if boost != "off" and not st.mine:
            # Somebody's own boost: it runs whatever the radiators are doing.
            self.operation = HEATING
            self.status = "Boosted by hand" + (": sharing the boiler with the radiators" if not quiet else "")
            return

        if st.mine:
            if not wants or not quiet:
                await self._send("off")
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
        if await self._send(HW_BOOST_OPTION):
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

    async def _send(self, option: str) -> bool:
        """Set the boost, once: the thermostat takes a moment to show it."""
        clock = monotonic.monotonic()
        if self._sent and self._sent[0] == option and clock - self._sent[1] < 60:
            return False
        self._sent = (option, clock)
        try:
            await self.hass.services.async_call(
                "select", "select_option", {"entity_id": self.boost, "option": option},
                blocking=True, context=self.hub.context,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Couldn't set %s to %s: %s", self.boost, option, err)
            return False
        return True

    # --- by hand ---------------------------------------------------------
    async def async_boost(self, minutes: int) -> None:
        """A boost somebody asked for: it runs whatever the radiators want."""
        state = self.hass.states.get(self.boost)
        lengths = sorted(int(o) for o in (state.attributes.get("options") or []) if str(o).isdigit()) if state else []
        option = str(next((n for n in lengths if n >= minutes), lengths[-1] if lengths else minutes))
        self._sent = None
        self.state.mine = False
        if await self._send(option):
            self.hub.async_save()

    async def async_stop(self) -> None:
        """Stop the hot water, whoever started it."""
        self._sent = None
        self.state.mine = False
        if self.on():
            await self._send("off")
        self.hub.async_save()

    async def async_shutdown(self) -> None:
        """Home Assistant is stopping: a boost Hearth set is stopped rather
        than left to run out with nobody watching the radiators."""
        if self.state.mine and self.on():
            self._sent = None
            await self._send("off")
            self.state.mine = False

    def attributes(self, now: datetime) -> dict[str, Any]:
        local = dt_util.as_local(now)
        upcoming = next_ready(local, parse_times(self.hub.settings.get(CONF_HW_READY)))
        return {
            "status": self.status,
            "heating": self.on(),
            "set_by_hearth": self.state.mine,
            "by": "temperature" if self.tank_temp() is not None else "time",
            "tank_sensor": self.sensor,
            "ready_by": list(self.hub.settings.get(CONF_HW_READY) or []),
            "next_ready_by": upcoming.isoformat() if upcoming else None,
            "minutes_heated": round(self.state.delivered) if self.state.heating_for else None,
            "minutes_wanted": int(self.minutes),
            "problem": self.problem(),
        }
