import pytest

from openviking.wiki.layer_decision import LayerDecisionRunner
from openviking.wiki.llm import WikiLLMRunner

from .fakes import FakeVLM


@pytest.mark.asyncio
async def test_layer_decision_retries_invalid_json_result_with_same_prompt():
    fake_vlm = FakeVLM(
        [
            None,
            {"continue_upward": False, "reasons": ["not enough stable topics"]},
        ]
    )
    runner = LayerDecisionRunner(WikiLLMRunner(fake_vlm))

    should_continue = await runner.should_continue_upward([])

    assert should_continue is False
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]
