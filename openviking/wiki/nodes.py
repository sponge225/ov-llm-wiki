"""Step 3: discover current-layer Wiki nodes."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import ValidationError

from .config import WikiConfig
from .llm import WikiLLMRunner
from .prompts import build_node_discovery_prompt
from .schemas import (
    DocumentCard,
    SourceAssignmentItem,
    SourceAssignmentResponse,
    WikiNode,
    WikiNodeDiscoveryItem,
    WikiSourceNodeDiscoveryResponse,
)
from .uri import sanitize_node_id

logger = logging.getLogger(__name__)
MAX_VALIDATION_ATTEMPTS = 3
T = TypeVar("T")


@dataclass(frozen=True)
class NodeDiscoveryResult:
    nodes: list[WikiNode]
    source_assignments: SourceAssignmentResponse


class NodeDiscoveryRunner:
    def __init__(self, llm: WikiLLMRunner, config: WikiConfig):
        self.llm = llm
        self.config = config

    async def discover_layer(
        self,
        cards: list[DocumentCard],
        *,
        depth: int,
        min_sources_per_node: int,
        reserved_node_ids: set[str] | None = None,
    ) -> NodeDiscoveryResult:
        source_ids = {card.doc_id for card in cards}
        prompt = build_node_discovery_prompt(
            cards,
            min_sources_per_node=min_sources_per_node,
        )
        return await _complete_with_validation_retry(
            self.llm,
            step="node_discovery",
            prompt=prompt,
            schema=WikiSourceNodeDiscoveryResponse.model_json_schema(),
            validate=lambda result: self._parse_layer_result(
                result,
                depth,
                source_ids,
                reserved_node_ids or set(),
            ),
        )

    def _parse_layer_result(
        self,
        result: dict,
        depth: int,
        source_ids: set[str],
        reserved_node_ids: set[str],
    ) -> NodeDiscoveryResult:
        response = WikiSourceNodeDiscoveryResponse.model_validate(result)
        _ensure_known_sources(response, source_ids)
        nodes = self._build_nodes(response.nodes, depth, reserved_node_ids=reserved_node_ids)
        assignments = [
            SourceAssignmentItem(
                node_id=node.node_id,
                source_ids=list(dict.fromkeys(item.supporting_source_ids)),
                support_scope=node.scope,
            )
            for node, item in zip(nodes, response.nodes, strict=False)
        ]
        return NodeDiscoveryResult(
            nodes=nodes,
            source_assignments=SourceAssignmentResponse(
                assignments=assignments,
                unassigned_source_ids=list(dict.fromkeys(response.unassigned_source_ids)),
            ),
        )

    def _build_nodes(
        self,
        discovered_nodes: list[WikiNodeDiscoveryItem],
        depth: int,
        *,
        reserved_node_ids: set[str] | None = None,
    ) -> list[WikiNode]:
        used_ids: set[str] = set(reserved_node_ids or set())
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


def _ensure_known_sources(response: WikiSourceNodeDiscoveryResponse, source_ids: set[str]) -> None:
    unknown_ids = [
        source_id
        for node in response.nodes
        for source_id in node.supporting_source_ids
        if source_id not in source_ids
    ]
    unknown_ids.extend(
        source_id for source_id in response.unassigned_source_ids if source_id not in source_ids
    )
    if unknown_ids:
        raise RuntimeError(f"node discovery references unknown source ids: {sorted(set(unknown_ids))}")


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
