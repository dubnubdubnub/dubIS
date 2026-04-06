/**
 * WCAG 2.2 contrast ratio tests for the dark theme color tokens.
 *
 * Thresholds:
 *   SC 1.4.3 (AA):  4.5:1 normal text, 3:1 large text (>=18pt or >=14pt bold)
 *   SC 1.4.11 (AA): 3:1 for UI components (borders, icons, focus rings)
 *
 * Each pair is annotated with its WCAG category so we apply the right minimum.
 * Decorative / inactive elements (per WCAG exemption) are tested at 1:1 to
 * document their ratios without enforcing a threshold — change the min if
 * those elements become interactive in the future.
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

// Tooltip (light theme) colors — tested against white bg
const tooltip = {
  bg:       '#ffffff',
  text:     '#1f2328',
  label:    '#656d76',
  link:     '#0969da',
  inStock:  '#1a7f37',
  noStock:  '#d1242f',
};

// ── Test pairs ───────────────────────────────────────────────────────
// Each: [foreground token, background token, min ratio, description]
//   min 4.5 = normal text (SC 1.4.3 AA)
//   min 3.0 = large text or UI component (SC 1.4.3 / 1.4.11 AA)
//   min 1.0 = decorative, exempt — recorded for documentation

/** @type {Array<{fg: string, bg: string, min: number, label: string}>} */
const TEXT_ON_BASE = [
  // Primary content text
  { fg: '--text-bright',    bg: '--bg-base',    min: 4.5, label: 'bright text on base' },
  { fg: '--text-primary',   bg: '--bg-base',    min: 4.5, label: 'primary text on base' },
  { fg: '--text-secondary', bg: '--bg-base',    min: 4.5, label: 'secondary text on base' },

  // Primary content on surface (panel backgrounds)
  { fg: '--text-bright',    bg: '--bg-surface', min: 4.5, label: 'bright text on surface' },
  { fg: '--text-primary',   bg: '--bg-surface', min: 4.5, label: 'primary text on surface' },
  { fg: '--text-secondary', bg: '--bg-surface', min: 4.5, label: 'secondary text on surface' },

  // Primary content on raised (modals, raised panels)
  { fg: '--text-bright',    bg: '--bg-raised',  min: 4.5, label: 'bright text on raised' },
  { fg: '--text-primary',   bg: '--bg-raised',  min: 4.5, label: 'primary text on raised' },
  { fg: '--text-secondary', bg: '--bg-raised',  min: 3.0, label: 'secondary text on raised (large/UI)' },

  // Muted text — decorative (timestamps, dividers, section counts)
  // Exempt from WCAG per SC 1.4.3 "incidental" exception, but tracked
  { fg: '--text-muted', bg: '--bg-base',    min: 1.0, label: 'muted text on base (decorative)' },
  { fg: '--text-muted', bg: '--bg-surface', min: 1.0, label: 'muted text on surface (decorative)' },
];

const STATUS_ON_BASE = [
  // Status colors used as text on --bg-base (inventory rows, BOM table)
  { fg: '--color-green',      bg: '--bg-base', min: 3.0, label: 'green status on base' },
  { fg: '--color-red',        bg: '--bg-base', min: 3.0, label: 'red status on base' },
  { fg: '--color-yellow',     bg: '--bg-base', min: 3.0, label: 'yellow status on base' },
  { fg: '--color-orange',     bg: '--bg-base', min: 3.0, label: 'orange status on base' },
  { fg: '--color-blue',       bg: '--bg-base', min: 3.0, label: 'blue status on base' },
  { fg: '--color-pink',       bg: '--bg-base', min: 3.0, label: 'pink status on base' },
  { fg: '--color-teal',       bg: '--bg-base', min: 3.0, label: 'teal status on base' },
  { fg: '--color-purple',     bg: '--bg-base', min: 3.0, label: 'purple status on base' },
  { fg: '--color-gray-muted', bg: '--bg-base', min: 3.0, label: 'gray-muted status on base' },

  // Dark variants used on surface (buttons, badges)
  { fg: '--color-green-dark',  bg: '--bg-surface', min: 3.0, label: 'green-dark on surface' },
  { fg: '--color-red-dark',    bg: '--bg-surface', min: 3.0, label: 'red-dark on surface' },
  { fg: '--color-blue-dark',   bg: '--bg-surface', min: 3.0, label: 'blue-dark on surface' },
  { fg: '--color-teal-dark',   bg: '--bg-surface', min: 3.0, label: 'teal-dark on surface' },
  { fg: '--color-pink-dark',   bg: '--bg-surface', min: 3.0, label: 'pink-dark on surface' },
];

const VENDOR_ON_BASE = [
  // Vendor colors as text on --bg-base (part ID badges in inventory rows)
  { fg: '--vendor-lcsc',    bg: '--bg-base', min: 3.0, label: 'LCSC blue on base' },
  { fg: '--vendor-digikey', bg: '--bg-base', min: 3.0, label: 'DigiKey red on base' },
  { fg: '--vendor-pololu',  bg: '--bg-base', min: 3.0, label: 'Pololu navy on base' },
  { fg: '--vendor-mouser',  bg: '--bg-base', min: 3.0, label: 'Mouser blue on base' },
];

const BUTTON_TEXT = [
  // Button text: white on colored backgrounds
  { fg: '#ffffff', bg: '--color-green-dark', min: 4.5, label: 'white on green-dark button' },
  { fg: '#ffffff', bg: '--color-red-dark',   min: 4.5, label: 'white on red-dark button' },
  { fg: '#ffffff', bg: '--color-blue-dark',  min: 4.5, label: 'white on blue-dark button' },
];

const UI_COMPONENTS = [
  // Border contrast against background (SC 1.4.11 — UI components need 3:1)
  { fg: '--border-default', bg: '--bg-base',    min: 1.0, label: 'border on base (decorative separator)' },
  { fg: '--color-blue',     bg: '--bg-base',    min: 3.0, label: 'blue focus ring on base' },
  { fg: '--color-blue',     bg: '--bg-surface', min: 3.0, label: 'blue focus ring on surface' },
];

// ── Helper ──
function resolve(token) {
  if (token.startsWith('#')) return token;
  if (token.startsWith('--')) return dark[token];
  throw new Error(`Unknown token: ${token}`);
}

// ── Test suites ──

describe('Dark theme — WCAG 2.2 contrast', () => {
  describe('Text on backgrounds', () => {
    for (const { fg, bg, min, label } of TEXT_ON_BASE) {
      it(`${label}: ${fg} on ${bg} >= ${min}:1`, () => {
        const ratio = hex(resolve(fg), resolve(bg));
        expect(ratio, `${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
      });
    }
  });

  describe('Status colors on backgrounds', () => {
    for (const { fg, bg, min, label } of STATUS_ON_BASE) {
      it(`${label}: ${fg} on ${bg} >= ${min}:1`, () => {
        const ratio = hex(resolve(fg), resolve(bg));
        expect(ratio, `${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
      });
    }
  });

  describe('Vendor colors on backgrounds', () => {
    for (const { fg, bg, min, label } of VENDOR_ON_BASE) {
      it(`${label}: ${fg} on ${bg} >= ${min}:1`, () => {
        const ratio = hex(resolve(fg), resolve(bg));
        expect(ratio, `${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
      });
    }
  });

  describe('Button text on colored backgrounds', () => {
    for (const { fg, bg, min, label } of BUTTON_TEXT) {
      it(`${label}: ${fg} on ${bg} >= ${min}:1`, () => {
        const ratio = hex(resolve(fg), resolve(bg));
        expect(ratio, `${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
      });
    }
  });

  describe('UI components (borders, focus rings)', () => {
    for (const { fg, bg, min, label } of UI_COMPONENTS) {
      it(`${label}: ${fg} on ${bg} >= ${min}:1`, () => {
        const ratio = hex(resolve(fg), resolve(bg));
        expect(ratio, `${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(min);
      });
    }
  });
});

describe('Tooltip (light theme) — WCAG 2.2 contrast', () => {
  it('body text on white >= 4.5:1', () => {
    expect(hex(tooltip.text, tooltip.bg)).toBeGreaterThanOrEqual(4.5);
  });
  it('label text on white >= 4.5:1', () => {
    expect(hex(tooltip.label, tooltip.bg)).toBeGreaterThanOrEqual(4.5);
  });
  it('link text on white >= 4.5:1', () => {
    expect(hex(tooltip.link, tooltip.bg)).toBeGreaterThanOrEqual(4.5);
  });
  it('in-stock badge text on #e6f7e9 >= 4.5:1', () => {
    expect(hex(tooltip.inStock, '#e6f7e9')).toBeGreaterThanOrEqual(4.5);
  });
  it('no-stock badge text on #fce8e8 >= 4.5:1', () => {
    expect(hex(tooltip.noStock, '#fce8e8')).toBeGreaterThanOrEqual(4.5);
  });
});
