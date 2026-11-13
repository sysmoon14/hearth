# Hearth

**Every room's heating, the same way, whatever heats it.**

Hearth gives every room in the house one model and one card. Each room
has a temperature, a mode, a weekday/weekend schedule and a boost.
Whether a room is a radiator with a smart valve or an underfloor zone on a
Heatmiser stat is a setting you choose once. Nobody using the card ever
sees it.

## What every room has

| | |
|-|-|
| **Mode** | Schedule, Fixed or Off. In Home Assistant these are `auto`, `heat` and `off`. |
| **Schedule** | Weekdays and weekends, up to six changes a day, the same limits in every room. |
| **− and +** | In Schedule mode, a change lasts until the schedule's next change. In Fixed, it changes the fixed temperature. |
| **Boost** | Warmer for an hour (configurable), then back to what the room was doing. |
| **The physical control** | Turning a valve by hand counts the same as − or + on the card. |
| **A "why" line** | Every room says what it's doing and why: "19° until 17:00, then 16°", "Away: held at 16°", "Warming up early for 21°". |

The house has one more control, **Home / Away / Holiday**:

- **Away** holds every room no warmer than the away temperature (16° by default).
- **Holiday** keeps every room above frost level (10°) and puts the neoHub in its own away mode.

## Two kinds of room

**Radiator rooms (Sonoff TRVZB and other Zigbee2MQTT valves).** Home Assistant runs the schedule.
- Hearth sets each valve's target and puts the valve in plain heat mode, so the valve's own weekly schedule never runs.
- It feeds the valve the room's real temperature from a sensor elsewhere in the room, because a valve's own sensor sits on a hot radiator.
- It switches the boiler: when any radiator room wants heat, the boiler thermostat (a Nest, for example) is set to 25°; when none does, it's set to 10°. Short delays stop it switching on and off as valves settle.
- If Hearth stops, the boiler thermostat is left at 17° so the house can't go cold.
- **With a Fledge Nest**, choose its **Call for heat** switch in House settings instead: Hearth asks through it rather than setting temperatures, the Nest's screen says which rooms are asking, and if Hearth stops the Nest goes back to its own schedule by itself. Pick a **backup room** and Hearth keeps the Nest's own schedule as that room's, so the fallback is sensible.

**Underfloor rooms (Heatmiser neoStats, through the [heatmiserneo](https://github.com/MindrustUK/Heatmiser-for-home-assistant) integration).** The stat runs the schedule itself.
- Hearth writes the schedule to the neoHub as a 5/2-day profile (`Hearth Kitchen`, `Hearth Kitchen Fixed`, `Hearth Kitchen Off`) and makes the stat run it.
- The stat keeps its own early start and carries on if Home Assistant is down.
- − and + set the stat's own temperature, which lasts until its next level. Boost and Away are holds on the stat.
- Underfloor rooms call the boiler through their own wiring, so they don't count towards the radiator demand.

## Install

1. Open HACS, then ⋮ → Custom repositories. Add `https://github.com/sysmoon14/hearth` as an Integration.
2. Download **Hearth** and restart Home Assistant.
3. Go to Settings → Devices & services → Add integration → **Hearth**, and choose the thermostat that switches the boiler for your radiators.
4. Press **Configure** to add rooms and change the house settings.

The card loads automatically. Add it to a dashboard:

```yaml
type: custom:hearth-card            # every room, with Home/Away/Holiday
```

```yaml
type: custom:hearth-card
entity: climate.office              # one room, opened
```

```yaml
type: custom:hearth-card
entities: [climate.bedroom, climate.office]
show_house: false
title: Upstairs
```

## Entities

| Entity | What it is |
|-|-|
| `climate.<room>` | One per room. Attributes include `reason`, `schedule`, `next_change_at`, `next_change_temp`, `calling_for_heat`, `kind` and `devices`. |
| `select.hearth_house_mode` | Home, Away or Holiday. Point presence automations here. |
| `binary_sensor.hearth_radiators_want_heat` | On while the boiler is wanted for the radiators. Its attributes list which rooms are asking. |

## Actions

- `hearth.set_schedule`: set a room's `weekdays` and `weekends`, each a list like `[{"time": "06:30", "temp": 19}, {"time": "22:30", "temp": 16}]`.
- `hearth.boost`: boost a room, with optional `minutes` and `temperature`.
- `hearth.cancel_boost`: end a boost early.

## When something looks wrong

1. Read the room's "why" line on the card.
2. If that doesn't explain it, download diagnostics: Settings → Devices & services → Hearth → ⋮ → Download diagnostics. It has every room's settings, schedule, temperature source, devices and the boiler's last command.
