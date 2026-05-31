// @ts-check
import { describe, it, expect } from 'vitest';
import {
  pickDistributor,
  estimateWidthMm,
  format6mm,
  format12mm,
  buildLabels,
  toCsvByDistributor,
} from '../../js/label-export.js';

// ── Controlled test config ──────────────────────────────────────────────────
// Each char is exactly 1 unit wide, so estimateWidthMm(s) === s.length.
const BASE_CFG = {
  narrow_chars: "",
  wide_chars: "",
  char_width: { narrow: 1, wide: 1, default: 1 },
  calibration_k: 1,
  distributor_prefix: {
    v_lcsc: "C",
    v_digikey: "DK-",
    v_mouser: "MO-",
    v_pololu: "PL-",
  },
  tape6: { font_pt: 1, budget_mm: 50 },
  tape12: { font_pt: 1, budget_mm: 40, preferred_mm: 30 },
  header_row: true,
};

// Item helpers
function item(overrides) {
  return {
    mpn: "",
    lcsc: "",
    digikey: "",
    mouser: "",
    pololu: "",
    manufacturer: "",
    description: "",
    primary_vendor_id: null,
    ...overrides,
  };
}

// ── pickDistributor ─────────────────────────────────────────────────────────
describe('pickDistributor', () => {
  it('uses primary_vendor_id when field is non-empty', () => {
    const i = item({ lcsc: "C12345", digikey: "DK999", primary_vendor_id: "v_lcsc" });
    expect(pickDistributor(i)).toEqual({ vendorId: "v_lcsc", number: "C12345" });
  });

  it('ignores primary_vendor_id when mapped field is empty, falls back', () => {
    const i = item({ lcsc: "", digikey: "DK999", primary_vendor_id: "v_lcsc" });
    expect(pickDistributor(i)).toEqual({ vendorId: "v_digikey", number: "DK999" });
  });

  it('falls back in order: lcsc, digikey, mouser, pololu', () => {
    const i = item({ mouser: "MO123" });
    expect(pickDistributor(i)).toEqual({ vendorId: "v_mouser", number: "MO123" });
  });

  it('handles digikey-only item with no primary_vendor_id', () => {
    const i = item({ digikey: "DK-XYZ" });
    expect(pickDistributor(i)).toEqual({ vendorId: "v_digikey", number: "DK-XYZ" });
  });

  it('returns v_unknown when all fields empty', () => {
    const i = item({});
    expect(pickDistributor(i)).toEqual({ vendorId: "v_unknown", number: "" });
  });

  it('pololu falls back correctly', () => {
    const i = item({ pololu: "PL42" });
    expect(pickDistributor(i)).toEqual({ vendorId: "v_pololu", number: "PL42" });
  });
});

// ── estimateWidthMm ─────────────────────────────────────────────────────────
describe('estimateWidthMm', () => {
  const cfg = BASE_CFG;

  it('empty string returns 0', () => {
    expect(estimateWidthMm("", 1, cfg)).toBe(0);
  });

  it('width equals string length with uniform char widths', () => {
    expect(estimateWidthMm("hello", 1, cfg)).toBe(5);
  });

  it('is monotonic: longer string >= shorter', () => {
    const shorter = estimateWidthMm("abc", 1, cfg);
    const longer  = estimateWidthMm("abcd", 1, cfg);
    expect(longer).toBeGreaterThanOrEqual(shorter);
  });

  it('wide chars are wider than narrow when configured', () => {
    const wideCfg = {
      ...cfg,
      narrow_chars: "il",
      wide_chars: "WM",
      char_width: { narrow: 0.5, wide: 2, default: 1 },
    };
    // "W" (wide=2) + "i" (narrow=0.5) = 2.5; at font_pt=1, k=1 → 2.5
    expect(estimateWidthMm("Wi", 1, wideCfg)).toBeCloseTo(2.5);
    // "il" (narrow=0.5 each) = 1.0
    expect(estimateWidthMm("il", 1, wideCfg)).toBeCloseTo(1.0);
    // "WM" (wide=2 each) = 4.0
    expect(estimateWidthMm("WM", 1, wideCfg)).toBeCloseTo(4.0);
  });

  it('scales with fontPt', () => {
    expect(estimateWidthMm("abc", 2, cfg)).toBeCloseTo(6);
  });
});

// ── format6mm ───────────────────────────────────────────────────────────────
describe('format6mm', () => {
  it('joins non-empty tokens: MPN NUMBER DESC', () => {
    const i = item({ mpn: "SRV05-4A", lcsc: "C20615829", description: "ESD Diode" });
    const cfg = { ...BASE_CFG, distributor_prefix: { ...BASE_CFG.distributor_prefix } };
    const result = format6mm({ ...i, primary_vendor_id: "v_lcsc" }, cfg);
    // number token = "C" + "C20615829" = "CC20615829"
    expect(result.text).toBe("SRV05-4A CC20615829 ESD Diode");
  });

  it('omits number token cleanly when number is empty', () => {
    const i = item({ mpn: "ABC123", description: "A resistor" });
    const result = format6mm(i, BASE_CFG);
    // no number → text should not have double spaces
    expect(result.text).toBe("ABC123 A resistor");
    expect(result.text).not.toContain("  ");
  });

  it('applies vendor prefix', () => {
    const i = item({ mpn: "X", lcsc: "C99", primary_vendor_id: "v_lcsc" });
    const result = format6mm(i, BASE_CFG);
    expect(result.text).toContain("CC99");
  });

  it('estMm equals text length with test cfg', () => {
    const i = item({ mpn: "AB", description: "CD" });
    const result = format6mm(i, BASE_CFG);
    expect(result.estMm).toBe(result.text.length);
  });

  it('fires over-budget warning when estMm exceeds budget', () => {
    const cfg = { ...BASE_CFG, tape6: { font_pt: 1, budget_mm: 5 } };
    const i = item({ mpn: "VERYLONGMPN", description: "LONG DESCRIPTION TEXT" });
    const result = format6mm(i, cfg);
    expect(result.warnings).toContain("over-budget");
  });

  it('no warning when within budget', () => {
    const i = item({ mpn: "AB", description: "CD" });
    const result = format6mm(i, BASE_CFG);
    expect(result.warnings).not.toContain("over-budget");
  });

  it('columns is [text]', () => {
    const i = item({ mpn: "X" });
    const result = format6mm(i, BASE_CFG);
    expect(result.columns).toEqual([result.text]);
  });
});

// ── format12mm ──────────────────────────────────────────────────────────────
describe('format12mm', () => {
  it('wraps description across two balanced lines', () => {
    // "one two three four" – 18 chars total. Optimal split minimises max(width(A),width(B)).
    // Words: ["one","two","three","four"]
    // Split after "one": A="one"(3), B="two three four"(14) → max=14
    // Split after "two": A="one two"(7), B="three four"(10) → max=10
    // Split after "three": A="one two three"(13), B="four"(4) → max=13
    // Winner: split after "two" → max=10
    const i = item({ mpn: "MPN", description: "one two three four" });
    const result = format12mm(i, BASE_CFG);
    expect(result.lines[1]).toBe("one two");
    expect(result.lines[2]).toBe("three four");
  });

  it('single word desc has empty line 3', () => {
    const i = item({ mpn: "X", description: "Resistor" });
    const result = format12mm(i, BASE_CFG);
    expect(result.lines[2]).toBe("");
  });

  it('drops number from row1 when including it widens beyond desc rows', () => {
    // Description that is wider than MPN+number combined will constrain label.
    // MPN = "X" (1 char), number = "CCCCCCCC" (8 chars via prefix+number).
    // Row1-A = "X" (1), Row1-B = "X CCCCCCCC" (10)
    // Description = "LongWordA LongWordB" (19 chars)
    //   wrapBalanced: A="LongWordA"(9), B="LongWordB"(9) → max=9...
    //   actually split after "LongWordA": max(9,9)=9
    // Variant A: labelWidth = max(1, 9, 9) = 9
    // Variant B: labelWidth = max(10, 9, 9) = 10 → wider
    // So A wins → number dropped
    const i = item({ mpn: "X", lcsc: "CCCCCCCC", description: "LongWordA LongWordB", primary_vendor_id: "v_lcsc" });
    const result = format12mm(i, BASE_CFG);
    expect(result.lines[0]).toBe("X");
  });

  it('includes number in row1 when it does not widen the label', () => {
    // MPN = "VERYLONGMPN" (11 chars), number token = "CC" (2 chars + 1 prefix = 3)
    // Row1-A = "VERYLONGMPN" (11), Row1-B = "VERYLONGMPN CC" (14)
    // Description = "ab cd" (5 chars)
    //   wrapBalanced: split after "ab": max(2,2)=2
    // Variant A: labelWidth = max(11, 2, 2) = 11
    // Variant B: labelWidth = max(14, 2, 2) = 14 → wider
    // A wins → number dropped
    // Let's use a short MPN and even shorter description instead:
    // MPN = "AB" (2), lcsc = "1" → number token = "C1" (2)
    // Row1-A = "AB" (2), Row1-B = "AB C1" (5)
    // Description = "xxxxx xxxxx" (11 chars total)
    //   wrapBalanced: split after "xxxxx": A="xxxxx"(5), B="xxxxx"(5) → max=5
    // Variant A: max(2, 5, 5) = 5
    // Variant B: max(5, 5, 5) = 5 → tie → B wins (on tie choose B=include number)
    const i = item({ mpn: "AB", lcsc: "1", description: "xxxxx xxxxx", primary_vendor_id: "v_lcsc" });
    const result = format12mm(i, BASE_CFG);
    expect(result.lines[0]).toBe("AB C1");
  });

  it('fires over-budget when wider than budget_mm', () => {
    const cfg = { ...BASE_CFG, tape12: { font_pt: 1, budget_mm: 5, preferred_mm: 3 } };
    const i = item({ mpn: "LONGMPN", description: "LONG DESCRIPTION HERE" });
    const result = format12mm(i, cfg);
    expect(result.warnings).toContain("over-budget");
  });

  it('fires over-preferred (not over-budget) when between thresholds', () => {
    const cfg = { ...BASE_CFG, tape12: { font_pt: 1, budget_mm: 100, preferred_mm: 3 } };
    const i = item({ mpn: "LONGMPN", description: "LONG DESCRIPTION HERE" });
    const result = format12mm(i, cfg);
    expect(result.warnings).toContain("over-preferred");
    expect(result.warnings).not.toContain("over-budget");
  });

  it('columns equals lines', () => {
    const i = item({ mpn: "X", description: "a b" });
    const result = format12mm(i, BASE_CFG);
    expect(result.columns).toEqual(result.lines);
  });
});

// ── buildLabels ─────────────────────────────────────────────────────────────
describe('buildLabels', () => {
  it('maps items through format6mm for 6mm tape', () => {
    const items = [
      item({ mpn: "ABC", description: "Desc" }),
      item({ mpn: "XYZ", description: "Other" }),
    ];
    const results = buildLabels(items, "6mm", BASE_CFG);
    expect(results).toHaveLength(2);
    expect(results[0].columns).toHaveLength(1);
  });

  it('maps items through format12mm for 12mm tape', () => {
    const items = [item({ mpn: "XYZ", description: "Hello World" })];
    const results = buildLabels(items, "12mm", BASE_CFG);
    expect(results[0].columns).toHaveLength(3);
  });
});

// ── toCsvByDistributor ──────────────────────────────────────────────────────
describe('toCsvByDistributor', () => {
  it('6mm produces 1-column CSV with header', () => {
    const items = [item({ mpn: "R1", lcsc: "C1", primary_vendor_id: "v_lcsc", description: "Res" })];
    const results = buildLabels(items, "6mm", BASE_CFG);
    const map = toCsvByDistributor(results, "6mm", BASE_CFG);
    expect(map.size).toBe(1);
    const csv = map.get("v_lcsc");
    expect(csv).toBeDefined();
    const lines = csv.replace(/^﻿/, "").split("\r\n").filter(Boolean);
    expect(lines[0]).toBe("Label");      // header
    expect(lines.length).toBe(2);       // header + 1 data row
  });

  it('12mm produces 3-column CSV with header', () => {
    const items = [item({ mpn: "R1", description: "a b" })];
    const results = buildLabels(items, "12mm", BASE_CFG);
    const map = toCsvByDistributor(results, "12mm", BASE_CFG);
    const csv = [...map.values()][0];
    const lines = csv.replace(/^﻿/, "").split("\r\n").filter(Boolean);
    expect(lines[0]).toBe("Line1,Line2,Line3"); // header
    const cols = lines[1].split(",");
    expect(cols).toHaveLength(3);
  });

  it('header_row=false omits header', () => {
    const cfg = { ...BASE_CFG, header_row: false };
    const items = [item({ mpn: "X", description: "Y" })];
    const results = buildLabels(items, "6mm", cfg);
    const map = toCsvByDistributor(results, "6mm", cfg);
    const csv = [...map.values()][0];
    const lines = csv.replace(/^﻿/, "").split("\r\n").filter(Boolean);
    expect(lines[0]).not.toBe("Label");
    expect(lines).toHaveLength(1);
  });

  it('groups results into separate map entries per distributor', () => {
    const items = [
      item({ mpn: "A", lcsc: "C1", primary_vendor_id: "v_lcsc", description: "X" }),
      item({ mpn: "B", digikey: "DK9", primary_vendor_id: "v_digikey", description: "Y" }),
    ];
    const results = buildLabels(items, "6mm", BASE_CFG);
    const map = toCsvByDistributor(results, "6mm", BASE_CFG);
    expect(map.size).toBe(2);
    expect(map.has("v_lcsc")).toBe(true);
    expect(map.has("v_digikey")).toBe(true);
  });

  it('CSV-escapes fields containing commas', () => {
    const items = [item({ mpn: "A,B", description: "C,D" })];
    const results = buildLabels(items, "6mm", BASE_CFG);
    const map = toCsvByDistributor(results, "6mm", BASE_CFG);
    const csv = [...map.values()][0];
    // Fields with commas must be quoted
    expect(csv).toContain('"');
  });

  it('prepends UTF-8 BOM', () => {
    const items = [item({ mpn: "X", description: "Y" })];
    const results = buildLabels(items, "6mm", BASE_CFG);
    const map = toCsvByDistributor(results, "6mm", BASE_CFG);
    const csv = [...map.values()][0];
    expect(csv.startsWith("﻿")).toBe(true);
  });

  it('uses CRLF line endings', () => {
    const items = [item({ mpn: "X", description: "Y" })];
    const results = buildLabels(items, "6mm", BASE_CFG);
    const map = toCsvByDistributor(results, "6mm", BASE_CFG);
    const csv = [...map.values()][0];
    expect(csv).toContain("\r\n");
  });
});
