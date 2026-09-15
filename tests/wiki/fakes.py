from __future__ import annotations

from typing import Any


class FakeVLM:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.calls: list[str] = []
        self.schemas: list[dict | None] = []
        self.schema_names: list[str | None] = []

    async def complete_json_async(
        self,
        prompt: str = "",
        schema: dict | None = None,
        schema_name: str | None = None,
        **_: Any,
    ) -> dict:
        if not self.responses:
            raise AssertionError("FakeVLM has no remaining responses")
        self.calls.append(prompt)
        self.schemas.append(schema)
        self.schema_names.append(schema_name)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self):
        self.mkdirs: list[str] = []
        self.writes: dict[str, str] = {}
        self.write_order: list[str] = []
        self.removes: list[str] = []

    async def mkdir(self, uri: str, *_: Any, **__: Any) -> None:
        self.mkdirs.append(uri)

    async def write(self, uri: str, content: str, *_: Any, **__: Any) -> dict[str, Any]:
        self.writes[uri] = content
        self.write_order.append(uri)
        return {}

    async def exists(self, uri: str, *_: Any, **__: Any) -> bool:
        return uri in self.writes or uri in self.mkdirs or any(
            path.startswith(uri) for path in self.writes
        )

    async def read_file(self, uri: str, *_: Any, **__: Any) -> str:
        if uri not in self.writes:
            raise FileNotFoundError(uri)
        return self.writes[uri]

    async def rm(self, uri: str, *, recursive: bool = False, **__: Any) -> dict[str, Any]:
        self.removes.append(uri)
        if recursive:
            self.writes = {
                path: content
                for path, content in self.writes.items()
                if not path.startswith(uri)
            }
            self.mkdirs = [path for path in self.mkdirs if not path.startswith(uri)]
        else:
            self.writes.pop(uri, None)
        return {}
