import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: ['Resistors', 'Capacitors'],
  FIELDNAMES: [],
}));

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { scrollDelta } from '../../js/a11y/scrollable.js';

describe('scrollDelta', () => {
  it('arrows scroll by a line', () => {
    expect(scrollDelta('ArrowDown', 500)).toBe(40);
    expect(scrollDelta('ArrowUp', 500)).toBe(-40);
  });
  it('page keys scroll ~90% of client height', () => {
    expect(scrollDelta('PageDown', 500)).toBe(450);
    expect(scrollDelta('PageUp', 500)).toBe(-450);
  });
  it('Home/End jump to extremes', () => {
    expect(scrollDelta('Home', 500)).toBe(-Infinity);
    expect(scrollDelta('End', 500)).toBe(Infinity);
  });
  it('ignores other keys', () => {
    expect(scrollDelta('Enter', 500)).toBeNull();
  });
});
