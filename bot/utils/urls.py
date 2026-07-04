import re
from typing import Optional

from slugify import slugify

REGEX_DRIVE_ID = re.compile(r"[-\w]{25,}")
REGEX_DRIVE_URL = re.compile(r".*/d/(?P<id>[-\w]{25,})[^-\w]?.*")


def puzzle_url_slug(name: str, sep: str = "-") -> str:
    """Slugify *name* into a URL path segment for a puzzle's hunt-site link.

    Unlike Discord's ``clean_channel_name`` (which passes non-ASCII through
    unchanged so the stored name matches the channel Discord creates), this
    transliterates accents/emoji/em-dashes down to plain ASCII so the URL stays
    web-safe. *sep* is the hunt's configured word separator (``url_sep``).
    """
    return slugify(name, separator=sep)

def spreadsheet_url(sheet_id: str, page_id=None) -> str:
    if sheet_id.startswith("https://"):
        return sheet_id
    if page_id is not None:
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={page_id}"
    else:
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}"

def docs_url(file_id: str) -> str:
    if file_id.startswith("https://"):
        return file_id
    return f"https://docs.google.com/document/d/{file_id}"

def drive_folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/u/0/folders/{folder_id}"

def normalize_external_url(url: str) -> str:
    """Ensure *url* has a scheme, defaulting to ``https://`` when missing.

    A hunt's own site URL is free-text (typed at !hunt creation or set via the
    dashboard), unlike the Google IDs/URLs above. Discord's masked-link
    markdown (``[text](url)``) silently refuses to render as a link — showing
    the raw brackets instead — unless the URL has a recognised scheme, so a
    bare ``example.com`` breaks every puzzle/hunt link built from it.
    """
    url = url.strip()
    if not url or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        return url
    return f"https://{url}"

def extract_id_from_url(url: str) -> Optional[str]:
    m = re.match(REGEX_DRIVE_URL, url)
    if m:
        return m["id"]
    m = re.match(REGEX_DRIVE_ID, url)
    if m:
        return url
    return None
