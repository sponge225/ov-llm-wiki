from types import SimpleNamespace

import pytest

from openviking.wiki.config import WikiGenerationLimits
from openviking.wiki.schemas import ResourceDocument, SourceRef, SourceSection, WikiNode
from openviking.wiki.source_selector import NodeSourceSelector
from openviking_cli.exceptions import ProcessingError


class FakeVikingFS:
    def __init__(self, results=None, error=None):
        self.results = results or {}
        self.error = error
        self.calls = []

    async def find(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.results[kwargs["target_uri"]]


@pytest.mark.asyncio
async def test_selector_searches_each_source_and_sorts_matches_stably():
    refs = [_ref("b"), _ref("a")]
    docs = {ref.doc_id: _doc(ref.doc_id) for ref in refs}
    fs = FakeVikingFS(
        {
            refs[0].resource_uri: [
                _match("b/second", 0.8),
                _match("b/first", 0.8),
                _match("b/missing", 1.0),
            ],
            refs[1].resource_uri: [_match("a/first", 0.9)],
        }
    )
    limits = WikiGenerationLimits(node_source_retrieval_limit=2, node_source_score_threshold=0.7)

    selected = await NodeSourceSelector(fs, "ctx", limits).select(_node(), refs, docs)

    assert [item.source_id for item in selected] == ["a", "b"]
    assert [section.section_uri for section in selected[1].sections] == [
        "b/first",
        "b/second",
    ]
    assert [call["target_uri"] for call in fs.calls] == [ref.resource_uri for ref in refs]
    assert all(call["level"] == [2] for call in fs.calls)
    assert all(call["limit"] == 2 for call in fs.calls)
    assert all(call["score_threshold"] == 0.7 for call in fs.calls)
    assert all("Canonical scope" in call["query"] for call in fs.calls)


@pytest.mark.asyncio
async def test_selector_logs_and_raises_structured_retrieval_error(caplog):
    ref = _ref("a")
    selector = NodeSourceSelector(
        FakeVikingFS(error=TimeoutError("search timed out")),
        "ctx",
        WikiGenerationLimits(),
    )

    with caplog.at_level("ERROR"), pytest.raises(ProcessingError) as exc_info:
        await selector.select(_node(), [ref], {"a": _doc("a")})

    assert exc_info.value.details["stage"] == "node_source_retrieval"
    assert exc_info.value.details["node_id"] == "topic"
    assert exc_info.value.details["source_id"] == "a"
    assert "node_id=topic" in caplog.text
    assert "source_id=a" in caplog.text
    assert "search timed out" in caplog.text


@pytest.mark.asyncio
async def test_selector_fails_when_no_source_has_a_mappable_match():
    ref = _ref("a")
    selector = NodeSourceSelector(
        FakeVikingFS({ref.resource_uri: [_match("a/missing", 0.9)]}),
        "ctx",
        WikiGenerationLimits(),
    )

    with pytest.raises(ProcessingError, match="No source sections matched"):
        await selector.select(_node(), [ref], {"a": _doc("a")})


def _node():
    return WikiNode(node_id="topic", title="Topic", depth=1, scope="Canonical scope")


def _ref(source_id: str):
    return SourceRef(
        ref_id=source_id,
        doc_id=source_id,
        resource_uri=f"viking://resources/{source_id}/",
        card_uri=f"viking://wiki/cards/{source_id}.card.md",
        title=f"Source {source_id}",
        support_scope="Support",
        matched_topics=["topic", "topic"],
    )


def _doc(source_id: str):
    return ResourceDocument(
        doc_id=source_id,
        resource_uri=f"viking://resources/{source_id}/",
        title=f"Source {source_id}",
        source_sections=[
            SourceSection(section_uri=f"{source_id}/first", content="First"),
            SourceSection(section_uri=f"{source_id}/second", content="Second"),
        ],
    )


def _match(uri: str, score: float):
    return SimpleNamespace(uri=uri, score=score)
