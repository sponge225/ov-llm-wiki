"""LLM wrapper for Wiki structured calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from openviking.models.vlm.llm import StructuredVLM


@dataclass
class LLMCallRecord:
    step: str
    prompt_version: str
    input_hash: str
    prompt: str
    schema_name: str | None = None
    schema_hash: str | None = None


@dataclass
class LLMOutputRecord:
    step: str
    output_hash: str
    raw_output: Any


@dataclass
class WikiLLMRunLog:
    prompts: list[LLMCallRecord] = field(default_factory=list)
    raw_outputs: list[LLMOutputRecord] = field(default_factory=list)


class WikiLLMRunner:
    def __init__(self, vlm: Any | None = None, vlm_config: dict[str, Any] | None = None):
        self.vlm = vlm or StructuredVLM(vlm_config=vlm_config)
        self.log = WikiLLMRunLog()

    async def complete_json(
        self,
        step: str,
        prompt: str,
        schema: dict[str, Any],
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        prompt_version = prompt_version or f"{step}_v1"
        schema_name = f"wiki_{step}"
        self.log.prompts.append(
            LLMCallRecord(
                step=step,
                prompt_version=prompt_version,
                input_hash=_hash_text(prompt),
                prompt=prompt,
                schema_name=schema_name,
                schema_hash=_hash_text(json.dumps(schema, ensure_ascii=False, sort_keys=True)),
            )
        )

        result = await self.vlm.complete_json_async(
            prompt=prompt,
            schema=schema,
            schema_name=schema_name,
        )
        if result is None:
            raise RuntimeError(f"LLM step {step} returned no parseable JSON")
        if not isinstance(result, dict):
            raise RuntimeError(f"LLM step {step} must return a JSON object")

        self.log.raw_outputs.append(
            LLMOutputRecord(
                step=step,
                output_hash=_hash_text(json.dumps(result, ensure_ascii=False, sort_keys=True)),
                raw_output=result,
            )
        )
        return result


def _hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
