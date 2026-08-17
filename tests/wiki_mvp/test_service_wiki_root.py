from types import SimpleNamespace

import pytest

from openviking.service.resource_service import ResourceService
from openviking.wiki_mvp.schemas import WikiResourceInput


@pytest.mark.asyncio
async def test_service_wiki_build_uses_stable_wiki_root(monkeypatch):
    captured = {}

    class FakePipeline:
        def __init__(self, _adapter, *, config):
            captured["config"] = config

        async def run_from_inputs(self, wiki_inputs, *, content_loader, card_input_mode, max_card_input_chars):
            return SimpleNamespace(cards=[], nodes=[], node_contexts=[])

    monkeypatch.setattr(
        "openviking.wiki_mvp.client_adapter.WikiServiceClientAdapter",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "openviking.wiki_mvp.content_loader.WikiContentLoader",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openviking.wiki_mvp.pipeline.WikiMVPPipeline", FakePipeline)
    monkeypatch.setattr(
        "openviking_cli.utils.config.get_openviking_config",
        lambda: SimpleNamespace(vlm=None),
    )

    service = ResourceService(vikingdb=object(), viking_fs=object())
    result = await service._run_add_resource_wiki_pipeline(
        wiki_inputs=[
            WikiResourceInput(
                doc_id="paper",
                resource_uri="viking://resources/qasper_30_processed_docs_42020d17/paper.md",
                title="paper",
                metadata={"root_uri": "viking://resources/qasper_30_processed_docs_42020d17"},
            )
        ],
        ctx=object(),
        card_input_mode="summary",
        max_card_input_chars=20000,
    )

    assert captured["config"].wiki_root_uri == "viking://wiki/"
    assert captured["config"].resource_root_uri == "viking://resources/qasper_30_processed_docs_42020d17"
    assert result["wiki_root_uri"] == "viking://wiki/"
