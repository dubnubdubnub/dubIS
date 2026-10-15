# A `tailscale serve` proxy in front of the Unix socket

**Nothing in this document has been applied to any machine.** It is the
artifact, not the change: the code and tests are in this repo, and running the
steps below on `600g4` (aspen) is the operator's call.

## The problem, in one paragraph

On a shared Linux box dubIS serves `/v1` on a Unix socket and identity comes
from the kernel: `SO_PEERCRED` reports the uid of the process that called
`connect()`, so a teammate who reaches the socket through `ssh -L` is
attributed to their own Unix account (`server/peercred.py`,
`deploy/systemd/README.md`). That only names the *person* when the person's own
process is the peer. When `tailscale serve` proxies into the socket, the peer is
`tailscaled`, running as **root** — so every tailnet caller is `root`, the
`Tailscale-User-Login` branch of `server/auth.py::_resolve` is never reached,
and every ledger row is stamped `something@root`.

Observed on the same request to two servers:

```
POST https://600g4.miku-parore.ts.net/v1/auth/session    -> {"identity":"root"}
POST https://fremont.miku-parore.ts.net/v1/auth/session  -> {"identity":"isaacchiu2006@gmail.com"}
```

fremont is TCP behind a trusted proxy, so it reaches the header branch. aspen is
a socket behind a proxy, so the kernel answers first — and the kernel is
answering about the wrong party.

## The fix, in one line

`DUBIS_UDS_PROXY_UIDS` names the uid that is a **hop** rather than a person.
When the socket peer's uid is on that list, tailnet header trust is on, and the
request carries an allowlisted `Tailscale-User-Login`, the identity is that
login. Everything else falls back to today's behavior exactly.

## What to set on `600g4`

Template: [`deploy/systemd/dubis-tailnet-serve.env`](../deploy/systemd/dubis-tailnet-serve.env).
Four variables, all of them required:

| Variable | Value on aspen | Why |
|---|---|---|
| `DUBIS_AUTH_MODE` | `on` | with `off` the middleware is never installed and everyone is `local` |
| `DUBIS_TRUST_TAILSCALE_HEADER` | `1` | the master switch for believing *any* `Tailscale-User-Login`; the socket hop is gated on it too |
| `DUBIS_TAILNET_ALLOWLIST` | the tailnet logins that may be admitted | bounds *who* may ever be named through the hop |
| `DUBIS_UDS_PROXY_UIDS` | `0` | `tailscaled` runs as root. Unset = the feature does not exist. |

`DUBIS_TRUSTED_PROXY_IPS` / `DUBIS_TRUSTED_PROXY_HOSTS` are **not** part of this
path. They gate the TCP header branch, where the peer's *address* is the only
evidence available. On a socket the kernel's uid is stronger evidence, so it is
the gate: a Unix-socket peer either is the named proxy uid or it is not, and
there is no address to spoof.

## The trust boundary, stated exactly

`AuthMiddleware._resolve_uds_peer` returns the peer's own identity unless **all
five** hold:

1. `DUBIS_UDS_PROXY_UIDS` is non-empty — an operator named a uid. Nothing
   anywhere infers "uid 0 looks like a proxy".
2. the kernel actually reported a uid for this connection. On a platform without
   `SO_PEERCRED`, or where the credential read failed, there is no uid, so the
   branch is *unreachable* rather than merely unused.
3. that uid is one the operator named.
4. `DUBIS_TRUST_TAILSCALE_HEADER=1`.
5. `Tailscale-User-Login` is present **and** in `DUBIS_TAILNET_ALLOWLIST`.

Fail any one and the answer is the peer's own identity — byte for byte today's
behavior. In particular a header can never *upgrade* a peer the kernel already
named: it is dropped, and the attempt is logged.

## Who can forge this

**To present the header at all, a caller must be able to connect to the
socket.** That is decided by the socket *directory*, not by anything here:
uvicorn chmods the socket itself `0666`, so `/run/dubis` is `0750 isaac:uwrl-dev`
([`deploy/systemd/dubis.conf`](../deploy/systemd/dubis.conf)) and only the owner
or `uwrl-dev` can traverse to it. The population that could try is therefore:
members of that group, root, and whoever tailscaled forwards.

* **A non-root local user in `uwrl-dev` cannot take the branch at all.** The
  kernel reports *their* uid, which is not the named proxy uid, so gate 3 fails
  and they resolve to their own username. Attaching
  `Tailscale-User-Login: someone-else@example.com` changes nothing and produces
  a rate-limited warning naming them. This is the property the design must not
  lose, and `test_a_peer_with_its_own_uid_cannot_be_upgraded_by_a_header` pins
  it.
* **A network caller cannot reach the branch either.** It sits behind
  `peercred.identity_from_scope`, which is populated only by `server/uds.py`,
  only for an `AF_UNIX` transport. A TCP connection never gets a peer identity
  or a peer uid in its scope, whatever it sends — pinned end to end by
  `test_a_tcp_caller_cannot_reach_the_hop_end_to_end`.
* **What is left is a process already running as the proxy uid** — on aspen,
  root. That is not an escalation: root can already `setuid()` to any account
  and connect (so it can already *be* any identity this server has), read the
  data dir, and read every token on the box. Granting root the ability to assert
  an allowlisted login buys nothing it did not already have.

### The one assumption the server cannot check

The kernel proves **who is connected**. It cannot prove **who wrote the
header**: a proxy that forwards client headers verbatim would carry a
client-typed `Tailscale-User-Login` through with the proxy's own uid behind it,
which is exactly the credential the branch accepts.

So the opt-in asserts one thing about the fronting proxy: that it *injects*
`Tailscale-User-Login` from an authenticated tailnet session and owns the
header. For `tailscale serve` that means the handler requires tailnet auth
(`tailscale serve --auth`), so tailscaled refuses an unauthenticated request
before it reaches the socket. The allowlist is not the defense — a login is not
a secret. It bounds the blast radius and nothing more.

**Do not enable the hop until step 4 below passes on your box.** If it fails,
the correct move is to fix the proxy's auth configuration, or leave
`DUBIS_UDS_PROXY_UIDS` unset and accept `root` as the identity.

## Verify

Run these in order. Each one says something the previous one cannot.

**1. What the socket itself says (no proxy in the way).** On the box:

```bash
curl -s --unix-socket /run/dubis/dubis.sock http://x/v1/whoami
```

Expect `{"identity":"<the account that ran curl>", "mechanism":"uds_peer_uid",
"socket_uid":<its uid>, ...}`. Run it as root and you get
`{"identity":"root","socket_uid":0}` — the shape every tailnet caller is
currently collapsed into. This is the baseline the hop exists to escape.

**2. What the tailnet says, before the change.** From a tailnet client:

```bash
curl -s https://600g4.miku-parore.ts.net/v1/whoami
curl -s -X POST https://600g4.miku-parore.ts.net/v1/auth/session
```

Before the change both say `root` (`mechanism: uds_peer_uid`). Note that
`/v1/whoami` is answered by whichever server receives it and is never proxied by
a hub — point `dubis` at the box directly with
`DUBIS_URL=https://600g4.miku-parore.ts.net scripts/dubis auth whoami`.

**3. Enable, then re-run step 2.** After setting the four variables and
restarting the unit:

```bash
curl -s https://600g4.miku-parore.ts.net/v1/whoami
```

Expect `{"identity":"<your tailnet login>","mechanism":"uds_proxy_hop",
"socket_uid":0,...}`. `socket_uid` still says `0` on purpose: the kernel's
answer is reported beside the one that replaced it, so the hop is visible in the
diagnostic instead of being something you have to remember. Then confirm the
audit trail, which is the actual point — written and read through the same
tailnet hop, so the result cannot depend on a hub:

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"adj_type":"add","quantity":1,"source":"cli"}' \
  https://600g4.miku-parore.ts.net/v1/parts/C100000/adjust
curl -s https://600g4.miku-parore.ts.net/v1/parts/C100000/history
# the new row's "source": "cli@<your login>", not "cli@root"
```

**4. The forge check — do this before you trust the hop.** From a tailnet
client, assert a login that is *not* yours, and one that is off the allowlist:

```bash
curl -s -H 'Tailscale-User-Login: someone-else@example.com' \
  https://600g4.miku-parore.ts.net/v1/whoami
```

* **Correct:** the identity is still *your* real tailnet login. tailscaled owns
  the header, a client cannot write it, and the design's assumption holds.
* **Wrong:** the identity is `someone-else@example.com`. tailscaled is
  forwarding a client-supplied header, so the hop is a login-spoofing hole.
  Unset `DUBIS_UDS_PROXY_UIDS`, fix the proxy's tailnet auth
  (`tailscale serve --auth`), and re-run this step.

This is the one check that could not be run from the change's own side, because
running it means touching the box.

**5. The fallbacks, which must keep working.** From the box, over the socket,
with no proxy in the way (so no header):

```bash
sudo -u <teammate> curl -s --unix-socket /run/dubis/dubis.sock http://x/v1/whoami
```

Expect `mechanism: uds_peer_uid` and *their* username, not `root` and not a
tailnet login — the `ssh -L` teammate is still attributed to their own account
with the hop armed.

## Rollback

Remove `DUBIS_UDS_PROXY_UIDS` (or set it empty) and restart. The branch is then
unreachable and every Unix-socket caller resolves to its own peer uid again. No
data is involved: identity only ever participates in the `source` column of new
rows, never in stored state.

## What this does not do

* It does not touch fremont, the cluster, or the tailscale operator config.
* It does not make `/v1/health` say anything about identity — health is
  auth-exempt and carries `Access-Control-Allow-Origin: *`, which is why the
  diagnostic is a separate, non-exempt route (`tests/python/server/test_health_cors.py`
  guards that surface). Health does now report `build_sha`, and that is
  consistent rather than a contradiction: a commit id is a per-process constant
  the public image tag already is, while an identity is per-caller data — the
  exact key set health may carry is pinned by that test, and identity is not in
  it.
* It does not attribute a *hub* acting for a user. When a desktop hub proxies to
  aspen, aspen sees the hub's bearer token and says so (`mechanism:
  bearer_token`) — the hop is about the socket, not about every hop.
