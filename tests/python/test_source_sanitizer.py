"""Tests for source_sanitizer module."""
import hashlib
import io

import pytest

import source_sanitizer as ss


def _png_bytes() -> bytes:
    """A minimal valid 1x1 PNG."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes_with_exif() -> bytes:
    """A 1x1 JPEG with EXIF metadata."""
    from PIL import Image
    buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), (0, 255, 0))
    # Pillow's exif builder
    exif = img.getexif()
    exif[0x010F] = "TestMaker"  # Make
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _pdf_bytes() -> bytes:
    """Minimal valid PDF magic header."""
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF"


class TestMagicBytes:
    def test_pdf_passes(self):
        assert ss.validate_magic(_pdf_bytes(), ".pdf") is True

    def test_png_passes(self):
        assert ss.validate_magic(_png_bytes(), ".png") is True

    def test_jpeg_passes(self):
        assert ss.validate_magic(_jpeg_bytes_with_exif(), ".jpg") is True

    def test_jpeg_alt_extension(self):
        assert ss.validate_magic(_jpeg_bytes_with_exif(), ".jpeg") is True

    def test_csv_extension_skips_magic_check(self):
        assert ss.validate_magic(b"a,b,c\n1,2,3\n", ".csv") is True

    def test_mismatch_fails(self):
        assert ss.validate_magic(_png_bytes(), ".pdf") is False


class TestExifStrip:
    def test_jpeg_exif_stripped(self):
        from PIL import Image
        original = _jpeg_bytes_with_exif()
        sanitized = ss.strip_exif(original, ".jpg")
        img = Image.open(io.BytesIO(sanitized))
        exif = img.getexif()
        assert 0x010F not in exif

    def test_png_passthrough(self):
        # PNG doesn't have EXIF; should pass through unchanged
        original = _png_bytes()
        sanitized = ss.strip_exif(original, ".png")
        assert sanitized == original

    def test_pdf_passthrough(self):
        original = _pdf_bytes()
        assert ss.strip_exif(original, ".pdf") == original


class TestRejectedExtensions:
    @pytest.mark.parametrize("ext", [".xlsm", ".docm", ".pptm"])
    def test_macro_extensions_rejected(self, ext):
        with pytest.raises(ValueError, match="macro"):
            ss.sanitize(b"PK\x03\x04anything", ext)


class TestSanitize:
    def test_returns_bytes_hash_ext(self):
        data = _png_bytes()
        out_bytes, out_hash, out_ext = ss.sanitize(data, ".png")
        assert out_bytes == data  # PNG passes through unchanged
        assert out_ext == ".png"
        assert out_hash == hashlib.sha256(data).hexdigest()

    def test_jpeg_hash_changes_after_strip(self):
        data = _jpeg_bytes_with_exif()
        out_bytes, out_hash, _ = ss.sanitize(data, ".jpg")
        assert out_hash == hashlib.sha256(out_bytes).hexdigest()
        assert out_bytes != data  # EXIF stripped → bytes different

    def test_invalid_magic_raises(self):
        with pytest.raises(ValueError, match="magic"):
            ss.sanitize(_png_bytes(), ".pdf")
