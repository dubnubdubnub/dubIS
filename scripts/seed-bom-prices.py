#!/usr/bin/env python3
"""Quote every part in a BOM, so a cart built from it can be planned.

    scripts/seed-bom-prices.py data/glasgow_revD0_bom.csv --url http://localhost:8080

WHY THIS EXISTS
`GET /v1/carts/{id}/plan` can only recommend a quantity for a part something
has quoted -- it reads `price_observations.csv` and invents nothing. For parts
you stock, those ladders accumulate on their own: every price/adjust modal
auto-fetches. For the parts a BOM turns up that you have never bought, the only
writer is the BOM row's own hover tooltip (`js/part-preview.js`), one row at a
time. A 119-line BOM is 119 hovers, and a plan is worth nothing until they are
all done.

This does the same two calls the hover does -- fetch the product, post its
ladder -- for every row at once. It is a tool, not a fix: the product answer is
a "quote every line" action in the cart modal, and when that exists this script
becomes a convenience for scripted runs.

WHAT IT SENDS
`packagings` / `reelQty` / `reelFee` ride along exactly as the tooltip sends
them, which is what makes a *reel* offer expressible at all -- a cut-tape ladder
plus a reeling fee is what `domain.purchase_candidates` turns into the derived
"+ reeling" offer. Dropping them would still record prices, and every plan
would then be quietly cut-tape-only.

The server does the fetching (this only speaks /v1), so it works against a
local server or the cluster. LCSC is a plain JSON API, so a headless pod can
reach it; Mouser and DigiKey are scrapers with heavier requirements, which is
why `--distributor` defaults to lcsc alone.

Rows are looked up by their LCSC column, since that is the key the tooltip
records under and therefore the key the plan resolves.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

LCSC_CODE = re.compile(r"^C\d{4,}$", re.IGNORECASE)


def _headers(token: str | None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _request(url: str, token: str | None, payload: dict | None = None,
             timeout: float = 30.0):
    """One /v1 call. Returns the decoded body, or None on any HTTP failure.

    A failure here is one part not quoted, never a reason to abandon the run --
    a BOM has hundreds of rows and one 404 (a code the distributor no longer
    lists) should cost that row alone.
    """
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(token),
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {"_error": str(e)}


def lcsc_codes(csv_path: str) -> list[str]:
    """Every distinct LCSC code in a BOM, in first-seen order.

    The column is found the same loose way the UI's detector does (any header
    mentioning lcsc or jlcpcb), so an export that spells it "LCSC Part #" is
    read too. Order is preserved only to make a --limit run reproducible.
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return []
    col = next((h for h in rows[0]
                if h and re.search(r"lcsc|jlcpcb", h, re.IGNORECASE)), None)
    if col is None:
        sys.exit(f"{csv_path}: no LCSC column found in {list(rows[0])}")
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        code = (row.get(col) or "").strip().upper()
        if LCSC_CODE.match(code) and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def seed(base: str, token: str | None, codes: list[str], distributor: str,
         delay: float, dry_run: bool) -> tuple[int, int, int]:
    quoted = skipped = failed = 0
    for i, code in enumerate(codes, 1):
        prefix = f"[{i}/{len(codes)}] {code}"
        if dry_run:
            print(f"{prefix}: would fetch from {distributor}")
            skipped += 1
            continue
        product = _request(f"{base}/v1/distributors/{distributor}/product/{code}",
                           token)
        if not product or product.get("_error"):
            reason = (product or {}).get("_error", "no response")
            print(f"{prefix}: fetch failed ({reason})")
            failed += 1
        elif not product.get("prices"):
            # A real product page that quotes nothing. Recording an empty
            # ladder would be indistinguishable from never having asked.
            print(f"{prefix}: no price ladder published")
            skipped += 1
        else:
            result = _request(
                f"{base}/v1/parts/{code}/fetched-prices", token,
                {"distributor": distributor,
                 "price_tiers": product["prices"],
                 "packagings": product.get("packagings"),
                 "reel_qty": product.get("reelQty"),
                 "reel_fee": product.get("reelFee")},
            )
            if result.get("_error"):
                print(f"{prefix}: record failed ({result['_error']})")
                failed += 1
            else:
                breaks = len(product["prices"])
                packs = len(product.get("packagings") or [])
                fee = product.get("reelFee")
                extra = f", reeling ${fee}" if fee else ""
                print(f"{prefix}: {breaks} breaks, {packs} packaging(s){extra}")
                quoted += 1
        # Spread the load: this is someone else's public catalogue, and a BOM
        # is hundreds of rows arriving as fast as the loop can send them.
        if delay and i < len(codes):
            time.sleep(delay)
    return quoted, skipped, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bom_csv", help="BOM CSV with an LCSC column")
    ap.add_argument("--url", default=os.environ.get("DUBIS_URL", "http://localhost:8080"),
                    help="dubIS server base URL (default: $DUBIS_URL or localhost:8080)")
    ap.add_argument("--token", default=os.environ.get("DUBIS_TOKEN"),
                    help="bearer token; omit on a tailnet-identity deploy")
    ap.add_argument("--distributor", default="lcsc",
                    help="distributor to quote from (default: lcsc)")
    ap.add_argument("--limit", type=int, default=0,
                    help="quote at most N parts (0 = all)")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="seconds between parts (default: 0.5)")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be quoted and exit")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    codes = lcsc_codes(args.bom_csv)
    if not codes:
        print(f"{args.bom_csv}: no LCSC codes found")
        return 1
    if args.limit:
        codes = codes[:args.limit]

    health = _request(f"{base}/v1/health", args.token, timeout=10.0)
    if not health or health.get("_error"):
        return _fail(f"{base} is not answering ({(health or {}).get('_error')})")
    # The plan route is what this seeding is FOR. A server without it is a
    # server where the seeded ladders would go unread -- say so now rather
    # than after several hundred distributor requests.
    spec = _request(f"{base}/v1/openapi.json", args.token, timeout=15.0)
    paths = (spec or {}).get("paths") or {}
    if paths and "/v1/carts/{cart_id}/plan" not in paths:
        return _fail(f"{base} has no /v1/carts/{{cart_id}}/plan -- it is running "
                     "an older build, so nothing would read these ladders")

    print(f"{len(codes)} part(s) to quote from {args.distributor} via {base}\n")
    quoted, skipped, failed = seed(base, args.token, codes, args.distributor,
                                   args.delay, args.dry_run)
    print(f"\nquoted {quoted}, skipped {skipped}, failed {failed}")
    return 1 if failed and not quoted else 0


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
