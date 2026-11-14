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
| **What it's doing** | Every room says it the same way: "Heating to 21° · about 35 min", "Warming up early · 21° by 07:00", "Comfortable" or "Off". It learns how fast each room warms to say how long it has to go. |
| **Why** | And why it's aiming there: "19° until 17:00, then 16°", "Away: held at 16°", "21° until 10:30, then 16°" after a tap on + or −. |

The house has one more control, **Home / Away / Holiday**:

- **Away** holds every room no warmer than the away temperature (16° by default).
- **Holiday** keeps every room above frost level (10°) and puts the neoHub in its own away mode.

## Calling for heat

Hearth asks the boiler for heat one way: a **Call for heat switch**. It's on while any radiator room wants heat and off when none does, with short delays so it doesn't flick on and off as valves settle. Any on/off `switch` or `input_boolean` works: a relay on the boiler, a thermostat's own call-for-heat switch, or a helper something else follows. What heats the house is up to what that switch drives; Hearth doesn't need to know.

If Hearth stops, it turns the switch off. When it only reloads (after you save a setting, say), the switch is left as it was for Hearth to pick up again, and turned off if Hearth isn't back within 30 seconds.

## Two kinds of room

**Radiator rooms (Sonoff TRVZB and other Zigbee2MQTT valves).** Home Assistant runs the schedule.
- Hearth sets each valve's target and puts the valve in plain heat mode, so the valve's own weekly schedule never runs.
- It feeds the valve the room's real temperature from a sensor elsewhere in the room, because a valve's own sensor sits on a hot radiator.
- It asks for heat with the Call for heat switch while the room is below its target.
- It warms up early. Each room learns how fast it warms, then starts early so a warmer schedule change is reached on time, not started then: "Warming up early for 21° by 07:00". It learns from each warm-up it sees and, the first time, from the recorder's history of when the Call for heat switch was on. Set how early it may start (up to 3 hours by default, 0 turns it off) in House settings. A room on its valve's own sensor learns from that reading, the one it decides it's warm by, but has no history to start from.
- `binary_sensor.hearth_radiators_want_heat` carries the same on/off, with the rooms asking in `rooms_asking`, for anything else that wants to follow it.

**Underfloor rooms (Heatmiser neoStats, through the [heatmiserneo](https://github.com/MindrustUK/Heatmiser-for-home-assistant) integration).** The stat runs the schedule itself.
- Hearth writes the schedule to the neoHub as a 5/2-day profile (`Hearth Kitchen`, `Hearth Kitchen Fixed`, `Hearth Kitchen Off`) and makes the stat run it.
- The stat keeps its own early start and carries on if Home Assistant is down. When it starts early, the room says "Warming up early", the same as a radiator room.
- Hearth learns how fast the floor warms, from the stat's heating and the room's temperature, so the room can say how long it has to go.
- Writing the schedule to the neoHub can take a minute. It happens in the background, and the room says "Sending the change to the thermostat…" meanwhile.
- − and + set the stat's own temperature, which lasts until its next level. Boost and Away are holds on the stat.
- Underfloor rooms call the boiler through their own wiring, so they don't count towards the radiator demand.

## Install

1. Open HACS, then ⋮ → Custom repositories. Add `https://github.com/sysmoon14/hearth` as an Integration.
2. Download **Hearth** and restart Home Assistant.
3. Go to Settings → Devices & services → Add integration → **Hearth**, and choose your Call for heat switch (or leave it and choose it in the panel later).
4. Open **Hearth** in the sidebar to add rooms, edit every room's schedule and change the house settings.

To try a release candidate before it's final, open Hearth in HACS, then ⋮ → Redownload, turn on **Show beta versions** and pick the newest. Turn it off again to stay on final releases.

### Upgrading from 0.x

Hearth 1.0 only calls for heat with a switch. If you had a boiler thermostat set, it's no longer used: choose a Call for heat switch on the House tab. The thermostat's temperatures (on, off and "if Hearth stops") go too. Everything else carries over.

## The Hearth panel

Everyone in the house can open it. Only admins see the House tab and can add, change or remove rooms.

- **Rooms:** Home / Away / Holiday, what the boiler and the underfloor are doing, what happens if Hearth stops, a card per room with its "why" line, and every room's day on one strip. Open a room for its controls, today's temperature against its target, and its devices.
- **History:** every room's last 24 hours, 3 days or 7 days from the recorder: its temperature against its target, and a strip showing when it was heating, warming up early or off. Above them, how long the boiler was asked for the radiators, how long rooms spent warming up early, and how many warmer schedule changes were reached on time. Hover or touch a graph for the reading at that moment.
- **Schedules:** one editor for every room: weekdays and weekends, up to six changes a day, copy to the other days or to other rooms. The card's Schedule button opens the same editor.
- **House:** the Call for heat switch, its delays, Away / Holiday / Boost temperatures, and the rooms with their devices. Add a room walks you through its valves or stat and where its temperature comes from.

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
| `climate.<room>` | One per room. `hvac_action` is `heating`, `preheating` (warming up early), `idle` or `off` in every room. Attributes include `status`, `reason`, `minutes_to_target`, `preheating`, `warm_up_rate` (degrees an hour), `warm_ups`, `schedule`, `next_change_at`, `next_change_temp`, `calling_for_heat`, `kind` and `devices`; radiator rooms also have `preheat_from`. |
| `select.hearth_house_mode` | Home, Away or Holiday. Point presence automations here. Its attributes carry the boost length and temperature. |
| `binary_sensor.hearth_radiators_want_heat` | On while the boiler is wanted for the radiators. Its attributes list which rooms are asking. |

## Actions

- `hearth.set_schedule`: set a room's `weekdays` and `weekends`, each a list like `[{"time": "06:30", "temp": 19}, {"time": "22:30", "temp": 16}]`.
- `hearth.boost`: boost a room, with optional `minutes` and `temperature`.
- `hearth.cancel_boost`: end a boost early.

## When something looks wrong

1. Read the room's "why" line on the card.
2. If that doesn't explain it, download diagnostics: Settings → Devices & services → Hearth → ⋮ → Download diagnostics. It has every room's settings, schedule, temperature source, devices and the boiler's last command.
