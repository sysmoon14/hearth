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

const VERSION = "0.3.0";
const MAX_CHANGES = 6;
const MODES = [
  ["auto", "Schedule"],
  ["heat", "Fixed"],
  ["off", "Off"],
];
const HOUSE = [
  ["home", "Home"],
  ["away", "Away"],
  ["holiday", "Holiday"],
];
const DAYS = [
  ["weekdays", "Weekdays"],
  ["weekends", "Weekends"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const deg = (t) => (t === null || t === undefined || isNaN(t) ? "–" : `${Number(t).toFixed(1).replace(/\.0$/, "")}°`);
const round = (t) => Math.round(t * 2) / 2;
const clamp = (t) => Math.min(28, Math.max(5, t));
const minutes = (hhmm) => {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
};

/* A cool-to-warm colour for a temperature, for the schedule bar. */
function tempColour(t) {
  const stops = [
    [7, [120, 144, 156]],
    [16, [100, 160, 210]],
    [18, [120, 190, 150]],
    [19.5, [245, 185, 70]],
    [21, [240, 130, 60]],
    [23, [225, 75, 60]],
  ];
  if (t <= stops[0][0]) return `rgb(${stops[0][1]})`;
  for (let i = 1; i < stops.length; i++) {
    const [t1, c1] = stops[i];
    const [t0, c0] = stops[i - 1];
    if (t <= t1) {
      const f = (t - t0) / (t1 - t0);
      return `rgb(${c0.map((v, k) => Math.round(v + (c1[k] - v) * f))})`;
    }
  }
  return `rgb(${stops[stops.length - 1][1]})`;
}

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  .house { display: flex; align-items: center; gap: 12px; padding: 16px 16px 4px; }
  .house .title { font-size: 1.1rem; font-weight: 500; flex: 1; color: var(--primary-text-color); }
  .seg { display: inline-flex; background: var(--secondary-background-color, rgba(127,127,127,.12)); border-radius: 999px; padding: 3px; gap: 2px; }
  .seg button { border: 0; background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .85rem;
    padding: 6px 12px; border-radius: 999px; cursor: pointer; min-height: 32px; }
  .seg button.on { background: var(--card-background-color, #fff); color: var(--primary-text-color); font-weight: 500;
    box-shadow: 0 1px 3px rgba(0,0,0,.15); }
  .seg.wide { display: flex; } .seg.wide button { flex: 1; }
  .room { border-top: 1px solid var(--divider-color); }
  .room:first-of-type { border-top: 0; }
  .single .room { border-top: 0; }
  .row { display: flex; align-items: center; gap: 12px; padding: 12px 16px; cursor: pointer; }
  .icon { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; flex: none;
    background: var(--secondary-background-color, rgba(127,127,127,.12)); color: var(--secondary-text-color); }
  .icon.heating { color: var(--red-color, #e53935); }
  .icon.off { opacity: .5; }
  .names { flex: 1; min-width: 0; }
  .name { font-weight: 500; color: var(--primary-text-color); }
  .why { font-size: .82rem; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .temps { text-align: right; }
  .now { font-size: 1.35rem; font-weight: 500; color: var(--primary-text-color); line-height: 1.1; }
  .aim { font-size: .8rem; color: var(--secondary-text-color); }
  .controls { padding: 4px 16px 16px; display: grid; gap: 14px; }
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
  .btn.hot { border-color: var(--red-color, #e53935); color: var(--red-color, #e53935); }
  .btn.primary { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .bar { position: relative; height: 10px; border-radius: 5px; overflow: hidden; display: flex; }
  .bar span { height: 100%; }
  .bar i { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--primary-text-color); border-radius: 1px; }
  .ticks { display: flex; justify-content: space-between; font-size: .7rem; color: var(--secondary-text-color); margin-top: 3px; }
  ha-icon { --mdc-icon-size: 22px; }
  .btn ha-icon { --mdc-icon-size: 18px; }
`;

const SHEET_STYLE = `
  .scrim { position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 9; display: flex; align-items: flex-end; justify-content: center; }
  @media (min-width: 600px) { .scrim { align-items: center; } }
  .sheet { background: var(--card-background-color, #fff); color: var(--primary-text-color); width: 100%; max-width: 480px;
    max-height: 92vh; display: flex; flex-direction: column; border-radius: 20px 20px 0 0; }
  @media (min-width: 600px) { .sheet { border-radius: 20px; } }
  .sheet header { padding: 18px 18px 8px; }
  .sheet h2 { margin: 0 0 12px; font-size: 1.15rem; font-weight: 500; }
  .sheet .body { padding: 8px 18px; overflow-y: auto; flex: 1; }
  .sheet footer { padding: 12px 18px calc(12px + env(safe-area-inset-bottom)); display: flex; gap: 8px; border-top: 1px solid var(--divider-color); }
  .change { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  .change input[type=time] { font: inherit; font-size: 1rem; padding: 8px; border-radius: 10px; border: 1px solid var(--divider-color);
    background: transparent; color: var(--primary-text-color); color-scheme: light dark; }
  .stepper { display: flex; align-items: center; gap: 6px; margin-left: auto; }
  .stepper button, .del { width: 36px; height: 36px; border-radius: 50%; border: 1px solid var(--divider-color); background: transparent;
    color: var(--primary-text-color); font-size: 1.1rem; cursor: pointer; }
  .stepper .val { min-width: 48px; text-align: center; font-size: 1.05rem; font-weight: 500; }
  .swatch { width: 10px; height: 28px; border-radius: 5px; flex: none; }
  .del { border: 0; color: var(--secondary-text-color); }
  .note { font-size: .82rem; color: var(--secondary-text-color); margin: 10px 0; }
  .error { color: var(--error-color, #db4437); font-size: .85rem; margin: 8px 0; }
  .link { background: none; border: 0; color: var(--primary-color); font: inherit; font-size: .88rem; cursor: pointer; padding: 8px 0; }
`;

class HearthCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._open = new Set();
    this._pending = {};
    this._sig = "";
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
    const ids = [...this._rooms(), this._houseEntity()].filter(Boolean);
    const sig = ids.map((id) => {
      const s = hass.states[id];
      return s ? `${s.last_updated}` : "";
    }).join("|") + Object.entries(this._pending).map(([k, v]) => k + v.temp).join();
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
    return this._hearthEntities("climate").sort((a, b) =>
      this._name(a).localeCompare(this._name(b))
    );
  }

  _houseEntity() {
    return this._hearthEntities("select")[0];
  }

  _name(id) {
    const s = this._hass.states[id];
    return s?.attributes.friendly_name || id;
  }

  /* --- actions ------------------------------------------------------------- */
  _call(domain, service, data) {
    return this._hass.callService(domain, service, data);
  }

  _nudge(id, delta) {
    const s = this._hass.states[id];
    const from = this._pending[id]?.temp ?? s.attributes.temperature ?? 19;
    const temp = clamp(round(from + delta));
    clearTimeout(this._pending[id]?.timer);
    this._pending[id] = { temp };
    this._render();
    // Wait for the taps to stop, then send once.
    this._pending[id].timer = setTimeout(async () => {
      try {
        await this._call("climate", "set_temperature", { entity_id: id, temperature: temp });
      } finally {
        setTimeout(() => {
          if (this._pending[id]?.temp === temp) delete this._pending[id];
          this._sig = "";
          this._render();
        }, 1500);
      }
    }, 700);
  }

  async _act(el) {
    const { action, id, value } = el.dataset;
    switch (action) {
      case "toggle":
        if (this._config.entity) return;
        this._open.has(id) ? this._open.delete(id) : this._open.add(id);
        this._render();
        break;
      case "minus":
        this._nudge(id, -0.5);
        break;
      case "plus":
        this._nudge(id, 0.5);
        break;
      case "mode":
        await this._call("climate", "set_hvac_mode", { entity_id: id, hvac_mode: value });
        break;
      case "boost":
        await this._call("hearth", "boost", { entity_id: id });
        break;
      case "unboost":
        await this._call("hearth", "cancel_boost", { entity_id: id });
        break;
      case "house":
        await this._call("select", "select_option", { entity_id: this._houseEntity(), option: value });
        break;
      case "schedule":
        this._openEditor(id);
        break;
    }
  }

  /* --- drawing ---------------------------------------------------------------- */
  _render() {
    if (!this._config || !this._hass) return;
    const rooms = this._rooms();
    const house = this._houseEntity();
    const single = !!this._config.entity;
    const houseRow =
      !single && this._config.show_house && house && this._hass.states[house]
        ? `<div class="house"><div class="title">${esc(this._config.title || "Heating")}</div>${this._seg(
            HOUSE, this._hass.states[house].state, "house", "")}</div>`
        : "";
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card class="${single ? "single" : ""}">${houseRow}
        ${rooms.map((id) => this._room(id)).join("") || `<div class="row"><div class="why">No Hearth rooms yet. Add them from Settings → Devices & services → Hearth → Configure.</div></div>`}
      </ha-card>`;
    this.shadowRoot.querySelectorAll("[data-action]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._act(el);
      })
    );
  }

  _seg(options, current, action, id, wide = false) {
    return `<div class="seg ${wide ? "wide" : ""}">${options
      .map(([v, label]) => `<button class="${v === current ? "on" : ""}" data-action="${action}" data-id="${esc(id)}" data-value="${v}">${label}</button>`)
      .join("")}</div>`;
  }

  _room(id) {
    const s = this._hass.states[id];
    if (!s) return `<div class="room"><div class="row"><div class="why">${esc(id)} isn't available</div></div></div>`;
    const a = s.attributes;
    const heating = a.hvac_action === "heating";
    const pending = this._pending[id]?.temp;
    const target = pending ?? a.temperature;
    const open = this._open.has(id);
    const icon = a.kind === "underfloor" ? "mdi:heating-coil" : "mdi:radiator";
    const boosting = a.preset_mode === "boost";
    const why = pending !== undefined ? `Setting ${deg(pending)}…` : a.reason || "";
    const row = `<div class="row" data-action="toggle" data-id="${esc(id)}">
        <div class="icon ${heating ? "heating" : ""} ${s.state === "off" ? "off" : ""}"><ha-icon icon="${heating ? "mdi:fire" : icon}"></ha-icon></div>
        <div class="names"><div class="name">${esc(a.friendly_name || id)}</div><div class="why">${esc(why)}</div></div>
        <div class="temps"><div class="now">${deg(a.current_temperature)}</div><div class="aim">${heating ? "heating to" : "set to"} ${deg(target)}</div></div>
      </div>`;
    if (!open) return `<div class="room">${row}</div>`;
    const controls = `<div class="controls">
        <div class="dial">
          <button class="round" data-action="minus" data-id="${esc(id)}" aria-label="Cooler">−</button>
          <div class="target"><div class="big">${deg(target)}</div><div class="label">${
            s.state === "auto" && !boosting ? "until the next change" : s.state === "heat" ? "fixed" : boosting ? "boost" : "off"
          }</div></div>
          <button class="round" data-action="plus" data-id="${esc(id)}" aria-label="Warmer">+</button>
        </div>
        ${this._seg(MODES, s.state, "mode", id, true)}
        ${a.schedule ? this._bar(a.schedule) : ""}
        <div class="actions">
          ${boosting
            ? `<button class="btn hot" data-action="unboost" data-id="${esc(id)}"><ha-icon icon="mdi:fire-off"></ha-icon>Stop boost</button>`
            : `<button class="btn" data-action="boost" data-id="${esc(id)}"><ha-icon icon="mdi:fire"></ha-icon>Boost 1 hour</button>`}
          <button class="btn" data-action="schedule" data-id="${esc(id)}"><ha-icon icon="mdi:calendar-clock"></ha-icon>Schedule</button>
        </div>
      </div>`;
    return `<div class="room">${row}${controls}</div>`;
  }

  /* Today's schedule as a coloured bar, with a marker for now. */
  _bar(schedule) {
    const now = new Date();
    const weekend = now.getDay() === 0 || now.getDay() === 6;
    const yesterdayWeekend = [0, 1].includes(now.getDay());
    const today = schedule[weekend ? "weekends" : "weekdays"] || [];
    const before = (schedule[yesterdayWeekend ? "weekends" : "weekdays"] || []).slice(-1)[0];
    return `<div>${this._barHtml(today, before?.temp ?? today.slice(-1)[0]?.temp, now.getHours() * 60 + now.getMinutes())}
      <div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div></div>`;
  }

  _barHtml(changes, startTemp, nowMins) {
    const pieces = [];
    let at = 0;
    let temp = startTemp;
    for (const c of changes) {
      const m = minutes(c.time);
      if (m > at) pieces.push([m - at, temp]);
      at = m;
      temp = c.temp;
    }
    pieces.push([1440 - at, temp]);
    const marker = nowMins === null ? "" : `<i style="left:calc(${(nowMins / 1440) * 100}% - 1px)"></i>`;
    return `<div class="bar">${pieces
      .filter(([len]) => len > 0)
      .map(([len, t]) => `<span style="width:${(len / 1440) * 100}%;background:${tempColour(t)}" title="${deg(t)}"></span>`)
      .join("")}${marker}</div>`;
  }

  /* --- the schedule editor ----------------------------------------------------- */
  _openEditor(id) {
    const s = this._hass.states[id];
    const schedule = JSON.parse(JSON.stringify(s.attributes.schedule || { weekdays: [], weekends: [] }));
    const weekend = [0, 6].includes(new Date().getDay());
    this._editor = { id, schedule, day: weekend ? "weekends" : "weekdays", error: "" };
    this._drawEditor();
  }

  _closeEditor() {
    this._editor = null;
    this._sheet?.remove();
    this._sheet = null;
  }

  _drawEditor() {
    const ed = this._editor;
    if (!ed) return;
    if (!this._sheet) {
      // Lives on the page, not inside the card, so dashboards' own layouts
      // and pop-ups can't clip it.
      this._sheet = document.createElement("div");
      this._sheet.attachShadow({ mode: "open" });
      document.body.appendChild(this._sheet);
    }
    const day = ed.schedule[ed.day];
    const other = ed.day === "weekdays" ? "weekends" : "weekdays";
    const last = ed.schedule[other].slice(-1)[0];
    const rows = day
      .map((c, i) => `<div class="change">
          <span class="swatch" style="background:${tempColour(c.temp)}"></span>
          <input type="time" value="${esc(c.time)}" data-i="${i}" step="300">
          <div class="stepper">
            <button data-do="down" data-i="${i}" aria-label="Cooler">−</button>
            <span class="val">${deg(c.temp)}</span>
            <button data-do="up" data-i="${i}" aria-label="Warmer">+</button>
          </div>
          <button class="del" data-do="del" data-i="${i}" aria-label="Remove">✕</button>
        </div>`)
      .join("");
    const sorted = [...day].sort((x, y) => minutes(x.time) - minutes(y.time));
    this._sheet.shadowRoot.innerHTML = `<style>${STYLE}${SHEET_STYLE}</style>
      <div class="scrim" data-do="scrim">
        <div class="sheet" role="dialog" aria-label="Schedule">
          <header>
            <h2>${esc(this._name(ed.id))} schedule</h2>
            ${this._seg(DAYS, ed.day, "day", "", true)}
          </header>
          <div class="body">
            ${this._barHtml(sorted, last?.temp ?? sorted.slice(-1)[0]?.temp ?? 16, null)}
            <div class="ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
            <p class="note">From each time, the room aims for that temperature until the next change. Up to ${MAX_CHANGES} a day.</p>
            ${rows}
            <button class="link" data-do="add" ${day.length >= MAX_CHANGES ? "disabled" : ""}>+ Add a change</button><br>
            <button class="link" data-do="copy">Copy to ${other}</button>
            ${ed.error ? `<div class="error">${esc(ed.error)}</div>` : ""}
          </div>
          <footer>
            <button class="btn" data-do="cancel">Cancel</button>
            <button class="btn primary" data-do="save">Save</button>
          </footer>
        </div>
      </div>`;
    const root = this._sheet.shadowRoot;
    root.querySelector(".sheet").addEventListener("click", (ev) => ev.stopPropagation());
    root.querySelectorAll("[data-do], [data-action=day]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._editAct(el);
      })
    );
    root.querySelectorAll("input[type=time]").forEach((el) =>
      el.addEventListener("change", () => {
        day[Number(el.dataset.i)].time = el.value || "00:00";
        this._drawEditor();
      })
    );
  }

  async _editAct(el) {
    const ed = this._editor;
    const day = ed.schedule[ed.day];
    const i = Number(el.dataset.i);
    switch (el.dataset.do || el.dataset.action) {
      case "scrim":
      case "cancel":
        return this._closeEditor();
      case "day":
        ed.day = el.dataset.value;
        break;
      case "up":
        day[i].temp = clamp(round(day[i].temp + 0.5));
        break;
      case "down":
        day[i].temp = clamp(round(day[i].temp - 0.5));
        break;
      case "del":
        day.splice(i, 1);
        break;
      case "add": {
        if (day.length >= MAX_CHANGES) return;
        const lastTime = day.length ? Math.max(...day.map((c) => minutes(c.time))) : 360;
        const at = Math.min(lastTime + 120, 23 * 60);
        const hh = String(Math.floor(at / 60)).padStart(2, "0");
        const mm = String(at % 60).padStart(2, "0");
        day.push({ time: `${hh}:${mm}`, temp: day.slice(-1)[0]?.temp ?? 19 });
        break;
      }
      case "copy": {
        const other = ed.day === "weekdays" ? "weekends" : "weekdays";
        ed.schedule[other] = JSON.parse(JSON.stringify(day));
        ed.error = `Copied to ${other}.`;
        return this._drawEditor();
      }
      case "save":
        for (const key of ["weekdays", "weekends"]) {
          if (!ed.schedule[key].length) {
            ed.error = `${key === "weekdays" ? "Weekdays" : "Weekends"} need at least one change.`;
            return this._drawEditor();
          }
        }
        try {
          await this._call("hearth", "set_schedule", { entity_id: ed.id, ...ed.schedule });
          return this._closeEditor();
        } catch (err) {
          ed.error = err?.message || String(err);
        }
        break;
    }
    ed.error = el.dataset.do === "save" ? ed.error : "";
    this._drawEditor();
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
