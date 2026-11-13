// Hearth's panel in Home Assistant's sidebar: every room, one schedule
// editor for all of them, and the house settings. Plain custom element, no
// build step. Rooms and house settings go through the hearth/* websocket
// commands (panel.py); schedules, modes, boosts and Home/Away/Holiday through
// Hearth's own actions and entities, the same ones the cards use.

const VERSION = "0.3.2";
const MAX_CHANGES = 6;
const DAYS = [["weekdays", "Weekdays · Mon–Fri"], ["weekends", "Weekends · Sat–Sun"]];
const HOUSE = [["home", "Home"], ["away", "Away"], ["holiday", "Holiday"]];
const MODES = [["auto", "Schedule"], ["heat", "Fixed"], ["off", "Off"]];
const ICON_RAD = "M4 6v12M8 6v12M12 6v12M16 6v12M20 6v12M3 18h18";
const ICON_UFH = "M3 20h18M7 4c-1.5 2 1.5 4 0 6s1.5 4 0 6M12 4c-1.5 2 1.5 4 0 6s1.5 4 0 6M17 4c-1.5 2 1.5 4 0 6s1.5 4 0 6";
const ICON_FLAME = "M12 3c2 3 5 5 5 9a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3 0-3-1-5 1-8z";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
const deg = (t) => (num(t) === null ? "–" : `${Number(t).toFixed(1).replace(/\.0$/, "")}°`);
const mins = (hhmm) => { const [h, m] = String(hhmm).split(":").map(Number); return h * 60 + m; };
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const clamp = (t) => Math.min(28, Math.max(5, Math.round(t * 2) / 2));
const todayType = () => ([0, 6].includes(new Date().getDay()) ? "weekends" : "weekdays");
const copy = (o) => JSON.parse(JSON.stringify(o));
const tempColour = (t) => (t === null ? "var(--hp-off)" : t >= 21 ? "#a3562a" : t >= 19 ? "#3d5f8f" : t >= 18 ? "#33496a" : "#283244");

class HearthPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "rooms";
    this._room = null; // a room's page, by id
    this._sched = { zone: null, day: todayType(), draft: null, dirty: false };
    this._dialog = null; // add / edit a room
    this._house = null; // the House tab's unsaved settings
    this._history = {};
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
    const lifted = () => setTimeout(() => {
      this._pressing = false;
      if (this._pending && !this._typing() && !this._dialog) this._render();
    }, 50);
    window.addEventListener("pointerup", lifted);
    window.addEventListener("pointercancel", lifted);
    root.addEventListener("click", (e) => this._click(e));
    root.addEventListener("change", (e) => this._change(e));
    root.addEventListener("input", (e) => this._input(e));
    root.addEventListener("value-changed", (e) => this._picked(e));
    root.addEventListener("focusout", () => setTimeout(() => this._pending && !this._typing() && this._render(), 0));
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
    if (!this._info) return;
    const sig = this._watched().map((id) => hass.states[id]?.last_updated).join("|");
    if (sig === this._sig) return;
    this._sig = sig;
    if (this._typing() || this._dialog || this._pressing) { this._pending = true; return; }
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
    return !!el && ["INPUT", "SELECT", "TEXTAREA", "HA-ENTITY-PICKER"].includes(el.tagName);
  }

  _watched() {
    const i = this._info;
    return [i.house_mode, i.demand, i.settings.boiler, i.settings.boiler_switch,
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
      if (!this._sched.zone && this._info.rooms.length) this._sched.zone = this._info.rooms[0].id;
      if (this._sched.zone && !this._sched.dirty) this._sched.draft = null;
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

  _scheduleOf(room) {
    return this._attr(room.entity_id, "schedule") || room.schedule || { weekdays: [{ time: "00:00", temp: 19 }], weekends: [{ time: "00:00", temp: 19 }] };
  }

  _todayBlocks(room) {
    const st = this._state(room.entity_id);
    const mode = st?.attributes?.mode;
    if (mode === "off") return [{ start: 0, end: 1440, temp: null }];
    if (mode === "fixed") return [{ start: 0, end: 1440, temp: num(st.attributes.temperature) }];
    return this._blocks(this._scheduleOf(room)[todayType()] || []);
  }

  _blocks(list) {
    const day = [...list].sort((a, b) => mins(a.time) - mins(b.time));
    if (!day.length) return [];
    const out = [];
    const last = day[day.length - 1].temp;
    if (mins(day[0].time) > 0) out.push({ start: 0, end: mins(day[0].time), temp: last });
    day.forEach((c, i) => out.push({ start: mins(c.time), end: i + 1 < day.length ? mins(day[i + 1].time) : 1440, temp: c.temp }));
    return out.filter((b) => b.end > b.start);
  }

  _bar(blocks, tall) {
    return blocks.map((b) => {
      const pct = ((b.end - b.start) / 1440) * 100;
      const label = pct >= (tall ? 5 : 6) ? (b.temp === null ? "Off" : deg(b.temp)) : "";
      const span = tall && pct >= 11 ? `<span class="span">${hhmm(b.start)}–${hhmm(b.end)}</span>` : "";
      return `<div class="block" style="width:${pct.toFixed(3)}%;background:${tempColour(b.temp)}"><span>${label}</span>${span}</div>`;
    }).join("");
  }

  _nowPct() {
    const d = new Date();
    return ((d.getHours() * 60 + d.getMinutes()) / 1440) * 100;
  }

  _heating(room) {
    const st = this._state(room.entity_id);
    return st?.attributes?.hvac_action === "heating" || st?.attributes?.calling_for_heat;
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
    else body = this._roomsTab();
    root.innerHTML = `<style>${STYLE}</style><div class="page">${this._header()}<div class="content">${
      this._info.loaded ? "" : `<div class="wrap"><div class="card warn">Hearth isn't running. Check Settings → Devices & services.</div></div>`}${body}</div>${
      this._dialog ? this._dialogHtml() : ""}${this._toast ? `<div class="toast" role="status">${esc(this._toast)}</div>` : ""}</div>`;
    const content = root.querySelector(".content");
    if (content) content.scrollTop = scroll;
    this._wirePickers();
    if (this._room) this._drawHistory(this._roomById(this._room));
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
          <h1>${esc(room.name)}</h1>${this._heating(room) ? `<span class="chip heat">Heating</span>` : ""}
          <span class="grow"></span>
          <button class="btn" data-act="boost" data-id="${esc(room.id)}">${boosting ? "End boost" : `Boost ${esc(this._boostLabel())}`}</button>
        </header>`;
      }
    }
    const mode = this._state(this._info.house_mode)?.state;
    const tabs = [["rooms", "Rooms"], ["schedules", "Schedules"], ["house", "House"]];
    return `<header class="bar">${menu}
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="var(--hp-heat)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${ICON_FLAME}"/></svg>
        <h1>Heating</h1><span class="grow"></span>
        ${this._info.house_mode ? `<div class="seg" role="group" aria-label="House mode">${HOUSE.map(([k, label]) =>
          `<button data-act="house" data-mode="${k}" aria-pressed="${mode === k}" class="${mode === k ? "on" : ""}">${label}</button>`).join("")}</div>` : ""}
      </header>
      <nav class="tabs" aria-label="Hearth sections">${tabs.map(([k, label]) =>
        `<button data-act="tab" data-tab="${k}" class="${this._tab === k ? "on" : ""}" ${this._tab === k ? 'aria-current="page"' : ""}>${label}</button>`).join("")}</nav>`;
  }

  _boostLabel() {
    const m = Number(this._info.settings.boost_minutes) || 60;
    return m % 60 === 0 ? `${m / 60} h` : `${m} min`;
  }

  // Rooms ---------------------------------------------------------------------
  _roomsTab() {
    const rooms = this._info.rooms;
    if (!rooms.length) {
      return `<div class="wrap"><div class="card empty"><h2>No rooms yet</h2>
        <p class="muted">Add each room once, radiator or underfloor, and Hearth runs them all the same way.</p>
        <button class="btn solid" data-act="add">Add a room</button></div></div>`;
    }
    const now = this._nowPct();
    return `<div class="wrap">
      <section class="status">${this._statusCards()}</section>
      <section class="stack">
        <div class="row between"><h2>Rooms</h2><button class="btn solid" data-act="add">Add a room</button></div>
        <div class="grid">${rooms.map((r) => this._roomCard(r)).join("")}</div>
      </section>
      <section class="card stack">
        <div class="row between"><h2>Today · ${new Date().toLocaleDateString(undefined, { weekday: "long" })}</h2>
          <span class="muted small">Now ${new Date().toTimeString().slice(0, 5)} · the line</span></div>
        <div class="scrollx"><div class="today">
          ${rooms.map((r) => `<div class="trow"><span class="tname">${esc(r.name)}</span>
            <div class="strip">${this._bar(this._todayBlocks(r))}<i style="left:${now.toFixed(2)}%"></i></div></div>`).join("")}
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
      const boiler = this._state(i.settings.boiler);
      const firing = boiler?.attributes?.hvac_action === "heating";
      let how;
      if (i.settings.boiler_switch) how = sw && ["on", "off"].includes(sw.state)
        ? `${esc(this._name(i.settings.boiler_switch))} is ${sw.state}`
        : `${esc(this._name(i.settings.boiler_switch))} is unavailable, so the boiler can't be asked`;
      else if (!i.settings.boiler) how = "Nothing switches the boiler yet: choose a switch or thermostat on the House tab";
      else how = `Setting ${esc(boiler?.attributes?.friendly_name || "the thermostat")} to ${deg(i.settings.boiler_on_temp)} / ${deg(i.settings.boiler_off_temp)}${firing ? " · boiler firing" : ""}`;
      cards.push(`<div class="card status-card ${on ? "hot" : ""}">
        <div class="row between"><span class="caps">Radiators</span><span class="chip ${on ? "heat" : "ok"}">${on ? "Calling" : "Idle"}</span></div>
        <div class="big">${on ? `${esc(asking.join(", ") || "A room")} ${asking.length > 1 ? "want" : "wants"} heat` : "No room wants heat"}</div>
        <div class="muted small">${how}</div></div>`);
    }
    if (ufh.length) {
      const heating = ufh.filter((r) => this._heating(r)).map((r) => r.name);
      cards.push(`<div class="card status-card">
        <div class="row between"><span class="caps">Underfloor</span><span class="chip grey">On its stats</span></div>
        <div class="big">${heating.length ? `${esc(heating.join(", "))} heating` : ufh.length > 1 ? "All comfortable" : "Comfortable"}</div>
        <div class="muted small">The neoHub runs ${esc(ufh.map((r) => r.name).join(", "))} from the profiles Hearth wrote</div></div>`);
    }
    if (rads.length && (i.settings.boiler_switch || i.settings.boiler)) {
      const big = i.settings.boiler_switch ? "It stops asking for heat" : `Thermostat left at ${deg(i.settings.fallback_temp)}`;
      cards.push(`<div class="card status-card">
        <div class="row between"><span class="caps">If Hearth stops</span><span class="chip ok">Covered</span></div>
        <div class="big">${big}</div>
        <div class="muted small">${i.settings.boiler_switch ? `By turning ${esc(this._name(i.settings.boiler_switch))} off · what happens next is up to what it drives` : "So the house can't go cold"}${ufh.length ? " · the stats carry on as they are" : ""}</div></div>`);
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
      <div class="temps"><span class="now">${deg(a.current_temperature)}</span><span class="muted">→ ${deg(a.temperature)}</span></div>
      <div class="why ${heating ? "hot" : ""}">${heating ? "Heating · " : ""}${esc(a.reason || "")}</div>
      <div class="muted small">${devices}</div></button>`;
  }

  _name(id) {
    return this._hass.states[id]?.attributes?.friendly_name || id;
  }

  // A room's page -------------------------------------------------------------
  _roomPage(room) {
    const st = this._state(room.entity_id);
    const a = st?.attributes || {};
    const heating = this._heating(room);
    const until = a.next_change_at && st?.state === "auto" ? ` until ${new Date(a.next_change_at).toTimeString().slice(0, 5)}` : st?.state === "heat" ? " fixed" : "";
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
    return `<div class="wrap split">
      <div class="main stack">
        <section class="card nowcard">
          <div class="stack tight grow">
            <span class="caps">Now</span>
            <div class="temps"><span class="huge">${deg(a.current_temperature)}</span><span class="muted mid">→ ${deg(a.temperature)}</span></div>
            <span class="why ${heating ? "hot" : ""}" style="font-size:17px">${esc(a.reason || "")}</span>
            <span class="muted small">${room.kind === "radiator" ? (heating ? "Asking for heat" : "Not asking for heat") : heating ? "The stat is heating" : "The stat is idle"}</span>
          </div>
          <div class="controls">
            <div class="row" style="gap:18px">
              <button class="round" data-act="step" data-id="${esc(room.id)}" data-step="-0.5" aria-label="Cooler">−</button>
              <div class="target"><div class="tbig">${deg(this._steps[room.id]?.temp ?? a.temperature)}</div><div class="muted small">${esc(until.trim())}</div></div>
              <button class="round" data-act="step" data-id="${esc(room.id)}" data-step="0.5" aria-label="Warmer">+</button>
            </div>
            <div class="seg" role="group" aria-label="Mode">${MODES.map(([k, label]) =>
              `<button data-act="mode" data-id="${esc(room.id)}" data-mode="${k}" class="${st?.state === k ? "on" : ""}" aria-pressed="${st?.state === k}">${label}</button>`).join("")}</div>
          </div>
        </section>
        <section class="card stack">
          <div class="row between"><h2>Today</h2><span class="muted small">Line: room · dashes: target · orange: heating</span></div>
          <div class="chart" data-chart="${esc(room.id)}"><div class="muted small">Loading today's readings…</div></div>
        </section>
        <section class="card stack dashed">
          <div class="row between"><h2>Pre-heat</h2><span class="chip grey">Later</span></div>
          <span class="muted">Once Hearth learns how fast ${esc(room.name)} warms, this shows its time to temperature and starts early so each change is reached on time, not started then.</span>
        </section>
      </div>
      <aside class="card side">
        <h2>${esc(room.name)}</h2>
        <div class="kv"><span>Heated by<span class="sub">${room.kind === "radiator" ? "Valves, with the boiler asked through the House tab" : `Its Heatmiser stat, running the “${esc(room.profile || `Hearth ${room.name}`)}” profile`}</span></span><span>${room.kind === "radiator" ? "Radiator" : "Underfloor"}</span></div>
        ${devRows}
        <div class="kv"><span>Room temperature<span class="sub">${room.sensor ? esc(this._name(room.sensor)) + (room.kind === "radiator" ? ", fed to the valve" : "") : room.kind === "radiator" ? "The valve's own: it sits on the radiator and reads high" : "The stat's own"}</span></span><span>${sensor ? deg(sensor.state) : deg(a.current_temperature)}</span></div>
        ${room.kind === "radiator" ? `<div class="kv"><span>Turning the valve by hand<span class="sub">Counts the same as − or +</span></span><span>On</span></div>` : ""}
        <div class="kv"><span>Away · Holiday<span class="sub">Set on the House tab</span></span><span>${deg(this._info.settings.away_temp)} · ${deg(this._info.settings.holiday_temp)}</span></div>
        <div class="row flexwrap" style="padding-top:12px">
          <button class="btn" data-act="edit-schedule" data-id="${esc(room.id)}">Edit schedule</button>
          <button class="btn" data-act="edit" data-id="${esc(room.id)}">Change devices</button>
          <button class="btn danger" data-act="remove" data-id="${esc(room.id)}">Remove</button>
        </div>
        <span class="muted small" style="padding-top:12px">${esc(room.entity_id || "")} · in Home Assistant its modes are Auto (Schedule), Heat (Fixed) and Off.</span>
      </aside></div>`;
  }

  async _drawHistory(room) {
    const host = this.shadowRoot.querySelector(`[data-chart="${CSS.escape(room.id)}"]`);
    if (!host || !room.entity_id) return;
    const cached = this._history[room.id];
    if (cached && Date.now() - cached.at < 120000) { host.innerHTML = cached.svg; return; }
    const start = new Date(); start.setHours(0, 0, 0, 0);
    try {
      const res = await this._hass.callWS({
        type: "history/history_during_period", start_time: start.toISOString(), entity_ids: [room.entity_id],
        minimal_response: false, no_attributes: false, significant_changes_only: false,
      });
      const rows = (res[room.entity_id] || []).map((r) => ({ t: (r.lu ?? r.lc) * 1000, a: r.a || {} }));
      const svg = this._chart(rows, start.getTime());
      this._history[room.id] = { at: Date.now(), svg };
      const now = this.shadowRoot.querySelector(`[data-chart="${CSS.escape(room.id)}"]`);
      if (now) now.innerHTML = svg;
    } catch (err) {
      host.innerHTML = `<div class="muted small">Today's readings couldn't be loaded.</div>`;
    }
  }

  _chart(rows, dayStart) {
    // Attributes carry forward between rows (each row lists only what changed).
    let cur = {};
    const pts = rows.map((r) => { cur = { ...cur, ...r.a }; return { t: r.t, room: num(cur.current_temperature), target: num(cur.temperature), heat: cur.hvac_action === "heating" }; })
      .filter((p) => p.t >= dayStart);
    if (!pts.length) return `<div class="muted small">No readings yet today.</div>`;
    const now = Date.now();
    const vals = pts.flatMap((p) => [p.room, p.target]).filter((v) => v !== null);
    let lo = Math.floor(Math.min(...vals) - 1), hi = Math.ceil(Math.max(...vals) + 1);
    if (hi - lo < 4) { hi = lo + 4; }
    const W = 1000, H = 230, L = 40, T = 20, B = 200;
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

  // Schedules -------------------------------------------------------------------
  _schedulesTab() {
    const rooms = this._info.rooms;
    if (!rooms.length) return this._roomsTab();
    const s = this._sched;
    if (!s.zone || !this._roomById(s.zone)) s.zone = rooms[0].id;
    const room = this._roomById(s.zone);
    if (!s.draft) s.draft = copy(this._scheduleOf(room));
    const list = [...(s.draft[s.day] || [])].sort((a, b) => mins(a.time) - mins(b.time));
    const n = list.length;
    const other = s.day === "weekdays" ? "weekends" : "weekdays";
    const st = this._state(room.entity_id);
    const mode = { auto: "Schedule", heat: `Fixed ${deg(st?.attributes?.temperature)}`, off: "Off" }[st?.state] || "unavailable";
    const ufh = room.kind === "underfloor";
    return `<div class="wrap stack">
      <div class="pills" role="group" aria-label="Room">${rooms.map((r) =>
        `<button class="pill ${r.id === s.zone ? "on" : ""}" data-act="sched-room" data-id="${esc(r.id)}" aria-pressed="${r.id === s.zone}">${esc(r.name)}</button>`).join("")}</div>
      <div class="split">
        <section class="card main stack">
          <div class="row between flexwrap">
            <div class="seg" role="group" aria-label="Days">${DAYS.map(([k, label]) =>
              `<button data-act="sched-day" data-day="${k}" class="${s.day === k ? "on" : ""}" aria-pressed="${s.day === k}">${label}</button>`).join("")}</div>
            <span class="small ${n >= MAX_CHANGES ? "warntext" : "muted"}">${n} of ${MAX_CHANGES} changes</span>
          </div>
          <div class="stack tight"><div class="strip tall">${this._bar(this._blocks(list), true)}</div>
            <div class="ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div></div>
          <div class="stack tight">${list.map((c, i) => `<div class="change">
            <span class="swatch" style="background:${tempColour(c.temp)}"></span>
            <label class="muted">From <input type="time" value="${esc(c.time)}" data-sched-time="${i}"></label>
            <div class="row push">
              <button class="round sm" data-act="sched-temp" data-i="${i}" data-step="-0.5" aria-label="Cooler">−</button>
              <span class="tval">${deg(c.temp)}</span>
              <button class="round sm" data-act="sched-temp" data-i="${i}" data-step="0.5" aria-label="Warmer">+</button>
              <button class="icon" data-act="sched-del" data-i="${i}" aria-label="Remove this change" ${n <= 1 ? "disabled" : ""}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
            </div></div>`).join("")}</div>
          <div class="row flexwrap">
            <button class="btn dashed" data-act="sched-add" ${n >= MAX_CHANGES ? "disabled" : ""}>+ Add a change</button>
            <button class="btn" data-act="sched-copyday">Copy to ${other}</button>
            ${rooms.length > 1 ? `<button class="btn" data-act="sched-copyrooms">Copy to other rooms…</button>` : ""}
          </div>
        </section>
        <aside class="sidecol stack">
          <div class="card stack tight">
            <div class="row"><span class="dot" style="background:${s.dirty ? "var(--hp-warn)" : "var(--hp-ok)"}"></span><b>${s.dirty ? "Not saved yet" : ufh ? "On the stat" : "Running"}</b></div>
            <span class="muted">${s.dirty ? (ufh ? "The stat keeps its last profile until you save." : "The valves keep following the last saved schedule until you save.")
              : ufh ? `Saved in Home Assistant and written to the neoHub as the “${esc(room.profile || `Hearth ${room.name}`)}” profile. The stat runs it with its own early start, even if Home Assistant is down.`
              : `Hearth sets ${esc(room.name)}'s valve${room.trvs.length === 1 ? "" : "s"} at each change and asks for heat when the room is below target.`}</span>
            <button class="btn solid wide" data-act="sched-save" ${s.dirty && !this._busy ? "" : "disabled"}>${this._busy ? "Saving…" : "Save"}</button>
            <button class="btn wide" data-act="sched-discard" ${s.dirty ? "" : "disabled"}>Discard changes</button>
          </div>
          <div class="card stack tight"><b>Same rules in every room</b>
            <span class="muted">Weekdays and weekends, up to six changes a day: the neoHub's limit, kept everywhere so a schedule can move between rooms. A change to the temperature already in force is merged away.</span></div>
          <div class="card stack tight"><b>${esc(room.name)} is on ${esc(mode)}</b>
            <span class="muted">${st?.state === "auto" ? "This schedule is what it runs. − and + on a card last until the next change here." : "This schedule is kept for when the room goes back to Schedule. Change the mode on the room's page."}</span></div>
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
    const boiler = this._state(h.boiler);
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
            <span class="node">${esc(h.boiler_switch ? this._name(h.boiler_switch) : boiler?.attributes?.friendly_name || "Boiler thermostat")}</span><span aria-hidden="true">→</span>
            <span class="node">Boiler</span></div>
          <div>
            <div class="kv"><span>Call for heat switch<span class="sub">${h.boiler_switch ? (swOk ? `On while a room wants heat, off otherwise${i.last_boiler_command ? ` · last: ${esc(i.last_boiler_command)}` : ""}` : "Unavailable: Hearth can't ask for heat until it's back") : "Any on/off switch that fires the boiler. Leave empty to use a thermostat instead"}</span></span>
              <span class="pick">${this._entityField("boiler_switch", "switch,input_boolean", "Call for heat switch", h.boiler_switch)}</span></div>
            ${h.boiler_switch ? "" : `<div class="kv"><span>Boiler thermostat<span class="sub">Without a switch: Hearth turns this thermostat up and down to switch the boiler</span></span><span class="pick">${this._entityField("boiler", "climate", "Thermostat", h.boiler)}</span></div>`}
            ${secRow("demand_on_delay", "Wait before heating", "So the boiler doesn't flick on as valves settle", 0, 900, "s", 1)}
            ${secRow("demand_off_delay", "Wait before stopping", "", 0, 1800, "s", 1)}
          </div>
          ${h.boiler_switch ? "" : `<details><summary>The temperatures Hearth sets on the thermostat</summary>
            ${tempRow("boiler_on_temp", "When heat is wanted", "", 15, 32)}
            ${tempRow("boiler_off_temp", "When it isn't", "", 5, 18)}
            ${tempRow("fallback_temp", "If Hearth stops", "So the house can't go cold", 5, 22)}
          </details>`}
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
          ${secRow("boost_minutes", "Boost for", "", 15, 240, "min", 1)}
          ${tempRow("off_temp", "A radiator room that's off", "Kept above", 5, 15)}
        </section>
        <section class="card stack tight">
          <button class="btn solid wide" data-act="house-save" ${dirty && !this._busy ? "" : "disabled"}>${this._busy ? "Saving…" : "Save house settings"}</button>
          <button class="btn wide" data-act="house-discard" ${dirty ? "" : "disabled"}>Discard changes</button>
          <span class="muted small">Hearth ${esc(i.version)} · each room says why it's doing what it does; diagnostics are under Settings → Devices & services → Hearth.</span>
        </section>
      </aside></div>`;
  }

  _health(room) {
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
    const ids = Object.keys(this._hass.states).filter((id) => domain.split(",").includes(id.split(".")[0])
      && (!deviceClass || this._hass.states[id].attributes.device_class === deviceClass)).sort();
    return `<select data-pick-select="${esc(key)}" aria-label="${esc(label)}"><option value="">None</option>${ids.map((id) =>
      `<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(this._name(id))}</option>`).join("")}</select>`;
  }

  _wirePickers() {
    for (const picker of this.shadowRoot.querySelectorAll("ha-entity-picker[data-pick]")) {
      picker.hass = this._hass;
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
    if (this._house && (key === "boiler" || key === "boiler_switch")) {
      this._house[key] = value || null;
      this._render();
    }
  }

  // Add / edit a room ---------------------------------------------------------------
  _openDialog(room) {
    this._dialog = {
      step: room ? 1 : 0,
      draft: room ? { id: room.id, name: room.name, kind: room.kind, trvs: [...room.trvs], sensor: room.sensor || null, stat: room.stat || null, profile: room.profile || "" }
        : { id: null, name: "", kind: "radiator", trvs: [], sensor: null, stat: null, profile: "" },
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
        </div>`;
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
      case "house": this._call("select", "select_option", { entity_id: this._info.house_mode, option: el.dataset.mode }); return;
      case "mode": this._call("climate", "set_hvac_mode", { entity_id: room.entity_id, hvac_mode: el.dataset.mode }); return;
      case "step": this._step(room, Number(el.dataset.step)); return;
      case "boost": {
        const boosting = this._attr(room.entity_id, "preset_mode") === "boost";
        this._call("hearth", boosting ? "cancel_boost" : "boost", { entity_id: room.entity_id });
        return;
      }
      case "edit-schedule": this._sched = { zone: id, day: todayType(), draft: null, dirty: false }; this._tab = "schedules"; this._room = null; this._render(); return;
      case "edit": this._openDialog(room); return;
      case "add": this._openDialog(null); return;
      case "remove": this._removeRoom(room); return;
      case "house-save": this._saveHouse(); return;
      case "house-discard": this._house = null; this._render(); return;
      default: break;
    }
    if (act.startsWith("sched-")) this._schedAct(act, el);
    else if (act.startsWith("d-")) this._dialogAct(act, el);
  }

  _change(e) {
    const t = e.target;
    if (t.dataset.schedTime !== undefined) {
      const list = this._schedList();
      if (t.value) { list[Number(t.dataset.schedTime)].time = t.value; this._schedSet(list); }
      return;
    }
    if (t.dataset.house) {
      const key = t.dataset.house;
      this._house[key] = Number(t.value) * Number(t.dataset.scale || 1);
      this._render();
      return;
    }
    if (t.dataset.pickSelect) { this._choose(t.dataset.pickSelect, t.value); return; }
    if (t.dataset.dialog) this._dialogInput(t, true);
  }

  _input(e) {
    const t = e.target;
    if (t.dataset.dialog === "name" || t.dataset.dialog === "profile") this._dialogInput(t, false);
  }

  // Schedule editing --------------------------------------------------------------------
  _schedList() {
    const s = this._sched;
    return [...(s.draft[s.day] || [])].sort((a, b) => mins(a.time) - mins(b.time)).map((c) => ({ ...c }));
  }

  _schedSet(list) {
    this._sched.draft[this._sched.day] = list;
    this._sched.dirty = true;
    this._render();
  }

  _schedAct(act, el) {
    const s = this._sched;
    if (act === "sched-room" || act === "sched-day") {
      if (act === "sched-room" && s.dirty && !confirm("Discard the unsaved changes to this schedule?")) return;
      if (act === "sched-room") { s.zone = el.dataset.id; s.draft = null; s.dirty = false; } else s.day = el.dataset.day;
      this._render();
      return;
    }
    const list = this._schedList();
    const i = Number(el.dataset.i);
    if (act === "sched-temp") { list[i].temp = clamp(list[i].temp + Number(el.dataset.step)); this._schedSet(list); }
    else if (act === "sched-del" && list.length > 1) { list.splice(i, 1); this._schedSet(list); }
    else if (act === "sched-add" && list.length < MAX_CHANGES) {
      const t = Math.min(1410, (list.length ? mins(list[list.length - 1].time) : 0) + 60);
      list.push({ time: hhmm(t), temp: list.length ? list[list.length - 1].temp : 19 });
      this._schedSet(list);
    } else if (act === "sched-copyday") {
      s.draft[s.day === "weekdays" ? "weekends" : "weekdays"] = copy(list);
      s.dirty = true;
      this._say(`Copied to ${s.day === "weekdays" ? "weekends" : "weekdays"}. Save to keep it.`);
      this._render();
    } else if (act === "sched-copyrooms") this._copyToRooms();
    else if (act === "sched-save") this._saveSchedule([this._roomById(s.zone)], s.draft);
    else if (act === "sched-discard") { s.draft = null; s.dirty = false; this._render(); }
  }

  async _copyToRooms() {
    const s = this._sched;
    const others = this._info.rooms.filter((r) => r.id !== s.zone);
    const names = others.map((r, i) => `${i + 1}. ${r.name}`).join("\n");
    const pick = prompt(`Copy this room's weekdays and weekends to which rooms? Numbers, separated by commas, or "all".\n\n${names}`, "all");
    if (pick === null) return;
    const chosen = pick.trim().toLowerCase() === "all" ? others
      : pick.split(",").map((x) => others[Number(x.trim()) - 1]).filter(Boolean);
    if (!chosen.length) return;
    await this._saveSchedule([this._roomById(s.zone), ...chosen], s.draft);
  }

  async _saveSchedule(rooms, schedule) {
    this._busy = true;
    this._render();
    try {
      await this._hass.callService("hearth", "set_schedule", {
        entity_id: rooms.map((r) => r.entity_id), weekdays: schedule.weekdays, weekends: schedule.weekends,
      });
      this._sched.dirty = false;
      this._sched.draft = null;
      this._say(rooms.length > 1 ? `Saved to ${rooms.map((r) => r.name).join(", ")}.` : "Saved.");
    } catch (err) {
      this._say(err?.message || String(err));
    }
    this._busy = false;
    // The room's own attributes carry the saved (tidied) schedule.
    setTimeout(() => this._render(), 400);
    this._render();
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
          sensor: d.sensor || null, stat: d.kind === "underfloor" ? d.stat : null, profile: d.kind === "underfloor" ? (d.profile || null) : null },
      });
      const added = !d.id;
      this._dialog = null;
      this._say(added ? `${d.name} added. Its schedule is next.` : "Saved.");
      await this._reload();
      if (added) { this._sched = { zone: res.id, day: todayType(), draft: null, dirty: false }; this._tab = "schedules"; this._room = null; }
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
.block{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:#fff;font-size:12px;font-weight:500;border-right:1px solid var(--hp-card);overflow:hidden;white-space:nowrap}
.strip.tall .block{font-size:16px;border-right-width:2px}
.block .span{font-size:11px;font-weight:400;opacity:.9}
.ticks{flex:1 1 auto;display:flex;justify-content:space-between;font-size:12px;color:var(--secondary-text-color)}
.nowcard{display:flex;flex-wrap:wrap;gap:28px;align-items:center}
.controls{display:flex;flex-direction:column;align-items:center;gap:12px}
.round{width:52px;height:52px;border-radius:26px;border:1px solid var(--hp-line);background:transparent;color:var(--primary-text-color);font-size:24px;cursor:pointer}
.round.sm{width:44px;height:44px;font-size:20px}
.target{text-align:center;min-width:96px}
.tbig{font-size:40px;font-weight:300}
.tval{min-width:60px;text-align:center;font-size:22px;font-weight:300}
.kv{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--hp-line);font-size:14px}
.kv label,.kv>span:first-child{flex:1 1 auto}
.sub{display:block;font-size:12px;color:var(--secondary-text-color);margin-top:2px;line-height:1.4}
.pick{flex:0 1 300px;min-width:200px}
.pick ha-entity-picker,.pick select{width:100%}
select,input[type=number],input[type=text],input[type=time]{min-height:40px;padding:6px 10px;border:1px solid var(--hp-line);border-radius:8px;background:var(--primary-background-color);color:var(--primary-text-color);font:inherit;font-size:14px}
input[type=time]{color-scheme:light dark;font-size:15px}
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
@media (max-width:600px){.wrap{padding:16px}.pills{flex-wrap:nowrap;overflow-x:auto;margin:0 -16px;padding:0 16px;scrollbar-width:none}.huge{font-size:52px}.tname{width:110px}}
`;

customElements.define("hearth-panel", HearthPanel);
console.info(`%c HEARTH PANEL %c ${VERSION} `, "background:#ff8100;color:#fff", "");
