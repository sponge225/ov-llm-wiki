"""Step 7: generate evidence and decide whether to continue upward."""

from __future__ import annotations

from .llm import WikiLLMRunner
from .prompts import build_evidence_prompt, build_next_layer_decision_prompt
from .schemas import (
    DocumentCard,
    EvidenceClaim,
    EvidenceRef,
    GeneratedNodeContext,
    NodeDocument,
    ResourceSpaceProfile,
    SourceRef,
    WikiNode,
)


class EvidenceRunner:
    def __init__(self, llm: WikiLLMRunner):
        self.llm = llm

    async def generate_node_evidence(
        self,
        node: WikiNode,
        node_documents: list[NodeDocument],
        source_refs: list[SourceRef],
        cards: list[DocumentCard],
    ) -> list[EvidenceClaim]:
        result = await self.llm.complete_json(
            step="evidence",
            prompt=build_evidence_prompt(
                node=node,
                node_documents=[document.model_dump(mode="json") for document in node_documents],
                source_refs=source_refs,
                cards=cards,
            ),
            schema={
                "type": "object",
                "properties": {"claims": {"type": "array"}},
                "required": ["claims"],
            },
        )
        claims = [
            _coerce_claim(item, source_refs, index)
            for index, item in enumerate(result["claims"], start=1)
        ]
        if not claims:
            raise RuntimeError(f"node {node.node_id} has no evidence claims")
        self._validate_claim_refs(node.node_id, claims, source_refs)
        return claims

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
            schema={
                "type": "object",
                "properties": {
                    "continue_upward": {"type": "boolean"},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["continue_upward", "reasons"],
            },
        )
        return bool(result["continue_upward"])

    def _validate_claim_refs(
        self,
        node_id: str,
        claims: list[EvidenceClaim],
        source_refs: list[SourceRef],
    ) -> None:
        allowed_doc_ids = {ref.doc_id for ref in source_refs}
        for claim in claims:
            for evidence_ref in claim.evidence_refs:
                if evidence_ref.doc_id not in allowed_doc_ids:
                    raise RuntimeError(
                        f"claim {claim.claim_id} in node {node_id} references "
                        f"doc_id outside node source refs: {evidence_ref.doc_id}"
                    )


def _coerce_claim(item: object, source_refs: list[SourceRef], index: int) -> EvidenceClaim:
    if isinstance(item, str):
        item = {"claim": item}
    if not isinstance(item, dict):
        raise TypeError(f"evidence claim item must be object, got {type(item).__name__}")
    payload = dict(item)
    payload.setdefault("claim_id", f"claim_{index:04d}")
    payload.setdefault("claim_type", "empirical_finding")
    payload.setdefault("confidence", 0.6)
    if "evidence_refs" not in payload:
        evidence = payload.pop("evidence", None)
        payload["evidence_refs"] = [_coerce_evidence_ref(evidence, source_refs)]
    else:
        payload["evidence_refs"] = [
            _coerce_evidence_ref(ref, source_refs) for ref in payload["evidence_refs"]
        ]
    payload = {
        key: payload[key]
        for key in ("claim_id", "claim", "claim_type", "evidence_refs", "confidence", "notes")
        if key in payload
    }
    return EvidenceClaim.model_validate(payload)


def _coerce_evidence_ref(value: object, source_refs: list[SourceRef]) -> EvidenceRef:
    fallback = source_refs[0]
    if isinstance(value, dict):
        raw_doc_id = str(value.get("doc_id") or value.get("source_document_id") or "")
        source_ref = next((ref for ref in source_refs if ref.doc_id == raw_doc_id), fallback)
        return EvidenceRef(
            doc_id=source_ref.doc_id,
            resource_uri=source_ref.resource_uri,
            section_uri=str(value.get("section_uri") or source_ref.resource_uri),
            section_title=str(value.get("section_title") or value.get("section") or source_ref.title),
            support_type=str(value.get("support_type") or "supports"),
            evidence_quote_or_summary=str(
                value.get("evidence_quote_or_summary")
                or value.get("quote")
                or value.get("summary")
                or value.get("text")
                or source_ref.support_scope
            ),
        )
    return EvidenceRef(
        doc_id=fallback.doc_id,
        resource_uri=fallback.resource_uri,
        section_uri=fallback.resource_uri,
        section_title=fallback.title,
        support_type="supports",
        evidence_quote_or_summary=str(value or fallback.support_scope),
    )
