/**
 * data-text.mjs — the data-vs-chrome classifier behind
 * tests/js/e2e/data-text-selectable.spec.mjs.
 *
 * The app's rule is "every value the user reads is selectable and offers a copy
 * affordance; only controls opt out". Enforcing that needs a decision procedure
 * for *which* on-screen text is a value, and it has to be reviewable — a vague
 * heuristic would silently absolve a newly-added unselectable data cell, which
 * is precisely the regression the spec exists to catch.
 *
 * So: the spec harvests plain, JSON-serialisable FACTS about every text-bearing
 * element in the page (see `harvestLeaves`), and this module — pure, no DOM —
 * turns each fact bundle into { kind, reason }. Both halves are testable on
 * their own; the reasons show up verbatim in failure messages.
 *
 * Adding an exemption means adding an entry to CHROME_CLASSES *with a reason*.
 * That is the only way past the spec, and it is meant to feel deliberate.
 */

/** Tags that are controls or control labels wherever they appear. */
export const CHROME_TAGS = ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL', 'OPTION', 'SUMMARY'];

/**
 * Tags whose text is a column/row heading rather than a value. `TH` is the
 * table equivalent of the inventory's `.inv-col-header`.
 */
export const HEADING_TAGS = ['TH'];

/**
 * Classes that mark an element (or any of its ancestors) as UI chrome. Each
 * entry is the reason it is exempt. Keep them specific: a broad match here is
 * how an unselectable value slips through.
 */
export const CHROME_CLASSES = {
  // ── Inventory panel ──
  'inv-col-header': 'column-header row: sort buttons, not values',
  'inv-col-cell': 'a sort button inside the column header',
  'inv-section-header': 'click-to-collapse category header; its text is the control label',
  'inv-parent-header': 'click-to-collapse parent-category header',
  'inv-subsection-header': 'click-to-collapse subcategory header',
  'generic-group-header': 'click-to-collapse generic-group header',
  'inv-drag-handle': 'drag target for the group flyout',
  'inv-other-divider': 'section divider label',
  'chevron': 'collapse/expand glyph',
  'inv-section-count': 'live count rendered as part of the collapse control',
  'vendor-icon': 'decorative distributor glyph',
  'part-actions': 'the row action buttons (Adjust / Link / Groups)',
  'new-mark': 'freshly-imported marker glyph',

  // ── Panel + app chrome ──
  'panel-header': 'panel title bar and its controls',
  'header': 'app header: toolbar buttons and the zoom slider',
  'filter-chips-bar': 'filter pills — clicking toggles a filter',
  'dist-filter': 'distributor filter pills',
  'saved-views-bar': 'saved-view buttons',
  'tab': 'tab control',
  'tabs': 'tab strip',
  'modal-header': 'modal title bar',
  'modal-footer': 'modal action buttons',
  'btn-group': 'a group of buttons',
  'sticky-scrollbar': 'synthetic scrollbar; carries no text of its own',

  // ── Popovers and overlays owned by this work ──
  'text-popover': 'the copy popover itself — it *is* the affordance',
  'part-preview': 'the part tooltip; its own Copy controls are asserted separately',
  'toast': 'transient notification',
  'command-palette': 'command launcher',

  // ── Import / cart chrome ──
  'import-zone-header': 'import zone title bar',
  'cart-del-line': 'per-line delete button',
  'cart-badge': 'count badge on the cart button',
  'dg-head': 'DataGrid header row',
};

/**
 * Text with no letters and no digits anywhere in it — a chevron, an arrow, a
 * warning triangle, a stray bracket. There is nothing in it to copy, and
 * enumerating icon codepoints instead would go stale the first time someone
 * picked a new glyph.
 */
export const GLYPH_ONLY = /^[^\p{L}\p{N}]+$/u;

/**
 * @typedef {object} LeafFacts
 * @property {string} selector    readable path, used in failure messages
 * @property {string} text        the element's own visible text
 * @property {string} tag         uppercase tagName
 * @property {string[]} classes   the element's own classes
 * @property {string[]} chainTags uppercase tagNames of self + ancestors
 * @property {string[]} chainClasses classes of self + ancestors
 * @property {string} userSelect  computed (inherited) user-select
 * @property {boolean} ariaHidden aria-hidden="true" on self or an ancestor
 * @property {boolean} editable   inside a contenteditable
 * @property {boolean} visible    has a non-zero box and is not display:none
 * @property {{x:number,y:number,w:number,h:number}} box
 * @property {{x:number,y:number,w:number,h:number}[]} lines client rects of the
 *   element's own text, one per rendered line — the target a real drag needs
 * @property {boolean} occluded another element is on top of the text, so no
 *   pointer can reach it
 * @property {string} hit what `elementFromPoint` actually returns over the text
 */

/**
 * The widest rendered line of an element's own text: the best single stroke for
 * a drag to cover.
 * @param {{lines: {x:number,y:number,w:number,h:number}[]}} leaf
 */
export function widestLine(leaf) {
  if (!leaf.lines || !leaf.lines.length) return null;
  return leaf.lines.reduce((a, b) => (b.w > a.w ? b : a));
}

/**
 * Did a drag highlight THIS value's glyphs?
 *
 * "Selected exactly the value" is the wrong bar for a general sweep, and not
 * because the app is at fault: an ellipsised cell only renders part of its text,
 * and a wrapped cell's widest line is one line of several, so a single stroke
 * legitimately returns a fragment. "Non-empty" is too weak in the other
 * direction — it passes when the drag grabbed some neighbouring cell instead.
 *
 * The bar in between: the selection must share a run of `minRun` consecutive
 * characters with the value (whole value if it is shorter). That is impossible
 * to satisfy by accident from unrelated text, and impossible to fail if the
 * drag highlighted the value at all.
 *
 * @param {string} got the selection, whitespace-normalised
 * @param {string} want the value's own text, whitespace-normalised
 * @param {number} [minRun]
 */
export function sharesRun(got, want, minRun = 4) {
  const a = (got || '').toUpperCase();
  const b = (want || '').toUpperCase();
  if (!a || !b) return false;
  const k = Math.min(minRun, b.length);
  for (let i = 0; i + k <= b.length; i++) {
    if (a.includes(b.slice(i, i + k))) return true;
  }
  return false;
}

/**
 * Decide whether a text-bearing element is a data VALUE (must be selectable and
 * copyable) or UI CHROME (exempt).
 *
 * @param {LeafFacts} f
 * @returns {{ kind: 'data'|'chrome', reason: string }}
 */
export function classifyLeaf(f) {
  const chrome = (reason) => ({ kind: /** @type {'chrome'} */ ('chrome'), reason });

  if (!f.visible) return chrome('not visible');
  if (f.ariaHidden) return chrome('aria-hidden: presentational');
  if (f.editable) return chrome('inside a contenteditable / input');

  for (const tag of f.chainTags) {
    if (CHROME_TAGS.includes(tag)) return chrome(`inside <${tag.toLowerCase()}>: a control`);
    if (HEADING_TAGS.includes(tag)) return chrome(`inside <${tag.toLowerCase()}>: a heading, not a value`);
  }

  for (const cls of f.chainClasses) {
    if (Object.prototype.hasOwnProperty.call(CHROME_CLASSES, cls)) {
      return chrome(`.${cls} — ${CHROME_CLASSES[cls]}`);
    }
  }

  if (GLYPH_ONLY.test(f.text)) return chrome('glyph/punctuation only: nothing to copy');

  return { kind: 'data', reason: 'a value the user reads' };
}

/**
 * Split harvested facts into the two buckets, keeping each decision's reason so
 * a failure can explain itself.
 * @param {LeafFacts[]} facts
 */
export function partitionLeaves(facts) {
  const data = [];
  const chrome = [];
  for (const f of facts) {
    const verdict = classifyLeaf(f);
    (verdict.kind === 'data' ? data : chrome).push({ ...f, reason: verdict.reason });
  }
  return { data, chrome };
}

/**
 * The in-page harvester. Passed straight to `page.evaluate`, which stringifies
 * it and runs it in the browser — so it must stay entirely self-contained: no
 * closure references, no imports.
 *
 * "Text-bearing" deliberately means *has at least one non-empty direct text-node
 * child* rather than *has no element children*. A cell like
 * `<span class="part-qty"><button>&#9888;</button>30</span>` shows the value 30
 * next to a control, and a leaf-only walk would skip it — hiding exactly the
 * kind of cell that ends up with no copy affordance.
 *
 * The returned order is stable and reproducible (document order of
 * `querySelectorAll('*')` filtered to text-bearing elements), which is what lets
 * a later in-page pass re-find the same element by index.
 *
 * @param {string} rootSelector where to start walking
 * @returns {LeafFacts[]}
 */
export function harvestLeaves(rootSelector) {
  const root = document.querySelector(rootSelector);
  if (!root) return [];
  const out = [];
  const ownText = (el) => {
    let t = '';
    for (const n of el.childNodes) if (n.nodeType === 3) t += n.nodeValue;
    return t.trim();
  };
  const pathOf = (el) => {
    const bits = [];
    let n = el;
    for (let i = 0; n && n !== document.body && i < 6; i++) {
      let b = n.tagName.toLowerCase();
      if (n.id) { bits.unshift(b + '#' + n.id); break; }
      if (n.classList.length) b += '.' + Array.from(n.classList).join('.');
      bits.unshift(b);
      n = n.parentElement;
    }
    return bits.join(' > ');
  };
  /* Client rects of the element's OWN text, one per rendered line. A drag has to
     travel along a line of glyphs; the element's border box is the wrong target
     — it can be padding-heavy, right-aligned, or (in the cart's narrow columns)
     12px wide and 700px tall, where a horizontal drag crosses no text at all. */
  const lineRects = (el) => {
    const lines = [];
    for (const n of el.childNodes) {
      if (n.nodeType !== 3 || !n.nodeValue.trim()) continue;
      const r = document.createRange();
      r.selectNodeContents(n);
      for (const cr of r.getClientRects()) {
        if (cr.width > 1 && cr.height > 1) {
          lines.push({ x: cr.x, y: cr.y, w: cr.width, h: cr.height });
        }
      }
    }
    return lines;
  };
  /* Is another element sitting on top of this text? The matched-BOM table's
     action column is `position: sticky` and deliberately floats over the qty
     cells as the table scrolls horizontally (see sticky-buttons.spec.mjs, which
     must not be weakened). A pointer cannot reach text underneath it — that is
     an overlay question, not a selection-policy one, so the real-drag sample
     skips these while the CSS and Range checks still cover them. */
  const hitState = (el, lines) => {
    const line = lines.length ? lines.reduce((a, b) => (b.w > a.w ? b : a)) : null;
    if (!line) return { occluded: false, hit: '' };
    const hit = document.elementFromPoint(line.x + line.w / 2, line.y + line.h / 2);
    const reachable = !!hit && (hit === el || el.contains(hit) || hit.contains(el));
    return {
      occluded: !reachable,
      hit: hit ? hit.tagName.toLowerCase()
        + (hit.classList.length ? '.' + Array.from(hit.classList).join('.') : '') : 'nothing',
    };
  };
  for (const el of root.querySelectorAll('*')) {
    const text = ownText(el);
    if (!text) continue;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const chainTags = [];
    const chainClasses = [];
    let ariaHidden = false;
    let editable = false;
    let n = el;
    while (n && n !== document.documentElement) {
      chainTags.push(n.tagName);
      for (const c of n.classList) chainClasses.push(c);
      if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') ariaHidden = true;
      if (n.isContentEditable) editable = true;
      n = n.parentElement;
    }
    out.push({
      selector: pathOf(el),
      text,
      tag: el.tagName,
      classes: Array.from(el.classList),
      chainTags,
      chainClasses,
      userSelect: cs.userSelect || cs.webkitUserSelect,
      ariaHidden,
      editable,
      visible: cs.display !== 'none' && cs.visibility !== 'hidden'
        && rect.width > 0 && rect.height > 0,
      box: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
      lines: lineRects(el),
      ...hitState(el, lineRects(el)),
    });
  }
  return out;
}

/**
 * Group data leaves by the shape of the cell they live in, so the expensive
 * per-element checks (a real drag, a real hover) can run once per *kind* of cell
 * instead of once per row. The signature is the element's own class list, or its
 * tag when it has none.
 * @param {{classes: string[], tag: string}[]} leaves
 */
export function leafKind(leaf) {
  return leaf.classes.length ? leaf.classes.join('.') : leaf.tag.toLowerCase();
}

/**
 * One representative per cell kind — the sample the drag/hover assertions run
 * against.
 * @template {{classes: string[], tag: string}} T
 * @param {T[]} leaves
 * @returns {T[]}
 */
export function oneOfEachKind(leaves) {
  const seen = new Set();
  const out = [];
  for (const leaf of leaves) {
    const k = leafKind(leaf);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(leaf);
  }
  return out;
}
