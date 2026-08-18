"""Build source refs for active Wiki nodes from discovery assignments."""

from __future__ import annotations

from .config import WikiConfig
from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    SourceAssignmentItem,
    SourceRef,
)
from .uri import card_md_uri, node_md_uri, node_root_uri


class SourceRefBuilder:
    def __init__(self, config: WikiConfig):
        self.config = config

    def build_child_refs_by_node(
        self,
        child_node_ids_by_node: dict[str, list[str]],
        child_contexts: list[GeneratedNodeContext],
    ) -> dict[str, list[SourceRef]]:
        child_contexts_by_id = {context.node.node_id: context for context in child_contexts}
        refs_by_node: dict[str, list[SourceRef]] = {}
        for node_id, child_node_ids in child_node_ids_by_node.items():
            unknown_child_node_ids = [
                child_node_id
                for child_node_id in child_node_ids
                if child_node_id not in child_contexts_by_id
            ]
            if unknown_child_node_ids:
                raise RuntimeError(
                    f"assignment for node {node_id} references unknown child node ids: "
                    f"{unknown_child_node_ids}"
                )
            refs: list[SourceRef] = []
            for child_node_id in child_node_ids:
                child_context = child_contexts_by_id[child_node_id]
                refs.append(
                    SourceRef(
                        ref_id=child_node_id,
                        ref_type="wiki_node",
                        doc_id=child_node_id,
                        resource_uri=node_root_uri(self.config, child_node_id),
                        card_uri=node_md_uri(self.config, child_node_id),
                        title=child_context.node.title,
                        support_scope=child_context.node.scope,
                        matched_topics=[child_context.node.title],
                    )
                )
            if refs:
                refs_by_node[node_id] = refs
        return refs_by_node

    def build_document_refs_by_node(
        self,
        assignments: list[SourceAssignmentItem],
        cards: list[DocumentCard],
    ) -> dict[str, list[SourceRef]]:
        cards_by_id = {card.doc_id: card for card in cards}
        refs_by_node: dict[str, list[SourceRef]] = {}
        for item in assignments:
            unknown_source_ids = [source_id for source_id in item.source_ids if source_id not in cards_by_id]
            if unknown_source_ids:
                raise RuntimeError(
                    f"assignment for node {item.node_id} references unknown doc_id values: "
                    f"{unknown_source_ids}"
                )
            known_source_ids = list(dict.fromkeys(item.source_ids))
            for source_id in known_source_ids:
                card = cards_by_id.get(source_id)
                if not card:
                    raise RuntimeError(f"assignment references unknown doc_id: {source_id}")
                refs_by_node.setdefault(item.node_id, []).append(
                    SourceRef(
                        ref_id=card.doc_id,
                        doc_id=card.doc_id,
                        resource_uri=card.resource_uri,
                        card_uri=card_md_uri(self.config, card.doc_id),
                        title=card.title,
                        support_scope=item.support_scope,
                        matched_topics=card.candidate_topics,
                    )
                )
        return refs_by_node
