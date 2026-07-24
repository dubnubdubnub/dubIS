/* Guard: every EventBus-backed trigger in the reopen table must name a real
   event. Follows the contract-over-a-family pattern in event-bus-contract.test.js
   — a renamed or mistyped event would otherwise silently stop reopening its
   panel, which is invisible until a user loses work behind a collapsed panel. */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { REOPEN_TRIGGERS } from '../../js/panel-collapse-logic.js';

/* Triggers raised directly by app code rather than read off the EventBus.
   Keep this list short: adding a name here to silence the guard defeats it. */
const SYNTHETIC = new Set([
  'IMPORT_MAPPER_OPENED', 'CART_ADD_MODE', 'LOG_WARN', 'LOG_ERROR',
]);

const EVENT_BUS_SRC = readFileSync(new URL('../../js/event-bus.js', import.meta.url), 'utf8');

/** Every .js file under js/, concatenated — used to prove each trigger is fired. */
function allJsSource(dir = fileURLToPath(new URL('../../js', import.meta.url)), acc = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) allJsSource(full, acc);
    else if (name.endsWith('.js')) acc.push(readFileSync(full, 'utf8'));
  }
  return acc;
}
const JS_SRC = allJsSource().join('\n');

describe('reopen trigger names', () => {
  it('every non-synthetic trigger is declared in js/event-bus.js', () => {
    for (const name of Object.keys(REOPEN_TRIGGERS)) {
      if (SYNTHETIC.has(name)) continue;
      expect(EVENT_BUS_SRC, `${name} should be declared in js/event-bus.js`)
        .toContain(`${name}:`);
    }
  });

  it('every synthetic name is genuinely absent from the EventBus', () => {
    // Keeps SYNTHETIC honest: if one of these becomes a real event, it should be
    // wired as one and dropped from the list.
    for (const name of SYNTHETIC) {
      expect(EVENT_BUS_SRC, `${name} is now a real event — remove it from SYNTHETIC`)
        .not.toContain(`${name}:`);
    }
  });

  it('every trigger in the table is actually fired somewhere', () => {
    /* A table entry nothing raises is a lie about behaviour: it reads as covered
       while that panel silently never reopens. EventBus-backed names are fired by
       app-init's EventBus.on wiring; synthetic ones by a direct handleTrigger call.
       LOG_WARN/LOG_ERROR are built as 'LOG_' + level.toUpperCase(), so they are
       matched via that construction rather than a literal. */
    const CONSTRUCTED = new Set(['LOG_WARN', 'LOG_ERROR']);
    for (const name of Object.keys(REOPEN_TRIGGERS)) {
      if (CONSTRUCTED.has(name)) {
        expect(JS_SRC, 'LOG_ level triggers should be built from the log level')
          .toContain("'LOG_' + level.toUpperCase()");
        continue;
      }
      expect(JS_SRC, `${name} is in REOPEN_TRIGGERS but nothing calls handleTrigger('${name}')`)
        .toContain(`handleTrigger('${name}')`);
    }
  });
});
