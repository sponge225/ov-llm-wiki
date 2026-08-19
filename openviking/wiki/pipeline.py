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
    WikiResourceInput,
    WikiNode,
)
from .uri import (
    card_md_uri,
    cards_dir,
    node_document_uri,
    node_md_uri,
    node_sources_dir,
    run_dir,
    wiki_root,
)
from .writer import WikiVikingFSWriter


logger = logging.getLogger(__name__)


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

    async def run(self, docs: list[ResourceDocument]) -> PipelineArtifacts:
        if not docs:
            raise ValueError("Wiki pipeline requires at least one resource document")

        artifacts = PipelineArtifacts()
        await self.writer.ensure_dirs()

        logger.info("[Wiki] Generating document cards for %d docs", len(docs))
        cards = await self.card_generator.generate(docs)
        logger.info("[Wiki] Generated %d document cards", len(cards))
        return await self._run_from_cards(cards, artifacts, _resource_documents_by_id(docs))

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
        resource_docs = [
            await content_loader.load_document(
                doc,
                mode=card_input_mode,
                max_card_input_chars=max_card_input_chars,
            )
            for doc in docs
        ]
        cards = await self.card_generator.generate(resource_docs)
        source_docs = (
            resource_docs
            if WikiCardInputMode(card_input_mode) == WikiCardInputMode.RAW_CHUNK
            else [
                await content_loader.load_document(
                    doc,
                    mode=WikiCardInputMode.RAW_CHUNK,
                    max_card_input_chars=max_card_input_chars,
                )
                for doc in docs
            ]
        )
        logger.info("[Wiki] Generated %d document cards", len(cards))
        return await self._run_from_cards(cards, artifacts, _resource_documents_by_id(source_docs))

    async def _run_from_cards(
        self,
        cards: list[DocumentCard],
        artifacts: PipelineArtifacts,
        resource_documents_by_id: dict[str, ResourceDocument],
    ) -> PipelineArtifacts:
        artifacts.cards = cards
        await self._write_cards(cards)

        all_nodes: list[WikiNode] = []
        all_source_refs_by_node: dict[str, list[SourceRef]] = {}
        all_unassigned_source_ids: list[str] = []
        all_contexts: list[GeneratedNodeContext] = []
        previous_layer_contexts: list[GeneratedNodeContext] = []

        for depth in range(1, self.config.limits.max_depth + 1):
            source_contexts = previous_layer_contexts
            if depth == 1:
                logger.info("[Wiki] Discovering bottom-layer nodes from %d card topics", len(cards))
                bottom_discovery = await self.node_discovery.discover_bottom_layer(cards, depth=depth)
                layer_nodes = bottom_discovery.nodes
            else:
                logger.info(
                    "[Wiki] Discovering depth=%d parent nodes from %d previous-layer contexts",
                    depth,
                    len(source_contexts),
                )
                parent_discovery = await self.node_discovery.discover_parent_layer(
                    source_contexts,
                    depth=depth,
                )
                layer_nodes = parent_discovery.nodes

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

            if depth == 1:
                logger.info(
                    "[Wiki] Building source refs for %d bottom-layer nodes from topic aggregation",
                    len(active_nodes),
                )
                assignment_result = SourceAssignmentResult(
                    source_refs_by_node=self.source_ref_builder.build_document_refs_by_node(
                        bottom_discovery.source_assignments.assignments,
                        cards,
                    ),
                    unassigned_source_ids=bottom_discovery.source_assignments.unassigned_source_ids,
                )
            else:
                logger.info(
                    "[Wiki] Building source refs for %d parent nodes from child-node aggregation",
                    len(active_nodes),
                )
                child_node_ids_by_node = {
                    item.node_id: item.source_ids
                    for item in parent_discovery.source_assignments.assignments
                }
                assignment_result = SourceAssignmentResult(
                    source_refs_by_node=self.source_ref_builder.build_child_refs_by_node(
                        child_node_ids_by_node,
                        source_contexts,
                    ),
                    child_node_ids_by_node=child_node_ids_by_node,
                    unassigned_source_ids=parent_discovery.source_assignments.unassigned_source_ids,
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
                min_refs_per_node=self.config.limits.min_refs_per_node,
                min_child_nodes_per_parent=self.config.limits.min_child_nodes_per_parent,
                child_contexts=source_contexts if depth > 1 else None,
                required_child_contexts=source_contexts if depth > 1 else None,
            )
            if not active_nodes:
                if depth == 1:
                    raise RuntimeError("bottom layer produced no supported active nodes")
                break
            logger.info("[Wiki] Depth=%d retained %d supported active nodes", depth, len(active_nodes))

            if depth > 1:
                all_nodes = _assign_parent_node_ids(all_nodes, active_nodes)

            all_nodes.extend(layer_nodes)
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
                source_contexts,
                resource_documents_by_id,
                depth=depth,
            )
            all_contexts.extend(layer_contexts)
            logger.info(
                "[Wiki] Depth=%d generated %d node contexts (total=%d)",
                depth,
                len(layer_contexts),
                len(all_contexts),
            )

            artifacts.node_contexts = all_contexts

            if depth >= self.config.limits.max_depth:
                break

            continue_upward = await self.layer_decision_runner.should_continue_upward(
                layer_contexts,
                min_child_nodes_per_parent=self.config.limits.min_child_nodes_per_parent,
            )
            logger.info("[Wiki] Depth=%d continue_upward=%s", depth, continue_upward)
            if not continue_upward:
                break
            previous_layer_contexts = layer_contexts

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
        all_contexts: list[GeneratedNodeContext],
        resource_documents_by_id: dict[str, ResourceDocument],
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
                    all_contexts,
                    resource_documents_by_id,
                    depth=depth,
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
        all_contexts: list[GeneratedNodeContext],
        resource_documents_by_id: dict[str, ResourceDocument],
        *,
        depth: int,
    ) -> GeneratedNodeContext:
        source_refs = assignment_result.source_refs_by_node.get(node.node_id)
        if not source_refs:
            raise RuntimeError(f"active node {node.node_id} has no source refs")

        await self.writer.ensure_dirs([node.node_id])
        await self._write_source_refs(node, source_refs)

        node_md = await self.content_generator.generate_node_md(node)
        await self.writer.write_text(node_md_uri(self.config, node.node_id), node_md)

        if depth == 1:
            source_documents = _source_documents_for_resource_refs(source_refs, resource_documents_by_id)
            documents = await self.content_generator.generate_node_documents(
                node,
                source_documents,
            )
        else:
            assigned_child_contexts = _assigned_child_contexts(
                node,
                assignment_result,
                all_contexts,
            )
            child_nodes = _child_node_document_inputs(assigned_child_contexts)
            documents = await self.content_generator.generate_parent_node_documents(
                node,
                child_nodes,
            )
        for document in documents:
            await self.writer.write_text(
                node_document_uri(self.config, node.node_id, document.document_id),
                document.content,
            )

        context = GeneratedNodeContext(
            node=node,
            node_md=node_md,
            documents=documents,
            source_refs=source_refs,
        )
        return context

    async def _write_cards(self, cards: list[DocumentCard]) -> None:
        for card in cards:
            await self.writer.write_text(card_md_uri(self.config, card.doc_id), card.markdown)
            await self.writer.write_json(f"{cards_dir(self.config)}{card.doc_id}.card.json", card)

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
            "model_config": self.config.vlm_config or {},
            "limits": asdict(self.config.limits),
        }
        await self.writer.write_json(f"{run_root}config.json", run_config)
        await self.writer.write_jsonl(f"{run_root}prompts.jsonl", self.llm.log.prompts)
        await self.writer.write_jsonl(f"{run_root}raw_outputs.jsonl", self.llm.log.raw_outputs)
        await self.writer.write_text(
            f"{run_root}logs.md",
            "# Wiki Run Logs\n\nGeneration completed without pipeline-level errors.\n",
        )


def _resource_documents_by_id(docs: list[ResourceDocument]) -> dict[str, ResourceDocument]:
    return {doc.doc_id: doc for doc in docs}


def _source_documents_for_resource_refs(
    source_refs: list[SourceRef],
    resource_documents_by_id: dict[str, ResourceDocument],
) -> list[dict]:
    source_documents: list[dict] = []
    for source_ref in source_refs:
        resource_document = resource_documents_by_id.get(source_ref.doc_id)
        if not resource_document:
            raise RuntimeError(f"node source ref has no loaded resource document: {source_ref.doc_id}")
        source_documents.append(
            {
                "doc_id": source_ref.doc_id,
                "sections": _sections_from_content(
                    resource_document.content_or_structure,
                    fallback_uri=source_ref.resource_uri,
                ),
            }
        )
    return source_documents


def _child_node_document_inputs(
    child_contexts: list[GeneratedNodeContext],
) -> list[dict]:
    child_nodes: list[dict] = []

    for context in child_contexts:
        child_nodes.append(
            {
                "title": context.node.title,
                "scope": context.node.scope,
                "documents": [
                    {
                        "content": document.content,
                    }
                    for document in context.documents
                ],
            }
        )

    return child_nodes


def _sections_from_content(content: str, fallback_uri: str) -> list[dict]:
    content = str(content or "").strip()
    if not content:
        raise RuntimeError(f"source document has no section content: {fallback_uri}")

    sections: list[dict] = []
    current_uri = ""
    current_lines: list[str] = []
    in_content = False

    for line in content.splitlines():
        if line.startswith("URI: "):
            text = "\n".join(current_lines).strip()
            if current_uri and text:
                sections.append({"section_uri": current_uri, "content": text})
            current_uri = line[5:].strip()
            current_lines = []
            in_content = False
            continue
        if line == "Content:":
            in_content = True
            continue
        if in_content:
            current_lines.append(line)

    text = "\n".join(current_lines).strip()
    if current_uri and text:
        sections.append({"section_uri": current_uri, "content": text})
    if sections:
        return sections
    return [{"section_uri": fallback_uri, "content": content}]


def _reject_nodes_with_insufficient_refs(
    layer_nodes: list[WikiNode],
    active_nodes: list[WikiNode],
    assignment_result: SourceAssignmentResult,
    min_refs_per_node: int,
    min_child_nodes_per_parent: int,
    child_contexts: list[GeneratedNodeContext] | None = None,
    required_child_contexts: list[GeneratedNodeContext] | None = None,
) -> tuple[list[WikiNode], list[WikiNode], SourceAssignmentResult]:
    min_refs = max(1, min_refs_per_node)
    min_child_nodes = max(1, min_child_nodes_per_parent)
    child_doc_ids_by_node = {
        context.node.node_id: {ref.doc_id for ref in context.source_refs}
        for context in child_contexts or []
    }
    required_child_node_ids = {context.node.node_id for context in required_child_contexts or []}
    unsupported_node_ids = {
        node.node_id
        for node in active_nodes
        if (
            _assigned_child_node_count(node.node_id, assignment_result, child_doc_ids_by_node) < min_child_nodes
            or not _has_required_child_node(
                node.node_id,
                assignment_result,
                child_doc_ids_by_node,
                required_child_node_ids,
            )
            if child_doc_ids_by_node
            else len(assignment_result.source_refs_by_node.get(node.node_id, [])) < min_refs
        )
    }

    updated_layer_nodes = [
        _with_assigned_child_node_ids(
            _reject_node_for_insufficient_refs(node)
            if node.node_id in unsupported_node_ids
            else node,
            assignment_result,
            child_doc_ids_by_node,
        )
        for node in layer_nodes
    ]
    if not unsupported_node_ids and not child_doc_ids_by_node:
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
            "child_node_ids_by_node": {
                node_id: child_node_ids
                for node_id, child_node_ids in assignment_result.child_node_ids_by_node.items()
                if node_id in supported_node_ids
            },
        }
    )
    return (
        updated_layer_nodes,
        [node for node in updated_layer_nodes if node.status == "active"],
        filtered_assignment_result,
    )


def _with_assigned_child_node_ids(
    node: WikiNode,
    assignment_result: SourceAssignmentResult,
    child_doc_ids_by_node: dict[str, set[str]],
) -> WikiNode:
    if not child_doc_ids_by_node:
        return node
    child_node_ids = _assigned_child_node_ids(node.node_id, assignment_result, child_doc_ids_by_node)
    return node.model_copy(update={"child_node_ids": child_node_ids})


def _assigned_child_contexts(
    node: WikiNode,
    assignment_result: SourceAssignmentResult,
    child_contexts: list[GeneratedNodeContext],
) -> list[GeneratedNodeContext]:
    child_node_ids = set(assignment_result.child_node_ids_by_node.get(node.node_id) or node.child_node_ids)
    if not child_node_ids:
        return child_contexts
    return [context for context in child_contexts if context.node.node_id in child_node_ids]


def _assign_parent_node_ids(
    nodes: list[WikiNode],
    parent_nodes: list[WikiNode],
) -> list[WikiNode]:
    parent_by_child_id = {
        child_node_id: parent.node_id
        for parent in parent_nodes
        for child_node_id in parent.child_node_ids
    }
    return [
        node.model_copy(update={"parent_node_id": parent_by_child_id[node.node_id]})
        if node.node_id in parent_by_child_id
        else node
        for node in nodes
    ]


def _assigned_child_node_count(
    node_id: str,
    assignment_result: SourceAssignmentResult,
    child_doc_ids_by_node: dict[str, set[str]],
) -> int:
    return len(_assigned_child_node_ids(node_id, assignment_result, child_doc_ids_by_node))


def _assigned_child_node_ids(
    node_id: str,
    assignment_result: SourceAssignmentResult,
    child_doc_ids_by_node: dict[str, set[str]],
) -> list[str]:
    explicit_child_node_ids = assignment_result.child_node_ids_by_node.get(node_id, [])
    if explicit_child_node_ids:
        return explicit_child_node_ids
    return [
        ref.doc_id
        for ref in assignment_result.source_refs_by_node.get(node_id, [])
        if ref.ref_type == "wiki_node" and ref.doc_id in child_doc_ids_by_node
    ]


def _has_required_child_node(
    node_id: str,
    assignment_result: SourceAssignmentResult,
    child_doc_ids_by_node: dict[str, set[str]],
    required_child_node_ids: set[str],
) -> bool:
    if not required_child_node_ids:
        return True
    assigned_child_node_ids = set(
        _assigned_child_node_ids(node_id, assignment_result, child_doc_ids_by_node)
    )
    return bool(assigned_child_node_ids & required_child_node_ids)


def _reject_node_for_insufficient_refs(node: WikiNode) -> WikiNode:
    return node.model_copy(update={"status": "rejected"})
