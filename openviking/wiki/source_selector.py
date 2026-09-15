"""Scope-guided selection of raw sections for large Wiki nodes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from openviking_cli.exceptions import ProcessingError

from .config import WikiGenerationLimits
from .schemas import ResourceDocument, SourceRef, SourceSection, WikiNode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelectedSourceDocument:
    source_id: str
    title: str
    sections: list[SourceSection]
    relevance_score: float


class NodeSourceSelector:
    def __init__(self, viking_fs: Any, ctx: Any, limits: WikiGenerationLimits):
        self.viking_fs = viking_fs
        self.ctx = ctx
        self.limits = limits

    async def select(
        self,
        node: WikiNode,
        source_refs: list[SourceRef],
        source_documents_by_id: dict[str, ResourceDocument],
    ) -> list[SelectedSourceDocument]:
        semaphore = asyncio.Semaphore(max(1, self.limits.node_source_retrieval_concurrency))

        async def select_one(source_ref: SourceRef) -> SelectedSourceDocument | None:
            source_document = source_documents_by_id.get(source_ref.doc_id)
            if source_document is None:
                raise RuntimeError(
                    f"node source ref has no loaded source document: {source_ref.doc_id}"
                )
            query = "\n".join([node.title, node.scope, *dict.fromkeys(source_ref.matched_topics)])
            try:
                async with semaphore:
                    result = await self.viking_fs.find(
                        query=query,
                        target_uri=source_ref.resource_uri,
                        level=[2],
                        limit=self.limits.node_source_retrieval_limit,
                        score_threshold=self.limits.node_source_score_threshold,
                        ctx=self.ctx,
                    )
            except Exception as exc:
                details = {
                    "stage": "node_source_retrieval",
                    "node_id": node.node_id,
                    "node_title": node.title,
                    "source_id": source_ref.doc_id,
                    "source_uri": source_ref.resource_uri,
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                }
                logger.exception(
                    "[Wiki] Node source retrieval failed: node_id=%s node_title=%r source_id=%s source_uri=%s",
                    node.node_id,
                    node.title,
                    source_ref.doc_id,
                    source_ref.resource_uri,
                )
                raise ProcessingError(
                    "Node source retrieval failed",
                    source=source_ref.resource_uri,
                    details=details,
                ) from exc

            section_order = {
                section.section_uri: index
                for index, section in enumerate(source_document.source_sections)
            }
            sections_by_uri = {
                section.section_uri: section for section in source_document.source_sections
            }
            threshold = self.limits.node_source_score_threshold
            matches_by_uri: dict[str, Any] = {}
            for match in result:
                if threshold is not None and match.score < threshold:
                    continue
                if match.uri not in sections_by_uri:
                    continue
                previous = matches_by_uri.get(match.uri)
                if previous is None or match.score > previous.score:
                    matches_by_uri[match.uri] = match

            matches = sorted(
                matches_by_uri.values(),
                key=lambda match: (-match.score, section_order[match.uri]),
            )[: self.limits.node_source_retrieval_limit]
            if not matches:
                return None
            return SelectedSourceDocument(
                source_id=source_ref.doc_id,
                title=source_ref.title,
                sections=[sections_by_uri[match.uri] for match in matches],
                relevance_score=max(match.score for match in matches),
            )

        selected = [
            item
            for item in await asyncio.gather(*(select_one(ref) for ref in source_refs))
            if item is not None
        ]
        if not selected:
            logger.error(
                "[Wiki] No source sections matched node scope: node_id=%s node_title=%r source_ids=%s",
                node.node_id,
                node.title,
                [ref.doc_id for ref in source_refs],
            )
            raise ProcessingError(
                "No source sections matched the Wiki node scope",
                details={
                    "stage": "node_source_retrieval",
                    "node_id": node.node_id,
                    "node_title": node.title,
                    "source_ids": [ref.doc_id for ref in source_refs],
                },
            )
        return sorted(selected, key=lambda item: (-item.relevance_score, item.source_id))
