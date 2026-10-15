"""Normalized skill records and manifest projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillRecord:
    id: str
    name: str | None
    description: str | None
    source_url: str | None
    source_host: str
    repo_owner: str | None
    repo_name: str | None
    repo_url: str | None
    commit_sha: str | None
    version: str | None
    local_path: str | None
    origin_path: str | None
    skill_relpath: str
    original_format: str
    category: str
    license_identifier: str | None
    license_evidence: str | None
    license_status: str
    content_checksum: str
    tree_checksum: str
    import_timestamp: str
    update_timestamp: str
    verification_status: str = "unverified"
    security_scan_status: str = "not_scanned"
    security_findings: list[dict[str, Any]] = field(default_factory=list)
    compatibility_status: str = "unknown"
    compatibility: str | None = None
    quality_assessment: str = "unassessed"
    deprecation_status: str = "unknown"
    lifecycle_status: str = "imported"
    resource_kind: str = "skill"
    is_synthetic: bool = False
    duplicate_of: str | None = None
    canonical: bool = True
    body_excerpt: str = ""
    capabilities: list[str] | None = None
    supported_runtimes: list[str] | None = None
    required_tools: list[str] | None = None
    dependencies: list[str] | None = None
    tags: list[str] | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "name_source": self.extensions.get("name_source"),
            "description": self.description,
            "description_source": self.extensions.get("description_source"),
            "source_url": self.source_url,
            "repository": {
                "host": self.source_host,
                "owner": self.repo_owner,
                "name": self.repo_name,
                "url": self.repo_url,
            },
            "commit_sha": self.commit_sha,
            "version": self.version,
            "local_path": self.local_path,
            "origin_path": self.origin_path,
            "skill_relpath": self.skill_relpath,
            "original_format": self.original_format,
            "normalized_format": "uaskills.manifest.v1",
            "category": self.category,
            "tags": self.tags,
            "capabilities": self.capabilities,
            "supported_runtimes": self.supported_runtimes,
            "required_tools": self.required_tools,
            "dependencies": self.dependencies,
            "license": {
                "identifier": self.license_identifier,
                "evidence": self.license_evidence,
                "status": self.license_status,
            },
            "content_checksum": self.content_checksum,
            "tree_checksum": self.tree_checksum,
            "import_timestamp": self.import_timestamp,
            "update_timestamp": self.update_timestamp,
            "verification_status": self.verification_status,
            "security_scan_status": self.security_scan_status,
            "security_findings": self.security_findings,
            "compatibility_status": self.compatibility_status,
            "compatibility": self.compatibility,
            "quality_assessment": self.quality_assessment,
            "deprecation_status": self.deprecation_status,
            "lifecycle_status": self.lifecycle_status,
            "resource_kind": self.resource_kind,
            "duplicate_of": self.duplicate_of,
            "is_synthetic": self.is_synthetic,
            "extensions": self.extensions,
        }


@dataclass
class ResourceRecord:
    id: str
    kind: str
    source_url: str | None
    repo_owner: str | None
    repo_name: str | None
    local_path: str | None
    reason: str
    evidence: str
    discovered_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
