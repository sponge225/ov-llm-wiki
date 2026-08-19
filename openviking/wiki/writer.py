"""vikingfs writer for Wiki assets."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from pydantic import BaseModel

from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import NotFoundError

from . import uri as wiki_uri
from .config import WikiConfig


class WikiVikingFSWriter:
    def __init__(
        self,
        *,
        viking_fs: VikingFS,
        vikingdb: VikingDBManager,
        ctx: RequestContext,
        config: WikiConfig,
        content_writer: Any | None = None,
    ):
        self.viking_fs = viking_fs
        self.ctx = ctx
        self.config = config
        self._writer = content_writer or ContentWriteCoordinator(viking_fs=viking_fs, vikingdb=vikingdb)

    async def ensure_dirs(self, node_ids: list[str] | None = None) -> None:
        """创建必要的目录"""
        dirs = [
            wiki_uri.wiki_root(self.config),
            wiki_uri.cards_dir(self.config),
            wiki_uri.nodes_dir(self.config),
            wiki_uri.run_dir(self.config),
        ]
        for node_id in node_ids or []:
            dirs.extend(
                [
                    wiki_uri.node_root_uri(self.config, node_id),
                    wiki_uri.node_documents_dir(self.config, node_id),
                    wiki_uri.node_sources_dir(self.config, node_id),
                ]
            )

        for directory in dirs:
            await self.viking_fs.mkdir(directory, exist_ok=True, ctx=self.ctx)

    async def write_text(self, uri: str, content: str) -> None:
        try:
            await self._writer.write(uri=uri, content=content, mode="create", wait=True, ctx=self.ctx)
        except Exception as exc:
            if not isinstance(exc, NotFoundError) and "exist" not in str(exc).lower():
                raise
            await self._writer.write(uri=uri, content=content, mode="replace", wait=True, ctx=self.ctx)

    async def write_json(self, uri: str, payload: Any) -> None:
        content = json.dumps(_to_jsonable(payload), ensure_ascii=False, indent=2)
        await self.write_text(uri, content)

    async def write_jsonl(self, uri: str, rows: list[Any]) -> None:
        content = "\n".join(json.dumps(_to_jsonable(row), ensure_ascii=False) for row in rows)
        if content:
            content += "\n"
        await self.write_text(uri, content)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value
