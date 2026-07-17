# dubis-mcp

A FastMCP stdio server exposing 10 curated dubIS inventory tools over the
`/v1` HTTP API (see `docs/plans/2026-07-16-phase2-mcp-server-design.md` for
the full design). This process never touches the CSVs/SQLite cache directly
— it's an HTTP client of `/v1`, same as the frontend. The `/v1` server
(desktop app, or a standalone `python -m server`) is the single writer.

## Registration

`.mcp.json` is local/untracked (see `.mcp.json.example` for the pattern).
Add an entry like:

```json
{
  "mcpServers": {
    "dubis": {
      "command": "python",
      "args": ["tools/dubis-mcp/server.py"]
    }
  }
}
```

No env vars are required for the common case — see Discovery below. Set
`DUBIS_URL` (e.g. `"env": {"DUBIS_URL": "http://127.0.0.1:7891"}`) to pin the
server explicitly (a tailnet server, a non-default port, etc.).

## Discovery order

Implemented in `v1client.py::connect()`:

1. **`DUBIS_URL` env var** — explicit override, wins unconditionally.
2. **Port file** `data/.v1_port` — written by `server/run.py`'s
   `start_server()` (desktop app) or `server/__main__.py` (standalone) once
   uvicorn has actually bound its socket, and removed on clean shutdown.
   Read, then health-checked with `GET /v1/health` requiring the exact JSON
   body `{"ok": true}` — a stale file left behind by a crashed server points
   at a dead or unrelated port and is ignored, not trusted.
3. **Spawned fallback** — if no live server is found, spawn
   `python -m server --data-dir <repo>/data --port 0` as a child process,
   parse its `READY:<port>` stdout line, and own its lifecycle (terminated
   at `atexit`). This keeps `dubis-mcp` usable even with the desktop app
   closed.

**Spawn-fallback caveat:** the desktop app, a standalone server, and the
spawned fallback all point at the same `data/` directory with no lockfile
serializing "am I the writer" — two servers started moments apart against
the same data dir can race (both write the same port file; whichever binds
last "wins" the discovery probe). This phase does not add a lockfile; that's
tracked as Phase "1c" follow-up work. Until then: prefer running the desktop
app or a standalone `python -m server` yourself and letting `dubis-mcp`
discover it via the port file; treat the spawn fallback as a
headless-convenience path, not something to run alongside another `/v1`
instance against the same data dir.

## Tools

All read tools return **compact projections** — trimmed fields plus counts,
never the full 14-field inventory record — so results stay small in agent
context.

| Tool | One-liner |
|---|---|
| `dubis_status()` | Which `/v1` server this session is talking to (url, discovery method, schema version, part count). |
| `search_parts(query="", section="", max_results=25)` | Case-insensitive substring search over lcsc/mpn/description/manufacturer/package, optionally scoped to one section. |
| `get_part(part_key)` | One aggregated detail card: inventory fields + prices + purchase history + generic groups + last 5 history entries. |
| `spec_search(part_type, value, package="")` | Find the generic group (+ best in-stock member) matching a BOM spec; `value` may be numeric or a display string like `"100nF"`. |
| `low_stock(threshold=None)` | Parts at/below a quantity threshold, per-section from preferences when `threshold` is omitted. |
| `price_summary(part_key)` | Per-distributor price aggregates + last purchase-order quantity. |
| `part_history(part_key, limit=10)` | Adjustment log for one part, most recent first. |
| `list_generic_parts(part_type="")` | Generic-part groups with member counts and each group's best-stock member. |
| `adjust_stock(part_key, adj_type, quantity, note="")` | Apply a stock adjustment; see semantics below. |
| `consume_bom(matches, board_qty=1, bom_name="mcp-bom")` | Consume a batch of BOM part matches against inventory as one operation. |

Every mutation (`adjust_stock`, `consume_bom`) sends `source="mcp"`, visible
afterward via `part_history`.

### `adjust_stock` semantics

- `adj_type="set"` — absolute new quantity. **May create a brand-new
  `part_key`** that has no prior ledger/adjustment row — this is how you
  seed a part that only exists via manual adjustments, mirroring the
  desktop UI's own Adjust-modal flow for new parts.
- `adj_type="add"` / `adj_type="remove"` — relative change against an
  **existing** part. These are pre-checked against current inventory before
  the `/v1` call: `/v1`'s domain layer silently no-ops add/remove on an
  unknown key (it only materializes new rows on `set`), so without the
  precheck a bad key would come back as a misleading `{"new_qty": null}`.
  Instead, `dubis-mcp` raises `ValueError("Part not found: <key>")` itself,
  before ever calling `/v1`.

## Out of scope (this phase)

Mutations beyond adjust/consume (vendors, POs, imports — desktop/UI
concerns), distributor fetches (network-bound, credential-coupled), feeder
tools (Phase 3), any write to generic parts. See the design doc's "Explicitly
OUT" list.
