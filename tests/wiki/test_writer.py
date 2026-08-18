import json

import pytest

from openviking.wiki.config import WikiConfig
from openviking.wiki.writer import WikiVikingFSWriter

from .fakes import FakeClient


@pytest.mark.asyncio
async def test_writer_writes_json_and_jsonl():
    client = FakeClient()
    writer = WikiVikingFSWriter(client, WikiConfig())

    await writer.write_json("viking://wiki/profile.json", {"space_title": "Test"})
    await writer.write_jsonl("viking://wiki/run/raw_outputs.jsonl", [{"step": "a"}, {"step": "b"}])

    assert json.loads(client.writes["viking://wiki/profile.json"]) == {"space_title": "Test"}
    assert client.writes["viking://wiki/run/raw_outputs.jsonl"].splitlines() == [
        '{"step": "a"}',
        '{"step": "b"}',
    ]


@pytest.mark.asyncio
async def test_writer_ensure_dirs_uses_viking_wiki_root():
    client = FakeClient()
    writer = WikiVikingFSWriter(client, WikiConfig())

    await writer.ensure_dirs(["question_answering"])

    assert "viking://wiki/" in client.mkdirs
    assert "viking://wiki/nodes/question_answering/documents/" in client.mkdirs
    assert all("corpus" not in uri for uri in client.mkdirs)
