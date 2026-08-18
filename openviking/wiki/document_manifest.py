"""Persist and load Wiki document boundaries for standalone Wiki builds."""

from __future__ import annotations

import json
from typing import Any

from openviking_cli.utils.uri import VikingURI

from .schemas import ResourceDocumentDraft, WikiResourceInput

WIKI_DOCUMENT_MANIFEST_FILENAME = ".wiki_documents.json"
WIKI_DOCUMENT_MANIFEST_VERSION = 1


def document_manifest_uri(root_uri: str) -> str:
    return f"{root_uri.rstrip('/')}/{WIKI_DOCUMENT_MANIFEST_FILENAME}"


async def write_document_manifest(
    viking_fs: Any,
    *,
    root_uri: str,
    drafts: list[ResourceDocumentDraft],
    ctx: Any,
    source_format: str | None = None,
    lock_handle: Any = None,
) -> None:
    if not root_uri or not drafts:
        return
    documents = _normalize_manifest_drafts(drafts, source_format=source_format)
    payload = {
        "version": WIKI_DOCUMENT_MANIFEST_VERSION,
        "documents": [draft.model_dump(mode="json") for draft in documents],
    }
    await viking_fs.write_file(
        document_manifest_uri(root_uri),
        json.dumps(payload, ensure_ascii=False, indent=2),
        ctx=ctx,
        lock_handle=lock_handle,
    )


def _normalize_manifest_drafts(
    drafts: list[ResourceDocumentDraft],
    *,
    source_format: str | None,
) -> list[ResourceDocumentDraft]:
    if source_format == "directory" or len(drafts) != 1:
        return drafts
    draft = drafts[0]
    return [
        ResourceDocumentDraft(
            doc_id=draft.doc_id,
            title=draft.title,
            relative_uri="",
        )
    ]


async def load_document_manifest(viking_fs: Any, *, root_uri: str, ctx: Any) -> list[ResourceDocumentDraft]:
    uri = document_manifest_uri(root_uri)
    if not await viking_fs.exists(uri, ctx=ctx):
        return []
    raw = await viking_fs.read_file(uri, ctx=ctx)
    payload = json.loads(raw or "{}")
    documents = payload.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError(f"{WIKI_DOCUMENT_MANIFEST_FILENAME} documents must be a list")
    return [ResourceDocumentDraft.model_validate(item) for item in documents]


def wiki_inputs_from_manifest(root_uri: str, drafts: list[ResourceDocumentDraft]) -> list[WikiResourceInput]:
    return [_wiki_input_from_draft(root_uri, draft) for draft in drafts]


def _wiki_input_from_draft(root_uri: str, draft: ResourceDocumentDraft) -> WikiResourceInput:
    relative_uri = _normalize_relative_uri(draft.relative_uri)
    resource_uri = VikingURI(root_uri).join(relative_uri).uri if relative_uri else root_uri
    return WikiResourceInput(
        doc_id=draft.doc_id,
        resource_uri=resource_uri,
        title=draft.title,
        source_type="resource_document",
        document_dir_uri=resource_uri,
        metadata={
            "root_uri": root_uri,
            "relative_uri": relative_uri,
            "generated_from_document_manifest": True,
        },
    )


def _normalize_relative_uri(relative_uri: str) -> str:
    parts = [part for part in str(relative_uri or "").strip("/").split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"invalid relative_uri in {WIKI_DOCUMENT_MANIFEST_FILENAME}: {relative_uri}")
    return "/".join(parts)
