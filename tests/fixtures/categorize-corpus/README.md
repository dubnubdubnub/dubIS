# Categorize corpus

Real parts that `tests/python/test_categorize_corpus.py` scores `categorize.py`
against. Each JSONL row is `{part_number, mpn, manufacturer, description,
category_path}`. The description is copied verbatim from the distributor, and
`category_path` is the **distributor's own** category, which serves as ground
truth. `tests/python/categorize_corpus_labels.py` maps each category to the
dubIS sections that count as correct.

| File | Parts | Source |
|------|-------|--------|
| `lcsc.jsonl` | 3000 | The [jlcparts](https://github.com/yaqwsx/jlcparts) snapshot of the JLC/LCSC catalogue: stratified over all 582 leaf categories, seeded. |
| `digikey.jsonl` | 1600 | DigiKey category listing pages, read in a real browser: up to 50 per leaf across 32 leaves. |
| `known-misfiles.json` | — | The parts that land in a wrong section today, each with a reason. Mostly these are rows the distributor filed under a category that its own description contradicts. |

## Rebuilding

LCSC comes from a single download and involves no scraping:

```bash
python scripts/build-categorize-corpus.py \
    --sample-out tests/fixtures/categorize-corpus/lcsc.jsonl \
    --full-out ~/.cache/dubis/categorize-corpus/lcsc-100k.jsonl
python scripts/score-categorize-corpus.py ~/.cache/dubis/categorize-corpus/lcsc-100k.jsonl
```

The `--full-out` corpus has ~100k parts. It stays out of git and is the file to
score against when tuning the rules. The committed 3000 are the regression gate.

DigiKey has no catalogue dump and sits behind Cloudflare. Its rows came from the
listing pages at `digikey.com/en/products/filter/<slug>/<leaf>`, 100 rows per
page, read one page at a time with randomized 8–25s gaps. The data is in each
page's embedded `__NEXT_DATA__`:

| Field | Source |
|-------|--------|
| part number, MPN, manufacturer | `compare` |
| short description | `productDetail.description` |
| category | `breadcrumb` |

The capture stopped after 34 pages, when DigiKey closed the connection, and it
was not pushed further. Only a few leaves are covered: no MOSFETs, crystals or
regulators yet. Trim a fresh capture to the committed sample with
`scripts/build-categorize-corpus.py --digikey-in capture.jsonl --digikey-sample-out
tests/fixtures/categorize-corpus/digikey.jsonl`, then label any new leaves in
`categorize_corpus_labels.py`.
