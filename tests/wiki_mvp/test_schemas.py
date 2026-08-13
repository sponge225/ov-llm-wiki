import pytest
from pydantic import ValidationError

from openviking.wiki_mvp.schemas import DocumentCard, EvidenceAnchor, WikiNode


def test_document_card_requires_candidate_topics():
    with pytest.raises(ValidationError):
        DocumentCard(
            doc_id="OARW_1",
            resource_uri="viking://resources/OARW_1/",
            title="Title",
            source_type="academic_paper_full_text",
            summary="Summary",
            main_points=["Point"],
            candidate_topics=[],
            evidence_anchors=[EvidenceAnchor(section_title="Intro", section_uri="viking://resources/OARW_1/intro")],
        )


def test_active_node_requires_inclusion_and_exclusion_criteria():
    with pytest.raises(ValidationError):
        WikiNode(
            node_id="question_answering",
            title="Question Answering",
            status="active",
            depth=1,
            scope="QA papers",
            seed_doc_ids=["OARW_1", "OARW_2", "OARW_3"],
            supporting_doc_count=3,
            promotion_decision="promote_to_node",
            promotion_reasons=["supported by multiple docs"],
        )


def test_node_id_must_be_snake_case():
    with pytest.raises(ValidationError):
        WikiNode(
            node_id="Question Answering",
            title="Question Answering",
            status="rejected",
            depth=1,
            scope="QA papers",
            seed_doc_ids=["OARW_1"],
            supporting_doc_count=1,
            promotion_decision="reject",
            promotion_reasons=["single doc"],
        )
