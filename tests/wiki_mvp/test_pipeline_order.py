import pytest

from openviking.wiki_mvp.config import WikiMVPConfig, WikiMVPGenerationLimits
from openviking.wiki_mvp.llm import WikiLLMRunner
from openviking.wiki_mvp.pipeline import WikiMVPPipeline
from openviking.wiki_mvp.schemas import ResourceDocument

from .fakes import FakeClient, FakeVLM


@pytest.mark.asyncio
async def test_pipeline_generates_layer_content_before_next_layer_decision():
    docs = [_doc(index) for index in range(1, 4)]
    fake_vlm = FakeVLM(
        [
            _card_response(1),
            _card_response(2),
            _card_response(3),
            _profile_response(),
            _node_discovery_response(),
            _assignment_response(),
            {"node_md": "# Question Answering\n\n## Scope\n\nQA scope."},
            {
                "documents": [
                    {
                        "document_id": "ignored_name",
                        "title": "High-Level Knowledge",
                        "content": "# High-Level Knowledge\n\nSynthesized QA knowledge.",
                    }
                ]
            },
            _evidence_response(),
            {"continue_upward": False, "reasons": ["no stable parent layer"]},
        ]
    )
    llm = WikiLLMRunner(fake_vlm)
    client = FakeClient()

    artifacts = await WikiMVPPipeline(client=client, config=WikiMVPConfig(), llm=llm).run(docs)

    assert [record.step for record in llm.log.raw_outputs] == [
        "doc_card",
        "doc_card",
        "doc_card",
        "profile",
        "node_discovery",
        "source_assignment",
        "node_md",
        "node_documents",
        "evidence",
        "next_layer_decision",
    ]
    assert "viking://wiki/nodes/question_answering/documents/0001.md" in client.writes
    assert "viking://wiki/nodes/question_answering/evidence.jsonl" in client.writes
    assert artifacts.node_contexts[0].documents[0].document_id == "0001"


@pytest.mark.asyncio
async def test_pipeline_does_not_precreate_unassigned_active_node_dirs():
    docs = [_doc(index) for index in range(1, 4)]
    fake_vlm = FakeVLM(
        [
            _card_response(1),
            _card_response(2),
            _card_response(3),
            _profile_response(),
            {
                "nodes": [
                    {
                        "node_id": "question_answering",
                        "title": "Question Answering",
                        "status": "active",
                        "depth": 1,
                        "scope": "QA methods and evaluation.",
                        "inclusion_criteria": ["papers about QA"],
                        "exclusion_criteria": ["unrelated papers"],
                        "seed_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
                        "supporting_doc_count": 3,
                        "promotion_decision": "promote_to_node",
                        "promotion_reasons": ["supported by multiple docs"],
                        "parent_node_id": None,
                    },
                    {
                        "node_id": "unassigned_topic",
                        "title": "Unassigned Topic",
                        "status": "active",
                        "depth": 1,
                        "scope": "No assigned sources.",
                        "inclusion_criteria": ["unassigned"],
                        "exclusion_criteria": ["assigned"],
                        "seed_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
                        "supporting_doc_count": 3,
                        "promotion_decision": "promote_to_node",
                        "promotion_reasons": ["model proposed it"],
                        "parent_node_id": None,
                    },
                ]
            },
            _assignment_response(),
            {"node_md": "# Question Answering\n\n## Scope\n\nQA scope."},
            {
                "documents": [
                    {
                        "document_id": "ignored_name",
                        "title": "High-Level Knowledge",
                        "content": "# High-Level Knowledge\n\nSynthesized QA knowledge.",
                    }
                ]
            },
            _evidence_response(),
            {"continue_upward": False, "reasons": ["no stable parent layer"]},
        ]
    )
    llm = WikiLLMRunner(fake_vlm)
    client = FakeClient()
    config = WikiMVPConfig(limits=WikiMVPGenerationLimits(min_refs_per_node=1))

    artifacts = await WikiMVPPipeline(client=client, config=config, llm=llm).run(docs)

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
                "node_id": "question_answering",
                "title": "Question Answering",
                "status": "active",
                "depth": 1,
                "scope": "QA methods and evaluation.",
                "inclusion_criteria": ["papers about QA"],
                "exclusion_criteria": ["unrelated papers"],
                "seed_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
                "supporting_doc_count": 3,
                "promotion_decision": "promote_to_node",
                "promotion_reasons": ["supported by multiple docs"],
                "parent_node_id": None,
            }
        ]
    }


def _assignment_response() -> dict:
    return {
        "assignments": [
            {
                "node_id": "question_answering",
                "doc_id": f"OARW_{index}",
                "resource_uri": f"viking://resources/OARW_{index}/",
                "card_uri": f"viking://wiki/cards/OARW_{index}.card.md",
                "support_scope": "Supports QA synthesis.",
            }
            for index in range(1, 4)
        ],
        "unassigned_doc_ids": [],
    }


def _evidence_response() -> dict:
    return {
        "claims": [
            {
                "claim_id": f"c{index}",
                "claim": f"QA claim {index}.",
                "claim_type": "research_direction",
                "evidence_refs": [
                    {
                        "doc_id": f"OARW_{index}",
                        "resource_uri": f"viking://resources/OARW_{index}/",
                        "section_uri": f"viking://resources/OARW_{index}/abstract",
                        "section_title": "Abstract",
                        "support_type": "supports",
                        "evidence_quote_or_summary": "QA evidence.",
                    }
                ],
                "confidence": 0.8,
                "notes": "supported",
            }
            for index in range(1, 4)
        ]
    }
