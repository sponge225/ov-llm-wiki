"""Step 1: generate Wiki Document Cards."""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError

from .llm import WikiLLMRunner
from .prompts import build_document_card_prompt
from .schemas import DocumentCard, DocumentCardContent, ResourceDocument


logger = logging.getLogger(__name__)


class DocumentCardGenerator:
    def __init__(self, llm: WikiLLMRunner, max_concurrent: int = 10):
        self.llm = llm
        self.max_concurrent = max(1, max_concurrent)

    async def generate(self, docs: list[ResourceDocument]) -> list[DocumentCard]:
        sem = asyncio.Semaphore(self.max_concurrent)
        cards: list[DocumentCard | None] = [None] * len(docs)

        async def _generate_card_at_index(index: int, doc: ResourceDocument) -> None:
            async with sem:
                cards[index] = await self._generate_card(doc)

        await asyncio.gather(
            *[_generate_card_at_index(index, doc) for index, doc in enumerate(docs)]
        )
        if any(card is None for card in cards):
            raise RuntimeError("document card generation did not produce all cards")
        return [card for card in cards if card is not None]

    async def _generate_card(self, doc: ResourceDocument) -> DocumentCard:
        prompt = build_document_card_prompt(doc)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                result = await self.llm.complete_json(
                    step="doc_card" if attempt == 1 else "doc_card_retry",
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
                    "[Wiki] Retrying document card generation for doc_id=%s attempt=%d/3",
                    doc.doc_id,
                    attempt,
                )
        else:
            assert last_error is not None
            raise last_error
        card = DocumentCard.model_validate(
            {
                **content.model_dump(mode="json"),
                "doc_id": doc.doc_id,
                "resource_uri": doc.resource_uri,
                "title": doc.title,
            }
        )
        if not card.markdown:
            card = card.model_copy(update={"markdown": render_card_markdown(card)})
        return card


def render_card_markdown(card: DocumentCard) -> str:
    main_points = "\n".join(f"- {item}" for item in card.main_points)
    terms = "\n".join(f"- {item}" for item in card.important_terms)
    topics = "\n".join(f"- {item}" for item in card.candidate_topics)
    return f"""# Document Card: {card.title}

## Source Info

- Resource URI: {card.resource_uri}

## Summary

{card.summary}

## Main Points

{main_points}

## Important Terms

{terms}

## Candidate Wiki Topics

{topics}
"""
