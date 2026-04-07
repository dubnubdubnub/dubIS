/**
 * WCAG 2.2 contrast ratio audit for color tokens.
 *
 * These tests are NON-BLOCKING — contrast failures log warnings and
 * track a violation count, but do not fail CI. This is intentional:
 * contrast issues are important but not as severe as the app refusing
 * to start. The violation count threshold catches regressions (adding
 * new violations) while allowing the existing ones to be fixed over time.
 *
 * Thresholds:
 *   SC 1.4.3 (AA):  4.5:1 normal text, 3:1 large text (>=18pt or >=14pt bold)
 *   SC 1.4.11 (AA): 3:1 for UI components (borders, icons, focus rings)
 */
import { describe, it, expect } from 'vitest';
import { hex } from 'wcag-contrast';

// ── Dark theme tokens (from css/variables.css :root) ─────────────────
const dark = {
  // Backgrounds
  '--bg-base':        '#0d1117',
  '--bg-surface':     '#161b22',
  '--bg-hover':       '#1c2333',
  '--bg-raised':      '#21262d',
  '--border-default': '#30363d',

  // Text
  '--text-muted':     '#484f58',
  '--text-secondary': '#8b949e',
  '--text-primary':   '#c9d1d9',
  '--text-bright':    '#e6edf3',

  // Status colors
  '--color-green':       '#3fb950',
  '--color-green-dark':  '#238636',
  '--color-red':         '#f85149',
  '--color-red-dark':    '#da3633',
  '--color-yellow':      '#d29922',
  '--color-orange':      '#f0883e',
  '--color-blue':        '#58a6ff',
  '--color-blue-dark':   '#1f6feb',
  '--color-pink':        '#f778ba',
  '--color-pink-dark':   '#db61a2',
  '--color-teal':        '#2dd4bf',
  '--color-teal-dark':   '#1a9985',
  '--color-purple':      '#a371f7',
  '--color-purple-light':'#d2a8ff',
  '--color-gray-muted':  '#636c76',

  // Vendor / provider colors
  '--vendor-lcsc':    '#1166dd',
  '--vendor-digikey': '#ee2821',
  '--vendor-pololu':  '#1e2f94',
  '--vendor-mouser':  '#004A99',
};

// Tooltip (light theme) colors
const tooltip = {
  bg:       '#ffffff',
  text:     '#1f2328',
  label:    '#656d76',
  link:     '#0969da',
  inStock:  '#1a7f37',
  noStock:  '#d1242f',
};

// ── All color pairs to audit ─────────────────────────────────────────
// min 4.5 = normal text (SC 1.4.3 AA)
// min 3.0 = large text or UI component (SC 1.4.3 / 1.4.11 AA)
// min 1.0 = decorative, exempt — recorded for documentation

const ALL_PAIRS = [
  // Text on backgrounds
  { fg: '--text-bright',    bg: '--bg-base',    min: 4.5, label: 'bright text on base' },
  { fg: '--text-primary',   bg: '--bg-base',    min: 4.5, label: 'primary text on base' },
  { fg: '--text-secondary', bg: '--bg-base',    min: 4.5, label: 'secondary text on base' },
  { fg: '--text-bright',    bg: '--bg-surface', min: 4.5, label: 'bright text on surface' },
  { fg: '--text-primary',   bg: '--bg-surface', min: 4.5, label: 'primary text on surface' },
  { fg: '--text-secondary', bg: '--bg-surface', min: 4.5, label: 'secondary text on surface' },
  { fg: '--text-bright',    bg: '--bg-raised',  min: 4.5, label: 'bright text on raised' },
  { fg: '--text-primary',   bg: '--bg-raised',  min: 4.5, label: 'primary text on raised' },
  { fg: '--text-secondary', bg: '--bg-raised',  min: 3.0, label: 'secondary text on raised (large/UI)' },
  { fg: '--text-muted',     bg: '--bg-base',    min: 1.0, label: 'muted text on base (decorative)' },
  { fg: '--text-muted',     bg: '--bg-surface', min: 1.0, label: 'muted text on surface (decorative)' },

  // Status colors on base
  { fg: '--color-green',      bg: '--bg-base', min: 3.0, label: 'green status on base' },
  { fg: '--color-red',        bg: '--bg-base', min: 3.0, label: 'red status on base' },
  { fg: '--color-yellow',     bg: '--bg-base', min: 3.0, label: 'yellow status on base' },
  { fg: '--color-orange',     bg: '--bg-base', min: 3.0, label: 'orange status on base' },
  { fg: '--color-blue',       bg: '--bg-base', min: 3.0, label: 'blue status on base' },
  { fg: '--color-pink',       bg: '--bg-base', min: 3.0, label: 'pink status on base' },
  { fg: '--color-teal',       bg: '--bg-base', min: 3.0, label: 'teal status on base' },
  { fg: '--color-purple',     bg: '--bg-base', min: 3.0, label: 'purple status on base' },
  { fg: '--color-gray-muted', bg: '--bg-base', min: 3.0, label: 'gray-muted status on base' },

  // Dark variants on surface
  { fg: '--color-green-dark',  bg: '--bg-surface', min: 3.0, label: 'green-dark on surface' },
  { fg: '--color-red-dark',    bg: '--bg-surface', min: 3.0, label: 'red-dark on surface' },
  { fg: '--color-blue-dark',   bg: '--bg-surface', min: 3.0, label: 'blue-dark on surface' },
  { fg: '--color-teal-dark',   bg: '--bg-surface', min: 3.0, label: 'teal-dark on surface' },
  { fg: '--color-pink-dark',   bg: '--bg-surface', min: 3.0, label: 'pink-dark on surface' },

  // Vendor colors on base
  { fg: '--vendor-lcsc',    bg: '--bg-base', min: 3.0, label: 'LCSC blue on base' },
  { fg: '--vendor-digikey', bg: '--bg-base', min: 3.0, label: 'DigiKey red on base' },
  { fg: '--vendor-pololu',  bg: '--bg-base', min: 3.0, label: 'Pololu navy on base' },
  { fg: '--vendor-mouser',  bg: '--bg-base', min: 3.0, label: 'Mouser blue on base' },

  // Distributor filter button text colors (lightened for contrast on surface)
  { fg: '#6a9fd8', bg: '--bg-surface', min: 3.0, label: 'Mouser filter text on surface' },
  { fg: '#5a6fd6', bg: '--bg-surface', min: 3.0, label: 'Pololu filter text on surface' },
  { fg: '--vendor-lcsc',    bg: '--bg-surface', min: 3.0, label: 'LCSC filter text on surface' },
  { fg: '--vendor-digikey', bg: '--bg-surface', min: 3.0, label: 'Digikey filter text on surface' },

  // Button text
  { fg: '#ffffff', bg: '--color-green-dark', min: 4.5, label: 'white on green-dark button' },
  { fg: '#ffffff', bg: '--color-red-dark',   min: 4.5, label: 'white on red-dark button' },
  { fg: '#ffffff', bg: '--color-blue-dark',  min: 4.5, label: 'white on blue-dark button' },

  // UI components
  { fg: '--border-default', bg: '--bg-base',    min: 1.0, label: 'border on base (decorative separator)' },
  { fg: '--color-blue',     bg: '--bg-base',    min: 3.0, label: 'blue focus ring on base' },
  { fg: '--color-blue',     bg: '--bg-surface', min: 3.0, label: 'blue focus ring on surface' },
];

const TOOLTIP_PAIRS = [
  { fg: tooltip.text,    bg: tooltip.bg, min: 4.5, label: 'body text on white' },
  { fg: tooltip.label,   bg: tooltip.bg, min: 4.5, label: 'label text on white' },
  { fg: tooltip.link,    bg: tooltip.bg, min: 4.5, label: 'link text on white' },
  { fg: tooltip.inStock, bg: '#e6f7e9',  min: 4.5, label: 'in-stock badge text on #e6f7e9' },
  { fg: tooltip.noStock, bg: '#fce8e8',  min: 4.5, label: 'no-stock badge text on #fce8e8' },
];

// ── Helper ──
function resolve(token) {
  if (token.startsWith('#')) return token;
  if (token.startsWith('--')) return dark[token];
  throw new Error(`Unknown token: ${token}`);
}

// ── Audit: collect failures, assert on count ──

describe('WCAG 2.2 contrast audit — dark theme', () => {
  const failures = [];

  for (const { fg, bg, min, label } of ALL_PAIRS) {
    it(`${label}`, () => {
      const ratio = hex(resolve(fg), resolve(bg));
      if (ratio < min) {
        failures.push({ label, fg, bg, ratio, min });
        console.warn(`  ⚠ CONTRAST: ${label} — ${ratio.toFixed(2)}:1 (need ${min}:1)`);
      }
    });
  }

  it('violation count does not exceed known baseline', () => {
    // Current known failures: Pololu navy (1.70:1), Mouser blue (2.20:1)
    // Reduce this threshold as vendor colors are fixed.
    if (failures.length > 0) {
      console.warn(`\n  ${failures.length} contrast violation(s):`);
      for (const f of failures) {
        console.warn(`    ${f.label}: ${f.ratio.toFixed(2)}:1 (need ${f.min}:1) — ${f.fg} on ${f.bg}`);
      }
    }
    expect(failures.length).toBeLessThanOrEqual(2);
  });
});

describe('WCAG 2.2 contrast audit — tooltip (light theme)', () => {
  const failures = [];

  for (const { fg, bg, min, label } of TOOLTIP_PAIRS) {
    it(`${label}`, () => {
      const ratio = hex(fg, bg);
      if (ratio < min) {
        failures.push({ label, fg, bg, ratio, min });
        console.warn(`  ⚠ CONTRAST: ${label} — ${ratio.toFixed(2)}:1 (need ${min}:1)`);
      }
    });
  }

  it('violation count does not exceed known baseline', () => {
    // Current known failure: no-stock red on pink bg (4.46:1, needs 4.5:1)
    // Reduce this threshold as colors are fixed.
    if (failures.length > 0) {
      console.warn(`\n  ${failures.length} contrast violation(s):`);
      for (const f of failures) {
        console.warn(`    ${f.label}: ${f.ratio.toFixed(2)}:1 (need ${f.min}:1) — ${f.fg} on ${f.bg}`);
      }
    }
    expect(failures.length).toBeLessThanOrEqual(1);
  });
});
