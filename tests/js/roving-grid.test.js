import { describe, it, expect, vi } from 'vitest';

vi.mock('../../js/constants.js', () => ({
  SECTION_ORDER: [
    'Resistors',
    { name: 'Capacitors', children: ['MLCC', 'Electrolytic'] },
    'Inductors',
  ],
  FIELDNAMES: [],
}));

vi.mock('../../js/api.js', () => ({
  api: vi.fn(async () => ({})),
  AppLog: { warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import { computeTarget } from '../../js/a11y/roving-grid.js';

const rows = [3, 1, 2]; // row0 has 3 cells, row1 has 1, row2 has 2

describe('computeTarget', () => {
  it('ArrowRight moves within a row', () => {
    expect(computeTarget(rows, 0, 0, 'ArrowRight')).toEqual({ r: 0, c: 1 });
  });
  it('ArrowRight at row end returns null (no wrap)', () => {
    expect(computeTarget(rows, 0, 2, 'ArrowRight')).toBeNull();
  });
  it('ArrowLeft at row start returns null', () => {
    expect(computeTarget(rows, 0, 0, 'ArrowLeft')).toBeNull();
  });
  it('ArrowDown preserves column, clamps to shorter row', () => {
    expect(computeTarget(rows, 0, 2, 'ArrowDown')).toEqual({ r: 1, c: 0 }); // row1 has 1 cell -> clamp
  });
  it('ArrowDown keeps column when target row is wide enough', () => {
    expect(computeTarget(rows, 0, 1, 'ArrowDown')).toEqual({ r: 1, c: 0 });
    expect(computeTarget(rows, 1, 0, 'ArrowDown')).toEqual({ r: 2, c: 0 });
  });
  it('ArrowUp from clamped position keeps column index intent', () => {
    expect(computeTarget(rows, 2, 1, 'ArrowUp')).toEqual({ r: 1, c: 0 });
  });
  it('ArrowDown at last row returns null', () => {
    expect(computeTarget(rows, 2, 0, 'ArrowDown')).toBeNull();
  });
  it('Home/End jump within row', () => {
    expect(computeTarget(rows, 0, 1, 'Home')).toEqual({ r: 0, c: 0 });
    expect(computeTarget(rows, 0, 1, 'End')).toEqual({ r: 0, c: 2 });
  });
});
