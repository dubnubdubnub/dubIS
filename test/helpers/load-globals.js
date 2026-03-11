/**
 * VM context loader — simulates browser script-tag loading for tests.
 * Loads pure-logic source files (no DOM-dependent IIFEs) into a shared
 * VM context, making all globals available just like in the browser.
 */
import { createContext, runInContext } from 'node:vm';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');

// Pure logic files only — order matches index.html (minus DOM-dependent IIFEs)
const SOURCE_FILES = [
  'js/csv-parser.js',
  'js/part-keys.js',
  'js/matching.js',
];

export function loadGlobals() {
  const sandbox = {
    // JS built-ins needed by source files
    Map, Set, Array, Object, String, Number, Boolean, Math,
    parseInt, parseFloat, isNaN, NaN, Infinity, undefined,
    JSON, console, RegExp, Date, Error, TypeError, RangeError,
    Float64Array, Uint8Array,
  };
  const ctx = createContext(sandbox);
  for (const file of SOURCE_FILES) {
    const code = readFileSync(join(ROOT, file), 'utf-8');
    runInContext(code, ctx, { filename: file });
  }
  return ctx;
}
