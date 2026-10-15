// @ts-check
/* threshold-profiles.js — the per-source cache of each server's ACTIVE
   threshold profile, and the staleness that says when it cannot be trusted.

   WHY A CACHE AT ALL

   A threshold is not one number the app owns; it is a per-server fact. Every
   dubIS server keeps its own snapshot history (domain/threshold_snapshots.py)
   and answers `GET /v1/meta` with the ACTIVE one as `active_threshold_profile`.
   The grid colours a row's qty cell against `getThreshold(section)`
   (js/inventory/inv-row-build.js), so a window showing server B must resolve
   with B's profile. One global copy is what let two callers disagree — the same
   part low on one screen and fine on the tab beside it. This module is the only
   place that holds those profiles, keyed by the source that answered.

   WHY NOT THROUGH `api()`

   `/v1/meta` is deliberately NOT in the generated api-map: SKIP_OPERATION_IDS
   in scripts/gen-api-client.py keeps infra routes off the bridge-style method
   surface, so there is no entry to build a URL from. js/server-probe.js fetches
   `/v1/health` the same hand-written way for the same reason. The header NAME
   is imported from js/api.js rather than restated here, because one definition
   of `X-Dubis-Source` is the whole frontend contract in server/dispatch.py.

   It also bypasses api()'s catch→toast wrapper on purpose — the precedent is
   js/inventory/distributor-fetch.js. A profile refresh runs on every tab
   switch, so a sleeping source would raise a toast every time. The failure is
   not swallowed: it becomes `state: 'stale'` on the entry plus one AppLog.warn
   naming the source, which is what the staleness marker renders.

   STALENESS IS A STATE, NOT A FALLBACK

   A failed refresh keeps that source's last-known profile and marks it stale.
   It never drops back to the build-time table: silently colouring rows with the
   defaults baked into THIS build is the exact failure this line of work exists
   to kill. Only a source that has never answered has nothing to resolve with,
   and that is its own state — `'unavailable'` after a failed first attempt,
   `'unknown'` before one — which the UI is required to say out loud rather than
   paper over. `resolveOptionsFor()` returns the empty object in both cases, so
   `resolveThreshold()` falls through to snapshot 0 / derivation / base exactly
   as the shared fixture defines, and the CALLER is the one that has to admit it
   is doing so (it asks `profileState()` first).

   Resolution order itself is NOT re-implemented here. `resolveOptionsFor()`
   hands `js/threshold-logic.js`'s `resolveThreshold()` the server layer and
   that server's own snapshot-0 table and base; the order lives in
   threshold-logic.js, mirrored in domain/thresholds.py, and pinned on both
   sides by tests/fixtures/thresholds/resolution-cases.json. */

import { SOURCE_HEADER, AppLog } from './api.js';
import { signal } from './signals.js';
import { LOCAL_ID, MERGED_ID } from './server-tabs-logic.js';

/** The route this cache reads. Hand-written because it is not in the generated
 *  api-map — see "WHY NOT THROUGH api()" above. (That heading carries no
 *  method-name literal on purpose: tests/python/test_api_surface.py scans every
 *  `js/**.js` for a call of that shape, comments included, and a name absent
 *  from the api-map is exactly what it would rightly reject.) */
const META_PATH = '/v1/meta';

/** What one server's `/v1/meta` said about its thresholds.
 * @typedef {object} SourceProfile
 * @property {Record<string, number>|null} active - `active_threshold_profile`
 * @property {Record<string, number>|null} defaults - `default_thresholds`, i.e.
 *           that server's snapshot 0 (NOT this build's generated table).
 * @property {number|null} base - `base_threshold`
 * @property {Record<string, any>|null} snapshots - `threshold_snapshots`
 *           metadata: count / active_id / active_created_by / active_created_at
 *           / active_note. What a staleness or provenance marker renders. */

/** `unknown` — never asked. `fresh` — the last request answered. `stale` — a
 *  later request failed and these are the last-known values. `unavailable` —
 *  the FIRST request failed, so there is nothing for this source at all.
 * @typedef {'unknown'|'fresh'|'stale'|'unavailable'} ProfileState */

/** One cache entry.
 * @typedef {object} ProfileEntry
 * @property {string} sourceId
 * @property {ProfileState} state
 * @property {SourceProfile|null} profile
 * @property {string} error - why it is stale/unavailable; '' when fresh
 * @property {number} fetchedAt - Date.now() of the answer that produced the
 *           profile; 0 when there never was one. */

/** @type {Map<string, ProfileEntry>} */
const _entries = new Map();

/** One in-flight refresh per source, so a fast double switch costs one request.
 * @type {Map<string, Promise<ProfileEntry|null>>} */
const _inflight = new Map();

let _revision = 0;

/**
 * Published whenever an entry changes, so the grid can repaint: row colour is
 * baked into the row HTML at build time, and a profile that lands AFTER
 * INVENTORY_LOADED would otherwise never reach the screen.
 *
 * Carries the source whose entry moved plus a revision that only ever grows —
 * a subscriber compares the source id and ignores publishes for a source it is
 * not showing. State, not an EventBus event: a panel that mounts after the
 * answer arrived must still be able to read it (js/signals.js).
 * @type {{get(): {sourceId: string, revision: number}, set(v: {sourceId: string, revision: number}): void, peek(): {sourceId: string, revision: number}}} */
export const thresholdProfileSignal = signal({ sourceId: '', revision: 0 });

/**
 * Which source's profile answers for a window's `X-Dubis-Source` value.
 *
 * ── THE MERGED / GROUPED DECISION ───────────────────────────────────────────
 * A merged or grouped tab resolves against the HUB's profile (`local`), not any
 * member's. A merged row is the SUM of several servers' stock and there is no
 * such thing as the merged view's threshold profile — the same absence of a
 * single owner is why a WRITE to `merged` is 400ed (server/sources.py's
 * Registry.resolve) and why a READ of anything but `/v1/parts` is served by the
 * hub (server/dispatch.py's outcome table). So the hub's active profile is not
 * a convenient stand-in for a merged view: it is literally the profile the
 * answering server holds. S3 replaces this with a per-source gradient selector;
 * until that exists this is the only honest answer, and it is deliberately a
 * function of the selector rather than a lookup that happens to miss.
 *
 * An EMPTY value means the window has not hydrated its tabs yet, and it gets
 * `''` — nothing to ask, nothing to resolve with. Guessing `local` there would
 * colour a remote window's first paint with the hub's numbers, which is the
 * same class of wrong this module exists to remove.
 *
 * @param {string} sourceValue - `getActiveSource()`: an id, `merged`, a
 *        comma-joined group, or `''` before hydration.
 * @returns {string} a source id, or `''` when there is nothing to ask.
 */
export function profileKeyFor(sourceValue) {
  const value = (sourceValue || '').trim();
  if (!value) return '';
  if (value === MERGED_ID || value.includes(',')) return LOCAL_ID;
  return value;
}

/** The cached entry for one source id, or null when nothing is known of it.
 *
 *  The cache's fundamental read — `activeProfile()` is this with the window's
 *  selector mapped through `profileKeyFor()`. Exported because a per-source
 *  view (S3's merged-view gradient) needs an entry that is NOT the active one.
 *  @param {string} sourceId
 *  @returns {ProfileEntry|null} */
export function profileFor(sourceId) {
  return _entries.get(sourceId) || null;
}

/** The entry the window resolves against right now.
 *  @param {string} sourceValue - `getActiveSource()`
 *  @returns {ProfileEntry|null} */
export function activeProfile(sourceValue) {
  return profileFor(profileKeyFor(sourceValue));
}

/** What the grid's colour is currently based on, for the marker (S3 renders it).
 *  @param {string} sourceValue - `getActiveSource()`
 *  @returns {ProfileState} */
export function profileState(sourceValue) {
  const entry = activeProfile(sourceValue);
  return entry ? entry.state : 'unknown';
}

/** Why the active profile is stale/unavailable; '' otherwise.
 *  @param {string} sourceValue - `getActiveSource()`
 *  @returns {string} */
export function profileError(sourceValue) {
  const entry = activeProfile(sourceValue);
  return entry ? entry.error : '';
}

/**
 * The `resolveThreshold()` options for the source behind `sourceValue`.
 *
 * Empty when that source has no profile — which is how "no server layer"
 * reaches the shared resolution order instead of becoming a local decision:
 * threshold-logic.js then falls through to the shipped table, derivation and
 * base exactly as the parity fixture defines. Callers that colour something the
 * user judges should read `profileState()` alongside this and say so when it is
 * not `'fresh'`.
 *
 * @param {string} sourceValue - `getActiveSource()`
 * @returns {import('./threshold-logic.js').ResolveOptions}
 */
export function resolveOptionsFor(sourceValue) {
  const entry = activeProfile(sourceValue);
  const profile = entry ? entry.profile : null;
  /** @type {import('./threshold-logic.js').ResolveOptions} */
  const options = {};
  if (!profile) return options;
  if (profile.active) options.server = profile.active;
  if (profile.defaults) options.defaults = profile.defaults;
  // `base` is OMITTED when unknown, never set to null: resolveThreshold() reads
  // only `undefined` as "no answer" (js/threshold-logic.js), and Number(null)
  // is 0 — the zero-default that once made `dubis low-stock` flag every part.
  if (typeof profile.base === 'number' && Number.isFinite(profile.base)) {
    options.base = profile.base;
  }
  return options;
}

/**
 * Ask one source for its profile, or join the request already running.
 *
 * Never rejects: a failed refresh is a state change, not an exception, because
 * the caller is a paint path. Resolves with the resulting entry, or null when
 * there was no source to ask.
 *
 * @param {string} sourceValue - `getActiveSource()`
 * @returns {Promise<ProfileEntry|null>} */
export function refreshProfile(sourceValue) {
  const key = profileKeyFor(sourceValue);
  if (!key) return Promise.resolve(null);
  const running = _inflight.get(key);
  if (running) return running;
  const done = _fetch(key).then(
    (entry) => { _inflight.delete(key); return entry; },
    (e) => { _inflight.delete(key); throw e; },
  );
  _inflight.set(key, done);
  return done;
}

// ── internals ─────────────────────────────────────────────

/** @param {string} sourceId @returns {Promise<ProfileEntry>} */
async function _fetch(sourceId) {
  const previous = _entries.get(sourceId) || null;
  /** @type {any} */
  let body = null;
  try {
    body = await fetchMeta(sourceId);
  } catch (e) {
    const error = e && /** @type {Error} */ (e).message
      ? /** @type {Error} */ (e).message : String(e);
    // Keep whatever we had. Downgrading to the build-time table here would make
    // a blip look like the server had reset its thresholds.
    /** @type {ProfileEntry} */
    const entry = {
      sourceId,
      state: previous && previous.profile ? 'stale' : 'unavailable',
      profile: previous ? previous.profile : null,
      error,
      fetchedAt: previous ? previous.fetchedAt : 0,
    };
    _entries.set(sourceId, entry);
    AppLog.warn(`thresholds: GET ${META_PATH} for "${sourceId}" failed (${error}) — ` +
      (entry.profile ? 'keeping the last-known profile' : 'no profile known for this server'));
    _publish(sourceId);
    return entry;
  }

  /** @type {ProfileEntry} */
  const entry = {
    sourceId,
    state: 'fresh',
    profile: toProfile(body),
    error: '',
    fetchedAt: Date.now(),
  };
  _entries.set(sourceId, entry);
  _publish(sourceId);
  return entry;
}

/**
 * `GET /v1/meta` pinned to ONE source.
 *
 * Throws on a non-2xx so `_fetch` can tell "this server said nothing" from
 * "this server said nothing useful" — the latter is a body that parsed to
 * something with no threshold keys, which is a fresh profile with nulls.
 *
 * @param {string} sourceId
 * @returns {Promise<any>} */
async function fetchMeta(sourceId) {
  const res = await fetch(META_PATH, {
    method: 'GET',
    headers: { [SOURCE_HEADER]: sourceId },
  });
  if (!res.ok) {
    let message = res.statusText || ('HTTP ' + res.status);
    try {
      const errBody = await res.json();
      if (errBody && errBody.error) message = errBody.error;
    } catch {
      // Non-JSON error body — fall back to statusText/status.
    }
    throw new Error(message);
  }
  return res.json();
}

/** The `/v1/meta` body -> a SourceProfile. A body from a server that predates
 *  the snapshot fields yields nulls, which is the honest reading of it: no
 *  server layer, and that server's own snapshot 0 / base where it has them.
 *  @param {any} body @returns {SourceProfile|null} */
function toProfile(body) {
  if (!body || typeof body !== 'object') return null;
  return {
    active: numberTable(body.active_threshold_profile),
    defaults: numberTable(body.default_thresholds),
    base: typeof body.base_threshold === 'number' && Number.isFinite(body.base_threshold)
      ? body.base_threshold : null,
    snapshots: body.threshold_snapshots && typeof body.threshold_snapshots === 'object'
      ? body.threshold_snapshots : null,
  };
}

/** A `{section: number}` map, or null when there is nothing to say. An empty
 *  map is null on purpose: `resolveThreshold()` treats null and absent
 *  identically, and a `{}` would be a third way of meaning "no opinion".
 *  @param {any} raw @returns {Record<string, number>|null} */
function numberTable(raw) {
  if (!raw || typeof raw !== 'object') return null;
  /** @type {Record<string, number>} */
  const out = {};
  let kept = 0;
  for (const key of Object.keys(raw)) {
    const value = Number(raw[key]);
    if (Number.isFinite(value)) { out[key] = value; kept += 1; }
  }
  return kept ? out : null;
}

function _publish(sourceId) {
  _revision += 1;
  thresholdProfileSignal.set({ sourceId, revision: _revision });
}
