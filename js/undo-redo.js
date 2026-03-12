/* undo-redo.js — Global undo/redo stack with panel-specific restorers */

import { AppLog } from './api.js';

const UNDO_MAX_HISTORY = 500;

export const UndoRedo = {
  _undo: [],
  _redo: [],
  _max: UNDO_MAX_HISTORY,
  _restorers: {},
  _busy: false,

  register(panel, restoreFn) { this._restorers[panel] = restoreFn; },

  save(panel, data) {
    this._undo.push({ panel, data: JSON.parse(JSON.stringify(data)) });
    if (this._undo.length > this._max) this._undo.shift();
    this._redo = [];
  },

  async undo() {
    if (!this._undo.length || this._busy) return false;
    this._busy = true;
    const entry = this._undo.pop();
    const savedRedo = this._redo.slice();
    try {
      const current = await this._restorers[entry.panel]("snapshot");
      this._redo.push({ panel: entry.panel, data: current });
      await this._restorers[entry.panel]("restore", entry.data);
      AppLog.info("Undo (" + entry.panel + ")");
      return true;
    } catch (err) {
      this._undo.push(entry);
      this._redo = savedRedo;
      AppLog.error("Undo failed: " + err.message);
      throw err;
    } finally {
      this._busy = false;
    }
  },

  async redo() {
    if (!this._redo.length || this._busy) return false;
    this._busy = true;
    const entry = this._redo.pop();
    const savedUndo = this._undo.slice();
    try {
      const current = await this._restorers[entry.panel]("snapshot");
      this._undo.push({ panel: entry.panel, data: current });
      await this._restorers[entry.panel]("restore", entry.data);
      AppLog.info("Redo (" + entry.panel + ")");
      return true;
    } catch (err) {
      this._redo.push(entry);
      this._undo = savedUndo;
      AppLog.error("Redo failed: " + err.message);
      throw err;
    } finally {
      this._busy = false;
    }
  },

  canUndo() { return this._undo.length > 0 && !this._busy; },
  canRedo() { return this._redo.length > 0 && !this._busy; },
  popLast() { return this._undo.pop(); },
};
