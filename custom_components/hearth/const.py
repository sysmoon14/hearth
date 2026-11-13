"""Constants for Hearth."""

from __future__ import annotations

DOMAIN = "hearth"
VERSION = "0.3.0"

# Zone kinds
KIND_RADIATOR = "radiator"
KIND_UNDERFLOOR = "underfloor"

# Zone modes (shown as Schedule / Fixed / Off)
MODE_SCHEDULE = "schedule"
MODE_FIXED = "fixed"
MODE_OFF = "off"

# House modes
HOUSE_HOME = "home"
HOUSE_AWAY = "away"
HOUSE_HOLIDAY = "holiday"
HOUSE_MODES = [HOUSE_HOME, HOUSE_AWAY, HOUSE_HOLIDAY]

# Day types: every zone gets weekdays and weekends, nothing else, so every
# room behaves the same whatever is behind it.
WEEKDAYS = "weekdays"
WEEKENDS = "weekends"
DAY_TYPES = (WEEKDAYS, WEEKENDS)

# The neoHub allows six levels a day. Every zone gets the same limit.
MAX_CHANGES_PER_DAY = 6
MIN_TEMP = 5.0
MAX_TEMP = 28.0
TEMP_STEP = 0.5

# Entry options
CONF_ZONES = "zones"
CONF_ZONE_ID = "id"
CONF_NAME = "name"
CONF_KIND = "kind"
CONF_TRVS = "trvs"
CONF_SENSOR = "sensor"
CONF_STAT = "stat"
CONF_PROFILE = "profile"

CONF_BOILER = "boiler"
# A switch that calls for heat: on while the radiators want the boiler, off
# otherwise. When set, Hearth uses it instead of the boiler thermostat.
CONF_BOILER_SWITCH = "boiler_switch"
CONF_BOILER_ON = "boiler_on_temp"
CONF_BOILER_OFF = "boiler_off_temp"
CONF_FALLBACK = "fallback_temp"
CONF_BOOST_TEMP = "boost_temp"
CONF_BOOST_MINUTES = "boost_minutes"
CONF_AWAY_TEMP = "away_temp"
CONF_HOLIDAY_TEMP = "holiday_temp"
CONF_OFF_TEMP = "off_temp"
CONF_ON_DELAY = "demand_on_delay"
CONF_OFF_DELAY = "demand_off_delay"

DEFAULTS = {
    CONF_BOILER_ON: 25.0,
    CONF_BOILER_OFF: 10.0,
    CONF_FALLBACK: 17.0,
    CONF_BOOST_TEMP: 22.0,
    CONF_BOOST_MINUTES: 60,
    CONF_AWAY_TEMP: 16.0,
    CONF_HOLIDAY_TEMP: 10.0,
    CONF_OFF_TEMP: 7.0,
    CONF_ON_DELAY: 60,
    CONF_OFF_DELAY: 180,
}

# Hysteresis for a radiator room asking for heat.
DEMAND_BELOW = 0.3
DEMAND_ABOVE = 0.2

# How often an outside temperature is re-sent to a valve even if unchanged,
# so the valve never falls back to its own sensor.
EXTERNAL_REFRESH_SECONDS = 20 * 60

STORAGE_KEY = "hearth"
STORAGE_VERSION = 1

FRONTEND_URL = "/hearth_static"
CARD_FILE = "hearth-card.js"

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_BOOST = "boost"
SERVICE_CANCEL_BOOST = "cancel_boost"

DEFAULT_SCHEDULE = {
    WEEKDAYS: [
        {"time": "06:30", "temp": 19.0},
        {"time": "09:00", "temp": 16.0},
        {"time": "17:00", "temp": 19.0},
        {"time": "22:30", "temp": 16.0},
    ],
    WEEKENDS: [
        {"time": "07:30", "temp": 19.0},
        {"time": "22:30", "temp": 16.0},
    ],
}
