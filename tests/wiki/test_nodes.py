import pytest

from openviking.wiki.config import WikiConfig
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.nodes import NodeDiscoveryRunner
from openviking.wiki.schemas import DocumentCard, GeneratedNodeContext, NodeDocument, SourceRef, WikiNode

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

    result = await runner.discover_bottom_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
    )
    nodes = result.nodes

    assert [node.title for node in nodes] == [
        "Reading comprehension dataset construction",
        "Semi-supervised question answering",
        "Cross-modal alignment",
    ]
    assert all(node.status == "active" for node in nodes)
    assert len(result.source_assignments.assignments) == 3
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

    result = await runner.discover_bottom_layer(
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
    )

    assert result.nodes[0].node_id == "low_resource_language_processing"
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


@pytest.mark.asyncio
async def test_parent_node_discovery_retries_duplicate_child_parent_assignment():
    fake_vlm = FakeVLM(
        [
            {
                "nodes": [
                    _parent_node("Parent A", ["Child A", "Child B", "Child C"]),
                    _parent_node("Parent B", ["Child C", "Child D", "Child E"]),
                ]
            },
            {
                "nodes": [
                    _parent_node("Parent A", ["Child A", "Child B", "Child C"]),
                    _parent_node("Parent B", ["Child D", "Child E", "Child F"]),
                ]
            },
        ]
    )
    runner = NodeDiscoveryRunner(WikiLLMRunner(fake_vlm), WikiConfig())

    result = await runner.discover_parent_layer(
        [
            _child_context("child_a", "Child A"),
            _child_context("child_b", "Child B"),
            _child_context("child_c", "Child C"),
            _child_context("child_d", "Child D"),
            _child_context("child_e", "Child E"),
            _child_context("child_f", "Child F"),
        ],
        depth=2,
    )

    source_ids_by_node = {
        assignment.node_id: assignment.source_ids
        for assignment in result.source_assignments.assignments
    }
    assert source_ids_by_node["parent_a"] == ["child_a", "child_b", "child_c"]
    assert source_ids_by_node["parent_b"] == ["child_d", "child_e", "child_f"]
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


def _node(title: str) -> dict:
    return {
        "title": title,
        "scope": f"{title} scope.",
        "supporting_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
        "merged_candidate_topics": [title],
    }


def _parent_node(title: str, child_titles: list[str]) -> dict:
    return {
        "title": title,
        "scope": f"{title} scope.",
        "supporting_child_titles": child_titles,
        "merged_child_topics": child_titles,
    }


def _child_context(node_id: str, title: str) -> GeneratedNodeContext:
    return GeneratedNodeContext(
        node=WikiNode(node_id=node_id, title=title, depth=1, scope=f"{title} scope."),
        node_md=f"# {title}",
        documents=[NodeDocument(document_id="0001", content=f"# {title}\n\nKnowledge.")],
        source_refs=[
            SourceRef(
                ref_id=f"{node_id}_doc",
                doc_id=f"{node_id}_doc",
                resource_uri=f"viking://resources/{node_id}/",
                card_uri=f"viking://wiki/cards/{node_id}.card.md",
                title=title,
                support_scope=f"{title} scope.",
            )
        ],
    )
