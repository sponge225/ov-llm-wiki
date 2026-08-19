# Wiki Benchmark 运行手册

这个 benchmark 用来验证 OpenViking 的 Wiki 生成和 VikingBot 问答评测流程。最常用的路径是：

1. 把 Qasper 示例数据导入 OpenViking。
2. 调用独立 Wiki 接口生成 Wiki。
3. 用 VikingBot 基于 OpenViking/Wiki 回答问题。
4. 用 LLM judge 评测答案，生成指标报告。

## 最短运行路径

如果你只想先跑通一次，可以按这个顺序执行：

```bash
cd /path/to/openviking-repo

uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset Qasper \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets \
  --num-docs 30 \
  --seed 42 \
  --sample-mode random

cp benchmark/wiki/.env.example benchmark/wiki/.env
cp benchmark/wiki/ov.conf.example benchmark/wiki/ov.conf
```

然后编辑 `benchmark/wiki/.env`，把模型名、服务地址和 API key 改成你自己的配置。确认后运行：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step all
```

如果 `import` 和 `build_wiki` 已经成功跑过，后续通常不要重跑 `all`，直接复用已有结果：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step gen+eval
```

## 你会用到哪些文件

```text
benchmark/wiki/
├── run.py                         # benchmark 入口脚本
├── README_adapter_zh.md           # 新数据集接入与 adapter 编写指南
├── README_code_zh.md              # 代码结构与实现逻辑说明
├── config/
│   ├── config.yaml                # 通用配置模板
│   └── qasper_30.yaml             # 已准备好的 Qasper 30 条样例配置
├── raw_data/                      # 运行下载脚本后生成：官方原始数据
├── datasets/                      # 运行抽样脚本后生成：benchmark 输入数据
├── .env.example                   # 模型配置环境变量模板
├── ov.conf.example                # OpenViking 配置示例
├── src/                           # pipeline、adapter、runner、metric 代码
├── scripts/                       # 可选的数据准备脚本
├── wiki_storage/                  # 运行后生成：OpenViking 存储、索引、Wiki 产物
├── Output/                        # 运行后生成：答案、评测结果、日志、报告
└── .temp/                         # 运行后生成：临时 OpenViking 配置、server 日志、bot JSON
```

`raw_data/`、`datasets/`、`wiki_storage/`、`Output/`、`.temp/` 都是运行产物，通常不提交到仓库。`benchmark/wiki/.env` 是本地密钥配置，也不要提交。

## 第 0 步：进入仓库根目录

推荐始终从仓库根目录运行命令，路径最不容易错。下面的 `/path/to/openviking-repo` 是占位路径，请替换成你自己 clone 出来的仓库目录。

```bash
cd /path/to/openviking-repo
```

确认当前目录正确：

```bash
pwd
```

你应该看到：

```text
/path/to/openviking-repo
```

## 第 1 步：确认 Python/uv 环境可用

本仓库推荐用 `uv run` 运行 Python 命令。先确认 `uv` 可用：

```bash
uv --version
```

再确认 benchmark 入口能加载：

```bash
uv run python benchmark/wiki/run.py --help
```

能看到 `--config`、`--step`、`--ov-conf`、`--env-file` 这些参数，就说明基础环境没问题。

如果 `uv` 不存在，先安装 uv，或使用项目既有的 Python 环境。但本文后续命令都使用 `uv run`。

## 第 2 步：下载并生成示例数据

`qasper_30.yaml` 使用 Qasper 示例。数据准备分两步：

1. 下载官方 Qasper 原始包到 `benchmark/wiki/raw_data/Qasper/`。
2. 从原始包里抽样生成 benchmark 可读取的数据到 `benchmark/wiki/datasets/Qasper/`。

推荐直接用统一脚本完成：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset Qasper \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets \
  --num-docs 30 \
  --seed 42 \
  --sample-mode random
```

这个命令会下载两个官方压缩包：

```text
qasper-train-dev-v0.3.tgz
qasper-test-and-evaluator-v0.3.tgz
```

下载并解压后，原始数据会在：

```text
benchmark/wiki/raw_data/Qasper/
```

抽样后的 benchmark 输入会在：

```text
benchmark/wiki/datasets/Qasper/
```

确认生成成功：

```bash
ls benchmark/wiki/datasets/Qasper
```

你应该能看到类似：

```text
qasper-train-v0.3.json
qasper-dev-v0.3.json
qasper-test-v0.3.json
sampling_metadata.json
```

`QasperAdapter` 支持读取一个目录下的多个 JSON 文件，所以 `qasper_30.yaml` 默认读取：

```yaml
paths:
  dataset_path: "datasets/Qasper"
```

### 如果下载已经完成，只想重新抽样

可以跳过下载：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset Qasper \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets \
  --num-docs 30 \
  --seed 42 \
  --sample-mode random \
  --skip-download
```

### 如果网络下载失败

可以手动下载并放到 `benchmark/wiki/raw_data/Qasper/`，确保目录里有：

```text
qasper-train-v0.3.json
qasper-dev-v0.3.json
qasper-test-v0.3.json
```

然后执行只抽样：

```bash
uv run python benchmark/wiki/scripts/sample_dataset.py \
  --dataset Qasper \
  --input-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets \
  --num-docs 30 \
  --seed 42 \
  --sample-mode random
```

## 第 3 步：准备 `.env` 和 OpenViking 配置

这个流程会调用两类模型，建议把模型相关配置集中放在 `benchmark/wiki/.env`：

- `embedding.dense`：用于导入文档时做向量化。
- `vlm`：用于 Wiki 生成、VikingBot 回答、评测打分。

`benchmark/wiki/run.py` 启动时会自动读取 `benchmark/wiki/.env`，然后展开 `qasper_30.yaml` 和 `ov.conf` 里的 `${VAR}` 占位符。shell 环境变量优先级更高：如果某个变量已经在当前 shell 里存在，`.env` 不会覆盖它。

### 1. 复制 `.env.example`

```bash
cp benchmark/wiki/.env.example benchmark/wiki/.env
```

然后编辑 `benchmark/wiki/.env`：

```text
OV_VLM_PROVIDER=volcengine
OV_VLM_MODEL=doubao-seed-2-0-pro-260215
OV_VLM_API_BASE=https://ark.cn-beijing.volces.com/api/v3
OV_VLM_API_KEY=your_vlm_api_key_here

OV_EMBEDDING_PROVIDER=volcengine
OV_EMBEDDING_MODEL=doubao-embedding-vision-251215
OV_EMBEDDING_API_BASE=https://ark.cn-beijing.volces.com/api/v3
OV_EMBEDDING_API_KEY=your_embedding_api_key_here
OV_EMBEDDING_INPUT=multimodal
OV_EMBEDDING_DIMENSION=1024
```

这里除了 `api_key`，还要确认模型名、provider、api_base、embedding 维度/输入类型都和你实际使用的模型服务匹配。上面是一个使用 volcengine 模型的示例, 如果你使用的模型和上面一样，只需要修改api_key即可。

不要把 `benchmark/wiki/.env` 提交到仓库。

### 2. 复制 `ov.conf.example`

如果没有现成配置，可以复制示例配置：

```bash
cp benchmark/wiki/ov.conf.example benchmark/wiki/ov.conf
```

示例里的模型字段会引用 `.env`：

```json
"model": "${OV_VLM_MODEL}",
"api_key": "${OV_VLM_API_KEY}",
"api_base": "${OV_VLM_API_BASE}"
```

一般情况下，你只需要改 `.env`，不用把真实 key 写进 `ov.conf`。

如果你已经有自己的 `ov.conf`，也可以继续使用。后续运行命令时加上：

```bash
--ov-conf /path/to/ov.conf
```

但仍然建议同时准备 `benchmark/wiki/.env`，因为 `benchmark/wiki/config/qasper_30.yaml` 里的 judge/VikingBot 模型配置也会从 `.env` 读取：

```yaml
llm:
  model: "${OV_VLM_MODEL}"
  provider: "${OV_VLM_PROVIDER}"
  temperature: 0
  base_url: "${OV_VLM_API_BASE}"
  api_key_env_var: "OV_VLM_API_KEY"
```

不要把包含真实 key 的 `ov.conf` 或 `.env` 提交到仓库。

### 3. 确认 `.env` 被加载

运行 benchmark 时，如果 `benchmark/wiki/.env` 存在，启动日志里会出现类似：

```text
[Init] Loaded 10 variable(s) from: .../benchmark/wiki/.env
```

如果显示 `.env` 中的 key 已经存在于环境变量里，也正常；这表示当前 shell 里的变量优先级更高。

### benchmark 会生成临时配置

运行时，`run.py` 不会直接改你的 `ov.conf`。它会基于 `--ov-conf` 和 benchmark YAML 生成一份临时配置：

```text
benchmark/wiki/.temp/ov_xxx.conf
```

这份临时配置会自动设置：

- OpenViking server 端口。
- 当前 benchmark 的 vector store 路径。
- YAML 里配置的模型名、base_url 等。

临时配置文件名由最终配置内容计算 hash 得到，所以复用规则很简单：

- 如果最终配置内容完全一致，下一次运行会复用同一个 `ov_xxx.conf`。
- 如果会影响最终配置的内容发生变化，就会生成新的 `ov_xxx.conf`。

常见会生成新临时配置的情况：

- 换了 `--ov-conf` 指向的源配置。
- 改了 `benchmark/wiki/.env` 里的模型名、provider、api_base 或 API key。
- 改了 benchmark YAML 里的 `execution.server_port`。
- 改了 `paths.vector_store`，或者换了 `dataset_name` 导致 vector store 路径变化。

只改 `max_queries`、输出目录、日志路径，通常不会影响临时 OpenViking 配置。

## 第 4 步：第一次完整跑通

第一次没有入库结果时，先确认数据已经生成：

```bash
ls benchmark/wiki/datasets/Qasper
```

然后运行完整流程：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step all
```

如果你使用的不是 `benchmark/wiki/ov.conf`，再额外传入 `--ov-conf /path/to/ov.conf`。

### `--step all` 会做什么

`all` 会顺序执行：

```text
import -> 可选 build_wiki -> gen -> eval
```

注意：只有配置里 `build_wiki: true` 时，`all` 才会在 `import` 后继续执行 `build_wiki`。当前 `run.py` 的 `all` 不会自动执行 `del`。`del` 是单独的清理步骤。

### 每个阶段的大概含义

`import`：

- 读取 `datasets/Qasper/` 下的 Qasper JSON 文件。
- 通过 `QasperAdapter` 把数据整理成 Markdown 文档。
- 写入 `wiki_storage/qasper_30/qasper_30_processed_docs/`。
- 调用 OpenViking 导入资源、生成语义索引。

`build_wiki`：

- 读取 `import` 阶段写入的 `imported_resources.json`。
- 调用 OpenViking 的独立 Wiki 生成接口。
- 使用 YAML 中的 `wiki_card_input_mode` 和 `wiki_max_card_input_chars`。

`gen`：

- 启动一个临时 OpenViking server。
- 运行 VikingBot。
- VikingBot 使用 OpenViking 工具检索/读取库内内容。
- 输出答案到 `generated_answers.json`。

`eval`：

- 读取 `generated_answers.json`。
- 调用 LLM judge 对答案和 gold answer 打分。
- 输出 `qa_eval_detailed_results.json` 和 `benchmark_metrics_report.json`。

## 第 5 步：不要误杀长时间运行的 LLM 阶段

Wiki 构建、VikingBot 回答、LLM judge 都可能几十秒甚至数分钟没有新终端输出。没有输出不等于卡死。

建议观察日志文件：

```bash
tail -f benchmark/wiki/Output/qasper_30/wiki/benchmark.log
```

也可以看 OpenViking server 日志：

```bash
tail -f benchmark/wiki/.temp/openviking-server.log
```

只要进程还在、日志没有明确报错，就先等。尤其不要因为 1 到 2 分钟没有输出就直接中断重跑。

## 第 6 步：复用已经完成的入库结果

如果 `import` 和 `build_wiki` 已经成功跑过，后续调试 VikingBot 或评测时，不需要从头 `--step all`。

先确认入库产物存在：

```bash
ls benchmark/wiki/wiki_storage/qasper_30/qasper_30_viking_store_index
```

再确认 `import` 阶段记录的资源根存在：

```bash
cat benchmark/wiki/Output/qasper_30/wiki/imported_resources.json
```

如果还没有生成 Wiki，先单独执行：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step build_wiki
```

然后确认 Wiki 产物存在：

```bash
find benchmark/wiki/wiki_storage/qasper_30/qasper_30_viking_store_index/viking/default/wiki \
  -maxdepth 3 -type f
```

你应该能看到类似：

```text
nodes.json
source_assignments.json
profile.json
```

如果这些产物存在，可以直接跑生成和评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step gen+eval
```

如果只想重新生成答案，不重新评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step gen
```

如果已有答案，只想重新评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step eval
```

## 第 7 步：查看结果

成功跑完后，主要看这几个文件：

```text
benchmark/wiki/Output/qasper_30/wiki/generated_answers.json
benchmark/wiki/Output/qasper_30/wiki/qa_eval_detailed_results.json
benchmark/wiki/Output/qasper_30/wiki/benchmark_metrics_report.json
benchmark/wiki/Output/qasper_30/wiki/benchmark.log
benchmark/wiki/Output/qasper_30/wiki/traces/
```

### `generated_answers.json`

保存每个问题的生成结果，包括：

- question
- gold_answers
- VikingBot final_answer
- token_usage
- trace_file

### `qa_eval_detailed_results.json`

保存每个问题的评测明细，包括：

- F1
- Accuracy
- LLM judge reasoning

### `benchmark_metrics_report.json`

保存汇总指标，例如：

```json
{
  "Total Queries Evaluated": 2,
  "Performance Metrics": {
    "Average F1 Score": 0.07142857142857144,
    "Average Recall": 0.0,
    "Average Accuracy (Hit 0-4)": 2.0,
    "Average Accuracy (normalization)": 0.5
  }
}
```

### `traces/`

保存 VikingBot 每个 query 的工具调用轨迹，适合排查“为什么它这么回答”。

## 第 8 步：清理或重建

如果你只是想重跑 `gen/eval`，不要清理 `wiki_storage/`。

如果你明确想重新入库、重新构建 Wiki，可以先删除当前数据集产物：

```bash
rm -rf benchmark/wiki/wiki_storage/qasper_30
rm -rf benchmark/wiki/Output/qasper_30/wiki
```

然后再跑：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step all
```

谨慎使用 `--step del`。它会调用 benchmark 的删除逻辑清理 OpenViking 资源和 Wiki，但不会替你判断哪些运行产物还想保留。

## 常见问题排查

### 1. `OpenViking config not found`

现象：

```text
[Warning] OpenViking config not found: ...
```

处理：

- 如果你使用默认路径，复制 `benchmark/wiki/ov.conf.example` 到 `benchmark/wiki/ov.conf`，并准备好 `benchmark/wiki/.env`。
- 如果你使用外部配置文件，传入正确的 `--ov-conf /path/to/ov.conf`。

### 2. 模型配置不完整或不可用

常见表现包括：

- `No API Key found`
- embedding 初始化失败
- VLM/LLM 请求 401、403、404
- 模型名不存在
- embedding 维度不匹配
- 请求超时或频繁限流
- 报错里出现 `${OV_VLM_API_KEY}`、`${OV_EMBEDDING_API_KEY}` 这类未展开的占位符

处理：

- 先确认 `benchmark/wiki/.env` 存在，并且不是原样保留 `your_vlm_api_key_here`、`your_embedding_api_key_here`。
- 检查 `benchmark/wiki/.env` 里的 `OV_VLM_MODEL`、`OV_VLM_PROVIDER`、`OV_VLM_API_BASE`、`OV_VLM_API_KEY`。
- 检查 `benchmark/wiki/.env` 里的 `OV_EMBEDDING_MODEL`、`OV_EMBEDDING_PROVIDER`、`OV_EMBEDDING_API_BASE`、`OV_EMBEDDING_API_KEY`、`OV_EMBEDDING_INPUT`、`OV_EMBEDDING_DIMENSION`。
- 如果你没有使用模板文件，检查 `ov.conf` 里的 `embedding.dense.dimension`、`batch_size`、`vlm.temperature`、`vlm.thinking` 是否适合当前模型服务。
- 如果你传了外部 `--ov-conf /path/to/ov.conf`，确认这份外部配置也能正确使用 `.env` 或 shell 环境变量。

可以用下面的命令快速确认 `.env` 能被读取，并且 `qasper_30.yaml` 里的 `${OV_...}` 能被展开。这个命令只检查配置，不会发起模型请求：

```bash
uv run python - <<'PY'
import os
from pathlib import Path
import yaml

for line in Path("benchmark/wiki/.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

raw = Path("benchmark/wiki/config/qasper_30.yaml").read_text()
cfg = yaml.safe_load(os.path.expandvars(raw))
print("llm.model =", cfg["llm"]["model"])
print("llm.base_url =", cfg["llm"]["base_url"])
print("has OV_VLM_API_KEY =", bool(os.environ.get("OV_VLM_API_KEY")))
print("has OV_EMBEDDING_API_KEY =", bool(os.environ.get("OV_EMBEDDING_API_KEY")))
PY
```

### 3. 端口 2026 被占用

现象类似：

```text
OpenViking server port is already occupied
```

处理方式 A：换端口。编辑 `benchmark/wiki/config/qasper_30.yaml`：

```yaml
execution:
  server_port: 2036
```

处理方式 B：找出占用进程：

```bash
lsof -nP -iTCP:2026 -sTCP:LISTEN
```

确认是你可以停掉的进程后再处理。

### 4. `Raw data file not found` 或找不到 `datasets/Qasper`

说明还没有下载/抽样生成示例数据，或者 `qasper_30.yaml` 里的 `dataset_path` 和实际数据目录不一致。

先确认目录：

```bash
ls benchmark/wiki/datasets/Qasper
```

如果目录不存在，先执行数据准备：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset Qasper \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets \
  --num-docs 30 \
  --seed 42 \
  --sample-mode random
```

如果你把数据放到了别的目录，需要同步修改：

```yaml
paths:
  dataset_path: "你的数据目录"
```

### 5. `Generated answers file not found`

你直接跑了 `--step eval`，但还没有 `generated_answers.json`。

先跑：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step gen
```

再跑：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step eval
```

### 6. VikingBot JSON 解析失败

历史上遇到过答案里包含 LaTeX 反斜杠导致 JSON 解析失败的问题，例如 `\pi`、`\mathbf`。当前 runner 已做兼容：

- 先尝试原始 JSON 解析。
- 失败后再保守修复非法反斜杠。
- 原始失败输出会保存在 `.temp/` 下用于排查。

如果再次出现，可以查看：

```bash
ls benchmark/wiki/.temp
ls benchmark/wiki/.temp/bot_json
```

### 7. 看起来卡住了

先不要直接中断。按顺序检查：

```bash
tail -f benchmark/wiki/Output/qasper_30/wiki/benchmark.log
```

```bash
tail -f benchmark/wiki/.temp/openviking-server.log
```

如果是 LLM 调用阶段，等待几分钟是正常的。只有看到明确异常栈、进程退出、或超过代码内 timeout，才按错误处理。

## 常用命令速查

### MDA-QA：前 100 条 QA

`MDAQAFirst100` 固定选择 MDA-QA 数据快照中的前 100 条记录（`id=0～99`），并只下载这些问题的 `support` 字段引用的 143 篇 arXiv PDF。QA 文件固定到 Hugging Face revision `7c4a4c374e3ff8298e9694648e0d793197a30814`，避免上游更新改变实验子集。实验语料采用 arXiv PDF 经 OpenViking 解析后的全文，不使用 SPIQA 预提取段落。

下载并准备数据：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset MDAQAFirst100 \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

PDF 会缓存在 `benchmark/wiki/raw_data/MDAQA/pdf_cache/`。下载器串行访问 arXiv，并在 manifest 中记录每篇 PDF 的 SHA-256；再次运行时会复用已经验证的文件。

将 143 篇论文导入资源库：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/MDAQA/mdaqa_first_100.yaml \
  --step import
```

基于已导入的资源构建 Wiki：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/MDAQA/mdaqa_first_100.yaml \
  --step build_wiki
```

执行全部 100 条 QA 并评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/MDAQA/mdaqa_first_100.yaml \
  --step gen+eval
```

### PaperScope Summary：全部有效 QA

PaperScope Summary 提供两种文档范围，但两者评测的是同一批 352 条有效 QA：

- `PaperScopeSummary57`：只准备有效 QA 引用的 57 篇论文。
- `PaperScopeSummary93`：准备原始 600 条 Summary 记录引用的全部 93 篇论文；额外 36 篇作为检索干扰文档。

准备 57 篇版本：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset PaperScopeSummary57 \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

准备 93 篇版本：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset PaperScopeSummary93 \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

PDF 使用共享缓存。因此先准备 57 篇、再准备 93 篇时，只需下载新增论文。若 OpenReview 要求登录，脚本会交互式读取邮箱和隐藏密码，不会保存凭据。

每个文档范围只需入库一次。例如先导入 57 篇论文：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/PaperScope_summary/paperscope_summary_57_trend.yaml \
  --step import
```

然后基于已导入的资源构建 Wiki：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/PaperScope_summary/paperscope_summary_57_trend.yaml \
  --step build_wiki
```

然后三种问题类型复用同一个 57 篇向量库：

```bash
uv run python benchmark/wiki/run.py --config benchmark/wiki/config/PaperScope_summary/paperscope_summary_57_trend.yaml --step gen+eval
uv run python benchmark/wiki/run.py --config benchmark/wiki/config/PaperScope_summary/paperscope_summary_57_gap.yaml --step gen+eval
uv run python benchmark/wiki/run.py --config benchmark/wiki/config/PaperScope_summary/paperscope_summary_57_results_comparison.yaml --step gen+eval
```

93 篇版本使用 `config/PaperScope_summary/` 下对应的 `paperscope_summary_93_*.yaml`，运行顺序相同。

### WildGraphBench Summary

WildGraphBench Summary 提供两个固定实验范围：

- `WildGraphBenchSummaryAll`：入库12个主题下的3894篇 `reference_pages` TXT，运行全部339条 Summary QA。
- `WildGraphBenchSummaryHealth`：只入库 Health 主题下的509篇 `reference_pages` TXT，运行该主题的55条 Summary QA。

两个范围均使用固定的上游 Git revision，且不接受 `--sample-size` 或 `--num-docs`。准备全部主题：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset WildGraphBenchSummaryAll \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

只准备 Health：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset WildGraphBenchSummaryHealth \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

运行相应实验：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/WildGraphBench/wildgraphbench_summary_all.yaml \
  --step all

uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/WildGraphBench/wildgraphbench_summary_health.yaml \
  --step all
```

Adapter 会把每条 QA 的多个 `gold_statements` 合并为一个完整标准答案，以便继续使用项目现有的通用 F1 和 LLM judge；原始陈述列表与 `ref_urls` 仍保留在 QA metadata 中。该结果不是 WildGraphBench 官方陈述级指标。

### ScholarQA-Multi：101 条有效 QA

`ScholarQAMultiValid101` 固定使用 ScholarQABench revision `95e6fc52b0a8a0ce0a74956029991e3bb00c38b9`。原始 ScholarQA-Multi 有 108 条专家 QA；其中 7 条答案含有超出各自 `ctxs` 范围的引用编号，因此固定排除，保留 101 条有效 QA。有效 QA 按 `subject` 形成 6 个 `StandardSample`，但共同检索同一份语料库。

本实验不下载论文 PDF。Adapter 使用官方 `ctxs` 引用片段：优先按 Semantic Scholar Paper URL 合并，没有 URL 时按规范化论文标题合并，最终生成 413 份 TXT 文档。每份 TXT 保存标题、作者、年份、来源 URL，以及去重后的官方引用片段。上游有 2 个被引用来源的 `text` 为 `NaN`；对应 TXT 仅保留可用的文献元数据，不伪造片段，也不把 `NaN` 加入 QA evidence。

下载并准备数据：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset ScholarQAMultiValid101 \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

导入 413 份引用片段文档、构建 Wiki、运行 101 条 QA 并评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/ScholarQABench/scholarqa_multi_valid_101.yaml \
  --step import

uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/ScholarQABench/scholarqa_multi_valid_101.yaml \
  --step build_wiki

uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/ScholarQABench/scholarqa_multi_valid_101.yaml \
  --step gen+eval
```

每个 `gold_answers` 只保存一个答案：原始专家答案后附零基引用编号到论文标题的对照表，以消除 `[0]`、`[1]` 等标签的指代歧义。原始专家答案、完整 `ctxs` 和引用映射也保留在 QA metadata 中。实验继续使用项目现有通用评价指标，不启用 ScholarQABench 官方 Prometheus 或引用正确性指标。

### MuDABench：Simple 与 Complex

MuDABench 提供两个固定实验范围：

- `MuDABenchSimple`：运行官方 `simple.json` 的全部 166 条 QA。
- `MuDABenchComplex`：运行官方 `complex.json` 的全部 166 条 QA。

数据固定到 Hugging Face revision `af2360876c0b8789e2ca1af9d648f9370eb52600`。两个范围都完整使用官方 589 份金融 PDF，语料集合完全相同，大小约为 3.78 GiB。PDF 缓存在 `benchmark/wiki/raw_data/MuDABench/pdf_cache/`，并通过官方 LFS SHA-256 和文件大小校验；两个准备目录优先使用硬链接复用缓存文件，在同一文件系统中不会重复占用相同文件内容的磁盘空间。

两个 QA 文件各有 166 行和 164 个唯一 `question_id`，其中两组 QA 是官方文件中的完全重复记录。Adapter 按官方发布口径保留全部 166 行，并通过全局查询序号区分重复记录。`final_answer` 直接作为唯一 gold answer，`source_answer` 作为 evidence；评测继续使用项目现有 F1 和通用 LLM judge，不接入 MuDABench 官方 evaluator。

先准备 Simple；这一步会下载完整 589-PDF 语料：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset MuDABenchSimple \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

再准备 Complex；已验证的 PDF 会从共享缓存复用：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset MuDABenchComplex \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

两份配置共享 `mudabench_589` 文档处理目录、向量库和 Wiki，因此只需要使用任意一个配置执行一次导入和 Wiki 构建。例如：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/MuDABench/mudabench_simple.yaml \
  --step import

uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/MuDABench/mudabench_simple.yaml \
  --step build_wiki
```

然后分别运行两类 QA：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/MuDABench/mudabench_simple.yaml \
  --step gen+eval

uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/MuDABench/mudabench_complex.yaml \
  --step gen+eval
```

MuDABench 官方说明指出，远程监督标注和文档覆盖限制可能导致部分问题不可回答，并建议优先关注年报类任务。当前适配为了保持完整 Simple/Complex 实验口径，不额外过滤这些记录。

### EnterpriseRAG-Bench：三个类别的 80 条 QA

`EnterpriseRAGBenchSelected80` 固定使用 EnterpriseRAG-Bench `v1.0.0` Release，只运行以下三个类别：

- `project_related`：40 条 QA。
- `conflicting_info`：20 条 QA。
- `completeness`：20 条 QA。

三类合计 80 条 QA，引用 322 个不同的逻辑 `doc_id`。其中 `qst_0413` 故意引用两个 `doc_id` 相同、内容和文件名不同的 Jira 文档；下载器和 Adapter 不按逻辑 ID 粗略去重，而是保留两个物理文件。因此共享语料库实际包含 323 份 TXT 文档。

准备数据时会下载官方 `questions.jsonl` 和约 1.17 GiB 的 `all_documents.zip`。完整压缩包固定缓存在 `benchmark/wiki/raw_data/EnterpriseRAGBench/v1.0.0/`，但只从中提取并准备上述 80 条 QA 对应的 323 份 TXT，不会把其余 50 多万份文档入库。下载文件通过官方 Release 的大小与 SHA-256 校验。

下载并准备固定范围：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset EnterpriseRAGBenchSelected80 \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets
```

导入 323 份 TXT、构建 Wiki、运行 80 条 QA 并评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml \
  --step import

uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml \
  --step build_wiki

uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml \
  --step gen+eval
```

Adapter 将官方 `gold_answer` 直接作为唯一 gold answer，将 `answer_facts` 保存为 evidence，同时在 metadata 中保留原始 `expected_doc_ids`、物理文件映射和来源类型。评测继续使用项目现有 F1 和通用 LLM judge，不接入 EnterpriseRAG-Bench 官方评价器。

下载并生成 Qasper 示例数据：

```bash
uv run python benchmark/wiki/scripts/prepare_dataset.py \
  --dataset Qasper \
  --download-dir benchmark/wiki/raw_data \
  --output-dir benchmark/wiki/datasets \
  --num-docs 30 \
  --seed 42 \
  --sample-mode random
```

复制模型配置模板：

```bash
cp benchmark/wiki/.env.example benchmark/wiki/.env
cp benchmark/wiki/ov.conf.example benchmark/wiki/ov.conf
```

检查 `.env` 和 YAML 占位符是否能正常展开：

```bash
uv run python - <<'PY'
import os
from pathlib import Path
import yaml

for line in Path("benchmark/wiki/.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

cfg = yaml.safe_load(os.path.expandvars(Path("benchmark/wiki/config/qasper_30.yaml").read_text()))
print(cfg["llm"])
PY
```

首次完整跑：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step all
```

复用已完成的入库和 Wiki，只跑生成和评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step gen+eval
```

只跑生成：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step gen
```

只跑评测：

```bash
uv run python benchmark/wiki/run.py \
  --config benchmark/wiki/config/qasper_30.yaml \
  --step eval
```

看主日志：

```bash
tail -f benchmark/wiki/Output/qasper_30/wiki/benchmark.log
```

看 server 日志：

```bash
tail -f benchmark/wiki/.temp/openviking-server.log
```

检查输出报告：

```bash
cat benchmark/wiki/Output/qasper_30/wiki/benchmark_metrics_report.json
```

跑相关测试：

```bash
uv run pytest tests/wiki -q
uv run pytest tests/benchmark/test_wiki_vikingbot_runner_json.py -q
```

## 如何接入新数据集

新数据集接入和 adapter 编写请看独立文档：

```text
benchmark/wiki/README_adapter_zh.md
```

## 如何理解代码结构

代码结构、模块职责和实现逻辑请看独立文档：

```text
benchmark/wiki/README_code_zh.md
```
