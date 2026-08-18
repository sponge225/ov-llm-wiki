# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Service layer for Wiki generation and cleanup."""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError, NotInitializedError
from openviking_cli.utils.config import get_openviking_config

from .client_adapter import WikiServiceClientAdapter
from .config import WikiConfig
from .content_loader import WikiContentLoader
from .document_manifest import load_document_manifest, wiki_inputs_from_manifest
from .pipeline import WikiPipeline
from .schemas import WikiResourceInput


class WikiService:
    """Build and clear generated Wiki assets."""

    def __init__(
        self,
        vikingdb: VikingDBManager | None = None,
        viking_fs: VikingFS | None = None,
    ):
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs

    def set_dependencies(self, *, vikingdb: VikingDBManager, viking_fs: VikingFS) -> None:
        self._vikingdb = vikingdb
        self._viking_fs = viking_fs

    async def build_wiki(
        self,
        *,
        resource_uris: list[str],
        ctx: RequestContext,
        wiki_root_uri: str = "viking://wiki/",
        card_input_mode: Literal["summary", "raw_chunk"] = "summary",
        max_card_input_chars: int = 20000,
    ) -> dict[str, Any]:
        self._ensure_initialized()
        self._validate_wiki_root_uri(wiki_root_uri)
        if card_input_mode not in {"summary", "raw_chunk"}:
            raise InvalidArgumentError("card_input_mode must be either 'summary' or 'raw_chunk'")
        if int(max_card_input_chars or 0) <= 0:
            raise InvalidArgumentError("max_card_input_chars must be positive")

        normalized_resource_uris = await self._normalize_resource_uris(resource_uris, ctx)
        wiki_inputs = await self._wiki_resource_inputs_from_uris(normalized_resource_uris, ctx)

        assert self._viking_fs is not None
        assert self._vikingdb is not None
        adapter = WikiServiceClientAdapter(self._viking_fs, self._vikingdb, ctx)
        loader = WikiContentLoader(self._viking_fs, self._vikingdb, ctx)
        wiki_config = WikiConfig(
            wiki_root_uri=wiki_root_uri,
            resource_root_uri=self._common_resource_root(normalized_resource_uris),
        )
        vlm_config = getattr(get_openviking_config(), "vlm", None)
        if vlm_config is not None:
            wiki_config.vlm_config = vlm_config._build_vlm_config_dict()
        pipeline = WikiPipeline(adapter, config=wiki_config)
        artifacts = await pipeline.run_from_inputs(
            wiki_inputs,
            content_loader=loader,
            card_input_mode=card_input_mode,
            max_card_input_chars=max_card_input_chars,
        )
        return {
            "status": "success",
            "docs": len(wiki_inputs),
            "cards": len(artifacts.cards),
            "nodes": len(artifacts.nodes),
            "node_contexts": len(artifacts.node_contexts),
            "card_input_mode": card_input_mode,
            "wiki_root_uri": wiki_root_uri,
            "resource_uris": normalized_resource_uris,
        }

    async def clear_wiki(
        self,
        *,
        ctx: RequestContext,
        wiki_root_uri: str = "viking://wiki/",
    ) -> dict[str, Any]:
        self._ensure_initialized()
        self._validate_wiki_root_uri(wiki_root_uri)
        assert self._viking_fs is not None
        missing = not await self._viking_fs.exists(wiki_root_uri, ctx=ctx)
        await self._viking_fs.rm(wiki_root_uri, recursive=True, ctx=ctx)
        return {
            "status": "success",
            "wiki_root_uri": wiki_root_uri,
            "cleared": not missing,
            "missing": missing,
        }

    def _ensure_initialized(self) -> None:
        if self._vikingdb is None:
            raise NotInitializedError("VikingDBManager")
        if self._viking_fs is None:
            raise NotInitializedError("VikingFS")

    async def _normalize_resource_uris(
        self,
        resource_uris: list[str],
        ctx: RequestContext,
    ) -> list[str]:
        if not resource_uris:
            raise InvalidArgumentError("resource_uris must not be empty")
        assert self._viking_fs is not None
        normalized: list[str] = []
        seen: set[str] = set()
        for uri in resource_uris:
            clean_uri = str(uri or "").strip()
            self._validate_resource_uri(clean_uri)
            canonical_uri = clean_uri.rstrip("/")
            if canonical_uri in seen:
                continue
            if not await self._viking_fs.exists(canonical_uri, ctx=ctx):
                raise NotFoundError(canonical_uri, "resource")
            seen.add(canonical_uri)
            normalized.append(canonical_uri)
        return normalized

    async def _wiki_resource_inputs_from_uris(
        self,
        resource_uris: list[str],
        ctx: RequestContext,
    ) -> list[WikiResourceInput]:
        assert self._viking_fs is not None
        wiki_inputs: list[WikiResourceInput] = []
        for uri in resource_uris:
            drafts = await load_document_manifest(self._viking_fs, root_uri=uri, ctx=ctx)
            if drafts:
                wiki_inputs.extend(wiki_inputs_from_manifest(uri, drafts))
                continue
            wiki_inputs.append(self._wiki_resource_input_from_uri(uri))
        return wiki_inputs

    @staticmethod
    def _validate_resource_uri(uri: str) -> None:
        if not uri.startswith("viking://resources/"):
            raise InvalidArgumentError("resource_uris must start with viking://resources/")

    @staticmethod
    def _validate_wiki_root_uri(uri: str) -> None:
        if not uri.startswith("viking://wiki/"):
            raise InvalidArgumentError("wiki_root_uri must start with viking://wiki/")

    @staticmethod
    def _wiki_resource_input_from_uri(resource_uri: str) -> WikiResourceInput:
        title = resource_uri.rstrip("/").rsplit("/", 1)[-1] or "resource"
        doc_hash = hashlib.sha1(resource_uri.encode("utf-8")).hexdigest()[:12]
        return WikiResourceInput(
            doc_id=f"resource_{doc_hash}",
            resource_uri=resource_uri,
            title=title,
            source_type="resource_document",
            document_dir_uri=resource_uri,
            metadata={
                "root_uri": resource_uri,
                "generated_from_standalone_wiki_build": True,
            },
        )

    @staticmethod
    def _common_resource_root(resource_uris: list[str]) -> str:
        if len(resource_uris) == 1:
            return resource_uris[0]
        return "viking://resources/"
