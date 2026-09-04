# Wiki Benchmark 调优流程

本文档说明在 benchmark 侧评估 Wiki 相关优化时的推荐流程。核心原则是：每轮实验使用独立 output 路径保存结果，先跑 baseline，再只改变待验证的代码或配置，重新生成 Wiki，最后重新跑 QA 和评测，对比不同 output 目录下的指标与 trace。

## 适用场景

当修改以下内容时，建议按本文流程重新评估：

- Wiki node 生成、card 生成、document 生成逻辑
- Wiki prompt、node discovery prompt、source assignment prompt
- OpenViking search/read 工具行为
- 数据集预处理或过滤逻辑
- Wiki 索引、层级、metadata、source refs 相关逻辑

## 1. 为本轮实验设置独立 Output

每次新实验都应使用新的 `paths.output_dir` 和 `paths.log_file`，避免覆盖已有结果。推荐复制一份 config，给 `dataset_name` 加实验后缀，并让 output 路径继续使用 `{dataset_name}`。

例如 baseline：

```yaml
dataset_name: "enterprise_rag_bench_selected_80_baseline_20260904"

paths:
  output_dir: "Output/{dataset_name}/wiki"
  log_file: "Output/{dataset_name}/wiki/benchmark.log"
```

例如优化实验：

```yaml
dataset_name: "enterprise_rag_bench_selected_80_l2_search_20260904"

paths:
  output_dir: "Output/{dataset_name}/wiki"
  log_file: "Output/{dataset_name}/wiki/benchmark.log"
```

如果只想最小改动，也可以直接改 `paths.output_dir` 和 `paths.log_file`：

```yaml
paths:
  output_dir: "Output/enterprise_rag_bench_selected_80/l2_search_20260904/wiki"
  log_file: "Output/enterprise_rag_bench_selected_80/l2_search_20260904/wiki/benchmark.log"
```

## 2. 跑 Baseline

先在未修改优化代码前完整跑一次当前配置，得到基准结果。

```bash
cd /home/zhanggaoyuan.225/llm-wiki/benchmark/wiki
python run.py --config config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml --step all
```

其他数据集替换 `--config` 即可，例如：

```bash
python run.py --config config/ScholarQABench/scholarqa_multi_valid_101.yaml --step all
```

`all` 的执行顺序是：

```text
import -> build_wiki -> gen -> eval
```

如果配置里 `execution.build_wiki: true`，`all` 会在 import 后构建 Wiki。

## 3. 记录 Baseline 结果

Baseline 跑完后，保留整个独立 output 目录，并至少记录以下文件和指标：

- `<output_dir>/benchmark_metrics_report.json`
- `<output_dir>/qa_eval_detailed_results.json`
- `<output_dir>/generated_answers.json`
- `<output_dir>/traces/`
- 平均分、各分段数量、平均 `iterations_used`
- Wiki node 命中/读取情况
- 低分样本列表，尤其是 `score <= 2` 的 QA

因为每轮实验使用不同 output 路径，后续 rerun 不会覆盖 baseline。仍建议把关键数字复制到单独实验记录中，便于快速对比。

## 4. 基于上一轮 Case 分析确定优化点

不要在没有 case 依据的情况下直接修改代码。每轮优化前，先从上一轮 output 中挑选具体样本做归因分析，尤其关注：

- `score <= 2` 的低分 QA
- 从 4 分退化到 2 分及以下的 QA
- 迭代轮次异常高的 QA
- search 命中相关 wiki node 但没有读取的 QA
- 读取了 wiki node 但仍答错关键事实的 QA
- 回答正确但 token 或轮次明显偏高的 QA

每个 case 至少查看：

- `<output_dir>/qa_eval_detailed_results.json`：问题、gold answer、得分、评审理由、`iterations_used`
- `<output_dir>/generated_answers.json`：模型最终回答
- `<output_dir>/traces/<query_id>_trace.json`：search query、search 结果排名、read 的 URI、工具调用顺序
- 对应的原始 evidence/source docs：判断是召回问题、阅读问题、综合问题，还是 gold/data 本身有问题

建议先把问题归入以下类型，再决定修改位置：

| 失败类型 | 现象 | 优先修改方向 |
|---|---|---|
| Wiki node 没被搜到 | 相关 node 不在 search 结果中，或排名太低 | search target、level/filter、ranking、node 文档内容 |
| Wiki node 被搜到但没读取 | trace 中 node 排名可见，但 agent 没有 `multi_read` | bot 工具提示、结果展示格式、read 策略 |
| 读了 node 但事实不够 | 答案方向对，但缺阈值、ID、owner、版本等细节 | node 文档结构、source refs drilldown、结构化事实表 |
| 读错原文档 | search/read 进入了相邻主题或旧版本文档 | query rewrite、ranking、版本/状态 metadata |
| 多文档综合失败 | 单篇事实正确，但没有整合多个 evidence | node 聚类策略、跨文档 summary、source assignment |
| 数据问题 | question 与 gold/evidence 不一致，或 gold 本身错误 | 数据清洗、排除样本、单独统计 |
| 轮次/token 过高 | 多次重复 search/read 或读取无关内容 | 工具使用策略、结果去重、max read 数量 |

完成归因后，再修改代码或配置。每次实验尽量只验证一个主要变量，例如：

- 只改 Wiki prompt
- 只改 node 生成策略
- 只改 search 过滤策略
- 只改 source refs drilldown 逻辑

避免一次改动过多，否则新结果变化很难归因。每次修改最好能明确对应一组 case，例如“解决 search 命中 wiki node 但未读取的问题”或“解决 Enterprise 精确阈值问题读了 node 仍答不全的问题”。

修改后先做基本检查：

```bash
python -m py_compile <changed_python_file_1> <changed_python_file_2>
```

如有单测环境，再跑相关单测。

## 5. 清理旧 Wiki

如果优化影响 Wiki 生成结果，必须先清理旧 Wiki，避免复用旧 node、旧文档或旧索引。

```bash
cd /home/zhanggaoyuan.225/llm-wiki/benchmark/wiki
python run.py --config config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml --step clear_wiki
```

这一步只清理 Wiki 相关产物，不等价于删除整个向量库或重新导入原始文档。

## 6. 重新 Build Wiki

清理后重新生成 Wiki：

```bash
python run.py --config config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml --step build_wiki
```

重点检查日志：

- 是否重新生成 document cards
- 是否重新发现 nodes
- node 数量、层级、source refs 是否符合预期
- 是否出现 LLM/API/embedding 异常

如果 build_wiki 阶段失败，不要继续跑 QA；先修复 Wiki 生成问题。

## 7. 重新跑 QA 和评测

Wiki 重新生成后，跑生成和评测：

```bash
python run.py --config config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml --step gen+eval
```

如果只想重新生成答案：

```bash
python run.py --config config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml --step gen
```

如果已有生成结果，只想重新评测：

```bash
python run.py --config config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml --step eval
```

通常验证 Wiki 优化时建议使用 `gen+eval`，因为 trace、answer 和 score 需要对应同一轮实验。

## 8. 对比结果

对比 baseline 与新结果时，从各自的独立 output 目录读取指标和 trace，不只看平均分。建议同时检查：

- 平均分是否提升
- `score <= 2` 的低分样本是否减少
- 高分样本是否发生退化
- 平均 `iterations_used` 是否变化
- search 是否命中 wiki node
- wiki node 是否实际被 `multi_read` 读取
- 读取 wiki node 后是否继续读取 source refs 或原始文档
- trace 中是否出现无效搜索、重复读取或过早回答

对 Wiki 优化尤其要关注两类问题：

- 主题相关但没读 node：说明 search 排名或工具使用策略有问题。
- 读了 node 仍答错细节：说明 node 摘要不足，需要 source refs drilldown 或结构化事实表。

## 推荐实验闭环

每轮优化按以下闭环执行：

```text
1. 为 baseline 设置独立 output_dir
2. run all 得到 baseline
3. 记录 baseline metrics、detailed results、traces
4. 从低分、退化、高轮次、wiki node 未使用等 case 中做归因
5. 根据 case 归因选择一个优化点并修改代码
6. 为新实验设置独立 output_dir
7. run clear_wiki
8. run build_wiki
9. run gen+eval
10. 对比 baseline output 和新实验 output
11. 继续分析新一轮低分 QA 和 trace
12. 决定保留、回滚或继续下一轮优化
```

## 注意事项

- 如果数据集预处理逻辑变了，需要重新准备数据，并重新 import。
- 如果只改 Wiki 生成逻辑，通常不需要重新 import 原始文档，但需要 `clear_wiki` 和 `build_wiki`。
- 如果只改 bot search/read 策略，Wiki 本身没变，可以直接 `gen+eval`。
- 改 `output_dir` 只保护 metrics、answers、traces、logs 不被覆盖；如果还要保留旧 Wiki 存储本体，也需要给 `paths.vector_store` 使用新的目录。
- 如果改 embedding 维度、向量库配置或 `ov.conf`，必须清理并重建对应向量库，不能复用旧索引。
- 在受限网络环境下建议设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，避免 LiteLLM 子进程联网超时影响实验时间。
- 不要把脏数据样本混入 Wiki 效果判断。已确认错误的 QA 应从后续实验中排除或单独统计。
