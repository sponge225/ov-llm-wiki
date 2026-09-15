import json
from pathlib import Path
from types import SimpleNamespace

from benchmark.wiki.src.pipeline import BenchmarkPipeline


def test_card_and_wiki_stages_keep_separate_metrics(tmp_path):
    db = FakeVectorStore()
    pipeline = _pipeline(tmp_path, db)
    pipeline._write_resource_manifest(["viking://resources/demo"])

    pipeline.run_build_cards()
    pipeline.run_build_wiki()

    report = json.loads(Path(pipeline.report_file).read_text(encoding="utf-8"))
    assert report["Document Card Generation"]["Total Tokens"] == 30
    assert report["Document Card Generation"]["LLM Call Count"] == 2
    assert report["Document Card Generation"]["Cards"] == 2
    assert report["Wiki Generation"]["Total Tokens"] == 70
    assert report["Wiki Generation"]["LLM Call Count"] == 4
    assert report["Wiki Generation"]["Cards Reused"] is True
    assert db.calls[:2] == [
        (
            "build_cards",
            {
                "resource_uris": ["viking://resources/demo"],
                "card_input_mode": "summary",
                "max_card_input_chars": 20000,
            },
        ),
        ("build_wiki", {"resource_uris": ["viking://resources/demo"]}),
    ]


def test_clear_wiki_preserve_cards_is_forwarded_and_reported(tmp_path):
    db = FakeVectorStore()
    pipeline = _pipeline(tmp_path, db)

    result = pipeline.run_clear_wiki(preserve_cards=True)

    report = json.loads(Path(pipeline.report_file).read_text(encoding="utf-8"))
    assert result["cards_preserved"] is True
    assert db.calls == [("clear_wiki", {"preserve_cards": True})]
    assert report["Wiki Cleanup"]["Cards Preserved"] is True
    assert report["Wiki Cleanup"]["Removed Paths"] == ["viking://wiki/nodes/"]


def test_resource_manifest_is_reused_across_output_directories(tmp_path):
    db = FakeVectorStore()
    vector_store = tmp_path / "shared_store"
    first = _pipeline(
        tmp_path / "baseline",
        db,
        vector_store=vector_store,
    )
    first._write_resource_manifest(["viking://resources/demo"])

    second = _pipeline(
        tmp_path / "wiki",
        db,
        vector_store=vector_store,
    )

    assert second._read_resource_manifest() == ["viking://resources/demo"]
    assert Path(second.resource_manifest_file).exists()


def test_resource_manifest_is_recovered_from_legacy_vector_store(tmp_path):
    db = FakeVectorStore(resource_roots=["viking://resources/demo_processed_docs"])
    pipeline = _pipeline(
        tmp_path / "wiki",
        db,
        vector_store=tmp_path / "legacy_store",
        doc_output_dir=tmp_path / "demo_processed_docs",
    )

    assert pipeline._read_resource_manifest() == [
        "viking://resources/demo_processed_docs"
    ]
    assert Path(pipeline.resource_manifest_file).exists()
    assert Path(f"{pipeline.config['paths']['vector_store']}.imported_resources.json").exists()


def _pipeline(
    tmp_path,
    db,
    *,
    vector_store=None,
    doc_output_dir=None,
):
    config = {
        "paths": {
            "output_dir": str(tmp_path),
            "vector_store": str(vector_store or (tmp_path / "store")),
            "doc_output_dir": str(doc_output_dir or (tmp_path / "demo")),
        },
        "execution": {
            "max_workers": 1,
            "mode": "vikingbot",
            "wiki_card_input_mode": "summary",
            "wiki_max_card_input_chars": 20000,
        },
        "llm": {"model": "fake-model"},
        "dataset_name": "UnitDataset",
    }
    return BenchmarkPipeline(
        config,
        adapter=None,
        vector_db=db,
        llm=SimpleNamespace(llm=object()),
    )


class FakeVectorStore:
    def __init__(self, resource_roots=None):
        self.calls = []
        self.resource_roots = list(resource_roots or [])

    def list_resource_roots(self):
        return self.resource_roots

    def build_wiki_cards(self, **kwargs):
        self.calls.append(("build_cards", kwargs))
        return {
            "time": 1.5,
            "status": "success",
            "cards": 2,
            "card_manifest_uri": "viking://wiki/cards/manifest.json",
            "token_usage": {
                "total_usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "call_count": 0,
                },
                "usage_by_model": {
                    "fake-model": {"total_usage": {"call_count": 2}}
                },
            },
        }

    def build_wiki(self, **kwargs):
        self.calls.append(("build_wiki", kwargs))
        return {
            "time": 2.5,
            "status": "success",
            "cards_reused": True,
            "card_manifest_uri": "viking://wiki/cards/manifest.json",
            "nodes": 3,
            "node_contexts": 3,
            "token_usage": {
                "total_usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 20,
                    "total_tokens": 70,
                    "call_count": 0,
                },
                "usage_by_model": {
                    "fake-model": {"total_usage": {"call_count": 4}}
                },
            },
        }

    def clear_wiki(self, **kwargs):
        self.calls.append(("clear_wiki", kwargs))
        return {
            "status": "success",
            "wiki_root_uri": "viking://wiki/",
            "cleared": True,
            "missing": False,
            "cards_preserved": True,
            "removed_paths": ["viking://wiki/nodes/"],
        }
