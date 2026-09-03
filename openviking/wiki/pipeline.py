"""Wiki batch generation orchestrator."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from .assignments import SourceRefBuilder
from .cards import DocumentCardGenerator
from .config import WikiConfig
from .content_loader import WikiCardInputMode, WikiContentLoader
from .documents import NodeContentGenerator
from .layer_decision import LayerDecisionRunner
from .llm import WikiLLMRunner
from .nodes import NodeDiscoveryRunner
from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    PipelineArtifacts,
    ResourceDocument,
    SourceAssignmentResult,
    SourceRef,
    WikiNode,
    WikiResourceInput,
)
from .uri import (
    card_json_uri,
    card_md_uri,
    node_card_json_uri,
    node_card_md_uri,
    node_document_uri,
    node_root_uri,
    node_sources_dir,
    run_dir,
    wiki_root,
)
from .writer import WikiVikingFSWriter

logger = logging.getLogger(__name__)
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "access_key",
    "access-key",
    "secret_key",
    "secret-key",
    "secret",
    "token",
    "authorization",
    "password",
}


class WikiPipeline:
    def __init__(
        self,
        writer: WikiVikingFSWriter,
        config: WikiConfig | None = None,
        llm: WikiLLMRunner | None = None,
    ):
        self.config = config or WikiConfig()
        self.llm = llm or WikiLLMRunner(vlm_config=self.config.vlm_config)
        self.writer = writer
        self.card_generator = DocumentCardGenerator(
            self.llm,
            max_concurrent=self.config.limits.max_concurrent_cards,
        )
        self.node_discovery = NodeDiscoveryRunner(self.llm, self.config)
        self.source_ref_builder = SourceRefBuilder(self.config)
        self.content_generator = NodeContentGenerator(self.llm)
        self.layer_decision_runner = LayerDecisionRunner(self.llm)

    def get_token_usage(self) -> dict[str, Any]:
        return self.llm.get_token_usage()

    async def run_from_inputs(
        self,
        docs: list[WikiResourceInput],
        *,
        content_loader: WikiContentLoader,
        card_input_mode: WikiCardInputMode | str = WikiCardInputMode.SUMMARY,
        max_card_input_chars: int = 20000,
    ) -> PipelineArtifacts:
        if not docs:
            raise ValueError("Wiki pipeline requires at least one resource document")

        artifacts = PipelineArtifacts()
        await self.writer.ensure_dirs()

        logger.info(
            "[Wiki] Generating document cards for %d docs from %s inputs",
            len(docs),
            card_input_mode,
        )

        input_mode = WikiCardInputMode(card_input_mode)
        max_concurrent = max(1, self.config.limits.max_concurrent_cards)
        sem = asyncio.Semaphore(max_concurrent)

        async def _load_documents(mode: WikiCardInputMode) -> list[ResourceDocument]:
            results: list[ResourceDocument | None] = [None] * len(docs)

            async def _load_one(index: int, doc: WikiResourceInput) -> None:
                async with sem:
                    results[index] = await content_loader.load_document(
                        doc,
                        mode=mode,
                        max_card_input_chars=max_card_input_chars,
                    )

            await asyncio.gather(*[_load_one(index, doc) for index, doc in enumerate(docs)])
            if any(result is None for result in results):
                raise RuntimeError("resource document loading did not produce all documents")
            return [result for result in results if result is not None]

        resource_docs = await _load_documents(input_mode)
        source_docs_task = (
            None
            if input_mode == WikiCardInputMode.RAW_CHUNK
            else asyncio.create_task(_load_documents(WikiCardInputMode.RAW_CHUNK))
        )
        try:
            cards = await self.card_generator.generate(resource_docs)
        except Exception:
            if source_docs_task is not None:
                source_docs_task.cancel()
                await asyncio.gather(source_docs_task, return_exceptions=True)
            raise
        source_docs = resource_docs if source_docs_task is None else await source_docs_task
        logger.info("[Wiki] Generated %d document cards", len(cards))
        return await self._run_from_cards(
            cards,
            artifacts,
            {doc.doc_id: doc for doc in source_docs},
        )

    async def _run_from_cards(
        self,
        cards: list[DocumentCard],
        artifacts: PipelineArtifacts,
        resource_documents_by_id: dict[str, ResourceDocument],
    ) -> PipelineArtifacts:
        all_cards: list[DocumentCard] = list(cards)
        source_documents_by_id = dict(resource_documents_by_id)
        artifacts.cards = all_cards
        await self._write_cards(cards)

        all_nodes: list[WikiNode] = []
        all_source_refs_by_node: dict[str, list[SourceRef]] = {}
        all_unassigned_source_ids: list[str] = []
        all_contexts: list[GeneratedNodeContext] = []
        previous_layer_cards: list[DocumentCard] = []
        reserved_node_ids = {card.doc_id for card in cards}

        for depth in range(1, self.config.limits.max_depth + 1):
            source_cards = cards if depth == 1 else previous_layer_cards
            if depth == 1:
                min_sources = self.config.limits.min_refs_per_node
                logger.info("[Wiki] Discovering bottom-layer nodes from %d card topics", len(source_cards))
            else:
                min_sources = self.config.limits.min_child_nodes_per_parent
                logger.info(
                    "[Wiki] Discovering depth=%d parent nodes from %d previous-layer cards",
                    depth,
                    len(source_cards),
                )
            discovery = await self.node_discovery.discover_layer(
                source_cards,
                depth=depth,
                min_sources_per_node=min_sources,
                reserved_node_ids=reserved_node_ids,
            )
            layer_nodes = discovery.nodes

            active_nodes = [node for node in layer_nodes if node.status == "active"]
            logger.info(
                "[Wiki] Depth=%d discovered %d nodes (%d active)",
                depth,
                len(layer_nodes),
                len(active_nodes),
            )
            if not active_nodes:
                if depth == 1:
                    raise RuntimeError("bottom layer produced no active nodes")
                break

            logger.info(
                "[Wiki] Building source refs for %d nodes from %d source cards",
                len(active_nodes),
                len(source_cards),
            )
            assignment_result = SourceAssignmentResult(
                source_refs_by_node=self.source_ref_builder.build_refs_by_node(
                    discovery.source_assignments.assignments,
                    source_cards,
                ),
                unassigned_source_ids=discovery.source_assignments.unassigned_source_ids,
            )
            logger.info(
                "[Wiki] Depth=%d produced %d source refs",
                depth,
                sum(len(refs) for refs in assignment_result.source_refs_by_node.values()),
            )

            layer_nodes, active_nodes, assignment_result = _reject_nodes_with_insufficient_refs(
                layer_nodes,
                active_nodes,
                assignment_result,
                min_sources=min_sources,
                depth=depth,
            )
            if not active_nodes:
                if depth == 1:
                    raise RuntimeError("bottom layer produced no supported active nodes")
                break
            logger.info("[Wiki] Depth=%d retained %d supported active nodes", depth, len(active_nodes))

            if depth > 1:
                all_nodes = _assign_parent_node_links(all_nodes, active_nodes)

            all_nodes.extend(layer_nodes)
            reserved_node_ids.update(node.node_id for node in layer_nodes)
            artifacts.nodes = all_nodes
            await self.writer.write_json(f"{wiki_root(self.config)}nodes.json", {"nodes": all_nodes})

            all_source_refs_by_node.update(assignment_result.source_refs_by_node)
            all_unassigned_source_ids.extend(assignment_result.unassigned_source_ids)
            artifacts.source_refs_by_node = all_source_refs_by_node
            await self.writer.write_json(
                f"{wiki_root(self.config)}source_assignments.json",
                {
                    "source_refs_by_node": all_source_refs_by_node,
                    "unassigned_source_ids": all_unassigned_source_ids,
                },
            )

            layer_contexts = await self._generate_layer_contexts(
                active_nodes,
                assignment_result,
                source_documents_by_id,
                depth=depth,
            )
            all_contexts.extend(layer_contexts)
            previous_layer_cards = [context.card for context in layer_contexts]
            all_cards.extend(previous_layer_cards)
            source_documents_by_id.update(
                {
                    context.node.node_id: _resource_document_for_node(self.config, context)
                    for context in layer_contexts
                }
            )
            logger.info(
                "[Wiki] Depth=%d generated %d node contexts (total=%d)",
                depth,
                len(layer_contexts),
                len(all_contexts),
            )

            artifacts.node_contexts = all_contexts
            artifacts.cards = all_cards

            if depth >= self.config.limits.max_depth:
                break

            continue_upward = await self.layer_decision_runner.should_continue_upward(
                layer_contexts,
                min_child_nodes_per_parent=self.config.limits.min_child_nodes_per_parent,
            )
            logger.info("[Wiki] Depth=%d continue_upward=%s", depth, continue_upward)
            if not continue_upward:
                break

        await self._write_run_records()
        logger.info(
            "[Wiki] Completed wiki generation: cards=%d nodes=%d contexts=%d wiki_root=%s",
            len(artifacts.cards),
            len(artifacts.nodes),
            len(artifacts.node_contexts),
            wiki_root(self.config),
        )
        return artifacts

    async def _generate_layer_contexts(
        self,
        active_nodes: list[WikiNode],
        assignment_result: SourceAssignmentResult,
        source_documents_by_id: dict[str, ResourceDocument],
        *,
        depth: int,
    ) -> list[GeneratedNodeContext]:
        max_concurrent = max(1, self.config.limits.max_concurrent_nodes)
        sem = asyncio.Semaphore(max_concurrent)
        contexts: list[GeneratedNodeContext | None] = [None] * len(active_nodes)
        logger.info(
            "[Wiki] Depth=%d generating %d node contexts with max_concurrent=%d",
            depth,
            len(active_nodes),
            max_concurrent,
        )

        async def _generate_one(index: int, node: WikiNode) -> None:
            async with sem:
                logger.info("[Wiki] Depth=%d generating node context: %s", depth, node.node_id)
                contexts[index] = await self._generate_node_context(
                    node,
                    assignment_result,
                    source_documents_by_id,
                )
                logger.info("[Wiki] Depth=%d generated node context: %s", depth, node.node_id)

        await asyncio.gather(*[_generate_one(index, node) for index, node in enumerate(active_nodes)])
        if any(context is None for context in contexts):
            raise RuntimeError("node context generation did not produce all contexts")
        return [context for context in contexts if context is not None]

    async def _generate_node_context(
        self,
        node: WikiNode,
        assignment_result: SourceAssignmentResult,
        source_documents_by_id: dict[str, ResourceDocument],
    ) -> GeneratedNodeContext:
        source_refs = assignment_result.source_refs_by_node.get(node.node_id)
        if not source_refs:
            raise RuntimeError(f"active node {node.node_id} has no source refs")

        await self.writer.ensure_dirs([node.node_id])
        await self._write_source_refs(node, source_refs)

        source_documents = _source_documents_for_refs(source_refs, source_documents_by_id)
        documents = await self.content_generator.generate_node_documents(
            node,
            source_documents,
        )
        for document in documents:
            await self.writer.write_text(
                node_document_uri(self.config, node.node_id, document.document_id),
                document.content,
            )
        card = await self.card_generator.generate_node_card(
            node,
            documents,
            resource_uri=node_root_uri(self.config, node.node_id),
        )
        await self._write_node_card(node, card)

        context = GeneratedNodeContext(
            node=node,
            card=card,
            documents=documents,
            source_refs=source_refs,
        )
        return context

    async def _write_cards(self, cards: list[DocumentCard]) -> None:
        for card in cards:
            await self.writer.write_text(card_md_uri(self.config, card.doc_id), card.markdown)
            await self.writer.write_json(card_json_uri(self.config, card.doc_id), card)

    async def _write_node_card(self, node: WikiNode, card: DocumentCard) -> None:
        await self.writer.write_text(node_card_md_uri(self.config, node.node_id), card.markdown)
        await self.writer.write_json(node_card_json_uri(self.config, node.node_id), card)

    async def _write_source_refs(self, node: WikiNode, source_refs: list[SourceRef]) -> None:
        for source_ref in source_refs:
            await self.writer.write_json(
                f"{node_sources_dir(self.config, node.node_id)}{source_ref.doc_id}.ref.json",
                source_ref,
            )

    async def _write_run_records(self) -> None:
        run_root = run_dir(self.config)
        run_config = {
            "pipeline_version": self.config.pipeline_version,
            "model_config": _redact_sensitive_config(self.config.vlm_config or {}),
            "limits": asdict(self.config.limits),
        }
        await self.writer.write_json(f"{run_root}config.json", run_config)
        await self.writer.write_jsonl(f"{run_root}prompts.jsonl", self.llm.log.prompts)
        await self.writer.write_jsonl(f"{run_root}raw_outputs.jsonl", self.llm.log.raw_outputs)
        await self.writer.write_text(
            f"{run_root}logs.md",
            "# Wiki Run Logs\n\nGeneration completed without pipeline-level errors.\n",
        )


def _source_documents_for_refs(
    source_refs: list[SourceRef],
    source_documents_by_id: dict[str, ResourceDocument],
) -> list[dict]:
    source_documents: list[dict] = []
    for source_ref in source_refs:
        resource_document = source_documents_by_id.get(source_ref.doc_id)
        if not resource_document:
            raise RuntimeError(f"node source ref has no loaded source document: {source_ref.doc_id}")
        if not resource_document.source_sections:
            raise RuntimeError(f"node source ref has no source sections: {source_ref.doc_id}")
        source_documents.append(
            {
                "source_id": source_ref.doc_id,
                "sections": [
                    section.model_dump(mode="json")
                    for section in resource_document.source_sections
                ],
            }
        )
    return source_documents


def _redact_sensitive_config(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if str(key).lower() in _SENSITIVE_CONFIG_KEYS else _redact_sensitive_config(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_config(item) for item in value]
    return value


def _reject_nodes_with_insufficient_refs(
    layer_nodes: list[WikiNode],
    active_nodes: list[WikiNode],
    assignment_result: SourceAssignmentResult,
    min_sources: int,
    *,
    depth: int,
) -> tuple[list[WikiNode], list[WikiNode], SourceAssignmentResult]:
    min_sources = max(1, min_sources)
    is_parent_layer = depth > 1
    unsupported_node_ids = {
        node.node_id
        for node in active_nodes
        if len(assignment_result.source_refs_by_node.get(node.node_id, [])) < min_sources
    }

    updated_layer_nodes = [
        _with_child_node_ids_from_refs(
            node.model_copy(update={"status": "rejected"})
            if node.node_id in unsupported_node_ids
            else node,
            assignment_result,
        )
        for node in layer_nodes
    ]
    if not unsupported_node_ids and not is_parent_layer:
        return layer_nodes, active_nodes, assignment_result

    supported_node_ids = {
        node.node_id
        for node in updated_layer_nodes
        if node.status == "active"
    }
    filtered_assignment_result = assignment_result.model_copy(
        update={
            "source_refs_by_node": {
                node_id: refs
                for node_id, refs in assignment_result.source_refs_by_node.items()
                if node_id in supported_node_ids
            },
        }
    )
    return (
        updated_layer_nodes,
        [node for node in updated_layer_nodes if node.status == "active"],
        filtered_assignment_result,
    )


def _with_child_node_ids_from_refs(
    node: WikiNode,
    assignment_result: SourceAssignmentResult,
) -> WikiNode:
    child_node_ids = [
        ref.doc_id
        for ref in assignment_result.source_refs_by_node.get(node.node_id, [])
        if ref.ref_type == "wiki_node"
    ]
    if not child_node_ids:
        return node
    return node.model_copy(update={"child_node_ids": child_node_ids})


def _assign_parent_node_links(
    nodes: list[WikiNode],
    parent_nodes: list[WikiNode],
) -> list[WikiNode]:
    parent_ids_by_child_id: dict[str, list[str]] = {}
    for parent in parent_nodes:
        for child_node_id in parent.child_node_ids:
            parent_ids_by_child_id.setdefault(child_node_id, []).append(parent.node_id)

    updated_nodes: list[WikiNode] = []
    for node in nodes:
        parent_ids = parent_ids_by_child_id.get(node.node_id)
        if not parent_ids:
            updated_nodes.append(node)
            continue
        updated_nodes.append(
            node.model_copy(
                update={"parent_node_ids": list(dict.fromkeys([*node.parent_node_ids, *parent_ids]))}
            )
        )
    return updated_nodes


def _resource_document_for_node(
    config: WikiConfig,
    context: GeneratedNodeContext,
) -> ResourceDocument:
    return ResourceDocument(
        doc_id=context.node.node_id,
        resource_uri=node_root_uri(config, context.node.node_id),
        title=context.node.title,
        content_or_structure="\n\n".join(document.content for document in context.documents),
        source_sections=[
            {
                "section_uri": node_document_uri(config, context.node.node_id, document.document_id),
                "content": document.content,
            }
            for document in context.documents
        ],
        metadata={"source_type": "wiki_node"},
    )
