// @vitest-environment jsdom
/* Unit tests for js/reader/reader-panel.js — the DOM/polling half of the reader
   install UI. The pure half (phase → label, bytes, pct) is
   js/reader/reader-progress-logic.js and is tested in
   tests/js/reader-progress-logic.test.js; nothing here re-tests it.

   What is worth pinning here is everything the pure module cannot see:

     - an indeterminate phase must render a *busy* bar and NO percentage. A bar
       parked at 0% reads as "stuck" when the truth is "working, length unknown".
     - closing Preferences mid-install must clear the timer (no polling a hidden
       modal forever) and reopening must re-attach to the SAME job — starting a
       second multi-GiB download is the failure this exists to prevent.
     - the Uninstall confirm must name the directory and the reclaimed bytes
       before anything is deleted, and must not fire the delete when declined.
     - a status call that keeps failing must give up rather than poll forever;
       api() swallows failures to undefined, so the panel is the only thing that
       can notice.

   The DOM fixture is sliced out of the real index.html rather than retyped, so a
   renamed id fails here instead of only in Playwright. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// ── Mocks ───────────────────────────────────────────────────────────────────

const api = vi.fn();
const AppLog = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
vi.mock('../../js/api.js', () => ({
  api: (...a) => api(...a),
  AppLog: {
    info: (...a) => AppLog.info(...a),
    warn: (...a) => AppLog.warn(...a),
    error: (...a) => AppLog.error(...a),
  },
}));

const showToast = vi.fn();
vi.mock('../../js/ui-helpers.js', () => ({
  showToast: (...a) => showToast(...a),
  escHtml: (s) => s || '',
}));

// A real-enough store: the coercion rules matter to two of the tests (an
// unschemed URL is rejected to ""), and they are store.js's documented contract.
const prefs = { reader_mode: 'off', reader_url: '' };
vi.mock('../../js/store.js', () => ({
  READER_MODES: ['off', 'local', 'remote', 'auto'],
  getReaderMode: () => prefs.reader_mode,
  setReaderMode: (m) => {
    prefs.reader_mode = ['off', 'local', 'remote', 'auto'].includes(m) ? m : 'off';
    return prefs.reader_mode;
  },
  getReaderUrl: () => prefs.reader_url,
  setReaderUrl: (u) => {
    const t = String(u ?? '').trim();
    prefs.reader_url = /^https?:\/\/[^\s/?#]+/i.test(t) ? t.replace(/\/+$/, '') : '';
    return prefs.reader_url;
  },
}));

const panel = await import('../../js/reader/reader-panel.js');
const {
  wireReaderPanel, syncReaderPanel, stopReaderPolling, _resetReaderPanelForTests,
  jobIdFrom, readerStatusView, uninstallConfirmText, readerModeHint,
} = panel;

// ── The real markup, sliced out of index.html ───────────────────────────────

/** The `<div id="reader-prefs">…</div>` subtree from index.html, verbatim. */
function readerPrefsMarkup() {
  // process.cwd() is the repo root under vitest; import.meta.url is not a file://
  // URL in the jsdom environment.
  const html = readFileSync(join(process.cwd(), 'index.html'), 'utf8');
  const anchor = html.indexOf('id="reader-prefs"');
  expect(anchor, 'index.html must carry #reader-prefs').toBeGreaterThan(-1);
  const start = html.lastIndexOf('<div', anchor);
  // Walk div opens/closes from `start` until the depth returns to zero, so the
  // slice is the whole subtree and not the first </div> inside it.
  const tag = /<div\b|<\/div>/g;
  tag.lastIndex = start;
  let depth = 0;
  let m;
  while ((m = tag.exec(html)) !== null) {
    depth += m[0] === '</div>' ? -1 : 1;
    if (depth === 0) return html.slice(start, m.index + m[0].length);
  }
  throw new Error('reader-prefs subtree is unbalanced in index.html');
}

const MARKUP = readerPrefsMarkup();

// ── Fixtures ────────────────────────────────────────────────────────────────

/** A polled install status, with the keys reader_jobs.py actually publishes. */
function status(over = {}) {
  return {
    job_id: 'job-1',
    phase: 'weights',
    message: 'Downloading the model',
    bytes_done: 512,
    bytes_total: 1024,
    pct: 50.0,
    indeterminate: false,
    done: false,
    error: null,
    tier: 'qwen2.5-vl-7b',
    endpoint: '',
    install_dir: '/data/reader',
    phase_history: ['detect', 'runtime', 'weights'],
    elapsed_s: 1.0,
    ...over,
  };
}

const NOT_INSTALLED = {
  installed: false, path: '/data/reader', bytes_total: 0, file_count: 0,
  server_running: false, endpoint: '', job_id: '',
};

const INSTALLED = {
  installed: true, path: '/data/reader', bytes_total: 5046586573, file_count: 3,
  server_running: true, endpoint: 'http://127.0.0.1:8081', job_id: '',
};

const $ = (id) => document.getElementById(id);

/** Default api() behaviour: nothing installed, no job running. */
function defaultApi() {
  api.mockImplementation(async (method) => {
    if (method === 'get_reader_status') return NOT_INSTALLED;
    return undefined;
  });
}

/** Mount the panel and let syncReaderPanel's initial status call settle. */
async function mount() {
  document.body.innerHTML = `<div id="prefs-modal">${MARKUP}</div>`;
  wireReaderPanel();
  syncReaderPanel();
  await vi.advanceTimersByTimeAsync(0);
}

beforeEach(() => {
  vi.useFakeTimers();
  api.mockReset();
  showToast.mockReset();
  AppLog.info.mockReset();
  AppLog.warn.mockReset();
  AppLog.error.mockReset();
  prefs.reader_mode = 'off';
  prefs.reader_url = '';
  _resetReaderPanelForTests();
  defaultApi();
});

afterEach(() => {
  stopReaderPolling();
  vi.useRealTimers();
});

/** Every api() call made for `method`, in order. */
const callsTo = (method) => api.mock.calls.filter(c => c[0] === method);

// ── Pure helpers ────────────────────────────────────────────────────────────

describe('jobIdFrom — whatever start_reader_install answered', () => {
  it('accepts the bare id, a dict carrying it, and a full initial status', () => {
    expect(jobIdFrom('job-7')).toBe('job-7');
    expect(jobIdFrom({ job_id: 'job-7' })).toBe('job-7');
    expect(jobIdFrom({ jobId: 'job-7' })).toBe('job-7');
    expect(jobIdFrom({ id: 'job-7' })).toBe('job-7');
    expect(jobIdFrom(status({ job_id: 'job-7' }))).toBe('job-7');
    expect(jobIdFrom({ status: { job_id: 'job-7' } })).toBe('job-7');
  });

  it('returns "" rather than a fake id for anything unusable', () => {
    // The caller reports a failed start on "", which is the only honest outcome:
    // polling `undefined` forever is the alternative.
    for (const bad of [undefined, null, '', '   ', 42, {}, { job_id: '' }, [1]]) {
      expect(jobIdFrom(bad)).toBe('');
    }
  });
});

describe('readerStatusView — get_reader_status, read defensively', () => {
  it('reads the flat shape', () => {
    const v = readerStatusView(INSTALLED);
    expect(v).toMatchObject({
      installed: true, path: '/data/reader', bytesTotal: 5046586573,
      fileCount: 3, running: true, endpoint: 'http://127.0.0.1:8081',
    });
  });

  it('reads a {local: {...}} nesting, since only the local half is installable', () => {
    const v = readerStatusView({ mode: 'auto', local: INSTALLED });
    expect(v.installed).toBe(true);
    expect(v.path).toBe('/data/reader');
    expect(v.bytesTotal).toBe(5046586573);
  });

  it('accepts `exists` and a measured size as evidence of an install', () => {
    expect(readerStatusView({ exists: true, path: '/x' }).installed).toBe(true);
    expect(readerStatusView({ bytes_total: 999 }).installed).toBe(true);
  });

  it('believes an explicit installed:false even when a size is reported', () => {
    // A backend that measures a stray empty directory must not make the panel
    // offer to delete a reader it has already said is not there.
    expect(readerStatusView({ installed: false, bytes_total: 4096 }).installed).toBe(false);
  });

  it('degrades to "unknown", never to a crash, on garbage', () => {
    for (const bad of [undefined, null, 'nope', 7, []]) {
      const v = readerStatusView(bad);
      expect(v.installed).toBe(false);
      expect(v.path).toBe('');
      expect(v.bytesTotal).toBe(null);
      expect(v.jobId).toBe('');
    }
  });

  it('infers running from an endpoint when no flag is sent', () => {
    expect(readerStatusView({ endpoint: 'http://127.0.0.1:9' }).running).toBe(true);
    expect(readerStatusView({ endpoint: '' }).running).toBe(false);
  });

  it('surfaces an in-flight job under any of its spellings', () => {
    expect(readerStatusView({ job_id: 'a' }).jobId).toBe('a');
    expect(readerStatusView({ active_job_id: 'b' }).jobId).toBe('b');
    expect(readerStatusView({ install_job_id: 'c' }).jobId).toBe('c');
  });
});

describe('uninstallConfirmText — the prompt before GiB are deleted', () => {
  const text = uninstallConfirmText(readerStatusView(INSTALLED));

  it('names the exact directory', () => {
    expect(text).toContain('/data/reader');
  });

  it('names the space it frees, and the file count', () => {
    expect(text).toContain('4.7 GiB');
    expect(text).toContain('3 files');
  });

  it('warns that the running reader is stopped first', () => {
    expect(text).toContain('running');
    expect(uninstallConfirmText(readerStatusView({ ...INSTALLED, server_running: false })))
      .not.toContain('will be stopped');
  });

  it('says a remote reader is unaffected — this is local-only', () => {
    expect(text).toMatch(/another machine/);
  });

  it('never prints null/undefined/NaN when the size is unknown', () => {
    const t = uninstallConfirmText(readerStatusView({ installed: true, path: '/p' }));
    expect(t).toContain('/p');
    expect(t).not.toMatch(/null|undefined|NaN/);
  });

  it('still names a folder when the backend sent no path', () => {
    const t = uninstallConfirmText(readerStatusView({ installed: true }));
    expect(t).not.toMatch(/null|undefined/);
    expect(t.length).toBeGreaterThan(20);
  });
});

describe('readerModeHint', () => {
  it('has a hint for every mode, and falls back for an unknown one', () => {
    for (const m of ['off', 'local', 'remote', 'auto']) {
      expect(readerModeHint(m).length).toBeGreaterThan(10);
    }
    expect(readerModeHint('bogus')).toBe(readerModeHint('off'));
  });
});

// ── Mode + endpoint controls ────────────────────────────────────────────────

describe('mode select and endpoint field', () => {
  it('shows the stored mode and greys the endpoint field when it is not consulted', async () => {
    prefs.reader_mode = 'local';
    await mount();
    expect($('pref-reader-mode').value).toBe('local');
    expect($('pref-reader-url').disabled).toBe(true);
    expect($('reader-url-field').classList.contains('is-inert')).toBe(true);
  });

  it('enables the endpoint field for remote and auto', async () => {
    for (const m of ['remote', 'auto']) {
      prefs.reader_mode = m;
      _resetReaderPanelForTests();
      await mount();
      expect($('pref-reader-url').disabled, m).toBe(false);
      expect($('reader-url-field').classList.contains('is-inert'), m).toBe(false);
    }
  });

  it('persists a mode change and re-renders the hint — with no restart prompt', async () => {
    await mount();
    const select = $('pref-reader-mode');
    select.value = 'auto';
    select.dispatchEvent(new Event('change'));
    expect(prefs.reader_mode).toBe('auto');
    expect($('reader-mode-hint').textContent).toBe(readerModeHint('auto'));
    // setReaderMode's contract is that the reader is chosen per import, unlike
    // server_url, which is decided once at launch and has a "Restart to apply"
    // button. Offering one here would be a lie about how the setting works, so
    // the block carries no restart control and no restart prompt — only the
    // static hint that says a change needs no restart.
    expect($('reader-prefs').querySelector('[id*="restart"]')).toBe(null);
    expect($('reader-prefs').textContent).not.toMatch(/restart to apply|pending restart/i);
    expect(showToast).not.toHaveBeenCalled();
  });

  it('echoes back a rejected endpoint instead of leaving it looking saved', async () => {
    prefs.reader_mode = 'remote';
    await mount();
    const input = $('pref-reader-url');
    input.value = 'y740:8080';
    input.dispatchEvent(new Event('change'));
    expect(prefs.reader_url).toBe('');
    expect(input.value).toBe('');
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('http://'));
  });

  it('stores a well-formed endpoint and keeps it in the field', async () => {
    prefs.reader_mode = 'remote';
    await mount();
    const input = $('pref-reader-url');
    input.value = 'http://y740:8080/';
    input.dispatchEvent(new Event('change'));
    expect(prefs.reader_url).toBe('http://y740:8080');
    expect(input.value).toBe('http://y740:8080');
    expect(showToast).not.toHaveBeenCalled();
  });
});

// ── Local status → buttons ──────────────────────────────────────────────────

describe('local install status', () => {
  it('hides Uninstall when nothing is installed', async () => {
    await mount();
    expect($('reader-uninstall-btn').classList.contains('hidden')).toBe(true);
    expect($('reader-local-status').textContent).toContain('not installed');
  });

  it('shows Uninstall, the size and the run state when something is installed', async () => {
    api.mockImplementation(async (m) => (m === 'get_reader_status' ? INSTALLED : undefined));
    await mount();
    expect($('reader-uninstall-btn').classList.contains('hidden')).toBe(false);
    expect($('reader-install-btn').textContent).toContain('Reinstall');
    expect($('reader-local-status').textContent).toContain('4.7 GiB');
    expect($('reader-local-status').textContent).toContain('running');
  });

  it('does not offer Uninstall on an inconclusive status check', async () => {
    // api() swallows a failed call to undefined. Offering to delete GiB off the
    // back of a failed check is exactly the wrong reading of "no answer".
    api.mockImplementation(async () => undefined);
    await mount();
    expect($('reader-uninstall-btn').classList.contains('hidden')).toBe(true);
    expect($('reader-local-status').textContent).toBe('');
  });
});

// ── Install + progress ──────────────────────────────────────────────────────

/** Script the poll sequence: one status per call, last one repeating. */
function scriptPolls(sequence, { localStatus = NOT_INSTALLED } = {}) {
  let i = 0;
  api.mockImplementation(async (method) => {
    if (method === 'get_reader_status') return localStatus;
    if (method === 'start_reader_install') return { job_id: 'job-1' };
    if (method === 'get_reader_install_status') {
      const s = sequence[Math.min(i, sequence.length - 1)];
      i += 1;
      return s;
    }
    return undefined;
  });
}

async function clickInstall() {
  $('reader-install-btn').click();
  await vi.advanceTimersByTimeAsync(0);
}

describe('Install: progress bar, percentage and status line', () => {
  it('renders a busy bar with NO percentage while the phase is indeterminate', async () => {
    scriptPolls([status({ phase: 'detect', bytes_total: null, pct: null, indeterminate: true })]);
    await mount();
    await clickInstall();

    const bar = $('reader-progress');
    expect($('reader-progress-wrap').classList.contains('hidden')).toBe(false);
    expect(bar.classList.contains('is-indeterminate')).toBe(true);
    // No percentage anywhere, and crucially no width:0 — the CSS owns the
    // travelling sliver, so an inline 0% here would park the bar at the start.
    expect($('reader-progress-pct').textContent).toBe('');
    expect($('reader-progress-fill').style.width).toBe('');
    expect(bar.hasAttribute('aria-valuenow')).toBe(false);
    expect(bar.getAttribute('aria-valuetext')).toContain('Checking what this computer can run');
    expect($('reader-status-line').textContent).toContain('Checking what this computer can run');
  });

  it('paints the bar before the first poll answers', async () => {
    // The first phase is the memory probe, which reports no bytes at all, so the
    // bar has to appear on the click — not one poll interval later.
    api.mockImplementation(async (method) => {
      if (method === 'get_reader_status') return NOT_INSTALLED;
      if (method === 'start_reader_install') return 'job-1';
      return new Promise(() => {});   // a status call that never answers
    });
    await mount();
    $('reader-install-btn').click();
    await Promise.resolve();
    await Promise.resolve();
    expect($('reader-progress-wrap').classList.contains('hidden')).toBe(false);
    expect($('reader-progress').classList.contains('is-indeterminate')).toBe(true);
  });

  it('renders width, percentage and byte progress for a byte-counted phase', async () => {
    scriptPolls([status()]);
    await mount();
    await clickInstall();

    expect($('reader-progress').classList.contains('is-indeterminate')).toBe(false);
    expect($('reader-progress-fill').style.width).toBe('50%');
    expect($('reader-progress-pct').textContent).toBe('50%');
    expect($('reader-progress').getAttribute('aria-valuenow')).toBe('50');
    expect($('reader-status-line').textContent).toContain('512 B of 1 KiB');
    expect($('reader-status-line').textContent).toContain('(50%)');
  });

  it('disables Install while a job runs and re-enables it when the job lands', async () => {
    scriptPolls([status(), status({ phase: 'done', done: true, pct: 100 })]);
    await mount();
    await clickInstall();
    expect($('reader-install-btn').disabled).toBe(true);

    await vi.advanceTimersByTimeAsync(1000);
    expect($('reader-install-btn').disabled).toBe(false);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('ready'));
  });

  it('stops polling on done and re-reads the local status', async () => {
    scriptPolls([status({ phase: 'done', done: true, pct: 100 })]);
    await mount();
    await clickInstall();
    const polls = callsTo('get_reader_install_status').length;

    await vi.advanceTimersByTimeAsync(5000);
    expect(callsTo('get_reader_install_status')).toHaveLength(polls);
    expect($('reader-status-line').classList.contains('is-done')).toBe(true);
    // The whole point of stopping: the panel now knows what got installed.
    expect(callsTo('get_reader_status').length).toBeGreaterThan(1);
  });

  it('stops polling on error, surfaces the reason, and marks the bar failed', async () => {
    scriptPolls([status({ phase: 'error', done: true, error: 'sha256 mismatch', pct: null })]);
    await mount();
    await clickInstall();
    const polls = callsTo('get_reader_install_status').length;

    await vi.advanceTimersByTimeAsync(5000);
    expect(callsTo('get_reader_install_status')).toHaveLength(polls);
    expect($('reader-progress').classList.contains('is-failed')).toBe(true);
    expect($('reader-status-line').textContent).toContain('sha256 mismatch');
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('sha256 mismatch'));
    expect(AppLog.error).toHaveBeenCalled();
  });

  it('reports a start that came back with no job id, instead of polling nothing', async () => {
    api.mockImplementation(async (method) => {
      if (method === 'get_reader_status') return NOT_INSTALLED;
      if (method === 'start_reader_install') return { ok: true };
      return undefined;
    });
    await mount();
    await clickInstall();
    expect(callsTo('get_reader_install_status')).toHaveLength(0);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Could not start'));
  });

  it('gives up after repeated failed status calls rather than polling forever', async () => {
    let n = 0;
    api.mockImplementation(async (method) => {
      if (method === 'get_reader_status') return NOT_INSTALLED;
      if (method === 'start_reader_install') return 'job-1';
      if (method === 'get_reader_install_status') { n += 1; return undefined; }
      return undefined;
    });
    await mount();
    await clickInstall();
    await vi.advanceTimersByTimeAsync(10_000);

    expect(n).toBe(3);
    expect($('reader-status-line').textContent).toContain('lost contact');
    expect($('reader-install-btn').disabled).toBe(false);
  });

  it('tolerates a single failed status call and keeps going', async () => {
    let n = 0;
    api.mockImplementation(async (method) => {
      if (method === 'get_reader_status') return NOT_INSTALLED;
      if (method === 'start_reader_install') return 'job-1';
      if (method === 'get_reader_install_status') {
        n += 1;
        if (n === 1) return undefined;
        return status({ phase: 'done', done: true, pct: 100 });
      }
      return undefined;
    });
    await mount();
    await clickInstall();
    await vi.advanceTimersByTimeAsync(2000);
    expect($('reader-status-line').classList.contains('is-done')).toBe(true);
  });
});

// ── Panel closed mid-install ────────────────────────────────────────────────

describe('a panel closed mid-install', () => {
  it('clears the poll timer, then re-attaches to the same job on reopen', async () => {
    scriptPolls([status(), status({ bytes_done: 768, pct: 75 }),
                 status({ phase: 'done', done: true, pct: 100 })]);
    await mount();
    await clickInstall();
    const pollsAtClose = callsTo('get_reader_install_status').length;

    // Preferences closes (Escape / Cancel / backdrop all land here).
    stopReaderPolling();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(callsTo('get_reader_install_status'),
      'a closed panel must not keep polling').toHaveLength(pollsAtClose);

    // Reopen: same job, no second install started, and polling resumes.
    syncReaderPanel();
    await vi.advanceTimersByTimeAsync(0);
    expect(callsTo('start_reader_install'),
      'reopening must not start a second multi-GiB download').toHaveLength(1);
    const resumed = callsTo('get_reader_install_status');
    expect(resumed.length).toBeGreaterThan(pollsAtClose);
    expect(resumed.every(c => c[1] === 'job-1')).toBe(true);

    await vi.advanceTimersByTimeAsync(5000);
    expect($('reader-status-line').classList.contains('is-done')).toBe(true);
  });

  it('replays the last status it saw, so the reopened panel is not blank', async () => {
    scriptPolls([status()]);
    await mount();
    await clickInstall();
    stopReaderPolling();

    document.body.innerHTML = '';           // the modal's DOM is re-rendered
    document.body.innerHTML = `<div id="prefs-modal">${MARKUP}</div>`;
    syncReaderPanel();
    await vi.advanceTimersByTimeAsync(0);
    expect($('reader-progress-wrap').classList.contains('hidden')).toBe(false);
    expect($('reader-progress-fill').style.width).toBe('50%');
  });

  it('adopts an install this window never started (e.g. after an app restart)', async () => {
    scriptPolls([status({ phase: 'projector', bytes_done: 900, pct: 88 })],
      { localStatus: { ...NOT_INSTALLED, job_id: 'job-1' } });
    await mount();
    // No click: the running job came back from get_reader_status.
    await vi.advanceTimersByTimeAsync(0);
    expect(callsTo('start_reader_install')).toHaveLength(0);
    expect(callsTo('get_reader_install_status').length).toBeGreaterThan(0);
    expect($('reader-progress-fill').style.width).toBe('88%');
  });
});

// ── Uninstall ───────────────────────────────────────────────────────────────

describe('Uninstall', () => {
  /** @param {boolean} answer what the operator clicks in the confirm */
  function stubConfirm(answer) {
    const spy = vi.fn().mockReturnValue(answer);
    window.confirm = spy;
    return spy;
  }

  function installedApi(uninstallResult = { path: '/data/reader', bytes_reclaimed: 5046586573 }) {
    let removed = false;
    api.mockImplementation(async (method) => {
      if (method === 'get_reader_status') return removed ? NOT_INSTALLED : INSTALLED;
      if (method === 'uninstall_reader') { removed = true; return uninstallResult; }
      return undefined;
    });
  }

  it('asks first, naming the directory and the bytes, and deletes nothing when declined', async () => {
    installedApi();
    const confirm = stubConfirm(false);
    await mount();
    $('reader-uninstall-btn').click();
    await vi.advanceTimersByTimeAsync(0);

    expect(confirm).toHaveBeenCalledTimes(1);
    const text = confirm.mock.calls[0][0];
    expect(text).toContain('/data/reader');
    expect(text).toContain('4.7 GiB');
    expect(callsTo('uninstall_reader'),
      'a declined confirm must not delete anything').toHaveLength(0);
  });

  it('re-reads the status before quoting a byte total in the confirm', async () => {
    installedApi();
    stubConfirm(false);
    await mount();
    const before = callsTo('get_reader_status').length;
    $('reader-uninstall-btn').click();
    await vi.advanceTimersByTimeAsync(0);
    expect(callsTo('get_reader_status').length).toBeGreaterThan(before);
  });

  it('deletes and reports the reclaimed space when accepted', async () => {
    installedApi();
    stubConfirm(true);
    await mount();
    $('reader-uninstall-btn').click();
    await vi.advanceTimersByTimeAsync(0);

    expect(callsTo('uninstall_reader')).toHaveLength(1);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('4.7 GiB'));
    // The button goes away with the thing it deleted.
    expect($('reader-uninstall-btn').classList.contains('hidden')).toBe(true);
    expect($('reader-progress-wrap').classList.contains('hidden')).toBe(true);
  });

  it('says so plainly when the backend reported no byte total', async () => {
    installedApi({ path: '/data/reader' });
    stubConfirm(true);
    await mount();
    $('reader-uninstall-btn').click();
    await vi.advanceTimersByTimeAsync(0);
    expect(showToast).toHaveBeenCalledWith('Reader removed');
  });

  it('cannot be fired while an install is running — the button is disabled', async () => {
    let removed = false;
    api.mockImplementation(async (method) => {
      if (method === 'get_reader_status') return removed ? NOT_INSTALLED : INSTALLED;
      if (method === 'start_reader_install') return 'job-1';
      if (method === 'get_reader_install_status') return status();
      if (method === 'uninstall_reader') { removed = true; return {}; }
      return undefined;
    });
    const confirm = stubConfirm(true);
    await mount();
    await clickInstall();

    // Deleting the tree an in-flight download is writing into is not a race
    // worth having, so the control is disabled for the duration...
    expect($('reader-uninstall-btn').disabled).toBe(true);
    $('reader-uninstall-btn').click();
    await vi.advanceTimersByTimeAsync(0);
    expect(confirm).not.toHaveBeenCalled();
    expect(callsTo('uninstall_reader')).toHaveLength(0);

    // ...and the handler refuses anyway, so a click that reaches it some other
    // way (a button left enabled by a stale render) still cannot delete.
    $('reader-uninstall-btn').disabled = false;
    $('reader-uninstall-btn').click();
    await vi.advanceTimersByTimeAsync(0);
    expect(confirm).not.toHaveBeenCalled();
    expect(callsTo('uninstall_reader')).toHaveLength(0);
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('Wait for the install'));
  });

  it('says nothing is installed rather than confirming a delete of nothing', async () => {
    // Reachable if the status changed under a stale button.
    const confirm = stubConfirm(true);
    let first = true;
    api.mockImplementation(async (method) => {
      if (method === 'get_reader_status') {
        if (first) { first = false; return INSTALLED; }
        return NOT_INSTALLED;
      }
      return undefined;
    });
    await mount();
    $('reader-uninstall-btn').click();
    await vi.advanceTimersByTimeAsync(0);
    expect(confirm).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith(expect.stringContaining('No reader is installed'));
  });
});

// ── Absent markup ───────────────────────────────────────────────────────────

describe('a page without the reader block', () => {
  it('wires and syncs without throwing, and calls no backend', async () => {
    document.body.innerHTML = '';
    expect(() => { wireReaderPanel(); syncReaderPanel(); }).not.toThrow();
    await vi.advanceTimersByTimeAsync(0);
    expect(api).not.toHaveBeenCalled();
  });
});
