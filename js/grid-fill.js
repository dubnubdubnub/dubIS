// @ts-check
/* grid-fill.js — Fill-sequence detection and value generation for grid selection.
   Pure logic, no DOM dependencies. */

/**
 * Detect arithmetic sequence in an array of string values.
 * Returns { start, step } if all values are numeric with a constant step, else null.
 * @param {string[]} values
 * @returns {{ start: number, step: number } | null}
 */
export function detectSequence(values) {
  if (values.length < 2) return null;
  const nums = values.map(v => Number(v));
  if (nums.some(n => isNaN(n))) return null;
  const step = nums[1] - nums[0];
  for (let i = 2; i < nums.length; i++) {
    if (nums[i] - nums[i - 1] !== step) return null;
  }
  return { start: nums[0], step };
}

/**
 * Generate fill values from source values into `count` new cells.
 * Detects arithmetic sequences; falls back to cyclic repeat.
 * @param {string[]} source
 * @param {number} count
 * @returns {string[]}
 */
export function fillValues(source, count) {
  const seq = detectSequence(source);
  if (seq) {
    const result = [];
    for (let i = 0; i < count; i++) {
      result.push(String(seq.start + seq.step * (source.length + i)));
    }
    return result;
  }
  // Cyclic repeat
  const result = [];
  for (let i = 0; i < count; i++) {
    result.push(source[i % source.length]);
  }
  return result;
}
