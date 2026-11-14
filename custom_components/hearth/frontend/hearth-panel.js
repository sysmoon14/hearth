// Hearth's panel in Home Assistant's sidebar: every room, one schedule
// editor for all of them, and the house settings. Plain custom element, no
// build step. Rooms and house settings go through the hearth/* websocket
// commands (panel.py); schedules, modes, boosts and Home/Away/Holiday through
// Hearth's own actions and entities, the same ones the cards use.

import {
  VERSION, TICKS, boostLabel, clamp, copy, deg, esc, heating, holidayBack, houseLock, isOff, mins, nowPct, num, preheating, problemOf, reasonOf, statusOf, stripHtml, targetLabel, todayBlocks,
} from "./hearth-common.js?v=1.0.0b5";

const HOUSE = [["home", "Home"], ["away", "Away"], ["holiday", "Holiday"]];
const MODES = [["auto", "Schedule"], ["heat", "Fixed"], ["off", "Off"]];
const ICON_RAD = "M4 6v12M8 6v12M12 6v12M16 6v12M20 6v12M3 18h18";
const ICON_UFH = "M3 20h18M7 4c-1.5 2 1.5 4 0 6s1.5 4 0 6M12 4c-1.5 2 1.5 4 0 6s1.5 4 0 6M17 4c-1.5 2 1.5 4 0 6s1.5 4 0 6";
const ICON_FLAME = "M12 3c2 3 5 5 5 9a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-8z";


class HearthPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "rooms";
    this._room = null; // a room's page, by id
    this._schedRoom = null; // the Schedules tab's room, by id
    this._editor = document.createElement("hearth-schedule-editor");
    this._dialog = null; // add / edit a room
    this._holiday = null; // starting a holiday: { until }
    this._house = null; // the House tab's unsaved settings
    this._history = {};
    this._hist = null; // the History tab's readings, for one range
    this._histRange = 24;
    this._histLoading = false;
    this._busy = false;
    this._error = "";
    this._toast = "";
    this._sig = "";
    this._pickers = !!customElements.get("ha-entity-picker");
    const root = this.shadowRoot;
    this._steps = {}; // − / + taps not yet sent, by room id
    this._pressing = false;
    // Rebuilding the page under a finger or mouse loses the tap, so hold
    // updates until it lifts (the click comes just after).
    root.addEventListener("pointerdown", () => { this._pressing = true; });
    this._lifted = () => setTimeout(() => {
      this._pressing = false;
      if (this._pending && !this._typing() && !this._dialog && !this._holiday) this._render();
    }, 50);
    root.addEventListener("click", (e) => this._click(e));
    root.addEventListener("change", (e) => this._change(e));
    root.addEventListener("input", (e) => this._input(e));
    root.addEventListener("value-changed", (e) => this._picked(e));
    root.addEventListener("focusout", () => setTimeout(() => this._pending && !this._typing() && this._render(), 0));
  }

  // Listeners on the window live only while the panel is on the page.
  connectedCallback() {
    window.addEventListener("pointerup", this._lifted);
    window.addEventListener("pointercancel", this._lifted);
  }

  disconnectedCallback() {
    window.removeEventListener("pointerup", this._lifted);
    window.removeEventListener("pointercancel", this._lifted);
    clearTimeout(this._toastTimer);
    for (const step of Object.values(this._steps)) clearTimeout(step.timer);
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._loadPickers();
      this._load();
      return;
    }
    for (const picker of this.shadowRoot.querySelectorAll("ha-entity-picker")) picker.hass = hass;
    this._editor.hass = hass;
    if (!this._info) return;
    const sig = this._watched().map((id) => hass.states[id]?.last_updated).join("|");
    if (sig === this._sig) return;
    this._sig = sig;
    if (this._typing() || this._dialog || this._holiday || this._pressing) { this._pending = true; return; }
    this._render();
  }

  set narrow(value) {
    this._narrow = value;
    if (this._info) this._render();
  }

  set panel(_value) {}
  set route(_value) {}

  _typing() {
    const el = this.shadowRoot.activeElement;
    return !!el && ["INPUT", "SELECT", "TEXTAREA", "HA-ENTITY-PICKER", "HEARTH-SCHEDULE-EDITOR"].includes(el.tagName);
  }

  _watched() {
    const i = this._info;
    return [i.house_mode, i.demand, i.settings.boiler_switch,
      ...i.rooms.flatMap((r) => [r.entity_id, ...r.trvs, r.stat, r.sensor])].filter(Boolean);
  }

  // Home Assistant's entity picker, which it loads with its card editors.
  async _loadPickers() {
    if (this._pickers) return;
    try {
      const helpers = await window.loadCardHelpers?.();
      const card = helpers && (await helpers.createCardElement({ type: "entities", entities: [] }));
      await card?.constructor?.getConfigElement?.();
      await Promise.race([customElements.whenDefined("ha-entity-picker"), new Promise((r) => setTimeout(r, 4000))]);
    } catch (_) { /* a plain list then */ }
    if (customElements.get("ha-entity-picker")) {
      this._pickers = true;
      if (this._info) this._render();
    }
  }

  async _load() {
    try {
      this._info = await this._hass.callWS({ type: "hearth/info" });
      this._error = "";
      if (!this._roomById(this._schedRoom) && this._info.rooms.length) this._schedRoom = this._info.rooms[0].id;
      if (!this._info.is_admin && this._tab === "house") this._tab = "rooms";
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._render();
  }

  // After a save Hearth reloads: wait for its rooms to come back.
  async _reload() {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 500));
      try {
        const info = await this._hass.callWS({ type: "hearth/info" });
        if (info.loaded && info.rooms.every((r) => r.entity_id && this._hass.states[r.entity_id])) {
          this._info = info;
          break;
        }
      } catch (_) { /* still reloading */ }
    }
    await this._load();
  }

  _say(text) {
    this._toast = text;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this._toast = ""; this._render(); }, 4000);
  }

  async _call(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
      this._say(err?.message || String(err));
      this._render();
    }
  }

  // --- what the house is doing -------------------------------------------
  _state(id) { return id ? this._hass.states[id] : undefined; }
  _attr(id, key) { return this._state(id)?.attributes?.[key]; }
  _roomById(id) { return this._info.rooms.find((r) => r.id === id); }

  _admin() { return !!this._info?.is_admin; }

  _heating(room) {
    return heating(this._state(room.entity_id));
  }

  // --- rendering ------------------------------------------------------------
  _render() {
    this._pending = false;
    const root = this.shadowRoot;
    const scroll = root.querySelector(".content")?.scrollTop || 0;
    if (!this._info) {
      root.innerHTML = `<style>${STYLE}</style><div class="page"><div class="content"><div class="wrap">${
        this._error ? `<div class="card error">${esc(this._error)}</div>` : `<div class="muted">Loading…</div>`}</div></div></div>`;
      return;
    }
    this._sig = this._watched().map((id) => this._hass.states[id]?.last_updated).join("|");
    let body;
    if (this._room && this._roomById(this._room)) body = this._roomPage(this._roomById(this._room));
    else if (this._tab === "schedules") body = this._schedulesTab();
    else if (this._tab === "house") body = this._houseTab();
    else if (this._tab === "history") body = this._historyTab();
    else body = this._roomsTab();
    root.innerHTML = `<style>${STYLE}</style><div class="page">${this._header()}<div class="content">${
      this._info.loaded ? "" : `<div class="wrap"><div class="card warn">Hearth isn't running. Check Settings → Devices & services.</div></div>`}${body}</div>${
      this._dialog ? this._dialogHtml() : this._holiday ? this._holidayHtml() : ""}${this._toast ? `<div class="toast" role="status">${esc(this._toast)}</div>` : ""}</div>`;
    const content = root.querySelector(".content");
    if (content) content.scrollTop = scroll;
    this._wirePickers();
    const slot = root.querySelector("[data-editor]");
    if (slot) slot.replaceWith(this._editor);
    if (this._room) this._drawHistory(this._roomById(this._room));
    else if (this._tab === "history") this._wireHistory();
  }

  _header() {
    const menu = this._narrow ? `<button class="icon" data-act="menu" aria-label="Sidebar"><svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button>` : "";
    if (this._room) {
      const room = this._roomById(this._room);
      if (room) {
        const st = this._state(room.entity_id);
        const boosting = st?.attributes?.preset_mode === "boost";
        return `<header class="bar">${menu}
          <button class="icon" data-act="back" aria-label="Back to rooms"><svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 5l-7 7 7 7"/></svg></button>
          <h1>${esc(room.name)}</h1>${this._heating(room) ? `<span class="chip heat">${preheating(st) ? "Warming up early" : "Heating"}</span>` : ""}
          <span class="grow"></span>
          <button class="btn" data-act="boost" data-id="${esc(room.id)}" ${!boosting && this._state(this._info.house_mode)?.state === "holiday" ? "disabled" : ""}>${boosting ? "End boost" : `Boost ${esc(boostLabel(this._state(this._info.house_mode), st))}`}</button>
        </header>`;
      }
    }
    const mode = this._state(this._info.house_mode)?.state;
    const tabs = [["rooms", "Rooms"], ["history", "History"], ["schedules", "Schedules"], ...(this._admin() ? [["house", "House"]] : [])];
    return `<header class="bar">${menu}
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="var(--hp-heat)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICON_FLAME}"/></svg>
        <h1>Heating</h1>${holidayBack(this._state(this._info.house_mode)) ? `<span class="chip grey">Holiday · ${esc(holidayBack(this._state(this._info.house_mode)))}</span>` : ""}<span class="grow"></span>
        ${this._info.house_mode ? `<div class="seg" role="group" aria-label="House mode">${HOUSE.map(([k, label]) =>
          `<button data-act="house" data-mode="${k}" aria-pressed="${mode === k}" class="${mode === k ? "on" : ""}">${label}</button>`).join("")}</div>` : ""}
      </header>
      <nav class="tabs" aria-label="Hearth sections">${tabs.map(([k, label]) =>
        `<button data-act="tab" data-tab="${k}" class="${this._tab === k ? "on" : ""}" ${this._tab === k ? 'aria-current="page"' : ""}>${label}</button>`).join("")}</nav>`;
  }

  // Rooms ---------------------------------------------------------------------
  _roomsTab() {
    const rooms = this._info.rooms;
    if (!rooms.length) {
      return `<div class="wrap"><div class="card empty"><h2>No rooms yet</h2>
        <p class="muted">${this._admin() ? "Add each room once, radiator or underfloor, and Hearth runs them all the same way." : "Someone with an admin account adds the rooms."}</p>
        ${this._admin() ? `<button class="btn solid" data-act="add">Add a room</button>` : ""}</div></div>`;
    }
    const now = nowPct();
    return `<div class="wrap">
      <section class="status">${this._statusCards()}</section>
      <section class="stack">
        <div class="row between"><h2>Rooms</h2>${this._admin() ? `<button class="btn solid" data-act="add">Add a room</button>` : ""}</div>
        <div class="grid">${rooms.map((r) => this._roomCard(r)).join("")}</div>
      </section>
      <section class="card stack">
        <div class="row between"><h2>Today · ${new Date().toLocaleDateString(undefined, { weekday: "long" })}</h2>
          <span class="muted small">Now ${new Date().toTimeString().slice(0, 5)} · the line</span></div>
        <div class="scrollx"><div class="today">
          ${rooms.map((r) => `<div class="trow"><span class="tname">${esc(r.name)}</span>
            ${stripHtml(todayBlocks(this._state(r.entity_id)), { now })}</div>`).join("")}
          <div class="trow"><span class="tname"></span><div class="ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div></div>
        </div></div>
      </section></div>`;
  }

  _statusCards() {
    const i = this._info;
    const rads = i.rooms.filter((r) => r.kind === "radiator");
    const ufh = i.rooms.filter((r) => r.kind === "underfloor");
    const cards = [];
    if (rads.length) {
      const demand = this._state(i.demand);
      const asking = demand?.attributes?.rooms_asking || [];
      const on = demand?.state === "on";
      const sw = this._state(i.settings.boiler_switch);
      let how;
      if (!i.settings.boiler_switch) how = `Nothing calls for heat yet: ${this._admin() ? "choose a Call for heat switch on the House tab" : "an admin chooses the switch on the House tab"}`;
      else how = sw && ["on", "off"].includes(sw.state)
        ? `${esc(this._name(i.settings.boiler_switch))} is ${sw.state}`
        : `${esc(this._name(i.settings.boiler_switch))} is unavailable, so the boiler can't be asked`;
      cards.push(`<div class="card status-card ${on ? "hot" : ""}">
        <div class="row between"><span class="caps">Radiators</span><span class="chip ${on ? "heat" : "ok"}">${on ? "Asking for heat" : "Idle"}</span></div>
        <div class="big">${on ? `${esc(asking.join(", ") || "A room")} ${asking.length > 1 ? "are" : "is"} asking for heat` : "No room is asking for heat"}</div>
        <div class="muted small">${how}</div></div>`);
    }
    if (ufh.length) {
      const heating = ufh.filter((r) => this._heating(r)).map((r) => r.name);
      cards.push(`<div class="card status-card">
        <div class="row between"><span class="caps">Underfloor</span><span class="chip ${heating.length ? "heat" : "ok"}">${heating.length ? "Heating" : "Idle"}</span></div>
        <div class="big">${heating.length ? `${esc(heating.join(", "))} heating` : "No floor is heating"}</div>
        <div class="muted small">${esc(ufh.map((r) => r.name).join(", "))} ${ufh.length > 1 ? "run" : "runs"} on ${ufh.length > 1 ? "their thermostats" : "its thermostat"}, which ${ufh.length > 1 ? "carry" : "carries"} on if Home Assistant stops</div></div>`);
    }
    return cards.join("");
  }

  _roomCard(room) {
    const st = this._state(room.entity_id);
    const a = st?.attributes || {};
    const heating = this._heating(room);
    const modeLabel = { auto: "Schedule", heat: "Fixed", off: "Off" }[st?.state] || "Unavailable";
    const chip = { auto: "blue", heat: "purple", off: "grey" }[st?.state] || "warn";
    const devices = room.kind === "radiator"
      ? `Radiator · ${room.trvs.length} valve${room.trvs.length === 1 ? "" : "s"} · ${room.sensor ? esc(this._name(room.sensor)) : "valve's sensor"}`
      : "Underfloor · Heatmiser stat";
    return `<button class="card room" data-act="room" data-id="${esc(room.id)}">
      <div class="row between"><span class="rname"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="${heating ? "var(--hp-heat)" : "var(--secondary-text-color)"}" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="${room.kind === "radiator" ? ICON_RAD : ICON_UFH}"/></svg>${esc(room.name)}</span>
        <span class="chip ${chip}">${modeLabel}${a.preset_mode === "boost" ? " · Boost" : ""}</span></div>
      <div class="temps"><span class="now">${deg(a.current_temperature)}</span><span class="muted">${isOff(st) ? "off" : `→ ${deg(a.temperature)}`}</span></div>
      ${problemOf(st) ? `<div class="small warntext">${esc(problemOf(st))}</div>` : ""}
      <div class="why ${heating || st?.state === "off" ? "" : "muted"} ${heating ? "hot" : ""}">${esc(statusOf(st))}</div>
      ${reasonOf(st) ? `<div class="muted small">${esc(reasonOf(st))}</div>` : ""}
      ${this._admin() ? `<div class="muted small">${devices}</div>` : ""}</button>`;
  }

  _name(id) {
    return this._hass.states[id]?.attributes?.friendly_name || id;
  }

  // A room's page -------------------------------------------------------------
  _roomPage(room) {
    const st = this._state(room.entity_id);
    const a = st?.attributes || {};
    const heating = this._heating(room);
    const devices = room.kind === "radiator" ? room.trvs : [room.stat].filter(Boolean);
    const online = (id) => { const s = this._state(id); return s && !["unavailable", "unknown"].includes(s.state); };
    const devRows = devices.map((id) => {
      const s = this._state(id);
      const bits = [];
      if (s?.attributes?.current_temperature !== undefined) bits.push(`reads ${deg(s.attributes.current_temperature)}`);
      if (s?.attributes?.temperature !== undefined) bits.push(`set to ${deg(s.attributes.temperature)}`);
      return `<div class="kv"><span>${esc(this._name(id))}<span class="sub">${esc(bits.join(" · ") || id)}</span></span>
        <span class="${online(id) ? "good" : "bad"}">${online(id) ? "Online" : "Offline"}</span></div>`;
    }).join("");
    const sensor = room.sensor ? this._state(room.sensor) : null;
    const lock = isOff(st) ? "" : houseLock(this._state(this._info.house_mode), st);
    const still = isOff(st) || lock ? "disabled" : "";
    return `<div class="wrap split">
      <div class="main stack">
        <section class="card nowcard">
          <div class="stack tight grow">
            <span class="caps">Now</span>
            <div class="temps"><span class="huge">${deg(a.current_temperature)}</span><span class="muted mid">${isOff(st) ? "off" : `→ ${deg(a.temperature)}`}</span></div>
            ${problemOf(st) ? `<span class="warntext">${esc(problemOf(st))}</span>` : ""}
            <span class="why ${heating ? "hot" : ""}" style="font-size:17px">${esc(statusOf(st))}</span>
            ${reasonOf(st) ? `<span class="muted">${esc(reasonOf(st))}</span>` : ""}
          </div>
          <div class="controls">
            <div class="row" style="gap:18px">
              <button class="round" data-act="step" data-id="${esc(room.id)}" data-step="-0.5" aria-label="Cooler" ${still}>−</button>
              <div class="target"><div class="tbig">${isOff(st) ? "Off" : deg(this._steps[room.id]?.temp ?? a.temperature)}</div><div class="muted small">${esc(!st ? "" : isOff(st) ? `kept above ${deg(a.temperature)}` : targetLabel(st))}</div></div>
              <button class="round" data-act="step" data-id="${esc(room.id)}" data-step="0.5" aria-label="Warmer" ${still}>+</button>
            </div>
            <div class="seg" role="group" aria-label="Mode">${MODES.map(([k, label]) =>
              `<button data-act="mode" data-id="${esc(room.id)}" data-mode="${k}" class="${st?.state === k ? "on" : ""}" aria-pressed="${st?.state === k}">${label}</button>`).join("")}</div>
            ${lock ? `<span class="muted small">${esc(lock)}</span>` : ""}
          </div>
        </section>
        <section class="card stack">
          <div class="row between"><h2>Today</h2><span class="muted small">Line: room · dashes: target · orange: heating</span></div>
          <div class="chart" data-chart="${esc(room.id)}"><div class="muted small">Loading today's readings…</div></div>
        </section>
        ${this._preheatCard(room, a)}
      </div>
      <aside class="card side">
        <h2>${esc(room.name)}</h2>
        <div class="kv"><span>Heated by<span class="sub">${room.kind === "radiator" ? "Valves, with the boiler asked through the House tab" : `Its Heatmiser stat, running the “${esc(room.profile || `Hearth ${room.name}`)}” profile`}</span></span><span>${room.kind === "radiator" ? "Radiator" : "Underfloor"}</span></div>
        ${devRows}
        <div class="kv"><span>Room temperature<span class="sub">${room.sensor ? esc(this._name(room.sensor)) + (room.kind === "radiator" ? ", fed to the valve" : "") : room.kind === "radiator" ? "The valve's own: it sits on the radiator and reads high" : "The stat's own"}</span></span><span>${sensor ? deg(sensor.state) : deg(a.current_temperature)}</span></div>
        ${room.kind === "radiator" ? `<div class="kv"><span>Turning the valve by hand<span class="sub">Counts the same as − or +</span></span><span>On</span></div>` : ""}
        <div class="kv"><span>Away · Holiday<span class="sub">The house settings</span></span><span>${deg(this._info.settings.away_temp)} · ${deg(this._info.settings.holiday_temp)}</span></div>
        <div class="row flexwrap" style="padding-top:12px">
          <button class="btn" data-act="edit-schedule" data-id="${esc(room.id)}">Edit schedule</button>
          ${this._admin() ? `<button class="btn" data-act="edit" data-id="${esc(room.id)}">Change room</button>
          <button class="btn danger" data-act="remove" data-id="${esc(room.id)}">Remove</button>` : ""}
        </div>
        <span class="muted small" style="padding-top:12px">${esc(room.entity_id || "")} · in Home Assistant its modes are Auto (Schedule), Heat (Fixed) and Off.</span>
      </aside></div>`;
  }

  // How fast the room warms, how long it has to go, and when it starts early:
  // the same card for every room.
  _preheatCard(room, a) {
    const card = (chip, chipClass, body) => `<section class="card stack">
      <div class="row between"><h2>Warming up</h2><span class="chip ${chipClass}">${chip}</span></div>${body}</section>`;
    const n = a.warm_ups || 0;
    const hist = a.warm_ups_from_history || 0;
    const learned = `${n} warm-up${n === 1 ? "" : "s"}${hist ? ` (${hist} from history)` : ""}`;
    const own = room.kind === "radiator" ? "the valve's" : "the stat's";
    const source = room.sensor ? "" : ` Timed by ${own} own reading, the one ${esc(room.name)} decides it's warm by.`;
    const early = room.kind === "radiator"
      ? (room.warm_early === false ? `Starting early is off for ${esc(room.name)} (Change room).` : Number(this._info.settings.preheat_max_minutes) ? `Hearth starts ${esc(room.name)} early, up to ${Number(this._info.settings.preheat_max_minutes)} min, so a warmer change is reached on time.` : "Starting early is off for radiator rooms (House settings).")
      : "The stat starts early by itself (its optimum start), so each change is reached on time.";
    if (a.warm_up_rate == null) {
      return card("Learning", "grey", `<span class="muted">Hearth times each warm-up, from asking for heat to reaching the target, and says how long a room has to go once it has seen 2. It has seen ${learned} so far.${source}</span>
        <span class="muted">${early}</span>`);
    }
    const rate = Number(a.warm_up_rate);
    let now;
    if (a.preheating) now = `${statusOf(this._state(room.entity_id))}.`;
    else if (a.minutes_to_target != null) now = `About ${a.minutes_to_target} min to reach ${deg(a.temperature)}.`;
    else if (room.kind === "radiator" && a.preheat_from && a.next_change_at) now = `Next: starts about ${new Date(a.preheat_from).toTimeString().slice(0, 5)} to reach ${deg(a.next_change_temp)} by ${new Date(a.next_change_at).toTimeString().slice(0, 5)}.`;
    else now = "Not heating now.";
    return card(a.preheating ? "Warming up early" : "Learned", a.preheating ? "heat" : "ok", `
      <div class="kv"><span>Warms about<span class="sub">Learned from ${learned}</span></span><span>${rate.toFixed(1)}° an hour</span></div>
      <div class="kv"><span>A degree takes<span class="sub">Including the boiler and ${room.kind === "radiator" ? "radiators" : "floor"} getting warm</span></span><span>${Math.round(60 / rate)} min</span></div>
      <span>${esc(now)}</span>
      <span class="muted">${early}${source}</span>`);
  }

  async _drawHistory(room) {
    const host = this.shadowRoot.querySelector(`[data-chart="${CSS.escape(room.id)}"]`);
    if (!host || !room.entity_id) return;
    // Drawn at the width it's shown, so its labels are the same size on a
    // phone as on a desk.
    const width = Math.max(280, Math.round(host.clientWidth || 600));
    const cached = this._history[room.id];
    if (cached && cached.width === width && Date.now() - cached.at < 120000) { host.innerHTML = cached.svg; return; }
    const start = new Date(); start.setHours(0, 0, 0, 0);
    try {
      const res = await this._hass.callWS({
        type: "history/history_during_period", start_time: start.toISOString(), entity_ids: [room.entity_id],
        minimal_response: false, no_attributes: false, significant_changes_only: false,
      });
      const rows = (res[room.entity_id] || []).map((r) => ({ t: (r.lu ?? r.lc) * 1000, a: r.a || {} }));
      const svg = this._chart(rows, start.getTime(), width);
      this._history[room.id] = { at: Date.now(), svg, width };
      const now = this.shadowRoot.querySelector(`[data-chart="${CSS.escape(room.id)}"]`);
      if (now) now.innerHTML = svg;
    } catch (err) {
      host.innerHTML = `<div class="muted small">Today's readings couldn't be loaded.</div>`;
    }
  }

  _chart(rows, dayStart, W = 1000) {
    // Attributes carry forward between rows (each row lists only what changed).
    let cur = {};
    const pts = rows.map((r) => { cur = { ...cur, ...r.a }; return { t: r.t, room: num(cur.current_temperature), target: num(cur.temperature), heat: ["heating", "preheating"].includes(cur.hvac_action) }; })
      .filter((p) => p.t >= dayStart);
    if (!pts.length) return `<div class="muted small">No readings yet today.</div>`;
    const now = Date.now();
    const vals = pts.flatMap((p) => [p.room, p.target]).filter((v) => v !== null);
    let lo = Math.floor(Math.min(...vals) - 1), hi = Math.ceil(Math.max(...vals) + 1);
    if (hi - lo < 4) { hi = lo + 4; }
    const H = 230, L = 34, T = 20, B = 200;
    const x = (t) => L + ((t - dayStart) / 86400000) * (W - L);
    const y = (v) => B - ((v - lo) / (hi - lo)) * (B - T);
    const line = (key, step) => {
      let d = ""; let prev = null;
      for (const p of pts) {
        if (p[key] === null) continue;
        if (!d) d = `M${x(p.t).toFixed(1)} ${y(p[key]).toFixed(1)}`;
        else d += step ? ` H${x(p.t).toFixed(1)} V${y(p[key]).toFixed(1)}` : ` L${x(p.t).toFixed(1)} ${y(p[key]).toFixed(1)}`;
        prev = p[key];
      }
      if (prev !== null && d) d += step ? ` H${x(now).toFixed(1)}` : ` L${x(now).toFixed(1)} ${y(prev).toFixed(1)}`;
      return d;
    };
    const bars = [];
    pts.forEach((p, i) => { if (p.heat) bars.push([p.t, i + 1 < pts.length ? pts[i + 1].t : now]); });
    const grid = [];
    for (let v = lo; v <= hi; v += (hi - lo > 8 ? 2 : 1)) grid.push(`<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="0" y="${y(v) + 4}">${v}°</text>`);
    const last = [...pts].reverse().find((p) => p.room !== null);
    return `<svg viewBox="0 0 ${W} ${H + 20}" role="img" aria-label="Today's room temperature and target">
      <g class="grid">${grid.join("")}</g>
      <g class="axis">${[0, 6, 12, 18, 24].map((h) => `<text x="${L + (h / 24) * (W - L) - (h === 24 ? 16 : 0)}" y="${H + 14}">${String(h).padStart(2, "0")}</text>`).join("")}</g>
      <path d="${line("target", true)}" fill="none" stroke="var(--secondary-text-color)" stroke-width="2" stroke-dasharray="3 4"/>
      <path d="${line("room", false)}" fill="none" stroke="var(--primary-color)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
      ${bars.map(([a, b]) => `<rect x="${x(a)}" y="${B + 12}" width="${Math.max(2, x(b) - x(a))}" height="6" rx="3" fill="var(--hp-heat)"/>`).join("")}
      ${last ? `<circle cx="${x(now)}" cy="${y(last.room)}" r="6" fill="var(--primary-color)"/>` : ""}
    </svg>`;
  }

  // History -------------------------------------------------------------------
  // Every room over the last day (or 3 or 7), from the recorder: its
  // temperature, its target, and when it was heating, warming up early or off.
  _historyTab() {
    const rooms = this._info.rooms;
    if (!rooms.length) return this._roomsTab();
    const hours = this._histRange;
    const h = this._hist;
    if (!(h && h.hours === hours && Date.now() - h.at < 300000) && !this._histLoading) this._loadHistory(hours);
    const ranges = [[24, "24 hours"], [72, "3 days"], [168, "7 days"]];
    const when = (t) => `${new Date(t).toLocaleDateString(undefined, hours > 24 ? { weekday: "short", day: "numeric", month: "short" } : { weekday: "long" })} ${new Date(t).toTimeString().slice(0, 5)}`;
    const ready = h && h.hours === hours && !h.error;
    const head = `<div class="row between flexwrap" style="gap:16px">
        <div class="stack tight"><h2 class="htitle">Last ${ranges.find(([k]) => k === hours)[1]}</h2>
          <span class="muted small">${ready ? `${esc(when(h.start))} to ${esc(when(h.end))} · from Home Assistant's recorder` : "From Home Assistant's recorder"}</span></div>
        <div class="seg" role="group" aria-label="Time range">${ranges.map(([k, label]) =>
          `<button data-act="hist-range" data-hours="${k}" class="${hours === k ? "on" : ""}" aria-pressed="${hours === k}">${label}</button>`).join("")}</div>
      </div>`;
    if (!h || h.hours !== hours) return `<div class="wrap">${head}<div class="card muted">Loading every room's readings…</div></div>`;
    if (h.error) return `<div class="wrap">${head}<div class="card error">The readings couldn't be loaded: ${esc(h.error)}</div></div>`;

    const dur = (ms) => { const m = Math.round(ms / 60000); const hh = Math.floor(m / 60), mm = m % 60; return hh ? (mm ? `${hh} h ${mm} min` : `${hh} h`) : `${mm} min`; };
    const rads = h.rooms.filter((r) => r.room.kind === "radiator");
    const early = h.rooms.filter((r) => r.pre > 0);
    const totals = [];
    if (rads.length) totals.push(["Boiler asked for radiators", dur(h.boiler), "Any radiator room heating or warming up early"]);
    totals.push(["Warming up early", dur(early.reduce((n, r) => n + r.pre, 0)), early.length ? early.map((r) => r.room.name).join(", ") : "No room needed to"]);
    totals.push(["On time", h.changes ? `${h.reached} of ${h.changes}` : "–", h.changes ? "Warmer schedule changes the room had reached by their time" : "No warmer schedule changes in this time"]);

    const { lo, hi } = h.scale;
    const ticks = h.ticks.map((t) => `<span style="left:${t.pct.toFixed(2)}%" class="${t.day ? "day" : ""}">${esc(t.label)}</span>`).join("") + `<span style="left:100%" class="nowtick">now</span>`;
    const grid = h.ticks.map((t) => `M${(t.pct * 10).toFixed(1)} 0V150`).join("");
    const segs = (r, cls) => r.segs.map((s) => `<div class="${cls} s-${s.state}" style="width:${s.pct.toFixed(3)}%"></div>`).join("");
    const card = (r, i) => {
      const room = r.room;
      const heated = r.alwaysOff ? "Off the whole time" : r.heat + r.pre ? `Heated ${dur(r.heat + r.pre)}` : "Didn't need heat";
      const earlyLine = room.kind !== "radiator" ? "Starts early by itself" : r.pre ? `of which ${dur(r.pre)} warming up early` : "No early start";
      const nowTop = r.now === null ? null : ((hi - r.now) / (hi - lo)) * 100;
      return `<article class="card hroom">
        <div class="hinfo">
          <button class="hname" data-act="room" data-id="${esc(room.id)}"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="${room.kind === "radiator" ? ICON_RAD : ICON_UFH}"/></svg>${esc(room.name)}</button>
          <div class="temps"><span class="hnow">${deg(r.now)}</span><span class="muted small">now</span></div>
          <span class="small ${r.heat + r.pre ? "hhot" : "muted"}">${esc(heated)}</span>
          ${r.alwaysOff ? "" : `<span class="muted small">${esc(earlyLine)}</span>`}
          ${r.lo === null ? "" : `<span class="muted small">Low ${deg(r.lo)} · high ${deg(r.hi)}</span>`}
          <span class="muted tiny">${room.kind === "radiator" ? "Radiator" : "Underfloor"}</span>
        </div>
        <div class="hplot">
          <div class="haxis" aria-hidden="true"><span>${hi}°</span><span>${Math.round((hi + lo) / 2)}°</span><span>${lo}°</span></div>
          <div class="hcol">
            <div class="hchart" data-hist="${i}">
              <div class="hwash" aria-hidden="true">${segs(r, "w")}</div>
              <svg viewBox="0 0 1000 150" preserveAspectRatio="none" role="img" aria-label="${esc(`${room.name}: ${heated.toLowerCase()}${r.lo === null ? "" : `, between ${deg(r.lo)} and ${deg(r.hi)}`}`)}">
                <path d="${grid}" class="hgrid"/><path d="M0 0H1000M0 75H1000M0 150H1000" class="hgrid"/>
                <path d="${r.targetPath}" class="htarget"/><path d="${r.roomPath}" class="hline"/>
              </svg>
              ${nowTop === null ? "" : `<i class="hdot" style="top:${nowTop.toFixed(2)}%"></i>`}
              <i class="hcursor" hidden></i><div class="htip" hidden></div>
            </div>
            <div class="hband" aria-hidden="true">${segs(r, "b")}</div>
            <div class="hticks" aria-hidden="true">${ticks}</div>
          </div>
        </div>
      </article>`;
    };
    return `<div class="wrap">${head}
      <section class="status">${totals.map(([cap, big, sub]) => `<div class="card status-card"><span class="caps">${esc(cap)}</span><span class="hbig">${esc(big)}</span><span class="muted small">${esc(sub)}</span></div>`).join("")}</section>
      <div class="hkey small">
        <span><svg width="26" height="10" aria-hidden="true"><line x1="1" y1="5" x2="25" y2="5" class="hline"/></svg>Room temperature</span>
        <span><svg width="26" height="10" aria-hidden="true"><line x1="1" y1="5" x2="25" y2="5" class="htarget"/></svg>Target</span>
        <span><i class="b s-heat"></i>Heating</span><span><i class="b s-pre"></i>Warming up early</span>
        <span><i class="b s-off"></i>Off</span><span><i class="b s-idle"></i>Not asking</span>
      </div>
      <section class="stack">${h.rooms.map(card).join("")}</section>
      <p class="muted small">Hover or touch a graph for the time, temperature, target and what the room was doing.</p>
    </div>`;
  }

  async _loadHistory(hours) {
    this._histLoading = true;
    const end = Date.now();
    const start = end - hours * 3600000;
    const rooms = this._info.rooms;
    try {
      const res = await this._hass.callWS({
        type: "history/history_during_period", start_time: new Date(start).toISOString(), end_time: new Date(end).toISOString(),
        entity_ids: rooms.map((r) => r.entity_id).filter(Boolean),
        minimal_response: false, no_attributes: false, significant_changes_only: false,
      });
      this._hist = this._histData(hours, start, end, rooms.map((r) => this._roomHistory(r, res[r.entity_id] || [], start, end)));
    } catch (err) {
      this._hist = { hours, at: Date.now(), error: err?.message || String(err) };
    }
    this._histLoading = false;
    if (this._tab !== "history" || this._room) return;
    if (this._typing() || this._dialog || this._pressing) this._pending = true;
    else this._render();
  }

  // One room's readings as points, each lasting until the next.
  _roomHistory(room, rows, start, end) {
    let attrs = {};
    let state = null;
    const pts = [];
    for (const row of rows) {
      if (row.s !== undefined) state = row.s;
      attrs = { ...attrs, ...(row.a || {}) };
      const gone = !state || state === "unavailable" || state === "unknown";
      const act = attrs.hvac_action;
      pts.push({
        t: Math.max(start, (row.lu ?? row.lc) * 1000),
        room: gone ? null : num(attrs.current_temperature),
        target: gone || state === "off" ? null : num(attrs.temperature),
        state: gone ? "gap" : act === "preheating" ? "pre" : act === "heating" ? "heat" : state === "off" || act === "off" ? "off" : "idle",
        nextAt: attrs.next_change_at ? Date.parse(attrs.next_change_at) : null,
        nextTemp: num(attrs.next_change_temp),
        early: !!attrs.preheating,
      });
    }
    const segs = [];
    let heat = 0, pre = 0, reached = 0, changes = 0;
    const busy = [];
    // What the room was set to before any early start for the change ahead.
    let sched = null;
    pts.forEach((p, i) => {
      const until = i + 1 < pts.length ? pts[i + 1].t : end;
      if (until <= p.t) return;
      const last = segs[segs.length - 1];
      if (last && last.state === p.state && last.until === p.t) last.until = until;
      else segs.push({ state: p.state, from: p.t, until });
      if (p.state === "heat") heat += until - p.t;
      if (p.state === "pre") pre += until - p.t;
      if (p.state === "heat" || p.state === "pre") busy.push([p.t, until]);
      // A warmer schedule change: was the room already there when it came?
      // The change is the moment the one it was waiting for passes. (The
      // target itself rises earlier, when the room starts warming up for it.)
      const prev = pts[i - 1];
      if (prev && prev.nextAt && prev.nextTemp !== null && p.nextAt !== prev.nextAt
          && p.t >= prev.nextAt - 1000 && p.t - prev.nextAt < 600000
          && sched !== null && prev.nextTemp > sched && p.state !== "off" && p.state !== "gap") {
        changes++;
        const room = p.room ?? prev.room;
        if (room !== null && room >= prev.nextTemp - 0.3) reached++;
      }
      if (p.target !== null && !p.early) sched = p.target;
    });
    const temps = pts.map((p) => p.room).filter((v) => v !== null);
    const now = [...pts].reverse().find((p) => p.room !== null)?.room ?? null;
    return {
      room, pts, segs, heat, pre, reached, changes, busy, now,
      lo: temps.length ? Math.min(...temps) : null, hi: temps.length ? Math.max(...temps) : null,
      targets: pts.map((p) => p.target).filter((v) => v !== null),
      alwaysOff: segs.length > 0 && segs.every((s) => s.state === "off" || s.state === "gap"),
    };
  }

  // Shared scale, ticks, paths and the house totals.
  _histData(hours, start, end, rooms) {
    const vals = rooms.flatMap((r) => [r.lo, r.hi, ...r.targets]).filter((v) => v !== null);
    let lo = vals.length ? Math.floor(Math.min(...vals) - 0.5) : 14;
    let hi = vals.length ? Math.ceil(Math.max(...vals) + 0.5) : 22;
    if (hi - lo < 6) { const mid = (hi + lo) / 2; lo = Math.floor(mid - 3); hi = lo + 6; }
    if ((hi - lo) % 2) hi += 1;
    const span = end - start;
    const x = (t) => (((t - start) / span) * 1000).toFixed(1);
    const y = (v) => (((hi - v) / (hi - lo)) * 150).toFixed(1);
    for (const r of rooms) {
      let d = "", t = "", prevT = null;
      let open = false;
      for (const p of r.pts) {
        if (p.room === null) open = false;
        else { d += `${open ? "L" : "M"}${x(p.t)} ${y(p.room)}`; open = true; }
        if (p.target === null) prevT = null;
        else { t += prevT === null ? `M${x(p.t)} ${y(p.target)}` : `H${x(p.t)}V${y(p.target)}`; prevT = p.target; }
      }
      if (open && r.now !== null) d += `H1000`;
      if (prevT !== null) t += `H1000`;
      r.roomPath = d;
      r.targetPath = t;
      for (const s of r.segs) s.pct = ((s.until - s.from) / span) * 100;
      const first = r.segs[0];
      if (first && first.from > start) r.segs.unshift({ state: "gap", from: start, until: first.from, pct: ((first.from - start) / span) * 100 });
    }
    // The boiler: any radiator room heating or warming up, merged.
    const busy = rooms.filter((r) => r.room.kind === "radiator").flatMap((r) => r.busy).sort((a, b) => a[0] - b[0]);
    let boiler = 0, cur = null;
    for (const [a, b] of busy) {
      if (cur && a <= cur[1]) cur[1] = Math.max(cur[1], b);
      else { if (cur) boiler += cur[1] - cur[0]; cur = [a, b]; }
    }
    if (cur) boiler += cur[1] - cur[0];
    // Ticks on whole hours: every 3 for a day, 12 for 3 days, midnights for a week.
    const every = hours <= 24 ? 3 : hours <= 72 ? 12 : 24;
    const ticks = [];
    const d = new Date(start); d.setMinutes(0, 0, 0);
    while (d.getTime() <= start || d.getHours() % every) d.setHours(d.getHours() + 1);
    for (; d.getTime() < end - span * 0.07; d.setHours(d.getHours() + every)) {
      const day = d.getHours() === 0;
      ticks.push({ pct: ((d.getTime() - start) / span) * 100, day,
        label: day ? d.toLocaleDateString(undefined, { weekday: "short" }) : `${String(d.getHours()).padStart(2, "0")}:00` });
    }
    return {
      hours, at: Date.now(), start, end, rooms, ticks, boiler, scale: { lo, hi },
      reached: rooms.reduce((n, r) => n + r.reached, 0), changes: rooms.reduce((n, r) => n + r.changes, 0),
    };
  }

  // The readout under a finger or pointer.
  _wireHistory() {
    const h = this._hist;
    if (!h || h.error) return;
    const words = { heat: "Heating", pre: "Warming up early", off: "Off", idle: "Not asking for heat", gap: "No readings" };
    for (const el of this.shadowRoot.querySelectorAll(".hchart")) {
      const r = h.rooms[Number(el.dataset.hist)];
      const cursor = el.querySelector(".hcursor");
      const tip = el.querySelector(".htip");
      const show = (e) => {
        const box = el.getBoundingClientRect();
        const f = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
        const t = h.start + f * (h.end - h.start);
        let p = null;
        for (const q of r.pts) { if (q.t <= t) p = q; else break; }
        const when = `${h.hours > 24 ? `${new Date(t).toLocaleDateString(undefined, { weekday: "short" })} ` : ""}${new Date(t).toTimeString().slice(0, 5)}`;
        const temps = p && p.room !== null ? `${deg(p.room)}${p.target !== null ? ` → ${deg(p.target)}` : ""}` : "";
        tip.innerHTML = `<b>${esc(when)}</b>${temps ? ` · ${esc(temps)}` : ""}<br><span class="${p && (p.state === "heat" || p.state === "pre") ? "hhot" : "muted"}">${esc(words[p?.state || "gap"])}</span>`;
        cursor.style.left = `${(f * 100).toFixed(2)}%`;
        tip.style.left = `${(f * 100).toFixed(2)}%`;
        tip.style.transform = `translateX(${f > 0.7 ? "-100%" : f < 0.3 ? "0" : "-50%"})`;
        cursor.hidden = false;
        tip.hidden = false;
      };
      const hide = () => { cursor.hidden = true; tip.hidden = true; };
      el.addEventListener("pointermove", show);
      el.addEventListener("pointerdown", show);
      el.addEventListener("pointerleave", hide);
    }
  }

  // Schedules -------------------------------------------------------------------
  _schedulesTab() {
    const rooms = this._info.rooms;
    if (!rooms.length) return this._roomsTab();
    if (!this._roomById(this._schedRoom)) this._schedRoom = rooms[0].id;
    const room = this._roomById(this._schedRoom);
    const st = this._state(room.entity_id);
    const mode = { auto: "Schedule", heat: `Fixed ${deg(st?.attributes?.temperature)}`, off: "Off" }[st?.state] || "unavailable";
    const ed = this._editor;
    ed.rooms = rooms.filter((r) => r.entity_id).map((r) => ({ entity_id: r.entity_id, name: r.name }));
    ed.hass = this._hass;
    ed.room = room.entity_id;
    return `<div class="wrap stack">
      <div class="pills" role="group" aria-label="Room">${rooms.map((r) =>
        `<button class="pill ${r.id === room.id ? "on" : ""}" data-act="sched-room" data-id="${esc(r.id)}" aria-pressed="${r.id === room.id}">${esc(r.name)}</button>`).join("")}</div>
      <div class="split">
        <section class="card main"><div data-editor></div></section>
        <aside class="sidecol stack">
          <div class="card stack tight"><b>${esc(room.name)} is on ${esc(mode)}</b>
            <span class="muted">${st?.state === "auto" ? "This schedule is what it runs. − and + on a card last until the next change here." : "This schedule is kept for when the room goes back to Schedule. Change the mode on the room's page."}</span></div>
          <div class="card stack tight"><b>${room.kind === "underfloor" ? "On the stat" : "Run by Hearth"}</b>
            <span class="muted">${room.kind === "underfloor"
              ? `Saved in Home Assistant and written to the neoHub as the “${esc(room.profile || `Hearth ${room.name}`)}” profile. The stat runs it with its own early start, even if Home Assistant is down.`
              : `Hearth sets ${esc(room.name)}'s valve${room.trvs.length === 1 ? "" : "s"} at each change and asks for heat when the room is below target.`}</span></div>
          <div class="card stack tight"><b>Same rules in every room</b>
            <span class="muted">Weekdays and weekends, up to six changes a day: the neoHub's limit, kept everywhere so a schedule can move between rooms. A change to the temperature already in force is merged away.</span></div>
        </aside>
      </div></div>`;
  }

  // House -------------------------------------------------------------------------
  _houseTab() {
    const i = this._info;
    if (!this._house) this._house = copy(i.settings);
    const h = this._house;
    const dirty = JSON.stringify(h) !== JSON.stringify(i.settings);
    const sw = this._state(h.boiler_switch);
    const demand = this._state(i.demand);
    const swOk = sw && ["on", "off"].includes(sw.state);
    const tempRow = (key, label, sub, lo, hi) => `<div class="kv"><label for="h-${key}">${label}${sub ? `<span class="sub">${sub}</span>` : ""}</label>
      <span class="unit"><input id="h-${key}" type="number" min="${lo}" max="${hi}" step="0.5" value="${esc(h[key])}" data-house="${key}">°</span></div>`;
    const secRow = (key, label, sub, lo, hi, unit, scale) => `<div class="kv"><label for="h-${key}">${label}${sub ? `<span class="sub">${sub}</span>` : ""}</label>
      <span class="unit"><input id="h-${key}" type="number" min="${lo / scale}" max="${hi / scale}" step="1" value="${esc(Math.round(Number(h[key]) / scale))}" data-house="${key}" data-scale="${scale}">${unit}</span></div>`;
    return `<div class="wrap split">
      <div class="main stack">
        <section class="card stack">
          <div class="row between flexwrap"><h2>How the radiators get heat</h2>
            <span class="chip ${demand?.state === "on" ? "heat" : "ok"}">${demand?.state === "on" ? `Asking · ${esc((demand.attributes.rooms_asking || []).join(", "))}` : "Idle"}</span></div>
          <div class="flow" aria-label="How a room's ask reaches the boiler">
            <span class="node">A room below target</span><span aria-hidden="true">→</span>
            <span class="node">${esc(h.boiler_switch ? this._name(h.boiler_switch) : "Call for heat switch")}</span><span aria-hidden="true">→</span>
            <span class="node">Boiler</span></div>
          <div>
            <div class="kv"><span>Call for heat switch<span class="sub">${h.boiler_switch ? (swOk ? `On while a room is asking for heat, off otherwise${i.last_boiler_command ? ` · last: ${esc(i.last_boiler_command)}` : ""}` : "Unavailable: Hearth can't ask for heat until it's back") : "Any on/off switch that calls the boiler for heat: a relay, a thermostat's call-for-heat switch, a helper"}</span></span>
              <span class="pick">${this._entityField("boiler_switch", "switch,input_boolean", "Call for heat switch", h.boiler_switch)}</span></div>
            ${secRow("demand_on_delay", "Wait before heating", "So the boiler doesn't flick on as valves settle", 0, 900, "s", 1)}
            ${secRow("demand_off_delay", "Wait before stopping", "", 0, 1800, "s", 1)}
            ${h.boiler_switch ? `<div class="kv"><span>If Hearth stops<span class="sub">It turns ${esc(this._name(h.boiler_switch))} off when Hearth or Home Assistant stops or restarts. If Home Assistant crashes or loses power, the switch stays as it was: what happens next is up to what it drives.</span></span></div>` : ""}
          </div>
        </section>
        <section class="card stack">
          <div class="row between flexwrap"><h2>Rooms</h2><button class="btn solid" data-act="add">Add a room</button></div>
          ${i.rooms.length ? i.rooms.map((r) => `<div class="kv">
            <span>${esc(r.name)}<span class="sub">${r.kind === "radiator"
              ? `Radiator · ${esc(r.trvs.map((t) => this._name(t)).join(", "))} · ${r.sensor ? esc(this._name(r.sensor)) : "no room sensor (uses the valve's)"}`
              : `Underfloor · ${esc(this._name(r.stat))} · profile ${esc(r.profile || `Hearth ${r.name}`)}`}</span></span>
            <span class="row">${this._health(r)}<button class="btn" data-act="edit" data-id="${esc(r.id)}">Edit</button></span></div>`).join("")
            : `<span class="muted">No rooms yet.</span>`}
        </section>
      </div>
      <aside class="sidecol stack">
        <section class="card">
          <h2>Away and Holiday</h2>
          ${tempRow("away_temp", "Away", "Every room no warmer than", 5, 22)}
          ${tempRow("holiday_temp", "Holiday", "Every room kept above · the neoHub goes to its own away", 5, 18)}
          <div class="kv"><span>Set from<span class="sub">The Home / Away / Holiday buttons, or automations using ${esc(i.house_mode || "")}</span></span></div>
        </section>
        <section class="card">
          <h2>Every room</h2>
          ${tempRow("boost_temp", "Boost to", "", 15, 28)}
          ${secRow("boost_minutes", "Boost a radiator room for", "", 15, 240, "min", 1)}
          ${secRow("boost_minutes_underfloor", "Boost an underfloor room for", "A floor takes longer to answer", 30, 480, "min", 1)}
          ${tempRow("off_temp", "A radiator room that's off", "Kept above · an underfloor room that's off keeps its thermostat's own frost setting", 5, 15)}
          ${secRow("preheat_max_minutes", "Warm up early by up to", "Radiator rooms start early to reach a warmer change on time · 0 turns it off", 0, 360, "min", 1)}
        </section>
        <section class="card stack tight">
          <button class="btn solid wide" data-act="house-save" ${dirty && !this._busy ? "" : "disabled"}>${this._busy ? "Saving…" : "Save house settings"}</button>
          <button class="btn wide" data-act="house-discard" ${dirty ? "" : "disabled"}>Discard changes</button>
          <span class="muted small">Hearth ${esc(i.version)} · if Hearth stops, it turns the switch off. Each room says why it's doing what it does; diagnostics are under Settings → Devices & services → Hearth.</span>
        </section>
      </aside></div>`;
  }

  _health(room) {
    const problem = problemOf(this._state(room.entity_id));
    if (problem) return `<span class="small ${/offline/.test(problem) ? "bad" : "warntext"}">${esc(problem)}</span>`;
    const ids = room.kind === "radiator" ? [...room.trvs, room.sensor] : [room.stat, room.sensor];
    const down = ids.filter(Boolean).filter((id) => { const s = this._state(id); return !s || ["unavailable", "unknown"].includes(s.state); });
    if (down.length) return `<span class="small bad">${esc(down.map((d) => this._name(d)).join(", "))} offline</span>`;
    if (room.kind === "radiator" && !room.sensor) return `<span class="small warntext">Add a sensor?</span>`;
    return `<span class="small good">All online</span>`;
  }

  // Entity pickers ----------------------------------------------------------------
  _entityField(key, domain, label, value, deviceClass = "") {
    if (this._pickers) {
      return `<ha-entity-picker data-pick="${esc(key)}" data-domain="${domain}" data-class="${esc(deviceClass)}" data-label="${esc(label)}" data-value="${esc(value || "")}"></ha-entity-picker>`;
    }
    const ids = Object.keys(this._hass.states).filter((id) => domain.split(",").includes(id.split(".")[0]) && this._allowed(key, id)
      && (!deviceClass || this._hass.states[id].attributes.device_class === deviceClass)).sort();
    return `<select data-pick-select="${esc(key)}" aria-label="${esc(label)}"><option value="">None</option>${ids.map((id) =>
      `<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(this._name(id))}</option>`).join("")}</select>`;
  }

  // What may be picked: a valve isn't a Hearth room or a Heatmiser stat, a
  // stat is a Heatmiser one, and neither already heats another room.
  _allowed(key, id) {
    const valve = key.startsWith("d-trv");
    if (!valve && key !== "d-stat") return true;
    const platform = this._hass.entities?.[id]?.platform;
    if (platform === "hearth") return false;
    if (valve && platform === "heatmiserneo") return false;
    if (!valve && platform && platform !== "heatmiserneo") return false;
    const mine = this._dialog?.draft;
    if (valve && mine && mine.trvs.includes(id) && mine.trvs[Number(key.slice(5))] !== id) return false;
    return !this._info.rooms.some((r) => r.id !== mine?.id && (r.trvs.includes(id) || r.stat === id));
  }

  _wirePickers() {
    for (const picker of this.shadowRoot.querySelectorAll("ha-entity-picker[data-pick]")) {
      picker.hass = this._hass;
      const key = picker.dataset.pick;
      picker.entityFilter = (s) => this._allowed(key, s.entity_id);
      picker.includeDomains = picker.dataset.domain.split(",");
      if (picker.dataset.class) picker.includeDeviceClasses = [picker.dataset.class];
      picker.value = picker.dataset.value || "";
      picker.label = picker.dataset.label;
    }
  }

  _picked(e) {
    const key = e.target?.dataset?.pick;
    if (!key) return;
    e.stopPropagation();
    this._choose(key, e.detail?.value || "");
  }

  _choose(key, value) {
    if (this._dialog && key.startsWith("d-")) {
      const d = this._dialog.draft;
      if (key === "d-stat") d.stat = value || null;
      else if (key === "d-sensor") d.sensor = value || null;
      else if (key.startsWith("d-trv")) {
        const i = Number(key.slice(5));
        if (value) d.trvs[i] = value; else d.trvs.splice(i, 1);
        d.trvs = d.trvs.filter(Boolean);
      }
      this._render();
      return;
    }
    if (this._house && key === "boiler_switch") {
      this._house[key] = value || null;
      this._render();
    }
  }

  // Add / edit a room ---------------------------------------------------------------
  _openDialog(room) {
    this._dialog = {
      step: room ? 1 : 0,
      draft: room ? { id: room.id, name: room.name, kind: room.kind, trvs: [...room.trvs], sensor: room.sensor || null, stat: room.stat || null, profile: room.profile || "", warm_early: room.warm_early !== false }
        : { id: null, name: "", kind: "radiator", trvs: [], sensor: null, stat: null, profile: "", warm_early: true },
      error: "",
    };
    this._render();
  }

  // Temperature sensors in the same area as the room's devices.
  _suggestSensors(d) {
    const ents = this._hass.entities || {};
    const devs = this._hass.devices || {};
    const areaOf = (id) => { const e = ents[id]; return e?.area_id || (e?.device_id && devs[e.device_id]?.area_id) || null; };
    const areas = new Set((d.kind === "radiator" ? d.trvs : [d.stat]).filter(Boolean).map(areaOf).filter(Boolean));
    const name = (d.name || "").toLowerCase();
    return Object.values(this._hass.states).filter((s) => s.entity_id.startsWith("sensor.") && s.attributes.device_class === "temperature"
      && (areas.has(areaOf(s.entity_id)) || (name && (s.attributes.friendly_name || "").toLowerCase().includes(name))))
      .map((s) => s.entity_id).slice(0, 3);
  }

  _dialogHtml() {
    const { step, draft: d, error } = this._dialog;
    const editing = !!d.id;
    const steps = ["Room", d.kind === "radiator" ? "Valves" : "Stat", "Temperature"];
    let body = "";
    let canNext = true;
    if (step === 0) {
      canNext = !!d.name.trim();
      body = `<div class="stack tight"><label class="field">Name<input type="text" maxlength="40" value="${esc(d.name)}" data-dialog="name" placeholder="Landing"></label></div>
        <div class="stack tight" role="radiogroup" aria-label="How it's heated">
          <label class="opt ${d.kind === "radiator" ? "on" : ""}"><input type="radio" name="kind" value="radiator" ${d.kind === "radiator" ? "checked" : ""} data-dialog="kind">
            <span class="stack tight"><span>Radiator</span><span class="muted small">Smart valves on the radiators. Hearth runs the schedule and asks the boiler for heat.</span></span></label>
          <label class="opt ${d.kind === "underfloor" ? "on" : ""}"><input type="radio" name="kind" value="underfloor" ${d.kind === "underfloor" ? "checked" : ""} data-dialog="kind">
            <span class="stack tight"><span>Underfloor</span><span class="muted small">A Heatmiser stat. Hearth writes the schedule to it, and it carries on if Home Assistant is down.</span></span></label>
        </div>`;
    } else if (step === 1) {
      if (d.kind === "radiator") {
        canNext = d.trvs.length > 0;
        body = `<div class="stack tight"><h3>Which valves are in ${esc(d.name)}?</h3>
          <p class="muted">Sonoff TRVZBs or other valves from Zigbee2MQTT. Hearth takes over their schedule and puts them in plain heat mode.</p></div>
          <div class="stack tight">${[...d.trvs, ""].map((id, i) => this._entityField(`d-trv${i}`, "climate", i ? "Another valve" : "Valve", id)).join("")}</div>`;
      } else {
        canNext = !!d.stat;
        body = `<div class="stack tight"><h3>Which stat runs ${esc(d.name)}?</h3>
          <p class="muted">A neoStat from the Heatmiser integration.</p></div>
          ${this._entityField("d-stat", "climate", "Heatmiser stat", d.stat)}
          <label class="field">Profile name on the neoHub<input type="text" maxlength="40" value="${esc(d.profile)}" placeholder="Hearth ${esc(d.name)}" data-dialog="profile"></label>`;
      }
    } else {
      const suggested = this._suggestSensors(d).filter((id) => id !== d.sensor);
      const own = d.kind === "radiator" ? (d.trvs[0] ? this._attr(d.trvs[0], "current_temperature") : null) : this._attr(d.stat, "current_temperature");
      const chosen = d.sensor ? "sensor" : "own";
      body = `<div class="stack tight"><h3>Where should ${esc(d.name)}'s temperature come from?</h3>
        <p class="muted">${d.kind === "radiator" ? "Somewhere you'd stand, not by the radiator. Hearth sends it to the valve, so the valve stops reading its own hot spot." : "Optional: the stat's own reading is usually right for underfloor."}</p></div>
        <div class="stack tight" role="radiogroup" aria-label="Temperature source">
          ${suggested.map((id) => `<label class="opt"><input type="radio" name="src" data-dialog="sensor-pick" value="${esc(id)}">
            <span class="stack tight"><span>${esc(this._name(id))} <span class="good small">SUGGESTED</span></span><span class="muted small">${esc(id)} · ${deg(this._hass.states[id]?.state)}</span></span></label>`).join("")}
          <label class="opt ${chosen === "sensor" ? "on" : ""}"><input type="radio" name="src" ${chosen === "sensor" ? "checked" : ""} data-dialog="sensor-mode" value="sensor">
            <span class="stack tight grow"><span>A sensor</span>${this._entityField("d-sensor", "sensor", "Temperature sensor", d.sensor, "temperature")}</span></label>
          <label class="opt ${chosen === "own" ? "on" : ""}"><input type="radio" name="src" ${chosen === "own" ? "checked" : ""} data-dialog="sensor-mode" value="own">
            <span class="stack tight"><span>${d.kind === "radiator" ? "The valve's own reading" : "The stat's own reading"}</span><span class="muted small">${own !== null && own !== undefined ? `Reads ${deg(own)} now` : ""}${d.kind === "radiator" ? ": it sits on the radiator, so the room may run cool" : ""}</span></span></label>
        </div>
        ${d.kind === "radiator" ? `<label class="opt ${d.warm_early ? "on" : ""}"><input type="checkbox" ${d.warm_early ? "checked" : ""} data-dialog="warm_early">
          <span class="stack tight"><span>Start early for warmer changes</span><span class="muted small">So ${esc(d.name)} is already warm at a change's time. Turn it off for a room that shouldn't heat ahead of time, like a bedroom before a short morning slot.</span></span></label>` : ""}`;
    }
    const last = step === 2;
    return `<div class="scrim"><section class="dialog" role="dialog" aria-label="${editing ? "Change a room" : "Add a room"}">
      <div class="row between"><h2 class="dtitle">${editing ? `Change ${esc(d.name)}` : "Add a room"}</h2>
        <button class="icon" data-act="d-close" aria-label="Close"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
      <ol class="steps">${steps.map((label, i) => `<li class="${i < step ? "done" : i === step ? "on" : ""}" ${i === step ? 'aria-current="step"' : ""}>
        <button data-act="d-goto" data-step="${i}" ${i > step && !editing ? "disabled" : ""}><span class="num">${i < step ? "✓" : i + 1}</span>${i === 0 && d.name ? esc(d.name) : label}</button></li>`).join("")}</ol>
      ${body}
      ${error ? `<div class="bad small">${esc(error)}</div>` : ""}
      <div class="row between">
        <button class="btn" data-act="${step ? "d-back" : "d-close"}">${step ? "Back" : "Cancel"}</button>
        <button class="btn solid" data-act="${last ? "d-save" : "d-next"}" ${canNext && !this._busy ? "" : "disabled"}>${last ? (this._busy ? "Saving…" : editing ? "Save" : "Add room") : `Next: ${steps[step + 1].toLowerCase()}`}</button>
      </div></section></div>`;
  }

  _holidayHtml() {
    const house = this._state(this._info.house_mode);
    const on = house?.state === "holiday";
    const pad = (n) => String(n).padStart(2, "0");
    const d = new Date(Date.now() + 3600000);
    const min = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:00`;
    const until = this._holiday.until;
    return `<div class="scrim"><section class="dialog" role="dialog" aria-label="Holiday">
      <div class="row between"><h2 class="dtitle">${on ? "Change the holiday" : "Go on holiday"}</h2>
        <button class="icon" data-act="hol-close" aria-label="Close"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
      <p class="muted">Every room is kept above ${deg(this._info.settings.holiday_temp)}, whatever its mode or schedule, and any boost ends.</p>
      <label class="field">Back on (optional)<input type="datetime-local" min="${min}" value="${esc(until)}" data-holiday="until"></label>
      <span class="muted small">${until ? "The house goes back to Home then, and rooms pick up their schedules. Set it a few hours before you're home so it's warm when you arrive." : "Without a day to come back, the house stays on Holiday until someone switches it to Home."}</span>
      <div class="row between">
        <button class="btn" data-act="hol-close">Cancel</button>
        <button class="btn solid" data-act="hol-start">${on ? "Save" : "Start holiday"}</button>
      </div></section></div>`;
  }

  // Show each − / + at once, and send once the taps stop.
  _step(room, delta) {
    const held = this._steps[room.id];
    const from = held?.temp ?? num(this._attr(room.entity_id, "temperature")) ?? 19;
    const temp = clamp(from + delta);
    clearTimeout(held?.timer);
    const step = { temp };
    this._steps[room.id] = step;
    this._render();
    step.timer = setTimeout(async () => {
      await this._call("climate", "set_temperature", { entity_id: room.entity_id, temperature: temp });
      setTimeout(() => {
        if (this._steps[room.id] === step) { delete this._steps[room.id]; this._render(); }
      }, 1500);
    }, 600);
  }

  // Events ----------------------------------------------------------------------------
  _click(e) {
    const el = e.target.closest("[data-act]");
    if (!el || el.disabled) return;
    const act = el.dataset.act;
    const id = el.dataset.id;
    const room = id ? this._roomById(id) : null;
    switch (act) {
      case "menu": this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })); return;
      case "tab": this._tab = el.dataset.tab; this._room = null; this._render(); return;
      case "back": this._room = null; this._render(); return;
      case "room": this._room = id; this._render(); return;
      case "house": {
        const now = this._state(this._info.house_mode)?.state;
        if (el.dataset.mode === now && now !== "holiday") return;
        // Holiday drops every room at once, and can end by itself: ask first.
        if (el.dataset.mode === "holiday") { this._holiday = { until: "" }; this._render(); return; }
        this._call("select", "select_option", { entity_id: this._info.house_mode, option: el.dataset.mode });
        return;
      }
      case "hol-close": this._holiday = null; this._render(); return;
      case "hol-start": {
        const until = this._holiday.until;
        this._holiday = null;
        this._call("hearth", "set_holiday", until ? { until: `${until.replace("T", " ")}:00` } : {});
        this._render();
        return;
      }
      case "mode": this._call("climate", "set_hvac_mode", { entity_id: room.entity_id, hvac_mode: el.dataset.mode }); return;
      case "step": this._step(room, Number(el.dataset.step)); return;
      case "boost": {
        const boosting = this._attr(room.entity_id, "preset_mode") === "boost";
        this._call("hearth", boosting ? "cancel_boost" : "boost", { entity_id: room.entity_id });
        return;
      }
      case "edit-schedule":
        if (this._schedRoom !== id && this._editor.dirty && !confirm("Discard the unsaved changes to this schedule?")) return;
        this._schedRoom = id; this._tab = "schedules"; this._room = null; this._render(); return;
      case "sched-room":
        if (id === this._schedRoom) return;
        if (this._editor.dirty && !confirm("Discard the unsaved changes to this schedule?")) return;
        this._schedRoom = id; this._render(); return;
      case "edit": this._openDialog(room); return;
      case "add": this._openDialog(null); return;
      case "remove": this._removeRoom(room); return;
      case "house-save": this._saveHouse(); return;
      case "house-discard": this._house = null; this._render(); return;
      case "hist-range": this._histRange = Number(el.dataset.hours); this._render(); return;
      default: break;
    }
    if (act.startsWith("d-")) this._dialogAct(act, el);
  }

  _change(e) {
    const t = e.target;
    if (t.dataset.house) {
      const key = t.dataset.house;
      this._house[key] = Number(t.value) * Number(t.dataset.scale || 1);
      this._render();
      return;
    }
    if (t.dataset.holiday && this._holiday) { this._holiday.until = t.value; this._render(); return; }
    if (t.dataset.pickSelect) { this._choose(t.dataset.pickSelect, t.value); return; }
    if (t.dataset.dialog) this._dialogInput(t, true);
  }

  _input(e) {
    const t = e.target;
    if (t.dataset.dialog === "name" || t.dataset.dialog === "profile") this._dialogInput(t, false);
  }

  // House settings ---------------------------------------------------------------------------
  async _saveHouse() {
    const i = this._info;
    const changed = {};
    for (const [k, v] of Object.entries(this._house)) if (JSON.stringify(v) !== JSON.stringify(i.settings[k])) changed[k] = v === "" ? null : v;
    this._busy = true;
    this._render();
    try {
      await this._hass.callWS({ type: "hearth/save_house", settings: changed });
      this._house = null;
      this._say("Saved. Hearth is restarting its rooms.");
      await this._reload();
    } catch (err) {
      this._say(err?.message || String(err));
    }
    this._busy = false;
    this._render();
  }

  // Rooms: dialog -----------------------------------------------------------------------------
  _dialogInput(t, render) {
    const d = this._dialog?.draft;
    if (!d) return;
    const key = t.dataset.dialog;
    if (key === "name") d.name = t.value;
    else if (key === "profile") d.profile = t.value;
    else if (key === "kind") d.kind = t.value;
    else if (key === "sensor-pick") d.sensor = t.value;
    else if (key === "sensor-mode" && t.value === "own") d.sensor = null;
    else if (key === "warm_early") d.warm_early = t.checked;
    if (render) this._render();
    else {
      const next = this.shadowRoot.querySelector('[data-act="d-next"]');
      if (next && key === "name") next.disabled = !d.name.trim();
    }
  }

  _dialogAct(act, el) {
    const dlg = this._dialog;
    if (act === "d-close") { this._dialog = null; this._render(); return; }
    if (act === "d-back") { dlg.step = Math.max(0, dlg.step - 1); dlg.error = ""; this._render(); return; }
    if (act === "d-next") { dlg.step = Math.min(2, dlg.step + 1); dlg.error = ""; this._render(); return; }
    if (act === "d-goto") { dlg.step = Number(el.dataset.step); this._render(); return; }
    if (act === "d-save") this._saveRoom();
  }

  async _saveRoom() {
    const d = this._dialog.draft;
    this._busy = true;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "hearth/save_room",
        room: { id: d.id, name: d.name.trim(), kind: d.kind, trvs: d.kind === "radiator" ? d.trvs : [],
          sensor: d.sensor || null, stat: d.kind === "underfloor" ? d.stat : null, profile: d.kind === "underfloor" ? (d.profile || null) : null,
          warm_early: d.warm_early !== false },
      });
      const added = !d.id;
      this._dialog = null;
      this._say(added ? `${d.name} added. Its schedule is next.` : "Saved.");
      await this._reload();
      if (added) { this._schedRoom = res.id; this._tab = "schedules"; this._room = null; }
    } catch (err) {
      if (this._dialog) this._dialog.error = err?.message || String(err);
    }
    this._busy = false;
    this._render();
  }

  async _removeRoom(room) {
    if (!confirm(`Remove ${room.name} from Hearth? Its valves or stat are left as they are, and its schedule is forgotten.`)) return;
    try {
      await this._hass.callWS({ type: "hearth/remove_room", room_id: room.id });
      this._room = null;
      this._say(`${room.name} removed.`);
      await this._reload();
    } catch (err) {
      this._say(err?.message || String(err));
      this._render();
    }
  }
}

const STYLE = `
:host{display:block;height:100%;--hp-heat:var(--state-climate-heat-color,#ff8100);--hp-ok:var(--success-color,#43a047);--hp-warn:var(--warning-color,#ffa600);--hp-bad:var(--error-color,#db4437);--hp-off:#4a4a4a;
  --hp-card:var(--card-background-color,#1c1c1c);--hp-line:var(--divider-color,rgba(127,127,127,.25));color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,system-ui,sans-serif)}
*{box-sizing:border-box}
.page{height:100%;display:flex;flex-direction:column;background:var(--primary-background-color)}
.bar{flex:none;display:flex;align-items:center;gap:12px;min-height:56px;padding:4px 16px;background:var(--app-header-background-color,var(--hp-card));color:var(--app-header-text-color,var(--primary-text-color));border-bottom:1px solid var(--hp-line);flex-wrap:wrap}
.bar h1{margin:0;font-size:20px;font-weight:400}
.grow{flex:1 1 auto}
.tabs{flex:none;display:flex;gap:4px;padding:0 12px;background:var(--app-header-background-color,var(--hp-card));border-bottom:1px solid var(--hp-line);overflow-x:auto}
.tabs button{flex:none;height:48px;padding:0 18px;border:0;border-bottom:2px solid transparent;background:none;color:var(--secondary-text-color);font:inherit;font-size:14px;font-weight:500;cursor:pointer}
.tabs button.on{color:var(--primary-color);border-bottom-color:var(--primary-color)}
.content{flex:1 1 auto;overflow-y:auto}
.wrap{max-width:1240px;margin:0 auto;padding:24px;display:flex;flex-direction:column;gap:24px}
.wrap.split,.split{display:flex;flex-direction:row;flex-wrap:wrap;gap:24px;align-items:flex-start}
.main{flex:999 1 560px;min-width:0}
.side{flex:1 1 320px;display:flex;flex-direction:column}
.sidecol{flex:1 1 300px}
.stack{display:flex;flex-direction:column;gap:14px}
.stack.tight{gap:8px}
.row{display:flex;align-items:center;gap:8px}
.row.flexwrap{flex-wrap:wrap}
.row.between{justify-content:space-between}
.row.push,.push{margin-left:auto}
.card{background:var(--hp-card);border:1px solid var(--hp-line);border-radius:var(--ha-card-border-radius,12px);padding:20px}
.card.dashed{border-style:dashed}
.card.warn{border-color:var(--hp-warn)}
.card.error{border-color:var(--hp-bad)}
.card.empty{text-align:center;display:flex;flex-direction:column;align-items:center;gap:10px;padding:48px}
h2{margin:0;font-size:16px;font-weight:500}
h3{margin:0;font-size:17px;font-weight:500}
p{margin:0;line-height:1.45}
.muted{color:var(--secondary-text-color)}
.small{font-size:13px}
.mid{font-size:22px}
.caps{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--secondary-text-color)}
.chip{font-size:11px;font-weight:500;letter-spacing:.04em;text-transform:uppercase;border-radius:10px;padding:3px 8px;white-space:nowrap}
.chip.heat{color:var(--hp-heat);background:color-mix(in srgb,var(--hp-heat) 16%,transparent)}
.chip.ok{color:var(--hp-ok);background:color-mix(in srgb,var(--hp-ok) 16%,transparent)}
.chip.warn{color:var(--hp-warn);background:color-mix(in srgb,var(--hp-warn) 16%,transparent)}
.chip.blue{color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 16%,transparent)}
.chip.purple{color:#b07cd8;background:rgba(171,71,188,.16)}
.chip.grey{color:var(--secondary-text-color);background:rgba(127,127,127,.18)}
.good{color:var(--hp-ok)}.bad{color:var(--hp-bad)}.warntext{color:var(--hp-warn)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:36px;padding:0 16px;border-radius:18px;border:1px solid var(--hp-line);background:transparent;color:var(--primary-text-color);font:inherit;font-size:14px;font-weight:500;cursor:pointer}
.btn.solid{background:var(--primary-color);border-color:var(--primary-color);color:var(--text-primary-color,#fff)}
.btn.danger{color:var(--hp-bad)}
.btn.dashed{border-style:dashed}
.btn.wide{width:100%;min-height:44px;border-radius:22px}
.btn:disabled{opacity:.45;cursor:default}
.icon{width:44px;height:44px;display:inline-flex;align-items:center;justify-content:center;border:0;background:none;color:inherit;cursor:pointer;border-radius:22px}
.icon:disabled{opacity:.35}
.seg{display:inline-flex;padding:3px;gap:2px;border:1px solid var(--hp-line);border-radius:20px;flex-wrap:wrap}
.seg button{min-height:34px;padding:0 16px;border:0;border-radius:17px;background:transparent;color:var(--secondary-text-color);font:inherit;font-size:14px;cursor:pointer}
.seg button.on{background:color-mix(in srgb,var(--primary-color) 20%,transparent);color:var(--primary-color);font-weight:500}
.pills{display:flex;gap:8px;flex-wrap:wrap}
.pill{flex:none;min-height:40px;padding:0 18px;border-radius:22px;border:1px solid var(--hp-line);background:var(--hp-card);color:var(--primary-text-color);font:inherit;font-size:14px;cursor:pointer}
.pill.on{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 16%,transparent);color:var(--primary-color);font-weight:500}
.status{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.status-card{display:flex;flex-direction:column;gap:8px}
.status-card.hot{border-color:color-mix(in srgb,var(--hp-heat) 55%,transparent)}
.big{font-size:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:16px}
.room{display:flex;flex-direction:column;gap:10px;text-align:left;color:inherit;font:inherit;cursor:pointer;padding:16px}
.room:hover{border-color:var(--primary-color)}
.rname{display:flex;align-items:center;gap:8px;font-size:15px;font-weight:500}
.temps{display:flex;align-items:baseline;gap:10px}
.now{font-size:40px;font-weight:300;letter-spacing:-1px}
.huge{font-size:64px;font-weight:300;letter-spacing:-2px}
.why{font-size:14px;color:var(--secondary-text-color)}
.why.hot{color:var(--hp-heat)}
.scrollx{overflow-x:auto}
.today{display:flex;flex-direction:column;gap:10px;min-width:620px}
.trow{display:flex;align-items:center;gap:16px}
.tname{width:150px;flex:none;font-size:14px}
.strip{position:relative;flex:1 1 auto;height:30px;display:flex;border-radius:8px;overflow:hidden}
.strip.tall{height:64px;border-radius:10px}
.strip i{position:absolute;top:0;bottom:0;width:2px;background:var(--primary-text-color)}
.block{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:#1b1b1b;font-size:12px;font-weight:500;border-right:1px solid var(--hp-card);overflow:hidden;white-space:nowrap}
.strip.tall .block{font-size:16px;border-right-width:2px}
.block .span{font-size:11px;font-weight:400;opacity:.9}
.ticks{flex:1 1 auto;display:flex;justify-content:space-between;font-size:12px;color:var(--secondary-text-color)}
.nowcard{display:flex;flex-wrap:wrap;gap:28px;align-items:center}
.controls{display:flex;flex-direction:column;align-items:center;gap:12px}
.round{width:52px;height:52px;border-radius:26px;border:1px solid var(--hp-line);background:transparent;color:var(--primary-text-color);font-size:24px;cursor:pointer}
.round:disabled{opacity:.35;cursor:default}
.round.sm{width:44px;height:44px;font-size:20px}
.target{text-align:center;min-width:96px}
.tbig{font-size:40px;font-weight:300}
.tval{min-width:60px;text-align:center;font-size:22px;font-weight:300}
.htitle{font-size:20px;font-weight:400}
.hbig{font-size:28px;font-weight:300}
.hkey{display:flex;flex-wrap:wrap;align-items:center;gap:8px 22px;color:var(--secondary-text-color)}
.hkey span{display:inline-flex;align-items:center;gap:8px}
.hkey .b{width:22px;height:12px;border-radius:3px;border:1px solid var(--hp-line)}
.hroom{display:flex;flex-wrap:wrap;gap:18px;padding:16px 18px}
.hinfo{flex:1 1 190px;max-width:230px;display:flex;flex-direction:column;gap:6px;min-width:0}
.hname{display:flex;align-items:center;gap:8px;min-height:32px;padding:0;border:0;background:none;color:var(--primary-text-color);font:inherit;font-size:16px;font-weight:500;cursor:pointer;text-align:left}
.hname:hover{color:var(--primary-color)}
.hname svg{color:var(--secondary-text-color);flex:none}
.hnow{font-size:30px;font-weight:300;letter-spacing:-1px}
.hhot{color:var(--hp-heat)}
.tiny{font-size:12px}
.hplot{flex:999 1 520px;min-width:0;display:flex;gap:8px}
.haxis{width:30px;flex:none;height:150px;display:flex;flex-direction:column;justify-content:space-between;font-size:11px;color:var(--secondary-text-color);text-align:right;margin-top:-6px;padding-bottom:0}
.haxis span{line-height:12px}
.hcol{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;gap:6px}
.hchart{position:relative;height:150px;touch-action:pan-y;cursor:crosshair}
.hchart svg{position:absolute;inset:0;width:100%;height:100%;display:block;overflow:visible}
.hwash,.hband{display:flex}
.hwash{position:absolute;inset:0}
.hband{height:14px;border-radius:4px;overflow:hidden;background:rgba(127,127,127,.12)}
.hband .b{border-right:1px solid var(--hp-card)}
.hgrid{fill:none;stroke:var(--hp-line);stroke-width:1;vector-effect:non-scaling-stroke}
.hline{fill:none;stroke:var(--primary-color);stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}
.htarget{fill:none;stroke:var(--secondary-text-color);stroke-width:2;stroke-dasharray:3 4;vector-effect:non-scaling-stroke}
.b.s-heat{background:var(--hp-heat)}
.b.s-pre{background:repeating-linear-gradient(135deg,color-mix(in srgb,var(--hp-heat) 60%,#fff) 0 3px,color-mix(in srgb,var(--hp-heat) 40%,#000) 3px 6px)}
.b.s-off{background:var(--hp-off)}
.b.s-idle,.b.s-gap{background:transparent}
.w.s-heat{background:color-mix(in srgb,var(--hp-heat) 11%,transparent)}
.w.s-pre{background:color-mix(in srgb,var(--hp-heat) 7%,transparent)}
.w.s-off{background:rgba(127,127,127,.07)}
.hdot{position:absolute;right:-4px;width:9px;height:9px;border-radius:5px;background:var(--primary-color);transform:translateY(-50%);pointer-events:none}
.hcursor{position:absolute;top:0;bottom:0;width:1px;background:var(--primary-text-color);opacity:.6;pointer-events:none}
.htip{position:absolute;top:-6px;padding:6px 10px;border-radius:8px;background:var(--hp-card);border:1px solid var(--hp-line);box-shadow:0 2px 8px rgba(0,0,0,.3);font-size:12px;line-height:1.5;white-space:nowrap;pointer-events:none;z-index:1}
.htip[hidden],.hcursor[hidden]{display:none}
.hticks{position:relative;height:16px;font-size:11px;color:var(--secondary-text-color)}
.hticks span{position:absolute;transform:translateX(-50%);white-space:nowrap}
.hticks .day{color:var(--primary-text-color)}
.hticks .nowtick{transform:translateX(-100%);color:var(--primary-color)}
.kv{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--hp-line);font-size:14px}
.kv label,.kv>span:first-child{flex:1 1 auto}
.kv>span:last-child:not(:first-child):not([class]){flex:none;white-space:nowrap}
.sub{display:block;font-size:12px;color:var(--secondary-text-color);margin-top:2px;line-height:1.4}
.pick{flex:0 1 300px;min-width:200px}
.pick ha-entity-picker,.pick select{width:100%}
select,input[type=number],input[type=text],input[type=time],input[type=datetime-local]{min-height:40px;padding:6px 10px;border:1px solid var(--hp-line);border-radius:8px;background:var(--primary-background-color);color:var(--primary-text-color);font:inherit;font-size:14px}
input[type=time],input[type=datetime-local]{color-scheme:light dark;font-size:15px}
.unit{display:inline-flex;align-items:center;gap:6px}
.unit input{width:84px;text-align:right}
.switch{width:44px;height:24px;border-radius:12px;border:0;padding:0;background:rgba(127,127,127,.5);position:relative;cursor:pointer;flex:none}
.switch span{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:9px;background:#fff;transition:left .15s}
.switch.on{background:var(--primary-color)}
.switch.on span{left:23px}
details{font-size:14px}
summary{cursor:pointer;min-height:36px;display:flex;align-items:center;color:var(--secondary-text-color)}
.flow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13px;color:var(--secondary-text-color)}
.node{border:1px solid var(--hp-line);border-radius:10px;padding:8px 12px;color:var(--primary-text-color)}
.change{display:flex;flex-wrap:wrap;align-items:center;gap:14px;padding:8px 12px;border-radius:10px;border:1px solid var(--hp-line)}
.change label{display:flex;align-items:center;gap:10px;font-size:14px}
.swatch{width:12px;height:12px;border-radius:3px;flex:none}
.dot{width:10px;height:10px;border-radius:5px;flex:none}
.chart svg{width:100%;height:auto;display:block}
.chart .grid line{stroke:var(--hp-line)}
.chart text{fill:var(--secondary-text-color);font-size:12px;font-family:inherit}
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px;z-index:5}
.dialog{width:640px;max-width:100%;max-height:calc(100% - 32px);overflow-y:auto;background:var(--hp-card);border:1px solid var(--hp-line);border-radius:24px;padding:28px;display:flex;flex-direction:column;gap:20px;box-shadow:0 24px 80px rgba(0,0,0,.5)}
.dtitle{font-size:22px;font-weight:400}
.steps{list-style:none;margin:0;padding:0;display:flex;gap:14px;flex-wrap:wrap}
.steps button{display:flex;align-items:center;gap:8px;border:0;background:none;color:var(--secondary-text-color);font:inherit;font-size:13px;cursor:pointer;min-height:32px;padding:0}
.steps button:disabled{cursor:default}
.steps .on button{color:var(--primary-text-color)}
.num{width:24px;height:24px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;font-size:12px;font-weight:500;background:rgba(127,127,127,.2)}
.steps .on .num{background:var(--primary-color);color:var(--text-primary-color,#fff)}
.steps .done .num{background:color-mix(in srgb,var(--hp-ok) 25%,transparent);color:var(--hp-ok)}
.opt{display:flex;gap:14px;align-items:flex-start;padding:14px 16px;border:1px solid var(--hp-line);border-radius:12px;cursor:pointer}
.opt.on{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 8%,transparent)}
.opt input{margin:3px 0 0;width:18px;height:18px;flex:none;accent-color:var(--primary-color)}
.field{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--secondary-text-color)}
.field input{font-size:15px}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--primary-text-color);color:var(--primary-background-color);padding:12px 20px;border-radius:8px;font-size:14px;z-index:6;max-width:calc(100% - 32px)}
@media (max-width:600px){.wrap>.status{order:2}.wrap>.status~section:last-child{order:3}.wrap{padding:16px}.pills{flex-wrap:nowrap;overflow-x:auto;margin:0 -16px;padding:0 16px;scrollbar-width:none}.huge{font-size:52px}.tname{width:110px}}
`;

if (!customElements.get("hearth-panel")) {
  customElements.define("hearth-panel", HearthPanel);
  console.info(`%c HEARTH PANEL %c ${VERSION} `, "background:#ff8100;color:#fff", "");
}
