"""Optional semantic search boundary.

Basic catalog search does not call an embedding model or an external API.
Enable a provider here only after the user configures it and agrees to send
the queries, and never by default.
"""

from __future__ import annotations

from typing import Any

from uaskills.errors import LibraryError


class SemanticIndex:
    def __init__(self, settings: dict[str, Any]):
        semantic = settings.get("semantic") or {}
        self.enabled = bool(semantic.get("enabled"))
        self.provider = str(semantic.get("provider") or "none")

    def available(self) -> bool:
        return False

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        raise LibraryError(
            "Semantic search is not active. The catalog search uses SQLite full-text "
            "and does not send skill content to an external service. To add embeddings later, "
            "configure a local model or an explicit provider in config/settings.yaml. "
            f"Current provider setting: {self.provider}; enabled: {self.enabled}."
        )
