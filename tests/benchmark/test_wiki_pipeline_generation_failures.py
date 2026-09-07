import json
from types import SimpleNamespace

from benchmark.wiki.src import pipeline as pipeline_mod
from benchmark.wiki.src.pipeline import BenchmarkPipeline


def _make_pipeline(tmp_path, adapter=None):
    config = {
        "paths": {"output_dir": str(tmp_path)},
        "execution": {"max_workers": 2, "mode": "vikingbot"},
        "llm": {"model": "fake-model"},
        "dataset_name": "UnitDataset",
    }
    return BenchmarkPipeline(
        config,
        adapter=adapter,
        vector_db=None,
        llm=SimpleNamespace(llm=object()),
    )


def _generated_item(index, generation_failed):
    return {
        "_global_index": index,
        "sample_id": f"sample-{index}",
        "question": f"question-{index}",
        "gold_answers": ["answer"],
        "category": "category",
        "evidence": [],
        "generation_failed": generation_failed,
        "generation_failure_reason": "[ERROR] timeout" if generation_failed else "",
        "retrieval": {"latency_sec": 1, "uris": []},
        "llm": {"final_answer": "" if generation_failed else "answer"},
        "metrics": {"Recall": 0.0 if generation_failed else 1.0},
        "token_usage": {"total_input_tokens": 0, "llm_output_tokens": 0},
    }


def _vikingbot_result(answer, total_time_sec=600, token_usage=None, session_id="session-id"):
    return {
        "answer": answer,
        "total_time_sec": total_time_sec,
        "token_usage": token_usage or {},
        "tools_used_names": [],
        "tools_used": [],
        "iterations_used": 0,
        "debug_log": "",
        "session_id": session_id,
        "stderr_output": "",
        "stdout_output": "",
        "ov_conf_path": "",
        "trace": [],
    }


def test_generation_writes_failed_vikingbot_records_without_aborting(tmp_path, monkeypatch):
    class Adapter:
        def load_and_transform(self):
            return [
                SimpleNamespace(
                    sample_id="sample",
                    qa_pairs=[
                        SimpleNamespace(
                            question="fail",
                            gold_answers=["a"],
                            category="cat",
                            evidence=[],
                        ),
                        SimpleNamespace(
                            question="ok",
                            gold_answers=["b"],
                            category="cat",
                            evidence=[],
                        ),
                    ],
                )
            ]

    pipe = _make_pipeline(tmp_path, adapter=Adapter())

    def fake_vikingbot_query(question, **kwargs):
        if question == "fail":
            return _vikingbot_result(
                "[ERROR] Command timed out after 600 seconds",
                session_id="session-fail",
            )
        result = _vikingbot_result(
            "ok answer",
            total_time_sec=1.5,
            token_usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            session_id="session-ok",
        )
        result["iterations_used"] = 1
        return result

    monkeypatch.setattr(pipeline_mod, "run_vikingbot_query", fake_vikingbot_query)

    pipe.run_generation()

    with open(pipe.generated_file, "r", encoding="utf-8") as f:
        generated = json.load(f)
    with open(pipe.report_file, "r", encoding="utf-8") as f:
        report = json.load(f)

    results = sorted(generated["results"], key=lambda item: item["_global_index"])
    assert generated["summary"]["total_queries"] == 2
    assert generated["summary"]["generation_failed_queries"] == 1
    assert results[0]["generation_failed"] is True
    assert results[0]["llm"]["final_answer"] == ""
    assert results[1]["generation_failed"] is False
    assert results[1]["llm"]["final_answer"] == "ok answer"
    assert report["Generation"]["Generation Failed Queries"] == 1
    assert report["Generation"]["Successful Queries"] == 1
    assert report["Query Efficiency (Average Per Query)"]["Average Retrieval Time (s)"] == 1.5
    assert report["Query Efficiency (Average Per Query)"]["Average Input Tokens"] == 2
    assert report["Query Efficiency (Average Per Query)"]["Average Output Tokens"] == 3
    assert results[1]["token_usage"]["total_input_tokens"] == 2
    assert results[1]["token_usage"]["llm_output_tokens"] == 3
    assert results[1]["token_usage"]["prompt_tokens"] == 2
    assert results[1]["token_usage"]["completion_tokens"] == 3


def test_vikingbot_error_result_is_recorded_as_generation_failure(tmp_path, monkeypatch):
    pipe = _make_pipeline(tmp_path)
    qa = SimpleNamespace(question="q", gold_answers=["a"], category="cat", evidence=[])

    def fake_vikingbot_query(*args, **kwargs):
        return _vikingbot_result("[ERROR] Command timed out after 600 seconds")

    monkeypatch.setattr(pipeline_mod, "run_vikingbot_query", fake_vikingbot_query)

    result = pipe._process_vikingbot_task({"id": 1, "sample_id": "sample", "qa": qa})

    assert result["generation_failed"] is True
    assert result["generation_failure_reason"] == "[ERROR] Command timed out after 600 seconds"
    assert result["llm"]["final_answer"] == ""
    assert result["vikingbot"]["answer"] == "[ERROR] Command timed out after 600 seconds"


def test_evaluation_skips_generation_failures(tmp_path, monkeypatch):
    pipe = _make_pipeline(tmp_path)
    generated = {"results": [_generated_item(0, True), _generated_item(1, False)]}
    with open(pipe.generated_file, "w", encoding="utf-8") as f:
        json.dump(generated, f)

    grader_calls = []

    def fake_grader(*args, **kwargs):
        grader_calls.append(args)
        return {"score": 4.0, "reasoning": "ok", "prompt_type": "fake"}

    monkeypatch.setattr(pipeline_mod, "llm_grader", fake_grader)

    pipe.run_evaluation()

    with open(pipe.eval_file, "r", encoding="utf-8") as f:
        eval_data = json.load(f)
    with open(pipe.report_file, "r", encoding="utf-8") as f:
        report = json.load(f)

    assert len(grader_calls) == 1
    assert [item["_global_index"] for item in eval_data["results"]] == [1]
    assert report["Generation Failed Queries"] == 1
    assert report["Skipped Failed Queries"] == 1
    assert report["Total Queries Evaluated"] == 1
    assert report["Performance Metrics"]["Average Recall"] == 1.0


def test_evaluation_handles_all_generation_failures(tmp_path, monkeypatch):
    pipe = _make_pipeline(tmp_path)
    generated = {"results": [_generated_item(0, True)]}
    with open(pipe.generated_file, "w", encoding="utf-8") as f:
        json.dump(generated, f)

    def fail_grader(*args, **kwargs):
        raise AssertionError("failed generation records should not be evaluated")

    monkeypatch.setattr(pipeline_mod, "llm_grader", fail_grader)

    pipe.run_evaluation()

    with open(pipe.eval_file, "r", encoding="utf-8") as f:
        eval_data = json.load(f)
    with open(pipe.report_file, "r", encoding="utf-8") as f:
        report = json.load(f)

    assert eval_data == {"results": []}
    assert report["Generation Failed Queries"] == 1
    assert report["Skipped Failed Queries"] == 1
    assert report["Total Queries Evaluated"] == 0
    assert report["Performance Metrics"]["Average F1 Score"] == 0
