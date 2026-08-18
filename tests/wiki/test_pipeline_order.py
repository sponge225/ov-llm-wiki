import pytest

from openviking.wiki.config import WikiConfig, WikiGenerationLimits
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.pipeline import WikiPipeline
from openviking.wiki.schemas import ResourceDocument

from .fakes import FakeClient, FakeVLM


@pytest.mark.asyncio
async def test_pipeline_generates_layer_content_before_next_layer_decision():
    docs = [_doc(index) for index in range(1, 4)]
    fake_vlm = FakeVLM(
        [
            _card_content_response(1),
            _card_content_response(2),
            _card_content_response(3),
            _profile_response(),
            _node_discovery_response(),
            {"node_md": "# Question Answering\n\n## Scope\n\nQA scope."},
            {
                "documents": [
                    {
                        "title": "High-Level Knowledge",
                        "content": "# High-Level Knowledge\n\nSynthesized QA knowledge.",
                    }
                ],
            },
            {"continue_upward": False, "reasons": ["no stable parent layer"]},
        ]
    )
    llm = WikiLLMRunner(fake_vlm)
    client = FakeClient()

    artifacts = await WikiPipeline(client=client, config=WikiConfig(), llm=llm).run(docs)

    assert [record.step for record in llm.log.raw_outputs] == [
        "doc_card",
        "doc_card",
        "doc_card",
        "profile",
        "bottom_node_discovery",
        "node_md",
        "node_documents",
        "next_layer_decision",
    ]
    assert "viking://wiki/nodes/question_answering/documents/0001.md" in client.writes
    assert "viking://wiki/nodes/question_answering/evidence.jsonl" not in client.writes
    assert artifacts.node_contexts[0].documents[0].document_id == "0001"


@pytest.mark.asyncio
async def test_pipeline_does_not_precreate_unassigned_active_node_dirs():
    docs = [_doc(index) for index in range(1, 4)]
    fake_vlm = FakeVLM(
        [
            _card_content_response(1),
            _card_content_response(2),
            _card_content_response(3),
            _profile_response(),
            {
                "nodes": [
                    {
                        "title": "Question Answering",
                        "scope": "QA methods and evaluation.",
                        "supporting_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
                        "merged_candidate_topics": ["question answering"],
                    },
                    {
                        "title": "Unassigned Topic",
                        "scope": "No assigned sources.",
                        "supporting_doc_ids": ["OARW_1"],
                        "merged_candidate_topics": ["unknown topic"],
                    },
                ]
            },
            {"node_md": "# Question Answering\n\n## Scope\n\nQA scope."},
            {
                "documents": [
                    {
                        "title": "High-Level Knowledge",
                        "content": "# High-Level Knowledge\n\nSynthesized QA knowledge.",
                    }
                ],
            },
            {"continue_upward": False, "reasons": ["no stable parent layer"]},
        ]
    )
    llm = WikiLLMRunner(fake_vlm)
    client = FakeClient()
    config = WikiConfig(limits=WikiGenerationLimits(min_refs_per_node=2))

    artifacts = await WikiPipeline(client=client, config=config, llm=llm).run(docs)

    assert "viking://wiki/nodes/question_answering/" in client.mkdirs
    assert "viking://wiki/nodes/unassigned_topic/" not in client.mkdirs
    assert "viking://wiki/nodes/unassigned_topic/documents/" not in client.mkdirs
    assert "viking://wiki/nodes/unassigned_topic/sources/" not in client.mkdirs
    rejected = [node for node in artifacts.nodes if node.node_id == "unassigned_topic"]
    assert rejected[0].status == "rejected"


def _doc(index: int) -> ResourceDocument:
    return ResourceDocument(
        doc_id=f"OARW_{index}",
        resource_uri=f"viking://resources/OARW_{index}/",
        title=f"Paper {index}",
        source_type="academic_paper_full_text",
        content_or_structure=f"# Paper {index}\n\nContent about question answering.",
    )


def _card_response(index: int) -> dict:
    return {
        "doc_id": f"OARW_{index}",
        "resource_uri": f"viking://resources/OARW_{index}/",
        "title": f"Paper {index}",
        "source_type": "academic_paper_full_text",
        **_card_content_response(index),
    }


def _card_content_response(index: int) -> dict:
    return {
        "summary": f"Paper {index} discusses question answering.",
        "main_points": ["QA method"],
        "important_terms": ["question answering"],
        "limitations_or_notes": ["limited evidence"],
        "candidate_topics": ["question answering"],
        "evidence_anchors": [
            {
                "section_title": "Abstract",
                "section_uri": f"viking://resources/OARW_{index}/abstract",
                "summary": "QA evidence",
            }
        ],
    }


def _profile_response() -> dict:
    return {
        "space_title": "Question Answering Research",
        "space_summary": "The resources discuss QA methods.",
        "main_topics": ["question answering"],
        "important_terms": ["QA"],
        "notes": ["small test profile"],
    }


def _node_discovery_response() -> dict:
    return {
        "nodes": [
            {
                "title": "Question Answering",
                "scope": "QA methods and evaluation.",
                "supporting_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
                "merged_candidate_topics": ["question answering"],
            }
        ]
    }
