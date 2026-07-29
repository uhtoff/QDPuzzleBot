"""Storage for images attached to evidence (and, later, canvases).

Kept free of Quart and discord.py so it unit-tests without a request context or
a client: callers hand over bytes and get back a saved relative path. Bytes live
under the same media root the Discord message bridge writes to
(config.web_archive_media_dir), so there is one directory to back up and one
serving guard to get right.
"""
import uuid
from typing import TypedDict

from bot.core.message_media import media_root, safe_filename
from bot.utils import config

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


class MediaRejected(Exception):
    """An upload failed validation. The message is safe to show a user."""


class SavedMedia(TypedDict):
    """What a validated, stored image needs for its `media` row.

    A TypedDict rather than a bare dict because two call sites (the web upload
    route and the Discord attachment path) index these keys by name, and a typo
    on either side would otherwise be caught by nothing — not the type checker,
    not the tests. Deliberately not `MediaData`: that carries `id` and `hunt_id`,
    which do not exist until the row is inserted.

    No `created_at`: the DAO stamps its own on insert, so generating one here
    would be a second source of truth that every caller then discards.
    """
    path: str
    filename: str
    content_type: str
    size: int
    origin: str


def validate_image_bytes(raw: bytes) -> str:
    """Reject oversize or non-allowlisted bytes; return the sniffed content type.

    Its own function because two other call sites (the OCR-preview route, and
    the discord clue-attachment path before it was unified) re-implemented this
    check inline, and one of them already drifted — it stopped checking
    ALLOWED_IMAGE_TYPES and accepted anything sniff_image_type recognised. One
    seam means there is only one place left to drift.
    """
    limit = max_upload_bytes()
    if len(raw) > limit:
        raise MediaRejected(f"Image too large (limit {limit} bytes).")
    content_type = sniff_image_type(raw[:16])
    if content_type is None or content_type not in ALLOWED_IMAGE_TYPES:
        raise MediaRejected(
            "Not a supported image. Allowed: PNG, JPEG, GIF, WebP."
        )
    return content_type


def save_image_bytes(hunt_id: int, filename: str, raw: bytes,
                     origin: str = "upload") -> SavedMedia:
    """Validate `raw` and write it under the media root.

    Returns the metadata a `media` row needs: path, filename, content_type,
    size, origin. Raises MediaRejected if the bytes are too large or are not an
    allowed image.

    There is deliberately no parameter for the client-declared content type.
    Accepting one and ignoring it would be dead API surface that every call site
    would keep faithfully passing; the magic bytes are the only input that
    decides anything here.
    """
    content_type = validate_image_bytes(raw)
    root = media_root()
    if str(root) in ("", "."):
        # An unset web.archive_media_dir makes media_root() the process cwd
        # (Path('') normalises to Path('.')). Silently scattering uploads
        # into the working directory is worse than refusing them outright.
        raise MediaRejected(
            "Image storage is not configured on this server "
            "(set web.archive_media_dir in config.json)."
        )
    relpath = upload_relpath(hunt_id, filename)
    dest = root / relpath
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(raw)
    except Exception:
        # Leave no partial file behind on a full disk or a permission error.
        # Mirrors download_attachment in message_media.py, which already solved
        # this; the exception still propagates, so no media row is ever created
        # pointing at a truncated image.
        dest.unlink(missing_ok=True)
        raise
    return {
        "path": relpath,
        "filename": safe_filename(filename),
        "content_type": content_type,
        "size": len(raw),
        "origin": origin,
    }
