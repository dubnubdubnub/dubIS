import { describe, it, expect } from 'vitest';
import {
  PHASES, TERMINAL_PHASES, INDETERMINATE_PHASES, PHASE_LABELS, UNKNOWN_PHASE_LABEL,
  phaseLabel, isIndeterminatePhase, isTerminalPhase,
  formatBytes, clampPct, progressPct, statusPct, formatPct, formatByteProgress,
  normalizeStatus, formatStatusLine, shouldKeepPolling,
} from '../../js/reader/reader-progress-logic.js';

const KIB = 1024;
const MIB = 1024 * 1024;
const GIB = 1024 * 1024 * 1024;

describe('PHASES', () => {
  it('is exactly the backend install state machine, in order', () => {
    expect(PHASES).toEqual([
      'detect', 'runtime', 'weights', 'projector', 'start', 'verify', 'done', 'error',
    ]);
  });

  it('gives every phase a non-empty human label', () => {
    // The guard that stops a future phase shipping label-less.
    for (const phase of PHASES) {
      expect(typeof PHASE_LABELS[phase]).toBe('string');
      expect(PHASE_LABELS[phase].trim().length).toBeGreaterThan(0);
      expect(phaseLabel(phase)).toBe(PHASE_LABELS[phase]);
    }
  });

  it('labels no phase the table does not list', () => {
    expect(Object.keys(PHASE_LABELS).sort()).toEqual([...PHASES].sort());
  });

  it('writes the labels for a non-technical reader', () => {
    expect(PHASE_LABELS.weights).toBe('Downloading the model…');
    expect(PHASE_LABELS.projector).toBe('Downloading the vision projector…');
    expect(PHASE_LABELS.start).toBe('Starting the reader…');
    expect(PHASE_LABELS.verify).toBe('Checking it can read a page…');
  });

  it('falls back to a non-empty label for an unknown or missing phase', () => {
    expect(phaseLabel('teleport')).toBe(UNKNOWN_PHASE_LABEL);
    expect(phaseLabel(undefined)).toBe(UNKNOWN_PHASE_LABEL);
    expect(phaseLabel(null)).toBe(UNKNOWN_PHASE_LABEL);
    expect(phaseLabel('')).toBe(UNKNOWN_PHASE_LABEL);
    expect(phaseLabel(7)).toBe(UNKNOWN_PHASE_LABEL);
    expect(UNKNOWN_PHASE_LABEL.trim().length).toBeGreaterThan(0);
  });

  it('does not inherit labels from Object.prototype', () => {
    expect(phaseLabel('constructor')).toBe(UNKNOWN_PHASE_LABEL);
    expect(phaseLabel('toString')).toBe(UNKNOWN_PHASE_LABEL);
  });
});

describe('phase classification', () => {
  it('marks detect, start and verify indeterminate', () => {
    expect(INDETERMINATE_PHASES).toEqual(['detect', 'start', 'verify']);
    for (const phase of INDETERMINATE_PHASES) expect(isIndeterminatePhase(phase)).toBe(true);
  });

  it('marks the download phases determinate', () => {
    for (const phase of ['runtime', 'weights', 'projector', 'done', 'error']) {
      expect(isIndeterminatePhase(phase)).toBe(false);
    }
  });

  it('treats an unknown or missing phase as indeterminate', () => {
    expect(isIndeterminatePhase('teleport')).toBe(true);
    expect(isIndeterminatePhase(undefined)).toBe(true);
    expect(isIndeterminatePhase(null)).toBe(true);
  });

  it('knows which phases are terminal', () => {
    expect(TERMINAL_PHASES).toEqual(['done', 'error']);
    expect(isTerminalPhase('done')).toBe(true);
    expect(isTerminalPhase('error')).toBe(true);
    expect(isTerminalPhase('weights')).toBe(false);
    expect(isTerminalPhase(undefined)).toBe(false);
  });
});

describe('formatBytes', () => {
  it('formats bytes below a KiB as whole bytes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1)).toBe('1 B');
    expect(formatBytes(999)).toBe('999 B');
    expect(formatBytes(1023)).toBe('1023 B');
  });

  it('uses binary units, consistent with the backend GiB figures', () => {
    expect(formatBytes(KIB)).toBe('1 KiB');
    expect(formatBytes(MIB)).toBe('1 MiB');
    expect(formatBytes(GIB)).toBe('1 GiB');
    expect(formatBytes(1024 * GIB)).toBe('1 TiB');
    // Not SI: 1000 bytes stays sub-KiB, 1_000_000 is not "1 MB".
    expect(formatBytes(1000)).toBe('1000 B');
    expect(formatBytes(1000000)).toBe('976.6 KiB');
  });

  it('shows one decimal for fractional sizes and trims a bare integer', () => {
    expect(formatBytes(1.5 * KIB)).toBe('1.5 KiB');
    expect(formatBytes(Math.round(4.7 * GIB))).toBe('4.7 GiB');
    expect(formatBytes(Math.round(6.04 * GIB))).toBe('6 GiB');
    expect(formatBytes(Math.round(6.06 * GIB))).toBe('6.1 GiB');
    expect(formatBytes(2 * MIB)).toBe('2 MiB');
  });

  it('carries into the next unit rather than printing 1024 of the smaller one', () => {
    expect(formatBytes(GIB - 1)).toBe('1 GiB');
    expect(formatBytes(MIB - 1)).toBe('1 MiB');
  });

  it('returns null — not text — for anything that is not a usable size', () => {
    expect(formatBytes(null)).toBeNull();
    expect(formatBytes(undefined)).toBeNull();
    expect(formatBytes(NaN)).toBeNull();
    expect(formatBytes(Infinity)).toBeNull();
    expect(formatBytes(-1)).toBeNull();
    expect(formatBytes('1024')).toBeNull();
    expect(formatBytes({})).toBeNull();
  });
});

describe('clampPct', () => {
  it('passes through an in-range percentage', () => {
    expect(clampPct(0)).toBe(0);
    expect(clampPct(42.5)).toBe(42.5);
    expect(clampPct(100)).toBe(100);
  });

  it('never reports a negative or above-100 percentage', () => {
    expect(clampPct(-1)).toBe(0);
    expect(clampPct(-1e9)).toBe(0);
    expect(clampPct(103)).toBe(100);
    expect(clampPct(1e9)).toBe(100);
  });

  it('returns null for a missing or non-numeric percentage', () => {
    expect(clampPct(null)).toBeNull();
    expect(clampPct(undefined)).toBeNull();
    expect(clampPct(NaN)).toBeNull();
    expect(clampPct(Infinity)).toBeNull();
    expect(clampPct('50')).toBeNull();
  });
});

describe('progressPct', () => {
  it('matches reader_install.progress_pct on the ordinary case', () => {
    expect(progressPct(0, 100)).toBe(0);
    expect(progressPct(25, 100)).toBe(25);
    expect(progressPct(100, 100)).toBe(100);
    expect(progressPct(MIB, 4 * MIB)).toBe(25);
  });

  it('is null — not 0 — when the total is unknown', () => {
    // A bar at 0% reads as "stuck"; the truth is "working, length unknown".
    expect(progressPct(MIB, null)).toBeNull();
    expect(progressPct(MIB, undefined)).toBeNull();
    expect(progressPct(0, null)).toBeNull();
  });

  it('treats a real zero-length file as complete, distinct from unknown', () => {
    expect(progressPct(0, 0)).toBe(100);
    expect(progressPct(0, -5)).toBe(100);
  });

  it('clamps a body that outruns its declared length', () => {
    expect(progressPct(150, 100)).toBe(100);
    expect(progressPct(-10, 100)).toBe(0);
  });

  it('treats a missing bytes_done as zero against a known total', () => {
    expect(progressPct(undefined, 100)).toBe(0);
    expect(progressPct(NaN, 100)).toBe(0);
  });
});

describe('statusPct', () => {
  it('reports no percentage for the indeterminate phases, even if one is sent', () => {
    for (const phase of INDETERMINATE_PHASES) {
      expect(statusPct({ phase, pct: 0 })).toBeNull();
      expect(statusPct({ phase, pct: 40, bytes_done: 1, bytes_total: 2 })).toBeNull();
    }
  });

  it('trusts and clamps a reported pct on a download phase', () => {
    expect(statusPct({ phase: 'weights', pct: 37.2 })).toBe(37.2);
    expect(statusPct({ phase: 'weights', pct: 137 })).toBe(100);
    expect(statusPct({ phase: 'weights', pct: -4 })).toBe(0);
  });

  it('derives the pct from the bytes when the backend omits it', () => {
    expect(statusPct({ phase: 'weights', bytes_done: MIB, bytes_total: 2 * MIB })).toBe(50);
    expect(statusPct({ phase: 'projector', bytes_done: 5, bytes_total: null })).toBeNull();
    expect(statusPct({ phase: 'weights', bytes_done: 0, bytes_total: 0 })).toBe(100);
  });

  it('is 100 when done and null when failed', () => {
    expect(statusPct({ phase: 'done' })).toBe(100);
    expect(statusPct({ phase: 'verify', done: true })).toBe(100);
    expect(statusPct({ phase: 'error', pct: 12 })).toBeNull();
    expect(statusPct({ phase: 'error', done: true })).toBeNull();
  });

  it('returns null for a status with no phase at all', () => {
    expect(statusPct({})).toBeNull();
    expect(statusPct(null)).toBeNull();
    expect(statusPct('nonsense')).toBeNull();
  });
});

describe('formatPct', () => {
  it('renders whole percents', () => {
    expect(formatPct(0)).toBe('0%');
    expect(formatPct(37.4)).toBe('37%');
    expect(formatPct(37.6)).toBe('38%');
    expect(formatPct(100)).toBe('100%');
  });

  it('clamps before rendering and returns null for no percentage', () => {
    expect(formatPct(120)).toBe('100%');
    expect(formatPct(-3)).toBe('0%');
    expect(formatPct(null)).toBeNull();
    expect(formatPct(NaN)).toBeNull();
  });
});

describe('formatByteProgress', () => {
  it('renders "done of total" when both are known', () => {
    expect(formatByteProgress(1.5 * MIB, Math.round(4.7 * GIB))).toBe('1.5 MiB of 4.7 GiB');
    expect(formatByteProgress(0, 0)).toBe('0 B of 0 B');
  });

  it('renders just the fetched amount when the total is unknown', () => {
    expect(formatByteProgress(2 * MIB, null)).toBe('2 MiB');
  });

  it('renders just the total when the fetched amount is missing', () => {
    expect(formatByteProgress(undefined, 2 * MIB)).toBe('2 MiB');
  });

  it('returns null when neither is usable', () => {
    expect(formatByteProgress(null, null)).toBeNull();
    expect(formatByteProgress(NaN, 'lots')).toBeNull();
  });
});

describe('normalizeStatus', () => {
  it('maps a mid-download status into everything the panel needs', () => {
    const view = normalizeStatus({
      job_id: 'job-1',
      phase: 'weights',
      message: 'qwen2.5-vl-7b-q4_k_m.gguf',
      bytes_done: Math.round(1.2 * GIB),
      bytes_total: Math.round(4.7 * GIB),
      pct: 25.5,
      done: false,
      error: null,
      tier: 'qwen2.5-vl-7b-q4_k_m',
    });
    expect(view.jobId).toBe('job-1');
    expect(view.phase).toBe('weights');
    expect(view.label).toBe(PHASE_LABELS.weights);
    expect(view.message).toBe('qwen2.5-vl-7b-q4_k_m.gguf');
    expect(view.pct).toBe(25.5);
    expect(view.indeterminate).toBe(false);
    expect(view.done).toBe(false);
    expect(view.failed).toBe(false);
    expect(view.error).toBeNull();
    expect(view.tier).toBe('qwen2.5-vl-7b-q4_k_m');
    expect(view.line).toBe('Downloading the model… 1.2 GiB of 4.7 GiB (26%)');
  });

  it('accepts tier_name as well as tier', () => {
    expect(normalizeStatus({ phase: 'runtime', tier_name: 'qwen2.5-vl-3b-q4_k_m' }).tier)
      .toBe('qwen2.5-vl-3b-q4_k_m');
  });

  it('marks an indeterminate phase indeterminate with no percentage', () => {
    const view = normalizeStatus({ phase: 'start', pct: 0 });
    expect(view.pct).toBeNull();
    expect(view.indeterminate).toBe(true);
    expect(view.line).toBe('Starting the reader…');
  });

  it('reports done as complete', () => {
    const view = normalizeStatus({ phase: 'done', bytes_done: 10, bytes_total: 10 });
    expect(view.done).toBe(true);
    expect(view.failed).toBe(false);
    expect(view.pct).toBe(100);
    expect(view.indeterminate).toBe(false);
    expect(view.line).toBe(PHASE_LABELS.done);
  });

  it('reports a failure with the backend message appended', () => {
    const view = normalizeStatus({
      phase: 'error',
      error: 'sha256 mismatch for mmproj.gguf',
      pct: 60,
    });
    expect(view.failed).toBe(true);
    expect(view.done).toBe(false);
    expect(view.pct).toBeNull();
    expect(view.line).toBe(
      'The reader could not be installed. sha256 mismatch for mmproj.gguf',
    );
  });

  it('treats a non-empty error field as a failure even without the error phase', () => {
    const view = normalizeStatus({ phase: 'weights', error: 'connection reset' });
    expect(view.failed).toBe(true);
    expect(view.line).toBe('The reader could not be installed. connection reset');
  });

  it('survives a poll that raced the job\'s first published update', () => {
    const view = normalizeStatus({ job_id: 'job-2' });
    expect(view.phase).toBeNull();
    expect(view.label).toBe(UNKNOWN_PHASE_LABEL);
    expect(view.pct).toBeNull();
    expect(view.indeterminate).toBe(true);
    expect(view.done).toBe(false);
    expect(view.failed).toBe(false);
    expect(view.line).toBe(UNKNOWN_PHASE_LABEL);
  });
});

describe('formatStatusLine', () => {
  const DEGENERATE = [
    undefined, null, {}, '', 'busy', 0, 42, [], NaN, true,
    { phase: null },
    { phase: '' },
    { phase: 'teleport' },
    { phase: 'weights' },
    { phase: 'weights', pct: null, bytes_done: null, bytes_total: null },
    { phase: 'weights', pct: NaN, bytes_done: NaN, bytes_total: NaN },
    { phase: 'weights', pct: undefined, bytes_done: undefined, bytes_total: undefined },
    { phase: 'weights', pct: 'lots', bytes_done: '5', bytes_total: '10' },
    { phase: 'weights', bytes_done: -1, bytes_total: -1 },
    { phase: 'weights', bytes_done: Infinity, bytes_total: Infinity },
    { phase: 'weights', bytes_done: 150, bytes_total: 100 },
    { phase: 'weights', bytes_done: 0, bytes_total: 0 },
    { phase: 'detect' },
    { phase: 'start', pct: 0 },
    { phase: 'verify', pct: 99 },
    { phase: 'done' },
    { phase: 'done', pct: null },
    { phase: 'error' },
    { phase: 'error', error: '' },
    { phase: 'error', error: null, message: null },
    { job_id: 'job-3' },
    ...PHASES.map(phase => ({ phase })),
  ];

  it('never renders null, undefined or NaN for any plausible status', () => {
    for (const status of DEGENERATE) {
      const line = formatStatusLine(status);
      expect(typeof line, JSON.stringify(status)).toBe('string');
      expect(line.trim().length, JSON.stringify(status)).toBeGreaterThan(0);
      expect(line, JSON.stringify(status)).not.toMatch(/null|undefined|NaN|Infinity/);
    }
  });

  it('is deterministic — the same status always renders the same line', () => {
    const status = { phase: 'weights', bytes_done: MIB, bytes_total: 4 * MIB };
    expect(formatStatusLine(status)).toBe(formatStatusLine(status));
    expect(formatStatusLine(status)).toBe('Downloading the model… 1 MiB of 4 MiB (25%)');
  });

  it('omits the byte segment when there are no bytes to report', () => {
    expect(formatStatusLine({ phase: 'runtime' })).toBe(PHASE_LABELS.runtime);
  });

  it('shows fetched bytes with no percentage for an unknown-length download', () => {
    expect(formatStatusLine({ phase: 'projector', bytes_done: 3 * MIB, bytes_total: null }))
      .toBe('Downloading the vision projector… 3 MiB');
  });
});

describe('shouldKeepPolling', () => {
  it('keeps polling while the job is in flight', () => {
    for (const phase of ['detect', 'runtime', 'weights', 'projector', 'start', 'verify']) {
      expect(shouldKeepPolling({ phase })).toBe(true);
    }
    expect(shouldKeepPolling({})).toBe(true);
  });

  it('stops on done or error', () => {
    expect(shouldKeepPolling({ phase: 'done' })).toBe(false);
    expect(shouldKeepPolling({ phase: 'verify', done: true })).toBe(false);
    expect(shouldKeepPolling({ phase: 'error' })).toBe(false);
    expect(shouldKeepPolling({ phase: 'weights', error: 'boom' })).toBe(false);
  });
});
