# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
File System Service for OpenViking.

Provides file system operations: ls, mkdir, rm, mv, tree, stat, read, abstract, overview, grep, glob.
"""

import json
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from openviking.core.namespace import context_type_for_uri
from openviking.core.uri_validation import validate_optional_viking_uri, validate_viking_uri
from openviking.privacy import (
    UserPrivacyConfigService,
    get_skill_name_from_uri,
    restore_skill_content,
)
from openviking.resource.watch_storage import is_watch_task_control_uri
from openviking.server.identity import RequestContext
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.storage.content_write import ContentWriteCoordinator
from openviking.storage.queuefs import SemanticMsg, get_queue_manager
from openviking.storage.queuefs.semantic_msg import build_semantic_coalesce_key
from openviking.storage.viking_fs import VikingFS
from openviking.telemetry import get_current_telemetry
from openviking.telemetry.request_wait_tracker import get_request_wait_tracker
from openviking.telemetry.resource_summary import build_queue_status_payload
from openviking.utils.embedding_utils import vectorize_directory_meta
from openviking_cli.exceptions import DeadlineExceededError, NotFoundError, NotInitializedError
from openviking_cli.utils import VikingURI, get_logger

logger = get_logger(__name__)

_WIKI_NODES_URI = "viking://wiki/nodes"
_WIKI_NODES_JSON_URI = "viking://wiki/nodes.json"
_WIKI_SOURCE_ASSIGNMENTS_URI = "viking://wiki/source_assignments.json"


def _normalize_uri(uri: str | None) -> str:
    normalized = (uri or "").strip()
    if normalized == "viking://":
        return normalized
    return normalized.rstrip("/")


def _is_wiki_node_grep_uri(uri: str | None) -> bool:
    normalized = _normalize_uri(uri)
    if _is_wiki_document_uri(normalized):
        return True
    if normalized == _WIKI_NODES_URI:
        return True
    prefix = f"{_WIKI_NODES_URI}/"
    if not normalized.startswith(prefix):
        return False
    remainder = normalized[len(prefix) :]
    return bool(remainder) and "/" not in remainder


def _is_wiki_node_ls_uri(uri: str | None) -> bool:
    normalized = _normalize_uri(uri)
    if normalized == _WIKI_NODES_URI:
        return True
    prefix = f"{_WIKI_NODES_URI}/"
    if not normalized.startswith(prefix):
        return False
    remainder = normalized[len(prefix) :]
    return bool(remainder) and "/" not in remainder


def _is_wiki_document_uri(uri: str | None) -> bool:
    normalized = _normalize_uri(uri)
    return normalized.startswith(f"{_WIKI_NODES_URI}/") and "/documents" in normalized


def _wiki_node_id_from_uri(uri: str) -> str | None:
    normalized = _normalize_uri(uri)
    if normalized == _WIKI_NODES_URI:
        return None
    prefix = f"{_WIKI_NODES_URI}/"
    if not normalized.startswith(prefix):
        return None
    remainder = normalized[len(prefix) :]
    node_id = remainder.split("/", 1)[0]
    return node_id or None


def _wiki_subtree_node_ids(
    nodes: list[dict[str, Any]],
    target_node_id: str | None,
) -> list[str]:
    node_ids = [str(node.get("node_id")) for node in nodes if node.get("node_id")]
    if target_node_id is None:
        return node_ids

    children_by_parent: dict[str, list[str]] = {}
    for node in nodes:
        node_id = node.get("node_id")
        if not node_id:
            continue
        for parent_node_id in _wiki_parent_node_ids(node):
            children_by_parent.setdefault(parent_node_id, []).append(str(node_id))
        for child_node_id in node.get("child_node_ids") or []:
            children_by_parent.setdefault(str(node_id), []).append(str(child_node_id))

    ordered: list[str] = []
    seen: set[str] = set()
    stack = [target_node_id]
    while stack:
        node_id = stack.pop(0)
        if node_id in seen:
            continue
        seen.add(node_id)
        ordered.append(node_id)
        stack.extend(children_by_parent.get(node_id, []))
    return ordered


def _wiki_direct_child_node_ids(
    nodes: list[dict[str, Any]],
    target_node_id: str | None,
) -> list[str]:
    if target_node_id is None:
        return [
            str(node.get("node_id"))
            for node in nodes
            if node.get("node_id") and not _wiki_parent_node_ids(node)
        ]

    direct: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if str(node.get("node_id") or "") != target_node_id:
            continue
        for child_node_id in node.get("child_node_ids") or []:
            child_id = str(child_node_id)
            if child_id and child_id not in seen:
                direct.append(child_id)
                seen.add(child_id)
        break

    for node in nodes:
        node_id = node.get("node_id")
        if not node_id or target_node_id not in _wiki_parent_node_ids(node):
            continue
        node_id = str(node_id)
        if node_id not in seen:
            direct.append(node_id)
            seen.add(node_id)
    return direct


def _wiki_parent_node_ids(node: dict[str, Any]) -> list[str]:
    parent_node_ids = node.get("parent_node_ids")
    if isinstance(parent_node_ids, list):
        return [str(parent_node_id) for parent_node_id in parent_node_ids if parent_node_id]
    parent_node_id = node.get("parent_node_id")
    return [str(parent_node_id)] if parent_node_id else []


def _wiki_node_title(node: dict[str, Any] | None, node_id: str) -> str:
    if node and node.get("title"):
        return str(node["title"])
    return node_id


def _uri_leaf(uri: str) -> str:
    normalized = _normalize_uri(uri)
    if normalized == "viking://":
        return normalized
    return normalized.rsplit("/", 1)[-1]


def _uri_parent(uri: str) -> str | None:
    parent = VikingURI(uri).parent
    return _normalize_uri(parent.uri) if parent else None


def _is_descendant_uri(uri: str, root_uri: str) -> bool:
    return uri.startswith(f"{root_uri}/")


def _relative_uri_path(uri: str, root_uri: str) -> str:
    normalized_uri = _normalize_uri(uri)
    normalized_root = _normalize_uri(root_uri)
    if normalized_uri == normalized_root:
        return ""
    if _is_descendant_uri(normalized_uri, normalized_root):
        return normalized_uri[len(normalized_root) + 1 :]
    return normalized_uri


def _wiki_node_entry(node_id: str, node: dict[str, Any] | None, output: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": node_id,
        "uri": f"{_WIKI_NODES_URI}/{node_id}",
        "isDir": True,
        "size": 0,
    }
    title = node.get("title") if node else None
    if title:
        entry["title"] = str(title)
        if output == "agent":
            entry["abstract"] = str(title)
    elif output == "agent":
        entry["abstract"] = ""
    return entry


def _wiki_documents_entry(node_id: str, output: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "documents",
        "uri": f"{_WIKI_NODES_URI}/{node_id}/documents",
        "isDir": True,
        "size": 0,
    }
    if output == "agent":
        entry["abstract"] = "Wiki node knowledge documents"
    return entry


def _filter_wiki_document_matches(matches: list[Any]) -> list[Any]:
    filtered = []
    for match in matches:
        match_uri = match.get("uri", "") if isinstance(match, dict) else getattr(match, "uri", "")
        normalized_match_uri = _normalize_uri(str(match_uri or ""))
        if (
            normalized_match_uri.startswith(f"{_WIKI_NODES_URI}/")
            and "/documents/" in normalized_match_uri
            and normalized_match_uri.endswith(".md")
        ):
            filtered.append(match)
    return filtered

if TYPE_CHECKING:
    from openviking.resource.watch_manager import WatchManager
    from openviking.resource.watch_scheduler import WatchScheduler
    from openviking.service.resource_memory_link_service import ResourceMemoryLinkService
    from openviking.storage import VikingDBManager


class FSService:
    """File system operations service."""

    def __init__(
        self,
        viking_fs: Optional[VikingFS] = None,
        vikingdb: Optional["VikingDBManager"] = None,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
    ):
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service
        self._resource_memory_link_service = resource_memory_link_service
        self._watch_scheduler = watch_scheduler

    def set_dependencies(
        self,
        viking_fs: VikingFS,
        vikingdb: Optional["VikingDBManager"] = None,
        privacy_config_service: Optional[UserPrivacyConfigService] = None,
        resource_memory_link_service: Optional["ResourceMemoryLinkService"] = None,
        watch_scheduler: Optional["WatchScheduler"] = None,
    ) -> None:
        """Set service dependencies (for deferred initialization)."""
        self._viking_fs = viking_fs
        self._vikingdb = vikingdb
        self._privacy_config_service = privacy_config_service
        self._resource_memory_link_service = resource_memory_link_service
        self._watch_scheduler = watch_scheduler

    def _ensure_initialized(self) -> VikingFS:
        """Ensure VikingFS is initialized."""
        if not self._viking_fs:
            raise NotInitializedError("VikingFS")
        return self._viking_fs

    def _get_watch_manager(self) -> Optional["WatchManager"]:
        if not self._watch_scheduler:
            return None
        return self._watch_scheduler.watch_manager

    async def ls(
        self,
        uri: str,
        ctx: RequestContext,
        recursive: bool = False,
        simple: bool = False,
        output: str = "original",
        abs_limit: int = 256,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> List[Any]:
        """List directory contents.

        Args:
            uri: Viking URI
            recursive: List all subdirectories recursively
            simple: Return only relative path list
            output: str = "original" or "agent"
            abs_limit: int = 256 if output == "agent" else ignore
            show_all_hidden: bool = False (list all hidden files, like -a)
            node_limit: int = 1000 (maximum number of nodes to list)
            sort_by: Optional sort field for non-recursive listings
            sort_order: Sort direction, "asc" or "desc"
        """
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)

        if not recursive and _is_wiki_node_ls_uri(uri):
            entries = await self._wiki_node_ls_entries(
                viking_fs,
                uri,
                ctx,
                output=output,
                node_limit=node_limit,
                sort_by=sort_by,
                sort_order=sort_order,
            )
            if simple:
                return [entry.get("uri", "") for entry in entries]
            return entries

        if simple:
            # Only return URIs — skip expensive abstract fetching to save tokens
            if recursive:
                entries = await viking_fs.tree(
                    uri,
                    ctx=ctx,
                    output="original",
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
                    level_limit=level_limit,
                )
            else:
                entries = await viking_fs.ls(
                    uri,
                    ctx=ctx,
                    output="original",
                    show_all_hidden=show_all_hidden,
                    node_limit=node_limit,
                    sort_by=sort_by,
                    sort_order=sort_order,
                )
            return [e.get("uri", "") for e in entries]

        if recursive:
            entries = await viking_fs.tree(
                uri,
                ctx=ctx,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
                level_limit=level_limit,
            )
        else:
            entries = await viking_fs.ls(
                uri,
                ctx=ctx,
                output=output,
                abs_limit=abs_limit,
                show_all_hidden=show_all_hidden,
                node_limit=node_limit,
                sort_by=sort_by,
                sort_order=sort_order,
            )
        return entries

    async def _wiki_node_ls_entries(
        self,
        viking_fs: VikingFS,
        uri: str,
        ctx: RequestContext,
        *,
        output: str,
        node_limit: int,
        sort_by: Optional[str],
        sort_order: str,
    ) -> list[dict[str, Any]]:
        if output not in {"original", "agent"}:
            raise ValueError(f"Invalid output format: {output}")
        if sort_order not in {"asc", "desc"}:
            raise ValueError("sort_order must be 'asc' or 'desc'")

        normalized = _normalize_uri(uri)
        nodes = await self._read_wiki_nodes(viking_fs, ctx)
        nodes_by_id = {str(node.get("node_id")): node for node in nodes if node.get("node_id")}
        target_node_id = _wiki_node_id_from_uri(normalized)
        child_node_ids = _wiki_direct_child_node_ids(nodes, target_node_id)

        entries: list[dict[str, Any]] = []
        if target_node_id:
            entries.append(_wiki_documents_entry(target_node_id, output))
        entries.extend(
            _wiki_node_entry(node_id, nodes_by_id.get(node_id), output)
            for node_id in child_node_ids
            if node_id in nodes_by_id
        )

        if sort_by == "name":
            entries.sort(
                key=lambda entry: (str(entry.get("name", "")).lower(), str(entry.get("name", ""))),
                reverse=sort_order == "desc",
            )
        elif sort_by not in {None, "mtime"}:
            raise ValueError("sort_by must be 'name' or 'mtime'")

        if node_limit is not None and node_limit > 0:
            return entries[:node_limit]
        return entries

    async def mkdir(
        self,
        uri: str,
        ctx: RequestContext,
        description: Optional[str] = None,
    ) -> None:
        """Create directory."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        await viking_fs.mkdir(uri, ctx=ctx)
        abstract = self._normalize_directory_description(description)
        if not abstract:
            return

        directory_uri, abstract_uri = self._resolve_directory_uris(uri)
        await viking_fs.write_file(abstract_uri, abstract, ctx=ctx)
        await vectorize_directory_meta(
            uri=directory_uri,
            abstract=abstract,
            overview="",
            context_type=context_type_for_uri(directory_uri),
            ctx=ctx,
            include_overview=False,
        )

    @staticmethod
    def _normalize_directory_description(description: Optional[str]) -> Optional[str]:
        if description is None:
            return None
        abstract = description.strip()
        return abstract or None

    @staticmethod
    def _resolve_directory_uris(uri: str) -> tuple[str, str]:
        abstract_uri = VikingURI(uri).join(".abstract.md").uri
        directory_uri = VikingURI(abstract_uri).parent.uri
        return directory_uri, abstract_uri

    async def rm(
        self,
        uri: str,
        ctx: RequestContext,
        recursive: bool = False,
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Remove resource."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        cleanup_result: Optional[Dict[str, Any]] = None
        context_type = context_type_for_uri(uri)
        refresh_parent_uri = self._semantic_refresh_parent_uri(uri, context_type)
        memory_overview_uri = self._memory_overview_parent_uri(uri, context_type)
        result = await viking_fs.rm(uri, recursive=recursive, ctx=ctx)
        await self._sync_watch_after_rm(uri, account_id=ctx.account_id, context_type=context_type)
        queue_status = None
        request_registered = False
        telemetry_id = get_current_telemetry().telemetry_id
        try:
            if refresh_parent_uri:
                if wait and telemetry_id:
                    get_request_wait_tracker().register_request(telemetry_id)
                    request_registered = True
                await self._enqueue_delete_refresh(
                    root_uri=refresh_parent_uri,
                    deleted_uri=uri,
                    context_type=context_type,
                    ctx=ctx,
                )
            if self._resource_memory_link_service and context_type == "resource":
                cleanup_result = await self._resource_memory_link_service.before_resource_delete(
                    ctx=ctx,
                    resource_uri=uri,
                    recursive=recursive,
                )
            if memory_overview_uri:
                await MemoryUpdater.refresh_schema_overview(
                    viking_fs=viking_fs,
                    directory_uri=memory_overview_uri,
                    ctx=ctx,
                )
            for cleanup_overview_uri in self._memory_overview_parent_uris_from_cleanup(
                cleanup_result
            ):
                await MemoryUpdater.refresh_schema_overview(
                    viking_fs=viking_fs,
                    directory_uri=cleanup_overview_uri,
                    ctx=ctx,
                )
            if refresh_parent_uri and wait:
                queue_status = await self._wait_for_refresh(timeout=timeout)
        finally:
            if request_registered:
                get_request_wait_tracker().cleanup(telemetry_id)
        if cleanup_result is not None and isinstance(result, dict):
            result["memory_cleanup"] = cleanup_result
        if refresh_parent_uri and isinstance(result, dict):
            result["semantic_root_uri"] = refresh_parent_uri
            result["semantic_status"] = self._semantic_refresh_status(
                wait=wait,
                queue_status=queue_status,
            )
            if queue_status is not None:
                result["queue_status"] = queue_status
        return result

    @staticmethod
    def _semantic_refresh_status(
        *,
        wait: bool,
        queue_status: Optional[Dict[str, Any]],
    ) -> str:
        if not wait:
            return "queued"
        if not isinstance(queue_status, dict):
            return "complete"
        semantic = queue_status.get("Semantic", {})
        if not isinstance(semantic, dict):
            return "complete"
        try:
            if int(semantic.get("error_count", 0) or 0) > 0:
                return "failed"
        except (TypeError, ValueError):
            if semantic.get("errors"):
                return "failed"
        if semantic.get("errors"):
            return "failed"
        return "complete"

    @staticmethod
    def _semantic_refresh_parent_uri(uri: str, context_type: str) -> Optional[str]:
        if context_type != "resource":
            return None
        parent = VikingURI(uri).parent
        return parent.uri if parent else None

    @staticmethod
    def _memory_overview_parent_uri(uri: str, context_type: str) -> Optional[str]:
        if context_type != "memory":
            return None
        leaf = uri.rstrip("/").rsplit("/", 1)[-1]
        if leaf in {".abstract.md", ".overview.md", ".relations.json"}:
            return None
        parent = VikingURI(uri).parent
        if parent is None:
            return None
        if not MemoryUpdater.memory_type_from_uri(parent.uri):
            return None
        return parent.uri

    @classmethod
    def _memory_overview_parent_uris_from_cleanup(
        cls,
        cleanup_result: Optional[Dict[str, Any]],
    ) -> List[str]:
        if not isinstance(cleanup_result, dict):
            return []

        overview_uris: List[str] = []
        for field in ("memory_uris", "deleted_memory_uris"):
            values = cleanup_result.get(field)
            if not isinstance(values, list):
                continue
            for memory_uri in values:
                if not isinstance(memory_uri, str):
                    continue
                overview_uri = cls._memory_overview_parent_uri(
                    memory_uri,
                    context_type_for_uri(memory_uri),
                )
                if overview_uri:
                    overview_uris.append(overview_uri)
        return list(dict.fromkeys(overview_uris))

    async def _enqueue_delete_refresh(
        self,
        *,
        root_uri: str,
        deleted_uri: str,
        context_type: str,
        ctx: RequestContext,
    ) -> None:
        try:
            queue_manager = get_queue_manager()
        except RuntimeError as exc:
            logger.warning("QueueManager not available, skipping delete refresh: %s", exc)
            return
        semantic_queue = queue_manager.get_queue(queue_manager.SEMANTIC, allow_create=True)
        telemetry_id = get_current_telemetry().telemetry_id
        msg = SemanticMsg(
            uri=root_uri,
            context_type=context_type,
            recursive=False,
            account_id=ctx.account_id,
            user_id=ctx.user.user_id,
            peer_id=ctx.user.user_id,
            role=str(ctx.role),
            skip_vectorization=False,
            telemetry_id=telemetry_id,
            coalesce_key=build_semantic_coalesce_key(
                context_type=context_type,
                uri=root_uri,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
                peer_id=ctx.user.user_id,
            ),
            changes={"deleted": [deleted_uri]},
        )
        if telemetry_id:
            get_request_wait_tracker().register_semantic_root(telemetry_id, msg.id)
        try:
            await semantic_queue.enqueue(msg)
        except Exception as exc:
            if telemetry_id:
                get_request_wait_tracker().mark_semantic_failed(telemetry_id, msg.id, str(exc))
            raise

    async def _wait_for_refresh(self, *, timeout: Optional[float]) -> Dict[str, Any]:
        telemetry_id = get_current_telemetry().telemetry_id
        if telemetry_id:
            try:
                await get_request_wait_tracker().wait_for_request(telemetry_id, timeout=timeout)
            except TimeoutError as exc:
                raise DeadlineExceededError("queue processing", timeout) from exc
            return get_request_wait_tracker().build_queue_status(telemetry_id)
        try:
            return build_queue_status_payload(
                await get_queue_manager().wait_complete(timeout=timeout)
            )
        except TimeoutError as exc:
            raise DeadlineExceededError("queue processing", timeout) from exc

    async def mv(self, from_uri: str, to_uri: str, ctx: RequestContext) -> None:
        """Move resource."""
        from_uri = validate_viking_uri(from_uri, field_name="from_uri")
        to_uri = validate_viking_uri(to_uri, field_name="to_uri")
        viking_fs = self._ensure_initialized()
        watch_manager = self._get_watch_manager()
        if not watch_manager or context_type_for_uri(from_uri) != "resource":
            await viking_fs.mv(from_uri, to_uri, ctx=ctx)
            return
        if context_type_for_uri(to_uri) != "resource":
            await viking_fs.mv(from_uri, to_uri, ctx=ctx)
            return
        if is_watch_task_control_uri(from_uri) or is_watch_task_control_uri(to_uri):
            await viking_fs.mv(from_uri, to_uri, ctx=ctx)
            return

        await watch_manager.sync_tasks_with_resource_move_internal(
            from_uri,
            to_uri,
            account_id=ctx.account_id,
            move_resource=lambda: viking_fs.mv(from_uri, to_uri, ctx=ctx),
            rollback_resource=lambda: viking_fs.mv(to_uri, from_uri, ctx=ctx),
        )

    async def _sync_watch_after_rm(
        self, uri: str, *, account_id: str, context_type: str
    ) -> None:
        if context_type != "resource":
            return
        if is_watch_task_control_uri(uri):
            return
        watch_manager = self._get_watch_manager()
        if not watch_manager:
            return
        deactivated = await watch_manager.deactivate_tasks_under_uri_internal(uri, account_id)
        if deactivated:
            logger.info(
                "Deactivated %d watch task(s) after deleting %s",
                len(deactivated),
                uri,
            )

    async def tree(
        self,
        uri: str,
        ctx: RequestContext,
        output: str = "original",
        abs_limit: int = 128,
        show_all_hidden: bool = False,
        node_limit: int = 1000,
        level_limit: int = 3,
    ) -> List[Dict[str, Any]]:
        """Get directory tree."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.tree(
            uri,
            ctx=ctx,
            output=output,
            abs_limit=abs_limit,
            show_all_hidden=show_all_hidden,
            node_limit=node_limit,
            level_limit=level_limit,
        )

    async def stat(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Get resource status."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.stat(uri, ctx=ctx)

    async def system_sync_status(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Return multi-write sync status for one Viking URI subtree."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.system_sync_status(uri, ctx=ctx)

    async def system_sync_retry(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        """Retry multi-write sync work for one Viking URI subtree."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.system_sync_retry(uri, ctx=ctx)

    async def read(self, uri: str, ctx: RequestContext, offset: int = 0, limit: int = -1) -> str:
        """Read file content."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        content = await viking_fs.read_file(uri, ctx=ctx)
        skill_name = get_skill_name_from_uri(uri)
        if skill_name and self._privacy_config_service:
            current = await self._privacy_config_service.get_current(
                ctx=ctx,
                category="skill",
                target_key=skill_name,
            )
            if current:
                content = restore_skill_content(content, skill_name, current.values)

        if offset == 0 and limit == -1:
            return content
        lines = content.splitlines(keepends=True)
        sliced = lines[offset:] if limit == -1 else lines[offset : offset + limit]
        return "".join(sliced)

    async def abstract(self, uri: str, ctx: RequestContext) -> str:
        """Read L0 abstract (.abstract.md)."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.abstract(uri, ctx=ctx)

    async def overview(self, uri: str, ctx: RequestContext) -> str:
        """Read L1 overview (.overview.md)."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.overview(uri, ctx=ctx)

    async def grep(
        self,
        uri: str,
        pattern: str,
        ctx: RequestContext,
        exclude_uri: Optional[str] = None,
        case_insensitive: bool = False,
        node_limit: Optional[int] = None,
        level_limit: int = 10,
    ) -> Dict:
        """Content search."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        exclude_uri = validate_optional_viking_uri(exclude_uri, field_name="exclude_uri") or None
        if _is_wiki_node_grep_uri(uri):
            target_uris = await self._wiki_grep_document_targets(viking_fs, uri, ctx)
            return await self._grep_multiple_targets(
                viking_fs,
                target_uris,
                pattern,
                ctx=ctx,
                exclude_uri=exclude_uri,
                case_insensitive=case_insensitive,
                node_limit=node_limit,
                level_limit=level_limit,
            )
        return await viking_fs.grep(
            uri,
            pattern,
            exclude_uri=exclude_uri,
            case_insensitive=case_insensitive,
            node_limit=node_limit,
            level_limit=level_limit,
            ctx=ctx,
        )

    async def _wiki_grep_document_targets(
        self,
        viking_fs: VikingFS,
        uri: str,
        ctx: RequestContext,
    ) -> list[str]:
        normalized = _normalize_uri(uri)
        if _is_wiki_document_uri(normalized):
            return [uri]

        nodes = await self._read_wiki_nodes(viking_fs, ctx)
        target_node_id = _wiki_node_id_from_uri(normalized)
        node_ids = _wiki_subtree_node_ids(nodes, target_node_id)
        if not node_ids and target_node_id:
            node_ids = [target_node_id]

        return [f"{_WIKI_NODES_URI}/{node_id}/documents/" for node_id in node_ids]

    async def _read_wiki_nodes(self, viking_fs: VikingFS, ctx: RequestContext) -> list[dict[str, Any]]:
        content = await viking_fs.read_file(_WIKI_NODES_JSON_URI, ctx=ctx)
        payload = json.loads(content)
        nodes = payload.get("nodes") if isinstance(payload, dict) else payload
        return [node for node in nodes or [] if isinstance(node, dict)]

    async def _read_wiki_source_assignments(
        self, viking_fs: VikingFS, ctx: RequestContext
    ) -> dict[str, Any]:
        content = await viking_fs.read_file(_WIKI_SOURCE_ASSIGNMENTS_URI, ctx=ctx)
        payload = json.loads(content)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _wiki_document_refs_by_node(
        source_assignments: dict[str, Any],
    ) -> dict[str, list[dict[str, Any]]]:
        refs_by_node = source_assignments.get("source_refs_by_node")
        if not isinstance(refs_by_node, dict):
            return {}

        document_refs_by_node: dict[str, list[dict[str, Any]]] = {}
        for node_id, refs in refs_by_node.items():
            if not isinstance(refs, list):
                continue
            document_refs = [
                ref
                for ref in refs
                if isinstance(ref, dict)
                and ref.get("ref_type") == "document"
                and ref.get("doc_id")
                and ref.get("resource_uri")
            ]
            if document_refs:
                document_refs_by_node[str(node_id)] = document_refs
        return document_refs_by_node

    @staticmethod
    def _wiki_resource_document_roots(
        document_refs_by_node: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, str]]:
        roots: dict[str, dict[str, str]] = {}
        for refs in document_refs_by_node.values():
            for ref in refs:
                root_uri = _normalize_uri(str(ref.get("resource_uri") or ""))
                doc_id = str(ref.get("doc_id") or "")
                if root_uri and doc_id:
                    roots[root_uri] = {"doc_id": doc_id, "resource_uri": root_uri}
        return roots

    @staticmethod
    def _wiki_context_roots_for_uri(
        uri: str,
        *,
        nodes_by_id: dict[str, dict[str, Any]],
        document_refs_by_node: dict[str, list[dict[str, Any]]],
        document_roots: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        normalized = _normalize_uri(uri)
        node_id = _wiki_node_id_from_uri(normalized)
        if node_id:
            node = nodes_by_id.get(node_id)
            if not node:
                return {"kind": "unmatched", "root_node_ids": [], "resource_tree_root": None}
            parent_node_ids = [
                parent_node_id
                for parent_node_id in _wiki_parent_node_ids(node)
                if parent_node_id in nodes_by_id
            ]
            return {
                "kind": "wiki_node",
                "root_node_ids": parent_node_ids or [node_id],
                "resource_tree_root": None,
                "document_root": None,
            }

        if normalized in document_roots:
            root_node_ids = [
                node_id
                for node_id, refs in document_refs_by_node.items()
                if any(_normalize_uri(str(ref.get("resource_uri") or "")) == normalized for ref in refs)
            ]
            return {
                "kind": "resource_document_root",
                "root_node_ids": root_node_ids,
                "resource_tree_root": None,
                "document_root": normalized,
            }

        matching_document_root = ""
        for document_root in document_roots:
            if _is_descendant_uri(normalized, document_root) and len(document_root) > len(
                matching_document_root
            ):
                matching_document_root = document_root

        if not matching_document_root:
            return {"kind": "unmatched", "root_node_ids": [], "resource_tree_root": None}

        parent_uri = _uri_parent(normalized)
        if not parent_uri:
            return {"kind": "unmatched", "root_node_ids": [], "resource_tree_root": None}
        resource_tree_root = (
            parent_uri
            if parent_uri == matching_document_root
            or _is_descendant_uri(parent_uri, matching_document_root)
            else matching_document_root
        )
        return {
            "kind": "resource_document_child",
            "root_node_ids": [],
            "resource_tree_root": resource_tree_root,
            "document_root": matching_document_root,
        }

    async def context_tree(
        self,
        uri: str,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        """Return a compact directory tree around a Wiki or resource URI."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        try:
            nodes = await self._read_wiki_nodes(viking_fs, ctx)
            source_assignments = await self._read_wiki_source_assignments(viking_fs, ctx)
        except (NotFoundError, FileNotFoundError, KeyError):
            return {"kind": "unmatched", "document_uri_map": {}, "lines": []}
        nodes_by_id = {str(node.get("node_id")): node for node in nodes if node.get("node_id")}
        document_refs_by_node = self._wiki_document_refs_by_node(source_assignments)
        document_roots = self._wiki_resource_document_roots(document_refs_by_node)
        roots = self._wiki_context_roots_for_uri(
            uri,
            nodes_by_id=nodes_by_id,
            document_refs_by_node=document_refs_by_node,
            document_roots=document_roots,
        )

        document_uri_map: dict[str, str] = {}
        lines: list[str] = []

        def add_line(line: str) -> None:
            lines.append(line)

        async def add_resource_tree(
            *,
            doc_id: str,
            document_root: str,
            tree_root: str,
            indent: int,
        ) -> None:
            normalized_document_root = _normalize_uri(document_root)
            normalized_tree_root = _normalize_uri(tree_root)
            if not (
                normalized_tree_root == normalized_document_root
                or _is_descendant_uri(normalized_tree_root, normalized_document_root)
            ):
                return
            document_uri_map.setdefault(doc_id, normalized_document_root)
            rel_root = _relative_uri_path(normalized_tree_root, normalized_document_root)
            root_label = f"{rel_root.rstrip('/')}/" if rel_root else f"{_uri_leaf(document_root)}/"
            add_line(f"{'  ' * indent}- [D:{doc_id}] {root_label}")
            try:
                entries = await viking_fs.tree(
                    normalized_tree_root,
                    output="original",
                    show_all_hidden=True,
                    node_limit=None,
                    level_limit=None,
                    ctx=ctx,
                )
            except Exception as exc:
                add_line(f"{'  ' * (indent + 1)}- (unable to list: {exc})")
                return
            for line in self._format_resource_tree_entries(entries, indent + 1):
                add_line(line)

        async def add_wiki_node(node_id: str, indent: int, path: set[str]) -> None:
            node = nodes_by_id.get(node_id)
            if not node:
                return
            title = _wiki_node_title(node, node_id)
            if node_id in path:
                add_line(f"{'  ' * indent}- [N:{node_id}] {title} (cycle)")
                return
            add_line(f"{'  ' * indent}- [N:{node_id}] {title}")
            add_line(f"{'  ' * (indent + 1)}- [N:{node_id}:card] card.md")

            next_path = {*path, node_id}
            for child_node_id in _wiki_direct_child_node_ids(nodes, node_id):
                await add_wiki_node(child_node_id, indent + 1, next_path)

            for ref in document_refs_by_node.get(node_id, []):
                await add_resource_tree(
                    doc_id=str(ref.get("doc_id")),
                    document_root=str(ref.get("resource_uri")),
                    tree_root=str(ref.get("resource_uri")),
                    indent=indent + 1,
                )

        if roots["kind"] == "resource_document_child":
            document_root = str(roots["document_root"])
            doc_meta = document_roots.get(document_root)
            if doc_meta:
                await add_resource_tree(
                    doc_id=doc_meta["doc_id"],
                    document_root=document_root,
                    tree_root=str(roots["resource_tree_root"]),
                    indent=0,
                )
        else:
            for root_node_id in roots["root_node_ids"]:
                await add_wiki_node(root_node_id, 0, set())

        return {
            "kind": roots["kind"],
            "document_uri_map": document_uri_map,
            "lines": lines,
        }

    @staticmethod
    def _format_resource_tree_entries(entries: list[dict[str, Any]], indent: int) -> list[str]:
        tree: dict[str, Any] = {}
        is_dir_by_path: dict[str, bool] = {}
        for entry in entries:
            rel_path = str(entry.get("rel_path") or entry.get("name") or "").strip("/")
            if not rel_path:
                continue
            is_dir_by_path[rel_path] = bool(entry.get("isDir"))
            cursor = tree
            for part in rel_path.split("/"):
                cursor = cursor.setdefault(part, {})

        lines: list[str] = []

        def walk(node: dict[str, Any], prefix: str, level: int) -> None:
            for name in sorted(node):
                rel_path = f"{prefix}/{name}" if prefix else name
                suffix = "/" if is_dir_by_path.get(rel_path) or node[name] else ""
                lines.append(f"{'  ' * level}- {name}{suffix}")
                walk(node[name], rel_path, level + 1)

        walk(tree, "", indent)
        return lines

    async def _grep_multiple_targets(
        self,
        viking_fs: VikingFS,
        target_uris: list[str],
        pattern: str,
        *,
        ctx: RequestContext,
        exclude_uri: Optional[str],
        case_insensitive: bool,
        node_limit: Optional[int],
        level_limit: int,
    ) -> Dict:
        matches: list[Any] = []
        files_scanned = 0
        for target_uri in target_uris:
            remaining_limit = None
            if node_limit is not None and node_limit > 0:
                remaining_limit = node_limit - len(matches)
                if remaining_limit <= 0:
                    break

            result = await viking_fs.grep(
                target_uri,
                pattern,
                exclude_uri=exclude_uri,
                case_insensitive=case_insensitive,
                node_limit=remaining_limit,
                level_limit=level_limit,
                ctx=ctx,
            )
            target_matches = result.get("matches", []) if isinstance(result, dict) else []
            matches.extend(_filter_wiki_document_matches(target_matches))
            files_scanned += result.get("files_scanned", 0) if isinstance(result, dict) else 0

        if node_limit is not None and node_limit > 0:
            matches = matches[:node_limit]
        return {
            "matches": matches,
            "count": len(matches),
            "match_count": len(matches),
            "files_scanned": files_scanned,
        }

    async def glob(
        self,
        pattern: str,
        ctx: RequestContext,
        uri: str = "viking://",
        node_limit: Optional[int] = None,
    ) -> Dict:
        """File pattern matching."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.glob(pattern, uri=uri, node_limit=node_limit, ctx=ctx)

    async def read_file_bytes(self, uri: str, ctx: RequestContext) -> bytes:
        """Read file as raw bytes."""
        viking_fs = self._ensure_initialized()
        uri = validate_viking_uri(uri)
        return await viking_fs.read_file_bytes(uri, ctx=ctx)

    async def write(
        self,
        uri: str,
        content: str,
        ctx: RequestContext,
        mode: str = "replace",
        wait: bool = False,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Write to an existing file and refresh semantics/vectors."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        coordinator = ContentWriteCoordinator(viking_fs=viking_fs, vikingdb=self._vikingdb)
        return await coordinator.write(
            uri=uri,
            content=content,
            ctx=ctx,
            mode=mode,
            wait=wait,
            timeout=timeout,
        )

    async def set_tags(
        self,
        uri: str,
        tags: list[str],
        mode: str,
        recursive: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Set explicit retrieval tags for a file or directory semantic nodes."""
        uri = validate_viking_uri(uri)
        viking_fs = self._ensure_initialized()
        coordinator = ContentWriteCoordinator(viking_fs=viking_fs)
        return await coordinator.set_tags(
            uri=uri,
            tags=tags,
            mode=mode,
            recursive=recursive,
            ctx=ctx,
        )

    async def commit(
        self,
        *,
        message: str,
        ctx: RequestContext,
        paths: Optional[List[str]] = None,
        branch: str = "main",
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Forward to VikingFS.commit. See viking_fs.commit for semantics."""
        viking_fs = self._ensure_initialized()
        validated = (
            [validate_viking_uri(p) for p in paths] if paths is not None else None
        )
        return await viking_fs.commit(
            message=message,
            paths=validated,
            branch=branch,
            author_name=author_name,
            author_email=author_email,
            ctx=ctx,
        )

    async def restore(
        self,
        *,
        project_dir: Optional[str],
        source_commit: str,
        ctx: RequestContext,
        branch: str = "main",
        dry_run: bool = False,
        message: Optional[str] = None,
        author_name: Optional[str] = None,
        author_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Forward to VikingFS.restore. See viking_fs.restore for semantics."""
        viking_fs = self._ensure_initialized()
        if project_dir is not None:
            project_dir = validate_viking_uri(project_dir, field_name="project_dir")
        return await viking_fs.restore(
            project_dir=project_dir,
            source_commit=source_commit,
            branch=branch,
            dry_run=dry_run,
            message=message,
            author_name=author_name,
            author_email=author_email,
            ctx=ctx,
        )

    async def show(
        self,
        target_ref: str,
        ctx: RequestContext,
        *,
        path: Optional[str] = None,
    ) -> Any:
        """Forward to VikingFS.show. Returns dict (metadata) or bytes (blob)."""
        viking_fs = self._ensure_initialized()
        # validate_optional_viking_uri returns "" for None input; VikingFS.show needs None.
        path = validate_optional_viking_uri(path, field_name="path") or None
        return await viking_fs.show(target_ref, path=path, ctx=ctx)

    async def show_blob_raw(
        self,
        target_ref: str,
        ctx: RequestContext,
        *,
        path: str,
    ) -> Dict[str, Any]:
        """Forward to VikingFS.show_blob_raw. Returns ``{"oid", "size", "bytes"}``."""
        viking_fs = self._ensure_initialized()
        path = validate_viking_uri(path, field_name="path")
        return await viking_fs.show_blob_raw(target_ref, path=path, ctx=ctx)

    async def diff(
        self,
        *,
        path: str,
        from_ref: Optional[str],
        to_ref: str,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Return a unified text diff for one path between two snapshots."""
        viking_fs = self._ensure_initialized()
        path = validate_viking_uri(path, field_name="path")
        return await viking_fs.diff(path=path, from_ref=from_ref, to_ref=to_ref, ctx=ctx)

    async def log(
        self,
        ctx: RequestContext,
        *,
        branch: str = "main",
        limit: int = 20,
        paths: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Forward to VikingFS.log. Walks parents[0] up to limit commits."""
        viking_fs = self._ensure_initialized()
        if paths is not None:
            paths = [validate_viking_uri(path, field_name="paths") for path in paths]
            if not paths:
                paths = None
        return await viking_fs.log(branch=branch, limit=limit, paths=paths, ctx=ctx)

    async def get_gitignore(self, *, ctx: RequestContext) -> str:
        """Forward to VikingFS.get_gitignore. Returns the account .ovgitignore
        content, or an empty string if absent."""
        viking_fs = self._ensure_initialized()
        return await viking_fs.get_gitignore(ctx=ctx)

    async def set_gitignore(
        self, *, content: str, ctx: RequestContext
    ) -> None:
        """Forward to VikingFS.set_gitignore. Writes the account .ovgitignore
        control file (validates the size limit)."""
        viking_fs = self._ensure_initialized()
        await viking_fs.set_gitignore(content, ctx=ctx)

    async def delete_gitignore(self, *, ctx: RequestContext) -> None:
        """Forward to VikingFS.delete_gitignore. Removes the account
        .ovgitignore; missing is success."""
        viking_fs = self._ensure_initialized()
        await viking_fs.delete_gitignore(ctx=ctx)
