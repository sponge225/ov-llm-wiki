import json

import pytest

from openviking.wiki.document_manifest import document_manifest_uri, write_document_manifest
from openviking.wiki.schemas import ResourceDocumentDraft


@pytest.mark.asyncio
async def test_write_document_manifest_persists_document_boundaries_only():
    writes = {}
    root_uri = "viking://resources/demo"

    class FakeVikingFS:
        async def write_file(self, uri, content, *, ctx, lock_handle=None):
            writes["uri"] = uri
            writes["content"] = content
            writes["ctx"] = ctx
            writes["lock_handle"] = lock_handle

    await write_document_manifest(
        FakeVikingFS(),
        root_uri=root_uri,
        drafts=[
            ResourceDocumentDraft(
                doc_id="doc_a",
                title="Doc A",
                relative_uri="nested/a",
            )
        ],
        ctx="ctx",
        lock_handle="lock",
    )

    payload = json.loads(writes["content"])
    assert writes["uri"] == document_manifest_uri(root_uri)
    assert payload == {
        "version": 1,
        "documents": [
            {
                "doc_id": "doc_a",
                "title": "Doc A",
                "relative_uri": "nested/a",
            }
        ],
    }
    assert writes["ctx"] == "ctx"
    assert writes["lock_handle"] == "lock"


@pytest.mark.asyncio
async def test_write_document_manifest_uses_root_boundary_for_single_non_directory_resource():
    writes = {}

    class FakeVikingFS:
        async def write_file(self, uri, content, *, ctx, lock_handle=None):
            writes["content"] = content

    await write_document_manifest(
        FakeVikingFS(),
        root_uri="viking://resources/single_doc",
        drafts=[
            ResourceDocumentDraft(
                doc_id="single_doc",
                title="Single Doc",
                relative_uri="single_doc",
            )
        ],
        ctx=object(),
        source_format="markdown",
    )

    payload = json.loads(writes["content"])
    assert payload["documents"] == [
        {
            "doc_id": "single_doc",
            "title": "Single Doc",
            "relative_uri": "",
        }
    ]
