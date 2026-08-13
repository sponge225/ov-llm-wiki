"""OARelatedWork MVP input adapter."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import WikiMVPConfig
from .schemas import ResourceDocument


def load_oarel_mvp_documents(
    path: str,
    max_samples: int | None = None,
    config: WikiMVPConfig | None = None,
) -> list[ResourceDocument]:
    config = config or WikiMVPConfig()
    docs: dict[str, ResourceDocument] = {}
    with Path(path).open("r", encoding="utf-8") as file:
        for index, line in enumerate(file):
            if max_samples is not None and index >= max_samples:
                break
            if not line.strip():
                continue
            row = json.loads(line)
            for source_doc in row.get("source_docs", []):
                doc = _source_doc_to_resource(source_doc, config)
                docs.setdefault(doc.doc_id, doc)
    return list(docs.values())


def _source_doc_to_resource(source_doc: dict[str, Any], config: WikiMVPConfig) -> ResourceDocument:
    raw_doc_id = str(source_doc.get("doc_id") or source_doc.get("oarw_id") or source_doc.get("title"))
    doc_id = _normalize_doc_id(raw_doc_id)
    title = str(source_doc.get("title") or doc_id)
    hierarchy = source_doc.get("hierarchy") or {}
    abstract = _extract_abstract(hierarchy)
    content_or_structure = _flatten_hierarchy(hierarchy)
    return ResourceDocument(
        doc_id=doc_id,
        resource_uri=f"{config.resource_root_uri}{doc_id}/",
        title=title,
        source_type=str(source_doc.get("source_type") or "academic_paper_full_text"),
        abstract=abstract,
        content_or_structure=content_or_structure,
        metadata={
            "year": source_doc.get("year"),
            "fields_of_study": source_doc.get("fields_of_study") or [],
            "hierarchy_word_count": source_doc.get("hierarchy_word_count"),
        },
    )


def _normalize_doc_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    if normalized.startswith("OARW_"):
        return normalized
    if normalized.startswith("OARW"):
        return normalized
    return f"OARW_{normalized}"


def _extract_abstract(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    if str(node.get("headline", "")).lower() == "abstract":
        return _collect_text(node)
    for child in _iter_children(node):
        abstract = _extract_abstract(child)
        if abstract:
            return abstract
    return ""


def _flatten_hierarchy(node: Any, depth: int = 0) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    parts: list[str] = []
    headline = node.get("headline")
    if headline:
        level = min(depth + 1, 6)
        parts.append(f"{'#' * level} {headline}")

    content = node.get("content")
    if isinstance(content, dict) and "text" in content:
        parts.append(str(content["text"]))
    elif isinstance(content, list):
        for child in content:
            child_text = _flatten_hierarchy(child, depth + 1)
            if child_text:
                parts.append(child_text)
    elif isinstance(content, str):
        parts.append(content)

    return "\n\n".join(part for part in parts if part)


def _collect_text(node: Any) -> str:
    if isinstance(node, dict):
        content = node.get("content")
        if isinstance(content, dict) and "text" in content:
            return str(content["text"])
        if isinstance(content, list):
            return " ".join(_collect_text(child) for child in content).strip()
        if isinstance(content, str):
            return content
    return ""


def _iter_children(node: dict[str, Any]) -> list[Any]:
    content = node.get("content")
    return content if isinstance(content, list) else []
