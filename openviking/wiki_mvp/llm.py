"""LLM wrapper for Wiki MVP structured calls."""

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
        prompt_with_format = f"{prompt}\n\nReturn only JSON matching this shape:\n{_compact_schema_shape(schema)}"
        self.log.prompts.append(
            LLMCallRecord(
                step=step,
                prompt_version=prompt_version,
                input_hash=_hash_text(prompt_with_format),
                prompt=prompt_with_format,
            )
        )

        result = await self.vlm.complete_json_async(prompt=prompt_with_format)
        if result is None:
            raise RuntimeError(f"LLM step {step} returned no parseable JSON")
        if not isinstance(result, dict):
            raise RuntimeError(f"LLM step {step} must return a JSON object")
        if _looks_like_json_schema(result):
            raise RuntimeError(f"LLM step {step} returned JSON schema instead of data")

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


def _compact_schema_shape(schema: dict[str, Any]) -> str:
    return json.dumps(_schema_node_shape(schema, schema), ensure_ascii=False, indent=2)


def _schema_node_shape(node: dict[str, Any], root: dict[str, Any]) -> Any:
    if "$ref" in node:
        ref = node["$ref"]
        if ref.startswith("#/$defs/"):
            name = ref.rsplit("/", 1)[-1]
            return _schema_node_shape(root.get("$defs", {}).get(name, {}), root)
        return "object"

    if "anyOf" in node:
        shapes = [_schema_node_shape(item, root) for item in node["anyOf"]]
        non_null = [shape for shape in shapes if shape != "null"]
        return non_null[0] if non_null else "null"

    node_type = node.get("type")
    if node_type == "object" or "properties" in node:
        return {
            name: _schema_node_shape(prop, root)
            for name, prop in node.get("properties", {}).items()
        }
    if node_type == "array":
        return [_schema_node_shape(node.get("items", {}), root)]
    if node_type in {"string", "integer", "number", "boolean", "null"}:
        enum = node.get("enum")
        if enum:
            return enum
        return node_type
    return node.get("title") or "value"


def _looks_like_json_schema(value: dict[str, Any]) -> bool:
    return (
        "$defs" in value
        or ("properties" in value and "type" in value)
        or ("$schema" in value)
    )
