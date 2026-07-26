"""The pure OCR wrapper: image bytes in, text out. Real tesseract, no mocks --
mirrors test_image_tiler.py's convention of exercising the real engine rather
than a stand-in for it (see docs/agents/python.md on what makes a mock-heavy
test worthless here)."""
import pyvips

from bot.core import ocr


def _text_png(text: str) -> bytes:
    """A real PNG with real rendered text, via pyvips (already a project
    dependency) -- same generation technique this session's manual OCR spike
    used, and the same one test_image_tiler.py's _write_image() uses for
    build_pyramid's tests."""
    t = pyvips.Image.text(text, width=600, dpi=150)
    canvas = pyvips.Image.black(t.width + 80, t.height + 80, bands=1) + 255
    canvas = canvas.insert(t.ifthenelse(0, 255), 40, 40)
    return canvas.write_to_buffer(".png")


def test_extract_text_reads_clean_typed_text():
    png = _text_png("HYDROGEN")
    assert "HYDROGEN" in ocr.extract_text(png)


def test_extract_text_returns_empty_string_for_a_blank_image():
    blank = (pyvips.Image.black(200, 100, bands=1) + 255).write_to_buffer(".png")
    assert ocr.extract_text(blank) == ""


def test_extract_text_is_not_a_fixed_stub():
    """Two different images must produce different text -- proves this reads
    the actual pixels rather than returning some constant string regardless of
    input (the failure mode a lazily-mocked or hard-coded implementation would
    pass everything else here without catching)."""
    a = ocr.extract_text(_text_png("HYDROGEN"))
    b = ocr.extract_text(_text_png("OXYGEN"))
    assert a != b
