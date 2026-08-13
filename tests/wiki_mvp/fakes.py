from __future__ import annotations

from typing import Any


class FakeVLM:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.calls: list[str] = []

    async def complete_json_async(self, prompt: str = "", schema: dict | None = None, **_: Any) -> dict:
        if not self.responses:
            raise AssertionError("FakeVLM has no remaining responses")
        self.calls.append(prompt)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self):
        self.mkdirs: list[str] = []
        self.writes: dict[str, str] = {}

    async def mkdir(self, uri: str, *_: Any, **__: Any) -> None:
        self.mkdirs.append(uri)

    async def write(self, uri: str, content: str, *_: Any, **__: Any) -> dict[str, Any]:
        self.writes[uri] = content
        return {}
