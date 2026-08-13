#!/usr/bin/env python3
"""Select MS2 dev samples for the LLM Wiki MVP dataset.

The rules implemented here follow:
  .trae/documents/ms2-dev-mvp-sample-selection-rules.md

This script intentionally uses deterministic, inspectable heuristics. It does
not call LLMs and does not depend on external packages.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[2]
MS2_DIR = ROOT / ".trae/dataset_scout/mslr_data/ms2"
OUT_DIR = ROOT / "data/wiki_eval"

MIN_DOCS = 8
MAX_DOCS = 25
MIN_TARGET_WORDS = 40
MIN_BACKGROUND_WORDS = 20
MIN_RELATED_DOC_RATIO = 0.70
FINAL_MVP_SIZE = 20
FINAL_DIRECTION_MIN_QUOTA = {
    "no_effect": 2,
    "insufficient_evidence": 3,
    "scope_or_population_limit": 2,
}


STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "although",
    "among",
    "analysis",
    "because",
    "before",
    "between",
    "clinical",
    "could",
    "data",
    "during",
    "effect",
    "effects",
    "either",
    "from",
    "have",
    "having",
    "into",
    "more",
    "most",
    "other",
    "outcome",
    "outcomes",
    "patient",
    "patients",
    "placebo",
    "randomized",
    "review",
    "risk",
    "study",
    "studies",
    "than",
    "that",
    "their",
    "there",
    "these",
    "this",
    "those",
    "through",
    "trial",
    "trials",
    "using",
    "were",
    "with",
    "without",
}

BACKGROUND_CATEGORY_TERMS = {
    "condition": {
        "cancer",
        "disease",
        "infection",
        "syndrome",
        "nec",
        "diabetes",
        "depression",
        "pain",
        "stroke",
        "asthma",
        "arthritis",
        "obesity",
        "mortality",
        "sepsis",
        "hypertension",
        "cardiovascular",
        "renal",
        "pulmonary",
        "neonatal",
    },
    "population": {
        "patients",
        "neonates",
        "infants",
        "adults",
        "children",
        "women",
        "men",
        "elderly",
        "preterm",
        "pregnant",
        "adolescents",
        "newborn",
    },
    "intervention": {
        "treatment",
        "intervention",
        "supplementation",
        "drug",
        "therapy",
        "surgery",
        "probiotics",
        "prebiotics",
        "antibiotics",
        "exercise",
        "screening",
        "prevention",
        "vaccine",
        "chemotherapy",
    },
    "outcome": {
        "mortality",
        "incidence",
        "risk",
        "outcome",
        "tolerance",
        "recurrence",
        "survival",
        "pain",
        "quality",
        "complications",
        "adverse",
        "response",
        "remission",
    },
}

DIRECTION_PATTERNS = {
    "positive_effect": [
        r"\beffective\b",
        r"\bpromising\b",
        r"\breduced?\b",
        r"\breduction\b",
        r"\bimproved?\b",
        r"\bbeneficial\b",
        r"associated with lower",
        r"\bdecreased?\b",
        r"\bincreased?\b",
        r"\bbetter\b",
        r"\bsignificant(?:ly)?\b",
    ],
    "no_effect": [
        r"\bno evidence\b",
        r"\bnot significant\b",
        r"\bdid not reduce\b",
        r"\bno difference\b",
        r"\bno significant\b",
        r"\bno clear\b",
    ],
    "insufficient_evidence": [
        r"\binsufficient evidence\b",
        r"\blimited evidence\b",
        r"\buncertain\b",
        r"\binconclusive\b",
        r"\bmore research\b",
        r"\bfurther research\b",
        r"\blow quality evidence\b",
        r"\bvery low quality\b",
    ],
    "risk_or_harm": [
        r"\badverse\b",
        r"\bharm\b",
        r"\bside effects?\b",
        r"\bcomplications?\b",
        r"\bshould be interpreted with caution\b",
    ],
    "mixed_evidence": [
        r"\bmixed\b",
        r"\bconflicting\b",
        r"\bheterogeneous\b",
        r"\bvaried\b",
    ],
    "scope_or_population_limit": [
        r"\bin .* patients\b",
        r"\bfor .* patients\b",
        r"\bmay be useful\b",
        r"\bmay be considered\b",
        r"\bsubgroup\b",
    ],
}

SYNTHESIS_PATTERNS = [
    r"\bevidence suggests\b",
    r"\bevidence (?:from )?systematic review\b",
    r"\bsystematic review .* revealed\b",
    r"\bmeta-?analysis (?:showed|revealed|suggested|found)\b",
    r"\bwas associated with\b",
    r"\bwere associated with\b",
    r"\breduced?\b",
    r"\breduction\b",
    r"\bimproved?\b",
    r"\bno significant\b",
    r"\binsufficient evidence\b",
    r"\blimited evidence\b",
    r"\bshould be interpreted\b",
    r"\bcurrent evidence\b",
    r"\bavailable evidence\b",
    r"\bconcluded\b",
    r"\bfindings suggest\b",
]

BACKGROUND_ONLY_PATTERNS = [
    r"\bis a common\b",
    r"\bis one of the\b",
    r"\baffects\b",
    r"\bis associated with\b",
    r"\bhas been used\b",
    r"\bthere is a need\b",
    r"\bthe objective\b",
    r"\bthis review\b",
    r"\bwe aimed\b",
]


def normalize_text(text: str) -> str:
    # MS2 has tokenization artifacts like "r and omized"; keep this minimal.
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z0-9\-']+", normalize_text(text).lower())


def word_count(text: str) -> int:
    return len(tokens(text))


def pattern_hits(text: str, patterns: list[str]) -> int:
    t = normalize_text(text).lower()
    return sum(1 for p in patterns if re.search(p, t))


def direction_label(target: str) -> tuple[str, dict[str, int]]:
    hits = {
        label: pattern_hits(target, patterns)
        for label, patterns in DIRECTION_PATTERNS.items()
    }
    best_label, best_count = max(hits.items(), key=lambda item: item[1])
    if best_count == 0:
        return "unclear", hits
    return best_label, hits


def background_specificity(background: str) -> tuple[str, list[str]]:
    toks = set(tokens(background))
    matched = []
    for category, terms in BACKGROUND_CATEGORY_TERMS.items():
        if toks & terms:
            matched.append(category)
    if len(matched) >= 2:
        return "specific", matched
    if len(matched) == 1:
        return "borderline", matched
    return "generic", matched


def target_content_type(target: str) -> tuple[str, dict[str, int]]:
    synthesis_hits = pattern_hits(target, SYNTHESIS_PATTERNS)
    background_hits = pattern_hits(target, BACKGROUND_ONLY_PATTERNS)
    stats = {
        "synthesis_signal_count": synthesis_hits,
        "background_signal_count": background_hits,
    }
    if synthesis_hits > 0:
        return "synthesis_conclusion", stats
    if background_hits > 1:
        return "background_only", stats
    return "unclear", stats


def stem_token(tok: str) -> str:
    tok = tok.lower().strip("-'")
    for suffix in ("ization", "ational", "fulness", "ousness", "iveness", "tional", "ments", "ment", "ing", "ies", "ied", "ed", "es", "s"):
        if len(tok) > len(suffix) + 3 and tok.endswith(suffix):
            if suffix in {"ies", "ied"}:
                return tok[: -len(suffix)] + "y"
            return tok[: -len(suffix)]
    return tok


def query_keywords(background: str, target: str, max_terms: int = 28) -> list[str]:
    counts = Counter()
    for tok in tokens(f"{background} {target}"):
        stem = stem_token(tok)
        if len(stem) < 4 or stem in STOPWORDS:
            continue
        if stem.isdigit():
            continue
        counts[stem] += 1
    return [term for term, _ in counts.most_common(max_terms)]


def related_doc_ratio(background: str, target: str, docs: list[dict]) -> tuple[float, list[str], dict[str, int]]:
    keywords = query_keywords(background, target)
    if not keywords:
        return 0.0, [], {}

    keyset = set(keywords)
    per_doc_overlap = {}
    related = 0
    for doc in docs:
        doc_terms = {stem_token(tok) for tok in tokens(f"{doc['Title']} {doc['Abstract']}")}
        overlap = len(keyset & doc_terms)
        per_doc_overlap[doc["PMID"]] = overlap
        # A doc is related if it shares multiple topic terms. For short or
        # method-heavy abstracts, one rare key term plus title overlap can still
        # be meaningful, so the threshold is intentionally modest.
        if overlap >= 2:
            related += 1
    return related / len(docs), keywords, per_doc_overlap


def load_data() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    inputs_by_review: dict[str, list[dict]] = defaultdict(list)
    with (MS2_DIR / "dev-inputs.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            inputs_by_review[row["ReviewID"]].append(row)

    targets: dict[str, dict] = {}
    with (MS2_DIR / "dev-targets.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            targets[row["ReviewID"]] = row

    return inputs_by_review, targets


def source_docs_for_output(docs: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for doc in docs:
        pmid = normalize_text(doc["PMID"])
        if pmid in seen:
            continue
        seen.add(pmid)
        out.append(
            {
                "doc_id": f"PMID:{pmid}",
                "pmid": pmid,
                "title": normalize_text(doc["Title"]),
                "abstract": normalize_text(doc["Abstract"]),
            }
        )
    return out


def score_candidate(record: dict) -> float:
    stats = record["filter_stats"]
    q = record["quality_filters"]
    # Prefer high topical consistency, sufficient but not enormous doc sets,
    # concrete backgrounds, and targets with a synthesis conclusion.
    doc_count = stats["num_source_docs"]
    doc_score = 1.0 - min(abs(doc_count - 16) / 16.0, 1.0)
    target_score = min(stats["target_word_count"] / 80.0, 1.5)
    background_score = min(stats["background_word_count"] / 50.0, 1.2)
    related_score = q["related_doc_ratio"]
    direction_score = 1.0 if q["conclusion_direction"] != "unclear" else 0.0
    synthesis_score = 1.0 if q["target_content_type"] == "synthesis_conclusion" else 0.0
    specificity_score = {
        "specific": 1.0,
        "borderline": 0.4,
        "generic": 0.0,
    }.get(q["background_specificity"], 0.0)
    return round(
        2.0 * related_score
        + 1.3 * synthesis_score
        + 1.0 * direction_score
        + 0.8 * specificity_score
        + 0.5 * doc_score
        + 0.25 * min(target_score, 1.0)
        + 0.15 * min(background_score, 1.0),
        4,
    )


def classify_review(review_id: str, docs: list[dict], target_row: dict) -> dict:
    target = normalize_text(target_row.get("Target", ""))
    background = normalize_text(target_row.get("Background", ""))
    source_docs = source_docs_for_output(docs)
    num_docs = len(source_docs)
    target_wc = word_count(target)
    background_wc = word_count(background)
    abstract_counts = [word_count(d["abstract"]) for d in source_docs]
    mean_abstract_wc = mean(abstract_counts) if abstract_counts else 0

    hard_filters = {
        "doc_count_in_range": MIN_DOCS <= num_docs <= MAX_DOCS,
        "target_length_ok": target_wc >= MIN_TARGET_WORDS,
        "background_present": background_wc >= MIN_BACKGROUND_WORDS,
        "abstracts_non_empty": all(d["abstract"] for d in source_docs),
        "doc_ids_unique": len({d["doc_id"] for d in source_docs}) == len(source_docs),
    }

    bg_specificity, bg_categories = background_specificity(background)
    direction, direction_hits = direction_label(target)
    content_type, content_stats = target_content_type(target)
    rel_ratio, keywords, per_doc_overlap = related_doc_ratio(background, target, docs)

    quality_filters = {
        "background_specificity": bg_specificity,
        "background_categories": bg_categories,
        "conclusion_direction": direction,
        "direction_hits": direction_hits,
        "target_content_type": content_type,
        "target_content_stats": content_stats,
        "related_doc_ratio": round(rel_ratio, 4),
        "topic_keywords": keywords,
        "topic_consistency_filter": rel_ratio >= MIN_RELATED_DOC_RATIO,
    }

    hard_pass = all(hard_filters.values())
    quality_pass = (
        bg_specificity == "specific"
        and direction != "unclear"
        and content_type == "synthesis_conclusion"
        and rel_ratio >= MIN_RELATED_DOC_RATIO
    )

    selection_reasons = []
    rejection_reasons = []
    for name, passed in hard_filters.items():
        (selection_reasons if passed else rejection_reasons).append(name)

    if bg_specificity == "specific":
        selection_reasons.append("background_specific")
    else:
        rejection_reasons.append(f"background_{bg_specificity}")

    if direction != "unclear":
        selection_reasons.append(f"direction_{direction}")
    else:
        rejection_reasons.append("direction_unclear")

    if content_type == "synthesis_conclusion":
        selection_reasons.append("target_synthesis_conclusion")
    else:
        rejection_reasons.append(f"target_{content_type}")

    if rel_ratio >= MIN_RELATED_DOC_RATIO:
        selection_reasons.append("topic_consistency_ok")
    else:
        rejection_reasons.append("topic_consistency_low")

    if hard_pass and quality_pass:
        status = "candidate"
    elif hard_pass:
        status = "manual_review"
    else:
        status = "reject"

    record = {
        "sample_id": f"ms2_dev_{review_id}",
        "dataset": "ms2",
        "split": "dev",
        "review_id": review_id,
        "background": background,
        "target": target,
        "source_docs": source_docs,
        "filter_stats": {
            "num_source_docs": num_docs,
            "target_word_count": target_wc,
            "background_word_count": background_wc,
            "mean_abstract_word_count": round(mean_abstract_wc, 1),
            "min_abstract_word_count": min(abstract_counts) if abstract_counts else 0,
            "max_abstract_word_count": max(abstract_counts) if abstract_counts else 0,
        },
        "hard_filters": hard_filters,
        "quality_filters": quality_filters,
        "selection_status": status,
        "selection_reasons": selection_reasons,
        "rejection_reasons": rejection_reasons,
    }
    record["selection_score"] = score_candidate(record) if status == "candidate" else 0.0
    return record


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def slim_record(record: dict) -> dict:
    slim = dict(record)
    slim["source_doc_ids"] = [doc["doc_id"] for doc in record["source_docs"]]
    slim.pop("source_docs", None)
    return slim


def select_final_mvp(candidates: list[dict]) -> list[dict]:
    selected = []
    selected_ids = set()

    for direction, quota in FINAL_DIRECTION_MIN_QUOTA.items():
        pool = [
            row for row in candidates
            if row["quality_filters"]["conclusion_direction"] == direction
        ]
        for row in pool[:quota]:
            if row["sample_id"] in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row["sample_id"])

    for row in candidates:
        if len(selected) >= FINAL_MVP_SIZE:
            break
        if row["sample_id"] in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(row["sample_id"])

    selected.sort(
        key=lambda r: (
            -r["selection_score"],
            -r["quality_filters"]["related_doc_ratio"],
            abs(r["filter_stats"]["num_source_docs"] - 16),
            r["review_id"],
        )
    )
    return selected


def write_summary(path: Path, records: list[dict], final_rows: list[dict]) -> None:
    status_counts = Counter(r["selection_status"] for r in records)
    direction_counts = Counter(
        r["quality_filters"]["conclusion_direction"]
        for r in records
        if r["selection_status"] == "candidate"
    )
    doc_counts = [r["filter_stats"]["num_source_docs"] for r in records if r["selection_status"] == "candidate"]
    target_counts = [r["filter_stats"]["target_word_count"] for r in records if r["selection_status"] == "candidate"]

    def fmt_mean(values: list[float]) -> str:
        return f"{mean(values):.2f}" if values else "n/a"

    lines = [
        "# MS2 Dev MVP Sample Selection Summary",
        "",
        "## Inputs",
        "",
        f"- Source directory: `{MS2_DIR.relative_to(ROOT)}`",
        f"- Total reviewed samples: {len(records)}",
        "",
        "## Selection Counts",
        "",
        f"- candidate: {status_counts.get('candidate', 0)}",
        f"- manual_review: {status_counts.get('manual_review', 0)}",
        f"- reject: {status_counts.get('reject', 0)}",
        "",
        "## Candidate Distribution",
        "",
        f"- Mean source docs: {fmt_mean(doc_counts)}",
        f"- Mean target words: {fmt_mean(target_counts)}",
        f"- Direction labels: {dict(direction_counts)}",
        "",
        "## Final MVP 20",
        "",
    ]

    for idx, row in enumerate(final_rows, 1):
        stats = row["filter_stats"]
        q = row["quality_filters"]
        lines.append(
            f"{idx}. `{row['sample_id']}`: docs={stats['num_source_docs']}, "
            f"target_words={stats['target_word_count']}, "
            f"related_ratio={q['related_doc_ratio']}, "
            f"direction={q['conclusion_direction']}, "
            f"score={row['selection_score']}"
        )

    lines.extend(
        [
            "",
            "## Output Files",
            "",
            "- `data/wiki_eval/ms2_dev_mvp_all_status.jsonl`",
            "- `data/wiki_eval/ms2_dev_mvp_candidates.jsonl`",
            "- `data/wiki_eval/ms2_dev_mvp_manual_review.jsonl`",
            "- `data/wiki_eval/ms2_dev_mvp_rejects.jsonl`",
            "- `data/wiki_eval/ms2_dev_mvp_final_20.jsonl`",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    inputs_by_review, targets = load_data()

    records = []
    for review_id in sorted(inputs_by_review.keys(), key=lambda x: int(x) if x.isdigit() else x):
        if review_id not in targets:
            continue
        records.append(classify_review(review_id, inputs_by_review[review_id], targets[review_id]))

    candidates = [r for r in records if r["selection_status"] == "candidate"]
    manual = [r for r in records if r["selection_status"] == "manual_review"]
    rejects = [r for r in records if r["selection_status"] == "reject"]

    candidates.sort(
        key=lambda r: (
            -r["selection_score"],
            -r["quality_filters"]["related_doc_ratio"],
            abs(r["filter_stats"]["num_source_docs"] - 16),
            r["review_id"],
        )
    )
    manual.sort(key=lambda r: (len(r["rejection_reasons"]), -r["filter_stats"]["num_source_docs"], r["review_id"]))
    rejects.sort(key=lambda r: (len(r["rejection_reasons"]), r["review_id"]))

    final_20 = candidates[:FINAL_MVP_SIZE]
    final_20 = select_final_mvp(candidates)

    write_jsonl(OUT_DIR / "ms2_dev_mvp_all_status.jsonl", [slim_record(r) for r in records])
    write_jsonl(OUT_DIR / "ms2_dev_mvp_candidates.jsonl", candidates)
    write_jsonl(OUT_DIR / "ms2_dev_mvp_manual_review.jsonl", manual)
    write_jsonl(OUT_DIR / "ms2_dev_mvp_rejects.jsonl", [slim_record(r) for r in rejects])
    write_jsonl(OUT_DIR / "ms2_dev_mvp_final_20.jsonl", final_20)
    write_summary(OUT_DIR / "ms2_dev_mvp_selection_summary.md", records, final_20)

    print(json.dumps(
        {
            "total": len(records),
            "candidate": len(candidates),
            "manual_review": len(manual),
            "reject": len(rejects),
            "final_20": len(final_20),
            "output_dir": str(OUT_DIR.relative_to(ROOT)),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
