from types import SimpleNamespace

from openviking.wiki.router import BuildWikiRequest, ClearWikiRequest, build_wiki, clear_wiki


async def test_build_wiki_router_calls_service(monkeypatch):
    seen = {}

    async def fake_build_wiki(**kwargs):
        seen.update(kwargs)
        return {
            "status": "success",
            "wiki_root_uri": kwargs["wiki_root_uri"],
            "resource_uris": kwargs["resource_uris"],
        }

    service = SimpleNamespace(wiki=SimpleNamespace(build_wiki=fake_build_wiki))
    monkeypatch.setattr("openviking.wiki.router.get_service", lambda: service)

    body = await build_wiki(
        BuildWikiRequest(
            resource_uris=["viking://resources/demo"],
            wiki_root_uri="viking://wiki/",
            card_input_mode="summary",
            max_card_input_chars=20000,
        ),
        _ctx=object(),
    )

    assert body["result"]["wiki_root_uri"] == "viking://wiki/"
    assert seen["resource_uris"] == ["viking://resources/demo"]
    assert seen["card_input_mode"] == "summary"


async def test_clear_wiki_router_calls_service(monkeypatch):
    seen = {}

    async def fake_clear_wiki(**kwargs):
        seen.update(kwargs)
        return {
            "status": "success",
            "wiki_root_uri": kwargs["wiki_root_uri"],
            "cleared": False,
            "missing": True,
        }

    service = SimpleNamespace(wiki=SimpleNamespace(clear_wiki=fake_clear_wiki))
    monkeypatch.setattr("openviking.wiki.router.get_service", lambda: service)

    body = await clear_wiki(
        ClearWikiRequest(wiki_root_uri="viking://wiki/"),
        _ctx=object(),
    )

    assert body["result"]["missing"] is True
    assert seen["wiki_root_uri"] == "viking://wiki/"
