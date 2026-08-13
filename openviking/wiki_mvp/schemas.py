"""Pydantic schemas used by the Wiki MVP pipeline."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NODE_ID_RE = re.compile(r"^[a-z0-9_]+$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResourceDocumentDraft(StrictModel):
    doc_id: str
    title: str
    source_type: str = "resource_document"
    summary: str = ""
    abstract: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    document_dir_uri_hint: str = ""
    relative_uri: str = ""

    @field_validator("doc_id", "title", "source_type")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)


class WikiResourceInput(StrictModel):
    doc_id: str
    resource_uri: str
    title: str
    source_type: str = "resource_document"
    summary: str = ""
    abstract: str = ""
    document_dir_uri: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("doc_id", "resource_uri", "title", "source_type")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("resource_uri")
    @classmethod
    def _resource_uri(cls, value: str) -> str:
        return _require_resource_uri(value)

    @field_validator("document_dir_uri")
    @classmethod
    def _document_dir_uri(cls, value: str) -> str:
        if not value:
            return value
        return _require_resource_uri(value)


class CardInputPayload(StrictModel):
    content: str
    missing_summary_uris: list[str] = Field(default_factory=list)


class EvidenceAnchor(StrictModel):
    section_title: str
    section_uri: str
    summary: str = ""

    @field_validator("section_title", "section_uri")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)


class ResourceDocument(StrictModel):
    doc_id: str
    resource_uri: str
    title: str
    source_type: str = "academic_paper_full_text"
    summary: str = ""
    abstract: str = ""
    content_or_structure: str = ""
    chunk_summaries: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("doc_id", "resource_uri", "title", "source_type")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("resource_uri")
    @classmethod
    def _resource_uri(cls, value: str) -> str:
        return _require_resource_uri(value)


class DocumentCard(StrictModel):
    doc_id: str
    resource_uri: str
    title: str
    source_type: str
    summary: str
    main_points: list[str]
    important_terms: list[str] = Field(default_factory=list)
    limitations_or_notes: list[str] = Field(default_factory=list)
    candidate_topics: list[str]
    evidence_anchors: list[EvidenceAnchor]
    markdown: str = ""

    @field_validator("doc_id", "resource_uri", "title", "source_type", "summary")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("resource_uri")
    @classmethod
    def _resource_uri(cls, value: str) -> str:
        return _require_resource_uri(value)

    @field_validator("main_points", "candidate_topics", "evidence_anchors")
    @classmethod
    def _not_empty_list(cls, value: list[Any]) -> list[Any]:
        return _require_list(value)


class ResourceSpaceProfile(StrictModel):
    space_title: str
    space_summary: str
    main_topics: list[str]
    important_terms: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("space_title", "space_summary")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("main_topics")
    @classmethod
    def _not_empty_list(cls, value: list[str]) -> list[str]:
        return _require_list(value)


class WikiNode(StrictModel):
    node_id: str
    title: str
    status: Literal["candidate", "active", "merged", "rejected"]
    depth: int = Field(ge=1)
    scope: str
    seed_doc_ids: list[str]
    supporting_doc_count: int = Field(ge=0)
    promotion_decision: Literal["promote_to_node", "merge_into_existing", "reject"]
    promotion_reasons: list[str]
    parent_node_id: str | None = None
    inclusion_criteria: list[str] = Field(default_factory=list)
    exclusion_criteria: list[str] = Field(default_factory=list)
    child_node_ids: list[str] = Field(default_factory=list)
    merged_into_node_id: str | None = None

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        value = _require_text(value)
        if not NODE_ID_RE.match(value):
            raise ValueError("node_id must contain only lowercase letters, digits, and underscores")
        return value

    @field_validator("title", "scope")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("seed_doc_ids", "promotion_reasons")
    @classmethod
    def _not_empty_list(cls, value: list[str]) -> list[str]:
        return _require_list(value)

    @model_validator(mode="after")
    def _validate_status_fields(self) -> "WikiNode":
        if self.status == "active":
            _require_list(self.inclusion_criteria)
            _require_list(self.exclusion_criteria)
        if self.status == "merged" and not self.merged_into_node_id:
            raise ValueError("merged node must include merged_into_node_id")
        return self


class SourceRef(StrictModel):
    ref_id: str
    ref_type: Literal["document", "wiki_node"] = "document"
    doc_id: str
    resource_uri: str
    card_uri: str
    title: str
    support_scope: str
    matched_topics: list[str] = Field(default_factory=list)

    @field_validator("ref_id", "doc_id", "resource_uri", "card_uri", "title", "support_scope")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("resource_uri")
    @classmethod
    def _resource_uri(cls, value: str) -> str:
        return _require_source_uri(value)


class SourceAssignment(StrictModel):
    node_id: str
    doc_id: str
    resource_uri: str
    card_uri: str
    support_scope: str

    @field_validator("node_id")
    @classmethod
    def _node_id(cls, value: str) -> str:
        value = _require_text(value)
        if not NODE_ID_RE.match(value):
            raise ValueError("node_id must contain only lowercase letters, digits, and underscores")
        return value

    @field_validator("doc_id", "resource_uri", "card_uri", "support_scope")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("resource_uri")
    @classmethod
    def _resource_uri(cls, value: str) -> str:
        return _require_resource_uri(value)


class SourceAssignmentResult(StrictModel):
    assignments: list[SourceAssignment]
    source_refs_by_node: dict[str, list[SourceRef]]
    child_node_ids_by_node: dict[str, list[str]] = Field(default_factory=dict)
    unassigned_doc_ids: list[str] = Field(default_factory=list)


class NodeDocument(StrictModel):
    document_id: str
    title: str = "High-Level Knowledge"
    content: str

    @field_validator("document_id", "title", "content")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)


class EvidenceRef(StrictModel):
    doc_id: str
    resource_uri: str
    section_uri: str
    section_title: str
    support_type: Literal["supports", "weak_support", "mixed", "contradicts", "background"]
    evidence_quote_or_summary: str

    @field_validator(
        "doc_id",
        "resource_uri",
        "section_uri",
        "section_title",
        "support_type",
        "evidence_quote_or_summary",
    )
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("resource_uri")
    @classmethod
    def _resource_uri(cls, value: str) -> str:
        return _require_source_uri(value)


class EvidenceClaim(StrictModel):
    claim_id: str
    claim: str
    claim_type: Literal[
        "research_direction",
        "method_comparison",
        "empirical_finding",
        "limitation",
        "tradeoff",
        "dataset_trend",
        "open_question",
    ]
    evidence_refs: list[EvidenceRef]
    confidence: float = Field(ge=0, le=1)
    notes: str = ""

    @field_validator("claim_id", "claim", "claim_type")
    @classmethod
    def _not_empty(cls, value: str) -> str:
        return _require_text(value)

    @field_validator("evidence_refs")
    @classmethod
    def _not_empty_list(cls, value: list[EvidenceRef]) -> list[EvidenceRef]:
        return _require_list(value)


class GeneratedNodeContext(StrictModel):
    node: WikiNode
    node_md: str
    documents: list[NodeDocument]
    evidence: list[EvidenceClaim]
    source_refs: list[SourceRef]


class NodeManifest(StrictModel):
    node_id: str
    title: str
    node_uri: str
    node_md: str
    documents_dir: str
    document_uris: list[str]
    evidence_jsonl: str
    sources_dir: str
    num_source_refs: int
    num_node_documents: int
    num_claims: int


class WikiManifest(StrictModel):
    dataset: str
    split: str
    pipeline_version: str
    resource_root_uri: str
    wiki_root: str
    profile_uri: str
    cards_dir: str
    node_uris: list[str]
    created_at: str


class PipelineArtifacts(StrictModel):
    cards: list[DocumentCard] = Field(default_factory=list)
    profile: ResourceSpaceProfile | None = None
    nodes: list[WikiNode] = Field(default_factory=list)
    source_assignments: list[SourceAssignment] = Field(default_factory=list)
    node_contexts: list[GeneratedNodeContext] = Field(default_factory=list)
    manifest: WikiManifest | None = None


def _require_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    return value.strip()


def _require_list(value: list[Any]) -> list[Any]:
    if not value:
        raise ValueError("list must not be empty")
    return value


def _require_resource_uri(value: str) -> str:
    if not value.startswith("viking://resources/"):
        raise ValueError("resource_uri must start with viking://resources/")
    return value


def _require_source_uri(value: str) -> str:
    if not (value.startswith("viking://resources/") or value.startswith("viking://wiki/")):
        raise ValueError("source uri must start with viking://resources/ or viking://wiki/")
    return value
