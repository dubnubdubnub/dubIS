"""GET /v1/parts/{key}/attributes and POST /v1/parts/{key}/evaluate.

The read route exists because attributes were persisted but unreachable over
/v1 -- the frontend and tools/dubis-mcp only speak /v1, so nothing outside
Python could see them. The evaluate route turns a requirement into a recorded
verdict without writing anything.
"""

import pytest


def _record(api, part_key, distributor, attributes):
    """Persist parametrics the way the distributor fetch path does."""
    return api.record_fetched_attributes(part_key, distributor, attributes)


class TestReadAttributes:
    def test_absent_part_reads_as_empty_not_an_error(self, client):
        r = client.get("/v1/parts/C999999/attributes")
        assert r.status_code == 200 and r.json() == []

    def test_recorded_attributes_are_readable_over_v1(self, client, api):
        _record(api, "C100000", "lcsc", [
            {"name": "Voltage - Supply", "value": "0.9V~5.5V"},
            {"name": "Capacitance", "value": "0.4pF"},
        ])
        rows = client.get("/v1/parts/C100000/attributes").json()
        by_name = {r["canonical_name"]: r for r in rows}
        assert len(rows) == 2
        supply = by_name["supply_voltage"]
        assert supply["distributor"] == "lcsc"
        assert supply["raw_value"] == "0.9V~5.5V"
        # Numbers arrive typed, in SI base units, with both ends of the range.
        assert supply["value_min"] == pytest.approx(0.9)
        assert supply["value_max"] == pytest.approx(5.5)
        assert supply["unit"] == "V"


class TestEvaluate:
    """The 74AXP1T45GWH case: the A side must reach 0.9 V."""

    REQ = {"attribute": "Voltage - Supply", "op": "lte", "bound": "lower",
           "value": 0.9, "unit": "V", "label": "min VCCA"}

    def _evaluate(self, client, predicates, **extra):
        r = client.post("/v1/parts/C100000/evaluate",
                        json={"predicates": predicates, **extra})
        assert r.status_code == 200, r.text
        return r.json()

    def test_candidate_that_reaches_the_floor_passes(self, client, api):
        _record(api, "C100000", "lcsc", [{"name": "Voltage - Supply", "value": "0.9V~5.5V"}])
        out = self._evaluate(client, [self.REQ])
        assert out["status"] == "pass"
        assert out["spec_deltas"] == [] and out["blockers"] == []

    def test_candidate_with_a_higher_floor_fails_with_evidence(self, client, api):
        _record(api, "C100000", "lcsc", [{"name": "Voltage - Supply", "value": "1.65V~5.5V"}])
        out = self._evaluate(client, [self.REQ])
        assert out["status"] == "fail"
        assert out["blockers"] == ["min VCCA"]
        delta = out["spec_deltas"][0]
        assert delta["field"] == "min VCCA" and delta["kind"] == "parametric"
        assert delta["blocking"] is True
        assert "1.65" in delta["candidate"]
        assert delta["evidence"] == "lcsc parametric"

    def test_missing_data_is_indeterminate_never_pass(self, client):
        out = self._evaluate(client, [self.REQ])
        assert out["status"] == "indeterminate"
        assert out["verdicts"][0]["status"] == "unknown"
        assert out["status"] != "pass"

    def test_evaluation_writes_nothing(self, client, api):
        """A judgement must never quietly become an approval."""
        _record(api, "C100000", "lcsc", [{"name": "Voltage - Supply", "value": "1.65V~5.5V"}])
        before = client.get("/v1/parts/C100000/attributes").json()
        self._evaluate(client, [self.REQ])
        assert client.get("/v1/parts/C100000/attributes").json() == before

    def test_no_predicates_is_a_vacuous_pass(self, client):
        assert self._evaluate(client, [])["status"] == "pass"


class TestMalformedRequests:
    """Two rejection paths, deliberately different codes.

    A schema violation is caught by Pydantic at the boundary (422); a
    semantically invalid predicate gets past the schema and is raised by the
    facade (400). Both must reject rather than drop, because a requirement that
    silently disappears reads to the caller as a pass.
    """

    def test_unknown_predicate_field_is_rejected_not_dropped(self, client):
        """Pydantic drops unknown keys by default -- `extra="forbid"` stops that.

        Without it this returns 200 with the typo'd requirement discarded, and
        the caller is told their part passed a check that never ran.
        """
        r = client.post("/v1/parts/C100000/evaluate",
                        json={"predicates": [{"op": "lte", "value": 1, "atribute": "typo"}]})
        assert r.status_code == 422
        assert "atribute" in r.text

    def test_unknown_op_is_400(self, client):
        r = client.post("/v1/parts/C100000/evaluate",
                        json={"predicates": [{"attribute": "x", "op": "roughly", "value": 1}]})
        assert r.status_code == 400 and "unknown op" in r.text

    def test_numeric_op_without_a_value_is_400(self, client):
        r = client.post("/v1/parts/C100000/evaluate",
                        json={"predicates": [{"attribute": "x", "op": "lte"}]})
        assert r.status_code == 400


class TestPackageDefaulting:
    PKG = {"op": "package_equivalent", "package": "SOT-363-6", "label": "package"}

    def test_explicit_package_is_used(self, client):
        out = client.post("/v1/parts/C100000/evaluate", json={
            "predicates": [self.PKG], "package": "6-TSSOP, SC-88, SOT-363",
        }).json()
        assert out["status"] == "pass"
        assert out["package"] == "6-TSSOP, SC-88, SOT-363"

    def test_a_different_package_fails(self, client):
        out = client.post("/v1/parts/C100000/evaluate", json={
            "predicates": [self.PKG], "package": "SOD-882",
        }).json()
        assert out["status"] == "fail"

    def test_unknown_package_is_indeterminate_not_fail(self, client):
        """Not having checked differs from having checked and refused."""
        out = client.post("/v1/parts/C100000/evaluate", json={
            "predicates": [self.PKG], "package": "",
        }).json()
        assert out["status"] == "indeterminate"


class TestDistributorPreference:
    def test_prefer_is_honoured_over_the_default_order(self, client, api):
        _record(api, "C100000", "lcsc", [{"name": "Capacitance", "value": "0.9pF"}])
        _record(api, "C100000", "digikey", [{"name": "Capacitance", "value": "0.4pF"}])
        req = [{"attribute": "capacitance", "op": "lte", "value": 0.5, "unit": "pF"}]

        default = client.post("/v1/parts/C100000/evaluate", json={"predicates": req}).json()
        assert default["status"] == "pass"
        assert default["verdicts"][0]["distributor"] == "digikey"

        forced = client.post("/v1/parts/C100000/evaluate",
                             json={"predicates": req, "prefer": ["lcsc"]}).json()
        assert forced["status"] == "fail"
        assert forced["verdicts"][0]["distributor"] == "lcsc"
