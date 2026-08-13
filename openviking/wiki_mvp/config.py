"""Configuration for Wiki MVP generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WikiMVPGenerationLimits:
    max_active_nodes: int = 20
    max_depth: int = 2
    min_support_docs: int = 3
    max_doc_overlap_with_existing_node: float = 0.8
    min_child_nodes_per_parent: int = 3
    min_refs_per_node: int = 3
    min_card_coverage: float = 0.95
    max_words_per_node_document: int = 800
    max_concurrent_cards: int = 10
    max_concurrent_nodes: int = 4


@dataclass
class WikiMVPConfig:
    pipeline_version: str = "wiki_mvp_v2_doc_card"
    resource_root_uri: str = "viking://resources/"
    wiki_root_uri: str = "viking://wiki/"
    dataset: str = "oarel_related_work"
    split: str = "validation_00000"
    limits: WikiMVPGenerationLimits = field(default_factory=WikiMVPGenerationLimits)
    vlm_config: dict[str, Any] | None = None
    dry_run: bool = False

    def __post_init__(self) -> None:
        self.resource_root_uri = _ensure_trailing_slash(self.resource_root_uri)
        self.wiki_root_uri = _ensure_trailing_slash(self.wiki_root_uri)


def _ensure_trailing_slash(uri: str) -> str:
    return uri if uri.endswith("/") else f"{uri}/"
