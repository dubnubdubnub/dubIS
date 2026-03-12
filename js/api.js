/* api.js — pywebview bridge + application log */

import { escHtml, showToast } from './ui-helpers.js';

const LOG_MAX_ENTRIES = 200;

export const AppLog = {
  _entries: [],
  _max: LOG_MAX_ENTRIES,
  _add(level, msg) {
    const entry = { level, msg, time: new Date() };
    this._entries.push(entry);
    if (this._entries.length > this._max) this._entries.shift();
    const el = document.getElementById("console-entries");
    if (!el) return;
    const div = document.createElement("div");
    div.className = "console-entry console-" + level;
    const t = entry.time.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit"});
    div.innerHTML = `<span class="console-time">${t}</span>${escHtml(msg)}`;
    el.appendChild(div);
    while (el.children.length > this._max) el.removeChild(el.firstChild);
    el.scrollTop = el.scrollHeight;
  },
  info(msg)  { this._add("info", msg); },
  warn(msg)  { this._add("warn", msg); },
  error(msg) { this._add("error", msg); },
  clear() {
    this._entries = [];
    const el = document.getElementById("console-entries");
    if (el) el.innerHTML = "";
  }
};

export async function api(method, ...args) {
  try {
    return await window.pywebview.api[method](...args);
  } catch (e) {
    AppLog.error(method + ": " + e.message);
    showToast("Error: " + e.message);
    return undefined;
  }
}
