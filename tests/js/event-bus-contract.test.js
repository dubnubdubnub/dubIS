// Exhaustiveness guard for the EventBus vocabulary. Three invariants over the
// family of `Events` constants (js/event-bus.js), enforced across all of js/:
//
//   1. No raw event strings — every EventBus.emit/on/off uses an `Events.X`
//      constant (raw literals bypass the typo-proofing the constants provide).
//   2. No typo'd constant — every `Events.X` referenced actually exists.
//   3. Completeness — every declared event is both emitted AND listened,
//      unless explicitly allowlisted as intentionally listener-less.
//
// A dead event (emitted, no listener) or an orphan listener (listens for an
// event nobody emits) is a silent bug: no throw, the wiring just doesn't fire.
// Adding an event without a listener fails CI until it's wired or allowlisted.
import { describe, it, expect } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { Events } from '../../js/event-bus.js';

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'js');

// Events intentionally with no listener (emitted for a future consumer / debug).
// Empty today — keep it that way unless there's a real reason.
const LISTENERLESS_ALLOWLIST = new Set([]);

function jsFiles(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...jsFiles(p));
    else if (name.endsWith('.js')) out.push(p);
  }
  return out;
}

const USED_CONST = /EventBus\.(emit|on|off)\(\s*Events\.(\w+)/g;
const USED_RAW = /EventBus\.(?:emit|on|off)\(\s*["'`]/g;

describe('EventBus vocabulary invariant', () => {
  const files = jsFiles(JS_DIR);
  const emitted = new Set();
  const listened = new Set();
  const rawUsages = [];
  const unknownConsts = [];

  for (const f of files) {
    const src = readFileSync(f, 'utf8');
    if (USED_RAW.test(src)) rawUsages.push(f);
    for (const m of src.matchAll(USED_CONST)) {
      const [, verb, name] = m;
      if (!(name in Events)) unknownConsts.push(`${name} (in ${f})`);
      (verb === 'emit' ? emitted : listened).add(name);
    }
  }

  it('uses no raw event strings (only Events.* constants)', () => {
    expect(rawUsages, `raw EventBus event string(s) in: ${rawUsages.join(', ')}`).toEqual([]);
  });

  it('references no unknown Events.* constant (typo guard)', () => {
    expect(unknownConsts, `unknown Events.* reference(s): ${unknownConsts.join(', ')}`).toEqual([]);
  });

  it('every declared event is emitted somewhere', () => {
    const neverEmitted = Object.keys(Events).filter(n => !emitted.has(n));
    expect(neverEmitted, `Event(s) declared but never emitted: ${neverEmitted.join(', ')}`).toEqual([]);
  });

  it('every emitted event has a listener (or is allowlisted)', () => {
    const dead = Object.keys(Events)
      .filter(n => !listened.has(n) && !LISTENERLESS_ALLOWLIST.has(n));
    expect(dead, `Event(s) emitted with no listener (wire one or allowlist): ${dead.join(', ')}`).toEqual([]);
  });
});
