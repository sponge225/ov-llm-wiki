"""Service-local client adapter for Wiki MVP writers."""

from __future__ import annotations

from typing import Any

from openviking.server.identity import RequestContext
from openviking.storage import VikingDBManager
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.viking_fs import VikingFS


class WikiServiceClientAdapter:
    """Expose the small client surface required by WikiVikingFSWriter."""

    def __init__(self, viking_fs: VikingFS, vikingdb: VikingDBManager, ctx: RequestContext):
        self.viking_fs = viking_fs
        self.vikingdb = vikingdb
        self.ctx = ctx
        self._writer = ContentWriteCoordinator(viking_fs=viking_fs, vikingdb=vikingdb)

    async def mkdir(self, uri: str, description: str | None = None) -> None:
        await self.viking_fs.mkdir(uri, exist_ok=True, ctx=self.ctx)
        if description:
            await self.viking_fs.write_file(f"{uri.rstrip('/')}/.abstract.md", description, ctx=self.ctx)

    async def write(self, uri: str, content: str, mode: str = "replace") -> dict[str, Any]:
        return await self._writer.write(
            uri=uri,
            content=content,
            mode=mode,
            wait=True,
            ctx=self.ctx,
        )
