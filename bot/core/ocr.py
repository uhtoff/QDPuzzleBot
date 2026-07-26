"""Text extraction for evidence images, via the system tesseract binary.

Pure: bytes in, text out. No DAOs, no Quart -- the async orchestration (running
this off the event loop) lives in the web layer, the same split image_tiler.py
and media_tiling.py already use.
"""
import io

import pytesseract
from PIL import Image


def extract_text(image_bytes: bytes) -> str:
    """OCR `image_bytes` (any format PIL can open) and return the text found.

    Raises on a corrupt/unreadable image or a tesseract failure -- callers are
    responsible for catching and reporting that, the same way image_tiler's
    build_pyramid raises rather than swallowing errors itself. Unlike the
    "never raises" convenience helpers elsewhere in this codebase
    (media_tiling.tile_media, bot/web/actions.py's _crop_region_media), THIS
    function's callers must NOT swallow the exception silently: the whole
    point of an OCR request is giving the user something back, so a silent
    failure here would just look broken. See the web-layer routes for where
    that reporting happens.
    """
    img = Image.open(io.BytesIO(image_bytes))
    return pytesseract.image_to_string(img).strip()
