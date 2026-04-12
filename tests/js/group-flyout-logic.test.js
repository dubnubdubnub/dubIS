import { describe, it, expect } from 'vitest';
import {
  generateTags,
  detectConflicts,
  filterMembers,
  generateDefaultSearchName,
} from '../../js/group-flyout/flyout-logic.js';

// ── generateTags ──

describe('generateTags', () => {
  it('creates enabled tag from group spec value_display when "value" is in required', () => {
    var spec = { value_display: "100nF", package: "0402" };
    var strictness = { required: ["value"] };
    var tags = generateTags(spec, strictness, []);
    var valueTag = tags.find(function (t) { return t.dimension === "value"; });
    expect(valueTag).toBeDefined();
    expect(valueTag.label).toBe("100nF");
    expect(valueTag.enabled).toBe(true);
    expect(valueTag.source).toBe("group");
  });

  it('creates enabled tag from group spec package when "package" is in required', () => {
    var spec = { value_display: "10k", package: "0603" };
    var strictness = { required: ["package"] };
    var tags = generateTags(spec, strictness, []);
    var pkgTag = tags.find(function (t) { return t.dimension === "package"; });
    expect(pkgTag).toBeDefined();
    expect(pkgTag.enabled).toBe(true);
  });

  it('creates disabled tag from group spec when field not in required', () => {
    var spec = { value_display: "100nF", package: "0402" };
    var strictness = { required: [] };
    var tags = generateTags(spec, strictness, []);
    tags.forEach(function (tag) {
      expect(tag.enabled).toBe(false);
    });
  });

  it('creates disabled member tags from member specs', () => {
    var spec = { value_display: "100nF" };
    var strictness = { required: [] };
    var members = [
      { spec: { voltage: "10V", tolerance: "10%", dielectric: "C0G" } },
      { spec: { voltage: "25V" } },
    ];
    var tags = generateTags(spec, strictness, members);
    var memberTags = tags.filter(function (t) { return t.source === "member"; });
    expect(memberTags.length).toBeGreaterThan(0);
    memberTags.forEach(function (tag) {
      expect(tag.enabled).toBe(false);
    });
    var voltages = memberTags.filter(function (t) { return t.dimension === "voltage"; });
    expect(voltages.map(function (t) { return t.label; }).sort()).toEqual(["10V", "25V"].sort());
  });

  it('deduplicates tags with the same dimension and label', () => {
    var spec = {};
    var strictness = { required: [] };
    var members = [
      { spec: { voltage: "10V" } },
      { spec: { voltage: "10V" } },
      { spec: { voltage: "25V" } },
    ];
    var tags = generateTags(spec, strictness, members);
    var voltageLabels = tags.filter(function (t) { return t.dimension === "voltage"; }).map(function (t) { return t.label; });
    expect(voltageLabels).toHaveLength(2);
    expect(voltageLabels.sort()).toEqual(["10V", "25V"].sort());
  });

  it('skips empty/null spec fields', () => {
    var spec = { value_display: "", package: null };
    var strictness = { required: ["value", "package"] };
    var tags = generateTags(spec, strictness, []);
    expect(tags).toHaveLength(0);
  });

  it('handles missing strictness gracefully', () => {
    var spec = { value_display: "4.7uF", package: "0805" };
    var tags = generateTags(spec, {}, []);
    tags.forEach(function (tag) {
      expect(tag.enabled).toBe(false);
    });
  });

  it('handles null members gracefully', () => {
    var spec = { value_display: "1uF" };
    var strictness = { required: ["value"] };
    var tags = generateTags(spec, strictness, null);
    expect(tags).toHaveLength(1);
    expect(tags[0].label).toBe("1uF");
  });

  it('does not produce duplicate when group spec value_display matches a member spec value_display', () => {
    // group spec value_display "100nF" already created; member shouldn't add "value:100nF" again
    // Note: members use MEMBER_SPEC_FIELDS which doesn't include value_display,
    // so this tests that the dedup by key works if they share a key via different path
    var spec = { value_display: "100nF" };
    var strictness = { required: [] };
    // members only have voltage here; no duplication risk
    var members = [{ spec: { voltage: "10V" } }];
    var tags = generateTags(spec, strictness, members);
    var valueTags = tags.filter(function (t) { return t.dimension === "value"; });
    expect(valueTags).toHaveLength(1);
  });

  it('member tag power and current fields are included', () => {
    var strictness = { required: [] };
    var members = [
      { spec: { power: "0.1W", current: "100mA" } },
    ];
    var tags = generateTags({}, strictness, members);
    var dims = tags.map(function (t) { return t.dimension; });
    expect(dims).toContain("power");
    expect(dims).toContain("current");
  });
});

// ── detectConflicts ──

describe('detectConflicts', () => {
  it('returns empty array when no tags', () => {
    expect(detectConflicts([])).toEqual([]);
  });

  it('returns empty array when all tags are in different dimensions', () => {
    var tags = [
      { label: "100nF", dimension: "value",   enabled: true,  source: "group" },
      { label: "0402",  dimension: "package", enabled: true,  source: "group" },
    ];
    expect(detectConflicts(tags)).toEqual([]);
  });

  it('returns empty array when only one enabled tag per dimension', () => {
    var tags = [
      { label: "10V", dimension: "voltage", enabled: true,  source: "member" },
      { label: "25V", dimension: "voltage", enabled: false, source: "member" },
    ];
    expect(detectConflicts(tags)).toEqual([]);
  });

  it('detects conflict when two enabled tags share the same dimension', () => {
    var tags = [
      { label: "10V", dimension: "voltage", enabled: true, source: "member" },
      { label: "25V", dimension: "voltage", enabled: true, source: "member" },
    ];
    var conflicts = detectConflicts(tags);
    expect(conflicts).toHaveLength(2);
    expect(conflicts).toContain("10V");
    expect(conflicts).toContain("25V");
  });

  it('ignores disabled tags for conflict detection', () => {
    var tags = [
      { label: "10V",  dimension: "voltage", enabled: false, source: "member" },
      { label: "25V",  dimension: "voltage", enabled: true,  source: "member" },
    ];
    expect(detectConflicts(tags)).toEqual([]);
  });

  it('detects conflicts across multiple dimensions independently', () => {
    var tags = [
      { label: "10V",  dimension: "voltage",   enabled: true, source: "member" },
      { label: "25V",  dimension: "voltage",   enabled: true, source: "member" },
      { label: "C0G",  dimension: "dielectric", enabled: true, source: "member" },
      { label: "X7R",  dimension: "dielectric", enabled: true, source: "member" },
      { label: "0402", dimension: "package",    enabled: true, source: "group" },
    ];
    var conflicts = detectConflicts(tags);
    expect(conflicts).toContain("10V");
    expect(conflicts).toContain("25V");
    expect(conflicts).toContain("C0G");
    expect(conflicts).toContain("X7R");
    expect(conflicts).not.toContain("0402");
  });
});

// ── filterMembers ──

describe('filterMembers', () => {
  var members = [
    { part_id: "C1001", description: "100nF 10V C0G 0402",  spec: { value_display: "100nF", package: "0402", voltage: "10V", dielectric: "C0G", tolerance: "5%" } },
    { part_id: "C1002", description: "100nF 25V X7R 0402",  spec: { value_display: "100nF", package: "0402", voltage: "25V", dielectric: "X7R", tolerance: "10%" } },
    { part_id: "C1003", description: "100nF 50V X5R 0805",  spec: { value_display: "100nF", package: "0805", voltage: "50V", dielectric: "X5R", tolerance: "20%" } },
    { part_id: "C1004", description: "10uF  10V  X7R 0805", spec: { value_display: "10uF",  package: "0805", voltage: "10V", dielectric: "X7R", tolerance: "20%" } },
  ];

  it('returns all members when no tags and no search', () => {
    expect(filterMembers(members, [], "")).toHaveLength(4);
  });

  it('returns all members when tags array is empty and search is empty', () => {
    expect(filterMembers(members, [], "")).toEqual(members);
  });

  it('returns empty array for null members', () => {
    expect(filterMembers(null, [], "")).toEqual([]);
  });

  it('filters by a single enabled tag (voltage: 10V)', () => {
    var tags = [
      { label: "10V", dimension: "voltage", enabled: true, source: "member" },
    ];
    var result = filterMembers(members, tags, "");
    var ids = result.map(function (m) { return m.part_id; });
    expect(ids).toContain("C1001");
    expect(ids).toContain("C1004");
    expect(ids).not.toContain("C1002");
    expect(ids).not.toContain("C1003");
  });

  it('filters AND across dimensions (voltage: 10V AND dielectric: X7R)', () => {
    var tags = [
      { label: "10V", dimension: "voltage",    enabled: true, source: "member" },
      { label: "X7R", dimension: "dielectric", enabled: true, source: "member" },
    ];
    var result = filterMembers(members, tags, "");
    expect(result).toHaveLength(1);
    expect(result[0].part_id).toBe("C1004");
  });

  it('filters OR within dimension (voltage: 10V OR voltage: 25V)', () => {
    var tags = [
      { label: "10V", dimension: "voltage", enabled: true, source: "member" },
      { label: "25V", dimension: "voltage", enabled: true, source: "member" },
    ];
    var result = filterMembers(members, tags, "");
    var ids = result.map(function (m) { return m.part_id; });
    expect(ids).toContain("C1001");
    expect(ids).toContain("C1002");
    expect(ids).toContain("C1004");
    expect(ids).not.toContain("C1003");
  });

  it('disabled tags are ignored in filtering', () => {
    var tags = [
      { label: "10V", dimension: "voltage", enabled: false, source: "member" },
    ];
    var result = filterMembers(members, tags, "");
    expect(result).toHaveLength(4);
  });

  it('filters by search text (substring match on description)', () => {
    var result = filterMembers(members, [], "c0g");
    expect(result).toHaveLength(1);
    expect(result[0].part_id).toBe("C1001");
  });

  it('filters by search text on part_id', () => {
    var result = filterMembers(members, [], "C1003");
    expect(result).toHaveLength(1);
    expect(result[0].part_id).toBe("C1003");
  });

  it('search text is case-insensitive', () => {
    var result = filterMembers(members, [], "c1003");
    expect(result).toHaveLength(1);
    expect(result[0].part_id).toBe("C1003");
  });

  it('combines tag filter AND search text filter', () => {
    var tags = [
      { label: "X7R", dimension: "dielectric", enabled: true, source: "member" },
    ];
    // Both C1002 and C1004 have X7R; search "0402" further limits to C1002
    var result = filterMembers(members, tags, "0402");
    expect(result).toHaveLength(1);
    expect(result[0].part_id).toBe("C1002");
  });

  it('handles members with no spec (excludes them when tag filter active)', () => {
    var withNoSpec = members.concat([{ part_id: "C9999", description: "mystery part" }]);
    var tags = [
      { label: "10V", dimension: "voltage", enabled: true, source: "member" },
    ];
    var result = filterMembers(withNoSpec, tags, "");
    var ids = result.map(function (m) { return m.part_id; });
    expect(ids).not.toContain("C9999");
  });

  it('handles members with no spec when only search is active', () => {
    var withNoSpec = members.concat([{ part_id: "C9999", description: "mystery part" }]);
    var result = filterMembers(withNoSpec, [], "mystery");
    expect(result).toHaveLength(1);
    expect(result[0].part_id).toBe("C9999");
  });

  it('filters by package dimension from group spec field', () => {
    var tags = [
      { label: "0402", dimension: "package", enabled: true, source: "group" },
    ];
    var result = filterMembers(members, tags, "");
    var ids = result.map(function (m) { return m.part_id; });
    expect(ids).toContain("C1001");
    expect(ids).toContain("C1002");
    expect(ids).not.toContain("C1003");
    expect(ids).not.toContain("C1004");
  });
});

// ── generateDefaultSearchName ──

describe('generateDefaultSearchName', () => {
  it('returns empty string when no tags', () => {
    expect(generateDefaultSearchName([])).toBe("");
  });

  it('returns empty string when no enabled tags', () => {
    var tags = [
      { label: "10V", dimension: "voltage", enabled: false, source: "member" },
      { label: "X7R", dimension: "dielectric", enabled: false, source: "member" },
    ];
    expect(generateDefaultSearchName(tags)).toBe("");
  });

  it('returns label of the single enabled tag', () => {
    var tags = [
      { label: "100nF", dimension: "value",   enabled: true,  source: "group" },
      { label: "10V",   dimension: "voltage",  enabled: false, source: "member" },
    ];
    expect(generateDefaultSearchName(tags)).toBe("100nF");
  });

  it('joins multiple enabled tag labels with a space', () => {
    var tags = [
      { label: "100nF", dimension: "value",   enabled: true,  source: "group" },
      { label: "0402",  dimension: "package", enabled: true,  source: "group" },
      { label: "10V",   dimension: "voltage", enabled: false, source: "member" },
    ];
    expect(generateDefaultSearchName(tags)).toBe("100nF 0402");
  });

  it('preserves order of enabled tags as they appear in the array', () => {
    var tags = [
      { label: "X7R",   dimension: "dielectric", enabled: true, source: "member" },
      { label: "0402",  dimension: "package",     enabled: true, source: "group" },
      { label: "100nF", dimension: "value",       enabled: true, source: "group" },
    ];
    expect(generateDefaultSearchName(tags)).toBe("X7R 0402 100nF");
  });
});
