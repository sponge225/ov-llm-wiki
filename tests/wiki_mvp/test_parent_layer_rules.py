from openviking.wiki_mvp.pipeline import _reject_nodes_with_insufficient_refs
from openviking.wiki_mvp.schemas import (
    GeneratedNodeContext,
    NodeDocument,
    SourceAssignmentResult,
    SourceRef,
    WikiNode,
)


def test_parent_layer_allows_mixed_lower_depth_children_with_previous_layer_child():
    parent = _node("parent", depth=3)
    result = _assignment_result("parent", ["bottom_a", "middle_a", "bottom_b"])

    layer_nodes, active_nodes, filtered_result = _reject_nodes_with_insufficient_refs(
        [parent],
        [parent],
        result,
        min_refs_per_node=1,
        min_child_nodes_per_parent=3,
        child_contexts=[
            _context("bottom_a", "doc_1", depth=1),
            _context("middle_a", "doc_2", depth=2),
            _context("bottom_b", "doc_3", depth=1),
        ],
        required_child_contexts=[_context("middle_a", "doc_2", depth=2)],
    )

    assert active_nodes[0].status == "active"
    assert layer_nodes[0].child_node_ids == ["bottom_a", "middle_a", "bottom_b"]
    assert filtered_result.child_node_ids_by_node["parent"] == ["bottom_a", "middle_a", "bottom_b"]


def test_parent_layer_rejects_parent_without_previous_layer_child():
    parent = _node("parent", depth=3)
    result = _assignment_result("parent", ["bottom_a", "bottom_b", "bottom_c"])

    layer_nodes, active_nodes, _ = _reject_nodes_with_insufficient_refs(
        [parent],
        [parent],
        result,
        min_refs_per_node=1,
        min_child_nodes_per_parent=3,
        child_contexts=[
            _context("bottom_a", "doc_1", depth=1),
            _context("bottom_b", "doc_2", depth=1),
            _context("bottom_c", "doc_3", depth=1),
            _context("middle_a", "doc_4", depth=2),
        ],
        required_child_contexts=[_context("middle_a", "doc_4", depth=2)],
    )

    assert active_nodes == []
    assert layer_nodes[0].status == "rejected"


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
