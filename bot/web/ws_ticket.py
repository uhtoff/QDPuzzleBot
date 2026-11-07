"""Short-lived, single-use tickets that authenticate the extension's websocket.

Chrome withholds the SameSite=Lax session cookie from a WebSocket opened by an
extension service worker, so the worker fetches a ticket over ordinary
(cookie-carrying) HTTP and presents it on the websocket URL instead.
"""
import secrets
import time

from itsdangerous import BadData, URLSafeTimedSerializer

from bot.utils import config

TICKET_MAX_AGE = 60  # seconds
_SALT = "puzzbot-ws-ticket"

# nonce -> wall-clock (epoch) expiry; a nonce present here has been redeemed.
# Same clock as itsdangerous' age check, so a nonce is never pruned while its
# ticket could still verify.
_PRUNE_MARGIN = 5
_seen = {}


def _serializer():
    return URLSafeTimedSerializer(config.web_session_secret, salt=_SALT)


def issue(user_id):
    return _serializer().dumps({"uid": str(user_id), "n": secrets.token_urlsafe(16)})


def redeem(ticket):
    """Return the user id (str) the ticket was issued to, or None if the
    ticket is malformed, tampered, expired or already used."""
    now = time.time()
    for nonce in [n for n, exp in _seen.items() if exp <= now]:
        del _seen[nonce]
    try:
        data, issued = _serializer().loads(
            ticket, max_age=TICKET_MAX_AGE, return_timestamp=True
        )
        uid, nonce = data["uid"], data["n"]
    except (BadData, KeyError, TypeError):
        return None
    if not isinstance(uid, str) or not isinstance(nonce, str):
        return None
    if nonce in _seen:
        return None
    _seen[nonce] = issued.timestamp() + TICKET_MAX_AGE + _PRUNE_MARGIN
    return uid
