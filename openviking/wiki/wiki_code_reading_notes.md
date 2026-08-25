# Wiki 代码阅读笔记

本文档按实际阅读顺序记录 `openviking/wiki` 相关代码的逐文件、逐块说明。目标是减少读代码时的来回跳转：先建立数据契约和底层工具概念，再读阶段实现、总编排和服务入口。

## 阅读顺序

1. `openviking/wiki/schemas.py`
2. `openviking/wiki/config.py`
3. `openviking/wiki/uri.py`
4. `openviking/wiki/writer.py`
5. `openviking/wiki/llm.py`
6. `openviking/wiki/prompts.py`
7. `openviking/wiki/document_manifest.py`
8. `openviking/wiki/content_loader.py`
9. `openviking/wiki/cards.py`
10. `openviking/wiki/nodes.py`
11. `openviking/wiki/assignments.py`
12. `openviking/wiki/documents.py`
13. `openviking/wiki/layer_decision.py`
14. `openviking/wiki/pipeline.py`
15. `openviking/wiki/service.py`
16. `openviking/wiki/router.py`

## 1. `openviking/wiki/schemas.py`

### 文件定位

`schemas.py` 集中定义 Wiki 流程里会用到的数据结构和字段规则。它主要说明：

- pipeline 各阶段之间会传哪些 Python 对象；
- LLM 返回的结构化 JSON 应该长什么样；
- 最终写入 `viking://wiki/...` 的 card、node、source、manifest 包含哪些字段；
- ID、URI、非空文本这类基础字段需要满足什么规则。

### 阅读记录

#### 1.1 基础字段校验函数

对应代码：`openviking/wiki/schemas.py#L10-L43`

这一段都是基础校验工具，后面的 Pydantic 数据模型会复用它们来检查字段是否合法。它们给 Wiki 数据结构设定最基本的安全边界。

- `NODE_ID_RE`：规定 Wiki 节点 ID 只能由小写字母、数字和下划线组成。因为节点 ID 后面会进入路径，比如 `viking://wiki/nodes/<node_id>/`，所以不能包含空格、斜杠或其他特殊字符。
- `_require_text`：要求字段必须是非空字符串，并自动去掉首尾空白。很多标题、摘要、范围说明都用这个规则。
- `_require_node_id`：在非空字符串基础上，进一步要求它符合 `NODE_ID_RE`。主要用于 Wiki 节点的稳定 ID。
- `_require_resource_uri`：要求 URI 必须指向原始资源区，也就是以 `viking://resources/` 开头。用于表示“这是一篇已经入库的原始文档或资源目录”。
- `_require_optional_resource_uri`：允许为空；但如果不为空，就必须符合 `_require_resource_uri`。用于一些可选资源路径字段。
- `_require_source_uri`：要求来源 URI 只能来自 `viking://resources/` 或 `viking://wiki/`。这是因为底层节点的来源通常是原始资源，而父层节点的来源可能是已经生成的 Wiki 子节点。

简单说，这组函数负责提前拦住明显不合法的字符串、节点 ID 和 URI，避免错误数据进入后续的 Wiki 生成流程。

#### 1.2 基础类型别名和输入对象

对应代码：`openviking/wiki/schemas.py#L46-L75`

这一段先定义了一些带校验规则的字段类型，例如非空字符串、合法节点 ID、资源 URI、来源 URI 等。后面的数据对象使用这些类型时，就会自动检查字段是否合法。

`StrictModel` 是所有 Wiki 数据对象的共同基类。它禁止传入未声明的字段，这样可以尽早发现旧字段、拼错字段或 LLM 返回的多余字段。

`ResourceDocumentDraft` 表示入库解析阶段识别出的“文档边界”。它告诉 Wiki：某个资源目录下面有哪些文档应该分别生成 card。

`WikiResourceInput` 表示一篇准备生成 Document Card 的文档。它通常由 `build_wiki` 接口收到的资源 URI 转换而来：如果传入的是一个包含多篇文档的资源目录，就会根据文档边界信息展开成多条 `WikiResourceInput`；如果传入的是单篇文档资源，就只生成一条。后续 `content_loader.py` 会根据它读取文档内容，供 `cards.py` 生成对应的 Document Card。

#### 1.3 Document Card 相关对象

对应代码：`openviking/wiki/schemas.py#L75-L97`

`SourceSection` 表示节点正文生成时使用的一段来源内容。它包含 `section_uri` 和 `content`：前者说明这段内容来自哪里，后者是真正给节点正文生成使用的文本。

`ResourceDocument` 表示已经加载好内容的来源文档。它同时服务两个后续阶段：`content_or_structure` 是给 `cards.py` 生成 Document Card 用的文本输入；`source_sections` 是给节点正文生成用的结构化来源片段。底层节点的 `ResourceDocument` 来自原始资源；更高层节点的 `ResourceDocument` 由下层节点正文临时转换而来。

`DocumentCardContent` 表示 LLM 需要返回的 card 内容。它只包含语义信息，例如文档总结、主要观点、重要术语、候选 Wiki 主题；不包含 `doc_id`、`resource_uri`、`title` 这类系统已经知道的字段。

`DocumentCard` 是最终的完整来源卡片。它在 `DocumentCardContent` 的基础上补上 `doc_id`、`resource_uri`、`title` 和 `markdown`。它既可以表示原始文档，也可以表示一个已经生成好的 Wiki node。后续节点发现和来源分配都基于同一类 card 继续处理。

#### 1.4 WikiNode 和节点发现相关对象

对应代码：`openviking/wiki/schemas.py#L100-L138`

`WikiNode` 表示最终 Wiki 图上的一个主题节点。它包含节点 ID、标题、层级、覆盖范围，以及父子节点关系。`node_id` 会进入节点目录路径，所以必须是稳定且路径安全的字符串。

`scope` 是节点的权威边界，长期保存在 `nodes.json`。它不迁移到 card，也不被 card 的 `summary` 替代；节点正文生成时继续使用 `scope` 作为 include/exclude 约束。

父子关系不是严格树，而是 DAG。`parent_node_ids` 是列表，允许同一个节点属于多个更高层节点；`child_node_ids` 记录当前节点覆盖的下层节点。

`status` 用来区分节点是否会继续生成内容。节点发现阶段生成的节点默认是 `active`；后续如果发现某个节点绑定到的来源数量不足，代码会把它标记为 `rejected`。被拒绝的节点会保留在 `nodes.json` 里，方便排查模型发现了哪些候选主题，但不会继续生成节点正文。

`WikiNodeDiscoveryItem` 是 LLM 在节点发现阶段返回的基础节点信息。它只让 LLM 说明“这个节点叫什么”（`title`）以及“这个节点覆盖哪些知识范围”（`scope`）。

`WikiSourceNodeDiscoveryItem` 用于统一的节点发现。无论当前层的来源是原始文档 card，还是下层 node card，LLM 都返回 `supporting_source_ids` 来说明“哪些来源 card 支撑这个节点”。

`WikiSourceNodeDiscoveryResponse` 是节点发现的整体返回结构。除了发现出的节点外，它还会记录没有被分配进去的来源 ID，便于后续排查。

#### 1.5 SourceRef 和来源分配相关对象

对应代码：`openviking/wiki/schemas.py#L141-L170`

`SourceRef` 表示生成某个 Wiki 节点正文时可以参考的一个来源。来源可能是原始文档，也可能是已经生成好的下层 Wiki 节点。两者都先以 `DocumentCard` 参与节点发现，再转换成 `SourceRef`。

`support_scope` 说明这个来源支撑当前节点的哪部分范围；`matched_topics` 记录匹配到的主题信息。它们不是新的推理结果，主要是把前面已有的节点范围和候选主题带到来源记录里，方便后续生成正文时使用。

`SourceAssignmentResult` 是代码整理后的“节点到来源”总表。它告诉后续流程每个节点有哪些可用来源。父层节点的 `child_node_ids` 不再由一个单独字段保存，而是从 `ref_type == "wiki_node"` 的 `SourceRef` 派生。
`unassigned_source_ids` 记录没有被分配到任何节点的来源 ID。底层时它表示未分配的原始文档 ID，父层时它表示未聚合的下层 node ID。当前它主要写入 `source_assignments.json` 用于排查覆盖缺口；未来也可以用于补节点、重新分配或增量更新。

`SourceAssignmentItem` 和 `SourceAssignmentResponse` 用来表达“某个节点由哪些来源支撑”。节点发现阶段先得到这类绑定关系，后续再把它转换成详细的 `SourceRef`。

#### 1.6 节点正文和生成上下文相关对象

对应代码：`openviking/wiki/schemas.py#L173-L213`

`NodeDocumentsResponse` 表示 LLM 生成的节点正文列表。真正的综合知识内容会写入 `nodes/<node_id>/documents/*.md`。

`NodeDocumentContent` 是 LLM 返回的单篇正文内容，只包含标题和正文；`NodeDocument` 是代码补上 `document_id` 后的完整对象，用来决定最终写成 `0001.md`、`0002.md` 这类文件。

`GeneratedNodeContext` 表示一个节点生成完成后的内存结果，包含节点对象、node card、正文文档和来源。后续生成更高层节点时，会把下层 node card 作为统一来源输入。

## 2. `openviking/wiki/config.py`

### 文件定位

`config.py` 定义 Wiki 生成时用到的运行参数。

### 阅读记录

#### 2.1 生成规模和运行配置

`WikiGenerationLimits` 控制生成规模和并发，例如最多生成多少层、底层节点至少需要多少文档来源、父层节点至少覆盖多少子节点，以及 card 和节点内容生成的并发数。

`WikiConfig` 是总配置，主要记录原始资源根路径、Wiki 产物写入根路径、生成限制，以及传给底层 LLM/VLM 的模型配置。

## 3. `openviking/wiki/uri.py`

### 文件定位

`uri.py` 统一管理 Wiki 产物的写入路径。后续代码要写 card、节点正文、来源文件或运行日志时，都通过这里的函数拼出 `viking://wiki/...` 路径，避免到处手写路径字符串。

### 阅读记录

#### 3.1 节点 ID 和产物路径

`sanitize_node_id` 会把节点标题转换成适合放进路径里的节点 ID。例如 `Question Answering` 会变成 `question_answering`。这样生成的节点目录可以稳定写到 `viking://wiki/nodes/<node_id>/`。

其他函数负责拼接固定产物路径，例如原始文档 cards 目录、节点目录、`nodes/<node_id>/card.md`、`nodes/<node_id>/card.json`、`documents/0001.md`、`sources/` 和 `run/`。简单说，这个文件定义了 Wiki 产物在 `viking://wiki/` 下的目录布局。

## 4. `openviking/wiki/writer.py`

### 文件定位

`writer.py` 是 Wiki 写文件的统一出口。它负责创建 Wiki 目录，并把文本、JSON、JSONL 写入 VikingFS。

### 阅读记录

#### 4.1 写入方法

`ensure_dirs` 用来创建 Wiki 根目录、cards 目录、nodes 目录、run 目录，以及具体节点的 documents/sources 目录。

`write_text` 写普通文本；如果目标文件已经存在，就改为覆盖写入。`write_json` 和 `write_jsonl` 分别用于写 JSON 文件和 JSON Lines 日志文件。

`_to_jsonable` 会把 Pydantic 对象、dataclass、嵌套 dict/list 转成 `json.dumps` 能处理的普通 Python 结构，方便直接把 `DocumentCard`、`WikiNode`、`SourceRef` 这类对象写成 JSON。

## 5. `openviking/wiki/llm.py`

### 文件定位

`llm.py` 是 Wiki 调模型的统一入口。它不负责设计 prompt，也不负责解释模型输出，只负责把 prompt 和 JSON Schema 交给 `StructuredVLM`，并记录每次调用的输入和输出。

### 阅读记录

#### 5.1 调用日志对象

`LLMCallRecord` 记录一次模型调用的输入侧信息：当前步骤名、prompt 版本、prompt 哈希、完整 prompt、schema 名称和 schema 哈希。

`LLMOutputRecord` 记录一次模型调用的输出侧信息：当前步骤名、输出哈希和模型返回的结构化 JSON。

`WikiLLMRunLog` 把所有 prompt 记录和模型输出记录放在一起。pipeline 结束时会把这些记录写到 `run/prompts.jsonl` 和 `run/raw_outputs.jsonl`，用于排查某一步到底给模型喂了什么、模型返回了什么。

#### 5.2 `WikiLLMRunner.complete_json`

`complete_json` 是 Wiki 里统一调用模型的入口。别的文件不会直接调模型，而是把“当前是哪一步、要问模型的问题、期望模型返回的 JSON 格式”交给这个方法。它会按顺序做几件事：

- 给这次调用起一个固定名字，方便后面查日志；
- 在调用前保存完整 prompt 和 schema 的 hash；
- 调用 `StructuredVLM.complete_json_async`，让模型按指定 JSON 格式返回结果；
- 如果模型没有返回可解析的 JSON，或者返回的不是 JSON object，就直接报错；
- 调用成功后保存模型返回的 JSON，供后面写入运行日志。

这个方法只做“调用和记录”。某个字段是否符合业务含义、节点是否来源充足、正文是否要重试，都在后面的 cards/nodes/documents 等阶段处理。

`_hash_text` 用 SHA256 给 prompt、schema 和输出生成稳定哈希。它不是业务 ID，只用于日志排查：内容没变时 hash 就不变，方便比较两次运行是否用了同一份输入或得到同一份输出。

## 6. `openviking/wiki/prompts.py`

### 文件定位

`prompts.py` 负责准备各个 Wiki 阶段要喂给模型的 prompt 输入，它把 Python 对象整理成 JSON，再交给 `PromptManager` 渲染 `openviking/prompts/templates/wiki/` 里的模板。

### 阅读记录

#### 6.1 Prompt 输入控制

每个 `build_*_prompt` 方法都会为一个 Wiki 生成阶段准备输入。它会从 card、node、source 等对象里挑出当前阶段真正需要的字段，组装成一段 JSON，再交给对应的 prompt 模板。

`build_document_card_prompt` 只给模型看文档内容和少量 card 生成相关 metadata，不传 `doc_id`、`resource_uri`、`title` 这些标识字段。

`build_node_discovery_prompt` 只给模型看每个来源 card 的 `source_id`、`title`、`summary` 和 `candidate_topics`，用来聚合当前层节点，不传全文。底层来源 card 来自原始文档；更高层来源 card 来自下层 node。

`build_node_documents_prompt` 传节点边界和来源片段，用来生成节点正文。来源片段可能来自原始文档，也可能来自下层 node documents；prompt 统一要求按知识综合，不按来源逐段总结。

`build_node_card_prompt` 在节点正文生成后调用。它传 `WikiNode.title/scope` 和当前节点的 `documents/*.md`，生成 node card 的 `summary/main_points/important_terms/candidate_topics`。这里 `scope` 只作为理解正文的边界，不作为 card 字段输出。

`build_next_layer_decision_prompt` 传当前层已经生成好的节点信息，用来判断是否还需要继续向上聚合。

#### 6.2 统一渲染

`_render_wiki_prompt` 是所有 prompt builder 的共同出口。它把输入对象格式化成缩进 JSON，放进模板变量 `input_json`，然后调用 `PromptManager.render` 渲染具体模板。

## 7. `openviking/wiki/document_manifest.py`

### 文件定位

`document_manifest.py` 负责保存和读取资源目录里的文档边界文件 `.wiki_documents.json`。它处理的是 `viking://resources/...` 下的资源侧 manifest


### 阅读记录

#### 7.1 manifest 文件读写

`document_manifest_uri` 根据资源根 URI 拼出 `.wiki_documents.json` 的位置。例如 `viking://resources/demo/` 会得到 `viking://resources/demo/.wiki_documents.json`。

`write_document_manifest` 会把解析阶段得到的 `ResourceDocumentDraft` 列表写成 manifest。写入内容只包含文档边界信息：版本号、`doc_id`、标题和相对路径。

`_normalize_manifest_drafts` 处理单个非目录资源的情况。如果只有一篇文档且来源不是目录，它会把 `relative_uri` 清空，表示这个资源根本身就是一篇文档。

`load_document_manifest` 会尝试读取资源根目录下的 `.wiki_documents.json`。如果文件不存在，就返回空列表；如果存在，就把其中的 `documents` 校验成 `ResourceDocumentDraft` 列表。

#### 7.2 转换成 Wiki 输入

`wiki_inputs_from_manifest` 会把 manifest 里的文档边界转换成 `WikiResourceInput` 列表。后续 `service.py` 会把这些输入交给 `WikiPipeline.run_from_inputs`。

`_wiki_input_from_draft` 负责把单条 `ResourceDocumentDraft` 转成 `WikiResourceInput`。它会根据 `root_uri` 和 `relative_uri` 拼出这篇文档实际所在的资源 URI，并把这个 URI 同时放进 `resource_uri` 和 `document_dir_uri`。


## 8. `openviking/wiki/content_loader.py`

### 文件定位

`content_loader.py` 负责从 VikingFS/VikingDB 读取已经入库的资源内容，并整理成后续 Wiki 生成能使用的 `ResourceDocument`。它不调用模型，也不生成 card。

### 阅读记录

#### 8.1 加载流程

`load_document` 接收一条 `WikiResourceInput`，返回一条带正文输入的 `ResourceDocument`。它的作用是把“这篇文档在哪里”转换成“这篇文档有哪些内容可以喂给后续模型”。

读取位置优先使用 `document_dir_uri`。这个字段表示目录资源展开后，某一篇文档实际对应的子目录；如果它为空，就从 `resource_uri` 指向的资源本身读取。

加载时有两种模式：`summary` 读取文件级摘要，用于生成 Document Card；`raw_chunk` 直接读取文件原文，主要用于后续生成 Wiki 节点正文。`_collect_entries` 会从文档根目录递归遍历，遇到目录就继续往下走，遇到叶子文件就按当前模式读取内容。

当前逻辑只收集叶子文件内容，会跳过隐藏文件、`.abstract.md` 和 `.overview.md`。它不会把目录级 `abstract/overview` 加入输入，避免目录摘要和叶子文件摘要重复喂给模型。

在 `summary` 模式下，叶子文件摘要只来自 VikingDB 里这个文件 URI 对应的 `abstract`。查不到摘要时，这个文件会被记为 `[summary missing]`；如果遍历到的文件全部缺摘要，会直接报错，避免用空信息生成 card。

#### 8.2 格式化和辅助逻辑

`load_document` 会从同一批 entry 生成两种结果：`content_or_structure` 和 `source_sections`。`content_or_structure` 是一段给 Document Card 使用的可读文本；`source_sections` 是结构化来源片段，后续底层节点生成正文时会直接使用它，不再从文本里反解析 URI 和正文。

`_render_entries` 会把所有 entry 拼成一段文本。每条 entry 包含 URI、类型、目录路径和正文内容；`_render_entry` 定义单条 entry 的文本格式，`_clip` 负责截断并追加 `...(truncated)`。

`_source_sections_from_entries` 会把 entry 里的 `uri` 和 `text` 转成 `SourceSection`。如果来源内容太长，也会按预算截断，避免后续节点正文 prompt 输入过大。

如果总长度超过 `max_card_input_chars`，它会按 entry 平均分配预算并截断。第一轮每条至少保留 600 字符，第二轮每条至少保留 400 字符。这个策略优先保留各文件覆盖面，但在文件数量特别多时，并不能严格保证最终字符串一定小于 `max_card_input_chars`。

文件底部的辅助方法只处理存储访问和目录判断：`_is_directory` 用 `viking_fs.stat` 判断目录，失败时按非目录处理；`_list_children` 用 `viking_fs.ls` 列目录；`_safe_read` 读取文件原文，失败时返回空字符串；`_entry_is_dir` 兼容不同目录标识；`_is_hidden_semantic_file` 识别需要跳过的语义中间文件。

## 9. `openviking/wiki/cards.py`

### 文件定位

`cards.py` 负责生成 Document Card。它接收已经加载好内容的 `ResourceDocument`，调用 LLM 提炼出文档摘要、要点、重要术语和候选 Wiki 主题，最后组装成完整的 `DocumentCard`。

这个文件不负责读取资源内容。资源内容已经由 `content_loader.py` 准备好，并放在 `ResourceDocument.content_or_structure` 里。

### 阅读记录

#### 9.1 Card 生成流程

`DocumentCardGenerator` 是生成 Document Card 的主类。它持有 `WikiLLMRunner`，并用 `max_concurrent` 控制同时生成多少张 card。

`generate` 是当前对外使用的入口。它接收一组 `ResourceDocument`，并行生成多张 `DocumentCard`。真实 pipeline 中，`WikiPipeline.run_from_inputs` 会先用 `content_loader.load_document` 加载内容，再调用这个 `generate` 入口。

单篇文档生成时，代码会先根据 `ResourceDocument` 构造 prompt，然后让 LLM 按 `DocumentCardContent` 返回结构化 JSON。返回结果会经过 Pydantic 校验；如果模型没有返回可解析 JSON，或者字段不符合要求，最多会重试 3 次。

LLM 只负责生成文档的语义内容，例如 `summary`、`main_points`、`important_terms` 和 `candidate_topics`。`doc_id`、`resource_uri`、`title` 这些系统已经知道的字段由代码自己补上，避免模型编造文档标识。

#### 9.2 Markdown 输出

`render_card_markdown` 会把结构化的 `DocumentCard` 转成一段可读 Markdown，包含来源信息、摘要、主要观点、重要术语和候选 Wiki 主题。

pipeline 会把这份 Markdown 写成 `.card.md` 文件；同时也会写一份 `.card.json`，后续节点发现和来源分配主要读取 card 里的 `summary`、`candidate_topics`、`doc_id` 等结构化字段。

## 10. `openviking/wiki/nodes.py`

### 文件定位

`nodes.py` 负责发现 Wiki 节点。它接收一组来源 card，让 LLM 归纳出当前层的节点，并整理出“每个节点由哪些下层来源支撑”。

### 阅读记录

#### 10.1 节点发现流程

`NodeDiscoveryRunner` 是节点发现的主类。它持有 `WikiLLMRunner` 和 `WikiConfig`，分别用于调用模型和读取过滤阈值。

`discover_layer` 是统一入口。它接收一组 `DocumentCard`，构造节点发现 prompt，让模型根据 card 的摘要和候选主题归纳 Wiki 节点。模型返回节点标题、范围说明，以及每个节点关联的 `supporting_source_ids`。

底层和更高层的区别只体现在输入 card 的来源：底层输入是原始文档 card，更高层输入是上一层生成出来的 node card。返回结果都是一组 `WikiNode` 和一份 `SourceAssignmentResponse`。

#### 10.2 结果整理和校验

`_parse_layer_result` 负责把模型返回的 JSON 转成项目内部对象。它先用 Pydantic 校验返回结构，再调用 `_build_nodes` 生成正式的 `WikiNode`。

`_build_nodes` 会根据节点标题生成 `node_id`，并补上当前层级 `depth`。如果同一批节点标题生成了重复 ID，或者和已有来源 card 的 ID 冲突，会自动加后缀，避免路径冲突。

`_ensure_known_sources` 会检查模型返回的来源 ID 是否全部来自输入 card。更高层不再通过标题匹配子节点，而是直接使用下层 node card 的 `doc_id` 作为来源 ID。

`_complete_with_validation_retry` 是通用的 LLM 调用重试逻辑。它负责调用模型、校验返回结果，失败时最多重试 3 次。

## 11. `openviking/wiki/assignments.py`

### 文件定位

`assignments.py` 负责把节点发现阶段得到的来源 ID，转换成后续生成正文可直接使用的 `SourceRef`。

它不调用模型，也不重新判断节点是否合理。它只做一件事：根据已知的 card 补齐来源标题、URI、支撑范围等信息。

### 阅读记录

#### 11.1 来源转换

`SourceRefBuilder` 是这个文件的主类。它持有 `WikiConfig`，用于拼出 card、node 等产物路径。

`build_refs_by_node` 是统一入口。输入是 `nodes.py` 生成的 `SourceAssignmentItem` 和当前层所有来源 `DocumentCard`，输出是按节点分组的 `SourceRef`。

如果 card 的 `resource_uri` 指向 `viking://wiki/...`，生成的 `SourceRef.ref_type` 是 `wiki_node`，`card_uri` 指向 `nodes/<node_id>/card.md`；否则 `ref_type` 是 `document`，`card_uri` 指向全局 `cards/<doc_id>.card.md`。

#### 11.2 来源校验

转换方法会先检查来源 ID 是否真实存在。如果模型返回了不存在的来源 ID，代码会直接报错，避免后续生成正文时引用不存在的来源。

同一个节点下的来源会去重，并保留原来的顺序。

## 12. `openviking/wiki/documents.py`

### 文件定位

`documents.py` 负责生成节点正文 `documents/*.md`。


### 阅读记录

#### 12.1 节点内容生成

`NodeContentGenerator` 是这个文件的主类。它持有 `WikiLLMRunner`，用于调用模型。

`generate_node_documents` 是统一正文生成入口。它接收当前节点和来源片段，适用于所有 depth。底层节点的来源片段来自原始 `ResourceDocument.source_sections`；更高层节点的来源片段来自下层节点正文转换出的 `ResourceDocument.source_sections`。

#### 12.2 结果校验和编号

`_parse_node_documents_result` 处理模型返回的节点正文。模型只返回每篇正文的标题和内容，代码会交给 `_build_node_documents` 补上 `document_id`，这样后续才能写成 `documents/0001.md`、`documents/0002.md`。如果模型没有返回任何正文，会报错触发重试。

`_build_node_documents` 会给每篇正文补上稳定的 `document_id`，格式是 `0001`、`0002` 这种编号。后续 pipeline 会用这个编号写出 `documents/0001.md` 等文件。

`_complete_with_validation_retry` 是通用重试逻辑。模型返回格式不对、内容为空或校验失败时，最多重试 3 次。

## 13. `openviking/wiki/layer_decision.py`

### 文件定位

`layer_decision.py` 负责判断当前层节点生成完以后，要不要继续向上生成父层节点。

### 阅读记录

`LayerDecisionRunner` 是这个文件的主类。它持有 `WikiLLMRunner`，用于调用模型。

`should_continue_upward` 接收当前层已经生成好的 `GeneratedNodeContext` 列表，把这些节点信息放进 prompt，让模型判断是否还需要继续向上聚合。

模型会返回 `continue_upward`。如果是 `True`，pipeline 会继续生成父层；如果是 `False`，pipeline 就停止。

`min_child_nodes_per_parent` 会传给 prompt，作为模型判断时的参考条件：如果继续向上聚合，一个父节点至少应该覆盖多少个子节点。

模型返回格式不对时，代码最多重试 3 次。

## 14. `openviking/wiki/pipeline.py`

### 文件定位

`pipeline.py` 是 Wiki 生成的总编排器。前面几个文件分别负责 card 生成、节点发现、来源转换、节点正文生成和是否继续向上判断；`pipeline.py` 把这些阶段串成一次完整 Wiki 构建。

### 阅读记录

#### 14.1 入口和内容加载

`WikiPipeline` 初始化时会创建各阶段组件。这里可以把它理解成一次总装配：

- `DocumentCardGenerator`：把每篇文档生成 Document Card。
- `NodeDiscoveryRunner`：根据 card 或上一层节点发现当前层 Wiki 节点。
- `SourceRefBuilder`：把节点绑定的来源 ID 转成完整 `SourceRef`。
- `NodeContentGenerator`：生成节点正文。
- `LayerDecisionRunner`：判断当前层生成完后是否继续向上聚合。

`run_from_inputs` 是当前正式入口。它接收 `WikiResourceInput`，先用 `WikiContentLoader` 加载成 `ResourceDocument`，再生成 Document Card，最后进入按层生成流程。

加载文档时会控制并发，并保持输出顺序和输入顺序一致。默认情况下，card 使用 summary 输入；如果后续节点正文需要 raw chunk，pipeline 会在 card 生成时并行加载 raw chunk，减少整体等待时间。

`ResourceDocument.content_or_structure` 用于生成 Document Card；`ResourceDocument.source_sections` 用于底层节点正文生成。

#### 14.2 按层生成 Wiki 节点

`_run_from_cards` 是主流程。它先写出所有 card 到文件，然后从第 1 层开始循环生成节点，最多不超过 `max_depth`。

第 1 层是底层节点，输入是原始文档 cards；更高层节点的输入是上一层已经生成好的 node cards。

每一层都会按同样的顺序处理：

```text
发现节点
-> 构造 SourceRef
-> 过滤来源不足的节点
-> 写 nodes.json 和 source_assignments.json
-> 生成 active 节点内容
-> 生成 active 节点 card
-> 判断是否继续向上
```

底层节点至少要绑定 `min_refs_per_node` 个文档来源；父层节点至少要覆盖 `min_child_nodes_per_parent` 个子节点。来源不足的节点会被标记为 `rejected`，保留在 `nodes.json` 中，但不会继续生成正文。

#### 14.3 节点内容生成

`_generate_layer_contexts` 会并发生成当前层所有 active 节点的内容，并保持返回顺序稳定。

`_generate_node_context` 负责生成单个节点的完整内容包：

```text
sources/*.ref.json
documents/*.md
card.md / card.json
GeneratedNodeContext
```

节点正文统一使用 `ResourceDocument.source_sections`。底层节点的 `ResourceDocument` 来自原始文档；更高层节点的 `ResourceDocument` 由下层节点正文转换而来。正文生成完成后，再基于 `WikiNode.title/scope` 和 `documents/*.md` 生成 node card，写入 `nodes/<node_id>/card.md` 和 `card.json`。

#### 14.4 主流程里的数据整理

这一组函数主要负责把前面阶段的结果整理成下一步要用的输入。

节点生成正文前，会调用 `_source_documents_for_refs`。它根据节点绑定的 `SourceRef` 找到对应的 `ResourceDocument`，再把 `ResourceDocument.source_sections` 包装成 `source_documents`。这样节点正文 prompt 可以直接拿到结构化来源片段。

每个 active 节点生成完成后，会通过 `_resource_document_for_node` 把它的正文转换成下一层可消费的 `ResourceDocument`，并把 node card 放入下一层的来源 card 列表。

节点发现之后，会调用 `_reject_nodes_with_insufficient_refs` 做来源数量过滤。底层阈值来自 `min_refs_per_node`，更高层阈值来自 `min_child_nodes_per_parent`；来源不足的节点会被标记为 `rejected`。

更高层节点生成后，`_assign_parent_node_links` 会把父节点 ID 追加写回对应子节点的 `parent_node_ids`。同一个下层节点可以被多个上层节点引用，所以这里是追加和去重，不是覆盖单个父节点。

文件写入相关的辅助函数比较直接：`_write_cards` 写原始文档 card，`_write_node_card` 写 node card，`_write_source_refs` 写节点来源，`_write_run_records` 写本次运行的配置、prompt 日志和模型原始输出。

## 15. `openviking/wiki/service.py`

### 文件定位

`service.py` 是 Wiki 的服务层。它接收外部请求，校验参数和资源 URI，准备 `WikiContentLoader`、`WikiVikingFSWriter` 和 `WikiPipeline`，然后调用 pipeline 开始生成。

### 阅读记录

#### 15.1 依赖注入

`WikiService` 初始化时可以传入 `VikingDBManager` 和 `VikingFS`，也可以先创建空服务，再通过 `set_dependencies` 注入依赖。

`_ensure_initialized` 会检查这两个依赖是否已经准备好。没有初始化就调用 `build_wiki` 或 `clear_wiki`，会直接报错。

#### 15.2 构建 Wiki

`build_wiki` 是服务侧构建入口。它接收资源 URI 列表、请求上下文、card 输入模式和输入长度限制。

它先做参数校验：

- `resource_uris` 不能为空；
- 每个资源必须存在；
- 资源 URI 必须以 `viking://resources/` 开头；
- Wiki 输出路径必须以 `viking://wiki/` 开头；
- `card_input_mode` 只能是 `summary` 或 `raw_chunk`。

资源 URI 校验完成后，`_wiki_resource_inputs_from_uris` 会把它们转换成 `WikiResourceInput`：

- 如果资源目录下有 `.wiki_documents.json`，就按 manifest 里的文档边界展开；
- 如果没有 manifest，就把整个资源 URI 当成一篇文档。

接着 `build_wiki` 会创建内容加载器、写入器和 pipeline，并调用：

```python
pipeline.run_from_inputs(...)
```

最后返回本次构建的摘要，比如生成了多少文档输入、cards、nodes 和 node contexts。

#### 15.3 清理 Wiki

`clear_wiki` 用来删除 Wiki 产物目录，默认清理：

```text
viking://wiki/
```

它会调用：

```python
self._viking_fs.rm(wiki_root_uri, recursive=True, ctx=ctx)
```

这里不只是删除文件系统目录。`VikingFS.rm` 在递归删除时会先收集目录下的文件和子目录 URI，并联动删除这些 URI 对应的向量记录，然后再删除文件。

所以 `clear_wiki` 清理的是 Wiki 产物和 Wiki 文件对应的向量索引。

#### 15.4 小工具函数

`_validate_resource_uri` 和 `_validate_wiki_root_uri` 是路径安全检查，防止把资源输入和 Wiki 输出写到错误的 URI 空间。

`_wiki_resource_input_from_uri` 用于没有 manifest 的情况。它会用资源 URI 的最后一段作为标题，并用 URI hash 生成稳定的 `doc_id`。

`_common_resource_root` 用于记录本次构建的资源范围。单个资源时返回该资源 URI；多个资源时返回统一的 `viking://resources/`。

## 16. `openviking/wiki/router.py`

### 文件定位

`router.py` 是 Wiki 的 HTTP 入口层。它只负责定义接口参数、拿请求上下文、调用 `WikiService`，不处理具体生成逻辑。

### 阅读记录

#### 16.1 请求模型

`BuildWikiRequest` 是 `/api/v1/wiki/build` 的请求体，包含资源 URI 列表、Wiki 输出目录、card 输入模式、输入长度限制和 telemetry 开关。

`ClearWikiRequest` 是 `/api/v1/wiki/clear` 的请求体，包含要清理的 Wiki 输出目录和 telemetry 开关。

两个请求模型都设置了 `extra="forbid"`，表示请求里不能带未定义字段。

#### 16.2 路由处理

`build_wiki` 路由会从请求里取参数，拿到当前 `RequestContext`，然后调用：

```python
service.wiki.build_wiki(...)
```

`clear_wiki` 路由同理，会调用：

```python
service.wiki.clear_wiki(...)
```

两个接口都通过 `run_operation` 包一层，用于统一记录操作和处理 telemetry，最后用 `response_from_result` 转成标准响应。

## 附录：实际产物对照

下面用一次 `qasper_30` benchmark 的实际产物，把磁盘文件和前面提到的关键类对应起来。建议读完前面的核心类和流程后再看这一节。

本次 Wiki 产物根目录是：

```text
benchmark/wiki/wiki_storage/qasper_30/qasper_30_viking_store_index/viking/default/wiki/
```

资源入库后的文档边界文件是：

```text
benchmark/wiki/wiki_storage/qasper_30/qasper_30_viking_store_index/viking/default/resources/qasper_30_processed_docs/.wiki_documents.json
```

这个文件里的每一条 `documents` 记录对应一个 `ResourceDocumentDraft`。例如：

```json
{
  "doc_id": "1603_08594_doc",
  "title": "1603.08594_doc",
  "relative_uri": "1603.08594_doc"
}
```

`WikiService` 读取这些 draft 后，会把它们转换成 `WikiResourceInput`，再交给 `WikiPipeline.run_from_inputs`。

Wiki 生成后的主要文件和关键类关系如下：

| 文件 | 对应对象 | 作用 |
| --- | --- | --- |
| `cards/<doc_id>.card.json` | `DocumentCard` | 一篇原始文档的结构化卡片，包含摘要、要点、候选主题和文档标识。 |
| `cards/<doc_id>.card.md` | `DocumentCard.markdown` | 同一张 card 的 Markdown 展示版本，方便人工查看。 |
| `nodes.json` | `list[WikiNode]` | 全部 Wiki 节点索引，包括 active 和 rejected 节点。 |
| `source_assignments.json` | `SourceAssignmentResult` | 每个节点绑定到哪些来源，以及有哪些来源未分配。 |
| `nodes/<node_id>/sources/*.ref.json` | `SourceRef` | 单个节点的来源记录。来源可能是原始文档，也可能是下层 node。 |
| `nodes/<node_id>/card.json` | `DocumentCard` | 节点的结构化 card，供更高层节点发现使用。 |
| `nodes/<node_id>/card.md` | `DocumentCard.markdown` | 节点 card 的 Markdown 展示版本，替代旧 `node.md`。 |
| `nodes/<node_id>/documents/0001.md` | `NodeDocument.content` | 节点正文，是模型基于来源综合生成的知识内容。 |
| `run/config.json` | `WikiConfig` | 本次运行使用的配置，包括模型配置和生成限制。 |
| `run/prompts.jsonl` | `WikiLLMRunner` 日志 | 每次 LLM 调用的 prompt 记录。 |
| `run/raw_outputs.jsonl` | `WikiLLMRunner` 日志 | 每次 LLM 调用的原始结构化输出。 |

看一个具体例子：

```text
cards/1909_08824_doc.card.json
```

对应一张 `DocumentCard`，里面的 `doc_id` 是 `1909_08824_doc`，`resource_uri` 指向原始资源：

```text
viking://resources/qasper_30_processed_docs/1909.08824_doc
```

再看节点索引：

```text
nodes.json
```

里面的 active 底层节点会带 `depth=1`，例如：

```text
biomedical_natural_language_processing_applications
```

这个节点目录下会有：

```text
nodes/biomedical_natural_language_processing_applications/card.md
nodes/biomedical_natural_language_processing_applications/card.json
nodes/biomedical_natural_language_processing_applications/documents/0001.md
nodes/biomedical_natural_language_processing_applications/sources/*.ref.json
```

其中 `sources/*.ref.json` 是 `SourceRef`。如果 `ref_type` 是 `document`，表示这个来源来自原始文档；如果 `ref_type` 是 `wiki_node`，表示这个来源来自上一层已经生成好的子节点。

更高层节点也在同一个 `nodes.json` 里。本次 benchmark 生成了 `depth=2` 的上层节点，例如：

```text
natural_language_processing_model_design_optimization_and_evaluation
```

它的 `child_node_ids` 指向若干底层 active 节点。反过来，底层节点的 `parent_node_ids` 会追加对应父节点 ID；如果一个底层节点同时支撑多个上层节点，`parent_node_ids` 会保留多个父节点。
