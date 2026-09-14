"""Authorization Code + PKCE helpers for the optional "Connect your Spotify
account" feature (private/collaborative playlist support, §2-E).

Groovarr is already a persistent, browser-reachable web application, so the
redirect target for the one-time interactive authorization step is simply a
route on Groovarr's own API (see app/api/spotify_oauth.py) rather than a
throwaway local HTTP listener spun up just for this — same effect (a loopback
redirect URI with no public exposure needed beyond what already serves the
UI), less moving parts.
"""

import base64
import hashlib
import secrets

SPOTIFY_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"

# playlist-read-private + playlist-read-collaborative (§3) — enough to read
# the owner's own private/collaborative playlists; nothing broader is
# requested.
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative"


def generate_code_verifier() -> str:
    """A cryptographically random 43-128 character unreserved-charset string,
    per RFC 7636.
    """
    return secrets.token_urlsafe(64)[:128]


def code_challenge_for(code_verifier: str) -> str:
    """S256 code challenge derived from the verifier, per RFC 7636."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str, code_verifier: str) -> str:
    """Build the URL the user's browser is sent to for the one-time
    interactive Spotify authorization step.
    """
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": SPOTIFY_SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge_for(code_verifier),
    }
    return f"{SPOTIFY_AUTHORIZE_URL}?{urlencode(params)}"
