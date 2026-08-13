#!/usr/bin/env python3
"""Export cleaned WildGraphBench reference documents as a stable dataset asset."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Any

from probe_wildgraphbench_topic_discovery import (
    HTML_TAG_RE,
    MARKDOWN_LINK_RE,
    MAX_WORDS_PER_DOC,
    ROOT,
    URL_RE,
    is_boilerplate_line,
    word_count,
)


CANDIDATES = ROOT / "data/wiki_eval/wildgraphbench_candidate_doc_sets.jsonl"
OUT_JSONL = ROOT / "data/wiki_eval/wildgraphbench_cleaned_reference_docs.jsonl"
OUT_SUMMARY = ROOT / "data/wiki_eval/wildgraphbench_cleaned_reference_docs_summary.md"

ADDITIONAL_BOILERPLATE_TERMS = {
    "about us",
    "all stories",
    "comment policy",
    "contact us",
    "download our app",
    "editorial guidelines",
    "more stories",
    "most popular",
    "popular stories",
    "recommended for you",
    "site map",
    "start the conversation",
    "the wayback machine",
    "trending now",
}

TAIL_MARKERS = {
    "start the conversation",
    "more from",
    "more stories",
    "most popular",
    "recommended for you",
    "related articles",
    "related stories",
    "trending now",
}


def _clean_markdown_links(line: str) -> str:
    line = MARKDOWN_LINK_RE.sub(lambda m: m.group(0).split("](", 1)[0].lstrip("!["), line)
    return URL_RE.sub(" ", line)


def _is_extra_boilerplate_line(line: str) -> bool:
    lower = line.lower()
    if any(term in lower for term in ADDITIONAL_BOILERPLATE_TERMS):
        return True
    if re.fullmatch(r"[*\-]\s*[A-Z][A-Za-z0-9 &'’:+,-]{2,45}", line):
        return True
    return False


def _is_probable_byline_or_date(line: str) -> bool:
    lower = line.lower()
    if re.match(r"^by\s+[A-Z][A-Za-z .,'’-]+", line):
        return True
    if "updated" in lower or "published" in lower:
        return word_count(line) <= 20
    if re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", lower):
        return word_count(line) <= 25
    return False


def _is_probable_body_line(line: str) -> bool:
    if line.startswith(("* ", "- ", "### ")):
        return False
    return word_count(line) >= 25


def _norm_title(text: str) -> str:
    text = text.lower().strip("#* -")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _trim_page_chrome(lines: list[str], title: str = "") -> list[str]:
    if not lines:
        return lines
    norm_title = _norm_title(title)

    start_idx = 0
    for i, line in enumerate(lines[:120]):
        if _is_probable_body_line(line) or _is_probable_byline_or_date(line):
            start_idx = i
            break

    if start_idx > 0:
        kept_prefix: list[str] = []
        for line in lines[:start_idx]:
            # Preserve the document title if it appears as a heading before the
            # navigation block. Drop short menu/list items.
            if line.startswith("# ") or (word_count(line) >= 4 and len(kept_prefix) < 2 and not line.startswith(("* ", "- "))):
                if line not in kept_prefix:
                    kept_prefix.append(line)
        lines = kept_prefix + lines[start_idx:]

    trimmed: list[str] = []
    for line in lines:
        lower = line.lower().strip("#* -")
        if trimmed and any(marker in lower for marker in TAIL_MARKERS):
            break
        if trimmed and line.startswith("### ") and norm_title and _norm_title(line) == norm_title:
            break
        trimmed.append(line)

    deduped: list[str] = []
    prev_norm = ""
    for line in trimmed:
        current_norm = _norm_title(line)
        if deduped and current_norm and current_norm == prev_norm:
            continue
        deduped.append(line)
        if current_norm:
            prev_norm = current_norm
    trimmed = deduped
    return trimmed


def clean_reference_text_structured(text: str, title: str = "") -> str:
    """Clean web page text while preserving basic document structure.

    The topic-probe cleaner intentionally flattens text for clustering. This
    exporter is used as a dataset artifact, so it keeps headings, paragraphs,
    and list-like line breaks whenever possible.
    """

    cleaned_lines: list[str] = []
    seen: set[str] = set()
    word_budget = MAX_WORDS_PER_DOC

    for raw_line in text.splitlines():
        original = raw_line.rstrip()
        stripped = original.strip()
        if not stripped:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        line = HTML_TAG_RE.sub(" ", stripped)
        line = _clean_markdown_links(line)
        line = re.sub(r"\b(x[0-9a-f]{2}|utm_[a-z_]+|wp-[a-z-]+)\b", " ", line, flags=re.I)
        line = re.sub(r"[`|{}\[\]<>]+", " ", line)
        line = re.sub(r"\s+", " ", line).strip()

        if not line:
            continue
        if re.fullmatch(r"[-=*_#\s]{3,}", line):
            continue
        if is_boilerplate_line(line) or _is_extra_boilerplate_line(line):
            continue

        normalized = line.lower()
        if normalized in seen:
            continue
        seen.add(normalized)

        line_words = word_count(line)
        if line_words == 0:
            continue
        if word_budget <= 0:
            break
        if line_words > word_budget:
            words = line.split()
            line = " ".join(words[:word_budget])
            line_words = word_count(line)

        cleaned_lines.append(line)
        word_budget -= line_words

    cleaned_lines = _trim_page_chrome(cleaned_lines, title=title)
    while cleaned_lines and cleaned_lines[0] == "":
        cleaned_lines.pop(0)
    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()
    return "\n".join(cleaned_lines)


def load_candidate_rows() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in CANDIDATES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    rows = load_candidate_rows()
    docs: dict[str, dict[str, Any]] = {}
    candidate_ids_by_path: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        candidate_id = row["candidate_id"]
        for source in row.get("source_docs", []):
            local_path = source.get("local_path")
            if not local_path:
                continue
            candidate_ids_by_path[local_path].add(candidate_id)
            if local_path in docs:
                continue
            path = ROOT / local_path
            if not path.exists():
                continue
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
            cleaned_text = clean_reference_text_structured(raw_text, title=source.get("title", path.stem))
            docs[local_path] = {
                "doc_id": source.get("doc_id", ""),
                "title": source.get("title", path.stem),
                "url": source.get("url", ""),
                "archive_url": source.get("archive_url", ""),
                "local_path": local_path,
                "domain": row.get("domain", ""),
                "topic": row.get("topic", ""),
                "raw_word_count": word_count(raw_text),
                "clean_word_count": word_count(cleaned_text),
                "cleaned_text": cleaned_text,
            }

    output_rows = []
    for local_path, doc in sorted(docs.items()):
        if doc["clean_word_count"] < 300:
            continue
        doc = dict(doc)
        doc["candidate_ids"] = sorted(candidate_ids_by_path[local_path])
        output_rows.append(doc)

    OUT_JSONL.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in output_rows) + "\n",
        encoding="utf-8",
    )

    raw_words = sum(row["raw_word_count"] for row in output_rows)
    clean_words = sum(row["clean_word_count"] for row in output_rows)
    domain_counts = Counter(row["domain"] for row in output_rows)
    topic_counts = Counter(row["topic"] for row in output_rows)
    candidate_ref_counts = Counter()
    for row in output_rows:
        for candidate_id in row["candidate_ids"]:
            candidate_ref_counts[candidate_id] += 1

    lines = [
        "# WildGraphBench 清洗后 Reference Docs",
        "",
        "这是前面筛出的 WildGraphBench source documents 的清洗后稳定副本，用于后续主题发现、Document Card 生成和 Wiki MVP 实验。",
        "",
        "## 文件",
        "",
        f"- cleaned_docs_jsonl: `{OUT_JSONL.relative_to(ROOT)}`",
        f"- source_candidates: `{CANDIDATES.relative_to(ROOT)}`",
        "",
        "## 清洗规则",
        "",
        "- 删除 URL。",
        "- 删除 Markdown/HTML 链接和标签残留，但尽量保留链接文本。",
        "- 删除 cookie / subscribe / share / nav / footer / privacy / login 等网页壳行。",
        "- 删除明显脚本字段、重复行、过短行、符号占比过高的行。",
        "- 保留标题、段落、列表等基本换行结构。",
        "- 不做摘要、不改写正文，只做页面噪声和格式残留清理。",
        "",
        "## 统计",
        "",
        f"- source_candidate_count: {len(rows)}",
        f"- cleaned_doc_count: {len(output_rows)}",
        f"- raw_word_count_total: {raw_words}",
        f"- clean_word_count_total: {clean_words}",
        f"- kept_word_ratio: {clean_words / raw_words:.3f}" if raw_words else "- kept_word_ratio: 0",
        "",
        "## Domain 分布",
        "",
    ]
    for domain, count in domain_counts.most_common():
        lines.append(f"- {domain}: {count}")
    lines.extend(["", "## Topic 分布 Top 20", ""])
    for topic, count in topic_counts.most_common(20):
        lines.append(f"- {topic}: {count}")
    lines.extend(["", "## Candidate 覆盖", ""])
    lines.append(f"- candidates_with_cleaned_docs: {len(candidate_ref_counts)}")
    lines.append(
        f"- cleaned_docs_per_candidate_median: {sorted(candidate_ref_counts.values())[len(candidate_ref_counts)//2] if candidate_ref_counts else 0}"
    )

    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"cleaned_doc_count={len(output_rows)}")
    print(f"raw_word_count_total={raw_words}")
    print(f"clean_word_count_total={clean_words}")
    print(f"kept_word_ratio={clean_words / raw_words:.3f}" if raw_words else "kept_word_ratio=0")
    print(f"wrote {OUT_JSONL}")
    print(f"wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
