"""Attachment byte storage for the message bridge.

Kept pure/free of discord.py so it unit-tests without a client: a caller passes
an aiohttp session, a source URL, and a destination path.
"""
import logging
import re
from pathlib import Path

from bot.utils import config

logger = logging.getLogger(__name__)


def media_root() -> Path:
    return Path(config.web_archive_media_dir)


def safe_filename(name: str) -> str:
    """Collapse to a safe basename: no separators, no traversal."""
    name = (name or "").replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    name = "_".join(parts)
    name = re.sub(r"[^A-Za-z0-9 ._-]", "_", name).strip("_")
    return name or "file"


def attachment_relpath(guild_id: int, channel_id: int, attachment_id: int, filename: str) -> str:
    """Relative path under media_root(); stored on the row and re-joined when serving."""
    return f"{guild_id}/{channel_id}/{attachment_id}_{safe_filename(filename)}"


async def download_attachment(session, url: str, dest: Path, max_bytes: int) -> bool:
    """Stream url -> dest, capped at max_bytes. Returns True on success.
    Never raises: a failed download leaves no partial file and returns False."""
    try:
        async with session.get(url) as resp:
            declared = resp.headers.get("Content-Length")
            if declared is not None and int(declared) > max_bytes:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with open(dest, "wb") as fh:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        return False
                    fh.write(chunk)
        return True
    except Exception:
        logger.exception("attachment download failed: %s", url)
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return False
