"""Step 1: generate Wiki Document Cards."""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError

from .content_loader import WikiCardInputMode, WikiContentLoader
from .llm import WikiLLMRunner
from .prompts import build_document_card_prompt
from .schemas import DocumentCard, DocumentCardContent, ResourceDocument, WikiResourceInput


logger = logging.getLogger(__name__)


class DocumentCardGenerator:
    def __init__(self, llm: WikiLLMRunner, max_concurrent: int = 10):
        self.llm = llm
        self.max_concurrent = max(1, max_concurrent)

    async def generate(self, docs: list[ResourceDocument]) -> list[DocumentCard]:
        sem = asyncio.Semaphore(self.max_concurrent)
        cards: list[DocumentCard | None] = [None] * len(docs)

        async def _generate_one(index: int, doc: ResourceDocument) -> None:
            async with sem:
                cards[index] = await self._generate_one(doc)

        await asyncio.gather(*[_generate_one(index, doc) for index, doc in enumerate(docs)])
        if any(card is None for card in cards):
            raise RuntimeError("document card generation did not produce all cards")
        return [card for card in cards if card is not None]

    async def generate_from_inputs(
        self,
        docs: list[WikiResourceInput],
        *,
        content_loader: WikiContentLoader,
        card_input_mode: WikiCardInputMode | str = WikiCardInputMode.SUMMARY,
        max_card_input_chars: int = 100000,
    ) -> list[DocumentCard]:
        sem = asyncio.Semaphore(self.max_concurrent)
        cards: list[DocumentCard | None] = [None] * len(docs)

        async def _generate_one(index: int, doc: WikiResourceInput) -> None:
            async with sem:
                resource_doc = await content_loader.load_document(
                    doc,
                    mode=card_input_mode,
                    max_card_input_chars=max_card_input_chars,
                )
                cards[index] = await self._generate_one(resource_doc)

        await asyncio.gather(*[_generate_one(index, doc) for index, doc in enumerate(docs)])
        if any(card is None for card in cards):
            raise RuntimeError("document card generation did not produce all cards")
        return [card for card in cards if card is not None]

    async def _generate_one(self, doc: ResourceDocument) -> DocumentCard:
        prompt = build_document_card_prompt(doc)
        result = await self.llm.complete_json(
            step="doc_card",
            prompt=prompt,
            schema=DocumentCardContent.model_json_schema(),
        )
        try:
            content = DocumentCardContent.model_validate(result)
        except ValidationError:
            for attempt in range(1, 4):
                logger.info(
                    "[Wiki] Retrying document card generation for doc_id=%s attempt=%d/3",
                    doc.doc_id,
                    attempt,
                )
                result = await self.llm.complete_json(
                    step="doc_card_retry",
                    prompt=prompt,
                    schema=DocumentCardContent.model_json_schema(),
                )
                try:
                    content = DocumentCardContent.model_validate(result)
                    break
                except ValidationError:
                    if attempt == 3:
                        raise
        card = DocumentCard.model_validate(
            {
                **content.model_dump(mode="json"),
                "doc_id": doc.doc_id,
                "resource_uri": doc.resource_uri,
                "title": doc.title,
                "source_type": doc.source_type,
            }
        )
        if not card.markdown:
            card = card.model_copy(update={"markdown": render_card_markdown(card)})
        return card


def render_card_markdown(card: DocumentCard) -> str:
    main_points = "\n".join(f"- {item}" for item in card.main_points)
    terms = "\n".join(f"- {item}" for item in card.important_terms)
    notes = "\n".join(f"- {item}" for item in card.limitations_or_notes)
    topics = "\n".join(f"- {item}" for item in card.candidate_topics)
    anchors = "\n".join(
        f"- {anchor.section_title}: {anchor.section_uri} - {anchor.summary}"
        for anchor in card.evidence_anchors
    )
    return f"""# Document Card: {card.title}

## Source Info

- Resource URI: {card.resource_uri}
- Source Type: {card.source_type}

## Summary

{card.summary}

## Main Points

{main_points}

## Important Terms

{terms}

## Limitations Or Notes

{notes}

## Candidate Wiki Topics

{topics}

## Evidence Anchors

{anchors}
"""
