"""Prompt builders for Wiki MVP generation."""

from __future__ import annotations

import json

from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    ResourceDocument,
    ResourceSpaceProfile,
    SourceRef,
    WikiNode,
)

_BOUNDARY = (
    "Do not use question, gold answer, gold_related_work, or target_paper.hierarchy. "
    "Only use the provided resources and already generated Wiki assets."
)


def build_document_card_prompt(doc: ResourceDocument) -> str:
    payload = doc.model_dump(mode="json")
    return f"""Generate one Wiki Document Card for the given resource document.

{_BOUNDARY}

The card must summarize only this document. It must include candidate_topics and evidence_anchors.

Input:
{_json(payload)}
"""


def build_profile_prompt(cards: list[DocumentCard]) -> str:
    payload = [
        card.model_dump(
            include={"doc_id", "title", "summary", "main_points", "important_terms", "candidate_topics"},
            mode="json",
        )
        for card in cards
    ]
    return f"""Generate a short resource space profile from the provided Document Cards.

{_BOUNDARY}

Do not generate wiki nodes. Do not include the full resource list.

Input:
{_json(payload)}
"""


def build_node_discovery_prompt(
    profile: ResourceSpaceProfile,
    cards: list[DocumentCard] | None = None,
    child_nodes: list[GeneratedNodeContext] | None = None,
    depth: int = 1,
    min_child_nodes_per_parent: int = 3,
) -> str:
    if child_nodes:
        inputs = {
            "profile": profile.model_dump(mode="json"),
            "depth": depth,
            "child_nodes": [_child_node_payload(context) for context in child_nodes],
        }
    else:
        inputs = {
            "profile": profile.model_dump(mode="json"),
            "depth": depth,
            "cards": [
                card.model_dump(
                    include={
                        "doc_id",
                        "title",
                        "summary",
                        "main_points",
                        "important_terms",
                        "candidate_topics",
                    },
                    mode="json",
                )
                for card in cards or []
            ],
        }
    return f"""Generate current-layer wiki candidate nodes and decide which ones become active nodes.

{_BOUNDARY}

This step handles only the current layer. Do not build the full hierarchy in one call.
Active nodes must have enough support, clear boundary, and synthesis value.
For parent layers, candidate child nodes may come from any lower depth. Create a parent node only when at least {min_child_nodes_per_parent} clearly related child nodes can be grouped under it, and at least one child node is from depth-1. Do not create broader abstractions just because a more general label is possible.

Input:
{_json(inputs)}
"""


def build_source_assignment_prompt(
    active_nodes: list[WikiNode],
    cards: list[DocumentCard] | None = None,
    child_nodes: list[GeneratedNodeContext] | None = None,
    min_child_nodes_per_parent: int = 3,
) -> str:
    inputs = {
        "nodes": [node.model_dump(mode="json") for node in active_nodes],
        "cards": [
            card.model_dump(
                include={"doc_id", "resource_uri", "title", "summary", "candidate_topics"},
                mode="json",
            )
            for card in cards or []
        ],
        "child_nodes": [_child_node_payload(context) for context in child_nodes or []],
    }
    return f"""Assign lower-level sources to each current-layer active node.

{_BOUNDARY}

Bottom-layer nodes use source documents. Parent nodes use child nodes and inherit their source refs.
For parent nodes, assign sources through clearly related child nodes. Child nodes may mix lower depths, but each parent node must cover at least {min_child_nodes_per_parent} child nodes and include at least one child node from the previous depth.

Input:
{_json(inputs)}
"""


def build_node_md_prompt(node: WikiNode, source_refs: list[SourceRef]) -> str:
    inputs = {
        "node": node.model_dump(mode="json"),
        "source_refs": [ref.model_dump(mode="json") for ref in source_refs],
    }
    return f"""Generate node.md for this Wiki node.

{_BOUNDARY}

node.md is a directory explanation. It is not the synthesized knowledge body.

Input:
{_json(inputs)}
"""


def build_node_documents_prompt(
    node: WikiNode,
    source_refs: list[SourceRef],
    cards: list[DocumentCard] | None = None,
    child_nodes: list[GeneratedNodeContext] | None = None,
) -> str:
    inputs = {
        "node": node.model_dump(mode="json"),
        "source_refs": [ref.model_dump(mode="json") for ref in source_refs],
        "cards": [
            card.model_dump(
                include={
                    "doc_id",
                    "summary",
                    "main_points",
                    "important_terms",
                    "limitations_or_notes",
                },
                mode="json",
            )
            for card in cards or []
        ],
        "child_nodes": [_child_node_payload(context) for context in child_nodes or []],
    }
    return f"""Generate Wiki node documents for this node.

{_BOUNDARY}

The output must synthesize multiple source documents or child nodes. Multiple documents are only length splits.

Input:
{_json(inputs)}
"""


def build_evidence_prompt(
    node: WikiNode,
    node_documents: list[dict],
    source_refs: list[SourceRef],
    cards: list[DocumentCard],
) -> str:
    inputs = {
        "node": node.model_dump(mode="json"),
        "node_documents": node_documents,
        "source_refs": [ref.model_dump(mode="json") for ref in source_refs],
        "cards": [
            card.model_dump(include={"doc_id", "evidence_anchors"}, mode="json") for card in cards
        ],
    }
    return f"""Extract key claims from node documents and bind each claim to section/chunk-level evidence.

{_BOUNDARY}

For bottom-layer nodes, evidence must come from original resources. For parent nodes whose source_refs have ref_type="wiki_node", evidence must come from those child Wiki nodes, not from original documents. Do not use Document Cards as final evidence.

Input:
{_json(inputs)}
"""


def build_next_layer_decision_prompt(
    profile: ResourceSpaceProfile,
    child_nodes: list[GeneratedNodeContext],
    min_child_nodes_per_parent: int = 3,
) -> str:
    inputs = {
        "profile": profile.model_dump(mode="json"),
        "child_nodes": [_child_node_payload(context) for context in child_nodes],
    }
    return f"""Decide whether the completed current layer should be aggregated upward.

{_BOUNDARY}

Return continue_upward=true only if multiple active nodes share a stable higher-level theme and the parent node adds organization value.
Do not continue upward for a generic overview label. Continue only if at least {min_child_nodes_per_parent} clearly related child nodes can form a useful parent node.

Input:
{_json(inputs)}
"""


def _child_node_payload(context: GeneratedNodeContext) -> dict:
    return {
        "node": context.node.model_dump(mode="json"),
        "node_md": context.node_md,
        "documents": [document.model_dump(mode="json") for document in context.documents],
        "evidence": [claim.model_dump(mode="json") for claim in context.evidence],
        "source_refs": [ref.model_dump(mode="json") for ref in context.source_refs],
    }


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
