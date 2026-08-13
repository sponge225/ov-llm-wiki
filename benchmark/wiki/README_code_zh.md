# Wiki Benchmark 代码结构 Guide

这份文档面向维护 `benchmark/wiki` 代码的Agent/人（主要面向Agent）。它不展开具体实现细节，只说明每个代码文件里关键函数/方法负责什么，以及排查或修改某类逻辑时应该从哪里看。

如果你只是想跑通流程，看 `README_zh.md`。如果你要接入新数据集、写 adapter，看 `README_adapter_zh.md`。

## 一句话总览

主链路：

```text
run.py
  -> 加载 .env / YAML / ov.conf
  -> 动态加载 Adapter
  -> 创建 BenchmarkPipeline
  -> 按 --step 执行 import / gen / eval
```

当前核心模式是：

```yaml
execution:
  mode: "vikingbot"
```

也就是说，生成答案阶段主要通过 VikingBot 调用 OpenViking 工具完成。`baseline` 是旧对照路径，后续会删除，不是当前维护重点。

## 目录结构

```text
benchmark/wiki/
├── run.py                         # benchmark 入口
├── config/                        # benchmark YAML 配置
├── scripts/                       # 数据下载、抽样、准备脚本
├── src/
│   ├── pipeline.py                # import / gen / eval 流程编排
│   ├── vikingbot_runner.py        # OpenViking server 与 VikingBot 调用编排
│   ├── adapters/                  # 数据集适配层
│   └── core/                      # LLM、指标、日志、OpenViking 封装
├── .temp/                         # 临时配置、server 日志、bot JSON
├── wiki_storage/                  # OpenViking 存储、索引、Wiki 产物
└── Output/                        # 答案、评测结果、报告、日志
```

运行产物目录通常不用关心代码逻辑：

```text
raw_data/
datasets/
wiki_storage/
Output/
.temp/
```

## 文件与函数速查

### `run.py`

入口脚本，负责把配置、adapter、pipeline 串起来。

| 函数/位置 | 功能 |
| --- | --- |
| `load_env_file(env_path)` | 读取 `.env`，把其中的 `KEY=VALUE` 放进环境变量；不会覆盖 shell 里已有变量。 |
| `load_config(config_path)` | 读取 benchmark YAML；读取前会做环境变量展开。 |
| `resolve_path(path_str, base_path)` | 把相对路径转换成基于 `benchmark/wiki/` 的绝对路径。 |
| `load_vlm_api_key_from_ov_conf(ov_conf_path)` | 从 `ov.conf` 里读取 VLM API key，支持 `${ENV_VAR}`。 |
| `main()` | 解析命令行参数，加载配置，动态加载 adapter，初始化 pipeline，并根据 `--step` 执行阶段。 |

重点关注：

- 改命令行参数：看 `main()` 里的 `ArgumentParser`。
- 改 YAML 路径解析：看 `path_keys` 和 `resolve_path()`。
- 改 adapter 加载方式：看 `importlib.import_module(module_path)` 附近。
- 改各 step 的执行顺序：看 `# --- E. Execute Tasks ---` 附近。

## `--step` 到代码的映射

| `--step` | 执行的方法 | 说明 |
| --- | --- | --- |
| `import` | `BenchmarkPipeline.run_import()` | 准备文档、入库、构建 Wiki。 |
| `gen` | `BenchmarkPipeline.run_generation()` | 读取 QA，调用 VikingBot 或 baseline 生成答案。 |
| `eval` | `BenchmarkPipeline.run_evaluation()` | 读取已有答案，计算指标和 LLM judge 分数。 |
| `gen+eval` | `run_generation()` + `run_evaluation()` | 复用已有入库结果，只重跑问答和评测。 |
| `all` | `run_import()` + `run_generation()` + `run_evaluation()` | 从入库到评测完整跑一遍。 |
| `del` | `BenchmarkPipeline.run_deletion()` | 调用删除逻辑清理 vector store。 |

## `src/pipeline.py`

主流程编排文件。维护 benchmark 行为时通常先看这里。

### `BenchmarkPipeline.__init__`

功能：保存配置和组件引用，确定输出文件路径。

主要输出文件：

```text
generated_answers.json
qa_eval_detailed_results.json
benchmark_metrics_report.json
```

### `run_import()`

功能：入库阶段。

核心调用关系：

```text
adapter.data_prepare(doc_output_dir)
  -> VikingStoreWrapper.ingest(...)
  -> OpenViking add_resource(...)
```

你想改这些逻辑时看这里：

- 入库前文档如何准备。
- `ingest_mode` 如何传给 OpenViking。
- 是否构建 Wiki。
- 入库指标如何写进报告。

### `run_generation()`

功能：生成答案阶段。

核心调用关系：

```text
adapter.load_and_transform()
  -> _prepare_tasks(samples)
  -> _process_vikingbot_task(...) 或 _process_generation_task(...)
  -> generated_answers.json
```

你想改这些逻辑时看这里：

- `max_queries` 如何限制任务数量。
- 并发任务如何提交。
- 生成失败时如何聚合错误。
- `generated_answers.json` 的结构。

### `run_evaluation()`

功能：评测阶段。

核心调用关系：

```text
generated_answers.json
  -> _process_evaluation_task(item)
  -> qa_eval_detailed_results.json
  -> benchmark_metrics_report.json
```

你想改这些逻辑时看这里：

- 评测输入从哪里读。
- 每条答案如何并发评测。
- 汇总指标如何写入 report。

### `_prepare_tasks(samples)`

功能：把 adapter 返回的 `StandardSample` 展开成逐 query 任务。

重点：这里会应用 `execution.max_queries`。

### `_process_vikingbot_task(task)`

功能：vikingbot 模式下处理单条问题。

核心调用：

```text
run_vikingbot_query(question, config, session_id)
```

输出里会包含：

- final answer
- token usage
- VikingBot trace
- session id
- tool 调用信息

### `_process_generation_task(task)`

功能：baseline 模式下处理单条问题。

当前不是重点维护路径。它会直接：

```text
vector_store.retrieve()
adapter.build_prompt()
llm.generate()
```

### `_process_evaluation_task(item)`

功能：评测单条答案。

核心内容：

- 调 `MetricsCalculator.calculate_f1()` 算 F1。
- 调 `llm_grader()` 算 Accuracy。
- 处理拒答类答案的启发式判断。

### `_update_report(data)`

功能：读取已有 `benchmark_metrics_report.json`，合并新字段后写回。

## `src/vikingbot_runner.py`

负责 OpenViking server 和 VikingBot CLI 的编排。

| 函数/类 | 功能 |
| --- | --- |
| `_runtime_dir()` | 返回运行时临时目录，默认是 `benchmark/wiki/.temp`。 |
| `_api_key_reference(config)` | 决定 API key 写入临时配置时使用原始值还是 `${ENV_VAR}`。 |
| `_generate_temp_ov_conf(...)` | 基于源 `ov.conf` 和 benchmark 配置生成 `ov_xxx.conf`。 |
| `prepare_openviking_config(config, original_conf_path)` | 对外入口：生成临时 OpenViking 配置。 |
| `_load_server_url_and_key(ov_conf_path)` | 从临时配置读取 server URL 和 root key。 |
| `_ensure_openviking_server(ov_conf_path)` | 确保目标 OpenViking server 已启动且健康。 |
| `_stop_openviking_server()` | 停止当前 benchmark 启动的 server。 |
| `_build_vikingbot_env(ov_conf_path)` | 构造调用 VikingBot 子进程的环境变量。 |
| `_extract_vikingbot_json(stdout)` | 从 stdout 里提取 VikingBot JSON。 |
| `_loads_vikingbot_json(text)` | JSON 解析入口，失败时兼容非法反斜杠。 |
| `VikingBotRunner.generate_answer(...)` | 单条问题的完整 VikingBot 调用流程。 |
| `run_vikingbot_query(...)` | pipeline 调用的简化入口。 |

常见修改入口：

- 改 VikingBot 提示词：看 `VikingBotRunner.generate_answer()` 里的 `input_msg`。
- 改 CLI 参数：看 `cmd = [...]`。
- 改 server 启停策略：看 `_ensure_openviking_server()`。
- 改临时配置生成：看 `_generate_temp_ov_conf()`。
- 改 JSON 解析策略：看 `_extract_vikingbot_json()` 和 `_loads_vikingbot_json()`。

## Adapter 层

目录：

```text
benchmark/wiki/src/adapters/
```

### `adapters/base.py`

| 对象 | 功能 |
| --- | --- |
| `StandardDoc` | `sample_id -> doc_path` 映射，用于入库。 |
| `StandardSample` | 一个样本及其 QA 列表。 |
| `StandardQA` | 单条问题、标准答案、证据和元信息。 |
| `BaseAdapter.data_prepare()` | 把原始数据转换成 OpenViking 可入库文档。 |
| `BaseAdapter.load_and_transform()` | 把原始 QA 转成标准 QA 结构。 |
| `BaseAdapter.build_prompt()` | baseline 模式下构造 prompt。 |
| `BaseAdapter.post_process_answer()` | baseline 模式下后处理答案，默认只 strip。 |

### 现有 adapter

| 文件 | 用途 |
| --- | --- |
| `qasper_adapter.py` | Qasper 学术论文 QA，支持目录或单 JSON 文件。 |

新增数据集时看 `README_adapter_zh.md`，不要在这份代码 guide 里找字段映射细节。

## `src/core/` 模块

### `core/execution_mode.py`

| 函数/常量 | 功能 |
| --- | --- |
| `BASELINE_MODE` | baseline 模式名。 |
| `VIKINGBOT_MODE` | vikingbot 模式名。 |
| `resolve_execution_mode(config)` | 从配置里读取并校验 `execution.mode`。 |

### `core/vector_store.py`

| 类/方法 | 功能 |
| --- | --- |
| `VikingStoreWrapper.__init__()` | 初始化 `openviking.SyncOpenViking`。 |
| `ingest()` | 调 `add_resource()` 入库，支持 `directory` / `per_file`。 |
| `retrieve()` | baseline 模式下调用 `client.find()`。 |
| `read_resource()` | baseline 模式下读取资源内容。 |
| `clear()` | 清理 `viking://resources` 和 `viking://wiki`。 |
| `close()` | 关闭底层 OpenViking client。 |
| `count_tokens()` | 粗略统计 token。 |

### `core/llm_client.py`

| 类/方法 | 功能 |
| --- | --- |
| `LLMClientWrapper.__init__()` | 初始化 `ChatOpenAI`。 |
| `generate(prompt)` | baseline 生成答案，带简单重试。 |

### `core/judge_util.py`

| 函数 | 功能 |
| --- | --- |
| `llm_grader(...)` | 调 LLM judge，根据问题、标准答案和生成答案输出 0~4 分及理由。 |

### `core/metrics.py`

| 函数 | 功能 |
| --- | --- |
| `normalize_answer()` | 文本归一化。 |
| `calculate_f1()` | 计算答案 F1。 |
| `check_refusal()` | 判断是否为拒答/不可回答表达。 |
| `check_recall()` | 根据 evidence 检查检索召回。 |

### `core/monitor.py`

| 类/方法 | 功能 |
| --- | --- |
| `BenchmarkMonitor` | 记录并发任务状态。 |
| `worker_start()` | 一个任务开始。 |
| `worker_end()` | 一个任务结束，记录 token 和失败数。 |
| `get_status_dict()` | 给 tqdm 展示 Active/QPS/Tokens/Errs。 |

### `core/logger.py`

| 函数 | 功能 |
| --- | --- |
| `setup_logging(log_file)` | 初始化 `Benchmark` logger，输出到终端和日志文件。 |
| `get_logger()` | 获取 `Benchmark` logger。 |

## 数据准备脚本

目录：

```text
benchmark/wiki/scripts/
```

| 文件/函数 | 功能 |
| --- | --- |
| `prepare_dataset.py` / `prepare_dataset()` | 统一入口，串联下载和抽样。 |
| `download_dataset.py` / `download_dataset()` | 下载、解压、校验公开数据集。 |
| `sample_dataset.py` / `sample_dataset()` | 按数据集执行抽样逻辑。 |
| `run_sampling.py` | 抽样辅助入口。 |

如果要新增公开数据集下载能力，通常改 `download_dataset.py` 和 `sample_dataset.py`。

如果只是接入本地已有数据集，通常不需要改 `scripts/`，只写 adapter 和 YAML 配置即可。

## 输出产物与负责代码

| 产物 | 负责代码 | 说明 |
| --- | --- | --- |
| `wiki_storage/<dataset_name>/...` | `VikingStoreWrapper.ingest()` | OpenViking 存储、索引、Wiki 产物。 |
| `generated_answers.json` | `BenchmarkPipeline.run_generation()` | 每条问题的生成答案和 trace 信息。 |
| `qa_eval_detailed_results.json` | `BenchmarkPipeline.run_evaluation()` | 每条问题的评测明细。 |
| `benchmark_metrics_report.json` | `BenchmarkPipeline._update_report()` | 汇总指标。 |
| `benchmark.log` | `setup_logging()` + pipeline logger | 主流程日志。 |
| `traces/query_<id>_trace.json` | `BenchmarkPipeline._write_vikingbot_trace()` | VikingBot 工具调用轨迹。 |
| `.temp/ov_xxx.conf` | `_generate_temp_ov_conf()` | 临时 OpenViking 配置。 |
| `.temp/openviking-server.log` | `_ensure_openviking_server()` | OpenViking server 日志。 |
| `.temp/bot_json/*.json` | `VikingBotRunner.generate_answer()` | VikingBot `-o` 输出。 |

## 常见修改目标

| 你想改什么 | 优先看哪里 |
| --- | --- |
| 新增命令行参数 | `run.py -> main()` |
| 改 `--step` 执行顺序 | `run.py -> # --- E. Execute Tasks ---` |
| 改入库/Wiki 构建参数 | `pipeline.py -> run_import()`，`core/vector_store.py -> ingest()` |
| 改生成答案流程 | `pipeline.py -> run_generation()` |
| 改 VikingBot 提示词或 CLI 参数 | `vikingbot_runner.py -> VikingBotRunner.generate_answer()` |
| 改 server 启停/端口占用逻辑 | `vikingbot_runner.py -> _ensure_openviking_server()` |
| 改临时配置生成规则 | `vikingbot_runner.py -> _generate_temp_ov_conf()` |
| 改 JSON 解析兼容 | `vikingbot_runner.py -> _extract_vikingbot_json()` / `_loads_vikingbot_json()` |
| 改评测指标 | `pipeline.py -> _process_evaluation_task()`，`core/metrics.py`，`core/judge_util.py` |
| 接入新数据集 | `adapters/base.py`，`README_adapter_zh.md` |
| 改数据下载/抽样 | `scripts/prepare_dataset.py`，`scripts/download_dataset.py`，`scripts/sample_dataset.py` |

## 调试入口

| 问题 | 优先看 |
| --- | --- |
| adapter 导入失败 | YAML 的 `adapter.module/class_name`，`run.py` 动态导入处。 |
| 数据文件找不到 | YAML 的 `paths.dataset_path`，`run.py` 路径解析。 |
| 入库失败 | `pipeline.py -> run_import()`，`core/vector_store.py -> ingest()`。 |
| Wiki 没生成 | YAML 的 `build_wiki`，`wiki_card_input_mode`，OpenViking 日志。 |
| VikingBot 无答案或报错 | `vikingbot_runner.py`，`.temp/openviking-server.log`，`.temp/bot_json/`。 |
| JSON 解析失败 | `.temp/vikingbot-json-error.*.stdout.txt`，`_extract_vikingbot_json()`。 |
| 评测报错 | `pipeline.py -> run_evaluation()`，`core/judge_util.py`。 |
| 指标异常 | `generated_answers.json`，`qa_eval_detailed_results.json`，`core/metrics.py`。 |

长时间没有终端输出不一定是卡住。Wiki 构建、VikingBot 回答、LLM judge 都可能等待模型调用完成。排查时优先看日志和产物是否还在更新。
