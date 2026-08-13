"""Step 2: generate resource space profile."""

from __future__ import annotations

from .llm import WikiLLMRunner
from .prompts import build_profile_prompt
from .schemas import DocumentCard, ResourceSpaceProfile


class ResourceSpaceProfiler:
    def __init__(self, llm: WikiLLMRunner):
        self.llm = llm

    async def generate(self, cards: list[DocumentCard]) -> ResourceSpaceProfile:
        result = await self.llm.complete_json(
            step="profile",
            prompt=build_profile_prompt(cards),
            schema=ResourceSpaceProfile.model_json_schema(),
        )
        payload = result.get("profile", result)
        return ResourceSpaceProfile.model_validate(payload)
