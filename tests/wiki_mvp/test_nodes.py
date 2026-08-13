import pytest

from openviking.wiki_mvp.config import WikiMVPConfig
from openviking.wiki_mvp.llm import WikiLLMRunner
from openviking.wiki_mvp.nodes import NodeDiscoveryRunner
from openviking.wiki_mvp.schemas import DocumentCard, ResourceSpaceProfile

from .fakes import FakeVLM
from .test_pipeline_order import _card_response, _profile_response


@pytest.mark.asyncio
async def test_node_discovery_caps_active_nodes_by_available_support():
    llm = WikiLLMRunner(
        FakeVLM(
            [
                {
                    "nodes": [
                        {"title": "Reading comprehension dataset construction"},
                        {"title": "Semi-supervised question answering"},
                        {"title": "Cross-modal alignment"},
                    ]
                }
            ]
        )
    )
    runner = NodeDiscoveryRunner(llm, WikiMVPConfig())

    nodes = await runner.discover_bottom_layer(
        ResourceSpaceProfile.model_validate(_profile_response()),
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
    )

    active_nodes = [node for node in nodes if node.status == "active"]
    rejected_nodes = [node for node in nodes if node.status == "rejected"]

    assert [node.title for node in active_nodes] == [
        "Reading comprehension dataset construction"
    ]
    assert len(rejected_nodes) == 2
    assert all("active node limit 1 reached" in node.promotion_reasons for node in rejected_nodes)


@pytest.mark.asyncio
async def test_node_discovery_sanitizes_model_supplied_node_id_before_validation():
    llm = WikiLLMRunner(
        FakeVLM(
            [
                {
                    "nodes": [
                        {
                            "node_id": "Question-Answering Methods",
                            "title": "Question Answering Methods",
                        }
                    ]
                }
            ]
        )
    )
    runner = NodeDiscoveryRunner(llm, WikiMVPConfig())

    nodes = await runner.discover_bottom_layer(
        ResourceSpaceProfile.model_validate(_profile_response()),
        [
            DocumentCard.model_validate(_card_response(1)),
            DocumentCard.model_validate(_card_response(2)),
            DocumentCard.model_validate(_card_response(3)),
        ],
        depth=1,
    )

    assert nodes[0].node_id == "question_answering_methods"
