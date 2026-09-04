import os

os.environ["OPENVIKING_CONFIG_FILE"] = "/tmp/openviking-missing-test.conf"
os.environ["OPENVIKING_CLI_CONFIG_FILE"] = "/tmp/openviking-cli-missing-test.conf"

import pytest

from vikingbot.agent.tools.base import ToolContext
from vikingbot.agent.tools.ov_file import VikingGrepTool, VikingMultiReadTool, VikingSearchTool


class FakeVikingClient:
    def __init__(self, *, actor_peer_id: str | None = None, sender_fanout: bool = False):
        self.actor_peer_id = actor_peer_id
        self.sender_fanout = sender_fanout
        self.search_calls = []

    def should_sender_fanout(self) -> bool:
        return self.sender_fanout

    def _memory_target_uri(self, user_id: str | None) -> str:
        if user_id:
            return f"viking://user/{user_id}/memories/"
        return "viking://user/memories/"

    def build_current_memory_target_uris(
        self, *, peer_ids: list[str] | None = None, include_self: bool = False
    ) -> list[str]:
        return [f"viking://user/peers/{peer_id}/memories/" for peer_id in peer_ids or []]

    async def search(self, query: str, **kwargs):
        self.search_calls.append({"query": query, **kwargs})
        return {
            "resources": [
                {
                    "uri": kwargs.get("target_uri") or "",
                    "abstract": "matched",
                    "score": 1.0,
                }
            ],
            "memories": [],
            "skills": [],
        }

    async def close(self) -> None:
        pass


class FakeVikingSearchTool(VikingSearchTool):
    def __init__(self, client: FakeVikingClient):
        super().__init__()
        self.client = client

    async def _get_client(self, tool_context: ToolContext):
        return self.client

    async def _release_client(self, tool_context: ToolContext, client) -> None:
        pass


class FakeReadClient:
    def __init__(self):
        self.read_calls = []
        self.list_calls = []
        self.source_refs = {}

    async def read_content(self, uri: str, level: str = "read") -> str:
        self.read_calls.append((uri, level))
        if uri in self.source_refs:
            return self.source_refs[uri]
        return f"content for {uri}"

    async def list_resources(self, path: str | None = None, recursive: bool = False):
        self.list_calls.append((path, recursive))
        return [
            {"uri": uri, "name": uri.rsplit("/", 1)[-1], "isDir": False, "size": len(content)}
            for uri, content in self.source_refs.items()
            if path and uri.startswith(path)
        ]

    async def close(self) -> None:
        pass


class FakeVikingMultiReadTool(VikingMultiReadTool):
    def __init__(self, client: FakeReadClient):
        super().__init__()
        self.client = client

    async def _get_client(self, tool_context: ToolContext):
        return self.client

    async def _release_client(self, tool_context: ToolContext, client) -> None:
        pass


class FakeGrepClient:
    def __init__(self, *, actor_peer_id: str | None = None):
        self.actor_peer_id = actor_peer_id
        self.grep_calls = []

    def _memory_target_uri(self, user_id: str | None) -> str:
        if user_id:
            return f"viking://user/{user_id}/memories/"
        return "viking://user/memories/"

    def build_current_memory_target_uris(
        self, *, peer_ids: list[str] | None = None, include_self: bool = False
    ) -> list[str]:
        return [f"viking://user/peers/{peer_id}/memories/" for peer_id in peer_ids or []]

    async def grep(
        self, uri: str, pattern: str, case_insensitive: bool = False, user_id: str | None = None
    ) -> dict:
        self.grep_calls.append(
            {
                "uri": uri,
                "pattern": pattern,
                "case_insensitive": case_insensitive,
                "user_id": user_id,
            }
        )
        return {
            "matches": [
                {
                    "uri": "viking://wiki/nodes/steam/documents/0001.md",
                    "line": 1,
                    "content": "steam creator support",
                },
                {
                    "uri": "viking://wiki/nodes/steam_workshop/documents/0001.md",
                    "line": 2,
                    "content": "steam workshop support",
                },
            ]
            if uri == "viking://wiki/nodes" or uri == "viking://wiki/nodes/steam"
            else []
        }

    async def close(self) -> None:
        pass


class FakeVikingGrepTool(VikingGrepTool):
    def __init__(self, client: FakeGrepClient):
        super().__init__()
        self.client = client

    async def _get_client(self, tool_context: ToolContext):
        return self.client

    async def _release_client(self, tool_context: ToolContext, client) -> None:
        pass


@pytest.mark.asyncio
async def test_openviking_search_default_target_filters_to_detail_level():
    client = FakeVikingClient()
    tool = FakeVikingSearchTool(client)

    await tool.execute(ToolContext(), query="steam")

    assert client.search_calls == [
        {
            "query": "steam",
            "target_uri": "",
            "limit": 10,
            "level": [2],
        }
    ]


@pytest.mark.asyncio
async def test_openviking_search_respects_explicit_level_override():
    client = FakeVikingClient()
    tool = FakeVikingSearchTool(client)

    await tool.execute(ToolContext(), query="steam", level=[0, 1])

    assert client.search_calls == [
        {
            "query": "steam",
            "target_uri": "",
            "limit": 10,
            "level": [0, 1],
        }
    ]


def test_openviking_search_marks_wiki_node_documents():
    payload = VikingSearchTool._format_search_items_json(
        VikingSearchTool(),
        {
            "memory": [],
            "resource": [
                {
                    "uri": "viking://wiki/nodes/steam_community/documents/0001.md",
                    "abstract": "Steam community overview",
                    "is_leaf": True,
                    "score": 0.9,
                }
            ],
            "skill": [],
        },
        min_score=0.35,
    )

    assert '"kind": "wiki_node_document"' in payload
    assert "source reference URIs for exact fact verification" in payload


@pytest.mark.asyncio
async def test_openviking_search_includes_wiki_nodes_for_actor_peer_default_target():
    client = FakeVikingClient(actor_peer_id="cli-user")
    tool = FakeVikingSearchTool(client)

    await tool.execute(ToolContext(actor_peer_id="cli-user"), query="steam")

    target_uris = [call["target_uri"] for call in client.search_calls]
    assert "viking://resources/" in target_uris
    assert "viking://wiki/nodes" in target_uris
    assert "viking://user/memories/" in target_uris
    assert "viking://user/skills/" in target_uris
    levels_by_target = {call["target_uri"]: call.get("level") for call in client.search_calls}
    assert levels_by_target["viking://resources/"] == [2]
    assert levels_by_target["viking://wiki/nodes"] == [2]
    assert levels_by_target["viking://user/memories/"] is None
    assert levels_by_target["viking://user/skills/"] is None


@pytest.mark.asyncio
async def test_openviking_grep_includes_wiki_nodes_for_default_target():
    client = FakeGrepClient()
    tool = FakeVikingGrepTool(client)

    result = await tool.execute(ToolContext(), pattern="steam")

    target_uris = [call["uri"] for call in client.grep_calls]
    assert "viking://resources/" in target_uris
    assert "viking://wiki/nodes" in target_uris
    assert "viking://user/memories/" in target_uris
    assert "viking://user/skills/" in target_uris
    assert "viking://wiki/nodes/steam/documents/0001.md" in result
    assert "viking://wiki/nodes/steam_workshop/documents/0001.md" in result


@pytest.mark.asyncio
async def test_openviking_grep_keeps_explicit_wiki_node_target_for_client_api():
    client = FakeGrepClient()
    tool = FakeVikingGrepTool(client)

    result = await tool.execute(
        ToolContext(),
        pattern="steam",
        uri="viking://wiki/nodes/steam",
    )

    target_uris = [call["uri"] for call in client.grep_calls]
    assert target_uris == ["viking://wiki/nodes/steam"]
    assert "viking://wiki/nodes/steam/documents/0001.md" in result
    assert "viking://wiki/nodes/steam_workshop/documents/0001.md" in result


@pytest.mark.asyncio
async def test_openviking_search_includes_wiki_nodes_for_sender_fanout_default_target():
    client = FakeVikingClient(sender_fanout=True)
    tool = FakeVikingSearchTool(client)

    await tool.execute(
        ToolContext(memory_owner_user_ids=["owner-a"]),
        query="steam",
    )

    target_calls = [(call["target_uri"], call.get("user_id")) for call in client.search_calls]
    assert ("viking://resources/", None) in target_calls
    assert ("viking://wiki/nodes", None) in target_calls
    assert ("viking://user/owner-a/memories/", "owner-a") in target_calls
    assert ("viking://user/owner-a/skills/", "owner-a") in target_calls
    levels_by_target = {call["target_uri"]: call.get("level") for call in client.search_calls}
    assert levels_by_target["viking://resources/"] == [2]
    assert levels_by_target["viking://wiki/nodes"] == [2]
    assert levels_by_target["viking://user/owner-a/memories/"] is None
    assert levels_by_target["viking://user/owner-a/skills/"] is None


@pytest.mark.asyncio
async def test_openviking_multi_read_supports_wiki_node_documents():
    client = FakeReadClient()
    tool = FakeVikingMultiReadTool(client)
    uri = "viking://wiki/nodes/steam_community/documents/0001.md"

    result = await tool.execute(ToolContext(), uris=[uri])

    assert client.read_calls == [(uri, "read")]
    assert f"--- START OF {uri} ---" in result
    assert f"content for {uri}" in result
    assert f"--- END OF {uri} ---" in result


@pytest.mark.asyncio
async def test_openviking_multi_read_adds_wiki_node_source_refs():
    client = FakeReadClient()
    client.source_refs = {
        "viking://wiki/nodes/steam_community/sources/source_a.ref.json": (
            '{"resource_uri":"viking://resources/docs/source-a.md",'
            '"title":"Source A","matched_topics":["policy","threshold"]}'
        )
    }
    tool = FakeVikingMultiReadTool(client)
    uri = "viking://wiki/nodes/steam_community/documents/0001.md"

    result = await tool.execute(ToolContext(), uris=[uri])

    assert client.list_calls == [
        ("viking://wiki/nodes/steam_community/sources/", False)
    ]
    assert ("viking://wiki/nodes/steam_community/sources/source_a.ref.json", "read") in client.read_calls
    assert "Wiki source references for exact fact verification:" in result
    assert "viking://resources/docs/source-a.md" in result
    assert "title: Source A" in result
    assert "topics: policy; threshold" in result
