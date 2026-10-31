"""The per-thread Http must carry scoped credentials.

A service-account credential with no scopes cannot refresh a token
(``RefreshError('invalid_scope')``), so every Sheets/Drive call would fail.
``build()`` used to scope its own copy; the thread Http bypasses that, so it
must scope its own. Real google-auth credentials from a throwaway key — no
network, no repo secrets.
"""
import json
import threading

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bot.utils import gsheets


def _write_throwaway_key(path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    path.write_text(json.dumps({
        "type": "service_account",
        "project_id": "throwaway",
        "private_key_id": "0",
        "private_key": pem,
        "client_email": "throwaway@throwaway.iam.gserviceaccount.com",
        "client_id": "0",
        "token_uri": "https://oauth2.googleapis.com/token",
    }))


def _fresh_thread_http():
    # _thread_http caches per thread; a new thread builds from scratch.
    out = []
    t = threading.Thread(target=lambda: out.append(gsheets._thread_http()))
    t.start()
    t.join()
    return out[0]


def test_thread_http_credentials_are_scoped_for_sheets_and_drive(tmp_path, monkeypatch):
    _write_throwaway_key(tmp_path / "google_secrets.json")
    monkeypatch.chdir(tmp_path)  # _credentials() reads google_secrets.json from cwd

    creds = _fresh_thread_http().credentials

    assert not creds.requires_scopes
    assert "https://www.googleapis.com/auth/spreadsheets" in creds.scopes
    assert "https://www.googleapis.com/auth/drive" in creds.scopes
