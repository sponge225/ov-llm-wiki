import pytest

from openviking.wiki_mvp.assignments import SourceAssignmentRunner
from openviking.wiki_mvp.config import WikiMVPConfig
from openviking.wiki_mvp.llm import WikiLLMRunner
from openviking.wiki_mvp.schemas import (
    EvidenceClaim,
    EvidenceRef,
    GeneratedNodeContext,
    NodeDocument,
    SourceRef,
    WikiNode,
)

from .fakes import FakeVLM


@pytest.mark.asyncio
async def test_parent_assignment_binds_child_node_ids_without_inheriting_source_refs():
    runner = SourceAssignmentRunner(
        WikiLLMRunner(
            FakeVLM(
                [
                    {
                        "assignments": [
                            {
                                "node_id": "parent_node",
                                "child_node_ids": ["child_a", "child_b"],
                                "support_scope": "Related child nodes support the parent.",
                            }
                        ],
                        "unassigned_doc_ids": [],
                    }
                ]
            )
        ),
        WikiMVPConfig(),
    )

    result = await runner.assign_parent_layer(
        [_node("parent_node", depth=2)],
        [
            _context("child_a", "OARW_1"),
            _context("child_b", "OARW_2"),
        ],
    )

    assert result.assignments == []
    assert [ref.doc_id for ref in result.source_refs_by_node["parent_node"]] == ["child_a", "child_b"]
    assert [ref.ref_type for ref in result.source_refs_by_node["parent_node"]] == ["wiki_node", "wiki_node"]
    assert result.child_node_ids_by_node["parent_node"] == ["child_a", "child_b"]


@pytest.mark.asyncio
async def test_parent_assignment_does_not_treat_parent_node_id_as_source_id():
    runner = SourceAssignmentRunner(
        WikiLLMRunner(
            FakeVLM(
                [
                    {
                        "assignments": [
                            {
                                "node_id": "child_a",
                                "child_node_ids": ["child_b", "child_c"],
                                "support_scope": "Related child nodes support the parent.",
                            }
                        ],
                        "unassigned_doc_ids": [],
                    }
                ]
            )
        ),
        WikiMVPConfig(),
    )

    result = await runner.assign_parent_layer(
        [_node("child_a", depth=2)],
        [
            _context("child_a", "OARW_1"),
            _context("child_b", "OARW_2"),
            _context("child_c", "OARW_3"),
        ],
    )

    assert result.assignments == []
    assert result.child_node_ids_by_node["child_a"] == ["child_b", "child_c"]


@pytest.mark.asyncio
async def test_parent_assignment_drops_unknown_child_node_ids():
    runner = SourceAssignmentRunner(
        WikiLLMRunner(
            FakeVLM(
                [
                    {
                        "assignments": [
                            {
                                "node_id": "parent_node",
                                "child_node_ids": ["child_a", "invented_child"],
                                "support_scope": "Related child nodes support the parent.",
                            }
                        ],
                        "unassigned_doc_ids": [],
                    }
                ]
            )
        ),
        WikiMVPConfig(),
    )

    result = await runner.assign_parent_layer(
        [_node("parent_node", depth=2)],
        [
            _context("child_a", "OARW_1"),
        ],
    )

    assert result.assignments == []
    assert [ref.doc_id for ref in result.source_refs_by_node["parent_node"]] == ["child_a"]
    assert result.child_node_ids_by_node["parent_node"] == ["child_a"]


@pytest.mark.asyncio
async def test_parent_assignment_accepts_child_source_assignments_shape():
    runner = SourceAssignmentRunner(
        WikiLLMRunner(
            FakeVLM(
                [
                    {
                        "assignments": [
                            {
                                "node_id": "parent_node",
                                "child_source_assignments": [
                                    {"child_node_id": "child_a", "supporting_source_refs": ["OARW_1"]},
                                    {"child_node_id": "child_b", "supporting_source_refs": ["OARW_2"]},
                                ],
                                "supporting_source_refs": ["OARW_1", "OARW_2"],
                                "coverage_validation": "ignored",
                            }
                        ],
                    }
                ]
            )
        ),
        WikiMVPConfig(),
    )

    result = await runner.assign_parent_layer(
        [_node("parent_node", depth=2)],
        [
            _context("child_a", "OARW_1"),
            _context("child_b", "OARW_2"),
        ],
    )

    assert result.assignments == []
    assert result.child_node_ids_by_node["parent_node"] == ["child_a", "child_b"]
    assert [ref.ref_type for ref in result.source_refs_by_node["parent_node"]] == ["wiki_node", "wiki_node"]


@pytest.mark.asyncio
async def test_parent_assignment_does_not_accept_raw_doc_ids_as_child_sources():
    runner = SourceAssignmentRunner(
        WikiLLMRunner(
            FakeVLM(
                [
                    {
                        "assignments": [
                            {
                                "node_id": "parent_node",
                                "doc_ids": ["OARW_1"],
                                "support_scope": "Raw document ids are not valid parent-layer sources.",
                            }
                        ],
                        "unassigned_doc_ids": [],
                    }
                ]
            )
        ),
        WikiMVPConfig(),
    )

    result = await runner.assign_parent_layer(
        [_node("parent_node", depth=2)],
        [
            _context("child_a", "OARW_1"),
        ],
    )

    assert result.assignments == []
    assert result.source_refs_by_node == {}
    assert result.child_node_ids_by_node == {}


@pytest.mark.asyncio
async def test_bottom_assignment_expands_single_doc_id_and_sanitizes_node_id():
    from openviking.wiki_mvp.schemas import DocumentCard

    runner = SourceAssignmentRunner(
        WikiLLMRunner(
            FakeVLM(
                [
                    {
                        "assignments": [
                            {
                                "node_id": "Question Answering",
                                "doc_id": "OARW_1",
                            }
                        ],
                        "unassigned_doc_ids": [],
                    }
                ]
            )
        ),
        WikiMVPConfig(),
    )

    result = await runner.assign_bottom_layer(
        [_node("question_answering", depth=1)],
        [
            DocumentCard(
                doc_id="OARW_1",
                resource_uri="viking://resources/OARW_1/",
                title="Paper OARW_1",
                source_type="academic_paper_full_text",
                summary="QA summary.",
                main_points=["QA"],
                candidate_topics=["question answering"],
                evidence_anchors=[
                    {
                        "section_title": "Abstract",
                        "section_uri": "viking://resources/OARW_1/abstract",
                        "summary": "QA evidence.",
                    }
                ],
            )
        ],
    )

    assert [assignment.doc_id for assignment in result.assignments] == ["OARW_1"]
    assert result.assignments[0].node_id == "question_answering"
    assert result.assignments[0].resource_uri == "viking://resources/OARW_1/"
    assert result.assignments[0].support_scope == "QA summary."


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
        node=_node(node_id, depth=1),
        node_md=f"# {node_id}",
        documents=[NodeDocument(document_id="0001", content=f"# {node_id}\n\nKnowledge.")],
        evidence=[
            EvidenceClaim(
                claim_id=f"{node_id}_claim",
                claim="Child node claim.",
                claim_type="research_direction",
                evidence_refs=[
                    EvidenceRef(
                        doc_id=doc_id,
                        resource_uri=f"viking://resources/{doc_id}/",
                        section_uri=f"viking://resources/{doc_id}/abstract",
                        section_title="Abstract",
                        support_type="supports",
                        evidence_quote_or_summary="Evidence.",
                    )
                ],
                confidence=0.8,
            )
        ],
        source_refs=[source_ref],
    )


def _node(node_id: str, depth: int) -> WikiNode:
    return WikiNode(
        node_id=node_id,
        title=node_id.replace("_", " ").title(),
        status="active",
        depth=depth,
        scope="Supported topic.",
        seed_doc_ids=["OARW_1"],
        supporting_doc_count=1,
        promotion_decision="promote_to_node",
        promotion_reasons=["supported"],
        inclusion_criteria=["related"],
        exclusion_criteria=["unrelated"],
    )
