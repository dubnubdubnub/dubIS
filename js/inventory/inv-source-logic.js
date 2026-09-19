// @ts-check
/* inv-source-logic.js — provenance decisions for a merged inventory row.

   Pure: no DOM, no store, no fetch. Everything the merged view shows or refuses
   is decided here, for the same reason js/server-tabs-logic.js exists — the
   interesting half of this feature is not the rendering, it is what a row is
   allowed to claim and where a write is allowed to land.

   The shape this reads is `domain/federation.py`'s merged record:

     { ...inventory fields,
       qty: 1250,                                    // summed across sources
       sources:   [{id: "bench", name: "bench", qty: 850},
                   {id: "shop",  name: "shop",  qty: 400}],
       conflicts: ["description"] }

   Two invariants from that module are relied on and never re-checked here:
   `sources` entries sum to the row's `qty`, and a single-source row still gets
   a one-entry array. A row from a NON-merged view has neither key, and every
   function below treats that as "no provenance to show" rather than as an
   error — the same code renders both views.

   ── Why the two extra fields are declared HERE and not in domain/schema.py ──

   `INVENTORY_FIELDS` describes an inventory record *as one server stores and
   serves it*: every entry needs a `sql_col`/`table`/`sql_ddl`, `server/models.py`
   turns each one into a REQUIRED Pydantic field, and `ts_type` only speaks
   `string | number | string[]`. `sources` and `conflicts` are none of those
   things — they are computed one level above, by the merge, out of N servers'
   answers, and adding them there would make the ordinary single-server
   `GET /v1/parts` fail its own response validation. So the merged record is
   declared as an EXTENSION of the generated one, where the merged UI lives. */

/**
 * One source's contribution to a merged row.
 * @typedef {{id: string, name: string, qty: number}} SourceEntry
 */

/**
 * An inventory record as the MERGED view serves it: the generated record plus
 * the two keys `domain/federation.py` manufactures. Both optional, because the
 * same rows, renderers and filters serve a single-server view, where neither
 * key exists at all.
 *
 * @typedef {import('../types.js').InventoryItem & {
 *   sources?: Array<SourceEntry>,
 *   conflicts?: Array<string>
 * }} MergedInventoryItem
 */

/**
 * The per-source breakdown of one row, normalized.
 *
 * Defensive about shape, not about meaning: an entry with no usable id is
 * dropped (it could not be written to, and naming it in the UI would offer the
 * user a target that does not exist), but nothing here second-guesses the
 * quantities — the merge already guaranteed they sum to `qty`, and "fixing" a
 * total in the renderer is how two parts of an app start disagreeing.
 *
 * @param {any} item an inventory record
 * @returns {Array<SourceEntry>} [] when the row is not from a merged view
 */
export function sourceEntries(item) {
  const raw = item && item.sources;
  if (!Array.isArray(raw)) return [];
  /** @type {Array<SourceEntry>} */
  const out = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const id = typeof entry.id === 'string' ? entry.id.trim() : '';
    if (!id) continue;
    const name = typeof entry.name === 'string' && entry.name.trim() ? entry.name.trim() : id;
    out.push({ id, name, qty: Number(entry.qty) || 0 });
  }
  return out;
}

/**
 * Is this row from a merged view at all?
 * @param {any} item
 * @returns {boolean}
 */
export function isMergedRow(item) {
  return sourceEntries(item).length > 0;
}

/**
 * The server names holding this part, in source order.
 * @param {any} item
 * @returns {Array<string>}
 */
export function sourceNames(item) {
  return sourceEntries(item).map((s) => s.name);
}

/**
 * What the row's provenance badge says.
 *
 * A single-source row NAMES its server: with one server per row that is the
 * whole answer, and a count ("1 server") would make the reader click to learn
 * nothing. A multi-source row shows the COUNT and becomes expandable, because
 * the names do not fit in the group cell and, more importantly, because the
 * split quantities — not the names — are what someone reading a summed row
 * actually wants.
 *
 * @param {any} item
 * @returns {{show: boolean, multi: boolean, label: string, title: string,
 *            count: number, entries: Array<SourceEntry>}}
 */
export function sourceBadge(item) {
  const entries = sourceEntries(item);
  if (entries.length === 0) {
    return { show: false, multi: false, label: '', title: '', count: 0, entries };
  }
  if (entries.length === 1) {
    const only = entries[0];
    return {
      show: true,
      multi: false,
      label: only.name,
      title: 'All ' + only.qty + ' on ' + only.name,
      count: 1,
      entries,
    };
  }
  const total = entries.reduce((sum, s) => sum + s.qty, 0);
  return {
    show: true,
    multi: true,
    label: entries.length + ' servers',
    title: total + ' across ' + entries.map((s) => s.name + ' (' + s.qty + ')').join(', ')
      + ' — click for the breakdown',
    count: entries.length,
    entries,
  };
}

// ── Conflicts ─────────────────────────────────────────────

/**
 * The fields where the merge had to pick between two disagreeing servers.
 *
 * @param {any} item
 * @returns {Array<string>}
 */
export function conflictFields(item) {
  const raw = item && item.conflicts;
  if (!Array.isArray(raw)) return [];
  return raw.filter((f) => typeof f === 'string' && f.trim()).map((f) => f.trim());
}

/**
 * Whether one displayed field is one of the disagreeing ones.
 * @param {any} item
 * @param {string} field
 * @returns {boolean}
 */
export function hasConflict(item, field) {
  return conflictFields(item).indexOf(field) !== -1;
}

/**
 * The sentence the conflict marker carries.
 *
 * Deliberately says which VALUE is on screen, not just that there was a
 * disagreement: the merge resolved it by first-non-empty in source order
 * (domain/federation.py), so the row is already showing one server's answer,
 * and the point of the marker is to admit that rather than to alarm.
 *
 * @param {any} item
 * @returns {string} "" when nothing conflicts
 */
export function conflictNote(item) {
  const fields = conflictFields(item);
  if (!fields.length) return '';
  const entries = sourceEntries(item);
  const winner = entries.length ? entries[0].name : 'the first source';
  return 'Servers disagree on ' + fields.join(', ')
    + ' — showing ' + winner + '’s value';
}

// ── Where a write goes ────────────────────────────────────

/**
 * The view's own honesty, as a write has to hear it.
 *
 * `{degraded, missing}`: whether the fetch these rows came from reached every
 * source, and the names of the ones it did not. Derived from the same `sources`
 * status array the banner reads, so the two consumers of a partial view — the
 * user and the write path — cannot end up believing different things about it.
 *
 * @param {Array<any>} statuses the `sources` array from `GET /v1/parts`
 * @returns {{degraded: boolean, missing: Array<string>}}
 */
export function viewStateFrom(statuses) {
  const note = partialViewNote(statuses);
  return { degraded: note.degraded, missing: note.failed.map((f) => f.name) };
}

/** `{degraded, missing}` read defensively out of whatever the caller passed. */
function readView(view) {
  const v = view && typeof view === 'object' ? view : {};
  const missing = Array.isArray(v.missing) ? v.missing.filter((n) => typeof n === 'string') : [];
  return { degraded: !!v.degraded, missing };
}

/** "shop" / "shop and attic" / a fallback when the names are not known. */
function missingPhrase(missing) {
  if (!missing.length) return 'A server that did not answer';
  if (missing.length === 1) return missing[0];
  return missing.slice(0, -1).join(', ') + ' and ' + missing[missing.length - 1];
}

/**
 * Which server a write about this row must be sent to.
 *
 * Four answers, and the last two are the reason this function exists:
 *
 * - Not a merged row → `{ok: true, sourceId: ''}`. No routing needed; the hub's
 *   own default serves the request, exactly as it does today.
 * - One source → `{ok: true, sourceId: <that server>}`. Unambiguous.
 * - Several sources → `{ok: false, ...}` unless the caller passes `chosen`.
 *   A part with 850 on the bench and 400 in the shop has no "the" server, and
 *   picking the biggest, or the first, would silently move stock on a machine
 *   the user was not thinking about. The hub refuses a merged write target for
 *   the same reason (400, server/sources.py), so guessing here would only move
 *   the failure somewhere less legible.
 * - **A DEGRADED view → `{ok: false, ...}` unless the caller passes `chosen`,
 *   however few sources the row names.** This is the case that looks like
 *   nothing at all. The fan-out degrades rather than failing
 *   (server/fanout.py), and a row lists only the sources that ANSWERED — so
 *   with the shop asleep, a part that is 850 on the bench and 400 in the shop
 *   comes back as 850 naming bench alone, byte-for-byte identical to a part
 *   that only ever lived on the bench. "One source" then means "one source
 *   answered", which is a different claim, and treating it as unambiguous
 *   routes a quantity the user worked out from an incomplete total to a server
 *   they were never asked about. The banner tells the USER the totals are
 *   incomplete; this is the same fact reaching the one other place where it
 *   changes what happens.
 *
 * `chosen` is the escape hatch in both refusing cases: a user who names a
 * server has made the decision themselves, which is the whole point of asking.
 * It is validated against the row rather than trusted — a stale choice, a
 * server picked before a refresh dropped that source, must not become a write
 * to a server that no longer holds the part.
 *
 * @param {any} item
 * @param {string} [chosen] a source id the user explicitly picked
 * @param {{degraded?: boolean, missing?: Array<string>}} [view] from
 *   `viewStateFrom()`. Omitted means "the view saw everything", which is the
 *   truth for every single-server view and the only safe default for a caller
 *   written before this argument existed.
 * @returns {{ok: boolean, sourceId: string, ambiguous: boolean, degraded: boolean,
 *            options: Array<SourceEntry>, reason: string}}
 */
export function writeTarget(item, chosen, view) {
  const entries = sourceEntries(item);
  const pick = typeof chosen === 'string' ? chosen.trim() : '';
  const { degraded, missing } = readView(view);

  if (entries.length === 0 && !degraded) {
    return { ok: true, sourceId: '', ambiguous: false, degraded, options: [], reason: '' };
  }
  if (pick && entries.length) {
    const match = entries.find((s) => s.id === pick);
    if (match) {
      return {
        ok: true, sourceId: match.id, ambiguous: false, degraded,
        options: entries, reason: '',
      };
    }
    return {
      ok: false,
      sourceId: '',
      ambiguous: true,
      degraded,
      options: entries,
      reason: 'That server no longer holds this part \u2014 pick one of: '
        + entries.map((s) => s.name).join(', '),
    };
  }
  if (degraded) {
    return {
      ok: false,
      sourceId: '',
      ambiguous: true,
      degraded,
      options: entries,
      // Names what is missing, because the user's next move depends on it:
      // wait for that server to come back, or decide anyway with the chooser.
      reason: missingPhrase(missing) + ' did not answer, so this part may be stocked '
        + 'there too \u2014 choose which server to write to',
    };
  }
  if (entries.length === 1) {
    return {
      ok: true, sourceId: entries[0].id, ambiguous: false, degraded,
      options: entries, reason: '',
    };
  }
  return {
    ok: false,
    sourceId: '',
    ambiguous: true,
    degraded,
    options: entries,
    reason: 'This part\u2019s stock is split across '
      + entries.map((s) => s.name + ' (' + s.qty + ')').join(' and ')
      + ' \u2014 choose which server to adjust',
  };
}

/**
 * The single server a MULTI-row write (a BOM consume) can be sent to.
 *
 * A consume is one request carrying many part keys, and the hub serves one
 * request from one server. So the answer is only unambiguous when every part
 * being consumed lives on the same single server; anything else has to stop and
 * ask, because splitting the write across servers would mean issuing several
 * consumes, each of which can fail independently — a half-consumed BOM, with no
 * transaction to roll it back.
 *
 * A DEGRADED view stops it too, and this is the sharper half. The span between
 * servers is exactly what this function refuses on, and a partial view HIDES
 * that span: with the shop asleep every row names bench alone, so a BOM whose
 * parts are really spread across both looks like a tidy bench-only consume. It
 * would route wholly to the bench, succeed, and take the shop's parts out of
 * the bench's stock — wrong about several parts at once, with nothing failing.
 * Unlike `writeTarget` there is no chooser to fall back on here (a consume has
 * no per-row conversation), so the honest answer is to stop and say why.
 *
 * @param {Array<any>} items the inventory rows a write will touch
 * @param {{degraded?: boolean, missing?: Array<string>}} [view] from
 *   `viewStateFrom()`; omitted means the view saw every source.
 * @returns {{ok: boolean, sourceId: string, ambiguous: boolean, degraded: boolean,
 *            options: Array<SourceEntry>, reason: string}}
 */
export function writeTargetForAll(items, view) {
  /** @type {Map<string, SourceEntry>} */
  const seen = new Map();
  let anyMerged = false;
  let splitRow = null;
  for (const item of items || []) {
    const entries = sourceEntries(item);
    if (!entries.length) continue;
    anyMerged = true;
    if (entries.length > 1 && !splitRow) splitRow = item;
    for (const entry of entries) {
      if (!seen.has(entry.id)) seen.set(entry.id, entry);
    }
  }
  const options = [...seen.values()];
  const { degraded, missing } = readView(view);

  if (degraded) {
    return {
      ok: false,
      sourceId: '',
      ambiguous: true,
      degraded,
      options,
      reason: missingPhrase(missing) + ' did not answer, so these rows may not show '
        + 'every server a part is stocked on \u2014 wait for it, or consume from one '
        + 'server at a time (pick its tab)',
    };
  }
  if (!anyMerged) {
    return { ok: true, sourceId: '', ambiguous: false, degraded, options: [], reason: '' };
  }
  if (splitRow) {
    return {
      ok: false,
      sourceId: '',
      ambiguous: true,
      degraded,
      options,
      reason: 'Some of these parts are stocked on more than one server \u2014 consume '
        + 'from one server at a time (pick its tab) so no BOM is half-consumed',
    };
  }
  if (options.length === 1) {
    return {
      ok: true, sourceId: options[0].id, ambiguous: false, degraded, options, reason: '',
    };
  }
  return {
    ok: false,
    sourceId: '',
    ambiguous: true,
    degraded,
    options,
    reason: 'These parts are stocked on ' + options.map((s) => s.name).join(' and ')
      + ' \u2014 consume from one server at a time (pick its tab)',
  };
}

// ── A view that is missing a server ───────────────────────

/**
 * What the last merged fetch's per-source status means for the totals on screen.
 *
 * The fan-out degrades rather than fails (server/fanout.py): a source that is
 * asleep costs its stock and nothing else, and the response is still a 200 full
 * of perfectly plausible numbers. Under-reporting stock silently is the worst
 * outcome this feature has, so a failed source has to become a sentence.
 *
 * @param {Array<any>} statuses the `sources` array from `GET /v1/parts`
 * @returns {{degraded: boolean, failed: Array<{id: string, name: string, error: string}>,
 *            message: string}}
 */
export function partialViewNote(statuses) {
  const failed = [];
  for (const entry of statuses || []) {
    if (!entry || typeof entry !== 'object') continue;
    if (entry.ok) continue;
    const id = typeof entry.id === 'string' ? entry.id : '';
    const name = typeof entry.name === 'string' && entry.name.trim() ? entry.name.trim() : (id || 'a server');
    failed.push({ id, name, error: typeof entry.error === 'string' ? entry.error : '' });
  }
  if (!failed.length) return { degraded: false, failed, message: '' };
  const names = failed.map((f) => f.name).join(', ');
  return {
    degraded: true,
    failed,
    // "Totals are incomplete" and not just "a server is down": the second is
    // what happened, the first is what it means for what is on screen.
    message: names + (failed.length === 1 ? ' did not answer' : ' did not answer either')
      + ' — these totals are incomplete',
  };
}

// ── Filtering ─────────────────────────────────────────────

/**
 * Every server name present in the current inventory, sorted.
 *
 * Derived from the rows rather than from the roster, exactly as the section
 * chip's options are: the roster can name a server that contributed nothing to
 * this view (disabled, or down), and an option that can only ever match zero
 * rows is a filter that looks broken.
 *
 * @param {Array<any>} inventory
 * @returns {Array<string>}
 */
export function serverOptions(inventory) {
  const names = new Set();
  for (const item of inventory || []) {
    for (const name of sourceNames(item)) names.add(name);
  }
  return [...names].sort();
}
