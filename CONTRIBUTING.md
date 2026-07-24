# Contributing

## Workflow

- `main` is protected: every change lands via PR with passing CI, **squash-merged**
  to keep linear history.
- Branch naming: `claude/<scope>-<description>` (e.g. `claude/feature-bom-export`).
  Yes, humans use the `claude/` prefix too — it is the repo convention.
- Push and open PRs via the helper script (it detects already-merged branches
  and creates a fresh one when needed):

  ```bash
  bash scripts/push-pr.sh                          # PR title = last commit subject
  bash scripts/push-pr.sh --title "fix: the thing"
  ```

- Before pushing, run the single verification gate:

  ```bash
  bash scripts/verify.sh    # or: npm run verify
  ```

  It runs the staleness guards plus ruff, pytest, eslint, tsc, and vitest.
- After opening a PR, watch CI (`gh pr checks <number>`) and fix failures —
  don't leave a PR red.

## Test policy

Never skip tests to hide a missing dependency — no `pytest.skip`,
`pytest.importorskip`, or `@pytest.mark.skip` for that purpose. Add the
dependency to `requirements-dev.txt` instead. Tests must run, not be skipped.

## Developing without the Claude/MCP tooling

The MCP servers in `tools/` (dev-tools, dubis, ssh) and the `.claude/` /
`memory/` scaffolding exist to accelerate agent-driven development — they are
entirely optional. Plain `git`, `pytest tests/python/`, `npx vitest run`,
`npx eslint js/`, `npx tsc --noEmit`, and `npx playwright test` work fine on
their own, and `bash scripts/verify.sh` wraps them all. Nothing in the build,
test, or runtime path depends on the agent tooling.
