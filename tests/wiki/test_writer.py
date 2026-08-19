import json

import pytest

from openviking.wiki.config import WikiConfig
from openviking.wiki.writer import WikiVikingFSWriter

from .fakes import FakeClient


@pytest.mark.asyncio
async def test_writer_writes_json_and_jsonl():
    client = FakeClient()
    writer = WikiVikingFSWriter(
        viking_fs=client,
        vikingdb=object(),
        ctx=object(),
        config=WikiConfig(),
        content_writer=client,
    )

    await writer.write_json("viking://wiki/nodes.json", {"nodes": []})
    await writer.write_jsonl("viking://wiki/run/raw_outputs.jsonl", [{"step": "a"}, {"step": "b"}])

    assert json.loads(client.writes["viking://wiki/nodes.json"]) == {"nodes": []}
    assert client.writes["viking://wiki/run/raw_outputs.jsonl"].splitlines() == [
        '{"step": "a"}',
        '{"step": "b"}',
    ]


@pytest.mark.asyncio
async def test_writer_ensure_dirs_uses_viking_wiki_root():
    client = FakeClient()
    writer = WikiVikingFSWriter(
        viking_fs=client,
        vikingdb=object(),
        ctx=object(),
        config=WikiConfig(),
        content_writer=client,
    )

    await writer.ensure_dirs(["question_answering"])

    assert "viking://wiki/" in client.mkdirs
    assert "viking://wiki/nodes/question_answering/documents/" in client.mkdirs
    assert all("corpus" not in uri for uri in client.mkdirs)
