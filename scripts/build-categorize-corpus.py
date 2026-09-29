#!/usr/bin/env python3
"""Build the real-part corpus that tests/python/test_categorize_corpus.py
scores categorize.py against.

LCSC

Source: the yaqwsx/jlcparts catalogue (https://github.com/yaqwsx/jlcparts), a
published SQLite snapshot of the whole JLC/LCSC catalogue -- ~1.1M parts,
each with LCSC's own description and category.  No scraping: one download.

Two outputs, both a stratified sample over (category, subcategory) with a
per-leaf floor (small leaves still show up) and cap (connectors and crystals
don't swamp it), seeded so a rerun on the same snapshot is byte-identical:

  --sample-out   the few-thousand-part sample committed under
                 tests/fixtures/categorize-corpus/ (runs in every pytest)
  --full-out     a ~100k-part sample kept OUT of git, for
                 scripts/score-categorize-corpus.py when tuning the rules

    python scripts/build-categorize-corpus.py \\
        --sample-out tests/fixtures/categorize-corpus/lcsc.jsonl \\
        --full-out ~/.cache/dubis/categorize-corpus/lcsc-100k.jsonl

DigiKey
DigiKey publishes no catalogue dump and sits behind Cloudflare, so its parts
come from a capture of DigiKey's category listing pages in a real browser
(see tests/fixtures/categorize-corpus/README.md for how).  This script only
trims such a capture to the committed sample:

    python scripts/build-categorize-corpus.py \\
        --digikey-in capture.jsonl \\
        --digikey-sample-out tests/fixtures/categorize-corpus/digikey.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import struct
import urllib.request
import zlib
from collections import defaultdict
from pathlib import Path

JLCPARTS_URL = "https://yaqwsx.github.io/jlcparts/data/"
# The archive is a split zip: cache.z01, cache.z02, ... then cache.zip last.
# How many parts it has changes as the catalogue grows, so probe for them.
DEFAULT_CACHE = Path.home() / ".cache" / "dubis" / "jlcparts"
SEED = 20260929


def _fetch(name: str, dest: Path) -> bool:
    try:
        with urllib.request.urlopen(JLCPARTS_URL + name, timeout=60) as resp, open(dest, "wb") as f:
            while chunk := resp.read(1 << 20):
                f.write(chunk)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def download_db(cache: Path) -> Path:
    """Download and extract jlcparts' cache.sqlite3 into cache/."""
    cache.mkdir(parents=True, exist_ok=True)
    db = cache / "cache.sqlite3"
    parts = []
    for i in range(1, 100):
        part = cache / f"cache.z{i:02d}"
        print(f"fetching {part.name} ...", flush=True)
        if not _fetch(part.name, part):
            part.unlink(missing_ok=True)
            break
        parts.append(part)
    last = cache / "cache.zip"
    print("fetching cache.zip ...", flush=True)
    if not _fetch(last.name, last):
        raise SystemExit(f"{JLCPARTS_URL}cache.zip is missing; has jlcparts moved?")
    parts.append(last)

    # The archive holds a single deflated member.  Rather than repair the
    # split archive (zip -FF takes tens of minutes on it), read the local
    # header at the start of the first volume and inflate straight through
    # the concatenated volumes.
    def stream():
        for p in parts:
            with open(p, "rb") as f:
                while chunk := f.read(1 << 20):
                    yield chunk

    chunks = stream()
    buf = next(chunks)
    off = 4 if buf[:4] == b"PK\x07\x08" else 0  # split-archive marker
    sig, _, _, method, _, _, _, _, _, name_len, extra_len = struct.unpack(
        "<IHHHHHIIIHH", buf[off:off + 30])
    if sig != 0x04034B50 or method != 8:
        raise SystemExit(f"unexpected jlcparts archive layout (sig {sig:#x}, method {method})")
    name = buf[off + 30:off + 30 + name_len].decode()
    if name != "cache.sqlite3":
        raise SystemExit(f"unexpected jlcparts archive member {name!r}")
    inflate = zlib.decompressobj(-15)
    with open(db, "wb") as out:
        out.write(inflate.decompress(buf[off + 30 + name_len + extra_len:]))
        for chunk in chunks:
            if inflate.eof:
                break
            out.write(inflate.decompress(chunk))
        out.write(inflate.flush())
    if not inflate.eof:
        raise SystemExit("jlcparts archive ended before the database did")
    for p in parts:
        p.unlink()
    return db


def load_leaves(db: Path) -> dict[tuple[str, str], list[tuple]]:
    con = sqlite3.connect(db)
    rows = con.execute("""
        select lcsc, category, subcategory, mfr, manufacturer, description
        from jlc_components
        where description != '' and category != ''
        order by lcsc
    """).fetchall()
    leaves: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for r in rows:
        leaves[(r[1], r[2])].append(r)
    return leaves


def stratified(leaves, target: int, floor: int, cap: int, seed: int) -> list[dict]:
    """Proportional quota per leaf, clamped to [floor, cap] and to the leaf's
    size, with the scale binary-searched so the total lands on target."""
    def quotas(scale: float) -> dict:
        return {k: min(len(v), cap, max(floor, round(len(v) * scale))) for k, v in leaves.items()}

    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if sum(quotas(mid).values()) < target:
            lo = mid
        else:
            hi = mid
    q = quotas(hi)
    rng = random.Random(seed)
    out = []
    for leaf in sorted(leaves):
        for lcsc, top, sub, mpn, mfr, desc in rng.sample(leaves[leaf], q[leaf]):
            out.append({"part_number": f"C{lcsc}", "mpn": mpn, "manufacturer": mfr,
                        "description": desc, "category_path": [top, sub]})
    return out


def write_jsonl(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} parts to {path}")


def digikey_sample(raw: Path, per_leaf: int, seed: int) -> list[dict]:
    """Up to per_leaf parts from each DigiKey breadcrumb, trimmed to the
    fields categorize() reads plus the label."""
    leaves: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    seen = set()
    for line in open(raw, encoding="utf-8"):
        r = json.loads(line)
        if r["part_number"] in seen:
            continue
        seen.add(r["part_number"])
        leaves[tuple(r["category_path"])].append(r)
    rng = random.Random(seed)
    out = []
    for leaf in sorted(leaves):
        rows = sorted(leaves[leaf], key=lambda r: r["part_number"])
        for r in rng.sample(rows, min(per_leaf, len(rows))):
            out.append({"part_number": r["part_number"], "mpn": r["mpn"],
                        "manufacturer": r["manufacturer"], "description": r["description"],
                        "category_path": r["category_path"]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, help="an existing jlcparts cache.sqlite3 (else downloaded)")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ap.add_argument("--sample-out", type=Path)
    ap.add_argument("--sample-size", type=int, default=3000)
    ap.add_argument("--full-out", type=Path)
    ap.add_argument("--full-size", type=int, default=100_000)
    ap.add_argument("--digikey-in", type=Path, help="a raw DigiKey listing capture (JSONL)")
    ap.add_argument("--digikey-sample-out", type=Path)
    ap.add_argument("--digikey-per-leaf", type=int, default=50)
    args = ap.parse_args()
    if bool(args.digikey_in) != bool(args.digikey_sample_out):
        ap.error("--digikey-in and --digikey-sample-out go together")
    if args.digikey_in:
        write_jsonl(digikey_sample(args.digikey_in, args.digikey_per_leaf, SEED),
                    args.digikey_sample_out)
    if not (args.sample_out or args.full_out):
        if args.digikey_in:
            return
        ap.error("nothing to do: pass --sample-out, --full-out and/or --digikey-in")

    db = args.db or args.cache_dir / "cache.sqlite3"
    if not db.exists():
        if args.db:
            ap.error(f"{db} does not exist")
        db = download_db(args.cache_dir)
    leaves = load_leaves(db)
    print(f"{sum(map(len, leaves.values()))} described parts in {len(leaves)} leaf categories")
    if args.sample_out:
        write_jsonl(stratified(leaves, args.sample_size, 2, 60, SEED), args.sample_out)
    if args.full_out:
        write_jsonl(stratified(leaves, args.full_size, 40, 3000, SEED), args.full_out)


if __name__ == "__main__":
    main()
