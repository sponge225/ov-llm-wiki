"""Pydantic schemas used by the Wiki pipeline."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

NODE_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _require_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    return value.strip()


def _require_node_id(value: str) -> str:
    value = _require_text(value)
    if not NODE_ID_RE.match(value):
        raise ValueError("node_id must contain only lowercase letters, digits, and underscores")
    return value


def _require_resource_uri(value: str) -> str:
    value = _require_text(value)
    if not value.startswith("viking://resources/"):
        raise ValueError("resource_uri must start with viking://resources/")
    return value


def _require_optional_resource_uri(value: str) -> str:
    if not value:
        return value
    return _require_resource_uri(value)


def _require_source_uri(value: str) -> str:
    value = _require_text(value)
    if not (value.startswith("viking://resources/") or value.startswith("viking://wiki/")):
        raise ValueError("source uri must start with viking://resources/ or viking://wiki/")
    return value


NonEmptyStr = Annotated[str, AfterValidator(_require_text)]
NodeId = Annotated[str, AfterValidator(_require_node_id)]
ResourceUri = Annotated[str, AfterValidator(_require_resource_uri)]
OptionalResourceUri = Annotated[str, AfterValidator(_require_optional_resource_uri)]
SourceUri = Annotated[str, AfterValidator(_require_source_uri)]
NonEmptyStrList = Annotated[list[str], Field(min_length=1)]


class StrictModel(BaseModel):
    """所有 Wiki 数据模型的基类，禁止接收未声明字段。"""
    model_config = ConfigDict(extra="forbid")


class ResourceDocumentDraft(StrictModel):
    """解析器列出的文档边界条目。"""
    doc_id: NonEmptyStr
    title: NonEmptyStr
    relative_uri: str = ""


class WikiResourceInput(StrictModel):
    """入库后传给 Wiki pipeline 的文档入口记录，用来定位资源并加载内容生成 Document Card。"""
    doc_id: NonEmptyStr
    resource_uri: ResourceUri
    title: NonEmptyStr
    document_dir_uri: OptionalResourceUri = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceDocument(StrictModel):
    """已加载好内容的资源文档，是生成 Document Card 时传给 LLM 的输入。"""
    doc_id: NonEmptyStr
    resource_uri: ResourceUri
    title: NonEmptyStr
    content_or_structure: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentCardContent(StrictModel):
    """LLM 为单篇文档提炼的语义卡片内容，不包含系统已知的文档标识字段。"""
    summary: NonEmptyStr
    main_points: NonEmptyStrList
    important_terms: list[str] = Field(default_factory=list)
    candidate_topics: NonEmptyStrList


class DocumentCard(DocumentCardContent):
    """单篇资源文档的结构化卡片，是后续画像、节点发现和来源分配的基础输入。"""
    doc_id: NonEmptyStr
    resource_uri: ResourceUri
    title: NonEmptyStr
    markdown: str = ""


class WikiNode(StrictModel):
    """Wiki 目录树中的内部节点，保存稳定标识、主题边界和层级关系。"""
    node_id: NodeId
    title: NonEmptyStr
    status: Literal["active", "rejected"] = "active"
    depth: int = Field(ge=1)
    scope: NonEmptyStr
    parent_node_id: str | None = None
    child_node_ids: list[str] = Field(default_factory=list)


class WikiNodeDiscoveryItem(StrictModel):
    """模型发现的一个 Wiki 主题，只描述名称和知识边界。"""
    title: NonEmptyStr = Field(description="面向读者的 Wiki 节点名称")
    scope: NonEmptyStr = Field(description="节点覆盖的知识范围及明确排除的内容")


class WikiBottomNodeDiscoveryItem(WikiNodeDiscoveryItem):
    """底层节点聚合结果，同时给出支撑该节点的文档。"""
    supporting_doc_ids: NonEmptyStrList
    merged_candidate_topics: list[str] = Field(default_factory=list)


class WikiBottomNodeDiscoveryResponse(StrictModel):
    """底层节点聚合步骤的结构化响应，包含节点和文档归属关系。"""
    nodes: list[WikiBottomNodeDiscoveryItem]
    unassigned_doc_ids: list[str] = Field(default_factory=list)


class WikiParentNodeDiscoveryItem(WikiNodeDiscoveryItem):
    """父层节点聚合结果，同时给出支撑该父节点的子节点标题。"""
    supporting_child_titles: NonEmptyStrList
    merged_child_topics: list[str] = Field(default_factory=list)


class WikiParentNodeDiscoveryResponse(StrictModel):
    """父层节点聚合步骤的结构化响应，包含父节点和子节点归属关系。"""
    nodes: list[WikiParentNodeDiscoveryItem]
    unassigned_child_titles: list[str] = Field(default_factory=list)


class SourceRef(StrictModel):
    """节点写正文时可使用的来源，可能是原始文档，也可能是子 Wiki 节点。"""
    ref_id: NonEmptyStr
    ref_type: Literal["document", "wiki_node"] = "document"
    doc_id: NonEmptyStr
    resource_uri: SourceUri
    card_uri: NonEmptyStr
    title: NonEmptyStr
    support_scope: NonEmptyStr
    matched_topics: list[str] = Field(default_factory=list)


class SourceAssignmentResult(StrictModel):
    """来源分配阶段的完整结果，按节点组织可引用来源并记录未分配来源。"""
    source_refs_by_node: dict[str, list[SourceRef]]
    child_node_ids_by_node: dict[str, list[str]] = Field(default_factory=dict)
    unassigned_source_ids: list[str] = Field(default_factory=list)


class SourceAssignmentItem(StrictModel):
    """模型返回的一条来源分配，把一个 Wiki 节点绑定到一组下层来源 ID。"""
    node_id: NodeId
    source_ids: NonEmptyStrList
    support_scope: NonEmptyStr


class SourceAssignmentResponse(StrictModel):
    """来源分配步骤的结构化响应，保留模型返回的节点到来源 ID 的绑定。"""
    assignments: list[SourceAssignmentItem]
    unassigned_source_ids: list[str] = Field(default_factory=list)


class NodeDocumentContent(StrictModel):
    """LLM 生成的节点正文内容，不包含代码侧确定的文档 ID。"""
    title: NonEmptyStr = "High-Level Knowledge"
    content: NonEmptyStr


class NodeDocument(NodeDocumentContent):
    """节点目录下生成的 Markdown 文档内容，最终会写入 documents/*.md。"""
    document_id: NonEmptyStr


class NodeMarkdownResponse(StrictModel):
    """node.md 生成步骤的结构化响应。"""
    node_md: NonEmptyStr


class NodeDocumentsResponse(StrictModel):
    """节点正文生成步骤的结构化响应。"""
    documents: list[NodeDocumentContent]


class NextLayerDecisionResponse(StrictModel):
    """向上聚合决策步骤的结构化响应。"""
    continue_upward: bool
    reasons: list[str] = Field(default_factory=list)


class GeneratedNodeContext(StrictModel):
    """单个节点生成完成后的内存上下文，汇总节点说明、正文文档和来源。"""
    node: WikiNode
    node_md: str
    documents: list[NodeDocument]
    source_refs: list[SourceRef]


class PipelineArtifacts(StrictModel):
    """Wiki pipeline 一次运行的内存产物集合，用于串联各阶段输出。"""
    cards: list[DocumentCard] = Field(default_factory=list)
    nodes: list[WikiNode] = Field(default_factory=list)
    source_refs_by_node: dict[str, list[SourceRef]] = Field(default_factory=dict)
    node_contexts: list[GeneratedNodeContext] = Field(default_factory=list)
