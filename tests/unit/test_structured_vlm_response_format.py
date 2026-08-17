import pytest

from openviking.models.vlm.llm import StructuredVLM


class FakeUnderlyingVLM:
    thinking = False

    def __init__(self):
        self.prompt = None
        self.response_format = None

    async def get_completion_async(self, prompt="", response_format=None, **_):
        self.prompt = prompt
        self.response_format = response_format
        return '{"ok": true}'


@pytest.mark.asyncio
async def test_structured_vlm_uses_response_format_for_json_schema():
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    fake = FakeUnderlyingVLM()
    vlm = StructuredVLM()
    vlm._vlm_instance = fake

    result = await vlm.complete_json_async(
        prompt="Return whether it worked.",
        schema=schema,
        schema_name="wiki.demo",
    )

    assert result == {"ok": True}
    assert fake.prompt == "Return whether it worked."
    assert fake.response_format == {
        "type": "json_schema",
        "json_schema": {
            "name": "wiki_demo",
            "schema": schema,
            "strict": True,
        },
    }
