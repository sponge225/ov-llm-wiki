"""Decide whether generated Wiki nodes should be aggregated upward."""

from __future__ import annotations

from .llm import WikiLLMRunner
from .prompts import build_next_layer_decision_prompt
from .schemas import (
    GeneratedNodeContext,
    NextLayerDecisionResponse,
    ResourceSpaceProfile,
)


class LayerDecisionRunner:
    def __init__(self, llm: WikiLLMRunner):
        self.llm = llm

    async def should_continue_upward(
        self,
        profile: ResourceSpaceProfile,
        current_layer_contexts: list[GeneratedNodeContext],
        min_child_nodes_per_parent: int = 3,
    ) -> bool:
        result = await self.llm.complete_json(
            step="next_layer_decision",
            prompt=build_next_layer_decision_prompt(
                profile,
                current_layer_contexts,
                min_child_nodes_per_parent=min_child_nodes_per_parent,
            ),
            schema=NextLayerDecisionResponse.model_json_schema(),
        )
        response = NextLayerDecisionResponse.model_validate(result)
        return response.continue_upward
