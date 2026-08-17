# Wiki MVP

`openviking/wiki_mvp` 负责一件事：把一批已经进入 OpenViking 的资源，整理成一个可浏览、可追溯来源的 Wiki。

它不是 benchmark 脚本，也不直接读取某个固定数据集。固定数据集实验应该放在 `benchmark/wiki`。这个目录只保留 Wiki 生成的核心实现，供服务侧和其他调用方复用。

## 代码概览树

先从依赖关系看职责。这个目录里的代码可以分成五类：配置与数据模型、基础工具、LLM 生成步骤、服务适配、总编排。

```text
openviking/wiki_mvp/
├── __init__.py
│   └── 包导出入口，暴露 WikiMVPConfig、WikiMVPGenerationLimits、WikiMVPPipeline。
│
├── config.py
│   └── 配置定义。包含 WikiMVPConfig 和 WikiMVPGenerationLimits，控制 wiki 根 URI、层数、节点数、过滤阈值和并发数。
│
├── schemas.py
│   └── 数据模型。定义 ResourceDocument、WikiResourceInput、DocumentCard、WikiNode、SourceRef、PipelineArtifacts 等结构。
│
├── uri.py
│   └── URI 工具。统一生成 manifest、cards、nodes、sources、run 日志等产物 URI。
│
├── writer.py
│   └── VikingFS 写入器。负责创建目录并写入 Markdown、JSON、JSONL 产物。
│
├── llm.py
│   └── LLM 调用封装。负责 response_format 结构化调用、prompt/output 记录、schema hash 记录。
│
├── prompts.py
│   └── Wiki prompt 模板渲染薄封装。负责组织输入变量并渲染 openviking/prompts/templates/wiki/ 下的模板。
│
├── cards.py
│   └── Step 1：生成 Document Card。把每篇资源文档压缩成结构化摘要卡片，供后续主题发现使用。
│
├── profile.py
│   └── Step 2：生成资源空间画像。总结这批资源整体是什么主题空间。
│
├── nodes.py
│   └── Step 3：发现 Wiki 节点。让 LLM 提出当前层候选节点，并做 node_id 规范化和 active 节点数量控制。
│
├── assignments.py
│   └── Step 4：分配来源。判断每个 active 节点由哪些原始文档或子节点支撑。
│
├── documents.py
│   └── Step 5/6：生成节点内容。生成 node.md 和节点正文文档。
│
├── layer_decision.py
│   └── Step 7：判断当前层是否继续向上聚合父节点。
│
├── content_loader.py
│   └── 服务内输入加载器。根据 WikiResourceInput 从 VikingFS/VikingDB 读取摘要或 raw chunk，生成卡片输入。
│
├── client_adapter.py
│   └── 服务侧 client 适配器。把 VikingFS/VikingDB 包装成 writer 需要的 mkdir/write 接口。
│
└── pipeline.py
    └── 总编排器。把上面这些模块按生成流程串起来；它依赖其他模块，不是底层实现。
```

推荐从底层到上层读：

1. `config.py`：先知道有哪些配置和生成限制。
2. `schemas.py`：再看所有阶段共用的数据结构。
3. `uri.py`、`writer.py`：看产物 URI 怎么生成、怎么写入 VikingFS。
4. `llm.py`、`prompts.py`、`openviking/prompts/templates/wiki/`：看 LLM 调用、模板渲染和 prompt 正文。
5. `cards.py -> profile.py -> nodes.py -> assignments.py -> documents.py -> layer_decision.py`：按生成阶段看每一步。
6. `content_loader.py`、`client_adapter.py`：看服务内如何加载资源、适配写入接口。
7. `pipeline.py`：最后看总编排，确认前面这些模块如何被串起来。

## 一句话流程

可以把整个流程理解成：

```text
资源文档
  -> 每篇文档压缩成 Document Card
  -> 总结整批资源的主题画像
  -> 发现 Wiki 节点
  -> 给节点分配来源
  -> 写节点说明和正文文档
  -> 必要时继续向上聚合父节点
```

这里最关键的设计是先生成 `Document Card`。直接让 LLM 在所有长文档上做主题发现会很不稳定；先把每篇文档压缩成结构化卡片，后续节点发现、来源分配、正文生成都会更可控。

## 产物结构

假设 `wiki_root_uri` 是 `viking://wiki/my_wiki/`，管线会写出如下结构：

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
│           └── <doc_id>.ref.json
└── run/
    ├── config.json
    ├── prompts.jsonl
    ├── raw_outputs.jsonl
    └── logs.md
```

几个关键文件的作用：

- `manifest.json`：整个 Wiki 的索引，记录版本、根 URI、卡片目录和节点 URI。
- `profile.json`：资源空间画像，描述这批资源整体在讲什么。
- `nodes.json`：所有候选节点和保留节点的结构化列表。
- `source_assignments.json`：按节点记录可引用来源，核心字段是 `source_refs_by_node`。
- `cards/*.card.md`：单篇文档的人类可读摘要卡片。
- `cards/*.card.json`：卡片的结构化版本，供后续步骤使用。
- `nodes/<node_id>/node.md`：节点目录说明，解释节点范围和边界。
- `nodes/<node_id>/documents/*.md`：节点正文，是对多个来源的综合写作结果。
- `run/prompts.jsonl` 和 `run/raw_outputs.jsonl`：每次 LLM 调用的 prompt 和原始输出，便于复盘。

## 核心流程

主编排类是 `WikiMVPPipeline`。它不自己实现所有细节，而是把几个小模块串起来：

| 阶段 | 做什么 | 主要文件 |
| --- | --- | --- |
| 加载输入 | 把资源变成可喂给 LLM 的文档输入 | `content_loader.py`, `schemas.py` |
| 生成卡片 | 每篇文档生成一张结构化摘要卡片 | `cards.py` |
| 生成画像 | 总结这批资源整体在讲什么 | `profile.py` |
| 发现节点 | 让 LLM 提出 Wiki 主题节点 | `nodes.py` |
| 分配来源 | 判断每个节点由哪些文档或子节点支撑 | `assignments.py` |
| 写节点内容 | 生成节点说明页和正文文档 | `documents.py` |
| 判断是否向上聚合 | 决定是否继续生成父层节点 | `layer_decision.py` |
| 写入产物 | 把 JSON、Markdown、日志写入 VikingFS | `writer.py`, `uri.py` |

### 输入有两种

`ResourceDocument` 适合调用方已经准备好正文的情况。它至少要有稳定的 `doc_id`、`resource_uri`、`title` 和正文内容。

`WikiResourceInput` 适合服务内构建。资源入库阶段只把资源 URI、文档目录等信息传进来，真正的摘要或 chunk 内容由 `WikiContentLoader` 再去 VikingFS/VikingDB 读取。

### 节点不是发现了就会写入

LLM 会先提出候选节点，但只有 `active` 节点会进入写作阶段。管线会做几层保护：

- 节点 ID 会被规范化，避免生成非法路径。
- 每层 active 节点数量受 `max_active_nodes` 限制。
- 底层节点必须有足够来源引用，默认至少 `min_refs_per_node=3`。
- 父层节点必须覆盖足够多的子节点，默认至少 `min_child_nodes_per_parent=3`。

不满足条件的节点会被标成 `rejected`。它们仍会出现在 `nodes.json`，这样后续能看出模型提过哪些节点、为什么没有进入最终 Wiki。

### 父节点怎么生成

第 1 层节点从 `Document Card` 中发现。第 2 层及以上的父节点，不再直接看原始长文档，而是看上一层已经生成的 `GeneratedNodeContext`。

这样做的好处是父节点基于已经验证过的子节点继续聚合，不容易直接跳到很空泛的大主题。是否继续向上聚合由 `LayerDecisionRunner.should_continue_upward(...)` 判断，同时受 `max_depth` 限制。

父节点正文生成时，prompt 输入只包含父 node 的 `title/scope` 和子 node 文档内容，不再要求模型输出 claim、support ref 或 evidence ref。

### 当前引用策略

当前正文生成阶段暂不产出 claim 级引用，只生成 Markdown 文档内容。节点与来源之间的关系仍由 `source_assignments.json` 和每个节点目录下的 `sources/*.ref.json` 保留。

`document_id` 由代码按本次输出顺序补齐，例如 `0001`。这些 ID 是 run-local ordinal identifiers，只保证同一次生成产物内唯一，不承诺跨 run 稳定。

## 常用配置

配置入口是 `WikiMVPConfig`，生成规模主要由 `WikiMVPGenerationLimits` 控制。

最常调的参数是这些：

| 参数 | 作用 | 默认值 |
| --- | --- | --- |
| `max_depth` | 最多生成几层 Wiki 节点 | `2` |
| `max_active_nodes` | 每层最多保留多少个 active 节点 | `20` |
| `min_refs_per_node` | 底层节点至少需要多少个来源引用 | `3` |
| `min_child_nodes_per_parent` | 父节点至少要覆盖多少个子节点 | `3` |
| `max_concurrent_cards` | 并发生成 Document Card 的数量 | `10` |
| `max_concurrent_nodes` | 并发生成节点内容的数量 | `4` |

还有一些字段目前更多是设计预留，不要默认认为它们已经在所有阶段生效。看真实行为时，以 `pipeline.py` 中的过滤逻辑为准。

## LLM 和日志

所有结构化 LLM 调用都经过 `WikiLLMRunner.complete_json(...)`。它会把业务 prompt 和 JSON Schema 传给 `StructuredVLM`，由底层模型接口通过 `response_format` 强制结构化输出。

```text
viking://wiki/<wiki_root>/run/prompts.jsonl
viking://wiki/<wiki_root>/run/raw_outputs.jsonl
```

排查生成质量时，这两个文件最有用：

- `prompts.jsonl` 看输入是否正确，是否混入了不该看的内容；其中会记录 schema name/hash，prompt 本身不再拼完整 schema。
- `raw_outputs.jsonl` 看模型是否返回了可解析、字段完整的 JSON。

Wiki prompt 正文放在 `openviking/prompts/templates/wiki/`。这些模板里有明确的数据边界约束：只能使用提供的资源和已经生成的 Wiki 资产，不能使用 question、gold answer、gold related work 或目标论文结构。这个约束是为了避免 benchmark 场景里的数据泄漏。

## 服务内构建

服务内入口在 `ResourceService._run_add_resource_wiki_pipeline(...)`。它主要做五件事：

1. 接收资源入库阶段产生的 `WikiResourceInput`。
2. 创建 `WikiServiceClientAdapter`，让 writer 能写 VikingFS。
3. 创建 `WikiContentLoader`，从资源目录读取摘要或原始 chunk。
4. 使用固定的 `viking://wiki/` 作为 Wiki 产物根目录。
5. 创建 `WikiMVPPipeline` 并调用 `run_from_inputs(...)`。

`WikiContentLoader` 有两种输入模式：

| 模式 | 行为 | 适用情况 |
| --- | --- | --- |
| `summary` | 优先读取语义摘要、overview 和 chunk abstract | 资源已经完成语义生成 |
| `raw_chunk` | 直接读取原始 chunk 内容 | 没有摘要，或想绕过摘要质量问题 |

如果使用 `summary`，但资源目录里没有可用摘要，构建会报错。这时要么先补语义生成，要么改用 `raw_chunk`。

## 排查问题

先确认入口有没有被触发：

- `add_resource(..., build_wiki=True)` 是否真的传了 `build_wiki=True`。
- 返回 telemetry 或服务日志里是否出现 `_run_add_resource_wiki_pipeline(...)`。
- 返回摘要里是否有 `wiki_root_uri`、卡片数和节点数。

如果没有生成节点目录，按这个顺序查：

1. 看 `nodes.json`，节点是不是 `active`。
2. 看 `source_assignments.json`，节点是否有足够的 `source_refs`。
3. 看节点是否被 `_reject_nodes_with_insufficient_refs` 改成了 `rejected`。
4. 检查 `max_active_nodes`、`min_refs_per_node`、`min_child_nodes_per_parent` 是否太严格。
5. 看 `run/raw_outputs.jsonl`，确认模型是否输出了预期字段。

如果 prompt 或模型输出有问题，优先看：

- `run/prompts.jsonl`
- `run/raw_outputs.jsonl`
- `run/logs.md`

跑核心单测：

```bash
uv run pytest tests/wiki_mvp -q
```

## 接入新输入源

如果要接新数据源，先判断它属于哪一类：

| 类型 | 放在哪里 | 做什么 |
| --- | --- | --- |
| 服务内资源 | OpenViking 入库链路 | 生成 `WikiResourceInput`，走 `run_from_inputs(...)` |
| benchmark 或固定数据集实验 | `benchmark/wiki` | 写 adapter 或脚本，把数据转成 `ResourceDocument` |

不要把固定数据集读取逻辑放到 `openviking/wiki_mvp`。这个目录只放可复用的 Wiki 生成能力。

无论哪种输入，都要保证：

- `doc_id` 稳定，能作为文件名的一部分。
- `resource_uri` 指向真实来源资源。
- `title` 非空。
- 输入里不能混入 gold answer、目标答案或 benchmark 评测标签。

## 当前限制

- 强依赖模型服务支持 JSON Schema `response_format`；如果后端不支持，会直接失败。
- 部分配置字段是 MVP 阶段的设计预留，不是所有字段都已经完整生效。
- `document_id` 是本次运行内的顺序编号，不适合作为跨版本稳定主键。

## 后续优化方向

### 相关功能入口

后续改 Wiki 构建逻辑时，优先从这些入口看：

| 入口 | 文件 | 作用 |
| --- | --- | --- |
| 资源入库触发 Wiki 构建 | `openviking/service/resource_service.py` | `_run_add_resource_wiki_pipeline(...)` 负责创建 `WikiMVPConfig`、`WikiContentLoader` 和 `WikiMVPPipeline`。 |
| 解析阶段收集 Wiki 输入 | `openviking/parse/base.py`、`openviking/parse/parsers/directory.py`、`openviking/parse/parsers/markdown.py` | 生成 `ResourceDocumentDraft`，也就是后续要生成 Document Card 的文档条目。 |
| 入库阶段补齐资源 URI | `openviking/utils/resource_processor.py` | 把 `ResourceDocumentDraft` 转成带正式 `resource_uri` 的 `WikiResourceInput`。 |
| Wiki 主流程 | `openviking/wiki_mvp/pipeline.py` | 串起 card、profile、node、source ref、node content、layer decision 和写入。 |
| Wiki Prompt 模板 | `openviking/prompts/templates/wiki/`、`openviking/wiki_mvp/prompts.py` | 模板目录维护 prompt 正文，`prompts.py` 只负责组织输入并渲染模板。 |
| Wiki 结构化输出 | `openviking/wiki_mvp/llm.py`、`openviking/models/vlm/llm.py` | Wiki 调用侧记录 prompt/schema 日志，通用 VLM 层通过 `response_format` 约束模型输出。 |
| Wiki 内容加载 | `openviking/wiki_mvp/content_loader.py` | 根据 `WikiResourceInput` 从 VikingFS/VikingDB 读取摘要或原始 chunk。 |
| Wiki 产物路径 | `openviking/wiki_mvp/uri.py` | 定义 `cards/`、`nodes/`、`run/` 等产物路径规则。 |
| Wiki 写入 | `openviking/wiki_mvp/writer.py` | 把 pipeline 生成的 JSON、JSONL、Markdown 写入 VikingFS。 |
| Wiki 读取/展示侧 | `openviking/service/fs_service.py` | 读取 `viking://wiki/` 下的节点和辅助文件，后续改产物结构时需要同步检查。 |

如果做增量更新，重点入口是 `resource_service.py -> pipeline.py -> writer.py`。如果做多 Wiki 管理，重点入口是 `resource_service.py -> config.py -> uri.py -> fs_service.py`。

### 增量更新

当前 Wiki 构建仍然是“按本次输入重建一批产物”。即使服务入口已经固定写到 `viking://wiki/`，分批导入时也只是避免了 `wiki/` 下出现多个资源目录；它还没有解决“多批资源合并成同一棵 Wiki”的问题。

例如先导入 A 批文档，再导入 B 批文档，当前 pipeline 会基于 B 批输入重新生成 `nodes.json`、`manifest.json` 和相关节点产物，而不是读取已有 Wiki 后做增量合并。

后续需要补一套增量逻辑：

1. 读取已有 `cards/`、`nodes.json` 和 `source_assignments.json`。
2. 只为新增或变化的资源生成 Document Card。
3. 重新评估受影响的节点，而不是整棵 Wiki 全量重建。
4. 合并新旧 `SourceRef`，保留仍然有效的来源引用。
5. 对被修改、合并或删除的节点写清楚变更记录，方便回溯。

### 来源引用优化

当前正文生成阶段暂时不做 claim 级引用，避免模型在长 URI、临时 ref 或结构化引用上引入额外不稳定性。后续如果需要恢复引用链路，可以重点评估这些方向：

1. 先确认消费侧是否真的需要 claim 级 drill down，而不是只需要节点级来源列表。
2. 如果恢复底层 evidence，优先使用短临时引用，不让模型复制长 URI。
3. 如果恢复父层 support，明确消费逻辑后再引入字段，避免产生无人使用的复杂结构。
4. 评估 `document_id` 是否需要从 run-local 顺序编号演进为更稳定的内容派生 ID 或匹配机制。

当前原则是先保持输出结构简洁，等消费侧和 benchmark 暴露出具体问题后，再针对性扩展引用模型。

### Agent 化生成流程

当前模型交互主要由 `WikiLLMRunner.complete_json(...)` 统一封装。各阶段代码先渲染 prompt 模板，再循环调用模型拿结构化 JSON，例如生成 Document Card、发现节点、分配来源、生成节点正文和 claim 引用。

`LLMCallRecord` 和 `LLMOutputRecord` 只是调用日志，分别记录发给模型的 prompt 和模型返回的 raw output。它们不是决策器，也不会管理多轮任务状态。

这种实现简单直接，但有几个限制：

1. 每个阶段主要依赖单次结构化返回，缺少“计划、执行、检查、修正”的闭环。
2. 模型输出不理想时，当前更多是报错或过滤，而不是自动追问、补证据、重试局部步骤。
3. 节点发现、来源分配、正文生成和引用绑定之间的信息流是固定管线，不容易根据中间结果动态调整。

后续可以考虑把 Wiki 生成从“固定步骤调用 LLM”升级为 agent 编排：

1. 由 agent 先制定 Wiki 构建计划，例如主题层级、需要检查的来源、需要补充的证据。
2. 给 agent 提供工具接口，例如读取已有 cards、查询节点来源、检查证据覆盖、写入候选节点。
3. 让 agent 对模型输出做自检和局部修复，而不是整步失败后直接中断。
4. 保留当前 `schemas.py` 的结构化产物和 `response_format` 约束作为最终落盘合同，避免 agent 输出变成不可控自由文本。
5. 继续记录 prompt、schema hash、tool call、raw output 和修正过程，方便调试和复现。

相关入口：

- `openviking/wiki_mvp/llm.py`：当前结构化模型调用入口。
- `openviking/wiki_mvp/prompts.py`：当前每一步的 prompt 渲染入口。
- `openviking/prompts/templates/wiki/`：Wiki prompt 正文。
- `openviking/wiki_mvp/pipeline.py`：当前固定流程编排位置，未来 agent 编排大概率从这里接入。
- `openviking/wiki_mvp/schemas.py`：最终仍应复用的数据结构和落盘合同。

### Wiki 根目录与多 Wiki 管理

当前服务内构建统一写到 `viking://wiki/`。这适合单资源库场景，但如果未来同一个 OpenViking 实例里要维护多套 Wiki，需要在服务/API 层显式引入 `wiki_id` 或 `wiki_root_uri`，而不是再从导入资源目录隐式推导路径。

### 失败恢复

当前写入是边生成边落盘。如果中途 LLM 调用失败或写入失败，可能留下部分产物。后续可以考虑引入临时运行目录，成功后再发布到正式 Wiki 根目录，或者在 `manifest.json` 里记录运行状态。
