#!/usr/bin/env python3
"""Score categorize.py against a real-part corpus and print where it goes wrong.

    python scripts/score-categorize-corpus.py tests/fixtures/categorize-corpus/*.jsonl
    python scripts/score-categorize-corpus.py ~/.cache/dubis/categorize-corpus/lcsc-100k.jsonl

Each file is JSONL as written by scripts/build-categorize-corpus.py; the
distributor is the file's stem up to the first "-" (lcsc.jsonl, lcsc-100k.jsonl,
digikey.jsonl).  A part is:
  correct  -- landed in a section its distributor category allows
  missed   -- landed in Other though a real section was expected (harmless)
  WRONG    -- landed in a real section the distributor category rules out;
              the kind of bug this corpus exists to catch

``--with-categories`` scores what categorize() actually does now: the
distributor's own category first, the description second. It then reports the
difference per part, which is the only way to see what a map row did to a part
you were not thinking about. It applies the shipped misfile list
(category_refusals.csv) too, because that is what the app does: a part the
project has recorded as a distributor misfile is scored with its description,
not with the wrong category. Without that the number would be circular for
exactly the parts known-misfiles.json says the distributor got wrong.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "python"))

from categorize_corpus_labels import allowed  # noqa: E402

import category_map  # noqa: E402
import category_refusals  # noqa: E402
from categorize import categorize  # noqa: E402


def _entry(distributor: str, r: dict) -> category_map.DistributorCategory:
    key = category_map.CategoryMap._path_to_key(distributor, r["category_path"])  # noqa: SLF001
    return category_map.DistributorCategory(distributor, *key)


def _section(distributor: str, r: dict, with_categories: bool,
             refusals: category_refusals.Refusals | None = None) -> str:
    row = {"Description": r["description"], "Manufacture Part Number": r["mpn"],
           "Manufacturer": r["manufacturer"]}
    if not with_categories:
        return categorize(row).split(" > ")[0]
    entry = _entry(distributor, r)
    if refusals is not None and refusals.refuses(r["part_number"], entry):
        entry = None  # type: ignore[assignment]
    return categorize(row, distributor_category=entry).split(" > ")[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--top", type=int, default=40, help="confusions to list")
    ap.add_argument("--with-categories", action="store_true",
                    help="feed each part's distributor category into categorize(), "
                         "the way the app does now")
    args = ap.parse_args()
    refusals = category_refusals.load() if args.with_categories else None
    for path in args.files:
        distributor = path.stem.split("-")[0]
        n = ok = missed = wrong = unlabelled = 0
        moved: list[str] = []
        opinions = 0
        refused = 0
        confusions: collections.Counter = collections.Counter()
        example: dict = {}
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            want = allowed(distributor, r["category_path"])
            if want is None:
                unlabelled += 1
                continue
            plain = _section(distributor, r, with_categories=False)
            got = _section(distributor, r, with_categories=args.with_categories,
                           refusals=refusals)
            if args.with_categories:
                entry = _entry(distributor, r)
                if refusals is not None and refusals.refuses(r["part_number"], entry):
                    refused += 1
                elif category_map.opinion(distributor, *entry[1:]):
                    opinions += 1
                if got != plain:
                    moved.append(
                        f'  {plain:30s} -> {got:30s} <- '
                        f"{' / '.join(r['category_path'])[:46]:46s} | "
                        f'{r["description"][:60]}')
            n += 1
            if got in want:
                ok += 1
            elif got == "Other":
                missed += 1
            else:
                wrong += 1
                key2 = (got, " / ".join(r["category_path"]))
                confusions[key2] += 1
                example.setdefault(key2, f'{r["mpn"]}: {r["description"][:90]}')
        print(f"\n{path}  ({n} labelled, {unlabelled} unlabelled"
              + (f", {opinions / n:.0%} get a category opinion,"
                 f" {refused} refused as a known misfile)"
                 if args.with_categories else ")"))
        print(f"  correct {ok / n:6.1%}   missed {missed / n:6.1%}   WRONG {wrong / n:6.1%} ({wrong})")
        if args.with_categories:
            print(f"  {len(moved)} parts moved vs the description alone:")
            for line in moved[:args.top]:
                print(line)
        for (got, cat), k in confusions.most_common(args.top):
            print(f"  {k:5d}  {got:34s} <- {cat[:60]:60s} | {example[(got, cat)]}")


if __name__ == "__main__":
    main()
