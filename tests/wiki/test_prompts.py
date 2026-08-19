import pytest

from openviking.prompts.manager import PromptManager
from openviking.wiki.prompts import (
    build_bottom_node_discovery_prompt,
    build_document_card_prompt,
    build_node_documents_prompt,
    build_parent_node_documents_prompt,
    build_parent_node_discovery_prompt,
)
from openviking.wiki.schemas import (
    DocumentCard,
    GeneratedNodeContext,
    ResourceDocument,
    WikiNode,
)

from .test_pipeline_order import _card_response


@pytest.mark.parametrize(
    ("prompt_id", "extra_vars"),
    [
        ("wiki.document_card", {}),
        ("wiki.bottom_node_discovery", {}),
        ("wiki.parent_node_discovery", {}),
        ("wiki.node_md", {}),
        ("wiki.node_documents", {}),
        ("wiki.parent_node_documents", {}),
        ("wiki.next_layer_decision", {"min_child_nodes_per_parent": 3}),
    ],
)
def test_wiki_prompt_templates_render(prompt_id: str, extra_vars: dict):
    rendered = PromptManager().render(
        prompt_id,
        {
            "input_json": '{"example": true}',
            **extra_vars,
        },
    )

    assert '{"example": true}' in rendered
    assert "Return only JSON matching this shape" not in rendered


def test_wiki_prompt_template_requires_input_json():
    with pytest.raises(ValueError, match="input_json"):
        PromptManager().render(
            "wiki.document_card",
            {},
        )


def test_document_card_prompt_uses_only_semantic_input_fields():
    prompt = build_document_card_prompt(
        ResourceDocument(
            doc_id="paper_1",
            resource_uri="viking://resources/paper_1/",
            title="Paper 1",
            content_or_structure="semantic content",
            metadata={
                "card_input_mode": "summary",
                "missing_summary_uris": ["viking://resources/missing"],
                "root_uri": "viking://resources/root",
            },
        )
    )

    assert '"content_or_structure": "semantic content"' in prompt
    assert '"card_input_mode": "summary"' in prompt
    assert "missing_summary_uris" in prompt
    assert "paper_1" not in prompt
    assert "Paper 1" not in prompt
    assert '"doc_id"' not in prompt
    assert '"source_type"' not in prompt
    assert "root_uri" not in prompt


def test_bottom_node_discovery_prompt_uses_only_topic_index_fields():
    prompt = build_bottom_node_discovery_prompt(
        [DocumentCard.model_validate(_card_response(1))],
        min_refs_per_node=3,
    )

    assert '"summary"' in prompt
    assert '"candidate_topics"' in prompt
    assert '"doc_id": "OARW_1"' in prompt
    assert '"min_refs_per_node": 3' in prompt
    assert '"source_unit_count": 1' in prompt
    assert '"main_points"' not in prompt
    assert "viking://resources/" not in prompt


def test_parent_node_discovery_prompt_omits_generated_content_and_sources():
    prompt = build_parent_node_discovery_prompt(
        [
            GeneratedNodeContext(
                node=WikiNode(
                    node_id="question_answering",
                    title="Question Answering",
                    depth=1,
                    scope="QA methods and evaluation.",
                ),
                node_md="NODE_MD_SENTINEL",
                documents=[],
                source_refs=[],
            )
        ],
        min_child_nodes_per_parent=3,
    )

    assert '"title": "Question Answering"' in prompt
    assert '"scope": "QA methods and evaluation."' in prompt
    assert '"min_child_nodes_per_parent": 3' in prompt
    assert '"node_id"' not in prompt
    assert "NODE_MD_SENTINEL" not in prompt
    assert '"documents"' not in prompt
    assert '"evidence"' not in prompt
    assert '"source_refs"' not in prompt


def test_node_documents_prompt_uses_only_node_boundary_and_source_sections():
    prompt = build_node_documents_prompt(
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

    assert '"title": "Question Answering"' in prompt
    assert '"scope": "QA methods and evaluation."' in prompt
    assert '"source_documents"' in prompt
    assert '"doc_id": "OARW_1"' in prompt
    assert '"section_uri": "viking://resources/OARW_1/abstract"' in prompt
    assert "Question answering evidence." in prompt
    assert '"node_id"' not in prompt
    assert '"depth"' not in prompt
    assert '"source_refs"' not in prompt
    assert '"cards"' not in prompt
    assert '"child_nodes"' not in prompt
    assert '"main_points"' not in prompt
    assert "Organize the content by synthesized knowledge, not by source document." in prompt
    assert "Do not write one paragraph for doc 1, another paragraph for doc 2" in prompt
    assert "not a sequence of per-document summaries" in prompt


def test_parent_node_documents_prompt_uses_child_documents_only():
    prompt = build_parent_node_documents_prompt(
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
                "documents": [
                    {"content": "First child document."},
                    {"content": "Second child document."},
                ],
            }
        ],
    )

    assert '"title": "Question Answering"' in prompt
    assert '"title": "Retrieval QA"' in prompt
    assert "First child document." in prompt
    assert "Second child document." in prompt
    assert '"node_id"' not in prompt
    assert '"document_id"' not in prompt
    assert '"evidence_refs"' not in prompt
    assert '"claims"' not in prompt
    assert '"claim_ref"' not in prompt
    assert '"source_refs"' not in prompt
    assert "The parent node, with a title and scope." in prompt
    assert "A list of child nodes." in prompt
    assert "Use only the provided child node document contents as source material." in prompt
    assert "Treat the parent node title and scope as the strict parent topic boundary." in prompt
    assert "Produce a parent-level knowledge document" in prompt
    assert "Do not organize by child node, source document, or document order." in prompt
    assert "Do not write one paragraph per child node." in prompt
    assert "Each substantive paragraph must:" in prompt
    assert "Final test before answering" in prompt
    assert "not a sequence of per-child summaries" in prompt
