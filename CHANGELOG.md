# Changelog

## 1.0.0b5

- A new action, `hearth.add_warm_up`, tells a room about a warm-up it didn't time itself (how far it rose, and how long that took), so a room that's still learning can start early straight away. What it's told counts until the room has seen two warm-ups of its own.

## 1.0.0b4

**When something's wrong**
- A problem that lasts a quarter of an hour is raised as a repair in Home Assistant (Settings → Repairs), so a dead valve or a silent sensor is seen without opening Hearth. It clears by itself when the problem does.
- A schedule that doesn't reach a Heatmiser stat is sent again every five minutes, up to six times, and the room says "The schedule hasn't reached the thermostat" meanwhile. A stat running some other profile says so too.
- If Hearth can't start, it turns the Call for heat switch off instead of leaving it as it was.
- Every room has a `problem` attribute, shown in amber on the card and the panel: "Can't heat: the valve is offline", "A valve is offline", "The thermostat is offline", or "The room sensor isn't reporting: using the valve's own reading".
- A radiator room with no valve online stops asking for heat, so the boiler doesn't fire for a radiator that can't open.
- One room going wrong no longer stops the others being set or the Call for heat switch being turned off. Hearth looks at the house one pass at a time.
- A warning is logged when the Call for heat switch is unavailable.

**Radiator rooms**
- A room sensor flicking between two readings 0.1° apart no longer changes the room on every flick. A room takes a new reading when it has moved 0.2°, or once it has stayed moved for a minute: fewer Zigbee writes to the valve, and far fewer rows in the recorder.
- A room sensor that stops reporting for 6 hours (a flat battery keeps its last reading) is let go: the room uses the valve's reading and, a quarter of an hour on, the valve goes back to its own sensor, until the room sensor reports again. `room_sensor_stale` says when.
- Warming up early uses the warm-ups a room has seen itself once it has two; the history's only fill in until then. Warm-ups read from the history before this version are dropped, because they were measured with sensors reporting once an hour and made rooms start far too early. Those rooms learn again over their next two warm-ups.

**Holiday**
- A holiday can be given the moment it ends: the house goes back to Home by itself. Start one from the panel (Holiday asks first, with an optional "Back on") or with the new `hearth.set_holiday` action.
- Starting a holiday ends any boost, and a room can't be boosted while the house is on Holiday.
- While the house is Away or on Holiday, − and + are greyed out with the reason, instead of looking as if they'd worked and snapping back.

**Every room**
- A room that isn't heating says "Up to temperature" or, when it's below its target, "Not heating". It used to say "Comfortable" either way.
- Turning a valve off at the valve makes the room Off ("Off: turned off at the valve"), the way a stat's standby does; turning it up again puts the room back on its schedule. Hearth used to turn the valve back on within a minute.
- Each radiator room can opt out of starting early (Change room), for a bedroom that shouldn't heat ahead of a short morning slot.
- An underfloor room's boost has its own length (2 hours by default): a floor takes longer to answer than a radiator. The Boost button says what it does: "Boost 22° · 1 h".
- A valve can only be in one room, and a Hearth room or a Heatmiser stat can't be picked as a valve.

**Underfloor**
- A room that was Off, whose stat was turned back on while Home Assistant was down, comes back on its schedule.
- − and + during a boost change the hold on the stat. They used to change nothing.
- Boosting a room that's Off brings the stat out of standby for the boost and puts it back after. The stat used to ignore the boost.
- Away is kept up however long the house is away (the stat's hold used to run out after 99 hours), is applied to a room turned back on while away, and holds at the schedule's temperature when that's cooler than the away temperature, the same as a radiator room.
- A stat catching up with a schedule change no longer reads as "Changed on the thermostat" for a minute.

**Schedules**
- A change at the start of a day is only dropped as a repeat when weekdays and weekends both end on that temperature. With different endings, Monday (or Saturday) ran at the night before's temperature until its first change.

**The card and the panel**
- On a phone, the panel's Rooms tab opens with the rooms. "If Hearth stops" has moved to the House tab.
- The card's day strip shows its temperatures, and a room's row opens from the keyboard.
- One word for it everywhere: a room is "asking for heat".
- The schedule editor says a room starts early for a warmer change, and "Add a change" offers the day's other temperature.
- History's "On time" counts changes a room warmed up early for. It used to count only the ones it hadn't, so it read worse the better early starts worked.
- A room that's Off shows "Off" where its temperature goes, and − and + are greyed out: a tap used to make it Fixed at 7.5°.
- While a room warms up early, the label under its temperature says "by 18:30", not "until 18:30".
- A room page's Today graph is readable on a phone.
- The card shows its `title` when `show_house` is off.
- The schedule editor won't take two changes at the same time, and says when a Monday or Saturday starts on the other days' last temperature.
- "If Hearth stops" says what it covers: a stop or restart, not Home Assistant crashing.
- The line marking now on a day's strip stands out in dark themes; History's last time label no longer sits under "now"; values on a room's page don't wrap on a phone.

**Fixes**
- A profile still on its way to the neoHub when Hearth reloads can no longer set valves or the Call for heat switch from the settings Hearth had before.

## 1.0.0b3

**Calling for heat**
- A radiator room stops asking for heat when it reaches its target, not 0.2° above it. The valves shut at the target, so the boiler was firing into closed radiators.
- A room whose valves have all been shut for 5 minutes stops asking, once it's within 0.3° of its target.

**Underfloor**
- Turning a stat off (standby) at the stat or in the Heatmiser app shows the room as Off straight away ("Off: turned off on the thermostat"), instead of as a change to 12° on its schedule. Turning it back on at the stat puts the room back on its schedule.

## 1.0.0b2

**Fixes**
- A + or − on a radiator room no longer stops it warming up early for its next change. Turning a room down at lunchtime used to mean it only started heating for the evening at the evening's start time.

## 1.0.0b1

The first release candidate for Hearth 1.0.

**Every room the same**
- Every room says what it's doing in the same words: "Heating to 21° · about 35 min", "Warming up early · 21° by 07:00", "Comfortable" or "Off" (the new `status` attribute).
- Every room says how long it has to go while it heats (`minutes_to_target`), from how fast it has warmed before. Underfloor rooms now learn this too.
- An underfloor stat warming up early reads as warming up early (`hvac_action: preheating`), the same as a radiator room.
- A tap on + or − says what comes after it: "21° until 10:30, then 16°".
- One schedule editor, used by both the card and the panel. Copy to other rooms is a list of tick boxes.

**Calling for heat is a switch**
- Hearth only ever turns a Call for heat switch on and off. The boiler thermostat option and its temperatures are gone; an older install's settings are tidied when it upgrades.
- Saving a setting no longer drops the call for heat while Hearth reloads.

**The panel**
- Everyone in the house can open it; only admins change rooms and house settings.
- Configure now points to the panel, so there's one place to change things.

**Fixes**
- The day strip showed the wrong temperature before a Monday's or Saturday's first change.
- Changing an underfloor room's mode or schedule no longer waits up to a minute for the neoHub.
- Removing a room removes its device too.
- The card's Boost button says how long a boost lasts.
- Hearth's actions say what's wrong when a room is gone or Hearth isn't running.
- Room schedules and settings are no longer written to the recorder on every update.
