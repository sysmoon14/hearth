"""How long each room has heated, and roughly what that cost.

Every room keeps a few days of totals, by local date:

- minutes: how long it was asking for heat (a floor counts while its stat
  says it's heating).
- share: its share of the boiler's time. While three rooms ask at once,
  each of them gets a third of that minute, so the shares add up to the
  time the boiler was wanted, not three times it.

The cost is the share times what the boiler burns while it heats (its
average output, in kW, a setting) times the gas price. It's an estimate:
a boiler modulates, and the hot water and the cooker use the same meter.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Days of totals kept per room: this week, and today again.
KEEP_DAYS = 8
WEEK_DAYS = 7
# A gap longer than this between two looks at the house isn't counted:
# Home Assistant was stopped, or Hearth was, and nobody knows what happened.
MAX_STEP_SECONDS = 300

Days = dict[str, list[float]]


def add(days: Days, day: date, minutes: float, share: float) -> Days:
    """The totals with some heating added to one day, the oldest dropped."""
    key = day.isoformat()
    total = days.get(key) or [0.0, 0.0]
    days = {**days, key: [total[0] + minutes, total[1] + share]}
    oldest = (day - timedelta(days=KEEP_DAYS - 1)).isoformat()
    return {k: v for k, v in days.items() if k >= oldest}


def today(days: Days, day: date) -> tuple[float, float]:
    """(minutes, share) for one day."""
    total = days.get(day.isoformat()) or [0.0, 0.0]
    return float(total[0]), float(total[1])


def week(days: Days, day: date) -> tuple[float, float]:
    """(minutes, share) over the last seven days, today included."""
    since = (day - timedelta(days=WEEK_DAYS - 1)).isoformat()
    upto = day.isoformat()
    picked = [v for k, v in days.items() if since <= k <= upto]
    return sum(float(v[0]) for v in picked), sum(float(v[1]) for v in picked)


def cost(share_minutes: float, boiler_kw: float | None, pence_per_kwh: float | None) -> float | None:
    """Pounds (or whatever the currency is) for a share of the boiler's time."""
    if not boiler_kw or boiler_kw <= 0 or pence_per_kwh is None or pence_per_kwh < 0:
        return None
    return share_minutes / 60 * boiler_kw * pence_per_kwh / 100


def pence(value: Any, unit: str | None) -> float | None:
    """A gas price in pence (or cents) a kWh, from a number or a sensor's
    state. Energy suppliers' sensors give pounds a kWh ("GBP/kWh", 0.079);
    a number typed in is pence a kWh (7.9)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    words = (unit or "").lower().replace(" ", "")
    if words.startswith(("p/", "pence", "c/", "cent")):
        return number
    if words:  # GBP/kWh, £/kWh, EUR/kWh: the currency's main unit
        return number * 100
    return number


def clean(data: Any) -> Days:
    """Stored totals, ignoring anything that isn't."""
    out: Days = {}
    if not isinstance(data, dict):
        return out
    for key, value in data.items():
        try:
            date.fromisoformat(key)
            out[key] = [float(value[0]), float(value[1])]
        except (TypeError, ValueError, IndexError):
            continue
    return out
