import ocr_engine


def test_ensure_uses_path_when_available(monkeypatch):
    monkeypatch.setattr(ocr_engine.shutil, "which", lambda n: "/usr/bin/tesseract")
    assert ocr_engine.ensure_tesseract() is True


def test_ensure_probes_common_paths(monkeypatch, tmp_path):
    fake = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    fake.parent.mkdir(parents=True)
    fake.write_text("x")
    monkeypatch.setattr(ocr_engine.shutil, "which", lambda n: None)
    monkeypatch.setattr(ocr_engine, "_CANDIDATES", [str(fake)])
    import pytesseract
    assert ocr_engine.ensure_tesseract() is True
    assert pytesseract.pytesseract.tesseract_cmd == str(fake)


def test_missing_returns_false(monkeypatch):
    monkeypatch.setattr(ocr_engine.shutil, "which", lambda n: None)
    monkeypatch.setattr(ocr_engine, "_CANDIDATES", [])
    assert ocr_engine.ensure_tesseract() is False
