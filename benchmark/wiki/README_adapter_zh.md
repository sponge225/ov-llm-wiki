# Wiki Benchmark 新数据集接入与 Adapter 编写指南

这份文档说明如何把一个新的 QA 数据集接入 `benchmark/wiki` 流程，并编写对应的 adapter。

主流程 README 负责教你跑通现有 `qasper_30` 示例；本文只关注两件事：

1. 新数据集应该整理成什么结构。
2. 新 adapter 应该实现哪些方法，才能适配 `import -> build_wiki -> gen -> eval` 流程。

## 接入前提

先确认你已经能跑通现有示例：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step all
```

如果 `qasper_30` 还没跑通，先不要接新数据集。否则新数据集问题、模型配置问题、OpenViking 入库问题会混在一起，很难定位。

## 目录约定

建议把新数据集放在：

```text
benchmark/wiki/datasets/<your_dataset_name>/
```

示例：

```text
benchmark/wiki/datasets/my_dataset/
├── documents.json
└── qa.json
```

也可以是单个 JSON、JSONL、CSV，或者一个包含文档和 QA 的目录。`adapter` 会拿到配置里的 `paths.dataset_path`，你可以在 adapter 里自行决定如何读取。

运行产物建议继续使用统一模板：

```yaml
paths:
  dataset_path: "datasets/{dataset_name}"
  doc_output_dir: "wiki_storage/{dataset_name}/{dataset_name}_processed_docs"
  vector_store: "wiki_storage/{dataset_name}/{dataset_name}_viking_store_index"
  output_dir: "Output/{dataset_name}/wiki"
  log_file: "Output/{dataset_name}/wiki/benchmark.log"
```

这些路径都相对 `benchmark/wiki/` 解析。

## Adapter 的职责

新 adapter 继承：

```python
from .base import BaseAdapter, StandardDoc, StandardSample, StandardQA
```

必须实现三个方法：

```python
def data_prepare(self, doc_dir: str) -> list[StandardDoc]:
    ...

def load_and_transform(self) -> list[StandardSample]:
    ...

def build_prompt(self, qa: StandardQA, context_blocks: list[str]) -> tuple[str, dict]:
    ...
```

### `data_prepare`

用于 `--step import`。

它负责把原始数据里的文档转换成 OpenViking 可以入库的文件，并返回文档列表：

```python
StandardDoc(sample_id="doc_001", doc_path="/path/to/doc_001.md")
```

常见做法：

- 如果原始文档是 JSON 字段，把它写成 Markdown 文件。
- 如果原始文档已经是 PDF、Markdown、TXT，可以直接返回原文件路径。
- `sample_id` 要稳定，最好能和 QA 里的文档 ID 对上。

返回值示例：

```python
[
    StandardDoc(sample_id="paper_001", doc_path=".../paper_001.md"),
    StandardDoc(sample_id="paper_002", doc_path=".../paper_002.md"),
]
```

### `load_and_transform`

用于 `--step gen`。`--step eval` 不会重新读取原始 QA 数据，而是评测 `gen` 阶段生成的 `generated_answers.json`。

它负责把原始 QA 数据转换成 benchmark 的统一结构：

```python
StandardSample(
    sample_id="paper_001",
    qa_pairs=[
        StandardQA(
            question="问题文本",
            gold_answers=["标准答案 1", "标准答案 2"],
            evidence=["证据文本 1", "证据文本 2"],
            category="optional_category",
            metadata={"source_question_id": "q_001"},
        )
    ],
)
```

字段说明：

- `sample_id`：样本或文档 ID。建议和 `StandardDoc.sample_id` 保持一致。
- `question`：最终要给 VikingBot 或 baseline LLM 的问题。
- `gold_answers`：标准答案列表。即使只有一个答案，也要放在 list 里。
- `evidence`：可选，用于 recall 计算和排查。没有证据时可以传空 list。
- `category`：可选，问题类型。
- `metadata`：可选，保留原始 ID、答案类型、出处等调试信息。

注意：如果某个问题没有可评测答案，建议直接过滤掉，或者明确把标准答案写成统一的不可回答表达，例如 `"Not mentioned"`。不要返回空的 `gold_answers`。

### `build_prompt`

用于 baseline 模式。

当前常用配置是：

```yaml
execution:
  mode: "vikingbot"
```

在 `vikingbot` 模式下，生成答案主要由 VikingBot 完成，`build_prompt` 不会作为主路径使用。但 `BaseAdapter` 仍要求实现它，方便未来切换 baseline 模式或做对比实验。

最小实现可以这样写：

```python
def build_prompt(self, qa: StandardQA, context_blocks: list[str]) -> tuple[str, dict]:
    context = "\n\n".join(context_blocks)
    prompt = f"""请只根据下面的上下文回答问题。

上下文：
{context}

问题：
{qa.question}

答案："""
    return prompt, {}
```

## 最小 Adapter 模板

在 `benchmark/wiki/src/adapters/` 下新建文件，例如：

```text
benchmark/wiki/src/adapters/my_dataset_adapter.py
```

模板：

```python
import json
import os
from collections import defaultdict
from typing import Any

from .base import BaseAdapter, StandardDoc, StandardSample, StandardQA


class MyDatasetAdapter(BaseAdapter):
    def data_prepare(self, doc_dir: str) -> list[StandardDoc]:
        if not os.path.exists(self.raw_file_path):
            raise FileNotFoundError(f"Raw data file not found: {self.raw_file_path}")

        os.makedirs(doc_dir, exist_ok=True)

        with open(self.raw_file_path, "r", encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)

        docs: list[StandardDoc] = []
        seen_doc_ids: set[str] = set()

        for item in data:
            doc_id = str(item["doc_id"])
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)

            title = str(item.get("title", doc_id))
            body = str(item.get("document", ""))
            content = f"# {title}\n\n{body}\n"

            doc_path = os.path.join(doc_dir, f"{doc_id}.md")
            with open(doc_path, "w", encoding="utf-8") as out:
                out.write(content)

            docs.append(StandardDoc(sample_id=doc_id, doc_path=doc_path))

        return docs

    def load_and_transform(self) -> list[StandardSample]:
        if not os.path.exists(self.raw_file_path):
            raise FileNotFoundError(f"Raw data file not found: {self.raw_file_path}")

        with open(self.raw_file_path, "r", encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)

        grouped: dict[str, list[StandardQA]] = defaultdict(list)

        for item in data:
            doc_id = str(item["doc_id"])
            answer = item.get("answer")
            if answer is None or str(answer).strip() == "":
                continue

            evidence = item.get("evidence", [])
            if isinstance(evidence, str):
                evidence = [evidence]

            grouped[doc_id].append(
                StandardQA(
                    question=str(item["question"]),
                    gold_answers=[str(answer)],
                    evidence=[str(x) for x in evidence if str(x).strip()],
                    category=item.get("category"),
                    metadata={"question_id": item.get("question_id")},
                )
            )

        return [
            StandardSample(sample_id=doc_id, qa_pairs=qa_pairs)
            for doc_id, qa_pairs in grouped.items()
        ]

    def build_prompt(self, qa: StandardQA, context_blocks: list[str]) -> tuple[str, dict]:
        context = "\n\n".join(context_blocks)
        prompt = f"""请只根据下面的上下文回答问题。

上下文：
{context}

问题：
{qa.question}

答案："""
        return prompt, {}
```

这个模板假设原始数据是一个 JSON list，每条数据包含：

```json
{
  "doc_id": "doc_001",
  "title": "Document Title",
  "document": "document content",
  "question_id": "q_001",
  "question": "question text",
  "answer": "gold answer",
  "evidence": ["evidence text"]
}
```

你的真实数据字段不一样时，只需要在 `data_prepare` 和 `load_and_transform` 里做字段映射。

## 新建配置文件

复制一份配置：

```bash
cp benchmark/wiki/config/config.yaml benchmark/wiki/config/my_dataset.yaml
```

然后修改关键项：

```yaml
dataset_name: "my_dataset"

adapter:
  module: "src.adapters.my_dataset_adapter"
  class_name: "MyDatasetAdapter"

execution:
  mode: "vikingbot"
  server_port: 2036
  max_workers: 1
  retrieval_topk: 5
  max_queries: 2
  skip_ingestion: false
  ingest_mode: "directory"
  build_wiki: true
  wiki_card_input_mode: "summary"
  wiki_max_card_input_chars: 20000
  retrieval_instruction: ""

paths:
  dataset_path: "datasets/my_dataset/my_dataset.json"
  doc_output_dir: "wiki_storage/{dataset_name}/{dataset_name}_processed_docs"
  vector_store: "wiki_storage/{dataset_name}/{dataset_name}_viking_store_index"
  output_dir: "Output/{dataset_name}/wiki"
  log_file: "Output/{dataset_name}/wiki/benchmark.log"

llm:
  model: "${OV_VLM_MODEL}"
  provider: "${OV_VLM_PROVIDER}"
  temperature: 0
  base_url: "${OV_VLM_API_BASE}"
  api_key_env_var: "OV_VLM_API_KEY"
```

建议新数据集一开始保持：

- `max_queries: 2`
- `max_workers: 1`
- `build_wiki: true`，用于 `--step all` 时在入库后自动继续生成 Wiki
- `wiki_card_input_mode: "summary"`

先跑通小样本，再扩大规模。

## 分阶段验证

### 1. 验证 adapter 能被导入

```bash
uv run python - <<'PY'
from benchmark.wiki.src.adapters.my_dataset_adapter import MyDatasetAdapter
print(MyDatasetAdapter)
PY
```

如果这里失败，优先检查：

- 文件名是否是 `my_dataset_adapter.py`。
- 类名是否是 `MyDatasetAdapter`。
- 配置里的 `adapter.module` 和 `adapter.class_name` 是否一致。

### 2. 验证 `data_prepare`

```bash
uv run python - <<'PY'
from benchmark.wiki.src.adapters.my_dataset_adapter import MyDatasetAdapter

adapter = MyDatasetAdapter("benchmark/wiki/datasets/my_dataset/my_dataset.json")
docs = adapter.data_prepare("benchmark/wiki/.temp/my_dataset_docs_check")
print("docs =", len(docs))
print(docs[:3])
PY
```

期望：

- `docs` 数量大于 0。
- 返回的 `doc_path` 文件真实存在。
- 打开生成的 Markdown，内容不是空的。

### 3. 验证 `load_and_transform`

```bash
uv run python - <<'PY'
from benchmark.wiki.src.adapters.my_dataset_adapter import MyDatasetAdapter

adapter = MyDatasetAdapter("benchmark/wiki/datasets/my_dataset/my_dataset.json")
samples = adapter.load_and_transform()
qa_count = sum(len(s.qa_pairs) for s in samples)
print("samples =", len(samples))
print("qa_count =", qa_count)
print(samples[0].qa_pairs[0])
PY
```

期望：

- `samples` 数量大于 0。
- `qa_count` 数量大于 0。
- 每个 `StandardQA.gold_answers` 都不是空 list。

### 4. 只跑入库

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/my_dataset.yaml \
  --step import
```

确认产物：

```bash
ls benchmark/wiki/wiki_storage/my_dataset
cat benchmark/wiki/Output/my_dataset/wiki/imported_resources.json
```

如需生成 Wiki，再单独执行：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/my_dataset.yaml \
  --step build_wiki
```

确认 Wiki 产物存在：

```bash
find benchmark/wiki/wiki_storage/my_dataset/my_dataset_viking_store_index/viking/default/wiki \
  -maxdepth 3 -type f
```

### 5. 复用入库和 Wiki，只跑生成和评测

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/my_dataset.yaml \
  --step gen+eval
```

结果会输出到：

```text
benchmark/wiki/Output/my_dataset/wiki/
```

## 常见问题

### `Could not import module`

检查配置：

```yaml
adapter:
  module: "src.adapters.my_dataset_adapter"
  class_name: "MyDatasetAdapter"
```

`module` 是 Python import 路径，不是文件系统路径；不要写成 `benchmark/wiki/src/adapters/...`。

### `Class ... not found`

模块导入成功了，但类名不对。检查：

- Python 文件里的类名。
- YAML 里的 `adapter.class_name`。

### `Raw data file not found`

`paths.dataset_path` 配错，或者数据文件没有放到对应目录。

注意：`paths.dataset_path` 是相对 `benchmark/wiki/` 解析的。

### `docs = 0`

`data_prepare` 没有生成任何文档。常见原因：

- 原始数据字段名映射错。
- 过滤条件过严。
- 文档路径存在但内容为空。

没有文档就无法入库，也无法构建 Wiki。

### `qa_count = 0`

`load_and_transform` 没有生成任何 QA。常见原因：

- 问题字段名映射错。
- 答案字段为空，被全部过滤。
- 数据是 JSONL/CSV，但代码按 JSON list 读取。

### `gold_answers` 为空

不要返回空标准答案。评测阶段需要 gold answer。

如果数据集中某些问题不可回答，可以选择：

- 过滤掉这些问题。
- 统一写成 `"Not mentioned"` 或数据集约定的不可回答表达。

### 入库成功但回答效果差

优先检查 `data_prepare` 生成的文档是否适合检索和 Wiki 构建：

- Markdown 标题是否清晰。
- 文档内容是否完整。
- 多个字段是否被拼接成可读文本。
- 表格、列表、段落是否保留了关键信息。

对于长文档，建议保留章节结构，例如：

```markdown
# Title

## Abstract

...

## Section Name

...
```

## 参考实现

当前仓库已有 adapter 可以作为参考：

- `benchmark/wiki/src/adapters/qasper_adapter.py`：目录或单 JSON 文件，学术论文 QA。

一般新数据集从 `qasper_adapter.py` 或本文的最小模板改起即可。
