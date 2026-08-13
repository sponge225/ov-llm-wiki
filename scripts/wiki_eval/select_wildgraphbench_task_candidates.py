#!/usr/bin/env python3
"""Select WildGraphBench document sets for LLM Wiki task drafting.

This script uses WildGraphBench summary tasks as weak supervision:
  .trae/dataset_scout/high_reuse/WildGraphBench/QA/{domain}/questions.jsonl

For each summary task, it resolves ref_urls to local reference_pages/*.txt,
scores the source-document set, and emits candidate bundles for LLM Wiki task
drafting. It does not call LLMs and does not import anything into OpenViking.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WGB_DIR = ROOT / ".trae/dataset_scout/high_reuse/WildGraphBench"
OUT_DIR = ROOT / "data/wiki_eval"

MIN_REFS = 3
MAX_REFS = 8
MIN_MAPPED_REFS = 3
MIN_DOC_WORDS = 500
MAX_DOC_WORDS = 12000
MIN_TOTAL_WORDS = 3000
MAX_TOTAL_WORDS = 45000

SYNTHESIS_TERMS = {
    "approach",
    "strategy",
    "development",
    "evolution",
    "reception",
    "impact",
    "patterns",
    "trade-off",
    "tradeoff",
    "policy",
    "organization",
    "capabilities",
    "limitations",
    "quality control",
    "governance",
    "creative strategy",
    "financial motivations",
}

WEAK_LIST_TERMS = {
    "what is",
    "who is",
    "when did",
    "where did",
    "what are the",
    "provide an overview of",
}


def clean_url(url: str | None) -> str:
    return (url or "").split("#", 1)[0].strip()


def normalize_for_matching(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\.txt$", "", text)
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", text or ""))


def compact_text(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def make_excerpt(text: str, limit: int = 900) -> str:
    compact = compact_text(text)
    if len(compact) <= limit:
        return compact
    return compact[:limit].rsplit(" ", 1)[0] + " ..."


class ReferenceResolver:
    def __init__(self, topic_dir: Path):
        self.topic_dir = topic_dir
        self.references_path = topic_dir / "references.jsonl"
        self.reference_pages_dir = topic_dir / "reference_pages"
        self.url_meta: dict[str, dict[str, Any]] = {}
        self.norm_file_items: list[tuple[str, Path]] = []
        self._load()

    def _load(self) -> None:
        refs: list[dict[str, Any]] = []
        if self.references_path.exists():
            with self.references_path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        ref = json.loads(line)
                        refs.append(ref)
                        url = clean_url(ref.get("url"))
                        if url:
                            self.url_meta[url] = {
                                "title": (ref.get("title") or "").strip(),
                                "url": url,
                                "archive_url": ref.get("archive_url") or "",
                            }

        actual_files = {
            normalize_for_matching(path.stem): path
            for path in self.reference_pages_dir.glob("*.txt")
        }
        self.norm_file_items = list(actual_files.items())

        for meta in self.url_meta.values():
            title = meta["title"]
            norm_title = normalize_for_matching(title)
            match = actual_files.get(norm_title)

            if match is None:
                match = self._prefix_match(norm_title, self.norm_file_items)
            if match is not None:
                meta["file"] = match

    @staticmethod
    def _prefix_match(norm_title: str, norm_file_items: list[tuple[str, Path]]) -> Path | None:
        if not norm_title:
            return None
        for file_norm, file_path in norm_file_items:
            min_len = min(len(norm_title), len(file_norm))
            prefix_len = min(min_len, 70)
            if prefix_len < 24:
                continue
            if norm_title[:prefix_len] == file_norm[:prefix_len]:
                return file_path
            if norm_title.startswith(file_norm) and len(file_norm) >= 50:
                return file_path
            if file_norm.startswith(norm_title) and len(norm_title) >= 50:
                return file_path
        return None

    @staticmethod
    def _similarity_match(norm_title: str, norm_file_items: list[tuple[str, Path]]) -> Path | None:
        if not norm_title:
            return None
        best_score = 0.0
        best_path: Path | None = None
        for file_norm, file_path in norm_file_items:
            score = difflib.SequenceMatcher(None, norm_title, file_norm).ratio()
            if score > best_score:
                best_score = score
                best_path = file_path
        return best_path if best_score >= 0.82 else None

    def resolve(self, url: str) -> dict[str, Any] | None:
        meta = self.url_meta.get(clean_url(url))
        if not meta:
            return None
        if "file" not in meta:
            norm_title = normalize_for_matching(meta.get("title") or "")
            match = self._similarity_match(norm_title, self.norm_file_items)
            if match is None:
                return None
            meta["file"] = match
        path: Path = meta["file"]
        text = path.read_text(encoding="utf-8", errors="ignore")
        wc = word_count(text)
        rel_path = path.relative_to(ROOT)
        return {
            "doc_id": f"wgb:{self.topic_dir.parent.name}:{self.topic_dir.name}:{path.stem}",
            "title": meta.get("title") or path.stem,
            "url": meta["url"],
            "archive_url": meta.get("archive_url") or "",
            "local_path": str(rel_path),
            "word_count": wc,
            "excerpt": make_excerpt(text),
        }


def iter_summary_tasks() -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for qa_path in sorted((WGB_DIR / "QA").glob("*/*.jsonl")):
        domain = qa_path.parent.name
        with qa_path.open(encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if not line.strip():
                    continue
                row = json.loads(line)
                if "summary" not in (row.get("question_type") or []):
                    continue
                tasks.append(
                    {
                        "domain": domain,
                        "qa_index": idx,
                        "question": row.get("question") or "",
                        "gold_statements": row.get("gold_statements") or [],
                        "ref_urls": [clean_url(u) for u in (row.get("ref_urls") or [])],
                    }
                )
    return tasks


def topic_dir_for_domain(domain: str) -> Path:
    domain_dir = WGB_DIR / "corpus" / domain
    topic_dirs = [p for p in domain_dir.iterdir() if p.is_dir()]
    if len(topic_dirs) != 1:
        raise RuntimeError(f"Expected one topic dir for {domain}, got {len(topic_dirs)}")
    return topic_dirs[0]


def score_candidate(task: dict[str, Any], source_docs: list[dict[str, Any]]) -> tuple[float, list[str], list[str]]:
    reasons = ["summary_task", "multi_doc"]
    warnings: list[str] = []

    question = task["question"].lower()
    original_ref_count = len(task["ref_urls"])
    mapped_ref_count = len(source_docs)
    word_counts = [doc["word_count"] for doc in source_docs]
    total_words = sum(word_counts)

    score = 0.0
    score += min(mapped_ref_count, 8) * 0.8
    score += min(len(task["gold_statements"]), 6) * 0.35

    if MIN_REFS <= original_ref_count <= MAX_REFS:
        score += 2.0
        reasons.append("ref_count_in_preferred_range")
    else:
        warnings.append("ref_count_outside_preferred_range")

    if mapped_ref_count == original_ref_count:
        score += 1.5
        reasons.append("mapped_all_refs")
    elif mapped_ref_count >= MIN_MAPPED_REFS:
        score += 0.5
        warnings.append("partially_mapped_refs")

    if word_counts and all(MIN_DOC_WORDS <= wc <= MAX_DOC_WORDS for wc in word_counts):
        score += 1.0
        reasons.append("doc_sizes_ok")
    else:
        warnings.append("some_doc_sizes_outside_preferred_range")

    if MIN_TOTAL_WORDS <= total_words <= MAX_TOTAL_WORDS:
        score += 1.0
        reasons.append("total_source_size_ok")
    else:
        warnings.append("total_source_size_outside_preferred_range")

    matched_terms = sorted(term for term in SYNTHESIS_TERMS if term in question)
    if matched_terms:
        score += min(len(matched_terms), 4) * 0.8
        reasons.extend(f"has_synthesis_signal:{term}" for term in matched_terms[:4])

    weak_terms = sorted(term for term in WEAK_LIST_TERMS if question.startswith(term))
    if weak_terms and not matched_terms:
        score -= 1.0
        warnings.append("question_may_be_fact_list_or_overview")

    return round(score, 4), reasons, warnings


def classify_task(task: dict[str, Any], resolver: ReferenceResolver, seq: int) -> tuple[dict[str, Any], str]:
    resolved_docs: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for url in task["ref_urls"]:
        doc = resolver.resolve(url)
        if doc and doc["doc_id"] not in seen_doc_ids:
            resolved_docs.append(doc)
            seen_doc_ids.add(doc["doc_id"])

    if len(resolved_docs) < MIN_MAPPED_REFS:
        return {}, "mapped_ref_count_lt_min"

    score, reasons, warnings = score_candidate(task, resolved_docs)
    domain = task["domain"]
    topic = resolver.topic_dir.name
    candidate = {
        "candidate_id": f"wgb_summary_{domain}_{seq:04d}",
        "dataset": "WildGraphBench",
        "domain": domain,
        "topic": topic,
        "original_question": task["question"],
        "original_gold_statements": task["gold_statements"],
        "original_ref_urls": task["ref_urls"],
        "source_docs": resolved_docs,
        "filter_stats": {
            "original_ref_count": len(task["ref_urls"]),
            "mapped_ref_count": len(resolved_docs),
            "total_source_words": sum(doc["word_count"] for doc in resolved_docs),
            "mean_source_words": round(mean([doc["word_count"] for doc in resolved_docs]), 1),
        },
        "selection_score": score,
        "selection_reasons": reasons,
        "warnings": warnings,
    }
    return candidate, "candidate"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_summary(path: Path, candidates: list[dict[str, Any]], rejects: Counter[str]) -> None:
    by_domain = Counter(c["domain"] for c in candidates)
    scores = [c["selection_score"] for c in candidates]
    mapped_counts = [c["filter_stats"]["mapped_ref_count"] for c in candidates]
    word_totals = [c["filter_stats"]["total_source_words"] for c in candidates]

    lines: list[str] = []
    lines.append("# WildGraphBench 候选文档集合筛选摘要")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"- 入选候选数：`{len(candidates)}`")
    lines.append(f"- 拒绝原因：`{dict(rejects)}`")
    if candidates:
        lines.append(f"- selection_score median：`{median(scores):.2f}`")
        lines.append(f"- mapped_ref_count median：`{median(mapped_counts)}`")
        lines.append(f"- total_source_words median：`{median(word_totals)}`")
    lines.append("")
    lines.append("## 各 Domain 入选数")
    lines.append("")
    for domain, count in sorted(by_domain.items()):
        lines.append(f"- `{domain}`：{count}")

    lines.append("")
    lines.append("## Top Candidates")
    lines.append("")
    for c in candidates[:30]:
        stats = c["filter_stats"]
        lines.append(f"### {c['candidate_id']} | {c['domain']} / {c['topic']}")
        lines.append("")
        lines.append(f"- score：`{c['selection_score']}`")
        lines.append(f"- mapped refs：`{stats['mapped_ref_count']}/{stats['original_ref_count']}`")
        lines.append(f"- total words：`{stats['total_source_words']}`")
        lines.append(f"- original question：{c['original_question']}")
        lines.append(f"- reasons：{', '.join(c['selection_reasons'])}")
        if c["warnings"]:
            lines.append(f"- warnings：{', '.join(c['warnings'])}")
        lines.append("- source docs：")
        for doc in c["source_docs"][:8]:
            lines.append(f"  - {doc['title']} (`{doc['word_count']}` words)")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    if not WGB_DIR.exists():
        raise SystemExit(f"WildGraphBench directory not found: {WGB_DIR}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    resolvers = {
        domain_dir.name: ReferenceResolver(topic_dir_for_domain(domain_dir.name))
        for domain_dir in sorted((WGB_DIR / "corpus").iterdir())
        if domain_dir.is_dir()
    }

    candidates: list[dict[str, Any]] = []
    rejects: Counter[str] = Counter()
    seq_by_domain: defaultdict[str, int] = defaultdict(int)
    for task in iter_summary_tasks():
        resolver = resolvers.get(task["domain"])
        if not resolver:
            rejects["missing_resolver"] += 1
            continue
        seq_by_domain[task["domain"]] += 1
        candidate, status = classify_task(task, resolver, seq_by_domain[task["domain"]])
        if status == "candidate":
            candidates.append(candidate)
        else:
            rejects[status] += 1

    candidates.sort(
        key=lambda c: (
            -c["selection_score"],
            c["domain"],
            c["candidate_id"],
        )
    )

    jsonl_path = OUT_DIR / "wildgraphbench_candidate_doc_sets.jsonl"
    summary_path = OUT_DIR / "wildgraphbench_candidate_doc_sets_summary.md"
    write_jsonl(jsonl_path, candidates)
    write_summary(summary_path, candidates, rejects)

    print(f"candidates: {len(candidates)}")
    print(f"rejects: {dict(rejects)}")
    print(f"wrote: {jsonl_path}")
    print(f"wrote: {summary_path}")


if __name__ == "__main__":
    main()
