import json

import pytest

from openviking.wiki.card_store import DocumentCardStore
from openviking.wiki.config import WikiConfig
from openviking.wiki.schemas import DocumentCard, ResourceDocument, SourceSection
from openviking.wiki.uri import card_json_uri, card_manifest_uri, card_md_uri
from openviking.wiki.writer import WikiVikingFSWriter
from openviking_cli.exceptions import FailedPreconditionError

from .fakes import FakeClient


@pytest.mark.asyncio
async def test_card_store_round_trip_writes_manifest_last():
    client, store, config = _store()
    doc = _doc()
    card = _card()

    manifest = await store.replace(
        cards=[card],
        resource_documents=[doc],
        resource_uris=["viking://resources/demo"],
        card_input_mode="summary",
        max_card_input_chars=20000,
    )
    loaded = await store.load_validated(
        manifest=await store.read_manifest(),
        resource_documents=[doc],
        resource_uris=["viking://resources/demo"],
    )

    assert loaded == [card]
    assert manifest.entries[0].doc_id == doc.doc_id
    assert client.write_order[-1] == card_manifest_uri(config)
    assert card_json_uri(config, doc.doc_id) in client.writes
    assert card_md_uri(config, doc.doc_id) in client.writes


@pytest.mark.asyncio
async def test_card_store_rejects_changed_document_card_prompt():
    _, store, _ = _store()
    doc = _doc()
    await _replace(store, doc)
    changed_doc = doc.model_copy(update={"content_or_structure": "Changed summary input."})

    with pytest.raises(FailedPreconditionError) as exc_info:
        await store.load_validated(
            manifest=await store.read_manifest(),
            resource_documents=[changed_doc],
            resource_uris=["viking://resources/demo"],
        )

    assert any(
        "input or prompt changed" in reason
        for reason in exc_info.value.details["reasons"]
    )


@pytest.mark.asyncio
async def test_card_store_rejects_tampered_card_and_missing_markdown():
    client, store, config = _store()
    doc = _doc()
    await _replace(store, doc)
    client.writes[card_json_uri(config, doc.doc_id)] = json.dumps(
        _card().model_copy(update={"summary": "Tampered."}).model_dump(mode="json")
    )
    client.writes.pop(card_md_uri(config, doc.doc_id))

    with pytest.raises(FailedPreconditionError) as exc_info:
        await store.load_validated(
            manifest=await store.read_manifest(),
            resource_documents=[doc],
            resource_uris=["viking://resources/demo"],
        )

    reasons = exc_info.value.details["reasons"]
    assert any("card content hash changed" in reason for reason in reasons)
    assert any("card markdown is missing" in reason for reason in reasons)


@pytest.mark.asyncio
async def test_card_store_rejects_changed_resource_roots_and_document_set():
    _, store, _ = _store()
    doc = _doc()
    await _replace(store, doc)

    with pytest.raises(FailedPreconditionError) as exc_info:
        await store.load_validated(
            manifest=await store.read_manifest(),
            resource_documents=[doc, _doc("doc_2")],
            resource_uris=["viking://resources/other"],
        )

    reasons = exc_info.value.details["reasons"]
    assert "resource roots changed" in reasons
    assert any("cards missing for document IDs" in reason for reason in reasons)


@pytest.mark.asyncio
async def test_card_store_rejects_missing_manifest():
    _, store, _ = _store()

    with pytest.raises(FailedPreconditionError) as exc_info:
        await store.read_manifest()

    assert "run build_cards before build_wiki" in str(exc_info.value)


def _store():
    client = FakeClient()
    config = WikiConfig()
    writer = WikiVikingFSWriter(
        viking_fs=client,
        vikingdb=object(),
        ctx=object(),
        config=config,
        content_writer=client,
    )
    return client, DocumentCardStore(
        viking_fs=client,
        writer=writer,
        config=config,
        ctx=writer.ctx,
    ), config


async def _replace(store: DocumentCardStore, doc: ResourceDocument) -> None:
    await store.replace(
        cards=[_card(doc.doc_id)],
        resource_documents=[doc],
        resource_uris=["viking://resources/demo"],
        card_input_mode="summary",
        max_card_input_chars=20000,
    )


def _doc(doc_id: str = "doc_1") -> ResourceDocument:
    return ResourceDocument(
        doc_id=doc_id,
        resource_uri=f"viking://resources/demo/{doc_id}",
        title=f"Document {doc_id}",
        content_or_structure=f"Summary input for {doc_id}.",
        source_sections=[
            SourceSection(
                section_uri=f"viking://resources/demo/{doc_id}/content.md",
                content=f"Raw content for {doc_id}.",
            )
        ],
    )


def _card(doc_id: str = "doc_1") -> DocumentCard:
    return DocumentCard(
        doc_id=doc_id,
        resource_uri=f"viking://resources/demo/{doc_id}",
        title=f"Document {doc_id}",
        summary=f"Summary for {doc_id}.",
        main_points=["Main point"],
        important_terms=["term"],
        candidate_topics=["topic"],
        markdown=f"# Document {doc_id}\n",
    )
