from types import SimpleNamespace

import pytest

from openviking.wiki.document_manifest import document_manifest_uri
from openviking.wiki.service import WikiService


@pytest.mark.asyncio
async def test_service_wiki_build_uses_stable_wiki_root(monkeypatch):
    captured = {}

    class FakePipeline:
        def __init__(self, *, writer, config):
            captured["writer"] = writer
            captured["config"] = config

        async def run_from_stored_cards(self, wiki_inputs, *, content_loader, resource_uris):
            return SimpleNamespace(cards=[], nodes=[], node_contexts=[])

        def get_token_usage(self):
            return {}

    monkeypatch.setattr(
        "openviking.wiki.service.WikiVikingFSWriter",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "openviking.wiki.service.WikiContentLoader",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openviking.wiki.service.WikiPipeline", FakePipeline)
    monkeypatch.setattr(
        "openviking.wiki.service.get_openviking_config",
        lambda: SimpleNamespace(vlm=SimpleNamespace(_build_vlm_config_dict=lambda: {})),
    )

    class FakeVikingFS:
        async def exists(self, uri, *, ctx):
            return uri == "viking://resources/qasper_30_processed_docs_42020d17"

    service = WikiService(vikingdb=object(), viking_fs=FakeVikingFS())
    result = await service.build_wiki(
        resource_uris=["viking://resources/qasper_30_processed_docs_42020d17"],
        ctx=object(),
    )

    assert captured["config"].wiki_root_uri == "viking://wiki/"
    assert captured["config"].resource_root_uri == "viking://resources/qasper_30_processed_docs_42020d17"
    assert result["wiki_root_uri"] == "viking://wiki/"


@pytest.mark.asyncio
async def test_service_wiki_build_expands_document_manifest(monkeypatch):
    captured = {}
    root_uri = "viking://resources/qasper_30_processed_docs"

    class FakePipeline:
        def __init__(self, *, writer, config):
            captured["writer"] = writer
            captured["config"] = config

        async def run_from_stored_cards(self, wiki_inputs, *, content_loader, resource_uris):
            captured["wiki_inputs"] = wiki_inputs
            return SimpleNamespace(cards=[], nodes=[], node_contexts=[])

        def get_token_usage(self):
            return {}

    monkeypatch.setattr(
        "openviking.wiki.service.WikiVikingFSWriter",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "openviking.wiki.service.WikiContentLoader",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openviking.wiki.service.WikiPipeline", FakePipeline)
    monkeypatch.setattr(
        "openviking.wiki.service.get_openviking_config",
        lambda: SimpleNamespace(vlm=None),
    )

    class FakeVikingFS:
        async def exists(self, uri, *, ctx):
            return uri in {
                root_uri,
                document_manifest_uri(root_uri),
            }

        async def read_file(self, uri, *, ctx):
            assert uri == document_manifest_uri(root_uri)
            return """
            {
              "version": 1,
              "documents": [
                {"doc_id": "doc_a", "title": "Doc A", "relative_uri": "a"},
                {"doc_id": "doc_b", "title": "Doc B", "relative_uri": "nested/b"}
              ]
            }
            """

    service = WikiService(vikingdb=object(), viking_fs=FakeVikingFS())
    result = await service.build_wiki(resource_uris=[root_uri], ctx=object())

    wiki_inputs = captured["wiki_inputs"]
    assert result["docs"] == 2
    assert [item.doc_id for item in wiki_inputs] == ["doc_a", "doc_b"]
    assert [item.resource_uri for item in wiki_inputs] == [
        "viking://resources/qasper_30_processed_docs/a",
        "viking://resources/qasper_30_processed_docs/nested/b",
    ]


@pytest.mark.asyncio
async def test_service_wiki_build_allows_missing_vlm_config(monkeypatch):
    captured = {}

    class FakePipeline:
        def __init__(self, *, writer, config):
            captured["writer"] = writer
            captured["config"] = config

        async def run_from_stored_cards(self, wiki_inputs, *, content_loader, resource_uris):
            return SimpleNamespace(cards=[], nodes=[], node_contexts=[])

        def get_token_usage(self):
            return {}

    monkeypatch.setattr(
        "openviking.wiki.service.WikiVikingFSWriter",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "openviking.wiki.service.WikiContentLoader",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openviking.wiki.service.WikiPipeline", FakePipeline)
    monkeypatch.setattr(
        "openviking.wiki.service.get_openviking_config",
        lambda: SimpleNamespace(vlm=None),
    )

    class FakeVikingFS:
        async def exists(self, uri, *, ctx):
            return uri == "viking://resources/demo"

    service = WikiService(vikingdb=object(), viking_fs=FakeVikingFS())
    result = await service.build_wiki(
        resource_uris=["viking://resources/demo"],
        ctx=object(),
    )

    assert captured["config"].vlm_config is None
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_service_build_wiki_cards_uses_independent_pipeline_stage(monkeypatch):
    captured = {}

    class FakeManifest:
        def model_dump(self, *, mode):
            return {"version": 1}

    class FakePipeline:
        def __init__(self, *, writer, config):
            captured["config"] = config

        async def generate_document_cards_from_inputs(
            self,
            wiki_inputs,
            *,
            content_loader,
            resource_uris,
            card_input_mode,
            max_card_input_chars,
        ):
            captured.update(
                {
                    "wiki_inputs": wiki_inputs,
                    "resource_uris": resource_uris,
                    "card_input_mode": card_input_mode,
                    "max_card_input_chars": max_card_input_chars,
                }
            )
            return [object()], FakeManifest()

        def get_token_usage(self):
            return {"total_usage": {"total_tokens": 12}}

    monkeypatch.setattr(
        "openviking.wiki.service.WikiVikingFSWriter",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "openviking.wiki.service.WikiContentLoader",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openviking.wiki.service.WikiPipeline", FakePipeline)
    monkeypatch.setattr(
        "openviking.wiki.service.get_openviking_config",
        lambda: SimpleNamespace(vlm=None),
    )

    class FakeVikingFS:
        async def exists(self, uri, *, ctx):
            return uri == "viking://resources/demo"

    service = WikiService(vikingdb=object(), viking_fs=FakeVikingFS())
    result = await service.build_wiki_cards(
        resource_uris=["viking://resources/demo"],
        ctx=object(),
        card_input_mode="raw_chunk",
        max_card_input_chars=1234,
    )

    assert captured["card_input_mode"] == "raw_chunk"
    assert captured["max_card_input_chars"] == 1234
    assert result["cards"] == 1
    assert result["card_manifest_uri"] == "viking://wiki/cards/manifest.json"


@pytest.mark.asyncio
async def test_service_clear_wiki_is_idempotent_for_missing_root():
    class FakeVikingFS:
        def __init__(self):
            self.removed = []

        async def exists(self, uri, *, ctx):
            return False

        async def rm(self, uri, *, recursive, ctx):
            self.removed.append((uri, recursive))
            return {}

    viking_fs = FakeVikingFS()
    service = WikiService(vikingdb=object(), viking_fs=viking_fs)

    result = await service.clear_wiki(ctx=object())

    assert result["cleared"] is False
    assert result["missing"] is True
    assert viking_fs.removed == [("viking://wiki/", True)]


@pytest.mark.asyncio
async def test_service_clear_wiki_can_preserve_cards():
    existing = {
        "viking://wiki/cards/",
        "viking://wiki/nodes/",
        "viking://wiki/nodes.json",
        "viking://wiki/source_assignments.json",
        "viking://wiki/run/",
    }

    class FakeVikingFS:
        def __init__(self):
            self.removed = []

        async def exists(self, uri, *, ctx):
            return uri in existing

        async def rm(self, uri, *, recursive, ctx):
            self.removed.append((uri, recursive))
            return {}

    viking_fs = FakeVikingFS()
    service = WikiService(vikingdb=object(), viking_fs=viking_fs)

    result = await service.clear_wiki(ctx=object(), preserve_cards=True)

    assert result["cards_preserved"] is True
    assert "viking://wiki/cards/" not in result["removed_paths"]
    assert viking_fs.removed == [
        ("viking://wiki/nodes/", True),
        ("viking://wiki/nodes.json", False),
        ("viking://wiki/source_assignments.json", False),
        ("viking://wiki/run/", True),
    ]
