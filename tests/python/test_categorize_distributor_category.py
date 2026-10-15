"""``categorize()`` with a distributor category: precedence, veto, subcategories.

The description rules are unchanged here -- test_categorize_corpus.py holds
those floors. What these tests pin is the new decision on top of them: when
the distributor's own category gets to overrule a description, when it does
not, and what still comes from the description either way.
"""
from __future__ import annotations

import pytest

import category_map
import category_refusals
from categorize import categorize
from category_map import DistributorCategory


def _row(desc="", mpn="", mfr=""):
    return {"Description": desc, "Manufacture Part Number": mpn, "Manufacturer": mfr}


def _place(desc, distributor, category, subcategory="", mpn="", mfr=""):
    return categorize(_row(desc, mpn, mfr),
                      distributor_category=DistributorCategory(distributor, category,
                                                               subcategory))


class TestNoCategoryIsTheOldBehaviour:
    """Everything that called categorize(row) must be byte-identical."""

    def test_positional_call_still_works(self):
        assert categorize(_row("RES 10k 0402")) == "Passives - Resistors"

    def test_none_is_the_same_as_omitting_it(self):
        row = _row("100nF 0402 MLCC")
        assert categorize(row, distributor_category=None) == categorize(row)

    def test_a_category_the_map_has_no_opinion_on_changes_nothing(self):
        row = _row("RES 10k 0402")
        assert categorize(row, distributor_category=DistributorCategory(
            "mouser", "Semiconductors", "Something Nobody Labelled")) == \
            "Passives - Resistors"

    def test_a_category_the_map_abstains_on_changes_nothing(self):
        row = _row("RES 10k 0402")
        assert categorize(row, distributor_category=DistributorCategory(
            "mouser", "Passive Components")) == "Passives - Resistors"


class TestPrecedence:
    def test_the_distributor_wins_over_the_description(self):
        """The TMUXHS4212 class: an analog switch quoting its on-resistance.

        The description says "SWITCH", which the Switches rule claims, and the
        part is nothing of the kind. DigiKey's breadcrumb is the only signal
        that knows.
        """
        got = _place("LOW VOLTAGE DUAL ANALOG SWITCH 8.4OHM",
                     "digikey", "Analog Switches, Multiplexers, Demultiplexers",
                     "Interface")
        assert got == "ICs - Interface"

    def test_the_distributor_wins_over_a_description_naming_a_feature(self):
        """C7495014: an Ethernet controller whose feature list mentions LEDs.

        The rules read "Programmable LED indication" and file a gigabit PHY as
        an LED. This is the case the feature exists for -- and the one the
        fixture blames on the description, which is what makes it the right
        half of the pair with ``TestPerPartRefusal`` below.
        """
        assert _place("10BASE-T、100BASE-TX、1000BASE-T PCIe SPI、I2C、SMBus "
                      "Wake-up function、Programmable LED indication QFN-64(9x9) "
                      "Ethernet Controllers ROHS",
                      "lcsc", "Interface", "Ethernet Controllers") == \
            "ICs - Interface"

    def test_the_description_still_answers_when_the_map_is_silent(self):
        assert _place("RES 10k 0402", "mouser", "Electronic Components") == \
            "Passives - Resistors"

    def test_other_is_a_real_answer_not_a_fallthrough(self):
        """Memory has no dubIS section; "Other" is the decision, not the default."""
        assert _place("EEPROM 256Kbit I2C 8-SOIC", "mouser", "Memory") == "Other"


class TestVeto:
    def test_a_description_that_disclaims_the_section_and_names_another_wins(self):
        """LCSC files this under Optoelectronics; the description says driver.

        "LED DRIVER" is the one phrase the LEDs rule exists to refuse, and the
        rules have a specific answer ready -- so the distributor is overruled
        with a reason, not merely contradicted.
        """
        got = _place("LED DRIVER, 8-CHANNEL, I2C, QFN-24", "lcsc", "Optoelectronics")
        assert got == "ICs - Power / Voltage Regulators"

    def test_a_disclaimer_alone_does_not_veto_when_the_rules_say_nothing(self):
        """A heart rate sensor whose description mentions a built-in sensor.

        The veto's own exclude_desc list matches ("built-in temperature
        sensor"), but the description rules offer only Other -- so refusing
        here would trade a correct placement for an unsorted part. Measured on
        the corpus, this exact part is why the veto needs the second half.
        """
        got = _place("19bit Integrated ambient light cancellation、Built-in "
                     "temperature sensor SPI WLP-20 Specialized Sensor",
                     "lcsc", "Magnetic Sensors", "Heart Rate Sensors")
        assert got == "ICs - Sensors"

    def test_no_veto_without_a_disclaimer(self):
        # "Reed Switches" trips the Switches rule; LCSC says it is a sensor and
        # nothing in the description disclaims being a sensor.
        assert _place("插件 Reed Switches ROHS", "lcsc", "Magnetic Sensors",
                      "Linear Hall Sensors") == "ICs - Sensors"


class TestPerPartRefusal:
    """``category_refusals.csv``: the per-part carve-out, applied where the
    category is *chosen*, not where it is used.

    C7497415 is FU6522L, a microcontroller in LQFP-48 that JLC files under
    *Programmers, Emulators, And Debuggers*. The description is the half that
    is right about it and the category is the misfile -- which is the opposite
    of the analog-switch case above, and the reason "trust the distributor"
    cannot be a blanket rule. Refusing LCSC's answer leaves the part with no
    category at all, so it is placed by its description.
    """

    MISFILE = DistributorCategory(
        "lcsc", "Development Boards & Tools",
        "Programmers, Emulators, And Debuggers")

    def _refused(self, part_id="C7497415", by=None):
        import domain.distributor_categories as store
        return store.resolve(by or {"lcsc": self.MISFILE}, part_id=part_id,
                             refusals=category_refusals.load())

    def test_the_shipped_list_refuses_the_answer_it_names(self):
        refusals = category_refusals.load()
        assert refusals.refusal_for("C7497415", self.MISFILE) is not None
        # ...and only that answer: the same part's next fetch, filed correctly,
        # is not refused forever by a row about the old misfile.
        assert refusals.refusal_for(
            "C7497415", DistributorCategory("lcsc", "Microcontrollers")) is None

    def test_a_refused_category_never_reaches_categorize(self):
        assert self._refused() is None

    def test_the_part_is_placed_by_its_description_not_by_the_misfile(self):
        row = _row("LQFP-48(7x7) Microcontrollers (MCU/MPU/SOC) ROHS")
        # First, the regression itself, stated as what it looks like: with no
        # carve-out the map wins this part and files a microcontroller as a
        # development board.
        assert categorize(row, distributor_category=self.MISFILE) == \
            "Development Boards, Kits, Programmers"
        assert categorize(row, distributor_category=self._refused()) == \
            "ICs - Microcontrollers"

    def test_refusing_lcsc_hands_the_decision_to_digikey(self):
        """Why this is a per-distributor skip and not a section veto.

        A seam that could only refuse a *section* would blind every distributor
        for the part at once. Skipping one leaves the next in PREFERENCE to
        answer, and DigiKey's taxonomy has this part right.
        """
        digikey = DistributorCategory("digikey", "System On Chip (SoC)", "Embedded")
        assert self._refused(by={"lcsc": self.MISFILE, "digikey": digikey}) == digikey

    def test_the_production_resolver_applies_the_list(self, tmp_path):
        """The defect this replaces: a carve-out nothing in production passed.

        Driven through ``resolver`` -- the callable ``domain.inventory.rebuild``
        hands ``categorize_and_sort`` -- over a real store file, so the shipped
        list is proven wired into the rebuild path rather than only into a
        test's own arguments.
        """
        import domain.distributor_categories as store
        import inventory_ops

        store.record_categories(str(tmp_path), "C7497415", "lcsc",
                                self.MISFILE.category, self.MISFILE.subcategory)
        store.record_categories(str(tmp_path), "C1525", "lcsc",
                                self.MISFILE.category, self.MISFILE.subcategory)
        row = {"Description": "LQFP-48(7x7) Microcontrollers (MCU/MPU/SOC) ROHS",
               "Manufacture Part Number": "FU6522L", "Manufacturer": "",
               "LCSC Part Number": "C7497415"}
        resolver = store.resolver(str(tmp_path), inventory_ops.get_part_key)
        assert resolver(row) is None
        assert list(inventory_ops.categorize_and_sort(
            [row], category_resolver=resolver))[0] == "ICs - Microcontrollers"
        # The same misfiled category recorded against a different part still
        # places that part: the refusal is about C7497415, and nothing else
        # loses its category because of it.
        other = dict(row, **{"LCSC Part Number": "C1525"})
        assert resolver(other) == self.MISFILE


class TestSubcategoriesStillComeFromTheDescription:
    """The distributor decides the parent and nothing else.

    No distributor's taxonomy is dubIS's sub-branching, and the subcategories
    are where the value-sorted views get their shape.
    """

    def test_a_distributor_placed_capacitor_still_gets_its_subcategory(self):
        assert _place("100nF 16V X7R 0402 MLCC", "lcsc", "Capacitors") == \
            "Passives - Capacitors > MLCC"

    def test_a_rule_placed_capacitor_is_unchanged(self):
        assert categorize(_row("CAP CER 100nF 16V X7R 0402")) == \
            "Passives - Capacitors > MLCC"

    def test_a_distributor_placed_resistor_sorts_into_chip_resistors(self):
        assert _place("10kΩ ±1% 0402 resistor", "lcsc", "Resistors") == \
            "Passives - Resistors > Chip Resistors"

    def test_a_parent_with_no_subcategory_rules_is_returned_bare(self):
        assert _place("CR1220 Battery holder", "lcsc", "Connectors",
                      "Battery Connector") == "Connectors"


class TestResolverIntegration:
    def test_categorize_and_sort_uses_the_resolver_and_defaults_to_the_old_path(self):
        import inventory_ops

        rows = [{"Description": "LQFP-48(7x7) Microcontrollers (MCU/MPU/SOC) ROHS",
                 "Manufacture Part Number": "FU6522L", "Manufacturer": "Fortior"}]
        assert list(inventory_ops.categorize_and_sort(rows)) == ["ICs - Microcontrollers"]
        got = inventory_ops.categorize_and_sort(
            rows,
            category_resolver=lambda r: DistributorCategory(
                "lcsc", "Development Boards & Tools",
                "Programmers, Emulators, And Debuggers"))
        assert list(got) == ["Development Boards, Kits, Programmers"]

    def test_the_real_map_is_the_one_categorize_reads(self):
        """Guards against a suite that only ever passes its own fixture map."""
        assert category_map.opinion("lcsc", "Capacitors", "") == "Passives - Capacitors"

    def test_an_unmapped_distributor_never_raises_out_of_categorize(self):
        # categorize() runs per part inside rebuild(); one odd row must not
        # take the whole rebuild down.
        assert categorize(_row("RES 10k"), distributor_category=DistributorCategory(
            "rs-components", "Resistors")) == "Passives - Resistors"


@pytest.mark.parametrize("distributor", ["lcsc", "digikey", "mouser"])
def test_every_labelled_distributor_can_place_something(distributor):
    """A distributor the map knows but that never places is a silent no-op."""
    loaded = category_map.load()
    placed = [k for k, sections in loaded._by_key.items()  # noqa: SLF001
              if k[0] == distributor and len(sections) == 1]
    assert placed, f"{distributor} has no single-section row, so it can never place a part"
    key = placed[0]
    assert category_map.opinion(distributor, key[1], key[2])


def test_pololu_is_the_one_distributor_nobody_has_labelled_yet():
    """Recorded, not forgotten.

    Pololu's client keeps the *first* breadcrumb as its category -- the opposite
    polarity to DigiKey and Mouser -- so its rows would key on a different level
    again, and no ground-truth pass over Pololu's catalogue has happened. The
    store accepts pololu rows today; the map simply has none, so a Pololu
    category is a recorded fact that places nothing. This test is the place
    that fact stops being invisible.
    """
    loaded = category_map.load()
    assert not [k for k in loaded._by_key if k[0] == "pololu"]  # noqa: SLF001
    assert "pololu" in category_map.DISTRIBUTORS
