import pytest

from openviking.wiki.config import WikiConfig
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.nodes import NodeDiscoveryRunner
from openviking.wiki.schemas import DocumentCard

from .fakes import FakeVLM
from .test_pipeline_order import _card_response


@pytest.mark.asyncio
async def test_node_discovery_keeps_llm_nodes_for_later_support_filtering():
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

    result = await runner.discover_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
        min_sources_per_node=3,
    )
    nodes = result.nodes

    assert [node.title for node in nodes] == [
        "Reading comprehension dataset construction",
        "Semi-supervised question answering",
        "Cross-modal alignment",
    ]
    assert all(node.status == "active" for node in nodes)
    assert len(result.source_assignments.assignments) == 3
    node_schema = fake_vlm.schemas[0]["$defs"]["WikiSourceNodeDiscoveryItem"]
    assert set(node_schema["properties"]) == {
        "title",
        "scope",
        "supporting_source_ids",
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

    result = await runner.discover_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
        min_sources_per_node=3,
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
                        "supporting_source_ids": [
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

    result = await runner.discover_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
        min_sources_per_node=3,
    )

    assert result.nodes[0].node_id == "low_resource_language_processing"
    assert result.source_assignments.assignments[0].source_ids == [
        "OARW_1",
        "OARW_2",
        "OARW_3",
    ]
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


@pytest.mark.asyncio
async def test_node_discovery_retries_invalid_json_result_with_same_prompt():
    fake_vlm = FakeVLM(
        [
            None,
            {
                "nodes": [
                    _node("Low-resource language processing")
                ]
            },
        ]
    )
    runner = NodeDiscoveryRunner(WikiLLMRunner(fake_vlm), WikiConfig())

    result = await runner.discover_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
        min_sources_per_node=3,
    )

    assert result.nodes[0].node_id == "low_resource_language_processing"
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


@pytest.mark.asyncio
async def test_parent_node_discovery_allows_duplicate_child_parent_assignment():
    fake_vlm = FakeVLM(
        [
            {
                "nodes": [
                    _parent_node("Parent A", ["child_a", "child_b", "child_c"]),
                    _parent_node("Parent B", ["child_c", "child_d", "child_e"]),
                ]
            },
        ]
    )
    runner = NodeDiscoveryRunner(WikiLLMRunner(fake_vlm), WikiConfig())

    result = await runner.discover_layer(
        [
            _node_card("child_a", "Child A"),
            _node_card("child_b", "Child B"),
            _node_card("child_c", "Child C"),
            _node_card("child_d", "Child D"),
            _node_card("child_e", "Child E"),
        ],
        depth=2,
        min_sources_per_node=3,
    )

    source_ids_by_node = {
        assignment.node_id: assignment.source_ids
        for assignment in result.source_assignments.assignments
    }
    assert source_ids_by_node["parent_a"] == ["child_a", "child_b", "child_c"]
    assert source_ids_by_node["parent_b"] == ["child_c", "child_d", "child_e"]
    assert len(fake_vlm.calls) == 1


def _node(title: str) -> dict:
    return {
        "title": title,
        "scope": f"{title} scope.",
        "supporting_source_ids": ["OARW_1", "OARW_2", "OARW_3"],
        "merged_candidate_topics": [title],
    }


def _parent_node(title: str, child_node_ids: list[str]) -> dict:
    return {
        "title": title,
        "scope": f"{title} scope.",
        "supporting_source_ids": child_node_ids,
        "merged_candidate_topics": child_node_ids,
    }


def _node_card(node_id: str, title: str) -> DocumentCard:
    return DocumentCard(
        doc_id=node_id,
        resource_uri=f"viking://wiki/nodes/{node_id}/",
        title=title,
        summary=f"{title} summary.",
        main_points=[f"{title} point."],
        candidate_topics=[title],
    )
