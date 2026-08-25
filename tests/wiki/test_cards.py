import pytest

from openviking.wiki.cards import DocumentCardGenerator
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.schemas import NodeDocument, ResourceDocument, WikiNode

from .fakes import FakeVLM
from .test_pipeline_order import _card_content_response


@pytest.mark.asyncio
async def test_document_card_retries_invalid_json_result_with_same_prompt():
    fake_vlm = FakeVLM([None, _card_content_response(1)])
    generator = DocumentCardGenerator(WikiLLMRunner(fake_vlm))

    card = await generator.generate(
        [
            ResourceDocument(
                doc_id="OARW_1",
                resource_uri="viking://resources/OARW_1/",
                title="Paper 1",
                content_or_structure="# Paper 1\n\nContent about question answering.",
            )
        ]
    )

    assert card[0].doc_id == "OARW_1"
    assert card[0].summary == "Paper 1 discusses question answering."
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


@pytest.mark.asyncio
async def test_node_card_uses_wiki_node_uri_and_node_card_step():
    fake_vlm = FakeVLM(
        [
            {
                "summary": "Question answering node synthesis.",
                "main_points": ["QA synthesis"],
                "important_terms": ["question answering"],
                "candidate_topics": ["question answering systems"],
            }
        ]
    )
    generator = DocumentCardGenerator(WikiLLMRunner(fake_vlm))

    card = await generator.generate_node_card(
        WikiNode(
            node_id="question_answering",
            title="Question Answering",
            depth=1,
            scope="QA methods and evaluation.",
        ),
        [NodeDocument(document_id="0001", content="# QA\n\nSynthesized QA knowledge.")],
        resource_uri="viking://wiki/nodes/question_answering/",
    )

    assert card.doc_id == "question_answering"
    assert card.resource_uri == "viking://wiki/nodes/question_answering/"
    assert card.summary == "Question answering node synthesis."
    assert fake_vlm.calls
