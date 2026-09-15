import pytest

from openviking.prompts.manager import PromptManager
from openviking.wiki.prompts import (
    build_document_card_prompt,
    build_next_layer_decision_prompt,
    build_node_card_prompt,
    build_node_discovery_prompt,
    build_node_document_outline_prompt,
    build_node_document_refine_prompt,
    build_node_documents_prompt,
)
from openviking.wiki.schemas import (
    DocumentCard,
    GeneratedNodeContext,
    NodeDocument,
    ResourceDocument,
    SourceRef,
    WikiNode,
)

from .test_pipeline_order import _card_response


@pytest.mark.parametrize(
    ("prompt_id", "extra_vars"),
    [
        ("wiki.document_card", {}),
        ("wiki.node_discovery", {}),
        ("wiki.node_card", {}),
        ("wiki.node_documents", {}),
        ("wiki.node_document_outline", {}),
        ("wiki.node_document_refine", {"phase": "initial"}),
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


def test_node_discovery_prompt_uses_only_card_index_fields():
    prompt = build_node_discovery_prompt(
        [DocumentCard.model_validate(_card_response(1))],
        min_sources_per_node=3,
    )

    assert '"summary"' in prompt
    assert '"candidate_topics"' in prompt
    assert '"source_id": "OARW_1"' in prompt
    assert "at least 3 distinct source cards" in prompt
    assert '"min_sources_per_node": 3' in prompt
    assert "at least min_sources_per_node distinct source cards" not in prompt
    assert '"source_unit_count": 1' in prompt
    assert '"main_points"' not in prompt
    assert "viking://resources/" not in prompt


def test_node_discovery_prompt_can_use_short_source_aliases():
    card = DocumentCard.model_validate(_card_response(1))

    prompt = build_node_discovery_prompt(
        [card],
        min_sources_per_node=1,
        source_ids=["S0001"],
    )

    assert '"source_id": "S0001"' in prompt
    assert card.doc_id not in prompt


def test_node_discovery_prompt_rejects_mismatched_source_aliases():
    with pytest.raises(ValueError, match="same length"):
        build_node_discovery_prompt(
            [DocumentCard.model_validate(_card_response(1))],
            min_sources_per_node=1,
            source_ids=[],
        )


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

    assert '"title": "Question Answering"' in prompt
    assert '"scope": "QA methods and evaluation."' in prompt
    assert '"source_documents"' in prompt
    assert '"source_id": "S0001"' in prompt
    assert '"title": ""' in prompt
    assert "OARW_1" not in prompt
    assert "section_uri" not in prompt
    assert "viking://resources/OARW_1/abstract" not in prompt
    assert "Question answering evidence." in prompt
    assert '"node_id"' not in prompt
    assert '"depth"' not in prompt
    assert '"source_refs"' not in prompt
    assert '"cards"' not in prompt
    assert '"child_nodes"' not in prompt
    assert '"main_points"' not in prompt
    assert "Organize the content by synthesized knowledge, not by source document." in prompt
    assert "Do not write one paragraph for source 1, another paragraph for source 2" in prompt
    assert "not a sequence of per-source summaries" in prompt


def test_outline_and_refine_prompts_expose_only_defined_fields():
    node = WikiNode(
        node_id="question_answering",
        title="Question Answering",
        depth=1,
        scope="QA methods and evaluation.",
    )
    card = DocumentCard.model_validate(_card_response(1))

    outline = build_node_document_outline_prompt(node, [card])
    refine = build_node_document_refine_prompt(
        node,
        "# Question Answering\n\n## Methods",
        [
            {
                "source_id": "opaque",
                "title": "Paper 1",
                "sections": [{"section_uri": "secret", "content": "Evidence."}],
            }
        ],
        initial=True,
    )

    assert '"main_points"' in outline
    assert '"candidate_topics"' in outline
    assert "important_terms" not in outline
    assert "resource_uri" not in outline
    assert '"current_markdown"' in refine
    assert '"source_id": "S0001"' in refine
    assert "opaque" not in refine
    assert "section_uri" not in refine
    assert "secret" not in refine


def test_node_card_prompt_uses_node_boundary_and_generated_documents():
    prompt = build_node_card_prompt(
        WikiNode(
            node_id="question_answering",
            title="Question Answering",
            depth=2,
            scope="QA methods and evaluation.",
        ),
        NodeDocument(
            title="Retrieval QA",
            content="Retrieval child document.",
        ),
    )

    assert '"title": "Question Answering"' in prompt
    assert '"scope": "QA methods and evaluation."' in prompt
    assert '"title": "Retrieval QA"' in prompt
    assert "Retrieval child document." in prompt
    assert '"node_id"' not in prompt
    assert '"document_id"' not in prompt
    assert '"source_refs"' not in prompt
    assert "Do not return doc_id, resource_uri, title, markdown, node fields" in prompt
    assert "summary: describe the synthesized knowledge" in prompt
    assert "candidate_topics: list broader parent-level topics" in prompt


def test_next_layer_decision_prompt_omits_persistent_ids_and_source_refs():
    opaque_id = "dsid_5217b3a64c4a433c89ba2a3186dea82a"
    node = WikiNode(
        node_id=opaque_id,
        title="Reliability Standards",
        depth=1,
        scope="Incident ownership and remediation standards.",
        child_node_ids=["child_opaque_id"],
    )
    context = GeneratedNodeContext(
        node=node,
        card=DocumentCard(
            doc_id=opaque_id,
            resource_uri=f"viking://wiki/nodes/{opaque_id}/",
            title=node.title,
            summary="Reliability standards summary.",
            main_points=["Ownership and remediation rules."],
            candidate_topics=["Operational governance"],
        ),
        document=NodeDocument(
            title="Reliability Synthesis",
            content="Synthesized reliability knowledge.",
        ),
        source_refs=[
            SourceRef(
                ref_id="source_opaque_id",
                doc_id="source_opaque_id",
                resource_uri="viking://resources/source_opaque_id",
                card_uri="viking://wiki/cards/source_opaque_id.card.md",
                title="Source",
                support_scope="Supports reliability standards.",
            )
        ],
    )

    prompt = build_next_layer_decision_prompt([context])

    assert '"source_count": 1' in prompt
    assert '"title": "Reliability Standards"' in prompt
    assert "Synthesized reliability knowledge." in prompt
    assert opaque_id not in prompt
    assert "source_opaque_id" not in prompt
    assert "resource_uri" not in prompt
    assert "document_id" not in prompt
    assert "source_refs" not in prompt
    assert "child_node_ids" not in prompt
