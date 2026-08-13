"""URI helpers for Wiki MVP assets."""

from __future__ import annotations

import re

from .config import WikiMVPConfig

_INVALID_NODE_CHARS = re.compile(r"[^a-z0-9]+")


def sanitize_node_id(value: str) -> str:
    normalized = _INVALID_NODE_CHARS.sub("_", value.lower()).strip("_")
    normalized = re.sub(r"_+", "_", normalized)
    if not normalized:
        raise ValueError("node_id cannot be empty after normalization")
    return normalized


def wiki_root(config: WikiMVPConfig) -> str:
    return _slash(config.wiki_root_uri)


def profile_uri(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}profile.json"


def nodes_json_uri(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}nodes.json"


def source_assignments_uri(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}source_assignments.json"


def manifest_uri(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}manifest.json"


def cards_dir(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}cards/"


def card_md_uri(config: WikiMVPConfig, doc_id: str) -> str:
    return f"{cards_dir(config)}{doc_id}.card.md"


def card_json_uri(config: WikiMVPConfig, doc_id: str) -> str:
    return f"{cards_dir(config)}{doc_id}.card.json"


def nodes_dir(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}nodes/"


def node_root_uri(config: WikiMVPConfig, node_id: str) -> str:
    return f"{nodes_dir(config)}{sanitize_node_id(node_id)}/"


def node_md_uri(config: WikiMVPConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}node.md"


def node_documents_dir(config: WikiMVPConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}documents/"


def node_document_uri(config: WikiMVPConfig, node_id: str, document_id: str) -> str:
    return f"{node_documents_dir(config, node_id)}{document_id}.md"


def node_evidence_uri(config: WikiMVPConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}evidence.jsonl"


def node_sources_dir(config: WikiMVPConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}sources/"


def node_source_ref_uri(config: WikiMVPConfig, node_id: str, doc_id: str) -> str:
    return f"{node_sources_dir(config, node_id)}{doc_id}.ref.json"


def node_manifest_uri(config: WikiMVPConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}manifest.json"


def run_dir(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}run/"


def run_config_uri(config: WikiMVPConfig) -> str:
    return f"{run_dir(config)}config.json"


def prompts_log_uri(config: WikiMVPConfig) -> str:
    return f"{run_dir(config)}prompts.jsonl"


def raw_outputs_log_uri(config: WikiMVPConfig) -> str:
    return f"{run_dir(config)}raw_outputs.jsonl"


def logs_md_uri(config: WikiMVPConfig) -> str:
    return f"{run_dir(config)}logs.md"


def _slash(uri: str) -> str:
    return uri if uri.endswith("/") else f"{uri}/"
