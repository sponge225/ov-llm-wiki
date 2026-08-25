"""Wiki 产物 URI 的拼接工具。"""

from __future__ import annotations

import re

from .config import WikiConfig

_INVALID_NODE_CHARS = re.compile(r"[^a-z0-9]+")


def sanitize_node_id(value: str) -> str:
    normalized = _INVALID_NODE_CHARS.sub("_", value.lower()).strip("_")
    normalized = re.sub(r"_+", "_", normalized)
    if not normalized:
        raise ValueError("node_id cannot be empty after normalization")
    return normalized


def wiki_root(config: WikiConfig) -> str:
    return _slash(config.wiki_root_uri)


def cards_dir(config: WikiConfig) -> str:
    return f"{wiki_root(config)}cards/"


def card_md_uri(config: WikiConfig, doc_id: str) -> str:
    return f"{cards_dir(config)}{doc_id}.card.md"


def card_json_uri(config: WikiConfig, doc_id: str) -> str:
    return f"{cards_dir(config)}{doc_id}.card.json"


def nodes_dir(config: WikiConfig) -> str:
    return f"{wiki_root(config)}nodes/"


def node_root_uri(config: WikiConfig, node_id: str) -> str:
    return f"{nodes_dir(config)}{sanitize_node_id(node_id)}/"


def node_card_md_uri(config: WikiConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}card.md"


def node_card_json_uri(config: WikiConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}card.json"


def card_md_uri_for_card(config: WikiConfig, card) -> str:
    if str(card.resource_uri).startswith("viking://wiki/"):
        return node_card_md_uri(config, card.doc_id)
    return card_md_uri(config, card.doc_id)


def node_documents_dir(config: WikiConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}documents/"


def node_document_uri(config: WikiConfig, node_id: str, document_id: str) -> str:
    return f"{node_documents_dir(config, node_id)}{document_id}.md"


def node_sources_dir(config: WikiConfig, node_id: str) -> str:
    return f"{node_root_uri(config, node_id)}sources/"


def run_dir(config: WikiConfig) -> str:
    return f"{wiki_root(config)}run/"


def _slash(uri: str) -> str:
    return uri if uri.endswith("/") else f"{uri}/"
