"""Wiki MVP batch generation orchestrator."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from .assignments import SourceAssignmentRunner
from .cards import DocumentCardGenerator
from .config import WikiMVPConfig
from .content_loader import WikiCardInputMode, WikiContentLoader
from .documents import NodeContentGenerator
from .evidence import EvidenceRunner
from .llm import WikiLLMRunner
from .nodes import NodeDiscoveryRunner
from .profile import ResourceSpaceProfiler
from .schemas import (
    DocumentCard,
    GeneratedNodeContext,
    NodeManifest,
    PipelineArtifacts,
    ResourceDocument,
    SourceAssignment,
    SourceAssignmentResult,
    SourceRef,
    WikiResourceInput,
    WikiManifest,
    WikiNode,
)
from .uri import (
    card_json_uri,
    card_md_uri,
    cards_dir,
    logs_md_uri,
    manifest_uri,
    node_document_uri,
    node_documents_dir,
    node_evidence_uri,
    node_manifest_uri,
    node_md_uri,
    node_root_uri,
    node_source_ref_uri,
    node_sources_dir,
    nodes_json_uri,
    profile_uri,
    prompts_log_uri,
    raw_outputs_log_uri,
    run_config_uri,
    source_assignments_uri,
)
from .writer import WikiVikingFSWriter


logger = logging.getLogger(__name__)


class WikiMVPPipeline:
    def __init__(
        self,
        client: Any,
        config: WikiMVPConfig | None = None,
        llm: WikiLLMRunner | None = None,
    ):
        self.config = config or WikiMVPConfig()
        self.llm = llm or WikiLLMRunner(vlm_config=self.config.vlm_config)
        self.writer = WikiVikingFSWriter(client, self.config)
        self.card_generator = DocumentCardGenerator(
            self.llm,
            max_concurrent=self.config.limits.max_concurrent_cards,
        )
        self.profiler = ResourceSpaceProfiler(self.llm)
        self.node_discovery = NodeDiscoveryRunner(self.llm, self.config)
        self.assignment_runner = SourceAssignmentRunner(self.llm, self.config)
        self.content_generator = NodeContentGenerator(self.llm)
        self.evidence_runner = EvidenceRunner(self.llm)

    async def run(self, docs: list[ResourceDocument]) -> PipelineArtifacts:
        if not docs:
            raise ValueError("Wiki MVP pipeline requires at least one resource document")

        artifacts = PipelineArtifacts()
        await self.writer.ensure_dirs()

        logger.info("[WikiMVP] Generating document cards for %d docs", len(docs))
        cards = await self.card_generator.generate(docs)
        logger.info("[WikiMVP] Generated %d document cards", len(cards))
        return await self._run_from_cards(cards, artifacts)

    async def run_from_inputs(
        self,
        docs: list[WikiResourceInput],
        *,
        content_loader: WikiContentLoader,
        card_input_mode: WikiCardInputMode | str = WikiCardInputMode.SUMMARY,
        max_card_input_chars: int = 20000,
    ) -> PipelineArtifacts:
        if not docs:
            raise ValueError("Wiki MVP pipeline requires at least one resource document")

        artifacts = PipelineArtifacts()
        await self.writer.ensure_dirs()

        logger.info(
            "[WikiMVP] Generating document cards for %d docs from %s inputs",
            len(docs),
            card_input_mode,
        )
        cards = await self.card_generator.generate_from_inputs(
            docs,
            content_loader=content_loader,
            card_input_mode=card_input_mode,
            max_card_input_chars=max_card_input_chars,
        )
        logger.info("[WikiMVP] Generated %d document cards", len(cards))
        return await self._run_from_cards(cards, artifacts)

    async def _run_from_cards(
        self,
        cards: list[DocumentCard],
        artifacts: PipelineArtifacts,
    ) -> PipelineArtifacts:
        artifacts.cards = cards
        await self._write_cards(cards)

        logger.info("[WikiMVP] Generating resource-space profile")
        profile = await self.profiler.generate(cards)
        artifacts.profile = profile
        await self.writer.write_json(profile_uri(self.config), profile)
        logger.info("[WikiMVP] Resource-space profile generated")

        all_nodes: list[WikiNode] = []
        all_assignments: list[SourceAssignment] = []
        all_contexts: list[GeneratedNodeContext] = []
        previous_layer_contexts: list[GeneratedNodeContext] = []

        for depth in range(1, self.config.limits.max_depth + 1):
            source_contexts = previous_layer_contexts
            if depth == 1:
                logger.info("[WikiMVP] Discovering bottom-layer nodes from %d cards", len(cards))
                layer_nodes = await self.node_discovery.discover_bottom_layer(profile, cards, depth=depth)
            else:
                logger.info(
                    "[WikiMVP] Discovering depth=%d parent nodes from %d previous-layer contexts",
                    depth,
                    len(source_contexts),
                )
                layer_nodes = await self.node_discovery.discover_parent_layer(
                    profile,
                    source_contexts,
                    depth=depth,
                )

            active_nodes = [node for node in layer_nodes if node.status == "active"]
            logger.info(
                "[WikiMVP] Depth=%d discovered %d nodes (%d active)",
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
                    "[WikiMVP] Assigning %d bottom-layer nodes to %d cards",
                    len(active_nodes),
                    len(cards),
                )
                assignment_result = await self.assignment_runner.assign_bottom_layer(active_nodes, cards)
            else:
                logger.info(
                    "[WikiMVP] Assigning %d parent nodes to %d child contexts",
                    len(active_nodes),
                    len(source_contexts),
                )
                assignment_result = await self.assignment_runner.assign_parent_layer(
                    active_nodes,
                    source_contexts,
                )
            logger.info(
                "[WikiMVP] Depth=%d produced %d source assignments",
                depth,
                len(assignment_result.assignments),
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
            logger.info("[WikiMVP] Depth=%d retained %d supported active nodes", depth, len(active_nodes))

            if depth > 1:
                all_nodes = _assign_parent_node_ids(all_nodes, active_nodes)

            all_nodes.extend(layer_nodes)
            artifacts.nodes = all_nodes
            await self.writer.write_json(nodes_json_uri(self.config), {"nodes": all_nodes})

            all_assignments.extend(assignment_result.assignments)
            artifacts.source_assignments = all_assignments
            await self.writer.write_json(
                source_assignments_uri(self.config),
                {
                    "assignments": all_assignments,
                    "unassigned_doc_ids": assignment_result.unassigned_doc_ids,
                },
            )

            layer_contexts = await self._generate_layer_contexts(
                active_nodes,
                assignment_result,
                cards,
                source_contexts,
                depth=depth,
            )
            all_contexts.extend(layer_contexts)
            logger.info(
                "[WikiMVP] Depth=%d generated %d node contexts (total=%d)",
                depth,
                len(layer_contexts),
                len(all_contexts),
            )

            artifacts.node_contexts = all_contexts

            if depth >= self.config.limits.max_depth:
                break

            continue_upward = await self.evidence_runner.should_continue_upward(
                profile,
                layer_contexts,
                min_child_nodes_per_parent=self.config.limits.min_child_nodes_per_parent,
            )
            logger.info("[WikiMVP] Depth=%d continue_upward=%s", depth, continue_upward)
            if not continue_upward:
                break
            previous_layer_contexts = layer_contexts

        artifacts.manifest = await self._write_manifest(all_contexts)
        await self._write_run_records()
        logger.info(
            "[WikiMVP] Completed wiki generation: cards=%d nodes=%d contexts=%d wiki_root=%s",
            len(artifacts.cards),
            len(artifacts.nodes),
            len(artifacts.node_contexts),
            self.config.wiki_root_uri,
        )
        return artifacts

    async def _generate_layer_contexts(
        self,
        active_nodes: list[WikiNode],
        assignment_result: SourceAssignmentResult,
        cards: list[DocumentCard],
        all_contexts: list[GeneratedNodeContext],
        *,
        depth: int,
    ) -> list[GeneratedNodeContext]:
        max_concurrent = max(1, self.config.limits.max_concurrent_nodes)
        sem = asyncio.Semaphore(max_concurrent)
        contexts: list[GeneratedNodeContext | None] = [None] * len(active_nodes)
        logger.info(
            "[WikiMVP] Depth=%d generating %d node contexts with max_concurrent=%d",
            depth,
            len(active_nodes),
            max_concurrent,
        )

        async def _generate_one(index: int, node: WikiNode) -> None:
            async with sem:
                logger.info("[WikiMVP] Depth=%d generating node context: %s", depth, node.node_id)
                contexts[index] = await self._generate_node_context(
                    node,
                    assignment_result,
                    cards,
                    all_contexts,
                    depth=depth,
                )
                logger.info("[WikiMVP] Depth=%d generated node context: %s", depth, node.node_id)

        await asyncio.gather(*[_generate_one(index, node) for index, node in enumerate(active_nodes)])
        if any(context is None for context in contexts):
            raise RuntimeError("node context generation did not produce all contexts")
        return [context for context in contexts if context is not None]

    async def _generate_node_context(
        self,
        node: WikiNode,
        assignment_result: SourceAssignmentResult,
        cards: list[DocumentCard],
        all_contexts: list[GeneratedNodeContext],
        *,
        depth: int,
    ) -> GeneratedNodeContext:
        source_refs = assignment_result.source_refs_by_node.get(node.node_id)
        if not source_refs:
            raise RuntimeError(f"active node {node.node_id} has no source refs")

        await self.writer.ensure_dirs([node.node_id])
        await self._write_source_refs(node, source_refs)

        node_md = await self.content_generator.generate_node_md(node, source_refs)
        await self.writer.write_text(node_md_uri(self.config, node.node_id), node_md)

        node_cards = _cards_for_refs(cards, source_refs)
        if depth == 1:
            documents = await self.content_generator.generate_node_documents(
                node,
                source_refs,
                cards=node_cards,
            )
        else:
            assigned_child_contexts = _assigned_child_contexts(
                node,
                assignment_result,
                all_contexts,
            )
            documents = await self.content_generator.generate_node_documents(
                node,
                source_refs,
                child_contexts=assigned_child_contexts,
            )
        for document in documents:
            await self.writer.write_text(
                node_document_uri(self.config, node.node_id, document.document_id),
                document.content,
            )

        evidence = await self.evidence_runner.generate_node_evidence(
            node,
            documents,
            source_refs,
            node_cards,
        )
        await self.writer.write_jsonl(node_evidence_uri(self.config, node.node_id), evidence)

        context = GeneratedNodeContext(
            node=node,
            node_md=node_md,
            documents=documents,
            evidence=evidence,
            source_refs=source_refs,
        )
        await self._write_node_manifest(context)
        return context

    async def _write_cards(self, cards: list[DocumentCard]) -> None:
        for card in cards:
            await self.writer.write_text(card_md_uri(self.config, card.doc_id), card.markdown)
            await self.writer.write_json(card_json_uri(self.config, card.doc_id), card)

    async def _write_source_refs(self, node: WikiNode, source_refs: list[SourceRef]) -> None:
        for source_ref in source_refs:
            await self.writer.write_json(
                node_source_ref_uri(self.config, node.node_id, source_ref.doc_id),
                source_ref,
            )

    async def _write_node_manifest(self, context: GeneratedNodeContext) -> None:
        node = context.node
        manifest = NodeManifest(
            node_id=node.node_id,
            title=node.title,
            node_uri=node_root_uri(self.config, node.node_id),
            node_md=node_md_uri(self.config, node.node_id),
            documents_dir=node_documents_dir(self.config, node.node_id),
            document_uris=[
                node_document_uri(self.config, node.node_id, document.document_id)
                for document in context.documents
            ],
            evidence_jsonl=node_evidence_uri(self.config, node.node_id),
            sources_dir=node_sources_dir(self.config, node.node_id),
            num_source_refs=len(context.source_refs),
            num_node_documents=len(context.documents),
            num_claims=len(context.evidence),
        )
        await self.writer.write_json(node_manifest_uri(self.config, node.node_id), manifest)

    async def _write_manifest(self, contexts: list[GeneratedNodeContext]) -> WikiManifest:
        manifest = WikiManifest(
            dataset=self.config.dataset,
            split=self.config.split,
            pipeline_version=self.config.pipeline_version,
            resource_root_uri=self.config.resource_root_uri,
            wiki_root=self.config.wiki_root_uri,
            profile_uri=profile_uri(self.config),
            cards_dir=cards_dir(self.config),
            node_uris=[node_root_uri(self.config, context.node.node_id) for context in contexts],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await self.writer.write_json(manifest_uri(self.config), manifest)
        return manifest

    async def _write_run_records(self) -> None:
        run_config = {
            "pipeline_version": self.config.pipeline_version,
            "model_config": self.config.vlm_config or {},
            "limits": asdict(self.config.limits),
        }
        await self.writer.write_json(run_config_uri(self.config), run_config)
        await self.writer.write_jsonl(prompts_log_uri(self.config), self.llm.log.prompts)
        await self.writer.write_jsonl(raw_outputs_log_uri(self.config), self.llm.log.raw_outputs)
        await self.writer.write_text(
            logs_md_uri(self.config),
            "# Wiki MVP Run Logs\n\nGeneration completed without pipeline-level errors.\n",
        )


def _cards_for_refs(cards: list[DocumentCard], source_refs: list[SourceRef]) -> list[DocumentCard]:
    wanted = {ref.doc_id for ref in source_refs}
    return [card for card in cards if card.doc_id in wanted]


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
            _reject_node_for_insufficient_refs(
                node,
                actual_refs=(
                    _assigned_child_node_count(node.node_id, assignment_result, child_doc_ids_by_node)
                    if child_doc_ids_by_node
                    else len(assignment_result.source_refs_by_node.get(node.node_id, []))
                ),
                min_refs=min_child_nodes if child_doc_ids_by_node else min_refs,
                unit_name="child nodes" if child_doc_ids_by_node else "source refs",
            )
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
            "assignments": [
                assignment
                for assignment in assignment_result.assignments
                if assignment.node_id in supported_node_ids
            ],
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
    assigned_doc_ids = {
        assignment.doc_id
        for assignment in assignment_result.assignments
        if assignment.node_id == node_id
    }
    return [
        child_node_id
        for child_node_id, child_doc_ids in child_doc_ids_by_node.items()
        if assigned_doc_ids & child_doc_ids
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


def _reject_node_for_insufficient_refs(
    node: WikiNode,
    actual_refs: int,
    min_refs: int,
    unit_name: str = "source refs",
) -> WikiNode:
    return node.model_copy(
        update={
            "status": "rejected",
            "promotion_decision": "reject",
            "promotion_reasons": [
                f"assigned {unit_name} {actual_refs} lower than required {min_refs}"
            ],
        }
    )
