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

from categorize import categorize  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--top", type=int, default=40, help="confusions to list")
    args = ap.parse_args()
    for path in args.files:
        distributor = path.stem.split("-")[0]
        n = ok = missed = wrong = unlabelled = 0
        confusions: collections.Counter = collections.Counter()
        example: dict = {}
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            want = allowed(distributor, r["category_path"])
            if want is None:
                unlabelled += 1
                continue
            got = categorize({"Description": r["description"], "Manufacture Part Number": r["mpn"],
                              "Manufacturer": r["manufacturer"]}).split(" > ")[0]
            n += 1
            if got in want:
                ok += 1
            elif got == "Other":
                missed += 1
            else:
                wrong += 1
                key = (got, " / ".join(r["category_path"]))
                confusions[key] += 1
                example.setdefault(key, f'{r["mpn"]}: {r["description"][:90]}')
        print(f"\n{path}  ({n} labelled, {unlabelled} unlabelled)")
        print(f"  correct {ok / n:6.1%}   missed {missed / n:6.1%}   WRONG {wrong / n:6.1%} ({wrong})")
        for (got, cat), k in confusions.most_common(args.top):
            print(f"  {k:5d}  {got:34s} <- {cat[:60]:60s} | {example[(got, cat)]}")


if __name__ == "__main__":
    main()
