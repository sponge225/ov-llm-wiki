"""Step 3: discover current-layer Wiki nodes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import ValidationError

from .config import WikiConfig
from .llm import WikiLLMRunner
from .prompts import (
    build_bottom_node_discovery_prompt,
    build_parent_node_discovery_prompt,
)
from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    SourceAssignmentItem,
    SourceAssignmentResponse,
    WikiBottomNodeDiscoveryResponse,
    WikiNode,
    WikiNodeDiscoveryItem,
    WikiParentNodeDiscoveryResponse,
)
from .uri import sanitize_node_id

logger = logging.getLogger(__name__)
MAX_VALIDATION_ATTEMPTS = 3
T = TypeVar("T")


@dataclass(frozen=True)
class BottomLayerDiscoveryResult:
    nodes: list[WikiNode]
    source_assignments: SourceAssignmentResponse


@dataclass(frozen=True)
class ParentLayerDiscoveryResult:
    nodes: list[WikiNode]
    source_assignments: SourceAssignmentResponse


class NodeDiscoveryRunner:
    def __init__(self, llm: WikiLLMRunner, config: WikiConfig):
        self.llm = llm
        self.config = config

    async def discover_bottom_layer(
        self,
        cards: list[DocumentCard],
        depth: int = 1,
    ) -> BottomLayerDiscoveryResult:
        prompt = build_bottom_node_discovery_prompt(
            cards,
            min_refs_per_node=self.config.limits.min_refs_per_node,
        )
        return await _complete_with_validation_retry(
            self.llm,
            step="bottom_node_discovery",
            prompt=prompt,
            schema=WikiBottomNodeDiscoveryResponse.model_json_schema(),
            validate=lambda result: self._parse_bottom_layer_result(
                result,
                depth,
            ),
        )

    def _parse_bottom_layer_result(
        self,
        result: dict,
        depth: int,
    ) -> BottomLayerDiscoveryResult:
        response = WikiBottomNodeDiscoveryResponse.model_validate(result)
        nodes = self._build_nodes(response.nodes, depth)
        assignments = [
            SourceAssignmentItem(
                node_id=node.node_id,
                source_ids=item.supporting_doc_ids,
                support_scope=node.scope,
            )
            for node, item in zip(nodes, response.nodes, strict=False)
        ]
        return BottomLayerDiscoveryResult(
            nodes=nodes,
            source_assignments=SourceAssignmentResponse(
                assignments=assignments,
                unassigned_source_ids=response.unassigned_doc_ids,
            ),
        )

    async def discover_parent_layer(
        self,
        child_nodes: list[GeneratedNodeContext],
        depth: int,
    ) -> ParentLayerDiscoveryResult:
        title_to_node_id = _child_title_to_node_id(child_nodes)
        prompt = build_parent_node_discovery_prompt(
            child_nodes,
            min_child_nodes_per_parent=self.config.limits.min_child_nodes_per_parent,
        )
        return await _complete_with_validation_retry(
            self.llm,
            step="parent_node_discovery",
            prompt=prompt,
            schema=WikiParentNodeDiscoveryResponse.model_json_schema(),
            validate=lambda result: self._parse_parent_layer_result(
                result,
                depth,
                title_to_node_id,
            ),
        )

    def _parse_parent_layer_result(
        self,
        result: dict,
        depth: int,
        title_to_node_id: dict[str, str],
    ) -> ParentLayerDiscoveryResult:
        response = WikiParentNodeDiscoveryResponse.model_validate(result)
        nodes = self._build_nodes(response.nodes, depth)
        assignments = [
            SourceAssignmentItem(
                node_id=node.node_id,
                source_ids=_map_child_titles(item.supporting_child_titles, title_to_node_id, node.title),
                support_scope=node.scope,
            )
            for node, item in zip(nodes, response.nodes, strict=False)
        ]
        unassigned_source_ids = _map_child_titles(
            response.unassigned_child_titles,
            title_to_node_id,
            "unassigned_child_titles",
        )
        return ParentLayerDiscoveryResult(
            nodes=nodes,
            source_assignments=SourceAssignmentResponse(
                assignments=assignments,
                unassigned_source_ids=unassigned_source_ids,
            ),
        )

    def _build_nodes(
        self,
        discovered_nodes: list[WikiNodeDiscoveryItem],
        depth: int,
    ) -> list[WikiNode]:
        used_ids: set[str] = set()
        nodes: list[WikiNode] = []
        for discovered in discovered_nodes:
            base_id = sanitize_node_id(discovered.title)
            node_id = base_id
            suffix = 2
            while node_id in used_ids:
                node_id = f"{base_id}_{suffix}"
                suffix += 1
            used_ids.add(node_id)
            nodes.append(
                WikiNode(
                    node_id=node_id,
                    title=discovered.title,
                    depth=depth,
                    scope=discovered.scope,
                )
            )
        return nodes


def _child_title_to_node_id(child_nodes: list[GeneratedNodeContext]) -> dict[str, str]:
    title_to_node_id: dict[str, str] = {}
    for context in child_nodes:
        title = context.node.title
        if title in title_to_node_id:
            raise ValueError(f"duplicate child node title for parent discovery: {title}")
        title_to_node_id[title] = context.node.node_id
    return title_to_node_id


def _map_child_titles(
    titles: list[str],
    title_to_node_id: dict[str, str],
    field_name: str,
) -> list[str]:
    unknown_titles = [title for title in titles if title not in title_to_node_id]
    if unknown_titles:
        raise RuntimeError(f"{field_name} references unknown child titles: {unknown_titles}")
    return list(dict.fromkeys(title_to_node_id[title] for title in titles))


async def _complete_with_validation_retry(
    llm: WikiLLMRunner,
    *,
    step: str,
    prompt: str,
    schema: dict,
    validate: Callable[[dict], T],
) -> T:
    last_error: Exception | None = None
    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        try:
            result = await llm.complete_json(
                step=step,
                prompt=prompt,
                schema=schema,
            )
            return validate(result)
        except (RuntimeError, ValidationError) as exc:
            last_error = exc
            if attempt == MAX_VALIDATION_ATTEMPTS:
                break
            logger.info(
                "[Wiki] Retrying %s after validation failure attempt=%d/%d",
                step,
                attempt,
                MAX_VALIDATION_ATTEMPTS,
            )
    assert last_error is not None
    raise last_error
