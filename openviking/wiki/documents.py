"""Generate the single Markdown document stored for each Wiki node."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import TypeVar

from pydantic import ValidationError

from openviking.utils.token_estimation import estimate_text_tokens

from .llm import WikiLLMOutputError, WikiLLMRunner
from .prompts import (
    build_node_document_outline_prompt,
    build_node_document_refine_prompt,
    build_node_documents_prompt,
)
from .schemas import DocumentCard, NodeDocument, NodeMarkdownResponse, WikiNode
from .source_selector import SelectedSourceDocument

logger = logging.getLogger(__name__)
MAX_VALIDATION_ATTEMPTS = 3
MAX_SOURCES_PER_BATCH = 3
T = TypeVar("T")
_NODE_MARKDOWN_SCHEMA = NodeMarkdownResponse.model_json_schema()
_NODE_MARKDOWN_SCHEMA_TEXT = json.dumps(_NODE_MARKDOWN_SCHEMA, sort_keys=True)


class NodeContentGenerator:
    def __init__(
        self,
        llm: WikiLLMRunner,
        *,
        max_prompt_tokens: int = 96000,
        max_document_tokens: int = 16000,
    ):
        self.llm = llm
        self.max_prompt_tokens = max(1, int(max_prompt_tokens))
        self.max_document_tokens = max(1, int(max_document_tokens))

    async def generate_direct(
        self,
        node: WikiNode,
        source_documents: list[dict],
    ) -> NodeDocument:
        prompt = build_node_documents_prompt(
            node, source_documents, max_document_tokens=self.max_document_tokens
        )
        self._check_prompt_budget(node, "node_documents", prompt)
        markdown = await self._complete_markdown(
            node,
            step="node_documents",
            prompt=prompt,
        )
        return NodeDocument(title=node.title, content=markdown)

    async def generate_outline(
        self,
        node: WikiNode,
        source_cards: list[DocumentCard],
    ) -> str:
        outline_prompt, included_cards = self._fit_outline_prompt(node, source_cards)
        logger.info(
            "[Wiki] Generating node outline: node_id=%s source_cards=%d/%d prompt_tokens=%d",
            node.node_id,
            included_cards,
            len(source_cards),
            _request_tokens(outline_prompt),
        )
        return await _complete_with_validation_retry(
            self.llm,
            step="node_document_outline",
            prompt=outline_prompt,
            schema=_NODE_MARKDOWN_SCHEMA,
            node_id=node.node_id,
            validate=self._parse_outline,
        )

    async def generate_staged(
        self,
        node: WikiNode,
        outline: str,
        selected_sources: list[SelectedSourceDocument],
    ) -> NodeDocument:
        current_markdown = f"# {node.title}\n\n{outline}"
        offset = 0
        first_batch = True
        while offset < len(selected_sources):
            batch_size = min(MAX_SOURCES_PER_BATCH, len(selected_sources) - offset)
            while batch_size:
                batch = selected_sources[offset : offset + batch_size]
                prompt = build_node_document_refine_prompt(
                    node,
                    current_markdown,
                    [_selected_source_payload(source) for source in batch],
                    initial=first_batch,
                    max_document_tokens=self.max_document_tokens,
                )
                if _request_tokens(prompt) <= self.max_prompt_tokens:
                    break
                batch_size -= 1
            if not batch_size:
                source = selected_sources[offset]
                raise RuntimeError(
                    f"node prompt exceeds {self.max_prompt_tokens} tokens for "
                    f"node_id={node.node_id} source_id={source.source_id}"
                )

            previous_nonempty_h2 = set() if first_batch else _nonempty_h2_headings(current_markdown)
            step = "node_documents_initial" if first_batch else "node_documents_refine"
            logger.info(
                "[Wiki] Generating node document batch: "
                "node_id=%s step=%s source_ids=%s prompt_tokens=%d",
                node.node_id,
                step,
                [source.source_id for source in batch],
                _request_tokens(prompt),
            )
            current_markdown = await self._complete_markdown(
                node,
                step=step,
                prompt=prompt,
                required_h2=previous_nonempty_h2,
            )
            offset += batch_size
            first_batch = False
        return NodeDocument(title=node.title, content=current_markdown)

    async def _complete_markdown(
        self,
        node: WikiNode,
        *,
        step: str,
        prompt: str,
        required_h2: set[str] | None = None,
    ) -> str:
        return await _complete_with_validation_retry(
            self.llm,
            step=step,
            prompt=prompt,
            schema=_NODE_MARKDOWN_SCHEMA,
            node_id=node.node_id,
            validate=lambda result: self._parse_document(
                node, result, required_h2=required_h2 or set()
            ),
        )

    def _fit_outline_prompt(
        self, node: WikiNode, source_cards: list[DocumentCard]
    ) -> tuple[str, int]:
        for count in range(len(source_cards), 0, -1):
            prompt = build_node_document_outline_prompt(node, source_cards[:count])
            if _request_tokens(prompt) <= self.max_prompt_tokens:
                return prompt, count
        raise RuntimeError(
            f"node outline prompt exceeds {self.max_prompt_tokens} tokens for "
            f"node_id={node.node_id}"
        )

    def _check_prompt_budget(self, node: WikiNode, step: str, prompt: str) -> None:
        prompt_tokens = _request_tokens(prompt)
        if prompt_tokens > self.max_prompt_tokens:
            raise RuntimeError(
                f"{step} prompt exceeds {self.max_prompt_tokens} tokens for "
                f"node_id={node.node_id}: {prompt_tokens}"
            )

    def _parse_outline(self, result: dict) -> str:
        markdown = NodeMarkdownResponse.model_validate(result).markdown
        headings = _markdown_headings(markdown)
        h2 = [text for level, text in headings if level == 2]
        if len(set(h2)) < 2:
            raise RuntimeError("node outline must contain at least two unique H2 headings")
        if any(level == 1 for level, _ in headings):
            raise RuntimeError("node outline must not contain an H1 heading")
        if any(
            line.strip() and not re.match(r"^#{2,3}\s+\S", line) for line in markdown.splitlines()
        ):
            raise RuntimeError("node outline may contain only H2/H3 headings and blank lines")
        return markdown.strip()

    def _parse_document(
        self,
        node: WikiNode,
        result: dict,
        *,
        required_h2: set[str],
    ) -> str:
        markdown = NodeMarkdownResponse.model_validate(result).markdown
        headings = _markdown_headings(markdown)
        h1 = [text for level, text in headings if level == 1]
        if h1 != [node.title] or markdown.splitlines()[0].strip() != f"# {node.title}":
            raise RuntimeError(f"node document H1 must be exactly: # {node.title}")
        h2 = {text for level, text in headings if level == 2}
        if not h2:
            raise RuntimeError("node document must contain at least one H2 heading")
        missing = required_h2 - h2
        if missing:
            raise RuntimeError(f"node document removed non-empty H2 headings: {sorted(missing)}")
        token_count = estimate_text_tokens(markdown)
        if token_count > self.max_document_tokens:
            raise RuntimeError(
                f"node document exceeds {self.max_document_tokens} tokens: {token_count}"
            )
        return markdown.strip()


def _selected_source_payload(source: SelectedSourceDocument) -> dict:
    return {
        "source_id": source.source_id,
        "title": source.title,
        "sections": [section.model_dump(mode="json") for section in source.sections],
    }


def _request_tokens(prompt: str) -> int:
    return estimate_text_tokens(prompt) + estimate_text_tokens(_NODE_MARKDOWN_SCHEMA_TEXT)


def _markdown_headings(markdown: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))
    return headings


def _nonempty_h2_headings(markdown: str) -> set[str]:
    result: set[str] = set()
    current_h2: str | None = None
    has_body = False
    in_fence = False
    for line in [*markdown.splitlines(), "## __end__"]:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            if current_h2 is not None:
                has_body = True
            continue
        match = None if in_fence else re.match(r"^##\s+(.+?)\s*#*\s*$", stripped)
        if match:
            if current_h2 is not None and has_body:
                result.add(current_h2)
            current_h2 = match.group(1).strip()
            has_body = False
        elif current_h2 is not None and stripped and not stripped.startswith("### "):
            has_body = True
    return result


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
            result = await llm.complete_json(step=step, prompt=prompt, schema=schema)
        except WikiLLMOutputError as exc:
            last_error = exc
        else:
            try:
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
