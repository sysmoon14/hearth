"""Warming up early: how fast each radiator room warms, and when to start.

A room learns from its warm-ups, each one a rise in degrees over a number of
minutes, timed from when heat was first asked for (so the boiler and the
radiators getting hot are part of it). Two sources:

- Warm-ups Hearth sees: from a room starting to ask for heat to it reaching
  its target.
- The recorder's history, once per room: each time the boiler fired, how far
  and how fast the room's sensor rose. Rooms whose valves were shut didn't
  rise, so small rises are left out.

Only rooms with their own sensor learn. A valve's own sensor sits on the
radiator and warms with it, not with the room.

The rate a room warms at is the median of its recent warm-ups, in degrees an
hour. To reach a warmer schedule change on time, a room starts
(degrees to go ÷ rate) early, plus a margin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable

# A warm-up counts if the room rose at least this far, over at least this long.
MIN_RISE = 0.5
MIN_MINUTES = 10
# Longer than this and something else was going on (a window open, valves shut).
MAX_MINUTES = 360
# Warm-ups kept per room, newest first; and how many before a room starts early.
KEEP = 12
MIN_WARMUPS = 2
# Added to every lead, so a room is warm on time rather than just after.
MARGIN_MINUTES = 10
# Boiler firing periods closer together than this are one period: thermostats
# flick between heating and idle as they modulate.
MERGE_GAP = timedelta(minutes=5)
# A firing period shorter than this can't show how fast a room warms.
MIN_PERIOD = timedelta(minutes=20)
# A room keeps warming a little after the boiler stops, as the radiators cool.
AFTER_FIRING = timedelta(minutes=15)


@dataclass
class WarmUp:
    """One observed warm-up."""

    rise: float
    minutes: float
    at: datetime
    source: str  # "seen" or "history"

    @property
    def rate(self) -> float:
        """Degrees an hour."""
        return self.rise / self.minutes * 60

    def as_dict(self) -> dict[str, Any]:
        return {"rise": round(self.rise, 2), "minutes": round(self.minutes, 1),
                "at": self.at.isoformat(), "source": self.source}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WarmUp | None:
        try:
            at = datetime.fromisoformat(data["at"])
            return cls(float(data["rise"]), float(data["minutes"]), at, str(data.get("source", "seen")))
        except (KeyError, TypeError, ValueError):
            return None


def counts(rise: float, minutes: float) -> bool:
    return rise >= MIN_RISE and MIN_MINUTES <= minutes <= MAX_MINUTES


def add(warmups: list[WarmUp], new: WarmUp) -> list[WarmUp]:
    """The room's warm-ups with a new one, newest first, the oldest dropped."""
    merged = sorted([*warmups, new], key=lambda w: w.at, reverse=True)
    return merged[:KEEP]


def rate(warmups: list[WarmUp]) -> float | None:
    """How fast the room warms, in degrees an hour, once it has seen enough."""
    if len(warmups) < MIN_WARMUPS:
        return None
    return median(w.rate for w in warmups)


def lead_minutes(need: float, rate_per_hour: float | None, cap: float) -> float | None:
    """How early to start to rise `need` degrees, or None if it can't say."""
    if rate_per_hour is None or rate_per_hour <= 0 or cap <= 0 or need <= 0:
        return None
    return min(cap, need / rate_per_hour * 60 + MARGIN_MINUTES)


# --- learning from history ------------------------------------------------------
Reading = tuple[datetime, float]


def firing_periods(spans: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Join firing spans that overlap or nearly touch, and keep long ones."""
    out: list[list[datetime]] = []
    for start, end in sorted(spans):
        if out and start - out[-1][1] <= MERGE_GAP:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(s, e) for s, e in out if e - s >= MIN_PERIOD]


def spans_where(changes: list[tuple[datetime, bool]], until: datetime) -> list[tuple[datetime, datetime]]:
    """Turn a time-ordered list of (when, firing) into the spans it was firing."""
    spans, since = [], None
    for when, firing in changes:
        if firing and since is None:
            since = when
        elif not firing and since is not None:
            spans.append((since, when))
            since = None
    if since is not None:
        spans.append((since, until))
    return spans


def warmups_from_history(
    periods: list[tuple[datetime, datetime]], readings: list[Reading]
) -> list[WarmUp]:
    """For each firing period, how far the room rose and how long it took.

    The rise runs from the last reading before the boiler started to the
    warmest reading while it fired (or just after), timed to that reading."""
    readings = sorted(readings)
    found = []
    for start, end in periods:
        before = [t for w, t in readings if w <= start]
        if not before:
            continue
        start_temp = before[-1]
        during = [(w, t) for w, t in readings if start < w <= end + AFTER_FIRING]
        if not during:
            continue
        peak_at, peak = max(during, key=lambda r: (r[1], -r[0].timestamp()))
        rise, minutes = peak - start_temp, (peak_at - start).total_seconds() / 60
        if counts(rise, minutes):
            found.append(WarmUp(rise, minutes, start, "history"))
    return found
