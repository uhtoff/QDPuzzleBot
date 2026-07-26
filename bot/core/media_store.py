"""Storage for images attached to evidence (and, later, canvases).

Kept free of Quart and discord.py so it unit-tests without a request context or
a client: callers hand over bytes and get back a saved relative path. Bytes live
under the same media root the Discord message bridge writes to
(config.web_archive_media_dir), so there is one directory to back up and one
serving guard to get right.
"""
import logging
import uuid

from bot.core.message_media import safe_filename
from bot.utils import config

logger = logging.getLogger(__name__)

# Raster formats only. SVG is deliberately absent: it is a scriptable document,
# and serving one from our own origin would be stored XSS.
ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def sniff_image_type(head: bytes):
    """Return the content type implied by the leading bytes, or None.

    The client-declared Content-Type is attacker-controlled and is never
    trusted; this is what decides whether the upload is stored at all.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    # RIFF is a container; only the WEBP flavour is an image.
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def max_upload_bytes() -> int:
    return config.web_attachment_max_bytes


def upload_relpath(hunt_id: int, filename: str) -> str:
    """Relative path under media_root(); stored on the row and re-joined when
    serving. The uuid prefix means two people uploading `screenshot.png` never
    collide, and the stored path is never taken from user input directly."""
    return f"uploads/{hunt_id}/{uuid.uuid4().hex}_{safe_filename(filename)}"
