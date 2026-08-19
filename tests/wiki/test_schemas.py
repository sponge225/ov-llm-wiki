import pytest
from pydantic import ValidationError

from openviking.wiki.schemas import (
    DocumentCard,
    WikiBottomNodeDiscoveryResponse,
    WikiNode,
    WikiParentNodeDiscoveryResponse,
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


@pytest.mark.parametrize(
    "schema,payload",
    [
        (
            WikiBottomNodeDiscoveryResponse,
            {
                "nodes": [
                    {
                        "node_id": "question_answering",
                        "title": "Question Answering",
                        "scope": "QA papers",
                        "supporting_doc_ids": ["doc_1"],
                    }
                ]
            },
        ),
        (
            WikiParentNodeDiscoveryResponse,
            {
                "nodes": [
                    {
                        "node_id": "question_answering",
                        "title": "Question Answering",
                        "scope": "QA papers",
                        "supporting_child_titles": ["Child Topic"],
                    }
                ]
            },
        ),
    ],
)
def test_node_discovery_rejects_internal_node_fields(schema, payload):
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_node_id_must_be_snake_case():
    with pytest.raises(ValidationError):
        WikiNode(
            node_id="Question Answering",
            title="Question Answering",
            depth=1,
            scope="QA papers",
        )
