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

## 4. 先完成 Badcase 归因，再决定是否优化

不要在没有充分 badcase 依据的情况下直接修改代码。每轮优化前必须先产出一份可检查的 case 归因记录；如果无法说明“哪些 query 因为什么失败、当前代码哪一处行为导致失败、改动后预期影响哪些 query”，就不能进入代码修改阶段。

### 4.1 必须先建立样本清单

从上一轮 output 中挑选样本时，以下集合只是分析入口，不是预设失败分类：

- `score <= 2` 的低分 QA
- 从 4 分退化到 2 分及以下的 QA
- 迭代轮次异常高的 QA
- search 命中相关 wiki node 但没有读取的 QA
- 读取了 wiki node 但仍答错关键事实的 QA
- 回答正确但 token 或轮次明显偏高的 QA

如果样本数量较多，不要求一次人工展开全部样本，但必须说明抽样覆盖率。例如 Enterprise 这类 80 条小集合，建议至少逐条检查所有低分样本和所有明显退化样本；对于大集合，可以先按分数变化、主题、轮次/token 异常、是否使用 wiki node 等客观维度分层抽样。

注意：不要先套用某个已有 benchmark 的分类框架。参考文档中的“检索型失败、重复召回、文档名问题、非连续知识综合、topk 是否有效”等，是基于 FinanceBench 具体 badcase 归纳出来的结论，不应直接作为 EnterpriseRAGBench 的组织方式。我们的分类必须从本轮 trace、gold、answer、evidence 中自底向上归纳。

### 4.2 每个 case 必须记录的字段

每个被分析的 case 至少记录：

- `<output_dir>/qa_eval_detailed_results.json`：问题、gold answer、得分、评审理由、`iterations_used`
- `<output_dir>/generated_answers.json`：模型最终回答
- `<output_dir>/traces/<query_id>_trace.json`：search query、search 结果排名、read 的 URI、工具调用顺序
- 对应的原始 evidence/source docs：判断是召回问题、阅读问题、综合问题，还是 gold/data 本身有问题

建议使用以下表头：

| Query | 分数变化 | 主题/类别 | Gold 需要的关键事实 | 实际回答遗漏/错误 | Search 命中 | 实际读取 | 失败根因 | 可验证优化假设 | 预期影响 |
|---|---:|---|---|---|---|---|---|---|---|

其中“失败根因”必须具体到行为，不要只写“召回不好”或“答案不完整”。先逐 case 写事实，再在多个 case 之间归纳共同模式。下面只是可能出现的根因写法示例，不是固定分类：

- search 返回了正确文档但排名在第 12 名以后，agent 只读前 5 个。
- 读到了 wiki node，但 node 只包含流程框架，缺少 gold 要求的阈值、字段名、owner 或版本号。
- 读到了旧版本/草案文档，gold 依据是后续 approved/update 文档。
- 问题要求计数/枚举，但 agent 没有遍历全部候选文档，过早基于局部证据回答。
- 问题要求冲突消解，但 agent 没有识别“旧建议 vs 最终决定”的时间线。
- trace 中多次列目录或读取大范围无关文档，导致上下文膨胀后仍没锁定关键证据。
- gold/evidence 与 question 不一致，属于数据问题，不应作为优化目标。

### 4.3 从归因到改动必须有证据链

每个准备实施的优化都必须写清楚下面三项：

| 项目 | 要求 |
|---|---|
| 支撑样本 | 至少列出 3 个同类 case；如果只有 1-2 个，必须说明这是低风险局部修复还是探索实验。 |
| 代码/配置位置 | 明确要改的模块、函数、prompt 或 config 字段。 |
| 预期收益与风险 | 写明预计改善哪些 query，可能伤害哪些 query，如何用 trace 验证。 |

禁止把“可能有帮助”的泛化想法直接改进主流程。例如“读取 wiki node 后给出所有 source refs”这类改动，必须先确认 badcase 的主要失败机制确实是“读了 node 但缺少 source refs”，并评估它是否会造成额外读取、上下文污染或旧版本证据干扰。

### 4.4 失败实验也要复盘

如果某轮优化结果下降，不能只看平均分后继续换方向，必须先写出失败原因。至少记录：

- 总体指标变化：平均分、分布、低分数、平均轮次、平均 token。
- 改善和退化 query 列表，尤其是从 4 分降到 0/2 的样本。
- 退化是否在逐 case 复盘后呈现共同模式，例如计数题、冲突消解题、流程题、精确字段题等；这些模式必须由样本证据归纳出来，不能预设。
- trace 中是否出现新的异常行为：过度读取、读取无关 source refs、重复 search、目录遍历、提前回答。
- 是否需要回滚代码，还是收敛为更窄的改动。

如果改动没有收益或明显劣化，必须先把代码回退到上一有效版本，再基于 badcase 归因选择下一轮改动；不要在劣化版本上继续叠加新优化。可以保留失败实验的 output、trace、分析文档和复盘结论，但代码基线必须干净。

例如 `enterprise_rag_bench_selected_80_source_refs_drilldown_20260904` 这轮实验中，平均分从 3.40 降到 2.39，平均输入 token 从约 4.56 万涨到约 17.91 万，40/80 个 query 退化。这说明“读取 wiki node 时直接暴露较多 source refs，并提示继续读原文档”过于激进，造成上下文和检索行为污染。后续不能沿用这个结论做更大范围推广，必须先回退这轮代码，再从上一有效版本重新选择优化方向。

## 5. 基于上一轮 Case 分析确定优化点

完成逐 case 复盘后，再把多个 case 中反复出现的失败机制合并为候选类型，并决定修改位置。下面的表只是候选标签库，用来帮助记录和沟通；不能反过来用它约束分析结论。

| 候选失败类型 | 需要由 case 证据确认的现象 | 可能的修改方向 |
|---|---|---|
| Wiki node 没被搜到 | 相关 node 不在 search 结果中，或排名太低 | search target、level/filter、ranking、node 文档内容 |
| Wiki node 被搜到但没读取 | trace 中 node 排名可见，但 agent 没有 `multi_read` | bot 工具提示、结果展示格式、read 策略 |
| 读了 node 但事实不够 | 答案方向对，但缺阈值、ID、owner、版本等细节 | node 文档结构、source refs drilldown、结构化事实表 |
| 读错原文档 | search/read 进入了相邻主题或旧版本文档 | query rewrite、ranking、版本/状态 metadata |
| 多文档综合失败 | 单篇事实正确，但没有整合多个 evidence | node 聚类策略、跨文档 summary、source assignment |
| 数据问题 | question 与 gold/evidence 不一致，或 gold 本身错误 | 数据清洗、排除样本、单独统计 |
| 轮次/token 过高 | 多次重复 search/read 或读取无关内容 | 工具使用策略、结果去重、max read 数量 |

如果某个实际 badcase 不适合上表，应新增该数据集自己的失败类型，而不是强行归入已有标签。完成归因后，再修改代码或配置。每次实验尽量只验证一个主要变量，例如：

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

## 6. 清理旧 Wiki

如果优化影响 Wiki 生成结果，必须先清理旧 Wiki，避免复用旧 node、旧文档或旧索引。

```bash
cd /home/zhanggaoyuan.225/llm-wiki/benchmark/wiki
python run.py --config config/EnterpriseRAGBench/enterprise_rag_bench_selected_80.yaml --step clear_wiki
```

这一步只清理 Wiki 相关产物，不等价于删除整个向量库或重新导入原始文档。

## 7. 重新 Build Wiki

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

## 8. 重新跑 QA 和评测

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

## 9. 对比结果

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
4. 建立 badcase 样本清单
5. 逐类归因，写明 query、证据、失败机制
6. 将优化假设映射到具体 case 和代码/配置位置
7. 评估预期收益和退化风险
8. 根据 case 归因选择一个优化点并修改代码
9. 为新实验设置独立 output_dir
10. run clear_wiki
11. run build_wiki
12. run gen+eval
13. 对比 baseline output 和新实验 output
14. 复盘改善与退化 case
15. 决定保留、回滚或继续下一轮优化
```

## 注意事项

- 如果数据集预处理逻辑变了，需要重新准备数据，并重新 import。
- 如果只改 Wiki 生成逻辑，通常不需要重新 import 原始文档，但需要 `clear_wiki` 和 `build_wiki`。
- 如果只改 bot search/read 策略，Wiki 本身没变，可以直接 `gen+eval`。
- 改 `output_dir` 只保护 metrics、answers、traces、logs 不被覆盖；如果还要保留旧 Wiki 存储本体，也需要给 `paths.vector_store` 使用新的目录。
- 如果改 embedding 维度、向量库配置或 `ov.conf`，必须清理并重建对应向量库，不能复用旧索引。
- 在受限网络环境下建议设置 `LITELLM_LOCAL_MODEL_COST_MAP=True`，避免 LiteLLM 子进程联网超时影响实验时间。
- 不要把脏数据样本混入 Wiki 效果判断。已确认错误的 QA 应从后续实验中排除或单独统计。
