# Changelog

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
