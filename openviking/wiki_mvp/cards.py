"""Step 1: generate Wiki Document Cards."""

from __future__ import annotations

import asyncio
from typing import Any

from .content_loader import WikiCardInputMode, WikiContentLoader
from .llm import WikiLLMRunner
from .prompts import build_document_card_prompt
from .schemas import DocumentCard, ResourceDocument, WikiResourceInput


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
        max_card_input_chars: int = 20000,
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
        result = await self.llm.complete_json(
            step="doc_card",
            prompt=build_document_card_prompt(doc),
            schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "main_points": {"type": "array", "items": {"type": "string"}},
                    "important_terms": {"type": "array", "items": {"type": "string"}},
                    "limitations_or_notes": {"type": "array", "items": {"type": "string"}},
                    "candidate_topics": {"type": "array", "items": {"type": "string"}},
                    "evidence_anchors": {"type": "array"},
                },
                "required": ["summary", "main_points", "candidate_topics", "evidence_anchors"],
            },
        )
        payload = _normalize_card_payload(result.get("card", result), doc)
        card = DocumentCard.model_validate(payload)
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


def _normalize_card_payload(payload: Any, doc: ResourceDocument) -> dict[str, Any]:
    if isinstance(payload, str):
        payload = {"summary": payload}
    if not isinstance(payload, dict):
        raise TypeError(f"document card payload must be object or string, got {type(payload).__name__}")

    normalized = {
        key: payload[key]
        for key in DocumentCard.model_fields
        if key in payload
    }
    normalized.setdefault("doc_id", doc.doc_id)
    normalized.setdefault("resource_uri", doc.resource_uri)
    normalized.setdefault("title", doc.title)
    normalized.setdefault("source_type", doc.source_type)
    normalized.setdefault("summary", doc.summary or doc.abstract or doc.title)
    normalized.setdefault("main_points", [doc.summary or doc.abstract or doc.title])
    normalized.setdefault("candidate_topics", [doc.title])
    normalized["evidence_anchors"] = _normalize_evidence_anchors(
        normalized.get("evidence_anchors"),
        doc,
    )
    return normalized


def _normalize_evidence_anchors(value: Any, doc: ResourceDocument) -> list[dict[str, str]]:
    fallback = {
        "section_title": doc.title,
        "section_uri": doc.resource_uri,
        "summary": doc.summary or doc.abstract or doc.title,
    }
    if not isinstance(value, list) or not value:
        return [fallback]

    anchors: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            anchors.append(
                {
                    "section_title": str(item.get("section_title") or item.get("section") or doc.title),
                    "section_uri": str(item.get("section_uri") or doc.resource_uri),
                    "summary": str(item.get("summary") or item.get("text") or item.get("quote") or doc.title),
                }
            )
            continue
        anchors.append(
            {
                "section_title": doc.title,
                "section_uri": doc.resource_uri,
                "summary": str(item),
            }
        )
    return anchors
