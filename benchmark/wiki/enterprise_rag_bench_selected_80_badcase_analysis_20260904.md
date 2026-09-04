# EnterpriseRAGBench Selected 80 Badcase 分析

本文记录 `enterprise_rag_bench_selected_80` baseline 与 `enterprise_rag_bench_selected_80_source_refs_drilldown_20260904` 失败实验的 case-driven 分析。分类来自本轮样本、trace、gold/evidence 和评审结果，不套用其他 benchmark 的预设分类。

## 结论摘要

Baseline：80 条 QA，平均分 3.40；分布为 4 分 52 条、3 分 14 条、2 分 11 条、0 分 3 条；平均迭代轮次 3.275。

Source refs drilldown 实验：80 条 QA，平均分 2.39；相对 baseline 改善 6 条、持平 34 条、退化 40 条。平均输入 token 从约 4.56 万涨到约 17.91 万。该实验不应作为有效优化保留。

可以被证据支持的判断：

- Baseline 低分主要不是单一问题，而是三类底层失败：旧/新版本冲突消解失败、跨多文档计数/枚举失败、精确长答案遗漏关键字段。
- Source refs drilldown 的大面积退化不能全部归因于 source refs，因为新实验中只有 5 条 query 实际出现 source refs hint，8 条 query 读取了 wiki node。
- 但 source refs hint 对出现过的样本没有稳定收益：5 条出现 hint 的 query 中 3 条退化、1 条改善、1 条持平；读取 wiki node 的 8 条中 6 条退化、1 条改善、1 条持平。
- 因此，当前“读 wiki node 后无条件追加最多 12 个 source refs，并提示 should read”的默认行为不应进入默认 benchmark 流程。它最多只能作为显式实验开关。

## Baseline 低分样本归因

| Query | 分数 | 问题摘要 | Gold 关键点 | Trace/回答现象 | 归纳失败机制 |
|---:|---:|---|---|---|---|
| 55 | 0 | Optimize cohort-driven gates PRD 的 rollback thresholds | 当前阈值是 safety >0.3%/5m throttle、quality >3%/10m cohort divert、KPI >4%/10m full rollback；旧阈值必须标成 outdated | baseline 读了 2 个 resource，但回答采用 earlier unapproved draft 的阈值 | 旧/新版本冲突消解失败：读到或采用了旧版证据，没有识别 updated PRD 优先级 |
| 59 | 2 | Hosted AWS Marketplace SKU 是否支持 BYOK | 2026 FAQ：Hosted 默认 provider-managed，但 enterprise select regions 可作为 add-on 支持 BYOK；旧 Northpeak call 已过时 | baseline 答成 Hosted 不支持 BYOK，只推荐 Private/VPC | 旧/新版本冲突消解失败：旧销售/会议信息覆盖了更新 FAQ |
| 61 | 0 | 哪个 intake channel 的 token accounting discrepancy 报告最多 | Jira/SUP tickets 最多 | baseline 最终答 Slack；只读到少量相关 resource，并未完整统计所有 provided cases | 跨多文档计数失败：需要覆盖所有候选 case 后聚合，agent 基于局部证据过早计数 |
| 79 | 0 | Fireflies transcripts 中多少条提到 data residency requirement | 8 条 | baseline 读 6 个 resource 后答 5；没有证明全集枚举完成 | 跨多文档计数失败：召回/读取集合不完整，局部计数被当作全局答案 |
| 13 | 2 | invoice token total 高于 Usage API export 的解释、修复和审批 | 需要完整解释 ledger vs deduped usage、修复步骤、credit/ledger/metering/backfill 审批人 | 评审指出遗漏 required named approvals | 精确长答案遗漏：方向正确，但字段/审批人/条件缺失 |
| 15 | 2 | Acme failover incident type、owner、TTM/TTF | `capacity_fleet.provisioning_lag`、Infrastructure/Fleet、45m、3 business days | baseline owner/TTM/TTF 部分正确，但 incident type 错，并加入未批准 10 business day | 精确字段选择错误：相邻 incident taxonomy 干扰 |
| 21 | 2 | streaming timeout/retry 标准和 SDK 违规 | 需要总体标准 + Python/TS/Go 当前状态 | 评审指出多个事实错误/遗漏 | 多证据综合失败：矩阵、ticket、标准文档需要合并，局部事实不足 |
| 24 | 2 | fast tier SLO、abort criteria、rollback、dashboards、reason codes | 需要大量 SLO、SEV、rollback、dashboard、reason code 细节 | 答案只有少量高层点正确 | 精确长答案遗漏：问题本身覆盖面大，单轮短读无法覆盖所有字段 |

## 失败实验退化样本归因

| Query | 分数变化 | Trace 现象 | 归纳失败机制 |
|---:|---:|---|---|
| 16 | 4 -> 0 | baseline 4 轮读 2 个 resource 答对；新实验 11 轮，search/grep/glob/list 后读到 reconciliation/FAQ 类文档，最终给出强制 sync 等错误 workaround | 长链路漂移：更多工具调用没有扩大正确证据，反而偏向相邻主题 |
| 27 | 4 -> 0 | baseline 读 2 个 resource 答对；新实验 12 轮，读取了 2 次 wiki node，出现 35 次 wiki URI 命中，最终说无相关信息 | wiki node 路由未落到原始证据：读 node 后没有定位到 gold 所需事故/PR 细节 |
| 40 | 4 -> 0 | baseline 读到正确 resource 答 30%；新实验读到另一个 resource 答 20% | 旧/新版本冲突消解退化：旧建议被当成最终配置 |
| 52 | 4 -> 0 | baseline 读 1 个 resource 答 12 months；新实验 search/grep 后没有 multi_read resource，最终答 18 months | 检索未闭环：search/grep 后未读取可验证原文，采用旧值 |
| 64 | 4 -> 0 | baseline 读 5 个 resource 答 3；新实验 21 轮、10 个 resource、11 次 list/glob，最终答 0 | 资源消耗型退化：大量目录/列表操作没有形成全集枚举，反而错过已知正确集合 |
| 68 | 4 -> 0 | 新实验 15 轮，多次 grep/read，仍答错 fallback activation 计数 | 跨文档计数退化：增加读取量不等于完整覆盖，缺少“候选全集”机制 |

## Source refs drilldown 的证据边界

按 trace 统计：

- 新实验中 source refs hint 只出现 5 条：Q18、Q20、Q27、Q71、Q76。
- 这 5 条的分数变化为：Q18 +2，Q20 -2，Q27 -4，Q71 0，Q76 -1。
- 新实验中实际读取 wiki node 的 8 条，平均分差为 -1.375；其中 6 条退化。
- 大部分退化样本没有 source refs hint，因此不能把整体 40 条退化全部归因给 source refs。

能支持的优化结论是：source refs 不应该默认强推，尤其不能用 “should read” 提示把 agent 引向更多无约束读取。因为这轮实验整体明显劣化，代码应先回退到上一有效版本；如果后续还要验证 source refs，只能作为新的、范围更窄的独立实验重新设计。

## 下一轮可验证优化假设

优先级 1：回滚 source refs hint 失败实验。当前已回退失败实验代码，后续调优从上一有效版本重新开始。

- 支撑样本：Q20、Q27、Q76 出现 hint 后未改善；Q27 读 wiki node 后进入长链路但没有读到正确原文。
- 修改位置：`bot/vikingbot/agent/tools/ov_file.py` 的 wiki node search 展示和 `VikingMultiReadTool._wiki_source_reference_hint`。
- 预期收益：消除当前失败实验引入的默认行为风险，避免默认 benchmark token 膨胀。
- 风险：可能失去 Q18 这类个别收益。但这 1 条收益不足以支撑保留一个导致整体劣化的默认行为。
- 实施方式：恢复 `bot/vikingbot/agent/tools/ov_file.py` 和相关单测到 source refs 实验之前的代码状态，删除失败实验配置文件；保留本分析文档和失败实验 output 作为后续参考。

优先级 2：针对旧/新版本冲突做 wiki 生成或检索层优化。

- 支撑样本：baseline Q55、Q59，失败实验 Q40、Q52。
- 方向：wiki node 或 source/card 中显式保留 `current/final/approved` 与 `earlier/draft/superseded` 的状态对比，并在 search/read 结果中暴露版本状态。
- 验证：重点看 Q55、Q59、Q40、Q52，不应牺牲 baseline 已答对的 Q40/Q52。

优先级 3：针对跨多文档计数/枚举建立候选全集机制。

- 支撑样本：baseline Q61、Q79，失败实验 Q64、Q68。
- 方向：不是简单提高 topk；需要先确认候选全集，再计数。可能需要专门的 list/grep 策略、structured index，或 wiki 生成阶段聚合计数字段。
- 验证：重点看 Q61、Q79、Q64、Q68 的 trace 是否能证明“候选全集已覆盖”。
