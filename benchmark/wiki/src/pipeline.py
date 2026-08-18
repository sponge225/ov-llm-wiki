import os
import json
import time
import random
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent))

from adapters.base import BaseAdapter
from core.logger import get_logger
from core.vector_store import VikingStoreWrapper
from core.monitor import BenchmarkMonitor
from core.metrics import MetricsCalculator
from core.judge_util import llm_grader
from core.execution_mode import BASELINE_MODE, VIKINGBOT_MODE, resolve_execution_mode
from vikingbot_runner import run_vikingbot_query


class BenchmarkPipeline:
    def __init__(self, config, adapter: BaseAdapter, vector_db: VikingStoreWrapper, llm):
        self.config = config
        self.adapter = adapter
        self.db = vector_db
        self.llm = llm
        self.logger = get_logger()
        self.monitor = BenchmarkMonitor()
        
        self.output_dir = self.config['paths']['output_dir']
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)
        self.generated_file = os.path.join(self.output_dir, "generated_answers.json")
        self.eval_file = os.path.join(self.output_dir, "qa_eval_detailed_results.json")
        self.report_file = os.path.join(self.output_dir, "benchmark_metrics_report.json")
        self.resource_manifest_file = os.path.join(self.output_dir, "imported_resources.json")
        
        self.metrics_summary = {
            "insertion": {"time": 0, "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0},
            "deletion": {"time": 0, "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0}
        }

    def run_import(self):
        """Stage: Import documents into the OpenViking store."""
        self.logger.info(">>> Stage: Import")
        if not self.db:
            raise RuntimeError("Cannot ingest without a vector store")

        doc_dir = self.config['paths'].get('doc_output_dir')
        if not doc_dir:
            doc_dir = os.path.join(self.output_dir, "docs")

        try:
            doc_info = self.adapter.data_prepare(doc_dir)
        except Exception as e:
            self.logger.exception(f"Data preparation failed: {e}")
            exit(1)

        ingest_mode = self.config['execution'].get('ingest_mode', 'per_file')
        mode_desc = {
            'directory': 'Unified directory mode',
            'per_file': 'Per-file mode'
        }
        self.logger.info(f"Ingestion mode: {ingest_mode} ({mode_desc.get(ingest_mode, 'Unknown mode')})")
        self.logger.info(f"Number of documents: {len(doc_info)}")

        ingest_stats = self.db.ingest(
            doc_info,
            monitor=self.monitor,
            ingest_mode=ingest_mode,
        )
        self._write_resource_manifest(ingest_stats.get("resource_uris", []))
        self.metrics_summary["insertion"] = ingest_stats
        self.logger.info(f"Insertion finished. Time: {ingest_stats['time']:.2f}s")

        self._update_report({
            "Insertion Efficiency (Total Dataset)": {
                "Total Insertion Time (s)": self.metrics_summary["insertion"]["time"],
                "Total Input Tokens": self.metrics_summary["insertion"]["input_tokens"],
                "Total Output Tokens": self.metrics_summary["insertion"]["output_tokens"],
                "Total Embedding Tokens": self.metrics_summary["insertion"].get("embedding_tokens", 0)
            }
        })

    def run_build_wiki(self):
        """Stage: Build Wiki from resources imported by the import step."""
        self.logger.info(">>> Stage: Build Wiki")
        if not self.db:
            raise RuntimeError("Cannot build Wiki without a vector store")

        resource_uris = self._read_resource_manifest()
        wiki_card_input_mode = self.config['execution'].get('wiki_card_input_mode', 'summary')
        wiki_max_card_input_chars = int(
            self.config['execution'].get('wiki_max_card_input_chars', 20000)
        )
        self.logger.info(f"Building Wiki for {len(resource_uris)} resource roots")
        wiki_stats = self.db.build_wiki(
            resource_uris=resource_uris,
            card_input_mode=wiki_card_input_mode,
            max_card_input_chars=wiki_max_card_input_chars,
        )
        self.logger.info(f"Wiki build finished. Time: {wiki_stats['time']:.2f}s")
        self._update_report({
            "Wiki Generation": {
                "Total Wiki Build Time (s)": wiki_stats["time"],
                "Resource Roots": resource_uris,
                "Status": wiki_stats.get("status"),
                "Cards": wiki_stats.get("cards", 0),
                "Nodes": wiki_stats.get("nodes", 0),
                "Node Contexts": wiki_stats.get("node_contexts", 0),
            }
        })
        return wiki_stats

    def run_generation(self):
        """Stage: Generate answers for QA queries."""
        self.logger.info(">>> Stage: Generation")
        mode = resolve_execution_mode(self.config)
        if mode == BASELINE_MODE and not self.db:
            raise RuntimeError("Baseline generation requires a vector store")
        
        samples = self.adapter.load_and_transform()    
        tasks = self._prepare_tasks(samples)
        results_map = {}
        max_workers = self.config['execution']['max_workers']
        task_errors = []
        process_fn = self._process_vikingbot_task if mode == VIKINGBOT_MODE else self._process_generation_task
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(process_fn, task): task
                for task in tasks
            }
            
            pbar = tqdm(total=len(tasks), desc="Generating Answers", unit="task")
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    res = future.result()
                    results_map[res['_global_index']] = res
                except Exception as e:
                    self.logger.error(f"Generation failed for task {task['id']}: {e}")
                    task_errors.append((task['id'], e))
                pbar.set_postfix(self.monitor.get_status_dict())
                pbar.update(1)
            pbar.close()

        if task_errors:
            first_id, first_err = task_errors[0]
            raise RuntimeError(
                f"Generation failed for {len(task_errors)} tasks; first failure task_id={first_id}: {type(first_err).__name__}: {first_err}"
            ) from first_err

        sorted_results = [results_map[i] for i in sorted(results_map.keys())]
        dataset_name = self.config.get('dataset_name', 'Unknown_Dataset')
        save_data = {
            "summary": {"dataset": dataset_name, "total_queries": len(sorted_results)},
            "results": sorted_results
        }
        total = len(sorted_results)
        if total > 0:
            self._update_report({
                    "Query Efficiency (Average Per Query)": {
                        "Average Retrieval Time (s)": sum(r['retrieval']['latency_sec'] for r in sorted_results) / total,
                        "Average Input Tokens": sum(r['token_usage']['total_input_tokens'] for r in sorted_results) / total,
                        "Average Output Tokens": sum(r['token_usage']['llm_output_tokens'] for r in sorted_results) / total,
                    }
                }
            )
        with open(self.generated_file, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2, ensure_ascii=False)

    def run_evaluation(self):
        """Step 4: Evaluation"""
        self.logger.info(">>> Stage: Evaluation")

        if not os.path.exists(self.generated_file):
            raise FileNotFoundError(f"Generated answers file not found: {self.generated_file}")

        with open(self.generated_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data.get("results", [])

        eval_items = items
        eval_results_map = {}
        task_errors = []
        
        with ThreadPoolExecutor(max_workers=self.config['execution']['max_workers']) as executor:
            future_to_item = {
                executor.submit(self._process_evaluation_task, item): item 
                for item in eval_items
            }
            
            pbar = tqdm(total=len(eval_items), desc="Evaluating", unit="item")
            for future in as_completed(future_to_item):
                try:
                    res = future.result()
                    eval_results_map[res['_global_index']] = res
                except Exception as e:
                    self.logger.error(f"Evaluation failed: {e}")
                    task_errors.append(e)
                pbar.update(1)
            pbar.close()

        if task_errors:
            first_err = task_errors[0]
            raise RuntimeError(
                f"Evaluation failed for {len(task_errors)} items; "
                f"first error: {type(first_err).__name__}: {first_err}"
            ) from first_err

        eval_records = list(eval_results_map.values())
        total = len(eval_records)
        if eval_items and total == 0:
            raise RuntimeError("Evaluation produced no records")

        with open(self.eval_file, "w", encoding="utf-8") as f:
            json.dump({"results": eval_records}, f, indent=2, ensure_ascii=False)

        if total > 0:
            self._update_report({
                "Dataset": self.config.get('dataset_name', 'Unknown_Dataset'),
                "Total Queries Evaluated": total,
                "Performance Metrics": {
                    "Average F1 Score": sum(r['metrics']['F1'] for r in eval_records) / total,
                    "Average Recall": sum(r['metrics']['Recall'] for r in eval_records) / total,
                    "Average Accuracy (Hit 0-4)": sum(r['metrics']['Accuracy'] for r in eval_records) / total,
                    "Average Accuracy (normalization)": (sum(r['metrics']['Accuracy'] for r in eval_records) / total)/4,
                }
            })

    def run_deletion(self):
        """Step 5: Cleanup"""
        self.logger.info(">>> Stage: Deletion")
        start_time = time.time()
        self.db.clear()
        duration = time.time() - start_time
        self.metrics_summary["deletion"] = {"time": duration, "input_tokens": 0, "output_tokens": 0}
        self.logger.info(f"Deletion finished. Time: {duration:.2f}s")

        self._update_report({
            "Deletion Efficiency (Total Dataset)": {
                "Total Deletion Time (s)": duration,
                "Total Input Tokens": 0,
                "Total Output Tokens": 0
            }
        })

    def _prepare_tasks(self, samples):
        tasks = []
        global_idx = 0
        max_queries = self.config['execution'].get('max_queries')
        for sample in samples:
            for qa in sample.qa_pairs:
                if max_queries is not None and global_idx >= max_queries:
                    break
                tasks.append({"id": global_idx, "sample_id": sample.sample_id, "qa": qa})
                global_idx += 1
            if max_queries is not None and global_idx >= max_queries:
                break
        return tasks

    def _process_generation_task(self, task):
        self.monitor.worker_start()
        try:
            qa = task['qa']
            
            t0 = time.time()
            # Get retrieval instruction from config, default to empty
            retrieval_instruction = self.config['execution'].get('retrieval_instruction', '')
            # Build enhanced query with instruction if provided
            if retrieval_instruction:
                enhanced_query = f"{retrieval_instruction} {qa.question}"
                self.logger.debug(f"[Query-{task['id']}] Using retrieval instruction: {retrieval_instruction}")
                self.logger.debug(f"[Query-{task['id']}] Enhanced query: {enhanced_query}")
            else:
                enhanced_query = qa.question
                self.logger.debug(f"[Query-{task['id']}] No retrieval instruction, using raw query")
            search_res = self.db.retrieve(query=enhanced_query, topk=self.config['execution']['retrieval_topk'])
            latency = time.time() - t0
            
            retrieved_texts = []
            retrieved_uris = []
            context_blocks = []
            
            for r in search_res.resources:
                retrieved_uris.append(r.uri)
                content = self.db.read_resource(r.uri) if getattr(r, 'level', 2) == 2 else f"{getattr(r, 'abstract', '')}\n{getattr(r, 'overview', '')}"
                retrieved_texts.append(content)
                clean = content[:8000]
                context_blocks.append(clean)
            
            recall = MetricsCalculator.check_recall(retrieved_texts, qa.evidence)
            
            full_prompt, meta = self.adapter.build_prompt(qa, context_blocks)
            
            ans_raw = self.llm.generate(full_prompt)

            ans = self.adapter.post_process_answer(qa, ans_raw, meta)

            in_tokens = self.db.count_tokens(full_prompt) + self.db.count_tokens(qa.question)
            out_tokens = self.db.count_tokens(ans)
            self.monitor.worker_end(tokens=in_tokens + out_tokens)
            
            self.logger.info(f"[Query-{task['id']}] Q: {qa.question[:30]}... | Recall: {recall:.2f} | Latency: {latency:.2f}s")

            return {
                "_global_index": task['id'], "sample_id": task['sample_id'], "question": qa.question,
                "gold_answers": qa.gold_answers, "category": str(qa.category), "evidence": qa.evidence,
                "retrieval": {"latency_sec": latency, "uris": retrieved_uris},
                "llm": {"final_answer": ans},
                "metrics": {"Recall": recall}, "token_usage": {"total_input_tokens": in_tokens, "llm_output_tokens": out_tokens}
            }
        except Exception:
            self.monitor.worker_end(success=False)
            raise

    def _process_vikingbot_task(self, task):
        self.monitor.worker_start()
        try:
            qa = task['qa']
            self.logger.info(f"[Query-{task['id']}] Using VikingBot for Wiki QA")

            session_id = f"wiki_{task['id']}_{uuid.uuid4().hex}"
            vikingbot_result = run_vikingbot_query(
                question=qa.question,
                config=self.config,
                session_id=session_id,
            )

            ans = str(vikingbot_result.get("answer", "") or "")
            stripped_ans = ans.strip()
            if (
                not stripped_ans
                or stripped_ans.startswith("[ERROR]")
                or stripped_ans.lower().startswith("error calling llm")
            ):
                raise RuntimeError(ans.strip() or "VikingBot returned an empty answer")

            trace_file = self._write_vikingbot_trace(task['id'], vikingbot_result.get("trace"))
            total_time_sec = float(vikingbot_result.get("total_time_sec", 0) or 0)
            token_usage = vikingbot_result.get("token_usage", {}) or {}
            prompt_tokens = int(token_usage.get("prompt_tokens", token_usage.get("input_tokens", 0)) or 0)
            completion_tokens = int(token_usage.get("completion_tokens", token_usage.get("output_tokens", 0)) or 0)
            total_tokens = int(token_usage.get("total_tokens") or (prompt_tokens + completion_tokens))
            self.monitor.worker_end(tokens=prompt_tokens + completion_tokens)

            self.logger.info(
                f"[Query-{task['id']}] VikingBot | "
                f"Iterations: {vikingbot_result.get('iterations_used', 0)} | "
                f"Time: {total_time_sec:.1f}s"
            )

            return {
                "_global_index": task['id'], "sample_id": task['sample_id'], "question": qa.question,
                "gold_answers": qa.gold_answers, "category": str(qa.category), "evidence": qa.evidence,
                "retrieval": {"latency_sec": total_time_sec, "uris": []},
                "llm": {"final_answer": ans},
                "vikingbot": {
                    "answer": ans,
                    "iterations_used": int(vikingbot_result.get("iterations_used", 0) or 0),
                    "tools_used_names": vikingbot_result.get("tools_used_names", []),
                    "tools_used": vikingbot_result.get("tools_used", []),
                    "total_time_sec": total_time_sec,
                    "debug_log": vikingbot_result.get("debug_log", ""),
                    "session_id": vikingbot_result.get("session_id", ""),
                    "stderr_output": vikingbot_result.get("stderr_output", ""),
                    "stdout_output": vikingbot_result.get("stdout_output", ""),
                    "ov_conf_path": vikingbot_result.get("ov_conf_path", ""),
                    "trace_file": trace_file,
                },
                "metrics": {"Recall": 0.0},
                "token_usage": {
                    "total_input_tokens": 0,
                    "llm_output_tokens": 0,
                    "retrieval_embedding_tokens": 0,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
            }
        except Exception:
            self.monitor.worker_end(success=False)
            raise

    def _write_vikingbot_trace(self, task_id, trace):
        if not trace:
            return ""
        trace_dir = os.path.join(self.output_dir, "traces")
        os.makedirs(trace_dir, exist_ok=True)
        trace_file = os.path.join(trace_dir, f"query_{task_id}_trace.json")
        with open(trace_file, "w", encoding="utf-8") as f:
            json.dump(trace, f, ensure_ascii=False, indent=2, default=str)
        return trace_file

    def _process_evaluation_task(self, item):
        """
        Process a single evaluation task, computing F1 and Accuracy metrics.
        
        For multi-annotator scenarios (like Qasper dataset), a question may have multiple gold answers.
        Evaluation logic:
        - F1: Compute for each gold answer separately and take the maximum
        - Accuracy: Pass all gold answers to LLM at once for comprehensive judgment
        
        This correctly handles multi-annotator scenarios while maintaining compatibility with single-answer datasets (like Locomo).
        """
        ans, golds = item['llm']['final_answer'], item['gold_answers']
        
        f1 = max((MetricsCalculator.calculate_f1(ans, gt) for gt in golds), default=0.0)
        
        dataset_name = self.config.get('dataset_name', 'Unknown_Dataset')
        
        eval_record = {
            "score": 0.0,
            "reasoning": "",
            "prompt_type": ""
        }
        
        try:
            eval_res = llm_grader(
                self.llm.llm, 
                self.config['llm']['model'], 
                item['question'], 
                golds,
                ans,
                dataset_name=dataset_name
            )
            eval_record = eval_res
                
        except Exception as e:
            self.logger.error(f"Grader error: {e}")
            
        if MetricsCalculator.check_refusal(ans) and any(MetricsCalculator.check_refusal(gt) for gt in golds):
            f1 = 1.0
            eval_record["score"] = 4.0
            eval_record["reasoning"] = "System successfully identified Unanswerable/Refusal condition."
            eval_record["prompt_type"] = "Heuristic_Refusal_Check"

        acc = eval_record["score"]

        item["metrics"].update({"F1": f1, "Accuracy": acc})
        
        item["llm_evaluation"] = {
            "prompt_used": eval_record["prompt_type"],
            "reasoning": eval_record["reasoning"],
            "normalized_score": acc
        }

        detailed_info = (
            f"\n" + "="*60 +
            f"\n[Query ID]: {item['_global_index']}"
            f"\n[Question]: {item['question']}"
            f"\n[Retrieved URIs]: {item['retrieval'].get('uris', [])}"
            f"\n[LLM Answer]: {ans}"
            f"\n[Gold Answer]: {golds}"
            f"\n[Metrics]: {item['metrics']}"
            f"\n[LLM Judge Reasoning]: {eval_record['reasoning']}"
            f"\n" + "="*60
        )
        self.logger.info(detailed_info)
        return item

    def _update_report(self, data):
        """Read existing report, merge new data, and write back"""
        report = {}
        if os.path.exists(self.report_file):
            with open(self.report_file, "r", encoding="utf-8") as f:
                try:
                    report = json.load(f)
                except json.JSONDecodeError:
                    report = {}
        report.update(data)
        with open(self.report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=4, ensure_ascii=False)
        self.logger.info(f"Report updated -> {self.report_file}")

    def _write_resource_manifest(self, resource_uris):
        data = {"resource_uris": list(resource_uris or [])}
        with open(self.resource_manifest_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Imported resource manifest -> {self.resource_manifest_file}")

    def _read_resource_manifest(self):
        if not os.path.exists(self.resource_manifest_file):
            raise FileNotFoundError(
                f"Imported resource manifest not found: {self.resource_manifest_file}. "
                "Run --step import first."
            )
        with open(self.resource_manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        resource_uris = data.get("resource_uris", [])
        if not resource_uris:
            raise RuntimeError(
                f"Imported resource manifest contains no resource roots: {self.resource_manifest_file}"
            )
        return resource_uris
