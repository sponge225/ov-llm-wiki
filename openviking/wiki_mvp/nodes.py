"""Step 3: discover current-layer Wiki nodes."""

from __future__ import annotations

from .config import WikiMVPConfig
from .llm import WikiLLMRunner
from .prompts import build_node_discovery_prompt
from .schemas import DocumentCard, GeneratedNodeContext, ResourceSpaceProfile, WikiNode
from .uri import sanitize_node_id


class NodeDiscoveryRunner:
    def __init__(self, llm: WikiLLMRunner, config: WikiMVPConfig):
        self.llm = llm
        self.config = config

    async def discover_bottom_layer(
        self,
        profile: ResourceSpaceProfile,
        cards: list[DocumentCard],
        depth: int = 1,
    ) -> list[WikiNode]:
        return await self._discover(profile=profile, cards=cards, child_nodes=None, depth=depth)

    async def discover_parent_layer(
        self,
        profile: ResourceSpaceProfile,
        child_nodes: list[GeneratedNodeContext],
        depth: int,
    ) -> list[WikiNode]:
        return await self._discover(profile=profile, cards=None, child_nodes=child_nodes, depth=depth)

    async def _discover(
        self,
        profile: ResourceSpaceProfile,
        cards: list[DocumentCard] | None,
        child_nodes: list[GeneratedNodeContext] | None,
        depth: int,
    ) -> list[WikiNode]:
        result = await self.llm.complete_json(
            step="node_discovery",
            prompt=build_node_discovery_prompt(
                profile,
                cards=cards,
                child_nodes=child_nodes,
                depth=depth,
                min_child_nodes_per_parent=self.config.limits.min_child_nodes_per_parent,
            ),
            schema={"type": "object", "properties": {"nodes": {"type": "array"}}, "required": ["nodes"]},
        )
        nodes = [
            self._normalize_node(self._coerce_node(item, cards, child_nodes, depth), depth)
            for item in result["nodes"]
        ]
        return self._cap_active_nodes(nodes, _source_unit_count(cards, child_nodes))

    def _normalize_node(self, node: WikiNode, depth: int) -> WikiNode:
        normalized_id = sanitize_node_id(node.node_id)
        return node.model_copy(update={"node_id": normalized_id, "depth": depth})

    def _cap_active_nodes(self, nodes: list[WikiNode], source_unit_count: int) -> list[WikiNode]:
        effective_limit = self._effective_active_node_limit(source_unit_count)
        kept_active = 0
        capped: list[WikiNode] = []
        for node in nodes:
            if node.status != "active":
                capped.append(node)
                continue
            kept_active += 1
            if kept_active <= effective_limit:
                capped.append(node)
                continue
            capped.append(
                node.model_copy(
                    update={
                        "status": "rejected",
                        "promotion_decision": "reject",
                        "promotion_reasons": [
                            f"active node limit {effective_limit} reached"
                        ],
                    }
                )
            )
        return capped

    def _effective_active_node_limit(self, source_unit_count: int) -> int:
        min_refs = max(1, self.config.limits.min_refs_per_node)
        support_limited = max(1, source_unit_count // min_refs)
        return max(1, min(self.config.limits.max_active_nodes, support_limited))

    def _coerce_node(
        self,
        item: object,
        cards: list[DocumentCard] | None,
        child_nodes: list[GeneratedNodeContext] | None,
        depth: int,
    ) -> WikiNode:
        source_doc_ids = _source_doc_ids(cards, child_nodes)
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            raise TypeError(f"node item must be object or string, got {type(item).__name__}")

        title = str(item.get("title") or item.get("node_id") or "wiki_node")
        payload = dict(item)
        payload["node_id"] = sanitize_node_id(str(payload.get("node_id") or title))
        payload.setdefault("title", title)
        payload.setdefault("status", "active")
        payload.setdefault("depth", depth)
        payload.setdefault("scope", title)
        payload.setdefault("seed_doc_ids", source_doc_ids)
        payload.setdefault("supporting_doc_count", len(source_doc_ids))
        payload.setdefault("promotion_decision", "promote_to_node")
        payload.setdefault("promotion_reasons", ["supported by current-layer sources"])
        payload.setdefault("parent_node_id", None)
        payload.setdefault("inclusion_criteria", [f"sources related to {title}"])
        payload.setdefault("exclusion_criteria", [f"sources not related to {title}"])
        if not payload.get("seed_doc_ids"):
            payload["seed_doc_ids"] = source_doc_ids
        if not payload.get("promotion_reasons"):
            payload["promotion_reasons"] = ["supported by current-layer sources"]
        if payload.get("status") == "active":
            if not payload.get("inclusion_criteria"):
                payload["inclusion_criteria"] = [f"sources related to {title}"]
            if not payload.get("exclusion_criteria"):
                payload["exclusion_criteria"] = [f"sources not related to {title}"]
        return WikiNode.model_validate(payload)


def _source_doc_ids(
    cards: list[DocumentCard] | None,
    child_nodes: list[GeneratedNodeContext] | None,
) -> list[str]:
    if cards:
        return [card.doc_id for card in cards]
    doc_ids: list[str] = []
    for context in child_nodes or []:
        for ref in context.source_refs:
            if ref.doc_id not in doc_ids:
                doc_ids.append(ref.doc_id)
    return doc_ids


def _source_unit_count(
    cards: list[DocumentCard] | None,
    child_nodes: list[GeneratedNodeContext] | None,
) -> int:
    if cards is not None:
        return len(cards)
    return len(child_nodes or [])
