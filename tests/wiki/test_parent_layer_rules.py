import pytest

from openviking.wiki.pipeline import _assigned_child_contexts, _reject_nodes_with_insufficient_refs
from openviking.wiki.schemas import (
    GeneratedNodeContext,
    NodeDocument,
    SourceAssignmentResult,
    SourceRef,
    WikiNode,
)


def test_parent_layer_keeps_parent_with_enough_child_nodes():
    parent = _node("parent", depth=3)
    result = _assignment_result("parent", ["child_a", "child_b", "child_c"])

    layer_nodes, active_nodes, filtered_result = _reject_nodes_with_insufficient_refs(
        [parent],
        [parent],
        result,
        min_refs_per_node=1,
        min_child_nodes_per_parent=3,
        child_contexts=[
            _context("child_a", "doc_1", depth=2),
            _context("child_b", "doc_2", depth=2),
            _context("child_c", "doc_3", depth=2),
        ],
        depth=3,
    )

    assert active_nodes[0].status == "active"
    assert layer_nodes[0].child_node_ids == ["child_a", "child_b", "child_c"]
    assert filtered_result.child_node_ids_by_node["parent"] == ["child_a", "child_b", "child_c"]


def test_parent_layer_rejects_parent_with_too_few_child_nodes():
    parent = _node("parent", depth=3)
    result = _assignment_result("parent", ["child_a", "child_b"])

    layer_nodes, active_nodes, _ = _reject_nodes_with_insufficient_refs(
        [parent],
        [parent],
        result,
        min_refs_per_node=1,
        min_child_nodes_per_parent=3,
        child_contexts=[
            _context("child_a", "doc_1", depth=2),
            _context("child_b", "doc_2", depth=2),
        ],
        depth=3,
    )

    assert active_nodes == []
    assert layer_nodes[0].status == "rejected"


def test_parent_layer_requires_assigned_child_nodes_for_document_generation():
    parent = _node("parent", depth=3)
    result = SourceAssignmentResult(source_refs_by_node={})

    with pytest.raises(RuntimeError, match="has no assigned child nodes"):
        _assigned_child_contexts(
            parent,
            result,
            [_context("child_a", "doc_1", depth=2)],
        )


def _assignment_result(node_id: str, child_node_ids: list[str]) -> SourceAssignmentResult:
    refs = [_source_ref(f"doc_{index}") for index in range(1, len(child_node_ids) + 1)]
    return SourceAssignmentResult(
        source_refs_by_node={node_id: refs},
        child_node_ids_by_node={node_id: child_node_ids},
    )


def _context(node_id: str, doc_id: str, depth: int) -> GeneratedNodeContext:
    return GeneratedNodeContext(
        node=_node(node_id, depth=depth),
        node_md=f"# {node_id}",
        documents=[NodeDocument(document_id="0001", content=f"# {node_id}\n\nKnowledge.")],
        source_refs=[_source_ref(doc_id)],
    )


def _node(node_id: str, depth: int) -> WikiNode:
    return WikiNode(
        node_id=node_id,
        title=node_id.replace("_", " ").title(),
        depth=depth,
        scope="Supported topic.",
    )


def _source_ref(doc_id: str) -> SourceRef:
    return SourceRef(
        ref_id=doc_id,
        doc_id=doc_id,
        resource_uri=f"viking://resources/{doc_id}/",
        card_uri=f"viking://wiki/cards/{doc_id}.card.md",
        title=doc_id,
        support_scope="Supports node.",
    )
