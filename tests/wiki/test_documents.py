import pytest

from openviking.wiki.documents import NodeContentGenerator
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.schemas import WikiNode

from .fakes import FakeVLM


@pytest.mark.asyncio
async def test_node_md_retries_invalid_json_result_with_same_prompt():
    fake_vlm = FakeVLM(
        [
            None,
            {"node_md": "# Question Answering\n\n## Scope\n\nQA scope."},
        ]
    )
    generator = NodeContentGenerator(WikiLLMRunner(fake_vlm))

    node_md = await generator.generate_node_md(
        WikiNode(
            node_id="question_answering",
            title="Question Answering",
            depth=1,
            scope="QA methods and evaluation.",
        )
    )

    assert node_md == "# Question Answering\n\n## Scope\n\nQA scope."
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


@pytest.mark.asyncio
async def test_node_documents_retries_empty_documents_with_same_prompt():
    fake_vlm = FakeVLM(
        [
            {"documents": []},
            {
                "documents": [
                    {
                        "title": "Node Synthesis",
                        "content": "# Node Synthesis\n\nValid content.",
                    }
                ],
            },
        ]
    )
    generator = NodeContentGenerator(WikiLLMRunner(fake_vlm))

    documents = await generator.generate_node_documents(
        WikiNode(
            node_id="question_answering",
            title="Question Answering",
            depth=1,
            scope="QA methods and evaluation.",
        ),
        [
            {
                "doc_id": "OARW_1",
                "sections": [
                    {
                        "section_uri": "viking://resources/OARW_1/abstract",
                        "content": "Question answering evidence.",
                    }
                ],
            }
        ],
    )

    assert documents[0].document_id == "0001"
    assert documents[0].content == "# Node Synthesis\n\nValid content."
    assert len(fake_vlm.calls) == 2
    assert fake_vlm.calls[0] == fake_vlm.calls[1]


@pytest.mark.asyncio
async def test_parent_node_documents_uses_child_documents_only():
    fake_vlm = FakeVLM(
        [
            {
                "documents": [
                    {
                        "title": "Parent Synthesis",
                        "content": "# Parent Synthesis\n\nRetrieval QA depends on retrieved passages.",
                    }
                ],
            }
        ]
    )
    generator = NodeContentGenerator(WikiLLMRunner(fake_vlm))

    documents = await generator.generate_parent_node_documents(
        WikiNode(
            node_id="question_answering",
            title="Question Answering",
            depth=2,
            scope="QA methods and evaluation.",
        ),
        [
            {
                "title": "Retrieval QA",
                "scope": "Retrieval-grounded answering.",
                "documents": [{"content": "Retrieval child document."}],
            }
        ],
    )

    assert documents[0].document_id == "0001"
    assert "Retrieval QA depends" in documents[0].content
    assert '"claims"' not in fake_vlm.calls[0]
    assert '"claim_ref"' not in fake_vlm.calls[0]
