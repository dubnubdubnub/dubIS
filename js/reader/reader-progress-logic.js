// @ts-check
/* js/reader/reader-progress-logic.js — pure reader-install progress logic.

   Phase → human label, byte formatting, and percentage resolution for the
   picture/PDF reader install UI. No DOM, no store, no imports: vitest can load
   this directly without dragging in js/constants.js's top-level fetch (see the
   CLAUDE.md trap). The DOM- and polling-facing half lives in
   js/reader/reader-panel.js.

   The backend cannot stream (pywebview has no push channel for this), so the
   panel polls a status object and re-renders from it. Every function here takes
   whatever the poll actually returned — including a status that raced the job's
   first published update and is missing most of its fields — and returns
   something a human can read. Nothing in here may ever put the strings
   "null", "undefined" or "NaN" on screen. */

/**
 * The install phases, in the order the backend walks them.
 * `done` and `error` are terminal; everything else is in-flight.
 * @typedef {'detect'|'runtime'|'weights'|'projector'|'start'|'verify'|'done'|'error'} ReaderPhase
 */

/** @type {string[]} */
export const PHASES = [
  'detect', 'runtime', 'weights', 'projector', 'start', 'verify', 'done', 'error',
];

/** @type {string[]} */
export const TERMINAL_PHASES = ['done', 'error'];

/**
 * Phases with no meaningful denominator: they are doing work whose length is not
 * knowable in bytes (probing memory, waiting for a server to answer /health,
 * reading one synthetic page). They must report *no* percentage — a bar parked
 * at 0% reads as "stuck" when the truth is "working, length unknown".
 * @type {string[]}
 */
export const INDETERMINATE_PHASES = ['detect', 'start', 'verify'];

/**
 * Human labels, written for a non-technical operator: the user asked for text
 * that says what it is doing, not a phase name. Every phase in PHASES must have
 * one (enforced by tests/js/reader-progress-logic.test.js), so a new phase
 * cannot ship label-less.
 * @type {Record<string, string>}
 */
export const PHASE_LABELS = {
  detect: 'Checking what this computer can run…',
  runtime: 'Downloading the reader program…',
  weights: 'Downloading the model…',
  projector: 'Downloading the vision projector…',
  start: 'Starting the reader…',
  verify: 'Checking it can read a page…',
  done: 'The reader is ready.',
  error: 'The reader could not be installed.',
};

/**
 * Shown for a phase this build does not know about — a newer backend, or a poll
 * that arrived before the job published its first update. Honest and non-empty
 * beats rendering the raw phase name or an empty status line.
 */
export const UNKNOWN_PHASE_LABEL = 'Working…';

/* Binary units throughout: 1 KiB = 1024 B, matching the backend's `.free_gib`
   and the GiB figures in the model-tier table. Mixing SI and binary between the
   two halves of the same install UI would make "4.7 GiB" mean two things. */
const BYTE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
const BYTES_PER_UNIT = 1024;

/**
 * A finite number, or null for anything else. Never throws and never returns
 * NaN: every caller here renders its result, so a bad field must degrade into
 * "unknown", not into text.
 * @param {unknown} v
 * @returns {number|null}
 */
function finiteOrNull(v) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * A non-empty trimmed string, or null.
 * @param {unknown} v
 * @returns {string|null}
 */
function textOrNull(v) {
  if (typeof v !== 'string') return null;
  const t = v.trim();
  return t.length > 0 ? t : null;
}

/** One decimal place, with a bare integer kept bare: 4.7 → "4.7", 1 → "1". */
function trimDecimal(n) {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

/**
 * Human label for a phase. Total: an unknown or missing phase gets
 * UNKNOWN_PHASE_LABEL rather than an empty string.
 * @param {unknown} phase
 * @returns {string}
 */
export function phaseLabel(phase) {
  const key = textOrNull(phase);
  if (key !== null && Object.prototype.hasOwnProperty.call(PHASE_LABELS, key)) {
    return PHASE_LABELS[key];
  }
  return UNKNOWN_PHASE_LABEL;
}

/**
 * Is this phase one that cannot report a percentage? An unknown phase counts as
 * indeterminate: we do not know what it measures, so we claim nothing.
 * @param {unknown} phase
 * @returns {boolean}
 */
export function isIndeterminatePhase(phase) {
  const key = textOrNull(phase);
  if (key === null) return true;
  if (INDETERMINATE_PHASES.includes(key)) return true;
  return !PHASES.includes(key);
}

/**
 * Has the job stopped? The panel stops polling on true.
 * @param {unknown} phase
 * @returns {boolean}
 */
export function isTerminalPhase(phase) {
  const key = textOrNull(phase);
  return key !== null && TERMINAL_PHASES.includes(key);
}

/**
 * Format a byte count in binary units, or null when the count is not a usable
 * size (missing, non-numeric, non-finite, negative). Callers omit the segment
 * rather than printing a placeholder.
 *
 * 0 → "0 B", 999 → "999 B", 1024 → "1 KiB", 5046586573 → "4.7 GiB".
 * @param {unknown} bytes
 * @returns {string|null}
 */
export function formatBytes(bytes) {
  const n = finiteOrNull(bytes);
  if (n === null || n < 0) return null;
  if (n < BYTES_PER_UNIT) return `${Math.round(n)} B`;

  let value = n;
  let unit = 0;
  while (value >= BYTES_PER_UNIT && unit < BYTE_UNITS.length - 1) {
    value /= BYTES_PER_UNIT;
    unit += 1;
  }
  let rounded = Math.round(value * 10) / 10;
  // 1023.97 MiB rounds to 1024.0 MiB; carry rather than print an out-of-range unit.
  if (rounded >= BYTES_PER_UNIT && unit < BYTE_UNITS.length - 1) {
    rounded = Math.round((rounded / BYTES_PER_UNIT) * 10) / 10;
    unit += 1;
  }
  return `${trimDecimal(rounded)} ${BYTE_UNITS[unit]}`;
}

/**
 * Clamp a reported percentage into 0..100, or null when there is no number to
 * clamp. Never negative, never above 100 — the backend can report
 * `bytes_done > bytes_total` when a response body outruns its Content-Length.
 * @param {unknown} pct
 * @returns {number|null}
 */
export function clampPct(pct) {
  const n = finiteOrNull(pct);
  if (n === null) return null;
  return Math.max(0, Math.min(100, n));
}

/**
 * Percentage from a byte pair. Mirrors `reader_install.progress_pct` exactly,
 * including its three distinct cases — conflating any two of them is the bug
 * both functions exist to prevent:
 *
 *  - `bytesTotal` unknown (null/absent/non-numeric) → null. There is no
 *    percentage. Not 0, which a bar renders as "stuck at the start".
 *  - `bytesTotal === 0` → 100. A real zero-length file is complete, and that is
 *    a different fact from "length unknown".
 *  - otherwise → clamped to 0..100.
 *
 * @param {unknown} bytesDone
 * @param {unknown} bytesTotal
 * @returns {number|null}
 */
export function progressPct(bytesDone, bytesTotal) {
  const total = finiteOrNull(bytesTotal);
  if (total === null) return null;
  if (total <= 0) return 100;
  const done = finiteOrNull(bytesDone) ?? 0;
  return clampPct((done * 100) / total);
}

/**
 * The percentage to show for a whole status object, or null for "no percentage".
 *
 * Resolution order: an indeterminate phase always wins (it reports nothing even
 * if the backend attached a pct); `done` is 100; otherwise a numeric `pct` from
 * the backend is trusted and clamped, and a missing one is derived from the
 * bytes.
 * @param {unknown} status
 * @returns {number|null}
 */
export function statusPct(status) {
  const s = /** @type {Record<string, unknown>} */ (
    status && typeof status === 'object' ? status : {}
  );
  const phase = textOrNull(s.phase);
  if (phase === 'error') return null;
  if (phase === 'done' || s.done === true) return 100;
  if (isIndeterminatePhase(phase)) return null;
  const reported = clampPct(s.pct);
  if (reported !== null) return reported;
  return progressPct(s.bytes_done, s.bytes_total);
}

/**
 * "42%", or null when there is no percentage. Whole percents: a status line
 * re-rendered on every poll should not flicker through decimals.
 * @param {unknown} pct
 * @returns {string|null}
 */
export function formatPct(pct) {
  const n = clampPct(pct);
  return n === null ? null : `${Math.round(n)}%`;
}

/**
 * "1.2 MiB of 4.7 GiB", or "1.2 MiB" when the total is unknown (an
 * indeterminate download still has a real amount fetched so far, and showing it
 * is the only proof that bytes are moving), or null when neither is usable.
 * @param {unknown} bytesDone
 * @param {unknown} bytesTotal
 * @returns {string|null}
 */
export function formatByteProgress(bytesDone, bytesTotal) {
  const done = formatBytes(bytesDone);
  const total = formatBytes(bytesTotal);
  if (done === null) return total === null ? null : total;
  return total === null ? done : `${done} of ${total}`;
}

/**
 * @typedef {object} ReaderProgressView
 * @property {string|null} jobId
 * @property {string|null} phase        raw phase as reported, null when absent
 * @property {string} label             always non-empty
 * @property {string|null} message      backend detail, e.g. the file being fetched
 * @property {number|null} bytesDone
 * @property {number|null} bytesTotal
 * @property {number|null} pct          null means indeterminate, never 0-as-unknown
 * @property {boolean} indeterminate    true when pct is null for an in-flight phase
 * @property {boolean} done
 * @property {boolean} failed
 * @property {string|null} error
 * @property {string|null} tier         model tier name once the detect phase picks one
 * @property {string} line              one-line status summary, always renderable
 */

/**
 * Normalize a polled status object into everything the panel renders. Total over
 * any input: `{}`, null, a string, or a half-published status all produce a
 * usable view.
 * @param {unknown} status
 * @returns {ReaderProgressView}
 */
export function normalizeStatus(status) {
  const s = /** @type {Record<string, unknown>} */ (
    status && typeof status === 'object' ? status : {}
  );
  const phase = textOrNull(s.phase);
  const error = textOrNull(s.error);
  const failed = phase === 'error' || error !== null;
  const done = !failed && (phase === 'done' || s.done === true);
  const pct = failed ? null : statusPct(s);

  const view = {
    jobId: textOrNull(s.job_id),
    phase,
    label: failed ? PHASE_LABELS.error : phaseLabel(done ? 'done' : phase),
    message: textOrNull(s.message),
    bytesDone: finiteOrNull(s.bytes_done),
    bytesTotal: finiteOrNull(s.bytes_total),
    pct,
    indeterminate: !done && !failed && pct === null,
    done,
    failed,
    error,
    // `tier` is the field name; `tier_name` is accepted because the job registry
    // and the tier table spell the same value differently.
    tier: textOrNull(s.tier) ?? textOrNull(s.tier_name),
    line: '',
  };
  view.line = buildLine(view);
  return view;
}

/**
 * label — bytes (pct), skipping every segment it has no value for.
 * @param {ReaderProgressView} view
 * @returns {string}
 */
function buildLine(view) {
  if (view.failed) {
    return view.error === null ? view.label : `${view.label} ${view.error}`;
  }
  if (view.done) return view.label;

  const parts = [];
  const bytes = formatByteProgress(view.bytesDone, view.bytesTotal);
  if (bytes !== null) parts.push(bytes);
  const pct = formatPct(view.pct);
  if (pct !== null) parts.push(`(${pct})`);
  return parts.length === 0 ? view.label : `${view.label} ${parts.join(' ')}`;
}

/**
 * One-line summary for the status line under the progress bar. Guaranteed
 * non-empty and free of "null"/"undefined"/"NaN" for any input.
 * @param {unknown} status
 * @returns {string}
 */
export function formatStatusLine(status) {
  return normalizeStatus(status).line;
}

/**
 * Should the panel keep polling? False once the job reports done or error.
 * @param {unknown} status
 * @returns {boolean}
 */
export function shouldKeepPolling(status) {
  const view = normalizeStatus(status);
  return !view.done && !view.failed;
}
