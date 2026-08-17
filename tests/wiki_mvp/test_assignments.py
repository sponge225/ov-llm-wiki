import pytest

from openviking.wiki_mvp.assignments import SourceRefBuilder
from openviking.wiki_mvp.config import WikiMVPConfig
from openviking.wiki_mvp.schemas import (
    DocumentCard,
    GeneratedNodeContext,
    NodeDocument,
    SourceAssignmentItem,
    SourceRef,
    WikiNode,
)


def test_source_ref_builder_binds_child_node_ids_without_inheriting_source_refs():
    builder = SourceRefBuilder(WikiMVPConfig())

    refs_by_node = builder.build_child_refs_by_node(
        {"parent_node": ["child_a", "child_b"]},
        [
            _context("child_a", "OARW_1"),
            _context("child_b", "OARW_2"),
        ],
    )

    refs = refs_by_node["parent_node"]
    assert [ref.doc_id for ref in refs] == ["child_a", "child_b"]
    assert [ref.ref_type for ref in refs] == ["wiki_node", "wiki_node"]
    assert [ref.resource_uri for ref in refs] == [
        "viking://wiki/nodes/child_a/",
        "viking://wiki/nodes/child_b/",
    ]


def test_source_ref_builder_binds_document_source_ids():
    builder = SourceRefBuilder(WikiMVPConfig())

    refs_by_node = builder.build_document_refs_by_node(
        [
            SourceAssignmentItem(
                node_id="question_answering",
                source_ids=["OARW_1"],
                support_scope="QA source supports the node.",
            )
        ],
        [_card("OARW_1")],
    )

    refs = refs_by_node["question_answering"]
    assert [ref.doc_id for ref in refs] == ["OARW_1"]
    assert refs[0].resource_uri == "viking://resources/OARW_1/"
    assert refs[0].support_scope == "QA source supports the node."


def test_source_ref_builder_rejects_unknown_child_node_ids():
    builder = SourceRefBuilder(WikiMVPConfig())

    with pytest.raises(RuntimeError, match="unknown child node ids"):
        builder.build_child_refs_by_node(
            {"parent_node": ["child_a", "invented_child"]},
            [_context("child_a", "OARW_1")],
        )


def test_source_ref_builder_rejects_unknown_document_source_ids():
    builder = SourceRefBuilder(WikiMVPConfig())

    with pytest.raises(RuntimeError, match="unknown doc_id values"):
        builder.build_document_refs_by_node(
            [
                SourceAssignmentItem(
                    node_id="question_answering",
                    source_ids=["missing_doc"],
                    support_scope="QA source supports the node.",
                )
            ],
            [_card("OARW_1")],
        )


def _card(doc_id: str) -> DocumentCard:
    return DocumentCard(
        doc_id=doc_id,
        resource_uri=f"viking://resources/{doc_id}/",
        title=f"Paper {doc_id}",
        source_type="academic_paper_full_text",
        summary="QA summary.",
        main_points=["QA"],
        candidate_topics=["question answering"],
        evidence_anchors=[
            {
                "section_title": "Abstract",
                "section_uri": f"viking://resources/{doc_id}/abstract",
                "summary": "QA evidence.",
            }
        ],
    )


def _context(node_id: str, doc_id: str) -> GeneratedNodeContext:
    source_ref = SourceRef(
        ref_id=doc_id,
        doc_id=doc_id,
        resource_uri=f"viking://resources/{doc_id}/",
        card_uri=f"viking://wiki/cards/{doc_id}.card.md",
        title=f"Paper {doc_id}",
        support_scope="Supports child node.",
    )
    return GeneratedNodeContext(
        node=WikiNode(
            node_id=node_id,
            title=node_id.replace("_", " ").title(),
            depth=1,
            scope="Supported topic.",
        ),
        node_md=f"# {node_id}",
        documents=[NodeDocument(document_id="0001", content=f"# {node_id}\n\nKnowledge.")],
        source_refs=[source_ref],
    )
