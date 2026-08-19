import pytest

from openviking.wiki.cards import DocumentCardGenerator
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.schemas import ResourceDocument

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
