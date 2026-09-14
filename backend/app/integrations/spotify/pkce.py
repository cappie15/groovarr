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
# the owner's own private/collaborative playlists — plus user-library-read,
# needed for "Liked Songs" (GET /me/tracks, see
# app/integrations/spotify/client.py's get_saved_tracks): Liked Songs is
# inherently private, user-specific data, so no scope short of this one can
# ever read it, regardless of auth mode. Nothing broader than these three is
# requested.
#
# A token issued before this scope was added here (i.e. under the old
# two-scope list) does NOT retroactively gain it — Spotify scopes are fixed
# at authorization time, so an existing PKCE connection must be redone
# ("Connect your Spotify account" again) before Liked Songs will work; until
# then, Spotify denies /me/tracks with 403 and Groovarr surfaces that as a
# clear, actionable SpotifyAccessDeniedError rather than crashing (see
# get_saved_tracks and services/spotify_sync.connect_liked_songs).
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"


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
