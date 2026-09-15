"""Prompt builders for Wiki generation."""

from __future__ import annotations

import json

from openviking.prompts.manager import PromptManager

from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    NodeDocument,
    ResourceDocument,
    WikiNode,
)

_PROMPT_MANAGER = PromptManager()


def build_document_card_prompt(doc: ResourceDocument) -> str:
    metadata = {
        key: value
        for key, value in (doc.metadata or {}).items()
        if key in {"card_input_mode", "missing_summary_uris"}
    }
    payload = {
        "content_or_structure": doc.content_or_structure,
        "metadata": metadata,
    }
    return _render_wiki_prompt("wiki.document_card", payload)


def build_node_discovery_prompt(
    cards: list[DocumentCard],
    min_sources_per_node: int,
    source_ids: list[str] | None = None,
) -> str:
    if source_ids is not None and len(source_ids) != len(cards):
        raise ValueError("source_ids must have the same length as cards")
    inputs = {
        "source_unit_count": len(cards),
        "min_sources_per_node": min_sources_per_node,
        "source_records": [
            _source_card_payload(
                card,
                source_id=source_ids[index] if source_ids is not None else card.doc_id,
            )
            for index, card in enumerate(cards)
        ],
    }
    return _render_wiki_prompt(
        "wiki.node_discovery",
        inputs,
        min_sources_per_node=min_sources_per_node,
    )


def build_node_card_prompt(node: WikiNode, document: NodeDocument) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "document": document.model_dump(include={"title", "content"}, mode="json"),
    }
    return _render_wiki_prompt("wiki.node_card", inputs)


def build_node_documents_prompt(
    node: WikiNode,
    source_documents: list[dict],
    *,
    max_document_tokens: int = 16000,
) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "source_documents": [
            _source_document_payload(source_document, index=index)
            for index, source_document in enumerate(source_documents, start=1)
        ],
    }
    return _render_wiki_prompt(
        "wiki.node_documents", inputs, max_document_tokens=max_document_tokens
    )


def build_node_document_outline_prompt(node: WikiNode, source_cards: list[DocumentCard]) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "source_cards": [
            card.model_dump(
                include={"title", "summary", "main_points", "candidate_topics"},
                mode="json",
            )
            for card in source_cards
        ],
    }
    return _render_wiki_prompt("wiki.node_document_outline", inputs)


def build_node_document_refine_prompt(
    node: WikiNode,
    current_markdown: str,
    source_documents: list[dict],
    *,
    initial: bool,
    max_document_tokens: int = 16000,
) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "current_markdown": current_markdown,
        "new_source_documents": [
            _source_document_payload(source_document, index=index)
            for index, source_document in enumerate(source_documents, start=1)
        ],
    }
    return _render_wiki_prompt(
        "wiki.node_document_refine",
        inputs,
        phase="initial" if initial else "refine",
        max_document_tokens=max_document_tokens,
    )


def build_next_layer_decision_prompt(
    child_nodes: list[GeneratedNodeContext],
    min_child_nodes_per_parent: int = 3,
) -> str:
    inputs = {
        "child_nodes": [_child_node_payload(context) for context in child_nodes],
    }
    return _render_wiki_prompt(
        "wiki.next_layer_decision",
        inputs,
        min_child_nodes_per_parent=min_child_nodes_per_parent,
    )


def _child_node_payload(context: GeneratedNodeContext) -> dict:
    return {
        "node": context.node.model_dump(include={"title", "scope"}, mode="json"),
        "card": context.card.model_dump(
            include={"summary", "main_points", "important_terms", "candidate_topics"},
            mode="json",
        ),
        "document": context.document.model_dump(include={"title", "content"}, mode="json"),
        "source_count": len(context.source_refs),
    }


def _source_document_payload(
    source_document: dict,
    *,
    index: int,
) -> dict:
    sections = [
        {"content": str(section.get("content") or "")}
        for section in source_document.get("sections", [])
        if str(section.get("content") or "")
    ]
    return {
        "source_id": f"S{index:04d}",
        "title": str(source_document.get("title") or ""),
        "sections": sections,
    }


def _source_card_payload(card: DocumentCard, *, source_id: str) -> dict:
    return {
        "source_id": source_id,
        "title": card.title,
        "summary": card.summary,
        "candidate_topics": card.candidate_topics,
    }


def _render_wiki_prompt(prompt_id: str, payload: object, **extra_vars: object) -> str:
    variables = {
        "input_json": json.dumps(payload, ensure_ascii=False, indent=2),
        **extra_vars,
    }
    return _PROMPT_MANAGER.render(prompt_id, variables)
