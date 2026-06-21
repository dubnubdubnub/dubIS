import ocr_layout


def test_tag_rows_sets_backend_and_null_bbox():
    rows = [{"mpn": "A", "quantity": 1}, {"mpn": "B", "quantity": 2}]
    out = ocr_layout._tag_rows(rows, "grid")
    assert all(r["_backend"] == "grid" for r in out)
    assert all(r["bbox"] is None for r in out)


def test_tag_rows_preserves_existing_bbox_for_vlm():
    rows = [{"mpn": "A", "_backend": "vlm", "bbox": [1, 2, 3, 4]}]
    out = ocr_layout._tag_rows(rows, "vlm")
    assert out[0]["bbox"] == [1, 2, 3, 4]
