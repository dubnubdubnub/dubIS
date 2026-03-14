import { describe, it, expect } from 'vitest';
import { detectSequence, fillValues } from '../../js/grid-fill.js';

describe('detectSequence', () => {
  it('detects incrementing by 1', () => {
    expect(detectSequence(['1', '2', '3'])).toEqual({ start: 1, step: 1 });
  });

  it('detects incrementing by 10', () => {
    expect(detectSequence(['10', '20', '30'])).toEqual({ start: 10, step: 10 });
  });

  it('detects decrementing', () => {
    expect(detectSequence(['9', '6', '3'])).toEqual({ start: 9, step: -3 });
  });

  it('returns null for text values', () => {
    expect(detectSequence(['a', 'b'])).toBeNull();
  });

  it('returns null for single value', () => {
    expect(detectSequence(['5'])).toBeNull();
  });

  it('returns null for non-constant step', () => {
    expect(detectSequence(['1', '2', '4'])).toBeNull();
  });

  it('detects zero step (constant)', () => {
    expect(detectSequence(['7', '7', '7'])).toEqual({ start: 7, step: 0 });
  });
});

describe('fillValues', () => {
  it('repeats a single text value', () => {
    expect(fillValues(['hello'], 5)).toEqual(['hello', 'hello', 'hello', 'hello', 'hello']);
  });

  it('continues an arithmetic sequence', () => {
    expect(fillValues(['1', '2', '3'], 3)).toEqual(['4', '5', '6']);
  });

  it('continues a sequence by 10', () => {
    expect(fillValues(['10', '20', '30'], 3)).toEqual(['40', '50', '60']);
  });

  it('cycles text values', () => {
    expect(fillValues(['a', 'b', 'c'], 5)).toEqual(['a', 'b', 'c', 'a', 'b']);
  });

  it('repeats single numeric value (no sequence)', () => {
    expect(fillValues(['5'], 3)).toEqual(['5', '5', '5']);
  });

  it('continues a decrementing sequence', () => {
    expect(fillValues(['10', '8', '6'], 2)).toEqual(['4', '2']);
  });
});
