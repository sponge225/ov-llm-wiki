"""Decide whether generated Wiki nodes should be aggregated upward."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from .llm import WikiLLMRunner
from .prompts import build_next_layer_decision_prompt
from .schemas import (
    GeneratedNodeContext,
    NextLayerDecisionResponse,
)

logger = logging.getLogger(__name__)
MAX_VALIDATION_ATTEMPTS = 3


class LayerDecisionRunner:
    def __init__(self, llm: WikiLLMRunner):
        self.llm = llm

    async def should_continue_upward(
        self,
        current_layer_contexts: list[GeneratedNodeContext],
        min_child_nodes_per_parent: int = 3,
    ) -> bool:
        prompt = build_next_layer_decision_prompt(
            current_layer_contexts,
            min_child_nodes_per_parent=min_child_nodes_per_parent,
        )
        last_error: Exception | None = None
        for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
            try:
                result = await self.llm.complete_json(
                    step="next_layer_decision",
                    prompt=prompt,
                    schema=NextLayerDecisionResponse.model_json_schema(),
                )
                response = NextLayerDecisionResponse.model_validate(result)
                return response.continue_upward
            except (RuntimeError, ValidationError) as exc:
                last_error = exc
                if attempt == MAX_VALIDATION_ATTEMPTS:
                    break
                logger.info(
                    "[Wiki] Retrying next_layer_decision after validation failure attempt=%d/%d",
                    attempt,
                    MAX_VALIDATION_ATTEMPTS,
                )
        assert last_error is not None
        raise last_error
