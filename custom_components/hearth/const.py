"""Constants for Hearth."""

from __future__ import annotations

DOMAIN = "hearth"
VERSION = "1.0.0b12"

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

# The one thing Hearth drives for the boiler: a switch that calls for heat,
# on while the radiators want it and off otherwise. A relay, a thermostat's
# own call-for-heat switch, an input_boolean: anything with on and off.
CONF_BOILER_SWITCH = "boiler_switch"
CONF_BOOST_TEMP = "boost_temp"
CONF_BOOST_MINUTES = "boost_minutes"
# A floor takes far longer to answer than a radiator, so its boost runs longer.
CONF_BOOST_MINUTES_UFH = "boost_minutes_underfloor"
# Per room: whether a radiator room starts early for a warmer change.
CONF_WARM_EARLY = "warm_early"
CONF_AWAY_TEMP = "away_temp"
CONF_HOLIDAY_TEMP = "holiday_temp"
CONF_OFF_TEMP = "off_temp"
CONF_ON_DELAY = "demand_on_delay"
CONF_OFF_DELAY = "demand_off_delay"
# How early a radiator room may start warming for a warmer schedule change.
# 0 turns warming up early off.
CONF_PREHEAT_MAX = "preheat_max_minutes"

# Hot water: Hearth heats the cylinder by turning on a Call for hot water
# switch (Fledge's, which keeps the Nest's hot water on while it's on) while
# no radiator room wants the boiler. Without a tank sensor it gives the cylinder so many
# minutes of gas ahead of each "ready by" time and leaves the cylinder's own
# thermostat to stop the boiler; with one it heats from the reheat
# temperature up to the target.
CONF_HW_SWITCH = "hot_water_switch"
# Before 1.0.0b10 Hearth set the thermostat's hot water boost (a select).
OLD_HW_BOOST = "hot_water_boost"
CONF_HW_SENSOR = "hot_water_sensor"
CONF_HW_READY = "hot_water_ready_by"
CONF_HW_MINUTES = "hot_water_minutes"
CONF_HW_TARGET = "hot_water_target"
CONF_HW_REHEAT = "hot_water_reheat_below"
# With a tank sensor: below this the hot water doesn't wait for the
# radiators. The boiler heats both until the tank is back to the reheat
# temperature.
CONF_HW_FLOOR = "hot_water_heat_anyway_below"

DEFAULTS = {
    CONF_HW_READY: ["06:30", "18:00"],
    CONF_HW_MINUTES: 60,
    CONF_HW_TARGET: 55.0,
    CONF_HW_REHEAT: 45.0,
    CONF_HW_FLOOR: 40.0,
    CONF_BOOST_TEMP: 22.0,
    CONF_BOOST_MINUTES: 60,
    CONF_BOOST_MINUTES_UFH: 120,
    CONF_AWAY_TEMP: 16.0,
    CONF_HOLIDAY_TEMP: 10.0,
    CONF_OFF_TEMP: 7.0,
    CONF_ON_DELAY: 60,
    CONF_OFF_DELAY: 180,
    CONF_PREHEAT_MAX: 180,
}

# Hysteresis for a radiator room asking for heat: from 0.3° below its
# target until it gets there. The valves shut at the target, so asking for
# more than that only fires the boiler into closed radiators.
DEMAND_BELOW = 0.3
DEMAND_ABOVE = 0.0
# A room whose valves have all been shut this long stops asking, once it's
# within the band: no radiator in it can take the heat.
VALVES_SHUT_MINUTES = 5
# A room that's within the band and hasn't got any warmer for this long stops
# asking: the boiler was running for a room that wasn't taking the heat.
STALL_MINUTES = 30
# A room still well short of its target that hasn't got any warmer for this
# long, while asking for heat, has a problem worth showing.
NOT_WARMING_MINUTES = 90

# How often an outside temperature is re-sent to a valve even if unchanged,
# so the valve never falls back to its own sensor.
EXTERNAL_REFRESH_SECONDS = 20 * 60

# Sensors flick between two readings 0.1° apart, every few seconds. A room
# takes a new reading when it has moved this far, or once it has stayed moved
# for this long, so a flick isn't a Zigbee write to a battery valve, a new
# state for the room and a row in the recorder each time.
READING_STEP = 0.2
READING_SETTLE_SECONDS = 60

# A room sensor that hasn't reported for this long has stopped (a flat
# battery keeps its last reading for ever): the room goes back to its
# valves' or stat's own reading until it reports again.
SENSOR_STALE_HOURS = 6
# A room sensor that's missing (unavailable, or stale) for this long has its
# valves put back on their own sensors. Not sooner: sensors drop out for a
# moment whenever Home Assistant or their integration restarts.
SENSOR_GONE_SECONDS = 15 * 60

# How far back the recorder is read for warm-ups when a room first learns.
SEED_DAYS = 10

# Settings Hearth keeps; anything else in an entry's options is from an older
# version and is dropped when the entry is migrated.
SETTINGS = (
    CONF_BOILER_SWITCH, CONF_BOOST_TEMP, CONF_BOOST_MINUTES, CONF_BOOST_MINUTES_UFH, CONF_AWAY_TEMP, CONF_HOLIDAY_TEMP,
    CONF_OFF_TEMP, CONF_ON_DELAY, CONF_OFF_DELAY, CONF_PREHEAT_MAX, CONF_ZONES,
    CONF_HW_SWITCH, CONF_HW_SENSOR, CONF_HW_READY, CONF_HW_MINUTES, CONF_HW_TARGET, CONF_HW_REHEAT, CONF_HW_FLOOR,
)

# How long before a "ready by" time the hot water may start, so it can find a
# gap between the radiators' warm-ups.
HW_WINDOW_HOURS = 6
# The hot water doesn't start when a radiator room is due to start warming
# within this long: the run would be cut off almost at once.
HW_CLEAR_MINUTES = 20
# After the radiators cut a run short, how long they must stay quiet before
# the hot water starts again.
HW_REST_MINUTES = 5
# Hot water turned off by hand stays off; after this long it's raised as a
# problem, in case it was forgotten.
HW_HELD_WARN_HOURS = 24
# A tank sensor that hasn't reported for this long isn't trusted: Hearth
# goes back to minutes of gas until it reports again.
HW_SENSOR_STALE_MINUTES = 45

# After Hearth unloads (a reload, say), how long the call for heat is left as
# it was for Hearth to come back, before it's turned off.
HANDOVER_SECONDS = 30

STORAGE_KEY = "hearth"
STORAGE_VERSION = 1
# 2: warm-ups read from the history before 1.0.0b5 are dropped; they were
# measured with sensors that reported once an hour.
STORAGE_MINOR_VERSION = 2

FRONTEND_URL = "/hearth_static"
CARD_FILE = "hearth-card.js"
COMMON_FILE = "hearth-common.js"

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_BOOST = "boost"
SERVICE_CANCEL_BOOST = "cancel_boost"
SERVICE_SET_HOLIDAY = "set_holiday"
SERVICE_ADD_WARM_UP = "add_warm_up"
SERVICE_BOOST_HOT_WATER = "boost_hot_water"
SERVICE_STOP_HOT_WATER = "stop_hot_water"
SERVICE_RESUME_HOT_WATER = "resume_hot_water"

# A problem that lasts this long is raised as a repair in Home Assistant, so
# it's seen without opening Hearth. Shorter ones are restarts and blips.
PROBLEM_AFTER_SECONDS = 15 * 60

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
