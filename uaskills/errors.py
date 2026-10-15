"""Error types used across the catalog tools."""

from __future__ import annotations


class LibraryError(Exception):
    """Expected operational failure with a message safe to show the user."""


class GitHubError(LibraryError):
    def __init__(self, status: int, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class UnsafePath(LibraryError):
    """A path would escape the directory it belongs in."""


class FileTooLarge(LibraryError):
    """A file is above the configured size limit and was skipped."""


class UnsafeArchive(LibraryError):
    """An archive failed a safety check and was not extracted."""


class ConfigError(LibraryError):
    """Local configuration is missing or refuses to load."""
