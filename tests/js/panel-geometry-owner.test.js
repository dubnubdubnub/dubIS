/* Single-owner guard for the three-panel row's geometry.

   js/resize-panels.js is the only module allowed to write the inline flex/width
   of #panel-import, #panel-inventory and #panel-bom. A second writer is how the
   "hide Purchase Import → dead strip beside the BOM" bug was built: collapse
   saved and restored the same inline styles resize-panels wrote, so each module
   held half of an encoding neither fully owned. The model-based spec
   (tests/js/e2e/panel-layout-model.spec.mjs) catches the symptom in a browser;
   this catches the cause at the source, in milliseconds.

   Scope — deliberately not "no style.width anywhere": table headers, sticky
   scrollbars, inline-edit inputs and the OCR band all write widths on their own
   elements, legitimately (js/inventory/*, js/components/data-grid.js, ...).
   Statically we cannot know which element a write lands on, so the rule is split
   by how specific the property is:

     A. flex-family writes (flex, flex-grow/-shrink/-basis), anywhere in js/.
        Nothing else in the app sizes by inline flex, so any such write is
        either panel geometry or new enough to deserve a look; allow-list it
        below with a reason if it is genuinely something else.
     B. width-family writes (width, min-/max-width, cssText, Object.assign onto
        a style), only in modules that can reach a panel element — that name a
        panel id or class, or the dom-refs getters for them. */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const JS_ROOT = fileURLToPath(new URL('../../js', import.meta.url));
const OWNER = 'js/resize-panels.js';

/** Rule-A exemptions: path → why its flex write is not panel geometry. */
const FLEX_ALLOWED = {};

const FLEX_WRITE = new RegExp([
  String.raw`\.style\.flex(Grow|Shrink|Basis)?\s*=(?!=)`,
  String.raw`\.style\[\s*['"\x60]flex(Grow|Shrink|Basis)?['"\x60]\s*\]\s*=(?!=)`,
  String.raw`setProperty\(\s*['"\x60]flex(-grow|-shrink|-basis)?['"\x60]`,
].join('|'));

const WIDTH_WRITE = new RegExp([
  String.raw`\.style\.(width|minWidth|maxWidth|cssText)\s*=(?!=)`,
  String.raw`\.style\[\s*['"\x60](width|minWidth|maxWidth)['"\x60]\s*\]\s*=(?!=)`,
  String.raw`setProperty\(\s*['"\x60](min-|max-)?width['"\x60]`,
  String.raw`Object\.assign\(\s*[\w.$]+\.style\b`,
].join('|'));

/** How a module gets hold of a panel element. */
const REACHES_PANEL = /panel-(import|inventory|bom)\b|\bpanel(Import|Inventory|Bom)\b|\bpanelId\b/;

function jsFiles(dir = JS_ROOT, out = []) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name !== 'vendor') jsFiles(full, out);
    } else if (name.endsWith('.js')) out.push(full);
  }
  return out;
}

/** Strip comments so prose about style.flex (like resize-panels' own header) is not a write. */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const FILES = jsFiles().map(full => ({
  path: relative(join(JS_ROOT, '..'), full).replace(/\\/g, '/'),
  code: stripComments(readFileSync(full, 'utf8')),
}));

describe('panel geometry has a single owner', () => {
  it('finds the js/ tree and the owner (guard is not silently scanning nothing)', () => {
    expect(FILES.length).toBeGreaterThan(40);
    const owner = FILES.find(f => f.path === OWNER);
    expect(owner, `${OWNER} should exist`).toBeTruthy();
    // If the owner stops matching, the detector has drifted from how the owner
    // actually writes, and every other module's clean result means nothing.
    expect(owner?.code).toMatch(FLEX_WRITE);
    expect(owner?.code).toMatch(WIDTH_WRITE);
  });

  it('the detectors flag the writes that built the dead-strip bug', () => {
    // Verbatim from the panel-collapse.js savedSizing restore this guard replaced.
    expect('node.style.flex = saved.flex;').toMatch(FLEX_WRITE);
    expect('node.style.width = saved.width;').toMatch(WIDTH_WRITE);
    expect("el.style.setProperty('flex-grow', '1')").toMatch(FLEX_WRITE);
    expect('Object.assign(node.style, { width: "0px" })').toMatch(WIDTH_WRITE);
    // Reads and comparisons are not writes.
    expect('if (node.style.flex === "") {}').not.toMatch(FLEX_WRITE);
    expect('const w = node.style.width;').not.toMatch(WIDTH_WRITE);
  });

  it('A: no module but the owner writes an inline flex', () => {
    const offenders = FILES
      .filter(f => f.path !== OWNER && !(f.path in FLEX_ALLOWED) && FLEX_WRITE.test(f.code))
      .map(f => f.path);
    expect(offenders,
      `Only ${OWNER} may write a panel's inline flex — collapse is the .panel-collapsed `
      + 'class, never a saved/restored style. If this write is not panel geometry, '
      + 'add it to FLEX_ALLOWED with the reason. Offenders: ' + offenders.join(', '))
      .toEqual([]);
  });

  it('B: no module that can reach a panel writes an inline width', () => {
    const offenders = FILES
      .filter(f => f.path !== OWNER && REACHES_PANEL.test(f.code) && WIDTH_WRITE.test(f.code))
      .map(f => f.path);
    expect(offenders,
      `Only ${OWNER} may write a panel's inline width. Offenders (each names a panel `
      + 'element and writes a width): ' + offenders.join(', '))
      .toEqual([]);
  });

  it('every FLEX_ALLOWED entry still exists and still writes flex', () => {
    for (const [path, reason] of Object.entries(FLEX_ALLOWED)) {
      const f = FILES.find(x => x.path === path);
      expect(f, `${path} is allow-listed (${reason}) but no longer exists`).toBeTruthy();
      expect(f?.code, `${path} is allow-listed but no longer writes flex`).toMatch(FLEX_WRITE);
    }
  });
});
