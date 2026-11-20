/*
 * Hearth hot water card: what the tank is doing, how hot it is, and the
 * buttons to boost it, keep it off, or hand it back to Hearth.
 *
 *   type: custom:hearth-hot-water-card
 *   title: Hot water          # optional
 *   boost_minutes: 60         # optional: how long the Boost button runs for
 *   entity: water_heater.x    # optional: found by itself
 */

import { VERSION, deg, esc, hotWaterNote } from "./hearth-common.js?v=1.0.0b12";

const STYLE = `
  :host { display: block; }
  ha-card { overflow: hidden; }
  .head { display: flex; align-items: center; gap: 12px; padding: 16px; }
  .icon { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; flex: none;
    background: var(--secondary-background-color, rgba(127,127,127,.12)); color: var(--secondary-text-color); }
  .icon.heating { color: var(--state-climate-heat-color, #ff8100); }
  .icon.off { opacity: .5; }
  .names { flex: 1; min-width: 0; }
  .name { font-weight: 500; color: var(--primary-text-color); }
  .why { font-size: .82rem; color: var(--secondary-text-color); }
  .why.hot { color: var(--state-climate-heat-color, #ff8100); }
  .temps { text-align: right; flex: none; }
  .now { font-size: 1.35rem; font-weight: 500; color: var(--primary-text-color); line-height: 1.1; }
  .aim { font-size: .8rem; color: var(--secondary-text-color); }
  .body { padding: 0 16px 16px; display: grid; gap: 12px; }
  .gauge { position: relative; padding-top: 16px; }
  .track { position: relative; height: 10px; border-radius: 5px; background: var(--secondary-background-color, rgba(127,127,127,.15)); overflow: hidden; }
  .cold { position: absolute; inset: 0 auto 0 0; background: color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent); }
  .fill { position: absolute; inset: 0 auto 0 0; border-radius: 5px; }
  .mark { position: absolute; top: 12px; bottom: -2px; width: 2px; margin-left: -1px; background: var(--primary-text-color); opacity: .45; }
  .mark span { position: absolute; top: -14px; left: 50%; transform: translateX(-50%); font-size: .7rem; color: var(--secondary-text-color); white-space: nowrap; }
  .legend { display: flex; flex-wrap: wrap; gap: 2px 6px; margin-top: 8px; font-size: .78rem; color: var(--secondary-text-color); }
  .note { font-size: .85rem; color: var(--secondary-text-color); }
  .note.warn { color: var(--warning-color, #ffa600); }
  .actions { display: flex; gap: 8px; }
  .btn { flex: 1; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color);
    font: inherit; font-size: .9rem; border-radius: 12px; padding: 10px; cursor: pointer; display: flex; align-items: center;
    justify-content: center; gap: 6px; min-height: 44px; }
  .btn.solid { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .btn.hot { border-color: var(--state-climate-heat-color, #ff8100); color: var(--state-climate-heat-color, #ff8100); }
  .empty { padding: 16px; color: var(--secondary-text-color); }
  ha-icon { --mdc-icon-size: 22px; }
  .btn ha-icon { --mdc-icon-size: 18px; }
`;

const HEAT = "var(--state-climate-heat-color, #ff8100)";
const COLD = "var(--primary-color, #03a9f4)";

class HearthHotWaterCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._sig = "";
    this.shadowRoot.addEventListener("click", (ev) => {
      const el = ev.target.closest("[data-action]");
      if (el && !el.disabled) { ev.stopPropagation(); this._act(el.dataset.action); }
    });
  }

  setConfig(config) {
    this._config = { boost_minutes: 60, ...config };
    this._sig = "";
    this._render();
  }

  static getStubConfig() {
    return {};
  }

  getCardSize() {
    return 3;
  }

  set hass(hass) {
    this._hass = hass;
    const s = hass.states[this._entity()];
    const sig = s ? s.last_updated : "none";
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  _entity() {
    if (this._config?.entity) return this._config.entity;
    const reg = this._hass?.entities || {};
    return Object.keys(reg).find((id) => reg[id].platform === "hearth" && id.startsWith("water_heater."));
  }

  async _act(action) {
    const service = { boost: "boost_hot_water", stop: "stop_hot_water", resume: "resume_hot_water" }[action];
    if (!service) return;
    try {
      await this._hass.callService("hearth", service, action === "boost" ? { minutes: Number(this._config.boost_minutes) || 60 } : {});
    } catch (err) {
      this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message: err?.message || String(err) }, bubbles: true, composed: true }));
    }
  }

  _render() {
    if (!this._config || !this._hass) return;
    const id = this._entity();
    const s = id && this._hass.states[id];
    const title = esc(this._config.title || "Hot water");
    if (!s) {
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div class="empty">${title}: Hearth isn't heating the hot water.
        Choose its Call for hot water switch on the House tab of Hearth in the sidebar.</div></ha-card>`;
      return;
    }
    const a = s.attributes;
    const hot = s.state === "heating";
    const off = s.state === "off";
    const temp = a.current_temperature;
    const aim = a.off_by_hand_since ? "off by hand" : off ? "off" : a.full_at != null ? `full at ${deg(a.full_at)}` : "";
    const head = `<div class="head">
        <div class="icon ${hot ? "heating" : ""} ${off ? "off" : ""}"><ha-icon icon="${off ? "mdi:water-boiler-off" : "mdi:water-boiler"}"></ha-icon></div>
        <div class="names"><div class="name">${title}</div><div class="why ${hot ? "hot" : ""}">${esc(a.status || s.state)}</div></div>
        ${temp != null || aim ? `<div class="temps">${temp != null ? `<div class="now">${deg(temp)}</div>` : ""}<div class="aim">${esc(aim)}</div></div>` : ""}
      </div>`;
    const buttons = a.off_by_hand_since
      ? `<button class="btn solid" data-action="resume"><ha-icon icon="mdi:autorenew"></ha-icon>Let Hearth decide</button>${this._boostBtn()}`
      : a.heating
        ? `<button class="btn hot" data-action="stop"><ha-icon icon="mdi:water-boiler-off"></ha-icon>Turn off</button>`
        : `<button class="btn" data-action="stop"><ha-icon icon="mdi:water-boiler-off"></ha-icon>Keep off</button>${this._boostBtn()}`;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card>${head}
        <div class="body">
          ${this._gauge(a)}
          ${!a.full_at && a.minutes_heated != null ? `<div class="note">Heated ${Math.round(a.minutes_heated)} of ${Math.round(a.minutes_wanted)} min so far</div>` : ""}
          <div class="note">${esc(hotWaterNote(a))}</div>
          ${a.problem ? `<div class="note warn">${esc(a.problem)}</div>` : ""}
          <div class="actions">${buttons}</div>
        </div>
      </ha-card>`;
  }

  _boostBtn() {
    const m = Number(this._config.boost_minutes) || 60;
    return `<button class="btn" data-action="boost"><ha-icon icon="mdi:fire"></ha-icon>Boost ${m % 60 ? `${m} min` : `${m / 60} h`}</button>`;
  }

  // The tank's temperature against its three lines: too cold, top up, full.
  _gauge(a) {
    const temp = a.current_temperature;
    if (a.full_at == null || temp == null) return "";
    const floor = a.heat_anyway_below, top = a.top_up_below, full = a.full_at;
    const lo = Math.min(floor ?? top, temp) - 5, hi = Math.max(full, temp) + 5;
    const pct = (t) => Math.max(0, Math.min(100, ((t - lo) / (hi - lo)) * 100));
    const color = floor != null && temp < floor ? COLD : HEAT;
    const mark = (t) => `<div class="mark" style="left:${pct(t)}%"><span>${deg(t)}</span></div>`;
    return `<div>
        <div class="gauge" role="img" aria-label="Tank at ${deg(temp)}: full at ${deg(full)}, topped up below ${deg(top)}${floor != null ? `, heated at once below ${deg(floor)}` : ""}">
          <div class="track">${floor != null ? `<div class="cold" style="width:${pct(floor)}%"></div>` : ""}<div class="fill" style="width:${pct(temp)}%;background:${color}"></div></div>
          ${floor != null ? mark(floor) : ""}${mark(top)}${mark(full)}
        </div>
        <div class="legend">${[floor != null ? `Too cold to wait below ${deg(floor)}` : "", `Top up below ${deg(top)}`, `Full at ${deg(full)}`]
          .filter(Boolean).map((t) => `<span>${t}</span>`).join('<span aria-hidden="true">·</span>')}</div>
      </div>`;
  }
}

if (!customElements.get("hearth-hot-water-card")) {
  customElements.define("hearth-hot-water-card", HearthHotWaterCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "hearth-hot-water-card",
    name: "Hearth hot water",
    description: "The hot water tank: how hot it is, what it's doing, and buttons to boost it, keep it off or hand it back to Hearth.",
    preview: false,
  });
  console.info(`%c HEARTH HOT WATER %c ${VERSION} `, "background:#e65100;color:#fff;border-radius:3px 0 0 3px", "background:#333;color:#fff;border-radius:0 3px 3px 0");
}
