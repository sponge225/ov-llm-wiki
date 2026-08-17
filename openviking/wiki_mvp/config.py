"""Wiki MVP 生成管线配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WikiMVPGenerationLimits:
    # 每层最多保留多少个可生成内容的 Wiki 节点。
    max_active_nodes: int = 20
    # 最多向上聚合多少层 Wiki 节点。
    max_depth: int = 3
    # 父节点至少要覆盖多少个子节点，否则不会保留。
    min_child_nodes_per_parent: int = 3
    # 底层节点至少要绑定多少个来源引用，否则会被拒绝。
    min_refs_per_node: int = 3
    # 同时发起多少个文档卡片生成请求。
    max_concurrent_cards: int = 10
    # 同时发起多少个节点内容生成请求。
    max_concurrent_nodes: int = 10


@dataclass
class WikiMVPConfig:
    # 写入产物中的管线版本标识。
    pipeline_version: str = "wiki_mvp_v2_doc_card"
    # 来源资源所在的根 URI，用来校验和记录引用来源。
    resource_root_uri: str = "viking://resources/"
    # Wiki 产物写入的根 URI。
    wiki_root_uri: str = "viking://wiki/"
    # 控制节点数量、层数、过滤阈值和并发量。
    limits: WikiMVPGenerationLimits = field(default_factory=WikiMVPGenerationLimits)
    # 传给底层 VLM/LLM 的模型配置。
    vlm_config: dict[str, Any] | None = None
