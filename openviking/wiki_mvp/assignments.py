"""Step 4: assign sources to active Wiki nodes."""

from __future__ import annotations

import logging

from .config import WikiMVPConfig
from .llm import WikiLLMRunner
from .prompts import build_source_assignment_prompt
from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    SourceAssignment,
    SourceAssignmentResult,
    SourceRef,
    WikiNode,
)
from .uri import card_md_uri, node_md_uri, node_root_uri, sanitize_node_id


logger = logging.getLogger(__name__)


class SourceAssignmentRunner:
    def __init__(self, llm: WikiLLMRunner, config: WikiMVPConfig):
        self.llm = llm
        self.config = config

    async def assign_bottom_layer(
        self,
        active_nodes: list[WikiNode],
        cards: list[DocumentCard],
    ) -> SourceAssignmentResult:
        return await self._assign(active_nodes=active_nodes, cards=cards, child_contexts=None)

    async def assign_parent_layer(
        self,
        active_nodes: list[WikiNode],
        child_contexts: list[GeneratedNodeContext],
    ) -> SourceAssignmentResult:
        return await self._assign(active_nodes=active_nodes, cards=None, child_contexts=child_contexts)

    async def _assign(
        self,
        active_nodes: list[WikiNode],
        cards: list[DocumentCard] | None,
        child_contexts: list[GeneratedNodeContext] | None,
    ) -> SourceAssignmentResult:
        result = await self.llm.complete_json(
            step="source_assignment",
            prompt=build_source_assignment_prompt(
                active_nodes,
                cards=cards,
                child_nodes=child_contexts,
                min_child_nodes_per_parent=self.config.limits.min_child_nodes_per_parent,
            ),
            schema={
                "type": "object",
                "properties": {
                    "assignments": {"type": "array"},
                    "unassigned_doc_ids": {"type": "array"},
                },
                "required": ["assignments"],
            },
        )
        if child_contexts:
            child_node_ids_by_node = self._build_child_node_ids_by_node(
                result["assignments"],
                child_contexts,
            )
            return SourceAssignmentResult(
                assignments=[],
                source_refs_by_node=self._build_child_refs_by_node(child_node_ids_by_node, child_contexts),
                child_node_ids_by_node=child_node_ids_by_node,
                unassigned_doc_ids=result.get("unassigned_doc_ids", []),
            )
        assignments = [
            SourceAssignment.model_validate(item)
            for item in self._expand_assignments(result["assignments"], cards or [], child_contexts or [])
        ]
        source_refs_by_node = self._build_refs_by_node(assignments, cards or [], child_contexts or [])
        child_node_ids_by_node = self._build_child_node_ids_by_node(
            result["assignments"],
            child_contexts or [],
        )
        return SourceAssignmentResult(
            assignments=assignments,
            source_refs_by_node=source_refs_by_node,
            child_node_ids_by_node=child_node_ids_by_node,
            unassigned_doc_ids=result.get("unassigned_doc_ids", []),
        )

    def _build_child_refs_by_node(
        self,
        child_node_ids_by_node: dict[str, list[str]],
        child_contexts: list[GeneratedNodeContext],
    ) -> dict[str, list[SourceRef]]:
        child_contexts_by_id = {context.node.node_id: context for context in child_contexts}
        refs_by_node: dict[str, list[SourceRef]] = {}
        for node_id, child_node_ids in child_node_ids_by_node.items():
            refs: list[SourceRef] = []
            for child_node_id in child_node_ids:
                child_context = child_contexts_by_id.get(child_node_id)
                if not child_context:
                    continue
                refs.append(
                    SourceRef(
                        ref_id=child_node_id,
                        ref_type="wiki_node",
                        doc_id=child_node_id,
                        resource_uri=node_root_uri(self.config, child_node_id),
                        card_uri=node_md_uri(self.config, child_node_id),
                        title=child_context.node.title,
                        support_scope=child_context.node.scope,
                        matched_topics=child_context.node.inclusion_criteria,
                    )
                )
            if refs:
                refs_by_node[node_id] = refs
        return refs_by_node

    def _expand_assignments(
        self,
        raw_assignments: list[object],
        cards: list[DocumentCard],
        child_contexts: list[GeneratedNodeContext],
    ) -> list[dict]:
        cards_by_id = {card.doc_id: card for card in cards}
        child_contexts_by_id = {context.node.node_id: context for context in child_contexts}
        inherited_refs = {
            ref.doc_id: ref for context in child_contexts for ref in context.source_refs
        }
        expanded: list[dict] = []
        for item in raw_assignments:
            if not isinstance(item, dict):
                raise TypeError(f"assignment item must be object, got {type(item).__name__}")
            node_id = item.get("node_id")
            if node_id:
                item = {**item, "node_id": sanitize_node_id(str(node_id))}
            known_source_ids = set(child_contexts_by_id) if child_contexts else set(cards_by_id)
            source_ids = _extract_source_ids(
                item,
                known_source_ids,
            )
            if source_ids is None:
                expanded.append(dict(item))
                continue
            if not source_ids:
                logger.warning(
                    "[WikiMVP] Dropping assignment for node %s because it references no known lower-layer ids",
                    item.get("node_id"),
                )
                continue
            node_id = item["node_id"]
            for source_id in source_ids:
                card = cards_by_id.get(source_id)
                child_context = child_contexts_by_id.get(source_id)
                if card:
                    expanded.append(
                        {
                            "node_id": node_id,
                            "doc_id": card.doc_id,
                            "resource_uri": card.resource_uri,
                            "card_uri": card_md_uri(self.config, card.doc_id),
                            "support_scope": item.get("support_scope") or item.get("reason") or card.summary,
                        }
                    )
                elif child_context:
                    for inherited_ref in child_context.source_refs:
                        expanded.append(
                            {
                                "node_id": node_id,
                                "doc_id": inherited_ref.doc_id,
                                "resource_uri": inherited_ref.resource_uri,
                                "card_uri": inherited_ref.card_uri,
                                "support_scope": item.get("support_scope") or inherited_ref.support_scope,
                            }
                        )
                else:
                    raise RuntimeError(f"assignment references unknown source id: {source_id}")
        return expanded

    def _build_refs_by_node(
        self,
        assignments: list[SourceAssignment],
        cards: list[DocumentCard],
        child_contexts: list[GeneratedNodeContext],
    ) -> dict[str, list[SourceRef]]:
        cards_by_id = {card.doc_id: card for card in cards}
        inherited_refs = {
            ref.doc_id: ref for context in child_contexts for ref in context.source_refs
        }
        refs_by_node: dict[str, list[SourceRef]] = {}
        for assignment in assignments:
            existing_ref = inherited_refs.get(assignment.doc_id)
            card = cards_by_id.get(assignment.doc_id)
            if existing_ref:
                ref = existing_ref.model_copy(update={"support_scope": assignment.support_scope})
            elif card:
                ref = SourceRef(
                    ref_id=assignment.doc_id,
                    doc_id=assignment.doc_id,
                    resource_uri=assignment.resource_uri,
                    card_uri=assignment.card_uri or card_md_uri(self.config, assignment.doc_id),
                    title=card.title,
                    support_scope=assignment.support_scope,
                    matched_topics=card.candidate_topics,
                )
            else:
                raise RuntimeError(f"assignment references unknown doc_id: {assignment.doc_id}")
            refs_by_node.setdefault(assignment.node_id, []).append(ref)
        return refs_by_node

    def _build_child_node_ids_by_node(
        self,
        raw_assignments: list[object],
        child_contexts: list[GeneratedNodeContext],
    ) -> dict[str, list[str]]:
        child_node_ids = {context.node.node_id for context in child_contexts}
        child_node_ids_by_node: dict[str, list[str]] = {}
        for item in raw_assignments:
            if not isinstance(item, dict) or "node_id" not in item:
                continue
            source_ids = _extract_source_ids(item, child_node_ids)
            if not source_ids:
                continue
            node_id = sanitize_node_id(str(item["node_id"]))
            for source_id in source_ids:
                if source_id in child_node_ids:
                    child_node_ids_by_node.setdefault(node_id, [])
                    if source_id not in child_node_ids_by_node[node_id]:
                        child_node_ids_by_node[node_id].append(source_id)
        return child_node_ids_by_node

def _extract_source_ids(item: dict, known_doc_ids: set[str]) -> list[str] | None:
    for key in ("child_node_id", "source_node_id", "source_id", "doc_id"):
        value = _coerce_source_id(item.get(key))
        if value:
            return [value] if value in known_doc_ids else []
    for key in (
        "child_node_ids",
        "source_node_ids",
        "node_ids",
        "source_ids",
        "doc_ids",
        "source_refs",
        "assigned_source_ids",
        "child_source_assignments",
    ):
        value = item.get(key)
        if isinstance(value, list) and value:
            source_ids = [_coerce_source_id(entry) for entry in value]
            source_ids = [source_id for source_id in source_ids if source_id]
            if not source_ids:
                continue
            known_source_ids = [source_id for source_id in source_ids if source_id in known_doc_ids]
            return known_source_ids
    for value in item.values():
        if not isinstance(value, list) or not value:
            continue
        values = [_coerce_source_id(entry) for entry in value]
        values = [entry for entry in values if entry]
        if values and all(entry in known_doc_ids for entry in values):
            return values
    return None


def _coerce_source_id(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("child_node_id", "ref_id", "doc_id", "source_id", "node_id", "id"):
            raw = value.get(key)
            if raw:
                return str(raw)
    return ""
