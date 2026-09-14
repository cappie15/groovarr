"""Typed exceptions for the Plex integration."""


class PlexError(Exception):
    """Base class for all Plex-integration errors."""


class PlexAuthError(PlexError):
    """The configured X-Plex-Token was rejected (HTTP 401)."""


class PlexConnectionError(PlexError):
    """Could not reach the configured Plex server at all."""


class PlexNotFoundError(PlexError):
    """A referenced section/playlist/item does not exist on the server."""
