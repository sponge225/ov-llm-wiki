import pytest

from openviking.wiki.content_loader import WikiCardInputMode, WikiContentLoader
from openviking.wiki.schemas import WikiResourceInput


@pytest.mark.asyncio
async def test_content_loader_populates_source_sections_from_entries():
    loader = WikiContentLoader(
        viking_fs=FakeVikingFS(),
        vikingdb=object(),
        ctx=object(),
    )

    doc = await loader.load_document(
        WikiResourceInput(
            doc_id="doc_1",
            resource_uri="viking://resources/doc_1/",
            title="Doc 1",
            document_dir_uri="viking://resources/doc_1/",
        ),
        mode=WikiCardInputMode.RAW_CHUNK,
        max_card_input_chars=100000,
    )

    assert [section.section_uri for section in doc.source_sections] == [
        "viking://resources/doc_1/a.md",
        "viking://resources/doc_1/b.md",
    ]
    assert doc.source_sections[0].content == "Alpha content."
    assert doc.source_sections[1].content == "URI: this line is part of the document, not a section marker."


class FakeVikingFS:
    async def stat(self, uri: str, *, ctx: object) -> dict:
        return {"isDir": uri == "viking://resources/doc_1/"}

    async def ls(self, uri: str, *, show_all_hidden: bool, node_limit: int, ctx: object) -> list[dict]:
        assert uri == "viking://resources/doc_1/"
        return [
            {"name": "a.md", "uri": "viking://resources/doc_1/a.md", "type": "file"},
            {"name": "b.md", "uri": "viking://resources/doc_1/b.md", "type": "file"},
        ]

    async def read_file(self, uri: str, *, ctx: object) -> str:
        return {
            "viking://resources/doc_1/a.md": "Alpha content.",
            "viking://resources/doc_1/b.md": "URI: this line is part of the document, not a section marker.",
        }[uri]
