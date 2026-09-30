# Changelog

Bump `version` in `manifest.json` with every entry. The Chrome Web Store
rejects a repeated version.

## 0.2.0 (2026-09-29)

- **Added: DigiKey.** A pairing code starting with `DK-` sends your DigiKey
  session to dubIS. This adds the host permission `*://*.digikey.com/*`, and
  Chrome will ask you to approve it.
- **Changed: name.** The extension is now "dubIS bridge" (was "dubIS JLC
  bridge") and lives in `extension/dubis-bridge/`. The extension ID is
  unchanged.
- **Added: icons**, made from the dubIS logo.
- **Improved: waiting status.** It now shows what the last sign-in check found,
  instead of only "Waiting".
- **Fixed: DigiKey sign-in check.** It uses `/MyDigiKey`. `/MyDigiKey/Account`
  returns 404 when you are signed in, so the old check never succeeded. It also
  recognises DigiKey's real sign-out redirect to `auth.digikey.com`.

## 0.1.0 (2026-09-20)

- First version: send your JLCPCB session to dubIS with a pairing code.
