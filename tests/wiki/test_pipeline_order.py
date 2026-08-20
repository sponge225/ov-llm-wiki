import json

import pytest

from openviking.wiki.config import WikiConfig, WikiGenerationLimits
from openviking.wiki.llm import WikiLLMRunner
from openviking.wiki.pipeline import WikiPipeline
from openviking.wiki.schemas import ResourceDocument, SourceSection, WikiResourceInput
from openviking.wiki.writer import WikiVikingFSWriter

from .fakes import FakeClient, FakeVLM


@pytest.mark.asyncio
async def test_pipeline_generates_layer_content_before_next_layer_decision():
    docs = [_doc(index) for index in range(1, 4)]
    wiki_inputs = [_wiki_input(doc) for doc in docs]
    fake_vlm = FakeVLM(
        [
            _card_content_response(1),
            _card_content_response(2),
            _card_content_response(3),
            _node_discovery_response(),
            {"node_md": "# Question Answering\n\n## Scope\n\nQA scope."},
            {
                "documents": [
                    {
                        "title": "High-Level Knowledge",
                        "content": "# High-Level Knowledge\n\nSynthesized QA knowledge.",
                    }
                ],
            },
            {"continue_upward": False, "reasons": ["no stable parent layer"]},
        ]
    )
    llm = WikiLLMRunner(fake_vlm)
    client = FakeClient()
    config = WikiConfig()
    writer = WikiVikingFSWriter(
        viking_fs=client,
        vikingdb=object(),
        ctx=object(),
        config=config,
        content_writer=client,
    )

    artifacts = await WikiPipeline(writer=writer, config=config, llm=llm).run_from_inputs(
        wiki_inputs,
        content_loader=FakeContentLoader(docs),
    )

    assert [record.step for record in llm.log.raw_outputs] == [
        "doc_card",
        "doc_card",
        "doc_card",
        "bottom_node_discovery",
        "node_md",
        "node_documents",
        "next_layer_decision",
    ]
    assert "viking://wiki/nodes/question_answering/documents/0001.md" in client.writes
    assert "viking://wiki/nodes/question_answering/evidence.jsonl" not in client.writes
    assert artifacts.node_contexts[0].documents[0].document_id == "0001"


@pytest.mark.asyncio
async def test_pipeline_run_config_redacts_sensitive_vlm_config():
    client = FakeClient()
    config = WikiConfig(
        vlm_config={
            "provider": "volcengine",
            "api_key": "secret-key",
            "nested": {"token": "secret-token", "model": "demo"},
        }
    )
    writer = WikiVikingFSWriter(
        viking_fs=client,
        vikingdb=object(),
        ctx=object(),
        config=config,
        content_writer=client,
    )

    await WikiPipeline(writer=writer, config=config, llm=WikiLLMRunner(FakeVLM([])))._write_run_records()

    run_config = json.loads(client.writes["viking://wiki/run/config.json"])
    assert run_config["model_config"]["provider"] == "volcengine"
    assert run_config["model_config"]["nested"]["model"] == "demo"
    assert run_config["model_config"]["api_key"] == "***REDACTED***"
    assert run_config["model_config"]["nested"]["token"] == "***REDACTED***"
    assert "secret-key" not in client.writes["viking://wiki/run/config.json"]
    assert "secret-token" not in client.writes["viking://wiki/run/config.json"]


@pytest.mark.asyncio
async def test_pipeline_does_not_precreate_unassigned_active_node_dirs():
    docs = [_doc(index) for index in range(1, 4)]
    wiki_inputs = [_wiki_input(doc) for doc in docs]
    fake_vlm = FakeVLM(
        [
            _card_content_response(1),
            _card_content_response(2),
            _card_content_response(3),
            {
                "nodes": [
                    {
                        "title": "Question Answering",
                        "scope": "QA methods and evaluation.",
                        "supporting_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
                        "merged_candidate_topics": ["question answering"],
                    },
                    {
                        "title": "Unassigned Topic",
                        "scope": "No assigned sources.",
                        "supporting_doc_ids": ["OARW_1"],
                        "merged_candidate_topics": ["unknown topic"],
                    },
                ]
            },
            {"node_md": "# Question Answering\n\n## Scope\n\nQA scope."},
            {
                "documents": [
                    {
                        "title": "High-Level Knowledge",
                        "content": "# High-Level Knowledge\n\nSynthesized QA knowledge.",
                    }
                ],
            },
            {"continue_upward": False, "reasons": ["no stable parent layer"]},
        ]
    )
    llm = WikiLLMRunner(fake_vlm)
    client = FakeClient()
    config = WikiConfig(limits=WikiGenerationLimits(min_refs_per_node=2))
    writer = WikiVikingFSWriter(
        viking_fs=client,
        vikingdb=object(),
        ctx=object(),
        config=config,
        content_writer=client,
    )

    artifacts = await WikiPipeline(writer=writer, config=config, llm=llm).run_from_inputs(
        wiki_inputs,
        content_loader=FakeContentLoader(docs),
    )

    assert "viking://wiki/nodes/question_answering/" in client.mkdirs
    assert "viking://wiki/nodes/unassigned_topic/" not in client.mkdirs
    assert "viking://wiki/nodes/unassigned_topic/documents/" not in client.mkdirs
    assert "viking://wiki/nodes/unassigned_topic/sources/" not in client.mkdirs
    rejected = [node for node in artifacts.nodes if node.node_id == "unassigned_topic"]
    assert rejected[0].status == "rejected"


def _doc(index: int) -> ResourceDocument:
    content = f"# Paper {index}\n\nContent about question answering."
    return ResourceDocument(
        doc_id=f"OARW_{index}",
        resource_uri=f"viking://resources/OARW_{index}/",
        title=f"Paper {index}",
        content_or_structure=content,
        source_sections=[
            SourceSection(
                section_uri=f"viking://resources/OARW_{index}/",
                content=content,
            )
        ],
    )


def _wiki_input(doc: ResourceDocument) -> WikiResourceInput:
    return WikiResourceInput(
        doc_id=doc.doc_id,
        resource_uri=doc.resource_uri,
        title=doc.title,
        document_dir_uri=doc.resource_uri,
    )


class FakeContentLoader:
    def __init__(self, docs: list[ResourceDocument]):
        self.docs_by_id = {doc.doc_id: doc for doc in docs}

    async def load_document(
        self,
        doc: WikiResourceInput,
        *,
        mode: object,
        max_card_input_chars: int,
    ) -> ResourceDocument:
        return self.docs_by_id[doc.doc_id]


def _card_response(index: int) -> dict:
    return {
        "doc_id": f"OARW_{index}",
        "resource_uri": f"viking://resources/OARW_{index}/",
        "title": f"Paper {index}",
        **_card_content_response(index),
    }


def _card_content_response(index: int) -> dict:
    return {
        "summary": f"Paper {index} discusses question answering.",
        "main_points": ["QA method"],
        "important_terms": ["question answering"],
        "candidate_topics": ["question answering"],
    }


def _node_discovery_response() -> dict:
    return {
        "nodes": [
            {
                "title": "Question Answering",
                "scope": "QA methods and evaluation.",
                "supporting_doc_ids": ["OARW_1", "OARW_2", "OARW_3"],
                "merged_candidate_topics": ["question answering"],
            }
        ]
    }
