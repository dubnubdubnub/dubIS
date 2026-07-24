/* Guard: every EventBus-backed trigger in the reopen table must name a real
   event. Follows the contract-over-a-family pattern in event-bus-contract.test.js
   — a renamed or mistyped event would otherwise silently stop reopening its
   panel, which is invisible until a user loses work behind a collapsed panel. */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { REOPEN_TRIGGERS } from '../../js/panel-collapse-logic.js';

/* Triggers panel-collapse.js raises itself rather than reading off the EventBus.
   Keep this list short: adding a name here to silence the guard defeats it. */
const SYNTHETIC = new Set([
  'IMPORT_COMPLETED', 'IMPORT_MAPPER_OPENED', 'CART_ADD_MODE', 'BOM_DIRTY',
  'LOG_WARN', 'LOG_ERROR',
]);

const EVENT_BUS_SRC = readFileSync(new URL('../../js/event-bus.js', import.meta.url), 'utf8');

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
});
