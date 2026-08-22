import csv
import os

import cart_qty
import domain.pricing


L = [(1, 9.2), (20, 7.23), (40, 6.83)]  # (break_qty, unit_price)

# The pre-packaging 8-column header, spelled out so the legacy-file tests below
# keep testing the legacy file even as domain.pricing.FIELDNAMES grows.
LEGACY_FIELDNAMES = ["timestamp", "part_id", "distributor", "unit_price",
                     "currency", "source", "moq", "note"]


def _write_events(tmp_path, rows, fieldnames=None):
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    csv_path = events_dir / "price_observations.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fieldnames or LEGACY_FIELDNAMES)
        for row in rows:
            writer.writerow(row)
    return str(events_dir)


def test_shortfall_rounds_up_to_nearest_break():
    # need 15 -> nearest break >=15 is 20, and 20 <= 2*15 -> 20
    assert cart_qty.default_qty(15, L) == 20


def test_shortfall_break_more_than_double_rounds_to_ten():
    # need 3 -> nearest break >=3 is 20, but 20 > 2*3 -> round 3 up to nearest 10 => 10
    assert cart_qty.default_qty(3, L) == 10


def test_shortfall_above_all_breaks_uses_largest_break():
    assert cart_qty.default_qty(100, L) == 40


def test_no_shortfall_uses_lowest_break_when_cheap():
    cheap = [(10, 0.05), (100, 0.02)]  # 10*0.05 = 0.5 <= 30
    assert cart_qty.default_qty(None, cheap) == 10


def test_no_shortfall_expensive_lowest_break_defaults_to_five():
    pricey = [(1, 40.0), (5, 35.0)]  # 1*40 = 40 > 30
    assert cart_qty.default_qty(None, pricey) == 5


def test_no_ladder_returns_shortfall_or_one():
    assert cart_qty.default_qty(7, []) == 7
    assert cart_qty.default_qty(None, []) == 1


def test_no_shortfall_unsorted_ladder_uses_smallest_break():
    # ladder deliberately out of order -> must still pick smallest break_qty (10), not ladder[0] (100)
    assert cart_qty.default_qty(None, [(100, 0.02), (10, 0.05)]) == 10


def test_tier_ladder_latest_per_moq_wins(tmp_path):
    rows = [
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "5.00", "USD", "manual", "10", ""),
        ("2026-06-01T00:00:00Z", "PN1", "LCSC", "4.50", "USD", "manual", "10", ""),  # later, wins
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "3.00", "USD", "manual", "50", ""),
        ("2026-01-01T00:00:00Z", "PN1", "MOUSER", "9.99", "USD", "manual", "10", ""),
        ("2026-01-01T00:00:00Z", "OTHER_PN", "LCSC", "1.00", "USD", "manual", "10", ""),
    ]
    events_dir = _write_events(tmp_path, rows)
    ladder = cart_qty.tier_ladder(events_dir, "PN1", "LCSC")
    assert ladder == [(10, 4.5), (50, 3.0)]
    # ascending by qty
    assert [q for q, _ in ladder] == sorted(q for q, _ in ladder)


def test_tier_ladder_missing_file_returns_empty(tmp_path):
    empty_dir = tmp_path / "events_missing"
    empty_dir.mkdir()
    assert cart_qty.tier_ladder(str(empty_dir), "PN1", "LCSC") == []


def test_tier_ladder_skips_malformed_rows(tmp_path):
    rows = [
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "5.00", "USD", "manual", "", ""),  # blank moq
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "bad", "USD", "manual", "10", ""),  # non-numeric price
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "6.00", "USD", "manual", "abc", ""),  # non-numeric moq
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "7.00", "USD", "manual", "25", ""),  # valid
    ]
    events_dir = _write_events(tmp_path, rows)
    ladder = cart_qty.tier_ladder(events_dir, "PN1", "LCSC")
    assert ladder == [(25, 7.0)]


# ── default_qty regression pin ────────────────────────────────────────────
#
# The cost-stepping rule (docs/superpowers/specs/2026-07-24-cart-feature-design.md)
# is deliberate, and `default_qty` takes an already-built ladder — so making
# `tier_ladder` packaging-aware must not move a single one of these outputs.
# This table was captured from the pre-packaging implementation and is asserted
# verbatim: it passes identically before and after the packaging change. Do not
# "fix" an entry here to match new behaviour — a diff in this table IS the bug.

DEFAULT_QTY_PINS = [
    # (shortfall, ladder, expected)
    # ── no ladder: shortfall passes through, else 1 ──
    (None, [], 1),
    (0, [], 1),
    (-5, [], 1),
    (1, [], 1),
    (7, [], 7),
    (999, [], 999),
    # ── shortfall + ladder: next break if <= 2x shortfall, else round up to 10 ──
    (1, L, 1),        # break 1 is exactly the shortfall
    (2, L, 10),       # next break 20 > 2*2 -> round 2 up to 10
    (3, L, 10),       # next break 20 > 2*3 -> round 3 up to 10
    (10, L, 20),      # next break 20 == 2*10 -> take the break (<=, not <)
    (11, L, 20),      # next break 20 <= 22
    (15, L, 20),
    (20, L, 20),      # shortfall lands exactly on a break
    (21, L, 40),      # next break 40 <= 42
    (25, L, 40),
    (30, L, 40),      # 40 == 2*20? no: 40 <= 60 -> take it
    (41, L, 40),      # above every break -> largest break
    (100, L, 40),
    (39, L, 40),
    (26, L, 40),      # 40 <= 52
    # a ladder whose only break is huge: 2x rule pushes to round-up-10
    (7, [(3000, 0.01)], 10),
    (1600, [(3000, 0.01)], 3000),   # 3000 <= 2*1600
    (1400, [(3000, 0.01)], 1400),   # 3000 > 2*1400 -> round 1400 up to 10
    (1495, [(3000, 0.01)], 1500),   # round-up-to-10 actually rounds
    # ── no shortfall: lowest break, or 5 when its extended price > $30 ──
    (None, [(10, 0.05), (100, 0.02)], 10),
    (None, [(1, 9.2), (20, 7.23)], 1),
    (None, [(1, 40.0), (5, 35.0)], 5),        # 1*40 = 40 > 30 -> 5
    (None, [(1, 30.0)], 1),                   # 1*30 == 30, not > 30 -> 1
    (None, [(1, 30.01)], 5),
    (None, [(100, 0.02), (10, 0.05)], 10),    # unsorted -> smallest break
    (None, [(3000, 0.011)], 5),               # 3000*0.011 = 33 > 30 -> 5
    (None, [(3000, 0.009)], 3000),            # 27 <= 30 -> the break itself
    (0, L, 1),                                # falsy shortfall == no shortfall
]


def test_default_qty_pinned_outputs():
    """Every (shortfall, ladder) -> qty pair below is frozen behaviour."""
    actual = [(s, tuple(lad), cart_qty.default_qty(s, lad))
              for s, lad, _ in DEFAULT_QTY_PINS]
    expected = [(s, tuple(lad), exp) for s, lad, exp in DEFAULT_QTY_PINS]
    assert actual == expected


# ── packaging-aware ladders ───────────────────────────────────────────────

PKG_FIELDNAMES = domain.pricing.FIELDNAMES


def _write_observations(tmp_path, observations):
    """Write observations through the REAL writer, so these tests exercise the
    same encoding (carrier derivation, is_reel flags) production writes."""
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    domain.pricing.record_observations(str(events_dir), observations)
    return str(events_dir)


def _obs(ts, part_id, distributor, moq, price, **extra):
    return {"timestamp": ts, "part_id": part_id, "distributor": distributor,
            "moq": moq, "unit_price": price, "source": "live_fetch", **extra}


def test_legacy_file_ladder_is_unchanged_by_packaging_awareness(tmp_path):
    """The pre-packaging fixture, read by the packaging-aware reader.

    Byte-for-byte the same rows as test_tier_ladder_latest_per_moq_wins above
    (8-column header, no packaging), asserting the identical ladder — the
    grouping change must be invisible to a file that has no packaging in it.
    """
    rows = [
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "5.00", "USD", "manual", "10", ""),
        ("2026-06-01T00:00:00Z", "PN1", "LCSC", "4.50", "USD", "manual", "10", ""),
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "3.00", "USD", "manual", "50", ""),
        ("2026-01-01T00:00:00Z", "PN1", "MOUSER", "9.99", "USD", "manual", "10", ""),
        ("2026-01-01T00:00:00Z", "OTHER_PN", "LCSC", "1.00", "USD", "manual", "10", ""),
    ]
    events_dir = _write_events(tmp_path, rows)
    assert cart_qty.tier_ladder(events_dir, "PN1", "LCSC") == [(10, 4.5), (50, 3.0)]
    # An explicit "" asks for exactly that unknown-packaging ladder.
    assert cart_qty.tier_ladder(events_dir, "PN1", "LCSC", "") == [(10, 4.5), (50, 3.0)]


def test_cut_tape_and_reel_ladders_stay_separate(tmp_path):
    """The bug this change exists to fix: same part, same distributor, same
    break quantity, two packagings — neither may overwrite the other."""
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 1, 0.10,
             packaging="Cut Tape (CT)"),
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 10, 0.09,
             packaging="Cut Tape (CT)"),
        # Same moq 1 as cut tape, different ladder, LATER timestamp — under the
        # old moq-only keying this row silently replaced the cut-tape 1-break.
        _obs("2026-02-01T00:00:00", "PN1", "digikey", 1, 0.20,
             packaging="Digi-Reel"),
        _obs("2026-02-01T00:00:00", "PN1", "digikey", 3000, 0.04,
             packaging="Tape & Reel (TR)"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "Cut Tape (CT)") == [
        (1, 0.10), (10, 0.09)]
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "Digi-Reel") == [(1, 0.20)]
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "Tape & Reel (TR)") == [
        (3000, 0.04)]


def test_ladders_are_enumerable_per_packaging(tmp_path):
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 1, 0.10, packaging="Cut Tape"),
        _obs("2026-02-01T00:00:00", "PN1", "digikey", 3000, 0.04,
             packaging="Tape & Reel"),
    ])
    ladders = cart_qty.tier_ladders(events_dir, "PN1", "digikey")
    assert set(ladders) == {"cut tape", "tape & reel"}
    assert ladders["cut tape"]["ladder"] == [(1, 0.10)]
    assert ladders["cut tape"]["carrier"] == "tape"
    assert ladders["cut tape"]["is_reel"] is False
    assert ladders["tape & reel"]["is_reel"] is True


def test_packaging_match_is_case_insensitive(tmp_path):
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 1, 0.10,
             packaging="Cut Tape (CT)"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "cut tape (ct)") == [(1, 0.10)]
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "  Cut Tape (CT) ") == [(1, 0.10)]


def test_packaging_falls_back_to_carrier_and_reel_match(tmp_path):
    """A caller who knows the physical form but not the vendor's prose."""
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 1, 0.10,
             packaging="Cut Tape (CT)"),
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 3000, 0.04,
             packaging="Tape & Reel (TR)"),
    ])
    # "cut tape" is not the stored name, but it is the same carrier + not-reel.
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "cut tape") == [(1, 0.10)]
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "tape & reel") == [(3000, 0.04)]


def test_unmatched_packaging_returns_empty_not_another_ladder(tmp_path):
    """Substituting a different packaging's prices would be a silent lie."""
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 1, 0.10,
             packaging="Cut Tape (CT)"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "Tray") == []
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "Wholly Unknown") == []
    # ...and the unknown-packaging group is not a wildcard either.
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "") == []


def test_unspecified_packaging_prefers_the_non_reel_ladder(tmp_path):
    """A reel ladder's breaks start at the reel quantity, so feeding one to
    default_qty for an unspecified packaging buys a whole reel nobody asked
    for. Asserted against default_qty's real output for both ladders."""
    cut_tape = [(1, 0.10), (10, 0.09), (100, 0.06), (1000, 0.05)]
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", q, p, packaging="Cut Tape")
        for q, p in cut_tape
    ] + [
        # Later, and cheaper per unit — but not what you buy for a shortfall.
        _obs("2026-06-01T00:00:00", "PN1", "digikey", 3000, 0.04,
             packaging="Tape & Reel"),
    ])
    chosen = cart_qty.tier_ladder(events_dir, "PN1", "digikey")
    assert chosen == cut_tape
    reel = cart_qty.tier_ladder(events_dir, "PN1", "digikey", "Tape & Reel")
    assert cart_qty.default_qty(1600, chosen) == 1000
    assert cart_qty.default_qty(1600, reel) == 3000   # the reel we avoided


def test_unspecified_packaging_uses_a_reel_ladder_only_if_alone(tmp_path):
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 3000, 0.04,
             packaging="Tape & Reel"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey") == [(3000, 0.04)]


def test_unspecified_packaging_picks_most_recent_of_the_non_reels(tmp_path):
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 1, 0.10, packaging="Cut Tape"),
        _obs("2026-06-01T00:00:00", "PN1", "digikey", 1, 2.00, packaging="Tray"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey") == [(1, 2.00)]


def test_unknown_packaging_group_competes_as_non_reel(tmp_path):
    """Legacy rows are unknown, not reels — a later packaged reel must not
    displace them when no packaging is asked for."""
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "lcsc", 10, 0.05),
        _obs("2026-06-01T00:00:00", "PN1", "lcsc", 3000, 0.01, packaging="Reel"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "lcsc") == [(10, 0.05)]


def test_latest_per_break_still_wins_inside_one_packaging(tmp_path):
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "lcsc", 10, 5.00, packaging="Cut Tape"),
        _obs("2026-06-01T00:00:00", "PN1", "lcsc", 10, 4.50, packaging="Cut Tape"),
        _obs("2026-01-01T00:00:00", "PN1", "lcsc", 50, 3.00, packaging="Cut Tape"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "lcsc", "Cut Tape") == [
        (10, 4.5), (50, 3.0)]


def test_authoritative_is_reel_flag_beats_the_name(tmp_path):
    """LCSC calls it a "Reel" but flags isReel False for parts it won't reel;
    the stored flag, not the prose, decides whether it is a reel ladder."""
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "C393939", "lcsc", 1, 0.02,
             packaging="Reel", is_reel=False),
    ])
    ladders = cart_qty.tier_ladders(events_dir, "C393939", "lcsc")
    assert ladders["reel"]["is_reel"] is False
    # ...so it is eligible as the unspecified-packaging ladder.
    assert cart_qty.tier_ladder(events_dir, "C393939", "lcsc") == [(1, 0.02)]


def test_packaging_does_not_leak_across_part_or_distributor(tmp_path):
    events_dir = _write_observations(tmp_path, [
        _obs("2026-01-01T00:00:00", "PN1", "digikey", 1, 0.10, packaging="Cut Tape"),
        _obs("2026-01-01T00:00:00", "PN1", "mouser", 1, 0.12, packaging="Cut Tape"),
        _obs("2026-01-01T00:00:00", "PN2", "digikey", 1, 0.99, packaging="Cut Tape"),
    ])
    assert cart_qty.tier_ladder(events_dir, "PN1", "digikey", "Cut Tape") == [(1, 0.10)]
    assert cart_qty.tier_ladder(events_dir, "PN1", "mouser", "Cut Tape") == [(1, 0.12)]
    assert cart_qty.tier_ladder(events_dir, "PN2", "digikey", "Cut Tape") == [(1, 0.99)]


def test_tier_ladders_missing_file_returns_empty(tmp_path):
    empty_dir = tmp_path / "events_missing"
    empty_dir.mkdir()
    assert cart_qty.tier_ladders(str(empty_dir), "PN1", "LCSC") == {}
    assert cart_qty.tier_ladder(str(empty_dir), "PN1", "LCSC", "Cut Tape") == []


def test_malformed_rows_still_skipped_with_packaging(tmp_path):
    rows = [
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "5.00", "USD", "manual", "", "",
         "Cut Tape", "tape", "0", "", ""),
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "bad", "USD", "manual", "10", "",
         "Cut Tape", "tape", "0", "", ""),
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "7.00", "USD", "manual", "25", "",
         "Cut Tape", "tape", "0", "", ""),
    ]
    events_dir = _write_events(tmp_path, rows, fieldnames=PKG_FIELDNAMES)
    assert cart_qty.tier_ladder(events_dir, "PN1", "LCSC", "Cut Tape") == [(25, 7.0)]


def test_migrated_file_with_blank_packaging_behaves_like_legacy(tmp_path):
    """A file that has been through the header migration but never had a
    packaged write: 13 columns, all packaging cells empty."""
    rows = [
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "5.00", "USD", "manual", "10", "",
         "", "", "", "", ""),
        ("2026-06-01T00:00:00Z", "PN1", "LCSC", "4.50", "USD", "manual", "10", "",
         "", "", "", "", ""),
        ("2026-01-01T00:00:00Z", "PN1", "LCSC", "3.00", "USD", "manual", "50", "",
         "", "", "", "", ""),
    ]
    events_dir = _write_events(tmp_path, rows, fieldnames=PKG_FIELDNAMES)
    assert cart_qty.tier_ladder(events_dir, "PN1", "LCSC") == [(10, 4.5), (50, 3.0)]
    assert cart_qty.tier_ladders(events_dir, "PN1", "LCSC")[""]["carrier"] is None


# ── observed_distributors ────────────────────────────────────────────────────
# The plan needs the distributors that QUOTED a part, which is not the same set
# as the ones it was catalogued or bought from (domain.pricing's
# get_sourced_distributors). A part with a quote and no purchase history is the
# ordinary case for a cart built from a BOM.

def test_observed_distributors_finds_a_quote_with_no_purchase_history(tmp_path):
    events = _write_events(tmp_path, [
        ["2026-01-01T00:00:00", "C52923", "lcsc", "0.01", "USD", "web", "10", ""],
        ["2026-01-01T00:00:00", "C52923", "lcsc", "0.005", "USD", "web", "100", ""],
    ])
    assert cart_qty.observed_distributors(events, "C52923") == ["lcsc"]


def test_observed_distributors_dedupes_and_sorts(tmp_path):
    events = _write_events(tmp_path, [
        ["2026-01-01T00:00:00", "P1", "mouser", "1.0", "USD", "web", "1", ""],
        ["2026-01-02T00:00:00", "P1", "lcsc", "0.9", "USD", "web", "1", ""],
        ["2026-01-03T00:00:00", "P1", "mouser", "0.8", "USD", "web", "10", ""],
    ])
    assert cart_qty.observed_distributors(events, "P1") == ["lcsc", "mouser"]


def test_observed_distributors_does_not_leak_across_parts(tmp_path):
    events = _write_events(tmp_path, [
        ["2026-01-01T00:00:00", "P1", "lcsc", "1.0", "USD", "web", "1", ""],
        ["2026-01-01T00:00:00", "P2", "mouser", "1.0", "USD", "web", "1", ""],
    ])
    assert cart_qty.observed_distributors(events, "P1") == ["lcsc"]
    assert cart_qty.observed_distributors(events, "P2") == ["mouser"]


def test_observed_distributors_ignores_blank_distributor(tmp_path):
    events = _write_events(tmp_path, [
        ["2026-01-01T00:00:00", "P1", "", "1.0", "USD", "web", "1", ""],
    ])
    assert cart_qty.observed_distributors(events, "P1") == []


def test_observed_distributors_missing_file_returns_empty(tmp_path):
    assert cart_qty.observed_distributors(str(tmp_path / "nope"), "P1") == []


def test_observed_distributors_batch_matches_the_per_part_version(tmp_path):
    events = _write_events(tmp_path, [
        ["2026-01-01T00:00:00", "P1", "lcsc", "1.0", "USD", "web", "1", ""],
        ["2026-01-01T00:00:00", "P2", "mouser", "1.0", "USD", "web", "1", ""],
        ["2026-01-02T00:00:00", "P2", "lcsc", "0.9", "USD", "web", "1", ""],
    ])
    batch = cart_qty.observed_distributors_batch(events, ["P1", "P2", "P3"])
    assert batch == {"P1": ["lcsc"], "P2": ["lcsc", "mouser"]}
    for pid in ("P1", "P2"):
        assert batch[pid] == cart_qty.observed_distributors(events, pid)
    # A part with no observation is absent, not mapped to an empty list: the
    # caller distinguishes "nothing quoted it" from "we did not ask".
    assert "P3" not in batch


def test_observed_distributors_batch_no_ids_skips_the_file(tmp_path):
    events = _write_events(tmp_path, [
        ["2026-01-01T00:00:00", "P1", "lcsc", "1.0", "USD", "web", "1", ""],
    ])
    assert cart_qty.observed_distributors_batch(events, []) == {}
    assert cart_qty.observed_distributors_batch(events, [""]) == {}
