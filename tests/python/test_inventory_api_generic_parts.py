"""Tests for InventoryApi — generic parts CRUD and BOM resolution."""


class TestGenericPartsAPI:
    def _import_parts(self, api):
        api.import_purchases([
            {"LCSC Part Number": "C1525", "Manufacture Part Number": "CL05B104KO5NNNC",
             "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
             "Manufacturer": "Samsung", "Quantity": "200",
             "Unit Price($)": "0.0074", "Ext.Price($)": "1.48",
             "Description": "100nF 16V 0402 Capacitor MLCC", "Package": "0402",
             "RoHS": "", "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
            {"LCSC Part Number": "C9999", "Manufacture Part Number": "CL05B104KA5NNNC",
             "Digikey Part Number": "", "Pololu Part Number": "", "Mouser Part Number": "",
             "Manufacturer": "Samsung", "Quantity": "50",
             "Unit Price($)": "0.006", "Ext.Price($)": "0.30",
             "Description": "100nF 25V 0402 Capacitor MLCC", "Package": "0402",
             "RoHS": "", "Customer NO.": "", "Estimated lead time (business days)": "",
             "Date Code / Lot No.": ""},
        ])

    def test_create_generic_part(self, api):
        self._import_parts(api)
        result = api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert result["generic_part_id"].startswith("cap_")
        assert len(result["members"]) == 2  # C1525 and C9999

    def test_resolve_bom_spec(self, api):
        self._import_parts(api)
        api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        result = api.resolve_bom_spec("capacitor", 1e-7, "0402")
        assert result is not None
        assert result["best_part_id"] in ("C1525", "C9999")

    def test_list_generic_parts(self, api):
        self._import_parts(api)
        api.create_generic_part(
            name="100nF 0402",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        gps = api.list_generic_parts()
        assert len(gps) == 1
        assert gps[0]["name"] == "100nF 0402"

    def test_get_generic_group_names_empty_when_not_a_member(self, api):
        self._import_parts(api)
        assert api.get_generic_group_names("C1525") == []

    def test_get_generic_group_names_lists_membership(self, api):
        self._import_parts(api)
        api.create_generic_part(
            name="100nF 0402 MLCC",
            part_type="capacitor",
            spec_json='{"value":"100nF","package":"0402"}',
            strictness_json='{"required":["value","package"]}',
        )
        assert api.get_generic_group_names("C1525") == ["100nF 0402 MLCC"]
        assert api.get_generic_group_names("C9999") == ["100nF 0402 MLCC"]

    def test_get_generic_group_names_multiple_groups_sorted(self, api):
        self._import_parts(api)
        gp_z = api.create_generic_part(
            name="Z Group", part_type="capacitor",
            spec_json='{"value":"200nF","package":"0603"}',
            strictness_json='{}',
        )
        gp_a = api.create_generic_part(
            name="A Group", part_type="resistor",
            spec_json='{"value":"10k","package":"0805"}',
            strictness_json='{}',
        )
        api.add_generic_member(gp_z["generic_part_id"], "C1525")
        api.add_generic_member(gp_a["generic_part_id"], "C1525")
        assert api.get_generic_group_names("C1525") == ["A Group", "Z Group"]
