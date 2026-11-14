// What the Hearth card and the Hearth panel share, so every room reads and
// edits the same way wherever it's shown: the words for what a room is doing,
// the colours for temperatures, a day's blocks, and the schedule editor.

export const VERSION = "1.0.0b4";
export const MAX_CHANGES = 6;
export const DAYS = [["weekdays", "Weekdays · Mon–Fri"], ["weekends", "Weekends · Sat–Sun"]];

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const num = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
export const deg = (t) => (num(t) === null ? "–" : `${Number(t).toFixed(1).replace(/\.0$/, "")}°`);
export const mins = (hhmm) => { const [h, m] = String(hhmm).split(":").map(Number); return h * 60 + m; };
export const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
export const clamp = (t) => Math.min(28, Math.max(5, Math.round(t * 2) / 2));
export const copy = (o) => JSON.parse(JSON.stringify(o));
export const clock = (iso) => new Date(iso).toTimeString().slice(0, 5);
const typeOf = (d) => ([0, 6].includes(d.getDay()) ? "weekends" : "weekdays");
export const todayType = () => typeOf(new Date());
export const yesterdayType = () => typeOf(new Date(Date.now() - 86400000));

// One scale, cool to warm, readable with dark text in light and dark themes.
const STOPS = [[7, [150, 160, 170]], [16, [125, 170, 220]], [18, [135, 200, 160]], [19.5, [240, 195, 95]], [21, [240, 150, 85]], [23, [228, 105, 85]]];
export function tempColour(t) {
  if (t === null || t === undefined) return "var(--hearth-off, #8a8a8a)";
  if (t <= STOPS[0][0]) return `rgb(${STOPS[0][1]})`;
  for (let i = 1; i < STOPS.length; i++) {
    const [t1, c1] = STOPS[i];
    const [t0, c0] = STOPS[i - 1];
    if (t <= t1) { const f = (t - t0) / (t1 - t0); return `rgb(${c0.map((v, k) => Math.round(v + (c1[k] - v) * f))})`; }
  }
  return `rgb(${STOPS[STOPS.length - 1][1]})`;
}

// A day's changes as blocks from 00:00 to 24:00. Before the first change,
// `carry` (the night before's last temperature) still applies.
export function dayBlocks(list, carry) {
  const day = [...(list || [])].sort((a, b) => mins(a.time) - mins(b.time));
  if (!day.length) return [];
  const out = [];
  const before = carry ?? day[day.length - 1].temp;
  if (mins(day[0].time) > 0) out.push({ start: 0, end: mins(day[0].time), temp: before });
  day.forEach((c, i) => out.push({ start: mins(c.time), end: i + 1 < day.length ? mins(day[i + 1].time) : 1440, temp: c.temp }));
  return out.filter((b) => b.end > b.start);
}

// Today's blocks for a room, as it will actually run.
export function todayBlocks(stateObj) {
  const a = stateObj?.attributes || {};
  if (a.mode === "off") return [{ start: 0, end: 1440, temp: null }];
  if (a.mode === "fixed") return [{ start: 0, end: 1440, temp: num(a.temperature) }];
  const schedule = a.schedule || {};
  const carry = (schedule[yesterdayType()] || []).slice(-1)[0]?.temp;
  return dayBlocks(schedule[todayType()], carry);
}

export function stripHtml(blocks, { tall = false, now = null } = {}) {
  const parts = blocks.map((b) => {
    const pct = ((b.end - b.start) / 1440) * 100;
    const label = pct >= (tall ? 5 : 6) ? (b.temp === null ? "Off" : deg(b.temp)) : "";
    const span = tall && pct >= 11 ? `<span class="span">${hhmm(b.start)}–${hhmm(b.end)}</span>` : "";
    return `<div class="block" style="width:${pct.toFixed(3)}%;background:${tempColour(b.temp)}" title="${hhmm(b.start)}–${hhmm(b.end)} ${b.temp === null ? "Off" : deg(b.temp)}"><span>${label}</span>${span}</div>`;
  }).join("");
  const marker = now === null ? "" : `<i style="left:${now.toFixed(2)}%"></i>`;
  return `<div class="strip ${tall ? "tall" : ""}">${parts}${marker}</div>`;
}
export const nowPct = () => { const d = new Date(); return ((d.getHours() * 60 + d.getMinutes()) / 1440) * 100; };
export const TICKS = `<div class="ticks"><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>`;

export const STRIP_STYLE = `
.strip{position:relative;flex:1 1 auto;height:30px;display:flex;border-radius:8px;overflow:hidden}
.strip.tall{height:64px;border-radius:10px}
.strip i{position:absolute;top:0;bottom:0;width:2px;background:var(--primary-text-color);box-shadow:0 0 0 1px var(--card-background-color,#fff)}
.block{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:#1b1b1b;font-size:12px;font-weight:500;border-right:1px solid var(--card-background-color,#fff);overflow:hidden;white-space:nowrap}
.strip.tall .block{font-size:16px;border-right-width:2px}
.block .span{font-size:11px;font-weight:400;opacity:.8}
.ticks{display:flex;justify-content:space-between;font-size:12px;color:var(--secondary-text-color)}
`;

// --- what a room is doing ---------------------------------------------------
export const heating = (stateObj) => ["heating", "preheating"].includes(stateObj?.attributes?.hvac_action);
export const preheating = (stateObj) => stateObj?.attributes?.hvac_action === "preheating";
// The one line every room shows: from Hearth, the same words in every room.
export const statusOf = (stateObj) => stateObj?.attributes?.status || (stateObj ? "" : "Unavailable");
// Why it's aiming where it is, when that adds something to the status.
export function reasonOf(stateObj) {
  const a = stateObj?.attributes || {};
  if (stateObj?.state === "off" || (preheating(stateObj) && String(a.reason).startsWith("Warming up early"))) return "";
  return a.reason || "";
}
// What the room is aiming for, for under the big number.
export function targetLabel(stateObj) {
  const a = stateObj?.attributes || {};
  if (a.preset_mode === "boost") return a.boost_until ? `boost until ${clock(a.boost_until)}` : "boost";
  if (stateObj?.state === "off") return "off";
  if (stateObj?.state === "heat") return "fixed";
  // Warming up early: the target is the next change's, wanted by its time.
  if (a.preheating && a.next_change_at) return `by ${clock(a.next_change_at)}`;
  if (a.override_until) return `until ${clock(a.override_until)}`;
  return a.next_change_at ? `until ${clock(a.next_change_at)}` : "on schedule";
}
// Something wrong with the room's devices, in Hearth's words, or "".
export const problemOf = (stateObj) => stateObj?.attributes?.problem || "";
// Off, and not boosted out of it: there's no temperature to turn up or down.
export const isOff = (stateObj) => stateObj?.state === "off" && stateObj?.attributes?.preset_mode !== "boost";
// What a room's Boost button does: "22° · 1 h". A room knows how long its
// own boost lasts (a floor's is longer than a radiator's).
export function boostLabel(house, stateObj) {
  const m = Number(stateObj?.attributes?.boost_minutes) || Number(house?.attributes?.boost_minutes) || 60;
  const length = m % 60 === 0 ? `${m / 60} h` : `${m} min`;
  const temp = num(house?.attributes?.boost_temp);
  return temp === null ? length : `${deg(temp)} · ${length}`;
}
// While the house is Away or on Holiday a tap on − or + wouldn't show (the
// house's temperature wins), so they're switched off, with the reason.
export function houseLock(house, stateObj) {
  const mode = house?.state;
  if (mode === "holiday") return `On Holiday${house.attributes?.holiday_temp != null ? `, kept above ${deg(house.attributes.holiday_temp)}` : ""}. Switch to Home to change a room.`;
  if (mode === "away" && stateObj?.attributes?.preset_mode !== "boost") return `Away${house.attributes?.away_temp != null ? `, no warmer than ${deg(house.attributes.away_temp)}` : ""}. Boost the room, or switch to Home to change it.`;
  return "";
}
export function holidayBack(house) {
  const until = house?.attributes?.holiday_until;
  if (house?.state !== "holiday" || !until) return "";
  const d = new Date(until);
  return `back ${d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} ${d.toTimeString().slice(0, 5)}`;
}

// --- the schedule editor ------------------------------------------------------
// <hearth-schedule-editor>: one room's weekdays and weekends, up to six
// changes a day. Set .hass, .room (an entity id) and .rooms ([{entity_id,
// name}] it can copy to). It fires "hearth-saved" and, with .cancellable,
// "hearth-cancel"; .dirty says whether there are unsaved changes.
const EDITOR_STYLE = `
:host{display:block;color:var(--primary-text-color)}
*{box-sizing:border-box}
${STRIP_STYLE}
.stack{display:flex;flex-direction:column;gap:14px}.tight{gap:8px}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.between{justify-content:space-between}.push{margin-left:auto}
.seg{display:inline-flex;padding:3px;gap:2px;border:1px solid var(--divider-color);border-radius:20px;flex-wrap:wrap}
.seg button{border:0;background:none;color:var(--secondary-text-color);font:inherit;font-size:14px;padding:0 14px;min-height:36px;border-radius:16px;cursor:pointer}
.seg button.on{background:color-mix(in srgb,var(--primary-color) 16%,transparent);color:var(--primary-color);font-weight:500}
.muted{color:var(--secondary-text-color)}.small{font-size:13px}.warn{color:var(--warning-color,#ffa600)}.bad{color:var(--error-color,#db4437)}
.change{display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:8px 12px;border-radius:10px;border:1px solid var(--divider-color)}
.change label{display:flex;align-items:center;gap:10px;font-size:14px}
.swatch{width:12px;height:28px;border-radius:4px;flex:none}
input[type=time]{font:inherit;font-size:16px;padding:8px;border-radius:8px;border:1px solid var(--divider-color);background:transparent;color:var(--primary-text-color);color-scheme:light dark}
.round{width:44px;height:44px;border-radius:22px;border:1px solid var(--divider-color);background:transparent;color:var(--primary-text-color);font-size:20px;cursor:pointer}
.tval{min-width:56px;text-align:center;font-size:20px}
.x{width:40px;height:40px;border:0;background:none;color:var(--secondary-text-color);font-size:18px;cursor:pointer;border-radius:20px}
.x:disabled{opacity:.3;cursor:default}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:40px;padding:0 16px;border-radius:20px;border:1px solid var(--divider-color);background:transparent;color:var(--primary-text-color);font:inherit;font-size:14px;font-weight:500;cursor:pointer}
.btn.solid{background:var(--primary-color);border-color:var(--primary-color);color:var(--text-primary-color,#fff)}
.btn.dashed{border-style:dashed}
.btn:disabled{opacity:.45;cursor:default}
.copyto{border:1px solid var(--divider-color);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:8px}
.copyto label{display:flex;align-items:center;gap:10px;font-size:15px;min-height:36px}
.copyto input{width:20px;height:20px}
.footer{display:flex;gap:8px;justify-content:flex-end;flex-wrap:wrap;padding-top:4px}
`;

class HearthScheduleEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._day = todayType();
    this._draft = null;
    this._from = "";
    this.dirty = false;
    this._copying = null;
    this._busy = false;
    this._msg = "";
    this.rooms = [];
    this.cancellable = false;
    this.shadowRoot.addEventListener("click", (e) => this._click(e));
    this.shadowRoot.addEventListener("change", (e) => this._change(e));
  }

  set hass(hass) {
    this._hass = hass;
    // Follow the saved schedule until there's something unsaved.
    const saved = JSON.stringify(this._saved());
    if (!this.dirty && saved !== this._from) this._reset();
  }

  set room(entityId) {
    if (entityId === this._room) return;
    this._room = entityId;
    this._day = todayType();
    this._copying = null;
    this._msg = "";
    this._reset();
  }
  get room() { return this._room; }

  _state() { return this._room ? this._hass?.states[this._room] : undefined; }
  _saved() { return this._state()?.attributes?.schedule || null; }
  _name(id) { return this._hass?.states[id]?.attributes?.friendly_name || id; }

  _reset() {
    const saved = this._saved();
    this._from = JSON.stringify(saved);
    this._draft = saved ? copy(saved) : { weekdays: [{ time: "07:00", temp: 19 }], weekends: [{ time: "08:00", temp: 19 }] };
    this._setDirty(false);
    this._render();
  }

  _setDirty(value) {
    if (this.dirty === value) return;
    this.dirty = value;
    this.dispatchEvent(new CustomEvent("hearth-dirty", { detail: value, bubbles: true, composed: true }));
  }

  _list() { return [...(this._draft[this._day] || [])].sort((a, b) => mins(a.time) - mins(b.time)).map((c) => ({ ...c })); }
  _set(list) { this._draft[this._day] = list; this._setDirty(true); this._msg = ""; this._render(); }

  _render() {
    if (!this._hass || !this._draft) return;
    const list = this._list();
    const n = list.length;
    const other = this._day === "weekdays" ? "weekends" : "weekdays";
    const st = this._state();
    const ufh = st?.attributes?.kind === "underfloor";
    const others = this.rooms.filter((r) => r.entity_id !== this._room);
    // The night before a Monday is a weekend's, and before a Saturday a
    // weekday's: say so when that changes what the day starts on.
    const otherDay = [...(this._draft[other] || [])].sort((a, b) => mins(a.time) - mins(b.time));
    const otherEnd = otherDay.length ? otherDay[otherDay.length - 1].temp : null;
    const carryNote = n && otherEnd !== null && otherEnd !== list[n - 1].temp && mins(list[0].time) > 0
      ? ` On ${this._day === "weekdays" ? "Mondays" : "Saturdays"} that's ${other === "weekends" ? "the weekend's" : "Friday's"} ${deg(otherEnd)}, not ${deg(list[n - 1].temp)}.` : "";
    this.shadowRoot.innerHTML = `<style>${EDITOR_STYLE}</style><div class="stack">
      <div class="row between">
        <div class="seg" role="group" aria-label="Days">${DAYS.map(([k, label]) =>
          `<button data-do="day" data-day="${k}" class="${this._day === k ? "on" : ""}" aria-pressed="${this._day === k}">${label}</button>`).join("")}</div>
        <span class="small ${n >= MAX_CHANGES ? "warn" : "muted"}">${n} of ${MAX_CHANGES} changes</span>
      </div>
      <div class="stack tight">${stripHtml(dayBlocks(list), { tall: true })}${TICKS}</div>
      <span class="muted small">From each time, the room aims for that temperature until the next change. Before the day's first change, the night before's last one carries on.${carryNote}${st?.attributes?.warms_early ? " The room starts warming early, so it's already at a warmer change's temperature by its time." : ""}</span>
      <div class="stack tight">${list.map((c, i) => `<div class="change">
        <span class="swatch" style="background:${tempColour(c.temp)}"></span>
        <label class="muted">From <input type="time" value="${esc(c.time)}" data-time="${i}"></label>
        <div class="row push">
          <button class="round" data-do="temp" data-i="${i}" data-step="-0.5" aria-label="Cooler">−</button>
          <span class="tval">${deg(c.temp)}</span>
          <button class="round" data-do="temp" data-i="${i}" data-step="0.5" aria-label="Warmer">+</button>
          <button class="x" data-do="del" data-i="${i}" aria-label="Remove this change" ${n <= 1 ? "disabled" : ""}>✕</button>
        </div></div>`).join("")}</div>
      <div class="row">
        <button class="btn dashed" data-do="add" ${n >= MAX_CHANGES ? "disabled" : ""}>+ Add a change</button>
        <button class="btn" data-do="copyday">Copy to ${other}</button>
        ${others.length ? `<button class="btn" data-do="copyrooms">Copy to other rooms…</button>` : ""}
      </div>
      ${this._copying ? `<div class="copyto" role="group" aria-label="Copy to rooms">
        <span class="small muted">Save this schedule to ${esc(this._name(this._room))} and:</span>
        ${others.map((r) => `<label><input type="checkbox" data-copy="${esc(r.entity_id)}" ${this._copying.has(r.entity_id) ? "checked" : ""}>${esc(r.name)}</label>`).join("")}
      </div>` : ""}
      ${ufh ? `<span class="muted small">Saving writes this to the thermostat, which can take a minute. The stat then runs it by itself, even if Home Assistant is down.</span>` : ""}
      ${this._msg ? `<span class="small ${this._msgBad ? "bad" : "muted"}" role="status">${esc(this._msg)}</span>` : ""}
      <div class="footer">
        ${this.cancellable ? `<button class="btn" data-do="cancel">Close</button>` : ""}
        ${this.dirty ? `<button class="btn" data-do="discard">Discard changes</button>` : ""}
        <button class="btn solid" data-do="save" ${(this.dirty || this._copying?.size) && !this._busy ? "" : "disabled"}>${this._busy ? "Saving…" : this._copying?.size ? `Save to ${this._copying.size + 1} rooms` : "Save"}</button>
      </div></div>`;
  }

  _change(e) {
    const t = e.target;
    if (t.dataset.time !== undefined && t.value) {
      const list = this._list();
      const i = Number(t.dataset.time);
      if (list.some((c, k) => k !== i && c.time === t.value)) {
        // Two changes at one time would silently become one when saved.
        this._msg = `Another change already starts at ${t.value}.`;
        this._msgBad = true;
        this._render();
        return;
      }
      list[i].time = t.value;
      this._set(list);
    } else if (t.dataset.copy) {
      t.checked ? this._copying.add(t.dataset.copy) : this._copying.delete(t.dataset.copy);
      this._render();
    }
  }

  _click(e) {
    const el = e.target.closest("[data-do]");
    if (!el || el.disabled) return;
    const list = this._list();
    const i = Number(el.dataset.i);
    switch (el.dataset.do) {
      case "day": this._day = el.dataset.day; this._render(); break;
      case "temp": list[i].temp = clamp(list[i].temp + Number(el.dataset.step)); this._set(list); break;
      case "del": if (list.length > 1) { list.splice(i, 1); this._set(list); } break;
      case "add": {
        if (list.length >= MAX_CHANGES) break;
        const t = Math.min(1410, (list.length ? mins(list[list.length - 1].time) : 360) + 60);
        // A change to the same temperature would do nothing: offer the day's
        // other temperature, the way a day goes warm, cool, warm, cool.
        const temps = list.map((c) => c.temp);
        const last = temps.length ? temps[temps.length - 1] : null;
        const hi = Math.max(...temps), lo = Math.min(...temps);
        const temp = last === null ? 19 : last !== hi ? hi : lo !== hi ? lo : clamp(last + (last >= 19 ? -3 : 3));
        list.push({ time: hhmm(t), temp });
        this._set(list);
        break;
      }
      case "copyday": {
        const other = this._day === "weekdays" ? "weekends" : "weekdays";
        this._draft[other] = copy(list);
        this._setDirty(true);
        this._msg = `Copied to ${other}. Save to keep it.`;
        this._msgBad = false;
        this._render();
        break;
      }
      case "copyrooms": this._copying = this._copying ? null : new Set(); this._render(); break;
      case "discard": this._copying = null; this._msg = ""; this._reset(); break;
      case "cancel": this.dispatchEvent(new CustomEvent("hearth-cancel", { bubbles: true, composed: true })); break;
      case "save": this._save(); break;
      default: break;
    }
  }

  async _save() {
    const ids = [this._room, ...(this._copying || [])];
    this._busy = true;
    this._render();
    try {
      await this._hass.callService("hearth", "set_schedule", { entity_id: ids, weekdays: this._draft.weekdays, weekends: this._draft.weekends });
      const names = ids.map((id) => this._name(id));
      this._copying = null;
      this._busy = false;
      this._setDirty(false);
      this._from = "";  // take the tidied schedule Hearth saved, when it arrives
      this._msg = ids.length > 1 ? `Saved to ${names.join(", ")}.` : "Saved.";
      this._msgBad = false;
      this.dispatchEvent(new CustomEvent("hearth-saved", { detail: names, bubbles: true, composed: true }));
    } catch (err) {
      this._busy = false;
      this._msg = err?.message || String(err);
      this._msgBad = true;
    }
    this._render();
  }
}

if (!customElements.get("hearth-schedule-editor")) customElements.define("hearth-schedule-editor", HearthScheduleEditor);
