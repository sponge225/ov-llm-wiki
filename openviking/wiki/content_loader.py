"""Bounded resource content loading for Wiki document card generation."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from .schemas import ResourceDocument, SourceSection, WikiResourceInput

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext
    from openviking.storage import VikingDBManager
    from openviking.storage.viking_fs import VikingFS

LS_ALL_NODES = 2**31 - 1


class WikiCardInputMode(str, Enum):
    SUMMARY = "summary"
    RAW_CHUNK = "raw_chunk"


class WikiContentLoader:
    """Load bounded card input from a finalized resource directory."""

    def __init__(
        self,
        viking_fs: "VikingFS",
        vikingdb: "VikingDBManager",
        ctx: "RequestContext",
    ):
        self.viking_fs = viking_fs
        self.vikingdb = vikingdb
        self.ctx = ctx

    async def load_document(
        self,
        doc: WikiResourceInput,
        *,
        mode: WikiCardInputMode | str,
        max_card_input_chars: int,
    ) -> ResourceDocument:
        input_mode = WikiCardInputMode(mode)
        root_uri = doc.document_dir_uri or doc.resource_uri
        entries = await self._collect_entries(root_uri, mode=input_mode)
        missing = [entry["uri"] for entry in entries if entry.get("missing_summary")]
        if input_mode == WikiCardInputMode.SUMMARY and entries:
            usable_entries = [entry for entry in entries if not entry.get("missing_summary")]
            if not usable_entries:
                raise RuntimeError(
                    f"summary mode found no semantic abstracts under {root_uri}; "
                    "rerun after semantic generation succeeds or use raw_chunk mode"
                )
        content = self._render_entries(entries, max_chars=max_card_input_chars)
        source_sections = self._source_sections_from_entries(entries, max_chars=max_card_input_chars)
        return ResourceDocument(
            doc_id=doc.doc_id,
            resource_uri=doc.resource_uri,
            title=doc.title,
            content_or_structure=content,
            source_sections=source_sections,
            metadata={
                **doc.metadata,
                "card_input_mode": input_mode.value,
                "missing_summary_uris": missing,
            },
        )

    async def _collect_entries(
        self,
        root_uri: str,
        *,
        mode: WikiCardInputMode,
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []

        async def visit(uri: str, title_path: list[str]) -> None:
            if await self._is_directory(uri):
                for child in await self._list_children(uri):
                    name = str(child.get("name") or "").strip()
                    if not name or name in {".", ".."}:
                        continue
                    if name.startswith("."):
                        continue
                    child_uri = str(child.get("uri") or f"{uri.rstrip('/')}/{name}")
                    if self._is_hidden_semantic_file(child_uri):
                        continue
                    if self._entry_is_dir(child):
                        await visit(child_uri, [*title_path, name])
                    else:
                        await self._append_leaf(entries, child_uri, [*title_path, name], mode)
                return

            await self._append_leaf(entries, uri, title_path or [uri.rsplit("/", 1)[-1]], mode)

        await visit(root_uri, [root_uri.rstrip("/").rsplit("/", 1)[-1]])
        return entries

    def _source_sections_from_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        max_chars: int,
    ) -> list[SourceSection]:
        if not entries:
            return []
        max_chars = max(1000, int(max_chars or 100000))
        raw_sections = [
            (str(entry.get("uri") or "").strip(), str(entry.get("text") or "").strip())
            for entry in entries
        ]
        raw_sections = [(uri, text) for uri, text in raw_sections if uri and text]
        if not raw_sections:
            return []

        total_chars = sum(len(text) for _, text in raw_sections)
        if total_chars <= max_chars:
            return [
                SourceSection(section_uri=uri, content=text)
                for uri, text in raw_sections
            ]

        per_section_budget = max(400, max_chars // max(1, len(raw_sections)))
        return [
            SourceSection(section_uri=uri, content=self._clip(text, per_section_budget))
            for uri, text in raw_sections
        ]

    async def _append_leaf(
        self,
        entries: list[dict[str, Any]],
        uri: str,
        title_path: list[str],
        mode: WikiCardInputMode,
    ) -> None:
        if self._is_hidden_semantic_file(uri):
            return
        if mode == WikiCardInputMode.RAW_CHUNK:
            text = await self._safe_read(uri)
            if not text:
                return
            entries.append(
                {"kind": "leaf_raw", "uri": uri, "title_path": title_path, "text": text}
            )
            return

        text = await self._leaf_abstract(uri)
        missing = not bool(text)
        if missing:
            text = "[summary missing]"
        entries.append(
            {
                "kind": "leaf_summary",
                "uri": uri,
                "title_path": title_path,
                "text": text,
                "missing_summary": missing,
            }
        )

    async def _leaf_abstract(self, uri: str) -> str:
        records = await self.vikingdb.get_context_by_uri(
            uri=uri,
            level=2,
            limit=1,
            ctx=self.ctx,
        )
        if records:
            abstract = str(records[0].get("abstract") or "").strip()
            if abstract:
                return abstract
        return ""

    def _render_entries(self, entries: list[dict[str, Any]], *, max_chars: int) -> str:
        if not entries:
            return ""
        max_chars = max(1000, int(max_chars or 100000))
        rendered = [self._render_entry(entry) for entry in entries]
        joined = "\n\n".join(rendered)
        if len(joined) <= max_chars:
            return joined

        per_entry_budget = max(600, max_chars // max(1, len(rendered)))
        compacted = [self._clip(text, per_entry_budget) for text in rendered]
        joined = "\n\n".join(compacted)
        if len(joined) <= max_chars:
            return joined

        # Preserve coverage over all entries instead of dropping tail entries.
        per_entry_budget = max(400, (max_chars - len(rendered) * 2) // max(1, len(rendered)))
        return "\n\n".join(self._clip(text, per_entry_budget) for text in rendered)

    @staticmethod
    def _render_entry(entry: dict[str, Any]) -> str:
        title = " / ".join(str(part) for part in entry.get("title_path") or [])
        return (
            f"URI: {entry.get('uri', '')}\n"
            f"Type: {entry.get('kind', '')}\n"
            f"Title Path: {title}\n"
            f"Content:\n{entry.get('text', '')}"
        ).strip()

    @staticmethod
    def _clip(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        if max_chars <= 20:
            return text[:max_chars]
        return text[: max_chars - 20].rstrip() + "\n...(truncated)"

    async def _is_directory(self, uri: str) -> bool:
        try:
            stat = await self.viking_fs.stat(uri, ctx=self.ctx)
        except Exception:
            return False
        return bool(isinstance(stat, dict) and stat.get("isDir"))

    async def _list_children(self, uri: str) -> list[dict[str, Any]]:
        try:
            return await self.viking_fs.ls(
                uri,
                show_all_hidden=True,
                node_limit=LS_ALL_NODES,
                ctx=self.ctx,
            )
        except Exception:
            return []

    async def _safe_read(self, uri: str) -> str:
        try:
            return str(await self.viking_fs.read_file(uri, ctx=self.ctx) or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _entry_is_dir(entry: dict[str, Any]) -> bool:
        return bool(entry.get("isDir", False)) or entry.get("type") == "directory"

    @staticmethod
    def _is_hidden_semantic_file(uri: str) -> bool:
        return uri.endswith("/.abstract.md") or uri.endswith("/.overview.md")
