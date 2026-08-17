"""Wiki 产物 URI 的拼接工具。"""

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


def cards_dir(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}cards/"


def card_md_uri(config: WikiMVPConfig, doc_id: str) -> str:
    return f"{cards_dir(config)}{doc_id}.card.md"


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


def node_sources_dir(config: WikiMVPConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}sources/"


def run_dir(config: WikiMVPConfig) -> str:
    return f"{wiki_root(config)}run/"


def _slash(uri: str) -> str:
    return uri if uri.endswith("/") else f"{uri}/"
