"""Prompt builders for Wiki generation."""

from __future__ import annotations

import json

from openviking.prompts.manager import PromptManager

from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    ResourceDocument,
    ResourceSpaceProfile,
    WikiNode,
)

_BOUNDARY = (
    "Do not use question, gold answer, gold_related_work, or target_paper.hierarchy. "
    "Only use the provided resources and already generated Wiki assets."
)

_PROMPT_MANAGER = PromptManager()


def build_document_card_prompt(doc: ResourceDocument) -> str:
    metadata = {
        key: value
        for key, value in (doc.metadata or {}).items()
        if key in {"card_input_mode", "missing_summary_uris"}
    }
    payload = {
        "source_abstract": doc.abstract,
        "content_or_structure": doc.content_or_structure,
        "metadata": metadata,
    }
    return _render_wiki_prompt("wiki.document_card", payload)


def build_profile_prompt(cards: list[DocumentCard]) -> str:
    payload = [
        card.model_dump(
            include={"doc_id", "title", "summary", "main_points", "important_terms", "candidate_topics"},
            mode="json",
        )
        for card in cards
    ]
    return _render_wiki_prompt("wiki.resource_profile", payload)


def build_bottom_node_discovery_prompt(
    cards: list[DocumentCard],
    min_refs_per_node: int,
) -> str:
    inputs = {
        "source_unit_count": len(cards),
        "min_refs_per_node": min_refs_per_node,
        "topic_records": [
            card.model_dump(
                include={"doc_id", "candidate_topics", "summary"},
                mode="json",
            )
            for card in cards
        ],
    }
    return _render_wiki_prompt("wiki.bottom_node_discovery", inputs)


def build_parent_node_discovery_prompt(
    child_nodes: list[GeneratedNodeContext],
    min_child_nodes_per_parent: int,
) -> str:
    inputs = {
        "source_unit_count": len(child_nodes),
        "min_child_nodes_per_parent": min_child_nodes_per_parent,
        "child_node_records": [
            context.node.model_dump(
                include={"title", "scope"},
                mode="json",
            )
            for context in child_nodes
        ],
    }
    return _render_wiki_prompt("wiki.parent_node_discovery", inputs)


def build_node_md_prompt(node: WikiNode) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
    }
    return _render_wiki_prompt("wiki.node_md", inputs)


def build_node_documents_prompt(
    node: WikiNode,
    source_documents: list[dict],
) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "source_documents": source_documents,
    }
    return _render_wiki_prompt("wiki.node_documents", inputs)


def build_parent_node_documents_prompt(
    node: WikiNode,
    child_nodes: list[dict],
) -> str:
    inputs = {
        "node": node.model_dump(include={"title", "scope"}, mode="json"),
        "child_nodes": child_nodes,
    }
    return _render_wiki_prompt("wiki.parent_node_documents", inputs)


def build_next_layer_decision_prompt(
    profile: ResourceSpaceProfile,
    child_nodes: list[GeneratedNodeContext],
    min_child_nodes_per_parent: int = 3,
) -> str:
    inputs = {
        "profile": profile.model_dump(mode="json"),
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
        "node_md": context.node_md,
        "documents": [document.model_dump(mode="json") for document in context.documents],
        "source_refs": [ref.model_dump(mode="json") for ref in context.source_refs],
    }


def _render_wiki_prompt(prompt_id: str, payload: object, **extra_vars: object) -> str:
    variables = {
        "boundary": _BOUNDARY,
        "input_json": _json(payload),
        **extra_vars,
    }
    return _PROMPT_MANAGER.render(prompt_id, variables)


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
