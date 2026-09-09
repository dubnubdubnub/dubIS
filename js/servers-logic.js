// @ts-check
/* servers-logic.js — pure logic for the server roster: validation of the
   persisted list, the add/edit/remove transforms, and the decision of what a
   given row's reachability dot is allowed to claim.

   No DOM, no fetch, no store — js/server-list.js owns those. Everything here
   is a pure function so the interesting half of this feature (which URLs are
   even probeable, and what a probe's silence means) is unit-testable.

   Persistence shape, in data/preferences.json:

     "servers":    [{ id, name, url }, ...]   the roster the user maintains
     "server_url": "https://..."              the SELECTED one, "" for local

   `server_url` deliberately stays the single source of truth for *which*
   server is in use, because it already is one: remote_mode.resolve_remote_base_url
   (Python, read at launch), app_restart.relaunch_env and tools/dubis-cli all
   read it. The roster is purely additive — a list of URLs the user might
   select — so none of those three had to learn about it. Selecting a server
   means writing its URL into `server_url`; the roster never becomes a second,
   disagreeing answer to "where am I connected".
*/

/** The id of the implicit, undeletable local entry. Never persisted. */
export const LOCAL_ID = 'local';

/**
 * Coerce a stored/typed server URL to a canonical form, or "" if unusable.
 *
 * A URL without an http(s) scheme would be resolved relative to the app's own
 * origin by the webview, silently pointing at the local server instead of the
 * remote one — a wrong answer that looks like a working one. Trailing slashes
 * are stripped so `https://x` and `https://x/` are one entry, not two.
 * @param {any} raw
 * @returns {string}
 */
export function normalizeServerUrl(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return '';
  if (!/^https?:\/\//i.test(text)) return '';
  return text.replace(/\/+$/, '');
}

/**
 * A default display name for a URL, used when the user leaves the name blank.
 * @param {string} url
 * @returns {string} the host (with port), or "" if the URL is unusable
 */
export function nameFromUrl(url) {
  const normalized = normalizeServerUrl(url);
  if (!normalized) return '';
  // Deliberately string surgery rather than `new URL()`: this module is
  // imported by tests that run outside a DOM, and the scheme is already known
  // to be http(s) by the time we get here.
  return normalized.replace(/^https?:\/\//i, '').split('/')[0];
}

/**
 * Validate a persisted roster into a clean array, dropping what it cannot
 * repair and warning about each drop.
 *
 * Same contract as the `saved_views` loader in store.js: a malformed entry is
 * dropped rather than crashing the load, because preferences.json is a file a
 * user may reasonably hand-edit.
 * @param {any} raw
 * @param {(msg: string) => void} [warn]
 * @returns {Array<{id: string, name: string, url: string}>}
 */
export function normalizeServers(raw, warn) {
  const log = typeof warn === 'function' ? warn : function () {};
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw)) {
    log('servers is not an array — ignoring');
    return [];
  }
  /** @type {Array<{id: string, name: string, url: string}>} */
  const out = [];
  const seenIds = new Set();
  const seenUrls = new Set();
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') {
      log('ignoring non-object servers entry');
      continue;
    }
    const id = typeof entry.id === 'string' ? entry.id.trim() : '';
    if (!id) {
      log('ignoring servers entry with missing/invalid id');
      continue;
    }
    if (id === LOCAL_ID) {
      // "local" names the implicit entry this module synthesizes. A persisted
      // one would render twice and be selectable in two contradictory ways.
      log('ignoring servers entry using the reserved id "' + LOCAL_ID + '"');
      continue;
    }
    if (seenIds.has(id)) {
      log('ignoring servers entry with duplicate id "' + id + '"');
      continue;
    }
    const url = normalizeServerUrl(entry.url);
    if (!url) {
      log('ignoring servers entry "' + id + '" with a URL that is not http(s)');
      continue;
    }
    if (seenUrls.has(url)) {
      log('ignoring servers entry "' + id + '" duplicating URL ' + url);
      continue;
    }
    const name = typeof entry.name === 'string' && entry.name.trim()
      ? entry.name.trim()
      : nameFromUrl(url);
    seenIds.add(id);
    seenUrls.add(url);
    out.push({ id, name, url });
  }
  return out;
}

/**
 * Add an entry, returning the new roster or a rejection reason.
 *
 * Returns a result object rather than throwing because every rejection here is
 * a thing to say to the user in a toast, not a bug.
 * The result carries optional fields rather than being a discriminated union
 * on `ok`, because this repo compiles with `strict: false` — without
 * strictNullChecks, tsc does not narrow a union by a boolean literal
 * discriminant, so `if (!r.ok) r.reason` is an error under a union type.
 * @param {Array<{id: string, name: string, url: string}>} servers
 * @param {{id: string, name?: string, url: string}} entry
 * @returns {{ok: boolean, reason?: string, servers?: Array<{id: string, name: string, url: string}>, entry?: {id: string, name: string, url: string}}}
 */
export function addServerEntry(servers, entry) {
  const list = Array.isArray(servers) ? servers : [];
  const url = normalizeServerUrl(entry && entry.url);
  if (!url) return { ok: false, reason: 'Server URL must start with http:// or https://' };
  const existing = list.find((s) => s.url === url);
  if (existing) return { ok: false, reason: '“' + existing.name + '” already points at that URL' };
  const id = String((entry && entry.id) || '').trim();
  if (!id) return { ok: false, reason: 'internal: server entry needs an id' };
  if (id === LOCAL_ID || list.some((s) => s.id === id)) {
    return { ok: false, reason: 'internal: duplicate server id' };
  }
  const name = entry && typeof entry.name === 'string' && entry.name.trim()
    ? entry.name.trim()
    : nameFromUrl(url);
  const added = { id, name, url };
  return { ok: true, servers: [...list, added], entry: added };
}

/**
 * Rename / re-point an entry.
 * @param {Array<{id: string, name: string, url: string}>} servers
 * @param {string} id
 * @param {{name?: string, url?: string}} patch
 * @returns {{ok: boolean, reason?: string, servers?: Array<{id: string, name: string, url: string}>, entry?: {id: string, name: string, url: string}}}
 */
export function updateServerEntry(servers, id, patch) {
  const list = Array.isArray(servers) ? servers : [];
  const idx = list.findIndex((s) => s.id === id);
  if (idx === -1) return { ok: false, reason: 'That server is no longer in the list' };
  const current = list[idx];
  const p = patch || {};
  const url = Object.prototype.hasOwnProperty.call(p, 'url')
    ? normalizeServerUrl(p.url)
    : current.url;
  if (!url) return { ok: false, reason: 'Server URL must start with http:// or https://' };
  const clash = list.find((s) => s.url === url && s.id !== id);
  if (clash) return { ok: false, reason: '“' + clash.name + '” already points at that URL' };
  let name = Object.prototype.hasOwnProperty.call(p, 'name')
    ? String(p.name ?? '').trim()
    : current.name;
  if (!name) name = nameFromUrl(url);
  const next = { id, name, url };
  const out = list.slice();
  out[idx] = next;
  return { ok: true, servers: out, entry: next };
}

/**
 * Remove an entry.
 * @param {Array<{id: string, name: string, url: string}>} servers
 * @param {string} id
 * @returns {Array<{id: string, name: string, url: string}>}
 */
export function removeServerEntry(servers, id) {
  const list = Array.isArray(servers) ? servers : [];
  return list.filter((s) => s.id !== id);
}

/**
 * The rows to render: the implicit local entry, then the roster, then — if the
 * selected URL is not in the roster — the selected URL itself.
 *
 * That last case is not hypothetical. `DUBIS_URL` outranks the preference, and
 * a hand-edited preferences.json can carry a `server_url` nobody added to the
 * list. Synthesizing a row for it means the selected server is always visible
 * and always the one marked selected, instead of the list quietly showing
 * "Local" as the choice while the app talks to something else.
 * @param {Array<{id: string, name: string, url: string}>} servers
 * @param {string} selectedUrl "" for local
 * @returns {Array<{id: string, name: string, url: string, selected: boolean, removable: boolean, unlisted: boolean}>}
 */
export function serverRows(servers, selectedUrl) {
  const list = normalizeServers(servers);
  const selected = normalizeServerUrl(selectedUrl);
  /** @type {Array<{id: string, name: string, url: string, selected: boolean, removable: boolean, unlisted: boolean}>} */
  const rows = [{
    id: LOCAL_ID,
    name: 'Local',
    url: '',
    selected: selected === '',
    removable: false,
    unlisted: false,
  }];
  for (const s of list) {
    rows.push({ ...s, selected: s.url === selected, removable: true, unlisted: false });
  }
  if (selected && !list.some((s) => s.url === selected)) {
    rows.push({
      id: 'unlisted',
      name: nameFromUrl(selected),
      url: selected,
      selected: true,
      removable: false,
      unlisted: true,
    });
  }
  return rows;
}

/**
 * What a row's dot is allowed to claim, before any probe runs.
 *
 * The dot means "reachable from where this window is right now" — that is the
 * question a user picking a server is actually asking — so it has to be probed
 * from the page, not from whichever server happens to be serving the page. Two
 * cases can't be probed from the page at all, and both must read as unknown
 * rather than as a red dot that says "down" about a server that is fine:
 *
 *  - `dormant`: the Local row while this window is talking to a remote server.
 *    There is no local server running to answer — selecting Local *starts*
 *    one at launch. A red dot here would be a lie about a choice that works.
 *  - `blocked`: an http:// target while the page itself is https. The browser
 *    blocks that request as mixed content before it reaches the network, so a
 *    failure says nothing about the server.
 *
 * @param {{url: string}} row
 * @param {string} pageOrigin `window.location.origin`
 * @returns {{state: 'active'|'dormant'|'blocked'|'probe', url?: string, detail?: string}}
 */
export function probePlan(row, pageOrigin) {
  const origin = normalizeServerUrl(pageOrigin);
  const target = normalizeServerUrl(row && row.url);
  const originIsLoopback = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|$)/i.test(origin);

  if (!target) {
    // The Local row. The page's own origin being loopback is proof a local
    // server is up and answering — we are talking to it.
    if (originIsLoopback) return { state: 'active', url: origin };
    return { state: 'dormant', detail: 'starts with the app' };
  }
  if (target === origin) return { state: 'active', url: target };
  if (/^https:/i.test(origin) && /^http:/i.test(target)) {
    return { state: 'blocked', detail: 'cannot be checked from an https page' };
  }
  return { state: 'probe', url: target };
}

/**
 * Turn a probe outcome into a dot state.
 *
 * The detail is deliberately terse — it shares a narrow lane with the row's
 * buttons, and a truncated explanation is worse than a short one. In
 * particular a failed cross-origin fetch reports only "Failed to fetch"
 * whatever went wrong (DNS, refused connection, blocked by CORS), so passing
 * it through would spend the whole lane saying nothing. A timeout is the one
 * failure worth distinguishing: it means something accepted the connection.
 * @param {{ok: boolean, status?: number, dubis?: boolean, error?: string}} result
 * @returns {{state: 'live'|'down'|'foreign', detail: string}}
 */
export function classifyProbe(result) {
  const r = result || { ok: false };
  if (!r.ok) {
    if (r.status) return { state: 'down', detail: 'HTTP ' + r.status };
    if (r.error === 'timed out') return { state: 'down', detail: 'timed out' };
    return { state: 'down', detail: 'unreachable' };
  }
  // Answered, but not with dubIS's health payload — something else is on that
  // host/port. Selecting it would restart into a broken app, so "reachable" on
  // its own would be the wrong thing to show.
  if (!r.dubis) return { state: 'foreign', detail: 'not dubIS' };
  return { state: 'live', detail: 'reachable' };
}

/** Dot states that mean "you can connect to this". @type {ReadonlySet<string>} */
export const GOOD_STATES = new Set(['active', 'live', 'dormant']);

/**
 * The label under the roster: is the selection live, or waiting on a restart?
 * @param {string} selectedUrl "" for local
 * @param {string} pageOrigin
 * @returns {{pending: boolean, text: string}}
 */
export function selectionStatus(selectedUrl, pageOrigin) {
  const selected = normalizeServerUrl(selectedUrl);
  const origin = normalizeServerUrl(pageOrigin);
  const originIsLoopback = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:|$)/i.test(origin);
  const pending = selected ? selected !== origin : !originIsLoopback;
  return {
    pending,
    text: pending
      ? 'pending restart — now using ' + (origin || 'an unknown origin')
      : 'active — ' + (origin || 'the local server'),
  };
}
