# dubIS bridge

A small Chrome (and Brave, Edge) extension that hands your **JLCPCB** or
**DigiKey** session to your own dubIS, once, when you click a button.

You sign in on the distributor's site in your normal browser, so saved
passwords, autofill and SSO work as usual. dubIS never sees your password, and
nothing here ever types one.

| | |
|---|---|
| **Version** | 0.3.0 (see [CHANGELOG.md](CHANGELOG.md)) |
| **Extension ID** | `mnfcgeaogiopofamomogjhljgongdami` (pinned, see [The ID and the signing key](#the-id-and-the-signing-key)) |
| **Sites** | `jlcpcb.com`, `digikey.com` |
| **Talks to** | your own dubIS on this machine (the port comes from the pairing code), and nothing else |
| **Privacy** | [PRIVACY.md](PRIVACY.md) |
| **Design and threat model** | [docs/plans/2026-09-20-extension-credential-capture.md](../../docs/plans/2026-09-20-extension-credential-capture.md) |

## Install

1. Open `brave://extensions` (Chrome: `chrome://extensions`, Edge:
   `edge://extensions`) and turn on **Developer mode**.
2. Click **Load unpacked** and choose this folder, `extension/dubis-bridge`
   (the one containing `manifest.json`).
3. After pulling changes, click the reload arrow on the extension's card.

There is nothing to configure. Each pairing code ends in `.<port>`, the port
your dubIS is listening on, and the extension sends to `127.0.0.1` on that
port. The Options page's address is used only for a code without a port, which
comes from an older dubIS or one served over a Unix socket.

There is no Chrome Web Store listing yet. See [Publishing](#publishing).

### Check it loaded correctly

| What you see | What it means |
|---|---|
| The card shows ID `mnfcgeaogiopofamomogjhljgongdami` and no **Errors** button | Loaded correctly |
| An **Errors** button, or no card at all | You picked the wrong folder: choose `dubis-bridge` itself |
| A different ID | `manifest.json` lost its `key`, and dubIS will refuse every push |
| Pasting a `DK-` code shows no "DigiKey code" hint | An old copy is loaded: reload or re-add it |
| "Failed to fetch" / "Could not reach dubIS" | dubIS isn't running, or the code came from a dubIS that has since restarted: get a new code |
| "Sends to" shows an address you don't expect | The code has no `.<port>` ending, so the Options address is used |

## Use it

1. In dubIS, open **Preferences** and click **Sign in** under JLCPCB or
   DigiKey. dubIS shows a pairing code, valid for 10 minutes, ending in its
   port, for example `DK-3kf9…Zq.55200`.
2. Sign in on that site in this browser, if you aren't already.
3. Click the extension icon, paste the code, and press **Send session to
   dubIS**. That click is the only thing that ever starts a send.
4. The extension waits until the site says you're signed in (up to about two
   minutes, checking every 3 seconds), then pushes the session and shows
   dubIS's answer. While it waits, the status line shows what the last check
   found.

**The code decides the site and the port.** DigiKey codes start with `DK-`;
anything else is a JLCPCB code. The `.<port>` ending picks the local dubIS.
The code can supply a port but never a host, so it cannot send your session
off this machine. A remote address can only come from Options, which you
typed yourself.

### How each site is checked

| | JLCPCB | DigiKey |
|---|---|---|
| **Signed-in check** | JLC's library API answers with anything but `code: 460` | `https://www.digikey.com/MyDigiKey` loads with HTTP 200 |
| **Signed out looks like** | `code: 460` | a redirect to `auth.digikey.com`, or HTTP 401 |
| **Cookies sent** | `JLCPCB_SESSION_ID` only | every `digikey.com` cookie ([why](#why-every-digikey-cookie)) |
| **Pushed to** | `POST /v1/distributors/jlcpcb/session` | `POST /v1/distributors/digikey/push` |

Neither check trusts a cookie's mere presence. An anonymous JLC request mints a
session cookie by itself. And DigiKey's `/MyDigiKey/Account`, the obvious page
to probe, now returns 404 when you *are* signed in.

A Cloudflare "Just a moment" page, a 403, a 5xx or a network error counts as
"can't tell yet", never as signed in.

#### Why every DigiKey cookie

JLC's session is one known cookie, so the JLC path sends exactly that one.
Which DigiKey cookies make up a session hasn't been pinned down yet. The likely
ones are `PA.MyDigiKey`, `PF`, `PF.PERSISTENT`, `dkuhint`, `sid` and
`dk_sft_ses`, out of about 57 on a signed-in browser. Guessing short would push
a session that fails silently. So the DigiKey path sends the `digikey.com` jar:
still one site, still enforced by Chrome through the host permission. This is a
recorded exception to the design's rule 6, meant to be narrowed to an
allowlist.

## Permissions

| Permission | Why it's needed |
|---|---|
| `cookies` | Reading `HttpOnly` session cookies, which page scripts cannot. Chrome limits it to the two sites below. |
| `storage` | Remembers the dubIS address (`storage.local`) and the current status line (`storage.session`). Never a cookie value. |
| `*://*.jlcpcb.com/*` | Whose cookies may be read, and where the JLC sign-in check goes. |
| `*://*.digikey.com/*` | The same, for DigiKey. |

Deliberately **not** requested: `tabs`, `scripting`, `webRequest`, `alarms`,
`<all_urls>`, content scripts, `externally_connectable`, or any optional
permission. `tests/python/test_extension_manifest.py` pins this set exactly,
so widening it fails CI.

## Security model

- **Nothing on the web can reach it.** It has no content scripts and no
  `externally_connectable`. The only senders are its own popup and options
  pages.
- **It acts only on your click plus a code from your dubIS.** The single-use
  code proves a person started the send. dubIS accepts it only from loopback,
  so a hub never forwards your session to another machine.
- **A code can only point at this machine.** It carries a port, never a host,
  and the extension sends to `127.0.0.1` on it. A local process that could run
  a fake dubIS could already read the browser's cookie store, so that gives it
  nothing new.
- **Every outbound URL is fixed.** It calls one sign-in check per site and one
  dubIS route per site. There is no "fetch this URL" message.
- **It is push-only.** No dubIS route ever returns a stored credential.
- **Cookie values live only in memory.** They exist for the single POST that
  sends them: never stored, logged, or shown.
- **dubIS lets exactly this extension in.** Its CORS rules answer
  `chrome-extension://mnfcgeaogiopofamomogjhljgongdami` and nothing else. That
  is why the ID is pinned.

## Files

| File | Role |
|---|---|
| `manifest.json` | MV3 manifest. The permission set is the security boundary; `key` pins the ID. |
| `background.js` | Service worker: routes a code to the JLC or DigiKey handshake, polls, reads cookies, sends once. |
| `handshake-logic.js` | Pure decisions with no `chrome` APIs: code routing, the code's port and destination, probe classification, cookie shape, error text. |
| `config.js` | The Options address (validated), the code-to-destination lookup, and the two intake paths. |
| `popup.html`, `popup.js` | Status line, code box, Send and Cancel. |
| `options.html`, `options.js` | The fallback dubIS address, for codes without a port. |
| `icons/` | Toolbar and store icons, made from `data/dubIS.png`. |

Tests live with the rest of dubIS's tests:

| Test | What it covers |
|---|---|
| `tests/js/extension-handshake-logic.test.js` | Routing, probe classification, cookie shape |
| `tests/python/test_extension_manifest.py` | Exact permissions, the pinned `key`, and that the ID matches the server's allowlist |
| `tests/python/test_extension_package.py` | The store zip's contents |
| `tests/python/server/test_health_cors.py` | That the CORS exception covers exactly these intake routes |

Lint with `npx eslint extension/`.

## Publishing

### Build the upload zip

```bash
python scripts/package-extension.py --check   # validate only
python scripts/package-extension.py           # dist/dubis-bridge-<version>.zip
```

The zip holds only what the browser runs: no docs, no keys. The build refuses
to write it if any file the manifest names, or any relative `import`, is
missing. That kind of break works unpacked and fails from the Store.

Bump `version` in `manifest.json` and add a [CHANGELOG.md](CHANGELOG.md) entry
for every upload. The Store rejects a repeated version.

### The ID and the signing key

The ID comes from the `key` in `manifest.json`, which is the public half of a
signing keypair. It must stay stable, because dubIS's allowlist names it. The
Chrome Web Store refuses a `key` field on an extension's **first** upload, so
that one upload is different:

- **Keep the current ID** (the plan):
  ```bash
  python scripts/package-extension.py --first-upload --key-from-keychain
  ```
  This reads the private key from the macOS login Keychain and adds it to the
  zip as `key.pem`. That zip is a secret: upload it, then delete it. On another
  machine, use `--key-pem /path/to/key.pem` with an exported copy.
- **Let the Store assign an ID:** build with `--first-upload` alone and upload.
  Then copy the public key from the Developer Dashboard's Package tab into
  `manifest.json`, and set `BRIDGE_EXTENSION_ID` in
  `server/routes/distributors.py` to the new ID.
  `tests/python/test_extension_manifest.py` fails until the two agree.
  Everyone re-adds the extension once.

After the first upload, every build keeps `key` and uploads normally.

**Where the private key lives.** It is in the macOS login Keychain, as a
generic password with service `dubis-bridge-signing-key`. The value is the
base64 of the PKCS#8 PEM. The key was generated on 2026-09-29, after the
original key was lost, and the ID changed with it. To back it up, or move it to
a password manager:

```bash
security find-generic-password -s dubis-bridge-signing-key -w | base64 -d > dubis-bridge.pem
```

Delete that file once it's stored. The key never enters this repo:
`.gitignore` refuses `*.pem`, `*.crx` and `dist/`. Whoever holds it can
publish an update that every installed copy accepts automatically. Losing it
before the first Store upload costs a new ID; losing it after costs every user
a reinstall.

### Store listing checklist

- **Visibility:** Unlisted, per the design. Auto-update matters for an
  extension that handles credentials, and a public listing isn't needed.
- **Single purpose:** "Sends your JLCPCB or DigiKey session to your own dubIS
  inventory server when you click Send."
- **Permission justifications:** copy them from [Permissions](#permissions).
- **Privacy:** declare that it handles authentication information. Link a
  hosted copy of [PRIVACY.md](PRIVACY.md), and declare that no data goes to
  the developer or any third party.
- **Icons:** `icons/icon-128.png`. Screenshots of the popup are still to be
  made.

## Known limitations

- **A long wait can be cut short.** Chrome may stop an idle MV3 service worker.
  The extension keeps itself awake while polling, without the broader `alarms`
  permission. If Chrome stops it anyway, press Send again.
