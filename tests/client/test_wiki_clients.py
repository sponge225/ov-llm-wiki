from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.async_client import AsyncOpenViking
from openviking.client.local import LocalClient
from openviking.sync_client import SyncOpenViking


@pytest.mark.asyncio
async def test_local_client_forwards_split_wiki_lifecycle():
    wiki = SimpleNamespace(
        build_wiki_cards=AsyncMock(return_value={"cards": 1}),
        build_wiki=AsyncMock(return_value={"nodes": 1}),
        clear_wiki=AsyncMock(return_value={"cards_preserved": True}),
    )
    client = LocalClient.__new__(LocalClient)
    client._service = SimpleNamespace(wiki=wiki)
    client._ctx = object()

    await LocalClient.build_wiki_cards(
        client,
        ["viking://resources/demo"],
        card_input_mode="raw_chunk",
        max_card_input_chars=1234,
    )
    await LocalClient.build_wiki(client, ["viking://resources/demo"])
    await LocalClient.clear_wiki(client, preserve_cards=True)

    wiki.build_wiki_cards.assert_awaited_once_with(
        resource_uris=["viking://resources/demo"],
        ctx=client._ctx,
        wiki_root_uri="viking://wiki/",
        card_input_mode="raw_chunk",
        max_card_input_chars=1234,
    )
    wiki.build_wiki.assert_awaited_once_with(
        resource_uris=["viking://resources/demo"],
        ctx=client._ctx,
        wiki_root_uri="viking://wiki/",
    )
    wiki.clear_wiki.assert_awaited_once_with(
        ctx=client._ctx,
        wiki_root_uri="viking://wiki/",
        preserve_cards=True,
    )


@pytest.mark.asyncio
async def test_async_client_forwards_split_wiki_lifecycle():
    client = object.__new__(AsyncOpenViking)
    client._initialized = True
    client._client = SimpleNamespace(
        build_wiki_cards=AsyncMock(return_value={"cards": 1}),
        build_wiki=AsyncMock(return_value={"nodes": 1}),
        clear_wiki=AsyncMock(return_value={"cards_preserved": True}),
    )

    await AsyncOpenViking.build_wiki_cards(
        client,
        ["viking://resources/demo"],
        card_input_mode="raw_chunk",
        max_card_input_chars=1234,
    )
    await AsyncOpenViking.build_wiki(client, ["viking://resources/demo"])
    await AsyncOpenViking.clear_wiki(client, preserve_cards=True)

    client._client.build_wiki_cards.assert_awaited_once_with(
        resource_uris=["viking://resources/demo"],
        wiki_root_uri="viking://wiki/",
        card_input_mode="raw_chunk",
        max_card_input_chars=1234,
        telemetry=False,
    )
    client._client.build_wiki.assert_awaited_once_with(
        resource_uris=["viking://resources/demo"],
        wiki_root_uri="viking://wiki/",
        telemetry=False,
    )
    client._client.clear_wiki.assert_awaited_once_with(
        wiki_root_uri="viking://wiki/",
        preserve_cards=True,
        telemetry=False,
    )


def test_sync_client_forwards_split_wiki_lifecycle():
    client = object.__new__(SyncOpenViking)
    client._async_client = SimpleNamespace(
        build_wiki_cards=Mock(return_value="cards-awaitable"),
        build_wiki=Mock(return_value="wiki-awaitable"),
        clear_wiki=Mock(return_value="clear-awaitable"),
    )

    with patch("openviking.sync_client.run_async", side_effect=lambda value: value):
        assert (
            SyncOpenViking.build_wiki_cards(
                client,
                ["viking://resources/demo"],
                card_input_mode="raw_chunk",
                max_card_input_chars=1234,
            )
            == "cards-awaitable"
        )
        assert (
            SyncOpenViking.build_wiki(client, ["viking://resources/demo"])
            == "wiki-awaitable"
        )
        assert SyncOpenViking.clear_wiki(client, preserve_cards=True) == "clear-awaitable"

    client._async_client.build_wiki_cards.assert_called_once_with(
        resource_uris=["viking://resources/demo"],
        wiki_root_uri="viking://wiki/",
        card_input_mode="raw_chunk",
        max_card_input_chars=1234,
        telemetry=False,
    )
    client._async_client.build_wiki.assert_called_once_with(
        resource_uris=["viking://resources/demo"],
        wiki_root_uri="viking://wiki/",
        telemetry=False,
    )
    client._async_client.clear_wiki.assert_called_once_with(
        wiki_root_uri="viking://wiki/",
        preserve_cards=True,
        telemetry=False,
    )
