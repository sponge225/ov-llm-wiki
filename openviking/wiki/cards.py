"""Generate Wiki source cards."""

from __future__ import annotations

import asyncio
import logging
import time

from pydantic import ValidationError

from .llm import WikiLLMRunner
from .prompts import build_document_card_prompt, build_node_card_prompt
from .schemas import (
    DocumentCard,
    DocumentCardContent,
    NodeDocument,
    ResourceDocument,
    WikiNode,
)

logger = logging.getLogger(__name__)


class DocumentCardGenerator:
    def __init__(self, llm: WikiLLMRunner, max_concurrent: int = 10):
        self.llm = llm
        self.max_concurrent = max(1, max_concurrent)

    async def generate(self, docs: list[ResourceDocument]) -> list[DocumentCard]:
        sem = asyncio.Semaphore(self.max_concurrent)
        cards: list[DocumentCard | None] = [None] * len(docs)
        progress_lock = asyncio.Lock()
        completed = 0
        total = len(docs)
        started_at = time.monotonic()
        progress_log_every = max(10, total // 10)

        logger.info(
            "[Wiki] Document card generation started: total=%d max_concurrent=%d",
            total,
            self.max_concurrent,
        )

        async def _generate_card_at_index(index: int, doc: ResourceDocument) -> None:
            nonlocal completed
            async with sem:
                cards[index] = await self._generate_card(doc)
                async with progress_lock:
                    completed += 1
                    should_log_progress = (
                        completed == total
                        or completed % progress_log_every == 0
                    )
                    if should_log_progress:
                        elapsed = time.monotonic() - started_at
                        logger.info(
                            "[Wiki] Document card progress: %d/%d (%.1f%%) elapsed=%.1fs latest_doc_id=%s",
                            completed,
                            total,
                            completed * 100 / total if total else 100.0,
                            elapsed,
                            doc.doc_id,
                        )

        await asyncio.gather(
            *[_generate_card_at_index(index, doc) for index, doc in enumerate(docs)]
        )
        if any(card is None for card in cards):
            raise RuntimeError("document card generation did not produce all cards")
        return [card for card in cards if card is not None]

    async def _generate_card(self, doc: ResourceDocument) -> DocumentCard:
        prompt = build_document_card_prompt(doc)
        return await self._generate_card_from_prompt(
            prompt=prompt,
            step="doc_card",
            retry_step="doc_card_retry",
            doc_id=doc.doc_id,
            resource_uri=doc.resource_uri,
            title=doc.title,
        )

    async def generate_node_card(
        self,
        node: WikiNode,
        documents: list[NodeDocument],
        *,
        resource_uri: str,
    ) -> DocumentCard:
        prompt = build_node_card_prompt(node, documents)
        return await self._generate_card_from_prompt(
            prompt=prompt,
            step="node_card",
            retry_step="node_card_retry",
            doc_id=node.node_id,
            resource_uri=resource_uri,
            title=node.title,
        )

    async def _generate_card_from_prompt(
        self,
        *,
        prompt: str,
        step: str,
        retry_step: str,
        doc_id: str,
        resource_uri: str,
        title: str,
    ) -> DocumentCard:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                result = await self.llm.complete_json(
                    step=step if attempt == 1 else retry_step,
                    prompt=prompt,
                    schema=DocumentCardContent.model_json_schema(),
                )
                content = DocumentCardContent.model_validate(result)
                break
            except (RuntimeError, ValidationError) as exc:
                last_error = exc
                if attempt == 3:
                    raise
                logger.info(
                    "[Wiki] Retrying card generation for doc_id=%s step=%s attempt=%d/3",
                    doc_id,
                    step,
                    attempt,
                )
        else:
            assert last_error is not None
            raise last_error
        card = DocumentCard.model_validate(
            {
                **content.model_dump(mode="json"),
                "doc_id": doc_id,
                "resource_uri": resource_uri,
                "title": title,
            }
        )
        if not card.markdown:
            card = card.model_copy(update={"markdown": render_card_markdown(card)})
        return card


def render_card_markdown(card: DocumentCard) -> str:
    main_points = "\n".join(f"- {item}" for item in card.main_points)
    terms = "\n".join(f"- {item}" for item in card.important_terms)
    topics = "\n".join(f"- {item}" for item in card.candidate_topics)
    return f"""# Wiki Card: {card.title}

## Source Info

- Source URI: {card.resource_uri}

## Summary

{card.summary}

## Main Points

{main_points}

## Important Terms

{terms}

## Candidate Wiki Topics

{topics}
"""
