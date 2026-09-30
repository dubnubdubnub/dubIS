# dubIS bridge: privacy

_Last updated 2026-09-29._

dubIS bridge moves one thing, your JLCPCB or DigiKey sign-in session, from your
browser to the dubIS inventory server **you** run. It does that only when you
click **Send**.

## What it reads

- **Session cookies for jlcpcb.com or digikey.com, and only those two sites.**
  For JLCPCB it reads the single `JLCPCB_SESSION_ID` cookie. For DigiKey it
  reads the cookies set for `digikey.com`. Chrome enforces the two-site limit
  through the extension's host permissions.
- **Whether you're signed in.** It asks one fixed page on the site you chose.
  It uses the answer only to decide when to send.

It does not read your browsing history, other sites' cookies, page content,
passwords, or form data.

## Where it goes

To exactly one place: the dubIS address you entered on the Options page. That
is normally your own computer, `http://127.0.0.1:<port>`. The extension has no
server of its own.

Nothing is sent to the extension's developer, to analytics, or to any third
party.

## What it keeps

- **Kept:** the dubIS address you entered, and the text of the last status
  message, in the browser's extension storage.
- **Never kept:** cookie values. They exist in memory only for the single send
  and are never written to storage, logged, or displayed.

On the dubIS side, the session is stored in a file only your user account can
read (`0600`). No dubIS route ever returns it.

## What it will not do

- Act on its own. Every send starts with your click and a one-time pairing code
  from your dubIS.
- Accept requests from websites. No page can talk to it.
- Sign in for you, or handle your password.

## Removing it

Remove the extension from your browser's extensions page. To remove a session
already sent to dubIS, use **Revoke** (JLCPCB) or **Logout** (DigiKey) in dubIS
Preferences.

## Contact

Open an issue on the dubIS repository.
