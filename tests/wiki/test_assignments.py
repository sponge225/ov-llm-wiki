import pytest

from openviking.wiki.assignments import SourceRefBuilder
from openviking.wiki.config import WikiConfig
from openviking.wiki.schemas import (
    DocumentCard,
    SourceAssignmentItem,
)


def test_source_ref_builder_binds_wiki_node_cards():
    builder = SourceRefBuilder(WikiConfig())

    refs_by_node = builder.build_refs_by_node(
        [
            SourceAssignmentItem(
                node_id="parent_node",
                source_ids=["child_a", "child_b"],
                support_scope="Children support parent.",
            )
        ],
        [_node_card("child_a"), _node_card("child_b")],
    )

    refs = refs_by_node["parent_node"]
    assert [ref.doc_id for ref in refs] == ["child_a", "child_b"]
    assert [ref.ref_type for ref in refs] == ["wiki_node", "wiki_node"]
    assert [ref.resource_uri for ref in refs] == [
        "viking://wiki/nodes/child_a/",
        "viking://wiki/nodes/child_b/",
    ]
    assert [ref.card_uri for ref in refs] == [
        "viking://wiki/nodes/child_a/card.md",
        "viking://wiki/nodes/child_b/card.md",
    ]


def test_source_ref_builder_binds_document_source_ids():
    builder = SourceRefBuilder(WikiConfig())

    refs_by_node = builder.build_refs_by_node(
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


def test_source_ref_builder_rejects_unknown_source_ids():
    builder = SourceRefBuilder(WikiConfig())

    with pytest.raises(RuntimeError, match="unknown doc_id values"):
        builder.build_refs_by_node(
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
        summary="QA summary.",
        main_points=["QA"],
        candidate_topics=["question answering"],
    )


def _node_card(node_id: str) -> DocumentCard:
    return DocumentCard(
        doc_id=node_id,
        resource_uri=f"viking://wiki/nodes/{node_id}/",
        title=node_id.replace("_", " ").title(),
        summary="Node summary.",
        main_points=["Node point"],
        candidate_topics=["Parent topic"],
    )
