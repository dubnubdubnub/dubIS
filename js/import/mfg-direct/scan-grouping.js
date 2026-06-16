/* scan-grouping.js — Desktop editor to group uploaded scan photos into POs.
 *
 * A phone scan can carry several photos. Each photo defaults to its own PO; the
 * user taps photos to select them and groups the ones that belong to the same
 * order. On confirm, each group is concatenated (pages + parsed rows) into one
 * PO payload and handed to the caller's import queue. Mirrors the phone-side
 * grouping so the two stay consistent.
 */

import { escHtml } from '../../ui-helpers.js';

// Module-level editor state (one editor open at a time).
let _photos = [];      // [{ index, filename, image_b64, pages, prefill_rows }]
let _groupOf = [];      // _groupOf[photoIndex] = group id (int)
let _selected = new Set();   // selected photo indices (tap-select)
let _template = 'generic';
let _onDone = null;     // (orderedGroupPayloads) => void

/**
 * Open the grouping editor.
 * @param {Array} photos - per-photo OCR records from the scan payload
 * @param {Array<Array<number>>} groups - initial grouping (photo-index lists)
 * @param {string} template
 * @param {(payloads: Array<Object>) => void} onDone - called with one payload per PO
 */
export function openGroupingEditor(photos, groups, template, onDone) {
  _photos = Array.isArray(photos) ? photos : [];
  _template = template || 'generic';
  _onDone = onDone;
  _selected = new Set();
  // Seed each photo's group id from the initial grouping; uncovered → own group.
  _groupOf = _photos.map((_, i) => i);
  (groups || []).forEach((grp, gid) => {
    (grp || []).forEach((i) => { if (i >= 0 && i < _photos.length) _groupOf[i] = gid; });
  });
  _render();
}

/** Distinct groups as arrays of photo indices, ordered by their first photo. */
function _orderedGroups() {
  const byGroup = new Map();
  _photos.forEach((_, i) => {
    const g = _groupOf[i];
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(i);
  });
  const groups = Array.from(byGroup.values());
  groups.forEach((g) => g.sort((a, b) => a - b));
  groups.sort((a, b) => a[0] - b[0]);
  return groups;
}

function _freshGroupId() {
  return _photos.length ? Math.max(..._groupOf) + 1 : 0;
}

function _groupSelected() {
  if (_selected.size < 2) return;
  const id = _freshGroupId();
  _selected.forEach((i) => { _groupOf[i] = id; });
  _selected.clear();
  _render();
}

function _ungroupSelected() {
  if (!_selected.size) return;
  let next = _freshGroupId();
  // Deterministic order so ids track photo order.
  Array.from(_selected).sort((a, b) => a - b).forEach((i) => { _groupOf[i] = next; next += 1; });
  _selected.clear();
  _render();
}

function _thumbSrc(photo) {
  const pg = (photo.pages && photo.pages[0]) || null;
  const b64 = (pg && pg.image_b64) || photo.image_b64 || '';
  return 'data:image/png;base64,' + escHtml(b64);
}

function _close() {
  const o = document.getElementById('scan-grouping-overlay');
  if (o) o.remove();
}

function _render() {
  let overlay = document.getElementById('scan-grouping-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'scan-grouping-overlay';
    overlay.className = 'modal-overlay';
    document.body.appendChild(overlay);
  }

  const groups = _orderedGroups();
  const canGroup = _selected.size >= 2;
  const canUngroup = _selected.size >= 1;

  const sections = groups.map((grp, k) => {
    const thumbs = grp.map((i) => {
      const p = _photos[i];
      const sel = _selected.has(i) ? ' selected' : '';
      return `<div class="scan-thumb${sel}" data-idx="${i}" role="button" tabindex="0"
        title="${escHtml(p.filename || '')}">
        <img src="${_thumbSrc(p)}" alt="page ${i + 1}">
        <span class="scan-thumb-check">✓</span>
      </div>`;
    }).join('');
    const items = grp.reduce((n, i) => n + ((_photos[i].prefill_rows || []).length), 0);
    return `<div class="scan-po-group">
      <div class="scan-po-label">PO ${k + 1} <span class="scan-po-meta">${grp.length} photo${grp.length === 1 ? '' : 's'} · ${items} item${items === 1 ? '' : 's'}</span></div>
      <div class="scan-po-thumbs">${thumbs}</div>
    </div>`;
  }).join('');

  overlay.innerHTML = `<div class="modal modal-wide scan-grouping-modal">
    <div class="modal-title">Group photos into purchase orders</div>
    <div class="modal-subtitle">Each photo is its own PO. Tap photos that belong to the
      same order, then <strong>Group</strong> them. ${groups.length} order${groups.length === 1 ? '' : 's'} so far.</div>
    <div class="scan-grouping-actions">
      <button class="btn-sm" id="scan-group-btn" type="button" ${canGroup ? '' : 'disabled'}>Group selected</button>
      <button class="btn-sm" id="scan-ungroup-btn" type="button" ${canUngroup ? '' : 'disabled'}>Ungroup</button>
    </div>
    <div class="scan-grouping-list">${sections}</div>
    <div class="modal-actions">
      <button class="btn-md btn btn-cancel" id="scan-group-cancel" type="button">Cancel</button>
      <button class="btn-md btn" id="scan-group-import" type="button">Review &amp; import ${groups.length} PO${groups.length === 1 ? '' : 's'}</button>
    </div>
  </div>`;

  // Tap a thumbnail to toggle selection.
  overlay.querySelectorAll('.scan-thumb').forEach((el) => {
    const toggle = () => {
      const idx = Number(el.dataset.idx);
      if (_selected.has(idx)) _selected.delete(idx); else _selected.add(idx);
      _render();
    };
    el.addEventListener('click', toggle);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
    });
  });

  const groupBtn = overlay.querySelector('#scan-group-btn');
  if (groupBtn) groupBtn.addEventListener('click', _groupSelected);
  const ungroupBtn = overlay.querySelector('#scan-ungroup-btn');
  if (ungroupBtn) ungroupBtn.addEventListener('click', _ungroupSelected);

  const cancelBtn = overlay.querySelector('#scan-group-cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', _close);

  const importBtn = overlay.querySelector('#scan-group-import');
  if (importBtn) {
    importBtn.addEventListener('click', () => {
      const payloads = buildGroupPayloads(_photos, _orderedGroups(), _template);
      _close();
      if (_onDone) _onDone(payloads);
    });
  }
}

/**
 * Build one PO payload per group: concatenate that group's pages + parsed rows,
 * and carry the first photo's original bytes as the PO source file. Pure (no
 * DOM) so it can be unit-tested.
 * @param {Array} photos
 * @param {Array<Array<number>>} orderedGroups
 * @param {string} template
 * @returns {Array<Object>}
 */
export function buildGroupPayloads(photos, orderedGroups, template) {
  const total = orderedGroups.length;
  return orderedGroups.map((grp, k) => {
    const pages = [];
    const rows = [];
    grp.forEach((i) => {
      pages.push(...((photos[i] && photos[i].pages) || []));
      rows.push(...((photos[i] && photos[i].prefill_rows) || []));
    });
    const first = photos[grp[0]] || {};
    return {
      pages,
      prefill_rows: rows,
      line_items: rows,
      template,
      image_b64: first.image_b64 || '',
      filename: first.filename || 'scan.jpg',
      poLabel: `PO ${k + 1} of ${total}`,
      poIndex: k,
      poTotal: total,
    };
  });
}
