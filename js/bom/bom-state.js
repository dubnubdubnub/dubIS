/* bom/bom-state.js — Centralized mutable state for the BOM panel. */

/** @enum {boolean} */
export const ConsumeState = Object.freeze({
  IDLE: false,
  ARMED: true,
});

const state = {
  body: null,              // set in init()
  lastResults: null,
  lastFileName: "",

  // Editable raw-row state
  bomRawRows: [],
  bomHeaders: [],
  bomCols: {},
  bomDirty: false,

  // Consume state
  consumeArmed: ConsumeState.IDLE,
  lastConsumeMeta: null,
  consumeModal: null,      // set in init()
};

export default state;
