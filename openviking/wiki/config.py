"""Wiki 生成管线配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WikiGenerationLimits:
    # 最多向上聚合多少层 Wiki 节点。
    max_depth: int = 6
    # 父节点至少要覆盖多少个子节点，否则不会保留。
    min_child_nodes_per_parent: int = 3
    # 底层节点至少要绑定多少个来源引用，否则会被拒绝。
    min_refs_per_node: int = 3
    # 同时发起多少个文档卡片生成请求。
    max_concurrent_cards: int = 10
    # 同时发起多少个节点内容生成请求。
    max_concurrent_nodes: int = 10
    # 仅原始文档来源超过该 token 数时启用 scope-guided 分阶段生成。
    large_node_source_token_threshold: int = 90000
    # 单次节点正文模型调用允许的最大输入 token 数。
    max_node_prompt_tokens: int = 96000
    # 最终节点正文允许的最大 token 数。
    max_node_document_tokens: int = 16000
    # 每篇原始来源最多保留的 scope 检索结果数。
    node_source_retrieval_limit: int = 20
    # scope 检索最低分数；None 表示只应用 top-k。
    node_source_score_threshold: float | None = None
    # 单个节点内并行检索多少篇原始来源。
    node_source_retrieval_concurrency: int = 8


@dataclass
class WikiConfig:
    # 写入产物中的管线版本标识。
    pipeline_version: str = "wiki_v2_doc_card"
    # 来源资源所在的根 URI，用来校验和记录引用来源。
    resource_root_uri: str = "viking://resources/"
    # Wiki 产物写入的根 URI。
    wiki_root_uri: str = "viking://wiki/"
    # 控制节点数量、层数、过滤阈值和并发量。
    limits: WikiGenerationLimits = field(default_factory=WikiGenerationLimits)
    # 传给底层 VLM/LLM 的模型配置。
    vlm_config: dict[str, Any] | None = None
