/*
 * Hearth card: every room the same, whatever heats it.
 *
 *   type: custom:hearth-card                 # every room, plus Home/Away/Holiday
 *   type: custom:hearth-card
 *   entity: climate.office                   # one room
 *   type: custom:hearth-card
 *   entities: [climate.office, climate.snug] # these rooms, in this order
 *   show_house: false                        # hide the Home/Away/Holiday row
 */

import {
  VERSION, TICKS, STRIP_STYLE, boostLabel, clamp, deg, esc, heating, isOff, nowPct, problemOf, reasonOf, statusOf, stripHtml, targetLabel, todayBlocks,
} from "./hearth-common.js?v=1.0.0b4";

const MODES = [["auto", "Schedule"], ["heat", "Fixed"], ["off", "Off"]];
const HOUSE = [["home", "Home"], ["away", "Away"], ["holiday", "Holiday"]];

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  ${STRIP_STYLE}
  .strip { height: 12px; border-radius: 6px; }
  .strip .block span { display: none; }
  .house { display: flex; align-items: center; gap: 12px; padding: 16px 16px 4px; flex-wrap: wrap; }
  .house .title { font-size: 1.1rem; font-weight: 500; flex: 1; color: var(--primary-text-color); }
  .seg { display: inline-flex; background: var(--secondary-background-color, rgba(127,127,127,.12)); border-radius: 999px; padding: 3px; gap: 2px; }
  .seg button { border: 0; background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .85rem;
    padding: 6px 12px; border-radius: 999px; cursor: pointer; min-height: 36px; }
  .seg button.on { background: var(--card-background-color, #fff); color: var(--primary-text-color); font-weight: 500;
    box-shadow: 0 1px 3px rgba(0,0,0,.15); }
  .seg.wide { display: flex; } .seg.wide button { flex: 1; }
  .room { border-top: 1px solid var(--divider-color); }
  .room:first-of-type, .single .room { border-top: 0; }
  .row { display: flex; align-items: center; gap: 12px; padding: 12px 16px; cursor: pointer; }
  .single .row { cursor: default; }
  .icon { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; flex: none;
    background: var(--secondary-background-color, rgba(127,127,127,.12)); color: var(--secondary-text-color); }
  .icon.heating { color: var(--state-climate-heat-color, #ff8100); }
  .icon.off { opacity: .5; }
  .names { flex: 1; min-width: 0; }
  .name { font-weight: 500; color: var(--primary-text-color); }
  .why { font-size: .82rem; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .why.hot { color: var(--state-climate-heat-color, #ff8100); }
  .why.warn { color: var(--warning-color, #ffa600); white-space: normal; }
  .round:disabled { opacity: .35; cursor: default; }
  .temps { text-align: right; }
  .now { font-size: 1.35rem; font-weight: 500; color: var(--primary-text-color); line-height: 1.1; }
  .aim { font-size: .8rem; color: var(--secondary-text-color); }
  .controls { padding: 4px 16px 16px; display: grid; gap: 14px; }
  .reason { font-size: .85rem; color: var(--secondary-text-color); text-align: center; }
  .dial { display: flex; align-items: center; justify-content: center; gap: 20px; }
  .round { width: 52px; height: 52px; border-radius: 50%; border: 1px solid var(--divider-color);
    background: transparent; color: var(--primary-text-color); font-size: 1.6rem; cursor: pointer; display: grid; place-items: center; }
  .round:active { background: var(--secondary-background-color); }
  .target { text-align: center; min-width: 110px; }
  .target .big { font-size: 2.6rem; font-weight: 400; color: var(--primary-text-color); line-height: 1; }
  .target .label { font-size: .78rem; color: var(--secondary-text-color); margin-top: 4px; }
  .actions { display: flex; gap: 8px; }
  .btn { flex: 1; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color);
    font: inherit; font-size: .9rem; border-radius: 12px; padding: 10px; cursor: pointer; display: flex; align-items: center;
    justify-content: center; gap: 6px; min-height: 44px; }
  .btn.hot { border-color: var(--state-climate-heat-color, #ff8100); color: var(--state-climate-heat-color, #ff8100); }
  .empty { padding: 16px; color: var(--secondary-text-color); }
  ha-icon { --mdc-icon-size: 22px; }
  .btn ha-icon { --mdc-icon-size: 18px; }
`;

const SHEET_STYLE = `
  .scrim { position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 9; display: flex; align-items: flex-end; justify-content: center; }
  @media (min-width: 600px) { .scrim { align-items: center; } }
  .sheet { background: var(--card-background-color, #fff); color: var(--primary-text-color); width: 100%; max-width: 560px;
    max-height: 92vh; overflow-y: auto; border-radius: 20px 20px 0 0; padding: 18px 18px calc(18px + env(safe-area-inset-bottom));
    font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif); }
  @media (min-width: 600px) { .sheet { border-radius: 20px; } }
  h2 { margin: 0 0 14px; font-size: 1.15rem; font-weight: 500; }
`;

class HearthCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._open = new Set();
    this._pending = {};
    this._sig = "";
    this.shadowRoot.addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-action]");
      if (el && !el.disabled) { ev.stopPropagation(); this._act(el); }
    });
  }

  setConfig(config) {
    this._config = { show_house: true, ...config };
    if (config.entity) this._open.add(config.entity);
    this._sig = "";
    this._render();
  }

  static getStubConfig() {
    return {};
  }

  getCardSize() {
    return this._rooms().length * 2 + 1;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._editor) this._editor.hass = hass;
    const ids = [...this._rooms(), this._houseEntity()].filter(Boolean);
    const sig = ids.map((id) => hass.states[id]?.last_updated || "").join("|")
      + Object.entries(this._pending).map(([k, v]) => k + v.temp).join();
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  /* --- which entities -------------------------------------------------- */
  _hearthEntities(domain) {
    const reg = this._hass?.entities || {};
    return Object.keys(reg).filter((id) => reg[id].platform === "hearth" && id.startsWith(`${domain}.`));
  }

  _rooms() {
    if (!this._hass) return [];
    if (this._config?.entity) return [this._config.entity];
    if (this._config?.entities) return this._config.entities;
    return this._allRooms();
  }

  _allRooms() {
    return this._hearthEntities("climate").sort((a, b) => this._name(a).localeCompare(this._name(b)));
  }

  _houseEntity() {
    return this._hearthEntities("select")[0];
  }

  _name(id) {
    return this._hass.states[id]?.attributes.friendly_name || id;
  }

  /* --- actions ------------------------------------------------------------- */
  async _call(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
      this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: err?.message || String(err) }, bubbles: true, composed: true }));
    }
  }

  _nudge(id, delta) {
    const s = this._hass.states[id];
    const from = this._pending[id]?.temp ?? s.attributes.temperature ?? 19;
    const temp = clamp(from + delta);
    clearTimeout(this._pending[id]?.timer);
    const step = { temp };
    this._pending[id] = step;
    this._render();
    // Wait for the taps to stop, then send once.
    step.timer = setTimeout(async () => {
      await this._call("climate", "set_temperature", { entity_id: id, temperature: temp });
      setTimeout(() => {
        if (this._pending[id] === step) delete this._pending[id];
        this._sig = "";
        this._render();
      }, 1500);
    }, 700);
  }

  _act(el) {
    const { action, id, value } = el.dataset;
    switch (action) {
      case "toggle":
        if (this._config.entity) return;
        this._open.has(id) ? this._open.delete(id) : this._open.add(id);
        this._render();
        break;
      case "minus": this._nudge(id, -0.5); break;
      case "plus": this._nudge(id, 0.5); break;
      case "mode": this._call("climate", "set_hvac_mode", { entity_id: id, hvac_mode: value }); break;
      case "boost": this._call("hearth", "boost", { entity_id: id }); break;
      case "unboost": this._call("hearth", "cancel_boost", { entity_id: id }); break;
      case "house": this._call("select", "select_option", { entity_id: this._houseEntity(), option: value }); break;
      case "schedule": this._openEditor(id); break;
      default: break;
    }
  }

  /* --- drawing ---------------------------------------------------------------- */
  _render() {
    if (!this._config || !this._hass) return;
    const rooms = this._rooms();
    const house = this._houseEntity();
    const single = !!this._config.entity;
    const houseRow = !single && this._config.show_house && house && this._hass.states[house]
      ? `<div class="house"><div class="title">${esc(this._config.title || "Heating")}</div>${this._seg(HOUSE, this._hass.states[house].state, "house", "")}</div>`
      : !single && this._config.title ? `<div class="house"><div class="title">${esc(this._config.title)}</div></div>` : "";
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card class="${single ? "single" : ""}">${houseRow}
        ${rooms.map((id) => this._room(id)).join("") || `<div class="empty">No Hearth rooms yet. Add them from Hearth in the sidebar.</div>`}
      </ha-card>`;
  }

  _seg(options, current, action, id, wide = false) {
    return `<div class="seg ${wide ? "wide" : ""}" role="group">${options
      .map(([v, label]) => `<button class="${v === current ? "on" : ""}" aria-pressed="${v === current}" data-action="${action}" data-id="${esc(id)}" data-value="${v}">${label}</button>`)
      .join("")}</div>`;
  }

  _room(id) {
    const s = this._hass.states[id];
    if (!s) return `<div class="room"><div class="row"><div class="why">${esc(id)} isn't available</div></div></div>`;
    const a = s.attributes;
    const hot = heating(s);
    const pending = this._pending[id]?.temp;
    const target = pending ?? a.temperature;
    const open = this._open.has(id);
    const icon = a.kind === "underfloor" ? "mdi:heating-coil" : "mdi:radiator";
    const boosting = a.preset_mode === "boost";
    // While it heats, say how long; otherwise, what it's doing and why.
    const problem = problemOf(s);
    const off = isOff(s);
    const line = pending !== undefined ? `Setting ${deg(pending)}…` : problem || (hot || s.state === "off" ? statusOf(s) : a.reason || statusOf(s));
    const row = `<div class="row" data-action="toggle" data-id="${esc(id)}" role="button" aria-expanded="${open}">
        <div class="icon ${hot ? "heating" : ""} ${s.state === "off" ? "off" : ""}"><ha-icon icon="${hot ? "mdi:fire" : icon}"></ha-icon></div>
        <div class="names"><div class="name">${esc(a.friendly_name || id)}</div><div class="why ${problem && pending === undefined ? "warn" : hot ? "hot" : ""}">${esc(line)}</div></div>
        <div class="temps"><div class="now">${deg(a.current_temperature)}</div><div class="aim">${off ? "off" : `→ ${deg(target)}`}</div></div>
      </div>`;
    if (!open) return `<div class="room">${row}</div>`;
    const house = this._hass.states[this._houseEntity()];
    const controls = `<div class="controls">
        ${(hot || problem) && reasonOf(s) ? `<div class="reason">${esc(reasonOf(s))}</div>` : ""}
        <div class="dial">
          <button class="round" data-action="minus" data-id="${esc(id)}" aria-label="Cooler" ${off ? "disabled" : ""}>−</button>
          <div class="target"><div class="big">${off ? "Off" : deg(target)}</div><div class="label">${esc(off ? `kept above ${deg(a.temperature)}` : targetLabel(s))}</div></div>
          <button class="round" data-action="plus" data-id="${esc(id)}" aria-label="Warmer" ${off ? "disabled" : ""}>+</button>
        </div>
        ${this._seg(MODES, s.state, "mode", id, true)}
        ${a.schedule ? `<div>${stripHtml(todayBlocks(s), { now: nowPct() })}${TICKS}</div>` : ""}
        <div class="actions">
          ${boosting
            ? `<button class="btn hot" data-action="unboost" data-id="${esc(id)}"><ha-icon icon="mdi:fire-off"></ha-icon>Stop boost</button>`
            : `<button class="btn" data-action="boost" data-id="${esc(id)}"><ha-icon icon="mdi:fire"></ha-icon>Boost ${esc(boostLabel(house))}</button>`}
          <button class="btn" data-action="schedule" data-id="${esc(id)}"><ha-icon icon="mdi:calendar-clock"></ha-icon>Schedule</button>
        </div>
      </div>`;
    return `<div class="room">${row}${controls}</div>`;
  }

  /* --- the schedule editor: the same one the Hearth panel uses ----------------- */
  _openEditor(id) {
    this._closeEditor();
    // Lives on the page, not inside the card, so dashboards' own layouts
    // and pop-ups can't clip it.
    this._sheet = document.createElement("div");
    this._sheet.attachShadow({ mode: "open" });
    this._sheet.shadowRoot.innerHTML = `<style>${SHEET_STYLE}</style>
      <div class="scrim"><div class="sheet" role="dialog" aria-label="${esc(this._name(id))} schedule">
        <h2>${esc(this._name(id))} schedule</h2></div></div>`;
    const editor = document.createElement("hearth-schedule-editor");
    editor.cancellable = true;
    editor.rooms = this._allRooms().map((r) => ({ entity_id: r, name: this._name(r) }));
    editor.hass = this._hass;
    editor.room = id;
    this._sheet.shadowRoot.querySelector(".sheet").appendChild(editor);
    const close = () => {
      if (editor.dirty && !confirm("Close without saving the changes to this schedule?")) return;
      this._closeEditor();
    };
    this._sheet.shadowRoot.querySelector(".scrim").addEventListener("click", (e) => { if (e.target.classList.contains("scrim")) close(); });
    editor.addEventListener("hearth-cancel", close);
    editor.addEventListener("hearth-saved", () => setTimeout(() => this._closeEditor(), 900));
    this._editor = editor;
    document.body.appendChild(this._sheet);
  }

  _closeEditor() {
    this._sheet?.remove();
    this._sheet = null;
    this._editor = null;
  }

  disconnectedCallback() {
    this._closeEditor();
  }
}

if (!customElements.get("hearth-card")) {
  customElements.define("hearth-card", HearthCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "hearth-card",
    name: "Hearth",
    description: "Every room's heating, the same way, whatever heats it.",
    preview: false,
  });
  console.info(`%c HEARTH %c ${VERSION} `, "background:#e65100;color:#fff;border-radius:3px 0 0 3px", "background:#333;color:#fff;border-radius:0 3px 3px 0");
}
