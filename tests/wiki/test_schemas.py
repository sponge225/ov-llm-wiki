import pytest
from pydantic import ValidationError

from openviking.wiki.schemas import (
    DocumentCard,
    WikiNode,
    WikiSourceNodeDiscoveryResponse,
)


def test_document_card_requires_candidate_topics():
    with pytest.raises(ValidationError):
        DocumentCard(
            doc_id="OARW_1",
            resource_uri="viking://resources/OARW_1/",
            title="Title",
            summary="Summary",
            main_points=["Point"],
            candidate_topics=[],
        )


def test_document_card_allows_wiki_node_uri():
    card = DocumentCard(
        doc_id="question_answering",
        resource_uri="viking://wiki/nodes/question_answering/",
        title="Question Answering",
        summary="Summary",
        main_points=["Point"],
        candidate_topics=["Parent topic"],
    )

    assert card.resource_uri == "viking://wiki/nodes/question_answering/"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "nodes": [
                {
                    "node_id": "question_answering",
                    "title": "Question Answering",
                    "scope": "QA papers",
                    "supporting_source_ids": ["doc_1"],
                }
            ]
        },
    ],
)
def test_node_discovery_rejects_internal_node_fields(payload):
    with pytest.raises(ValidationError):
        WikiSourceNodeDiscoveryResponse.model_validate(payload)


def test_node_id_must_be_snake_case():
    with pytest.raises(ValidationError):
        WikiNode(
            node_id="Question Answering",
            title="Question Answering",
            depth=1,
            scope="QA papers",
        )
