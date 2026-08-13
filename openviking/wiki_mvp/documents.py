"""Steps 5 and 6: generate node.md and node documents."""

from __future__ import annotations

from .llm import WikiLLMRunner
from .prompts import build_node_documents_prompt, build_node_md_prompt
from .schemas import DocumentCard, GeneratedNodeContext, NodeDocument, SourceRef, WikiNode


class NodeContentGenerator:
    def __init__(self, llm: WikiLLMRunner):
        self.llm = llm

    async def generate_node_md(self, node: WikiNode, source_refs: list[SourceRef]) -> str:
        result = await self.llm.complete_json(
            step="node_md",
            prompt=build_node_md_prompt(node, source_refs),
            schema={
                "type": "object",
                "properties": {"node_md": {"type": "string"}},
                "required": ["node_md"],
            },
        )
        node_md = result["node_md"].strip()
        if not node_md:
            raise RuntimeError(f"node_md for {node.node_id} is empty")
        return node_md

    async def generate_node_documents(
        self,
        node: WikiNode,
        source_refs: list[SourceRef],
        cards: list[DocumentCard] | None = None,
        child_contexts: list[GeneratedNodeContext] | None = None,
    ) -> list[NodeDocument]:
        result = await self.llm.complete_json(
            step="node_documents",
            prompt=build_node_documents_prompt(
                node,
                source_refs,
                cards=cards,
                child_nodes=child_contexts,
            ),
            schema={
                "type": "object",
                "properties": {"documents": {"type": "array"}},
                "required": ["documents"],
            },
        )
        documents = [_coerce_document(item) for item in result["documents"]]
        if not documents:
            raise RuntimeError(f"node_documents for {node.node_id} is empty")
        return [_normalize_document_id(document, index) for index, document in enumerate(documents, start=1)]


def _normalize_document_id(document: NodeDocument, index: int) -> NodeDocument:
    return document.model_copy(update={"document_id": f"{index:04d}"})


def _coerce_document(item: object) -> NodeDocument:
    if isinstance(item, str):
        return NodeDocument(document_id="0001", content=item)
    if isinstance(item, dict):
        payload = dict(item)
        payload.setdefault("document_id", "0001")
        payload.setdefault("title", "High-Level Knowledge")
        if "content" not in payload and "markdown" in payload:
            payload["content"] = payload["markdown"]
        return NodeDocument.model_validate(payload)
    raise TypeError(f"node document item must be object or string, got {type(item).__name__}")
