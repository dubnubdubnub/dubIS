# dubIS

Personal electronics-parts inventory, as a desktop app: a Python FastAPI backend
(`/v1` API) plus a pywebview window running a vanilla JS/HTML/CSS frontend — no
build step, no framework. CSV files are the source of truth; SQLite (`cache.db`)
is a derived, deletable cache. It imports purchases from DigiKey, LCSC, Mouser,
and Pololu, consumes BOMs against stock, integrates with OpenPnP, and can
optionally run as an always-on remote server instead of a local process.

This README is the entry point for both humans and Claude agents. The deep
codebase guide — architecture tables, data flow, EventBus map, known traps — is
[CLAUDE.md](CLAUDE.md); read that before making code changes.

## Quick start

Requires Python 3.12 (the version the server container and CI use).

```bash
pip install -r requirements.txt
python app.pyw
```

That boots the `/v1` server on a loopback port and opens the app window.
Optional extras (Tesseract OCR, local VLM via llama.cpp for photographed packing
lists): see [docs/install.md](docs/install.md).

## Development

```bash
pip install -r requirements-dev.txt   # pytest, ruff, playwright deps, ...
npm install                           # eslint, tsc, vitest, playwright
```

Before any PR, run the single catch-all gate:

```bash
bash scripts/verify.sh    # or: npm run verify
```

It runs the staleness guards plus ruff, pytest, eslint, tsc, and vitest. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the branch/PR workflow and
[CLAUDE.md](CLAUDE.md) for per-change fast loops and gotchas (e.g. WebView2
caching stale JS, fixture regeneration after backend changes).

## Repo map

| Path | What lives there |
|------|------------------|
| `server/` | FastAPI `/v1` app: routes, mutations, SSE pub/sub, Pydantic models |
| `domain/` | Extracted business logic: inventory, pricing, generic parts, part registry, schema SSOT |
| `js/`, `css/`, `index.html` | Frontend — ES modules, split stylesheets, no build step |
| `data/`, `events/` | CSV source of truth, JSON config, SQLite cache (`cache.db`, rebuildable) |
| `tests/` | `tests/python/` (pytest), `tests/js/` (vitest + Playwright E2E), fixtures |
| `docs/` | Design docs, `docs/install.md`, `docs/ci-reference.md`, `docs/deploy-runbook.md`, OpenAPI spec |
| `tools/` | Optional MCP servers for agent workflows (dev-tools, dubis, ssh) |
| `scripts/` | `verify.sh`, `push-pr.sh`, fixture/type generators |

## More

- [CLAUDE.md](CLAUDE.md) — the canonical codebase guide (architecture, data flow, policies, traps)
- [docs/](docs/) — design docs and references
- [docs/deploy-runbook.md](docs/deploy-runbook.md) — deploying `dubis-server` remotely (k3s/tailnet)
