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
) -> str:
    inputs = {
        "source_unit_count": len(cards),
        "min_sources_per_node": min_sources_per_node,
        "source_records": [_source_card_payload(card) for card in cards],
    }
    return _render_wiki_prompt("wiki.node_discovery", inputs)


def build_node_card_prompt(node: WikiNode, documents: list[NodeDocument]) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "documents": [
            document.model_dump(include={"title", "content"}, mode="json")
            for document in documents
        ],
    }
    return _render_wiki_prompt("wiki.node_card", inputs)


def build_node_documents_prompt(
    node: WikiNode,
    source_documents: list[dict],
) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "source_documents": source_documents,
    }
    return _render_wiki_prompt("wiki.node_documents", inputs)


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
        "node": context.node.model_dump(mode="json"),
        "card": context.card.model_dump(
            include={"summary", "main_points", "important_terms", "candidate_topics"},
            mode="json",
        ),
        "documents": [document.model_dump(mode="json") for document in context.documents],
        "source_refs": [ref.model_dump(mode="json") for ref in context.source_refs],
    }


def _source_card_payload(card: DocumentCard) -> dict:
    return {
        "source_id": card.doc_id,
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
