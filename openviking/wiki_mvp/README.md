# Wiki MVP

`openviking/wiki_mvp` 是可复用的 Wiki 生成核心：输入一批已经进入 OpenViking 的资源，输出一棵写在 `viking://wiki/...` 下的可浏览 Wiki。

这个目录不负责 benchmark，也不读取固定数据集。固定数据集实验放在 `benchmark/wiki`；服务侧资源入库通过 `ResourceService` 调用这里的通用能力。

## 当前契约

当前 MVP 优先保证 Wiki 正文稳定生成：

- 生成 Document Card、Wiki 节点、节点说明页和节点 Markdown 正文。
- 保留节点级来源列表，写入 `source_assignments.json` 和 `nodes/<node_id>/sources/*.ref.json`。
- 正文生成阶段暂不让模型输出 claim 级 `evidence_refs` 或父层 `support_refs`。
- 不再做“生成句子必须逐字出现在正文中”的运行时校验。

这是当前设计选择。之前的 claim/ref 方案会让模型复制长 URI 或额外临时标识符，稳定性不够，而当前消费侧还没有必须依赖 claim 级 drill down 的需求。后续如果恢复 claim 级引用，应使用短临时引用交给模型复制，再由代码映射回持久 ID。

## 模块职责

```text
openviking/wiki_mvp/
├── config.py
│   └── WikiMVPConfig 和 WikiMVPGenerationLimits。
├── schemas.py
│   └── Card、Node、SourceRef、Document、Manifest 等 Pydantic 契约。
├── uri.py
│   └── Wiki 产物 URI 生成规则。
├── writer.py
│   └── VikingFS 写入器，负责目录、JSON、JSONL 和 Markdown 写入。
├── llm.py
│   └── Wiki LLM 调用封装，记录 prompt、schema hash 和结构化输出。
├── prompts.py
│   └── 渲染 openviking/prompts/templates/wiki/ 下的模板。
├── cards.py
│   └── 为每篇资源文档生成 Document Card。
├── profile.py
│   └── 基于 cards 生成资源空间画像。
├── nodes.py
│   └── 发现底层节点和父层节点。
├── assignments.py
│   └── 把节点发现结果里的来源 ID 确定性转换成 SourceRef。
├── documents.py
│   └── 生成 node.md 和节点 Markdown 正文，并处理结构化输出重试。
├── layer_decision.py
│   └── 判断是否继续向上生成父层节点。
├── content_loader.py
│   └── 服务侧构建时，从 VikingFS/VikingDB 加载 summary 或 raw chunk。
├── client_adapter.py
│   └── 把服务侧 client 适配成 writer 需要的接口。
└── pipeline.py
    └── 总编排器，串起完整 Wiki 生成流程。
```

推荐阅读顺序：

1. `schemas.py`：先看最终数据契约。
2. `prompts.py` 和 `openviking/prompts/templates/wiki/`：看每一步给模型的输入。
3. `cards.py`、`profile.py`、`nodes.py`、`assignments.py`、`documents.py`、`layer_decision.py`：按阶段看行为。
4. `pipeline.py`：看编排、过滤和写入时机。
5. `content_loader.py`、`client_adapter.py`：看服务侧如何接入。

## 生成流程

```text
资源文档
  -> Document Cards
  -> 资源空间画像
  -> 底层节点发现
  -> SourceRef 构造
  -> node.md + 节点正文
  -> 可选父层节点发现
  -> 父层 SourceRef 构造
  -> 父层节点正文
  -> Manifest 和运行日志
```

核心边界是 Document Card。节点发现不直接读所有长文档，而是先读压缩后的 cards。节点正文生成时，再拿该节点被选中的来源文档内容或子节点正文内容做综合写作。

### 底层节点

底层节点从 Document Card 中发现。模型返回候选节点，以及每个节点由哪些 `doc_id` 支撑。代码随后：

- 规范化 `node_id`；
- 用 `max_active_nodes` 限制 active 节点数量；
- 基于已知 `DocumentCard` 构造 `SourceRef`；
- 用 `min_refs_per_node` 过滤来源不足的节点；
- 为保留下来的 active 节点写 `node.md` 和正文文档。

`assignments.py` 不调用 LLM。它只校验模型返回的来源 ID 是否存在，并用已知 card/child context 构造 SourceRef。

### 父层节点

父层节点从已生成的子节点上下文中发现，不再直接读取原始长文档。父层正文的输入是父 node 的 title/scope，以及子节点的 title/scope/document content。

父层 prompt 的重点是综合：输出应围绕父层知识点组织，而不是按照 child node 顺序逐个总结。child node 的 title/scope 只用于理解覆盖范围，不应成为输出结构。

是否继续向上由 `LayerDecisionRunner` 判断，同时受 `max_depth` 限制。父层节点还要满足 `min_child_nodes_per_parent`。

## 产物结构

假设 `wiki_root_uri` 是 `viking://wiki/my_wiki/`，管线会写出：

```text
viking://wiki/my_wiki/
├── manifest.json
├── profile.json
├── nodes.json
├── source_assignments.json
├── cards/
│   ├── <doc_id>.card.md
│   └── <doc_id>.card.json
├── nodes/
│   └── <node_id>/
│       ├── node.md
│       ├── manifest.json
│       ├── documents/
│       │   └── 0001.md
│       └── sources/
│           └── <ref_id>.ref.json
└── run/
    ├── config.json
    ├── prompts.jsonl
    ├── raw_outputs.jsonl
    └── logs.md
```

关键文件：

- `manifest.json`：整个 Wiki 的顶层索引。
- `profile.json`：资源空间画像。
- `nodes.json`：所有发现节点，包括 rejected 节点。
- `source_assignments.json`：节点级来源引用和未分配文档。
- `cards/*.card.json`：后续阶段使用的结构化 card。
- `cards/*.card.md`：人类可读 card。
- `nodes/<node_id>/node.md`：节点说明和边界。
- `nodes/<node_id>/documents/*.md`：综合生成的节点正文。
- `nodes/<node_id>/sources/*.ref.json`：该节点可用的来源列表。
- `run/prompts.jsonl`：每次 LLM 调用的 prompt、schema name 和 schema hash。
- `run/raw_outputs.jsonl`：模型返回并成功解析后的结构化输出。

`document_id` 由代码按输出顺序补齐，例如 `0001`。它只保证同一次 run 内唯一，不承诺跨 run 稳定。

## LLM 行为

所有 Wiki LLM 调用都经过 `WikiLLMRunner.complete_json(...)`，再由 `StructuredVLM` 使用 JSON Schema response format 调模型。Prompt 模板放在 `openviking/prompts/templates/wiki/`。

当前会调用 LLM 的阶段：

- `document_card`
- `resource_profile`
- `bottom_node_discovery`
- `parent_node_discovery`
- `node_md`
- `node_documents`
- `parent_node_documents`
- `next_layer_decision`

文档生成阶段带结构化输出重试：如果模型返回的 JSON 外层可解析，但字段不符合 Pydantic 契约或生成了空 documents，会用同一个干净 prompt 最多重试 3 次。重试 prompt 不追加 Pydantic 错误细节，避免污染模型注意力。

Prompt 边界是产品契约的一部分：

- 不能使用 benchmark question、gold answer、评测标签或目标答案。
- 不能使用外部知识。
- 每一步只能使用该阶段明确提供的资源、card 或子节点正文内容。
- 父层正文必须综合多个子节点文档，不要按 child node 一段一段总结。

## 服务侧接入

服务入口是：

```text
ResourceService._run_add_resource_wiki_pipeline(...)
```

它主要做五件事：

1. 接收资源入库阶段产生的 `WikiResourceInput`。
2. 创建 `WikiServiceClientAdapter`。
3. 创建 `WikiContentLoader`。
4. 配置 Wiki 根目录，目前服务内固定为 `viking://wiki/`。
5. 调用 `WikiMVPPipeline.run_from_inputs(...)`。

`WikiContentLoader` 支持两种 card 输入模式：

| 模式 | 行为 | 适用场景 |
| --- | --- | --- |
| `summary` | 读取语义摘要、overview 和 chunk abstract | 语义生成已完成且质量可用 |
| `raw_chunk` | 直接读取原始 chunk 内容 | 没有摘要，或需要绕过摘要质量问题 |

如果 `summary` 模式读不到可用摘要，构建会提前失败。此时要么先完成语义生成，要么切到 `raw_chunk`。

## 常用配置

生成规模主要由 `WikiMVPGenerationLimits` 控制：

| 参数 | 含义 | 默认值 |
| --- | --- | --- |
| `max_depth` | 最多生成几层 Wiki 节点 | `2` |
| `max_active_nodes` | 每层最多保留多少个 active 节点 | `20` |
| `min_refs_per_node` | 底层节点最少需要多少个文档来源 | `3` |
| `min_child_nodes_per_parent` | 父节点最少需要多少个子节点 | `3` |
| `max_concurrent_cards` | Document Card 并发生成数 | `10` |
| `max_concurrent_nodes` | 节点正文并发生成数 | `4` |

有些配置字段仍是扩展预留。判断真实行为时，以 `pipeline.py` 中的过滤和编排逻辑为准。

## 排查问题

如果 Wiki 没启动：

- 确认调用方传了 `build_wiki=True`。
- 看服务日志是否出现 `_run_add_resource_wiki_pipeline(...)`。
- 看返回摘要里是否有 `wiki_root_uri`、card 数和 node 数。

如果没有生成节点正文：

1. 看 `nodes.json`，确认节点是 `active` 还是 `rejected`。
2. 看 `source_assignments.json`，确认来源数量是否足够。
3. 检查 `min_refs_per_node` 或 `min_child_nodes_per_parent` 是否过严。
4. 看 `run/prompts.jsonl`，确认该阶段真实输入。
5. 看 `run/raw_outputs.jsonl`，确认模型结构化输出。

如果是模型输出不稳定：

- 先看对应阶段是否已经有重试。
- 对 LLM 导致的格式问题，优先用同一个干净 prompt 重试。
- 不要默认把 Pydantic 错误或实现细节拼进重试 prompt，除非有明确产品理由。

核心单测：

```bash
uv run pytest tests/wiki_mvp -q
```

benchmark 全流程在包外执行：

```bash
rm -rf benchmark/wiki/wiki_storage/qasper_30 benchmark/wiki/Output/qasper_30/wiki
uv run python benchmark/wiki/run.py --config benchmark/wiki/config/qasper_30.yaml
```

重跑完整 benchmark 前先删除旧产物，避免把上一次失败或中断留下的 Wiki 文件混进判断。

## 接入新输入

调用方已经有文档正文时，用 `ResourceDocument`。服务侧需要从 VikingFS/VikingDB 加载内容时，用 `WikiResourceInput`。

新的输入来源必须提供：

- 稳定的 `doc_id`；
- 真实的 `resource_uri`；
- 非空 `title`；
- loader 能读取到的 summary、abstract 或 chunk 内容；
- 不包含 benchmark gold answer、评测标签或只应评测侧可见的目标信息。

固定数据集 adapter 应放在 `benchmark/wiki`，不要放进 `openviking/wiki_mvp`。

## 当前限制和后续方向

当前限制：

- 只支持全量重建，不支持读取旧 Wiki 后增量合并。
- 服务内构建目前固定写入 `viking://wiki/`。
- claim 级 citation 暂时关闭。
- 中途失败可能留下部分已写产物。
- 模型后端必须支持结构化 JSON response format。

后续方向：

- 引入发布/提交语义，避免失败 run 暴露半成品 Wiki。
- 在服务/API 层支持显式 `wiki_id` 或可配置 Wiki root。
- 做增量更新：复用已有 cards，只重算受影响节点。
- 如果消费侧需要，再恢复 claim 级引用，并用短临时 ref + 代码映射实现。
- 考虑 agent 化编排，让生成流程具备计划、检查和局部修复能力，同时继续以 `schemas.py` 作为最终落盘契约。
