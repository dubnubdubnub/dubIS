# Reaching a remote dubIS over SSH (`ssh://` servers)

A dubIS server running on a shared Linux box is reachable from any teammate's
desktop app **without anyone running a tunnel by hand**. You add the server as
an `ssh://` URL; your local dubIS (the *hub*) starts `ssh`, keeps it alive,
restarts it after sleep, and kills it when you quit. Implementation:
`server/ssh_tunnel.py`, wired in by `server/sources.py`'s `SourceClients`.

## Onboarding a teammate (three steps)

1. **Box side, once per person.** Give them a Unix account on the box and put
   their public key in `~/.ssh/authorized_keys`. For the per-person
   attribution described below they also need permission to connect to the
   server's socket (e.g. group membership on its directory). The shared box's
   setup (systemd unit, tmpfiles.d socket directory, group, traps) is in
   [deploy/systemd/README.md](../deploy/systemd/README.md).
2. **Their machine.** Check key auth works non-interactively:
   `ssh -o BatchMode=yes you@box true`. If the key has a passphrase, load it
   into the agent first (`ssh-add`; on macOS `ssh-add --apple-use-keychain`
   keeps it across reboots). The hub never prompts — it runs ssh in BatchMode.
3. **In dubIS.** Preferences → Server → Add server, URL:
   `ssh://you@box/run/dubis/dubis.sock` (or `ssh://you@box/127.0.0.1:7891` for
   a TCP server). Done — it shows up in the tab strip like any other server.

The system OpenSSH client is used: built into macOS and Linux; on Windows 10+
enable the "OpenSSH Client" optional feature. Your `~/.ssh/config` applies
(host aliases, `IdentityFile`, `ProxyJump`), except connection multiplexing,
which the hub turns off so no tunnel escapes its supervision.

## URL grammar

    ssh://[user@]host[:sshport]/<remote target>

| Remote target | Meaning | Example |
|---|---|---|
| contains no `:` | absolute Unix socket path on the box | `ssh://me@box/run/dubis/dubis.sock` |
| contains a `:` | TCP `host:port` on the box | `ssh://me@box/127.0.0.1:7891`, `ssh://me@box/[::1]:7891` |

A colon decides it, unambiguously: ssh's `-L` cannot forward a socket path that
contains one. There is no default target (`ssh://me@box` is refused), no
password in the URL (keys/agent only), and no query string. A user or host that
starts with `-` is refused, since it would reach ssh as an option. The same
grammar is implemented in `js/servers-logic.js` (`parseSshUrl`); both are pinned
by `tests/fixtures/ssh/url-cases.json`.

**Prefer the socket form.** Against `python -m server --uds <path>` on the box,
sshd connects to the socket *as the ssh user*, so `server/peercred.py` stamps
each mutation `source@<username>`. Over the TCP form every teammate arrives as a
loopback peer — `local` — and the attribution is lost.

## What you see when it breaks

`GET /v1/sources` reports, for an `ssh://` source, a `tunnel` object
(`state`: `idle` | `starting` | `up` | `failed`; `kind`; `error`) and folds the
failure sentence into `detail`. The picker's dot shows a terse label with the
full sentence as its tooltip; the tab strip shows the sentence.

| `kind` | Label | Cause / fix |
|---|---|---|
| `auth` | ssh key refused | `Permission denied` / too many auth failures. Add your key on the box, or `ssh-add` a passphrase-protected key. |
| `host_key` | host key changed | The box's host key differs from `known_hosts`. Verify, then `ssh-keygen -R box`. (A *new* host is accepted on first use, like typing "yes".) |
| `dns` | unknown host | Typo, or not on the tailnet/VPN. |
| `unreachable` | host unreachable | Connection refused/timed out. |
| `remote_target` | dubIS not running | ssh works but nothing listens at the target path/port on the box. |
| `timeout` | ssh timed out | ssh neither connected nor failed within 15s. |
| `no_ssh` | no ssh client | Install/enable OpenSSH. |
| `exited` | ssh failed | Anything else; the sentence carries ssh's last stderr line. |

A tunnel that was up and died (sleep, network change — `ServerAliveInterval=15`
x3 notices within ~45s) restarts on the very next request, on the same local
port when free. A tunnel that fails to *start* backs off 1s, 2s, 4s ... 30s, and
inside that window the last failure is reported without re-running ssh.

## Lifecycle notes

* Lazy: nothing is spawned until the source is first used (the tab strip's
  `GET /v1/sources` probe counts as a use).
* Only the hub spawns tunnels. A second, attached window has no hub of its own
  and uses the owner's tunnels.
* Cleanup: on hub shutdown (app lifespan, then `atexit`); on Linux
  `PR_SET_PDEATHSIG` also kills tunnels if the hub is SIGKILLed; and the hub
  records its tunnels in `<data_dir>/.ssh_tunnels.json`, so the next start reaps
  any a hard-killed hub left behind (matched on pid *and* the exact `-L` spec in
  the command line, so a reused pid is spared). On Windows the reap cannot read
  command lines and leaves such processes alone.
