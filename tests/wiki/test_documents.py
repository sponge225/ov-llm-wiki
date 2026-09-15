import pytest

from openviking.wiki.documents import NodeContentGenerator
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.schemas import DocumentCard, SourceSection, WikiNode
from openviking.wiki.source_selector import SelectedSourceDocument

from .fakes import FakeVLM


@pytest.mark.asyncio
async def test_node_documents_retries_invalid_markdown_with_same_prompt():
    fake_vlm = FakeVLM(
        [
            {"markdown": "# Wrong Title\n\n## Details\n\nInvalid content."},
            {"markdown": "# Question Answering\n\n## Details\n\nValid content."},
        ]
    )
    generator = NodeContentGenerator(WikiLLMRunner(fake_vlm))

    document = await generator.generate_direct(
        WikiNode(
            node_id="question_answering",
            title="Question Answering",
            depth=1,
            scope="QA methods and evaluation.",
        ),
        [
            {
                "source_id": "OARW_1",
                "sections": [
                    {
                        "section_uri": "viking://resources/OARW_1/abstract",
                        "content": "Question answering evidence.",
                    }
                ],
            }
        ],
    )

    assert document.title == "Question Answering"
    assert document.content == "# Question Answering\n\n## Details\n\nValid content."
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


@pytest.mark.asyncio
async def test_node_documents_do_not_retry_provider_errors():
    class FailingVLM:
        calls = 0

        async def complete_json_async(self, **kwargs):
            self.calls += 1
            raise RuntimeError("provider rejected request")

    vlm = FailingVLM()
    generator = NodeContentGenerator(WikiLLMRunner(vlm))

    with pytest.raises(RuntimeError, match="provider rejected request"):
        await generator.generate_direct(
            WikiNode(
                node_id="question_answering",
                title="Question Answering",
                depth=1,
                scope="QA methods and evaluation.",
            ),
            [{"title": "Source", "sections": [{"content": "Evidence."}]}],
        )

    assert vlm.calls == 1


@pytest.mark.asyncio
async def test_staged_generation_uses_three_source_batches_and_preserves_filled_h2():
    fake_vlm = FakeVLM(
        [
            {"markdown": "## Foundations\n\n## Evaluation"},
            {"markdown": "# Question Answering\n\n## Foundations\n\nFirst batch."},
            {"markdown": "# Question Answering\n\n## Evaluation\n\nInvalid rewrite."},
            {
                "markdown": (
                    "# Question Answering\n\n## Foundations\n\nFirst batch.\n\n"
                    "## Evaluation\n\nSecond batch."
                )
            },
        ]
    )
    generator = NodeContentGenerator(WikiLLMRunner(fake_vlm))
    node = WikiNode(
        node_id="question_answering",
        title="Question Answering",
        depth=1,
        scope="QA methods and evaluation.",
    )
    cards = [_card(index) for index in range(4)]
    sources = [_selected(index) for index in range(4)]

    outline = await generator.generate_outline(node, cards)
    document = await generator.generate_staged(node, outline, sources)

    assert [record.step for record in generator.llm.log.raw_outputs] == [
        "node_document_outline",
        "node_documents_initial",
        "node_documents_refine",
        "node_documents_refine",
    ]
    assert all(f"Source {index}" in fake_vlm.calls[1] for index in range(3))
    assert "Source 3" not in fake_vlm.calls[1]
    assert "Source 3" in fake_vlm.calls[2]
    assert fake_vlm.calls[2] == fake_vlm.calls[3]
    assert "## Foundations" in document.content
    assert "## Evaluation" in document.content


@pytest.mark.asyncio
async def test_staged_generation_reduces_batch_size_without_splitting_sources():
    fake_vlm = FakeVLM(
        [
            {"markdown": "## Foundations\n\n## Evaluation"},
            {"markdown": "# Question Answering\n\n## Foundations\n\nFirst."},
            {"markdown": ("# Question Answering\n\n## Foundations\n\nFirst and second.")},
            {
                "markdown": (
                    "# Question Answering\n\n## Foundations\n\nFirst and second.\n\n"
                    "## Evaluation\n\nThird."
                )
            },
        ]
    )
    generator = NodeContentGenerator(
        WikiLLMRunner(fake_vlm),
        max_prompt_tokens=950,
    )
    node = WikiNode(
        node_id="question_answering",
        title="Question Answering",
        depth=1,
        scope="QA methods and evaluation.",
    )
    sources = [
        SelectedSourceDocument(
            source_id=f"doc_{index}",
            title=f"Source {index}",
            sections=[SourceSection(section_uri=f"uri-{index}", content="x" * 1000)],
            relevance_score=1.0 - index / 10,
        )
        for index in range(3)
    ]

    outline = await generator.generate_outline(node, [_card(index) for index in range(3)])
    await generator.generate_staged(node, outline, sources)

    assert "Source 0" in fake_vlm.calls[1]
    assert "Source 1" not in fake_vlm.calls[1]
    assert "Source 1" in fake_vlm.calls[2]
    assert "Source 2" in fake_vlm.calls[3]


def _card(index: int) -> DocumentCard:
    return DocumentCard(
        doc_id=f"doc_{index}",
        resource_uri=f"viking://resources/doc_{index}/",
        title=f"Source {index}",
        summary="Summary",
        main_points=["Point"],
        candidate_topics=["Topic"],
    )


def _selected(index: int) -> SelectedSourceDocument:
    return SelectedSourceDocument(
        source_id=f"doc_{index}",
        title=f"Source {index}",
        sections=[SourceSection(section_uri=f"uri-{index}", content=f"Evidence {index}")],
        relevance_score=1.0 - index / 10,
    )
