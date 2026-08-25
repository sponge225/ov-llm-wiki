"""Generate Wiki node documents."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from pydantic import ValidationError

from .llm import WikiLLMRunner
from .prompts import build_node_documents_prompt
from .schemas import (
    NodeDocument,
    NodeDocumentsResponse,
    WikiNode,
)

logger = logging.getLogger(__name__)
MAX_VALIDATION_ATTEMPTS = 3
T = TypeVar("T")


class NodeContentGenerator:
    def __init__(self, llm: WikiLLMRunner):
        self.llm = llm

    async def generate_node_documents(
        self,
        node: WikiNode,
        source_documents: list[dict],
    ) -> list[NodeDocument]:
        prompt = build_node_documents_prompt(
            node,
            source_documents,
        )
        return await _complete_with_validation_retry(
            self.llm,
            step="node_documents",
            prompt=prompt,
            schema=NodeDocumentsResponse.model_json_schema(),
            node_id=node.node_id,
            validate=lambda result: self._parse_node_documents_result(
                node,
                result,
            ),
        )

    def _parse_node_documents_result(
        self,
        node: WikiNode,
        result: dict,
    ) -> list[NodeDocument]:
        response = NodeDocumentsResponse.model_validate(result)
        documents = _build_node_documents(response.documents)
        if not documents:
            raise RuntimeError(f"node_documents for {node.node_id} is empty")
        return documents


def _build_node_documents(document_contents: list) -> list[NodeDocument]:
    return [
        NodeDocument.model_validate(
            {
                **document.model_dump(mode="json"),
                "document_id": f"{index:04d}",
            }
        )
        for index, document in enumerate(document_contents, start=1)
    ]


async def _complete_with_validation_retry(
    llm: WikiLLMRunner,
    *,
    step: str,
    prompt: str,
    schema: dict,
    node_id: str,
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
                "[Wiki] Retrying %s for node_id=%s after validation failure attempt=%d/%d",
                step,
                node_id,
                attempt,
                MAX_VALIDATION_ATTEMPTS,
            )
    assert last_error is not None
    raise last_error
