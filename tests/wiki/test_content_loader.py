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
    assert (
        doc.source_sections[1].content
        == "URI: this line is part of the document, not a section marker."
    )


@pytest.mark.asyncio
async def test_source_document_loader_returns_complete_raw_sections():
    loader = WikiContentLoader(FakeVikingFS(), object(), object())

    doc = await loader.load_source_document(
        WikiResourceInput(
            doc_id="doc_1",
            resource_uri="viking://resources/doc_1/",
            title="Doc 1",
            document_dir_uri="viking://resources/doc_1/",
        )
    )

    assert doc.content_or_structure == ""
    assert [section.section_uri for section in doc.source_sections] == [
        "viking://resources/doc_1/a.md",
        "viking://resources/doc_1/b.md",
    ]


def test_content_loader_strictly_bounds_large_multi_entry_documents():
    loader = WikiContentLoader(
        viking_fs=FakeVikingFS(),
        vikingdb=object(),
        ctx=object(),
    )
    entries = [
        {
            "kind": "leaf_summary",
            "uri": f"viking://resources/doc_1/{index}.md",
            "title_path": ["doc_1", f"{index}.md"],
            "text": f"summary-{index} " + ("x" * 1000),
        }
        for index in range(2000)
    ]

    rendered = loader._render_entries(entries, max_chars=20000)
    sections = loader._source_sections_from_entries(entries, max_chars=20000)

    assert len(rendered) <= 20000
    assert sum(len(section.content) for section in sections) <= 20000
    assert "0.md" in rendered
    assert "1999.md" in rendered
    assert sections[0].section_uri.endswith("/0.md")
    assert sections[-1].section_uri.endswith("/1999.md")


class FakeVikingFS:
    async def stat(self, uri: str, *, ctx: object) -> dict:
        return {"isDir": uri == "viking://resources/doc_1/"}

    async def ls(
        self, uri: str, *, show_all_hidden: bool, node_limit: int, ctx: object
    ) -> list[dict]:
        assert uri == "viking://resources/doc_1/"
        return [
            {"name": "a.md", "uri": "viking://resources/doc_1/a.md", "type": "file"},
            {"name": "b.md", "uri": "viking://resources/doc_1/b.md", "type": "file"},
            {"name": "figure.PNG", "uri": "viking://resources/doc_1/figure.PNG", "type": "file"},
        ]

    async def read_file(self, uri: str, *, ctx: object) -> str:
        return {
            "viking://resources/doc_1/a.md": "Alpha content.",
            "viking://resources/doc_1/b.md": "URI: this line is part of the document, not a section marker.",
        }[uri]
