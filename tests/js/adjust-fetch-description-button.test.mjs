import { test, expect } from 'vitest';
import { pickBestDescription } from '../../js/inventory/pick-description.js';

const R = (desc, unitPrice) => ({ description: desc, unitPrice });

test('prefers the pinned row when it has a description', () => {
  const rows = [R('cheap-desc', 1), R('pinned-desc', 5)];
  expect(pickBestDescription(rows, 1, 0)).toBe('pinned-desc');
});

test('falls back to cheapest when pinned row has no description', () => {
  const rows = [R('cheap-desc', 1), R('', 5)];
  expect(pickBestDescription(rows, 1, 0)).toBe('cheap-desc');
});

test('falls back to first row with any non-empty description', () => {
  const rows = [R('', 1), R('', 5), R('third', 9)];
  expect(pickBestDescription(rows, -1, -1)).toBe('third');
});

test('treats nan/none/whitespace as empty', () => {
  const rows = [R('nan', 1), R('  ', 5), R('real', 9)];
  expect(pickBestDescription(rows, -1, -1)).toBe('real');
});

test('returns empty string when no row has a description', () => {
  const rows = [R('', 1), R('nan', 5)];
  expect(pickBestDescription(rows, -1, -1)).toBe('');
});

test('returns empty string for empty rows', () => {
  expect(pickBestDescription([], -1, -1)).toBe('');
});
