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
 * Coerce a typed API token to the form we would send, or "" for "no token".
 *
 * Trimmed because a token pasted out of a terminal or a password manager
 * routinely arrives with a trailing newline, and "" because blank and absent
 * have to mean the same thing everywhere — the caller that clears a token and
 * the caller that never set one must produce the identical write.
 * @param {any} raw
 * @returns {string}
 */
export function normalizeToken(raw) {
  if (typeof raw !== 'string') return '';
  return raw.trim();
}

/* A token that cannot safely become an `Authorization:` header value.

   This is not cosmetic validation. The hub sends the token verbatim as
   `Authorization: Bearer <token>` on its outbound httpx request, so a newline
   or carriage return inside it is header injection — the remainder of the line
   is parsed as further headers by whatever is on the other end. Tabs and inner
   spaces cannot appear in a real token either and are the usual sign of a
   half-selected paste, and a control character is a paste accident that would
   otherwise fail server-side with something unreadable. Reject all of them here,
   with a reason, rather than letting any of them reach the wire. */
const TOKEN_BAD_CHAR_RE = /[\s\u0000-\u001f\u007f]/;

/**
 * Why this token is unacceptable, or "" if it is fine.
 *
 * Returns a reason string rather than throwing, matching the rest of this
 * module: every rejection here is a sentence to show the user in a toast, not
 * a bug in the caller.
 * @param {any} token
 * @returns {string} "" when acceptable
 */
export function tokenRejection(token) {
  if (token === undefined || token === null) return '';
  if (typeof token !== 'string') return 'A token must be text';
  const trimmed = token.trim();
  if (!trimmed) return '';
  if (TOKEN_BAD_CHAR_RE.test(trimmed)) {
    return 'A token cannot contain spaces, tabs, newlines or control characters '
      + '— it is sent verbatim as an Authorization header';
  }
  return '';
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

/* ── Why a token never travels inside a roster entry ────────────────────────

   Both transforms below accept an optional `token`, and both hand it back on
   the result's OWN `token` field rather than on the entry they built. That is
   the structural guarantee behind this whole feature.

   `preferences.servers` is written to data/preferences.json by savePreferences(),
   which posts the WHOLE in-memory preferences object — so anything that ends up
   on a roster entry ends up on disk, in a file the user hand-edits, and in every
   later save of any unrelated preference. A remote server's token is a
   credential used SERVER-SIDE, by the hub's outbound httpx client; the browser
   only ever writes it (PATCH /v1/sources/{id}) and reads it back as the boolean
   `has_token`. It is stored by server/token_store.py, in its own file.

   Returning it on a separate top-level field is what makes "the roster is
   token-free" a property of the shape rather than of every caller remembering
   to delete a key. A caller that forgets `result.token` simply fails to send a
   credential; it cannot accidentally persist one.
*/

/**
 * Add an entry, returning the new roster or a rejection reason.
 *
 * Returns a result object rather than throwing because every rejection here is
 * a thing to say to the user in a toast, not a bug.
 * The result carries optional fields rather than being a discriminated union
 * on `ok`, because this repo compiles with `strict: false` — without
 * strictNullChecks, tsc does not narrow a union by a boolean literal
 * discriminant, so `if (!r.ok) r.reason` is an error under a union type.
 *
 * `result.token` is ALWAYS a string here ("" when none was given) — adding a
 * server is the one moment where "leave it alone" cannot mean anything, since
 * there is nothing yet to leave alone.
 * @param {Array<{id: string, name: string, url: string}>} servers
 * @param {{id: string, name?: string, url?: string, token?: string}} entry
 * @returns {{ok: boolean, reason?: string, servers?: Array<{id: string, name: string, url: string}>, entry?: {id: string, name: string, url: string}, token?: string}}
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
  // Validated BEFORE the roster is built, so a bad token cannot half-apply: the
  // server is either added with its credential or not added at all.
  const badToken = tokenRejection(entry && entry.token);
  if (badToken) return { ok: false, reason: badToken };
  const name = entry && typeof entry.name === 'string' && entry.name.trim()
    ? entry.name.trim()
    : nameFromUrl(url);
  const added = { id, name, url };
  return {
    ok: true,
    servers: [...list, added],
    entry: added,
    token: normalizeToken(entry && entry.token),
  };
}

/**
 * Rename / re-point an entry, and optionally set or clear its token.
 *
 * Three distinct meanings for `patch.token`, and they must stay distinct:
 *
 *   - ABSENT   → `result.token` is `undefined`: leave whatever the hub holds
 *                alone. This is the case for every plain rename, and getting
 *                it wrong would silently wipe a working credential every time
 *                someone fixed a typo in a name.
 *   - `""`     → `result.token` is `""`: clear the stored token. A deliberate
 *                "this server needs no credential any more".
 *   - a string → `result.token` is that string: replace it.
 *
 * `undefined` vs `""` is exactly the distinction PATCH /v1/sources/{id} makes
 * server-side (`None` means leave alone), which is why it survives to here
 * rather than being flattened into a boolean.
 * @param {Array<{id: string, name: string, url: string}>} servers
 * @param {string} id
 * @param {{name?: string, url?: string, token?: string}} patch
 * @returns {{ok: boolean, reason?: string, servers?: Array<{id: string, name: string, url: string}>, entry?: {id: string, name: string, url: string}, token?: string}}
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
  const hasToken = Object.prototype.hasOwnProperty.call(p, 'token');
  if (hasToken) {
    const badToken = tokenRejection(p.token);
    if (badToken) return { ok: false, reason: badToken };
  }
  let name = Object.prototype.hasOwnProperty.call(p, 'name')
    ? String(p.name ?? '').trim()
    : current.name;
  if (!name) name = nameFromUrl(url);
  const next = { id, name, url };
  const out = list.slice();
  out[idx] = next;
  return {
    ok: true,
    servers: out,
    entry: next,
    token: hasToken ? normalizeToken(p.token) : undefined,
  };
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
 *
 * `statusById` is optional and is exactly the `GET /v1/sources` entries keyed
 * by id — the hub is the only party that can say whether it holds a token for
 * a source or whether that source's auth let it in, since it is the hub, not
 * this window, that does the fetching. Omitting it (every pre-existing 2-arg
 * caller) leaves every row at `hasToken: false, auth: 'unknown'`, which is the
 * honest reading of "nobody has told us", not a claim that no token exists.
 * @param {Array<{id: string, name: string, url: string}>} servers
 * @param {string} selectedUrl "" for local
 * @param {Record<string, {has_token?: boolean, auth?: string}>} [statusById]
 * @returns {Array<{id: string, name: string, url: string, selected: boolean, removable: boolean, unlisted: boolean, hasToken: boolean, auth: string}>}
 */
export function serverRows(servers, selectedUrl, statusById) {
  const list = normalizeServers(servers);
  const selected = normalizeServerUrl(selectedUrl);
  const status = statusById && typeof statusById === 'object' ? statusById : {};
  /** @param {string} id */
  const credFor = (id) => {
    const s = status[id];
    return {
      hasToken: !!(s && s.has_token),
      auth: s && typeof s.auth === 'string' && s.auth ? s.auth : 'unknown',
    };
  };
  /** @type {Array<{id: string, name: string, url: string, selected: boolean, removable: boolean, unlisted: boolean, hasToken: boolean, auth: string}>} */
  const rows = [{
    id: LOCAL_ID,
    name: 'Local',
    url: '',
    selected: selected === '',
    removable: false,
    unlisted: false,
    // This process. There is no credential between a window and the server
    // serving it, and no auth hop that could fail — anything else here would
    // be a chip on the one row that can never need one.
    hasToken: false,
    auth: 'ok',
  }];
  for (const s of list) {
    rows.push({
      ...s, selected: s.url === selected, removable: true, unlisted: false, ...credFor(s.id),
    });
  }
  if (selected && !list.some((s) => s.url === selected)) {
    rows.push({
      id: 'unlisted',
      name: nameFromUrl(selected),
      url: selected,
      selected: true,
      removable: false,
      unlisted: true,
      // Not a roster entry, so the hub has no source id to report a token for.
      ...credFor('unlisted'),
    });
  }
  return rows;
}

/**
 * What a row may say about its credential.
 *
 * `auth` comes from the hub and answers the question `/v1/health` cannot: that
 * route is exempt from AuthMiddleware, so a server running `DUBIS_AUTH_MODE=on`
 * that we hold no token for answers the reachability probe happily and paints a
 * green dot — while every actual data request from the hub 401s. A green dot
 * next to an unusable server is the exact bug this chip exists to kill.
 *
 * Labels stay terse because they share the row's narrow lane with the probe
 * detail (`--server-detail-w` in css/tokens.css); a truncated explanation is
 * worse than a short one, so the sentence lives in the title instead.
 * @param {{hasToken?: boolean, auth?: string}} row
 * @returns {{state: 'none'|'ok'|'needs-token'|'bad-token', label: string, title: string}}
 */
export function credentialState(row) {
  const r = row || {};
  const auth = typeof r.auth === 'string' ? r.auth : 'unknown';
  if (auth === 'required') {
    return {
      state: 'needs-token',
      label: 'needs a token',
      title: 'This server requires a credential and none is stored for it. '
        + 'Its reachability dot cannot show this: /v1/health is exempt from '
        + 'authentication, so the server answers the probe and refuses the data. '
        + 'Use Edit to add an API token.',
    };
  }
  if (auth === 'rejected') {
    return {
      state: 'bad-token',
      label: 'token rejected',
      title: 'This server refused the stored token — it is wrong, expired, or '
        + 'was issued by a different server. Use Edit to replace it.',
    };
  }
  if (r.hasToken && auth === 'ok') {
    return {
      state: 'ok',
      label: 'token',
      title: 'A token is stored for this server and it was accepted.',
    };
  }
  // Includes the ordinary case: no token, no auth, nothing to say. Also the
  // unreachable and not-yet-probed cases, where `auth` is "unknown" — a row
  // nothing has contacted must not claim its credential is fine OR broken.
  return { state: 'none', label: '', title: '' };
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
  // host/port. Selecting it now switches the hub onto a source that cannot
  // serve an inventory, so "reachable" on its own would be the wrong thing to
  // show.
  if (!r.dubis) return { state: 'foreign', detail: 'not dubIS' };
  return { state: 'live', detail: 'reachable' };
}

/** Dot states that mean "you can connect to this". @type {ReadonlySet<string>} */
export const GOOD_STATES = new Set(['active', 'live', 'dormant']);

/**
 * The label under the roster: which server the inventory is coming from.
 *
 * There is no "pending" state any more, and the page's origin is no longer part
 * of the answer. Both used to be: selecting a server wrote a preference that
 * only `app.pyw` read, at launch, and the window was then navigated at that
 * origin — so comparing the selection to `window.location.origin` was how you
 * could tell a saved choice from an applied one, and "pending restart" was the
 * honest thing to say in between.
 *
 * The window now always stays on the local hub and other servers are sources it
 * fetches from, so a selection is applied by `PUT /v1/sources/active` before
 * this label is re-rendered, and the origin says nothing about which server the
 * data came from. A switch that FAILS never reaches here: js/server-list.js
 * re-renders from the store, which still holds the previous selection, and
 * toasts the reason.
 * @param {string} selectedUrl "" for local
 * @returns {{text: string}}
 */
export function selectionStatus(selectedUrl) {
  const selected = normalizeServerUrl(selectedUrl);
  return { text: 'active — ' + (selected || 'the local server') };
}
