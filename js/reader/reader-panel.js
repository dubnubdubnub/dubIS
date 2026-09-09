// @ts-check
/* js/reader/reader-panel.js — the picture/PDF reader block in the Preferences
   modal: mode select, endpoint field, Install (with a polled progress bar) and
   Uninstall (behind a confirm that names the directory and the bytes).

   Why the Preferences modal and not the header: `.header` is `flex-wrap: wrap`,
   so one more control there adds a whole wrapped row at narrow widths and shifts
   every panel down — enough to shove the Import button off an 800x600 viewport.
   See the CLAUDE.md header trap and tests/js/e2e/resize-visibility.spec.mjs.

   Why there is no "restart to apply" here: `setReaderMode`'s contract is that
   the reader is chosen per import, unlike `server_url`, which is decided once at
   launch. Prompting for a restart would be a lie about how the setting works.

   Division of labour:
     - js/reader/reader-progress-logic.js  pure: phase → label, bytes, pct
     - this file                           DOM, the poll timer, the api() calls

   Every string that reaches the screen goes through `normalizeStatus`, which is
   total over garbage input, so a poll that races the job's first published
   update cannot put "undefined" in the status line.

   The reader install/uninstall calls live on the pywebview client shell rather
   than /v1: the local reader has to install on the *client* machine, and in
   remote-backend mode there is no local /v1 at all (see the transport section of
   docs/plans/2026-08-21-cross-platform-reader-design.md). They are reached
   through api()'s string-keyed form — one of the two conventions in use across
   js/ (see js/store.js) — and are deliberately absent from js/api-map.js, so
   api() falls through to window.pywebview.api instead of issuing a fetch. */

import { api, AppLog } from '../api.js';
import { showToast } from '../ui-helpers.js';
import { READER_MODES, getReaderMode, setReaderMode, getReaderUrl, setReaderUrl } from '../store.js';
import { normalizeStatus, shouldKeepPolling, formatBytes, formatPct } from './reader-progress-logic.js';

/** Poll cadence while an install runs. Fast enough that the bar moves, slow
    enough that a bridge round trip per tick is not the bottleneck. */
const POLL_MS = 700;

/** Consecutive failed status calls tolerated before the panel gives up.
    api() swallows a failed call to `undefined` (it has already logged + toasted),
    so a single blip is indistinguishable from a wedged bridge; retrying a couple
    of times beats both giving up on one hiccup and polling a dead job forever. */
const MAX_STATUS_FAILURES = 3;

/** Modes for which `reader_url` is consulted at all. */
const URL_MODES = ['remote', 'auto'];

/** What each mode actually does, in the operator's terms. */
const MODE_HINTS = {
  off: 'Pictures and PDFs are read as plain text only. Nothing is downloaded.',
  local: 'Reads on this computer, using the model installed below.',
  remote: 'Reads on another machine. Blank address = find one automatically.',
  auto: 'Prefers this computer, and falls back to another machine when the local reader is missing or not running.',
};

// ── Module state ────────────────────────────────────────────────────────────
// Deliberately module-scoped, not per-open: an install outlives the modal. The
// user closes Preferences, the download keeps going in the backend, and reopening
// must re-attach to the same job rather than start a second multi-GiB fetch.

/** @type {ReturnType<typeof setTimeout>|null} */
let _timer = null;
/** @type {string|null} the job being polled; survives the panel closing */
let _jobId = null;
/** @type {unknown} last status object polled, replayed on reopen */
let _lastStatus = null;
/** @type {ReaderLocalStatus|null} last `get_reader_status` answer */
let _local = null;
let _statusFailures = 0;
let _wired = false;

/**
 * @typedef {object} ReaderLocalStatus
 * @property {boolean} installed  a local reader exists on this machine
 * @property {string} path        the managed directory, "" when unknown
 * @property {number|null} bytesTotal  installed size, null when unknown
 * @property {number|null} fileCount
 * @property {boolean} running    a local reader process is up
 * @property {string} endpoint    its base URL, "" when not running
 * @property {string} jobId       an install still in flight, "" when none
 */

// ── DOM ─────────────────────────────────────────────────────────────────────

/** @param {string} id @returns {HTMLElement|null} */
function el(id) {
  return document.getElementById(id);
}

/** @returns {HTMLSelectElement|null} */
function modeSelect() {
  return /** @type {HTMLSelectElement|null} */ (el('pref-reader-mode'));
}

/** @returns {HTMLInputElement|null} */
function urlInput() {
  return /** @type {HTMLInputElement|null} */ (el('pref-reader-url'));
}

// ── Pure-ish readers of backend payloads ────────────────────────────────────
// The shapes below are owned by client_shell.py (start_reader_install,
// get_reader_status, uninstall_reader). Both are read key-by-key with fallbacks
// rather than destructured: a spelling difference must degrade into "unknown"
// and a still-usable panel, never into a crash or the string "undefined" on
// screen.

/** @param {unknown} v @returns {number|null} */
function num(v) {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** @param {unknown} v @returns {string} */
function str(v) {
  return typeof v === 'string' ? v.trim() : '';
}

/** First non-empty string among the named keys. @param {Record<string, unknown>} o @param {string[]} keys */
function pickStr(o, keys) {
  for (const k of keys) {
    const s = str(o[k]);
    if (s) return s;
  }
  return '';
}

/** First finite number among the named keys. @param {Record<string, unknown>} o @param {string[]} keys */
function pickNum(o, keys) {
  for (const k of keys) {
    const n = num(o[k]);
    if (n !== null) return n;
  }
  return null;
}

/**
 * The job id out of whatever `start_reader_install` answered: the bare id, or a
 * dict carrying it, or a full initial status dict. "" when there is none, which
 * the caller reports as a failed start rather than polling `undefined` forever.
 * @param {unknown} result
 * @returns {string}
 */
export function jobIdFrom(result) {
  if (typeof result === 'string') return result.trim();
  if (!result || typeof result !== 'object') return '';
  const o = /** @type {Record<string, unknown>} */ (result);
  const direct = pickStr(o, ['job_id', 'jobId', 'id']);
  if (direct) return direct;
  // A wrapper around the initial status, e.g. {"status": {...}}.
  const nested = o.status;
  if (nested && typeof nested === 'object') {
    return pickStr(/** @type {Record<string, unknown>} */ (nested), ['job_id', 'jobId', 'id']);
  }
  return '';
}

/**
 * Normalize `get_reader_status` into what this panel renders. Accepts the flat
 * shape and a `{local: {...}}` nesting, because the local half of the answer is
 * the only half this panel installs or deletes.
 * @param {unknown} raw
 * @returns {ReaderLocalStatus}
 */
export function readerStatusView(raw) {
  const top = /** @type {Record<string, unknown>} */ (
    raw && typeof raw === 'object' ? raw : {}
  );
  const nested = top.local && typeof top.local === 'object'
    ? /** @type {Record<string, unknown>} */ (top.local)
    : {};
  /** @param {string[]} keys */
  const s = (keys) => pickStr(nested, keys) || pickStr(top, keys);
  /** @param {string[]} keys */
  const n = (keys) => {
    const fromNested = pickNum(nested, keys);
    return fromNested !== null ? fromNested : pickNum(top, keys);
  };
  /** @param {string[]} keys */
  const b = (keys) => {
    for (const k of keys) {
      if (typeof nested[k] === 'boolean') return nested[k];
      if (typeof top[k] === 'boolean') return top[k];
    }
    return null;
  };

  const bytesTotal = n(['bytes_total', 'bytes', 'size_bytes', 'bytes_installed']);
  const endpoint = s(['endpoint', 'url', 'base_url']);
  const explicitInstalled = b(['installed', 'exists']);
  return {
    // A reported flag wins; absent, a measured size is the evidence. Note the
    // asymmetry: a *false* flag is believed even when bytes were reported, so a
    // backend that measures a stray empty directory cannot offer an Uninstall
    // for a reader it has already said is not installed.
    installed: explicitInstalled !== null ? explicitInstalled : (bytesTotal !== null && bytesTotal > 0),
    path: s(['install_dir', 'path', 'dir', 'directory']),
    bytesTotal,
    fileCount: n(['file_count', 'files']),
    running: b(['server_running', 'running']) ?? endpoint !== '',
    endpoint,
    jobId: s(['job_id', 'active_job_id', 'install_job_id']),
  };
}

/**
 * The confirm text for Uninstall. The design doc makes this non-negotiable:
 * this deletes multiple GiB of the user's disk, so the prompt names the exact
 * directory and the space it frees before anything is removed. It also says
 * what does NOT happen — a remote reader is untouched — because "uninstall the
 * reader" could otherwise read as "turn off reading pictures everywhere".
 * @param {ReaderLocalStatus} view
 * @returns {string}
 */
export function uninstallConfirmText(view) {
  const size = formatBytes(view.bytesTotal);
  const files = view.fileCount !== null && view.fileCount > 0
    ? ` in ${view.fileCount} file${view.fileCount === 1 ? '' : 's'}`
    : '';
  const lines = [
    'Delete the picture/PDF reader from this computer?',
    '',
    'This permanently deletes the folder:',
    `    ${view.path || '(the reader folder under the dubIS data directory)'}`,
    size === null
      ? '    size unknown — everything under that folder goes'
      : `    freeing ${size}${files}`,
  ];
  if (view.running) {
    lines.push('', 'The reader is running now and will be stopped first.');
  }
  lines.push(
    '',
    'Only this computer’s copy is removed — a reader on another machine keeps '
    + 'working, and Automatic falls back to it. Installing again downloads the '
    + 'whole model over.',
  );
  return lines.join('\n');
}

/** @param {string} mode @returns {string} */
export function readerModeHint(mode) {
  return MODE_HINTS[mode] || MODE_HINTS.off;
}

// ── Rendering ───────────────────────────────────────────────────────────────

/**
 * Paint the bar, the percentage and the status line from a polled status.
 * `null` clears the whole block back to "nothing to report".
 * @param {unknown} status
 */
function renderProgress(status) {
  const wrap = el('reader-progress-wrap');
  const bar = el('reader-progress');
  const fill = el('reader-progress-fill');
  const pct = el('reader-progress-pct');
  const line = el('reader-status-line');
  if (!wrap || !bar || !fill || !pct || !line) return;

  if (status === null || status === undefined) {
    wrap.classList.add('hidden');
    bar.classList.remove('is-indeterminate', 'is-failed');
    bar.removeAttribute('aria-valuenow');
    fill.style.width = '0';
    pct.textContent = '';
    line.textContent = '';
    line.classList.remove('is-failed', 'is-done');
    return;
  }

  const view = normalizeStatus(status);
  wrap.classList.remove('hidden');
  bar.classList.toggle('is-failed', view.failed);
  // Indeterminate: no percentage anywhere. A bar parked at 0% reads as "stuck"
  // when the truth is "working, length unknown" — the CSS gives this a travelling
  // sliver instead, and the readout stays blank rather than saying "0%".
  bar.classList.toggle('is-indeterminate', view.indeterminate);
  if (view.indeterminate) {
    fill.style.width = '';
    bar.removeAttribute('aria-valuenow');
    bar.setAttribute('aria-valuetext', view.label);
    pct.textContent = '';
  } else {
    const p = view.pct === null ? 0 : view.pct;
    fill.style.width = `${p}%`;
    bar.setAttribute('aria-valuenow', String(Math.round(p)));
    bar.removeAttribute('aria-valuetext');
    pct.textContent = formatPct(view.pct) ?? '';
  }
  line.textContent = view.line;
  line.classList.toggle('is-failed', view.failed);
  line.classList.toggle('is-done', view.done);
}

/** Reflect the local-install answer onto the buttons and the one-line summary. */
function renderLocal() {
  const installBtn = /** @type {HTMLButtonElement|null} */ (el('reader-install-btn'));
  const uninstallBtn = /** @type {HTMLButtonElement|null} */ (el('reader-uninstall-btn'));
  const status = el('reader-local-status');
  const busy = _jobId !== null;

  if (installBtn) {
    installBtn.disabled = busy;
    installBtn.textContent = busy
      ? 'Installing…'
      : (_local && _local.installed ? 'Reinstall on this computer' : 'Install on this computer');
  }
  if (uninstallBtn) {
    // Hidden, not merely disabled, when there is nothing installed: an Uninstall
    // button for a reader that does not exist invites a click that can only fail.
    uninstallBtn.classList.toggle('hidden', !(_local && _local.installed));
    uninstallBtn.disabled = busy;
  }
  if (status) {
    if (!_local) {
      status.textContent = '';
    } else if (!_local.installed) {
      status.textContent = 'not installed on this computer';
    } else {
      const size = formatBytes(_local.bytesTotal);
      const where = _local.running ? 'installed and running' : 'installed, not running';
      status.textContent = size === null ? where : `${where} — ${size}`;
    }
  }
}

/** Mode select + endpoint field + hint, from the store. */
function renderMode() {
  const mode = getReaderMode();
  const select = modeSelect();
  const input = urlInput();
  const field = el('reader-url-field');
  const hint = el('reader-mode-hint');

  if (select && document.activeElement !== select) select.value = mode;
  const urlUsed = URL_MODES.includes(mode);
  if (input) {
    if (document.activeElement !== input) input.value = getReaderUrl();
    input.disabled = !urlUsed;
  }
  if (field) field.classList.toggle('is-inert', !urlUsed);
  if (hint) hint.textContent = readerModeHint(mode);
}

// ── Polling ─────────────────────────────────────────────────────────────────

/**
 * Stop the poll timer. Called when the Preferences modal closes: the install
 * keeps running in the backend, and `_jobId` is kept so reopening re-attaches to
 * the same job instead of starting a second multi-GiB download.
 */
export function stopReaderPolling() {
  if (_timer !== null) {
    clearTimeout(_timer);
    _timer = null;
  }
}

function scheduleNextPoll() {
  stopReaderPolling();
  if (_jobId === null) return;
  _timer = setTimeout(() => { pollOnce(); }, POLL_MS);
}

/** Land a terminal (or given-up) install: drop the job, refresh the local view. */
function finishJob() {
  stopReaderPolling();
  _jobId = null;
  _statusFailures = 0;
  renderLocal();
  refreshLocalStatus();
}

async function pollOnce() {
  _timer = null;
  const jobId = _jobId;
  if (jobId === null) return;

  const raw = await api('get_reader_install_status', jobId);
  // The job may have been abandoned (Uninstall, or a terminal status from a poll
  // that overlapped this one) while this call was in flight.
  if (_jobId !== jobId) return;

  if (raw === undefined || raw === null) {
    // api() already logged and toasted the failure; decide whether to keep going.
    _statusFailures += 1;
    if (_statusFailures >= MAX_STATUS_FAILURES) {
      AppLog.error('reader: giving up polling install ' + jobId + ' after '
        + _statusFailures + ' failed status calls');
      _lastStatus = {
        job_id: jobId,
        phase: 'error',
        error: 'lost contact with the install job',
      };
      renderProgress(_lastStatus);
      finishJob();
      return;
    }
    scheduleNextPoll();
    return;
  }

  _statusFailures = 0;
  _lastStatus = raw;
  renderProgress(raw);

  if (shouldKeepPolling(raw)) {
    scheduleNextPoll();
    return;
  }

  const view = normalizeStatus(raw);
  if (view.failed) {
    AppLog.error('reader install failed: ' + (view.error || view.label));
    showToast('Reader install failed — ' + (view.error || view.label));
  } else {
    AppLog.info('reader install finished'
      + (view.tier ? ' (' + view.tier + ')' : ''));
    showToast('The picture/PDF reader is ready');
  }
  finishJob();
}

/** Ask the backend what is installed locally, and re-render from the answer. */
async function refreshLocalStatus() {
  const raw = await api('get_reader_status');
  if (raw === undefined || raw === null) {
    // Inconclusive, not "nothing installed": leave whatever we last knew, and
    // do not offer an Uninstall based on a failed check.
    return;
  }
  _local = readerStatusView(raw);
  // An install this window never started — a previous run of the app, or the
  // panel reopened after a restart. Adopt it so the bar tracks the real job.
  if (_jobId === null && _local.jobId) {
    _jobId = _local.jobId;
    _statusFailures = 0;
    pollOnce();
  }
  renderLocal();
}

// ── Actions ─────────────────────────────────────────────────────────────────

async function onInstallClick() {
  if (_jobId !== null) return;   // already running; the button is disabled anyway
  const result = await api('start_reader_install');
  const jobId = jobIdFrom(result);
  if (!jobId) {
    // api() has already surfaced a thrown error; this covers the quieter case of
    // a call that returned something with no job id in it.
    AppLog.error('reader: start_reader_install returned no job id');
    showToast('Could not start the reader install');
    return;
  }
  _jobId = jobId;
  _statusFailures = 0;
  AppLog.info('reader install started (job ' + jobId + ')');
  // Paint immediately rather than after the first poll: the first phase is the
  // memory probe, which reports no percentage, so the bar must already be
  // visible and busy before any bytes exist.
  _lastStatus = typeof result === 'object' && result !== null && 'phase' in result
    ? result
    : { job_id: jobId, phase: 'detect' };
  renderProgress(_lastStatus);
  renderLocal();
  await pollOnce();
}

async function onUninstallClick() {
  if (_jobId !== null) {
    showToast('Wait for the install to finish before uninstalling');
    return;
  }
  // Re-read rather than trusting the cached view: the confirm is about to name a
  // byte total, and naming a stale one is exactly the mistake this dialog exists
  // to prevent.
  await refreshLocalStatus();
  if (!_local || !_local.installed) {
    showToast('No reader is installed on this computer');
    return;
  }
  if (!window.confirm(uninstallConfirmText(_local))) return;

  const result = await api('uninstall_reader');
  if (result === undefined || result === null) {
    // api() already logged + toasted.
    await refreshLocalStatus();
    return;
  }
  const o = /** @type {Record<string, unknown>} */ (result);
  const reclaimed = pickNum(o, ['bytes_reclaimed', 'bytes_total', 'bytes']);
  const size = formatBytes(reclaimed);
  showToast(size === null ? 'Reader removed' : `Reader removed — ${size} freed`);
  AppLog.info('reader uninstalled from ' + (pickStr(o, ['path']) || 'the reader folder')
    + (size === null ? '' : ' (' + size + ' reclaimed)'));
  _lastStatus = null;
  renderProgress(null);
  await refreshLocalStatus();
}

// ── Wiring / lifecycle ──────────────────────────────────────────────────────

/**
 * One-time DOM wiring. Idempotent, and a no-op when the reader block is absent
 * (a page that does not carry the Preferences modal).
 */
export function wireReaderPanel() {
  if (_wired) return;
  const select = modeSelect();
  if (!select) return;
  _wired = true;

  select.addEventListener('change', () => {
    // setReaderMode returns what it actually stored, and coerces anything it
    // does not recognise to "off" — echo that back rather than leaving a value
    // in the select that the store rejected.
    const stored = setReaderMode(select.value);
    if (!READER_MODES.includes(select.value)) {
      AppLog.warn('reader: unknown mode ' + JSON.stringify(select.value) + ' — stored ' + stored);
    }
    renderMode();
  });

  const input = urlInput();
  if (input) {
    // 'change' (blur or Enter), not 'input': every keystroke of a URL passes
    // through prefixes that normalizeReaderUrl rejects, and persisting those
    // would blank the field mid-type.
    input.addEventListener('change', () => {
      const typed = input.value.trim();
      const stored = setReaderUrl(typed);
      if (typed && !stored) {
        showToast('Reader address must start with http:// or https://');
      }
      input.value = stored;
      renderMode();
    });
  }

  const installBtn = el('reader-install-btn');
  if (installBtn) installBtn.addEventListener('click', () => { onInstallClick(); });
  const uninstallBtn = el('reader-uninstall-btn');
  if (uninstallBtn) uninstallBtn.addEventListener('click', () => { onUninstallClick(); });
}

/**
 * Called every time the Preferences modal opens. Repaints from the store, replays
 * the last status we saw, and re-attaches the poll timer when an install this
 * window started is still running.
 */
export function syncReaderPanel() {
  if (!modeSelect()) return;
  renderMode();
  renderProgress(_lastStatus);
  renderLocal();
  refreshLocalStatus();
  if (_jobId !== null && _timer === null) pollOnce();
}

/** Test seam: forget the module state between cases. */
export function _resetReaderPanelForTests() {
  stopReaderPolling();
  _jobId = null;
  _lastStatus = null;
  _local = null;
  _statusFailures = 0;
  _wired = false;
}
