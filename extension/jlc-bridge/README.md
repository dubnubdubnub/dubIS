# dubIS JLC bridge (MV3 extension)

Hands your **JLCPCB session** to a local dubIS server, once, when you click a
button. That is the entire feature.

It exists because JLC's `JLCPCB_SESSION_ID` cookie is `HttpOnly; Secure;
SameSite=None`, so a dubIS page at `http://127.0.0.1:<port>` can never read it,
and because every alternative (CDP, a dedicated Chromium profile) breaks
Chrome's saved-password autofill — see
`docs/plans/2026-09-20-extension-credential-capture.md` for the full reasoning
and the threat model these files implement.

## Load it (dev mode)

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode**.
3. **Load unpacked** → pick this folder (`extension/jlc-bridge`).
4. Open the extension's **Options** and set the dubIS base URL if it is not the
   default `http://127.0.0.1:7897`.

There is no Chrome Web Store listing and no auto-update yet; re-load the folder
after pulling changes.

### The extension ID is pinned

`manifest.json` carries a `key` — the base64 public half of an RSA keypair — so
this extension always loads as

```
fboadceadnhfhdkdmfjlhbicocbhbbpc
```

whether it is loaded unpacked from any folder or installed from an unlisted
Chrome Web Store entry later. Without it, an unpacked load derives the ID from
the folder path and a Store install derives it from the signing key, so the ID
would change on the move and anything pinning it would break.
`tests/python/test_extension_manifest.py` fails if the field goes missing.

**The matching private key is not in this repo and must never be.** It is the
Web Store upload key: whoever holds it can publish an update that every
installed copy auto-accepts. It currently lives outside the repo at

```
<scratchpad>/jlc-bridge-key/jlc-bridge.pem      (mode 0600)
```

— ask Isaac for the current location; the scratchpad is session-scoped, so it
needs moving into a password manager or a secrets store before the first Store
upload. `.gitignore` refuses `*.pem` and `extension/**/*.crx` so a copy dropped
next to the extension cannot be committed by accident. If the key is ever lost,
the ID cannot be recovered: a new key means a new ID and a fresh install for
everyone.

## Using it

1. In dubIS, start a JLC sign-in. dubIS shows a short-lived **pairing code**.
2. Sign in at [jlcpcb.com](https://jlcpcb.com/) in this browser — your normal
   profile, so saved passwords, autofill and Google/Apple SSO all work as usual.
   The extension never types anything for you.
3. Click the extension's toolbar icon, paste the pairing code, and press
   **Send session to dubIS**. *That click is the authorisation for everything
   that follows.*
4. The extension polls JLC's own library API (about 40 checks, 3s apart, then it
   gives up with a message) until JLC answers with something other than
   `code: 460`, i.e. genuinely signed in. Then it POSTs the session to dubIS and
   says whether dubIS accepted it.

**Why the poll watches the API and not the cookie:** an anonymous request to
that endpoint *already mints* a fresh `JLCPCB_SESSION_ID`. Waiting for the
cookie to appear therefore succeeds immediately and captures an anonymous
session, every time. The API response code is the only honest signal.

## Permissions, and why each one is here

| Permission | Why |
|---|---|
| `cookies` | The one capability page JS lacks: reading the `HttpOnly` session cookie. Chrome scopes it to the hosts below, so it cannot read cookies for any other site. |
| `storage` | `chrome.storage.local` keeps the dubIS base URL; `chrome.storage.session` keeps the in-progress status line. Neither ever holds a cookie value. |
| `*://*.jlcpcb.com/*` (host) | The only site whose cookies may be read, and the only site the validation request goes to. |

Deliberately **not** requested: `tabs`, `scripting`, `webRequest`, `downloads`,
`<all_urls>`, and no `externally_connectable` or `content_scripts` keys.
`tests/python/test_extension_manifest.py` asserts that permission set exactly,
so widening it later fails CI rather than passing review unnoticed.

## What it can do

- Read **only** the cookies named in `SESSION_COOKIE_NAMES` (`JLCPCB_SESSION_ID`)
  for `jlcpcb.com`.
- Call one fixed JLC URL to ask "am I signed in?".
- POST `{nonce, account, cookies}` to `POST /v1/distributors/jlcpcb/session` on
  the dubIS base URL you configured — and only after you clicked the button and
  supplied a pairing code from that dubIS.

## What it cannot do

- **Be reached by a web page.** There is no `externally_connectable`, no content
  script, and no message any page can send. The only senders are this
  extension's own popup and options page.
- **Act on its own.** Nothing happens without your click plus a pairing code.
  The code pins *which* dubIS receives the session, so a "dubIS" someone else
  points you at cannot silently collect one.
- **Fetch arbitrary URLs.** There is no "fetch this URL" message. The two
  outbound URLs are both compile-time constants (plus your configured dubIS
  origin).
- **Touch any other site.** With `host_permissions` limited to `jlcpcb.com`,
  Chrome itself refuses cookie reads elsewhere.
- **Read anything back out of dubIS.** The flow is push-only; no dubIS route
  returns a stored credential.
- **Store or log a cookie value.** Values exist only in memory for the single
  POST that consumes them.
- **Log in for you.** No scripted login, ever — Chrome's password autofill
  cannot be driven programmatically, and SSO has no credential to hold.

## Files

| File | Role |
|---|---|
| `manifest.json` | MV3 manifest; the permission set is the security boundary, and `key` pins the extension ID. |
| `background.js` | Service worker: the poll, the cookie filter, the single POST. |
| `config.js` | dubIS base URL storage + validation, and the session route path. |
| `popup.html` / `popup.js` | Status, pairing-code field, the button that starts a send. |
| `options.html` / `options.js` | The dubIS base URL. |

## Linting

`eslint.config.mjs` has an `extension/**/*.js` block carrying the same rules as
`js/`, plus the WebExtension globals:

```bash
npx eslint extension/
```

(Before that block existed the command matched no configuration and silently
linted these files with zero rules.)

## Known limitation

An MV3 service worker can be evicted when idle. This extension has no `alarms`
permission (it would be a wider surface than the feature needs), so the poll
keeps itself alive by touching an extension API each tick. If Chrome tears the
worker down anyway mid-poll, the run ends without a push — reopen the popup and
press the button again.
