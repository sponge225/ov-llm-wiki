import pytest

from openviking.wiki.llm import WikiLLMRunner

from .fakes import FakeVLM


@pytest.mark.asyncio
async def test_wiki_llm_runner_uses_schema_argument_without_prompt_schema_shape():
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    fake_vlm = FakeVLM([{"ok": True}])
    runner = WikiLLMRunner(fake_vlm)

    result = await runner.complete_json(
        step="demo",
        prompt="Business prompt only.",
        schema=schema,
    )

    assert result == {"ok": True}
    assert fake_vlm.calls == ["Business prompt only."]
    assert fake_vlm.schemas == [schema]
    assert fake_vlm.schema_names == ["wiki_demo"]
    assert "Return only JSON matching this shape" not in runner.log.prompts[0].prompt
    assert runner.log.prompts[0].schema_name == "wiki_demo"
    assert runner.log.prompts[0].schema_hash is not None
