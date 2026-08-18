import pytest

from openviking.wiki.config import WikiConfig
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.nodes import NodeDiscoveryRunner
from openviking.wiki.schemas import DocumentCard

from .fakes import FakeVLM
from .test_pipeline_order import _card_response


@pytest.mark.asyncio
async def test_node_discovery_caps_active_nodes_by_available_support():
    fake_vlm = FakeVLM(
        [
            {
                "nodes": [
                    _node("Reading comprehension dataset construction"),
                    _node("Semi-supervised question answering"),
                    _node("Cross-modal alignment"),
                ]
            }
        ]
    )
    llm = WikiLLMRunner(fake_vlm)
    runner = NodeDiscoveryRunner(llm, WikiConfig())

    result = await runner.discover_bottom_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
    )
    nodes = result.nodes

    active_nodes = [node for node in nodes if node.status == "active"]
    rejected_nodes = [node for node in nodes if node.status == "rejected"]

    assert [node.title for node in active_nodes] == [
        "Reading comprehension dataset construction"
    ]
    assert len(rejected_nodes) == 2
    node_schema = fake_vlm.schemas[0]["$defs"]["WikiBottomNodeDiscoveryItem"]
    assert set(node_schema["properties"]) == {
        "title",
        "scope",
        "supporting_doc_ids",
        "merged_candidate_topics",
    }


@pytest.mark.asyncio
async def test_node_discovery_generates_node_id_and_depth():
    llm = WikiLLMRunner(
        FakeVLM(
            [
                {
                    "nodes": [
                        _node("Question Answering Methods")
                    ]
                }
            ]
        )
    )
    runner = NodeDiscoveryRunner(llm, WikiConfig())

    result = await runner.discover_bottom_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
    )
    nodes = result.nodes

    assert nodes[0].node_id == "question_answering_methods"
    assert nodes[0].depth == 1
    assert nodes[0].status == "active"
    assert result.source_assignments.assignments[0].source_ids == ["OARW_1", "OARW_2", "OARW_3"]


@pytest.mark.asyncio
async def test_node_discovery_retries_invalid_structured_output_with_same_prompt():
    fake_vlm = FakeVLM(
        [
            {
                "nodes": [
                    {
                        **_node("Low-resource language processing"),
                        "supporting_doc_ids": [
                            "OARW_1",
                            {
                                "title": "Low-resource language syntactic parsing techniques"
                            },
                        ],
                    }
                ]
            },
            {
                "nodes": [
                    _node("Low-resource language processing")
                ]
            },
        ]
    )
    runner = NodeDiscoveryRunner(WikiLLMRunner(fake_vlm), WikiConfig())

    result = await runner.discover_bottom_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
    )

    assert result.nodes[0].node_id == "low_resource_language_processing"
    assert result.source_assignments.assignments[0].source_ids == [
        "OARW_1",
        "OARW_2",
        "OARW_3",
    ]
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


def _node(title: str) -> dict:
    return {
        "title": title,
        "scope": f"{title} scope.",
        "supporting_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
        "merged_candidate_topics": [title],
    }
