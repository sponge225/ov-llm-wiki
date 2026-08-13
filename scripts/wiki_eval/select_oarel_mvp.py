#!/usr/bin/env python3
"""Select OARelatedWork full-paper MVP samples.

This script works on the downloaded OARelatedWork default validation shard:
  .trae/dataset_scout/OARelatedWork/default_validation_00000.parquet

The selected samples use referenced papers with full `hierarchy` text as the
resource corpus and the target paper's `related_work` section as gold synthesis.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean, median

import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
PARQUET = ROOT / ".trae/dataset_scout/OARelatedWork/default_validation_00000.parquet"
OUT_DIR = ROOT / "data/wiki_eval"

FINAL_MVP_SIZE = 20

MIN_REFS = 4
MAX_REFS = 12
MIN_RELATED_WORK_WORDS = 200
MAX_RELATED_WORK_WORDS = 900
MIN_TARGET_HIERARCHY_WORDS = 2000
MIN_REF_HIERARCHY_WORDS = 1500
MIN_TOTAL_SOURCE_WORDS = 12000
MAX_TOTAL_SOURCE_WORDS = 80000

PRIMARY_FIELDS = {
    "computer science",
    "computer science",
    "natural language processing",
    "machine learning",
    "information retrieval",
    "artificial intelligence",
    "deep learning",
    "speech recognition",
    "machine translation",
    "parsing",
    "language model",
    "theoretical computer science",
}


def parse_maybe(value):
    if value is None or isinstance(value, (list, dict)):
        return value
    text = value.strip()
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(text)
        except Exception:
            pass
    return value


def word_count(obj) -> int:
    if obj is None:
        return 0
    if isinstance(obj, str):
        return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", obj))
    if isinstance(obj, dict):
        return sum(word_count(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(word_count(v) for v in obj)
    return 0


def normalize_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", title or "").strip()


def normalize_fields(fields) -> list[str]:
    fields = parse_maybe(fields)
    if not fields:
        return []
    return [str(x) for x in fields]


def is_primary_field(fields: list[str]) -> bool:
    lowered = {f.lower() for f in fields}
    if lowered & PRIMARY_FIELDS:
        return True
    # Accept common CS-adjacent terms from S2ORC metadata.
    return any(
        term in field
        for field in lowered
        for term in (
            "computer",
            "language",
            "translation",
            "parsing",
            "retrieval",
            "learning",
            "neural",
            "algorithm",
        )
    )


def collect_headlines(hierarchy, limit: int = 40) -> list[str]:
    out: list[str] = []

    def rec(obj):
        if len(out) >= limit:
            return
        if isinstance(obj, dict):
            headline = obj.get("headline")
            if isinstance(headline, str):
                h = normalize_title(headline)
                if h:
                    out.append(h[:160])
            for value in obj.values():
                rec(value)
        elif isinstance(obj, list):
            for value in obj:
                rec(value)

    rec(hierarchy)
    return out


def as_text_from_related_work(related_work) -> str:
    related_work = parse_maybe(related_work)
    texts: list[str] = []

    def rec(obj):
        if isinstance(obj, dict):
            text = obj.get("text")
            if isinstance(text, str):
                texts.append(re.sub(r"\s+", " ", text).strip())
            for value in obj.values():
                rec(value)
        elif isinstance(obj, list):
            for value in obj:
                rec(value)
        elif isinstance(obj, str):
            # Only use raw string if parsing failed.
            if not texts:
                texts.append(re.sub(r"\s+", " ", obj).strip())

    rec(related_work)
    return "\n\n".join(t for t in texts if t)


def ref_record(ref: dict) -> dict:
    hierarchy = parse_maybe(ref.get("hierarchy"))
    return {
        "doc_id": f"OARW:{ref.get('id')}",
        "oarw_id": ref.get("id"),
        "s2orc_id": ref.get("s2orc_id"),
        "mag_id": ref.get("mag_id"),
        "doi": ref.get("doi"),
        "title": normalize_title(ref.get("title")),
        "authors": ref.get("authors") or [],
        "year": ref.get("year"),
        "fields_of_study": normalize_fields(ref.get("fields_of_study")),
        "source_type": "full_paper_hierarchy",
        "hierarchy": hierarchy,
        "hierarchy_word_count": word_count(hierarchy),
        "section_headlines_preview": collect_headlines(hierarchy, 20),
        "bibliography": ref.get("bibliography") or [],
        "non_plaintext_content": ref.get("non_plaintext_content") or [],
    }


def classify_row(row: dict) -> tuple[dict, dict | None]:
    target_hierarchy = parse_maybe(row.get("hierarchy"))
    related_work = parse_maybe(row.get("related_work"))
    fields = normalize_fields(row.get("fields_of_study"))
    refs = row.get("referenced") or []

    ref_docs = [ref_record(ref) for ref in refs]
    ref_words = [doc["hierarchy_word_count"] for doc in ref_docs]

    rw_text = as_text_from_related_work(related_work)
    rw_words = word_count(rw_text)
    target_words = word_count(target_hierarchy)
    total_source_words = sum(ref_words)

    hard_filters = {
        "ref_count_in_range": MIN_REFS <= len(ref_docs) <= MAX_REFS,
        "related_work_length_ok": MIN_RELATED_WORK_WORDS <= rw_words <= MAX_RELATED_WORK_WORDS,
        "target_full_hierarchy_ok": target_words >= MIN_TARGET_HIERARCHY_WORDS,
        "all_refs_have_full_hierarchy": bool(ref_words) and all(w >= MIN_REF_HIERARCHY_WORDS for w in ref_words),
        "total_source_size_ok": MIN_TOTAL_SOURCE_WORDS <= total_source_words <= MAX_TOTAL_SOURCE_WORDS,
        "primary_field_ok": is_primary_field(fields),
    }

    filter_stats = {
        "related_work_word_count": rw_words,
        "target_hierarchy_word_count": target_words,
        "num_referenced_papers": len(ref_docs),
        "total_source_hierarchy_words": total_source_words,
        "mean_ref_hierarchy_words": round(mean(ref_words), 1) if ref_words else 0,
        "min_ref_hierarchy_words": min(ref_words) if ref_words else 0,
        "max_ref_hierarchy_words": max(ref_words) if ref_words else 0,
    }

    status = "candidate" if all(hard_filters.values()) else "reject"
    rejection_reasons = [name for name, passed in hard_filters.items() if not passed]
    selection_reasons = [name for name, passed in hard_filters.items() if passed]

    score = 0.0
    if status == "candidate":
        # Prefer medium-size source sets with substantial but not huge related work.
        ref_score = 1.0 - min(abs(len(ref_docs) - 7) / 7.0, 1.0)
        rw_score = min(rw_words / 500.0, 1.3)
        source_score = 1.0 - min(abs(total_source_words - 35000) / 35000.0, 1.0)
        field_score = 1.0 if is_primary_field(fields) else 0.0
        score = round(2.0 * field_score + 1.2 * ref_score + 1.0 * min(rw_score, 1.0) + 0.8 * source_score, 4)

    slim = {
        "sample_id": f"oarel_default_val0_{row['id']}",
        "dataset": "OARelatedWork",
        "config": "default",
        "split": "validation",
        "shard": "validation-00000-of-00002",
        "target_paper": {
            "id": row.get("id"),
            "s2orc_id": row.get("s2orc_id"),
            "mag_id": row.get("mag_id"),
            "doi": row.get("doi"),
            "title": normalize_title(row.get("title")),
            "authors": row.get("authors") or [],
            "year": row.get("year"),
            "fields_of_study": fields,
            "source_type": "target_paper",
            "hierarchy_word_count": target_words,
            "section_headlines_preview": collect_headlines(target_hierarchy, 30),
        },
        "gold_related_work": rw_text,
        "filter_stats": filter_stats,
        "hard_filters": hard_filters,
        "selection_status": status,
        "selection_reasons": selection_reasons,
        "rejection_reasons": rejection_reasons,
        "selection_score": score,
    }

    full = None
    if status == "candidate":
        full = {
            **slim,
            "target_paper": {
                **slim["target_paper"],
                # Kept for analysis only. The target paper must not be imported
                # into viking://resources for evaluation.
                "hierarchy": target_hierarchy,
            },
            "source_docs": ref_docs,
        }
    return slim, full


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_summary(path: Path, all_rows: list[dict], candidates: list[dict], final_rows: list[dict]) -> None:
    status_counts = Counter(r["selection_status"] for r in all_rows)
    fields = Counter()
    for r in candidates:
        for f in r["target_paper"]["fields_of_study"]:
            fields[f] += 1

    def avg(rows: list[dict], key: str) -> float:
        return round(mean(r["filter_stats"][key] for r in rows), 2) if rows else 0

    lines = [
        "# OARelatedWork MVP Sample Selection Summary",
        "",
        "## Input",
        "",
        f"- Parquet: `{PARQUET.relative_to(ROOT)}`",
        "- Dataset config: `default`",
        "- Split shard: `validation-00000-of-00002`",
        "",
        "## Selection Counts",
        "",
        f"- total rows: {len(all_rows)}",
        f"- candidate: {status_counts.get('candidate', 0)}",
        f"- reject: {status_counts.get('reject', 0)}",
        "",
        "## Candidate Means",
        "",
        f"- referenced papers: {avg(candidates, 'num_referenced_papers')}",
        f"- related_work words: {avg(candidates, 'related_work_word_count')}",
        f"- total source hierarchy words: {avg(candidates, 'total_source_hierarchy_words')}",
        "",
        "## Top Candidate Fields",
        "",
    ]
    for field, count in fields.most_common(20):
        lines.append(f"- {field}: {count}")

    lines.extend(["", "## Final MVP 20", ""])
    for idx, row in enumerate(final_rows, 1):
        stats = row["filter_stats"]
        title = row["target_paper"]["title"][:100].replace("\n", " ")
        lines.append(
            f"{idx}. `{row['sample_id']}` refs={stats['num_referenced_papers']}, "
            f"rw_words={stats['related_work_word_count']}, "
            f"source_words={stats['total_source_hierarchy_words']}, "
            f"score={row['selection_score']} - {title}"
        )

    lines.extend(
        [
            "",
            "## Output Files",
            "",
            "- `data/wiki_eval/oarel_mvp_all_status.jsonl`",
            "- `data/wiki_eval/oarel_mvp_candidates.jsonl`",
            "- `data/wiki_eval/oarel_mvp_final_20.jsonl`",
            "- `data/wiki_eval/oarel_mvp_selection_summary.md`",
            "",
            "Note: `target_paper.hierarchy` is retained only for offline inspection. It must not be imported into `viking://resources` during evaluation; resources should be built from `source_docs` only.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    table = pq.read_table(PARQUET)
    rows = table.to_pylist()

    all_slim: list[dict] = []
    full_candidates: list[dict] = []
    for row in rows:
        slim, full = classify_row(row)
        all_slim.append(slim)
        if full is not None:
            full_candidates.append(full)

    full_candidates.sort(
        key=lambda r: (
            -r["selection_score"],
            abs(r["filter_stats"]["num_referenced_papers"] - 7),
            abs(r["filter_stats"]["total_source_hierarchy_words"] - 35000),
            r["sample_id"],
        )
    )
    final_20 = full_candidates[:FINAL_MVP_SIZE]

    candidate_slim = [
        {
            **{k: v for k, v in row.items() if k not in {"source_docs"}},
            "source_doc_ids": [doc["doc_id"] for doc in row["source_docs"]],
        }
        for row in full_candidates
    ]

    write_jsonl(OUT_DIR / "oarel_mvp_all_status.jsonl", all_slim)
    write_jsonl(OUT_DIR / "oarel_mvp_candidates.jsonl", candidate_slim)
    write_jsonl(OUT_DIR / "oarel_mvp_final_20.jsonl", final_20)
    write_summary(OUT_DIR / "oarel_mvp_selection_summary.md", all_slim, candidate_slim, final_20)

    print(json.dumps(
        {
            "total": len(all_slim),
            "candidate": len(full_candidates),
            "reject": len(all_slim) - len(full_candidates),
            "final_20": len(final_20),
            "output_dir": str(OUT_DIR.relative_to(ROOT)),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
