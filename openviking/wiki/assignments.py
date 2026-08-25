"""Build source refs for active Wiki nodes from discovery assignments."""

from __future__ import annotations

from .config import WikiConfig
from .schemas import (
    DocumentCard,
    SourceAssignmentItem,
    SourceRef,
)
from .uri import card_md_uri_for_card


class SourceRefBuilder:
    def __init__(self, config: WikiConfig):
        self.config = config

    def build_refs_by_node(
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
                        ref_type=_ref_type_for_card(card),
                        doc_id=card.doc_id,
                        resource_uri=card.resource_uri,
                        card_uri=card_md_uri_for_card(self.config, card),
                        title=card.title,
                        support_scope=item.support_scope,
                        matched_topics=card.candidate_topics,
                    )
                )
        return refs_by_node


def _ref_type_for_card(card: DocumentCard) -> str:
    if card.resource_uri.startswith("viking://wiki/"):
        return "wiki_node"
    return "document"
