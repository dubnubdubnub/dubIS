# dubIS as a systemd unit on a shared Linux box

Reference copies of what runs on `infra.uwrealitylabs.com`, where dubIS serves
`/v1` over a Unix socket so each teammate's mutations are attributed to their
own Unix account (`server/peercred.py`, `SO_PEERCRED`). Teammates reach it with
an `ssh://` server entry; see [docs/ssh-sources.md](../../docs/ssh-sources.md).

| File here | Installed as |
|---|---|
| `dubis.service` | `/etc/systemd/system/dubis.service` |
| `dubis.conf` | `/etc/tmpfiles.d/dubis.conf` |
| `dubis-tailnet-serve.env` | `/etc/dubis/tailnet-serve.env` (a `EnvironmentFile=`; see below) |

These are not applied by anything in this repo. The box is managed by hand.

## Install

```bash
sudo cp deploy/systemd/dubis.service /etc/systemd/system/dubis.service
sudo cp deploy/systemd/dubis.conf /etc/tmpfiles.d/dubis.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/dubis.conf
sudo systemctl daemon-reload
sudo systemctl enable --now dubis
ls -ld /run/dubis        # drwxr-x--- isaac uwrl-dev
```

Adjust `User=`, the group in `dubis.conf`, and the paths for another box.

## Traps

1. **The directory is the access control, not the socket.** uvicorn always
   chmods the socket `0666` (`bind_socket` in `uvicorn/config.py`), so the
   socket's own mode cannot restrict anyone. `/run/dubis` is `0750`, owned
   `isaac:uwrl-dev`, so only members of `uwrl-dev` can reach the socket.
   Identity is still per-uid: the group decides who may connect, and
   `SO_PEERCRED` decides who they are.
2. **`RuntimeDirectory=` cannot be used for this.** systemd re-chowns a
   `RuntimeDirectory=` to `User:Group` while it sets up `ExecStart`, which
   undoes any `ExecStartPre=+chgrp`. This was verified on the live box. That is
   why the directory comes from tmpfiles.d, and why the unit
   `Requires=`/`After=` `systemd-tmpfiles-setup.service`.
3. **`DUBIS_AUTH_MODE=on` is required for per-user identity.** With `off`, the
   `AuthMiddleware` is never installed and every caller is `local`, even over
   the socket. To check, send `POST /v1/auth/session` over the socket. It
   answers `{"identity":"<your username>"}` only when the mode is `on`.

## When something proxies into the socket, the peer is the proxy

`SO_PEERCRED` names the process that called `connect()`. If `tailscale serve`
proxies into `/run/dubis/dubis.sock`, that process is `tailscaled` (uid 0), so
every tailnet caller is attributed to `root` and the `Tailscale-User-Login`
branch of `server/auth.py` is never reached. `DUBIS_UDS_PROXY_UIDS` is the
explicit, default-off opt-in that lets an operator declare a uid a *hop* rather
than a person; `deploy/systemd/dubis-tailnet-serve.env` is the template and
[docs/tailnet-socket-proxy.md](../../docs/tailnet-socket-proxy.md) is the trust
boundary, the forge analysis, and the verification recipe (including the one
check that must be run on the box before trusting it).

`GET /v1/whoami` answers "what do you think I am, and how did you decide?" on
any dubIS, over any transport — the fastest way to see which identity a server
is about to stamp a mutation with. It is not auth-exempt and a hub never
proxies it, so it always describes the connection you made.

## Onboarding a teammate

1. Their account needs SSH key access to the box.
2. Add them to the socket group:
   `sudo usermod -aG uwrl-dev <user>`. This takes effect on their **next**
   login/ssh session, not in one that is already open.
3. In dubIS, Preferences → Server → Add server:
   `ssh://<user>@infra.uwrealitylabs.com/run/dubis/dubis.sock`.

To check the result from their machine, run
`ssh <user>@infra.uwrealitylabs.com 'curl -s --unix-socket /run/dubis/dubis.sock -X POST http://x/v1/auth/session'`.
It should answer with their own username.
