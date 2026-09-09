/**
 * Unit tests for the data-vs-chrome classifier used by
 * tests/js/e2e/data-text-selectable.spec.mjs.
 *
 * The broad E2E spec's whole value rests on this decision procedure: if it
 * quietly classified a new part-number cell as chrome, the spec would pass while
 * the cell stayed unselectable. So the rules are pinned here, in isolation,
 * where each one is legible.
 */
import { describe, it, expect } from 'vitest';
import {
  classifyLeaf, partitionLeaves, leafKind, oneOfEachKind, CHROME_CLASSES,
  widestLine, sharesRun,
} from '../../tests/js/e2e/helpers/data-text.mjs';

/** Build a fact bundle with sensible defaults for a plain visible value. */
function facts(over = {}) {
  return {
    selector: 'span.part-mpn',
    text: 'DF40C-30DP-0.4V(51)',
    tag: 'SPAN',
    classes: ['part-mpn'],
    chainTags: ['SPAN', 'DIV', 'DIV'],
    chainClasses: ['part-mpn', 'inv-part-row'],
    userSelect: 'text',
    ariaHidden: false,
    editable: false,
    visible: true,
    box: { x: 10, y: 10, w: 80, h: 13 },
    ...over,
  };
}

describe('classifyLeaf — values', () => {
  it('an MPN cell is data', () => {
    expect(classifyLeaf(facts()).kind).toBe('data');
  });

  it('a part-number cell is data even though it is clickable', () => {
    // .part-id-lcsc has cursor: pointer (it opens the part tooltip). Clickable
    // is NOT the same as chrome — this is the value the user complained they
    // could not highlight, so it must stay in the enforced set.
    expect(classifyLeaf(facts({
      selector: 'span.part-id-lcsc', text: 'C429942', classes: ['part-id-lcsc'],
      chainClasses: ['part-id-lcsc', 'part-ids', 'inv-part-row'],
    })).kind).toBe('data');
  });

  it('quantities, prices and descriptions are data', () => {
    for (const [cls, text] of [['part-qty', '30'], ['part-value', '$8.57'],
      ['part-unit-price', '$0.29'], ['part-desc-inner', 'Board to Board Connector']]) {
      expect(classifyLeaf(facts({ classes: [cls], chainClasses: [cls, 'inv-part-row'], text })).kind,
        `${cls} should be data`).toBe('data');
    }
  });

  it('a table cell of BOM data is data', () => {
    expect(classifyLeaf(facts({
      tag: 'TD', classes: ['mono'], chainTags: ['TD', 'TR', 'TBODY', 'TABLE'],
      chainClasses: ['mono'],
    })).kind).toBe('data');
  });
});

describe('classifyLeaf — chrome', () => {
  it('exempts anything inside a button or link', () => {
    expect(classifyLeaf(facts({ chainTags: ['SPAN', 'BUTTON', 'DIV'] })).reason)
      .toMatch(/inside <button>/);
    expect(classifyLeaf(facts({ chainTags: ['SPAN', 'A', 'DIV'] })).reason)
      .toMatch(/inside <a>/);
  });

  it('exempts a <th> column heading, not just a styled header div', () => {
    expect(classifyLeaf(facts({ tag: 'TH', chainTags: ['TH', 'TR', 'THEAD'] })).kind)
      .toBe('chrome');
  });

  it('exempts the collapse headers by class, with the reason attached', () => {
    const v = classifyLeaf(facts({
      classes: ['inv-section-header'], chainClasses: ['inv-section-header'], text: 'Switches',
    }));
    expect(v.kind).toBe('chrome');
    expect(v.reason).toContain('click-to-collapse');
  });

  it('exempts text nested inside a chrome ancestor', () => {
    expect(classifyLeaf(facts({
      classes: ['gp-name'], chainClasses: ['gp-name', 'generic-group-header'],
    })).kind).toBe('chrome');
  });

  it('exempts aria-hidden decoration and contenteditable', () => {
    expect(classifyLeaf(facts({ ariaHidden: true })).kind).toBe('chrome');
    expect(classifyLeaf(facts({ editable: true })).kind).toBe('chrome');
  });

  it('exempts invisible elements', () => {
    expect(classifyLeaf(facts({ visible: false })).kind).toBe('chrome');
  });

  it('exempts glyph-only text', () => {
    for (const glyph of ['▾', '≡', '⚠', '●●', '→', '↺', '×', '(', '—']) {
      expect(classifyLeaf(facts({ text: glyph })).kind, `${glyph} should be chrome`).toBe('chrome');
    }
  });

  it('does NOT treat a short alphanumeric value as a glyph', () => {
    for (const text of ['30', '0402', '5', '$0.29', 'C1']) {
      expect(classifyLeaf(facts({ text })).kind, `${text} should be data`).toBe('data');
    }
  });
});

describe('the exemption list is deliberate', () => {
  it('every CHROME_CLASSES entry carries a non-empty reason', () => {
    for (const [cls, reason] of Object.entries(CHROME_CLASSES)) {
      expect(typeof reason, `${cls} needs a string reason`).toBe('string');
      expect(reason.length, `${cls}'s reason must explain the exemption`).toBeGreaterThan(8);
    }
  });

  it('does not exempt any of the inventory value cells', () => {
    // A guard on the guard: if someone "fixes" a failing spec by adding a value
    // cell here, this test objects instead.
    for (const cls of ['part-mpn', 'part-id', 'part-ids', 'part-id-lcsc', 'part-id-digikey',
      'part-id-pololu', 'part-id-mouser', 'part-qty', 'part-value', 'part-unit-price',
      'part-desc', 'part-desc-inner', 'inv-part-row']) {
      expect(Object.prototype.hasOwnProperty.call(CHROME_CLASSES, cls),
        `.${cls} holds inventory data and must never be exempted`).toBe(false);
    }
  });
});

describe('partitionLeaves / sampling', () => {
  it('splits and keeps the reason on both sides', () => {
    const { data, chrome } = partitionLeaves([
      facts(),
      facts({ classes: ['inv-section-header'], chainClasses: ['inv-section-header'] }),
    ]);
    expect(data).toHaveLength(1);
    expect(chrome).toHaveLength(1);
    expect(data[0].reason).toBeTruthy();
    expect(chrome[0].reason).toBeTruthy();
  });

  it('leafKind falls back to the tag when there are no classes', () => {
    expect(leafKind({ classes: ['part-mpn'], tag: 'SPAN' })).toBe('part-mpn');
    expect(leafKind({ classes: [], tag: 'TD' })).toBe('td');
  });

  it('oneOfEachKind keeps the first of each distinct cell shape', () => {
    const picked = oneOfEachKind([
      { classes: ['part-mpn'], tag: 'SPAN', text: 'a' },
      { classes: ['part-mpn'], tag: 'SPAN', text: 'b' },
      { classes: ['part-qty'], tag: 'SPAN', text: 'c' },
    ]);
    expect(picked.map(p => p.text)).toEqual(['a', 'c']);
  });
});

describe('widestLine', () => {
  it('picks the longest rendered line of a wrapped value', () => {
    const leaf = { lines: [
      { x: 0, y: 0, w: 40, h: 12 },
      { x: 0, y: 12, w: 90, h: 12 },
      { x: 0, y: 24, w: 25, h: 12 },
    ] };
    expect(widestLine(leaf)).toEqual({ x: 0, y: 12, w: 90, h: 12 });
  });

  it('is null when the element renders no text line', () => {
    expect(widestLine({ lines: [] })).toBeNull();
    expect(widestLine({})).toBeNull();
  });
});

describe('sharesRun — "did the drag highlight THIS value?"', () => {
  it('accepts an exact match', () => {
    expect(sharesRun('C429942', 'C429942')).toBe(true);
  });

  it('accepts an ellipsised cell, where only part of the value is rendered', () => {
    // td.mono in the matched BOM table clips with text-overflow, so a drag along
    // the visible line can only ever return the visible prefix.
    expect(sharesRun('DF40C-30DP-', 'DF40C-30DP-0.4V(51)')).toBe(true);
  });

  it('accepts one line of a wrapped value, plus a neighbouring cell picked up on the way', () => {
    expect(sharesRun('Male Pin 50Ω Surface Mount SMD',
      'Connector Receptacle IPEX Male Pin 50Ω Surface Mount')).toBe(true);
  });

  it('is case-insensitive, so text-transform does not fail a real selection', () => {
    expect(sharesRun('DESIGNATORS', 'Designators')).toBe(true);
  });

  it('rejects an empty selection — the symptom of an unselectable value', () => {
    expect(sharesRun('', 'C429942')).toBe(false);
  });

  it('rejects a selection of unrelated text — a drag that grabbed an overlay', () => {
    expect(sharesRun('Salvage', 'DF40C-30DP-0.4V(51)')).toBe(false);
    expect(sharesRun('Unknown', '$0.29')).toBe(false);
  });

  it('requires the WHOLE value for values shorter than the run length', () => {
    expect(sharesRun('30', '30')).toBe(true);
    expect(sharesRun('130', '30')).toBe(true);   // a wider drag that included it
    expect(sharesRun('40', '30')).toBe(false);
  });
});
