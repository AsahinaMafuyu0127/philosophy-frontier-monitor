# 报告前的本地证据审计

周报、即时拉取和历史检索完成后，保存完整控制台记录与可直接解析的纯结果 JSON。
使用本包的 `scripts/audit_result.py` 读取最终结果和对应队列，再依据生成的摘要说明结果。
该脚本只读输入文件，不访问网络，不调用监测程序，不修改配置、裁定、缓存或周报状态。

例如在已验证的项目目录运行，脚本路径换成实际安装位置：

```powershell
.\.venv\Scripts\python.exe <Skill目录>\scripts\audit_result.py --result var\search-result.json --queue var\search-queue.json --record EXAMPLE --identity-reviews var\identity-reviews.json --output var\search-audit.json --markdown var\search-audit.md
```

`--record` 可重复或省略；`--identity-reviews` 仅在本轮实际提供该私人文件时使用。
`--output` 和 `--markdown` 必须指定尚不存在的私人文件；不传 `--output` 时输出 JSON 到终端。
周报和即时拉取使用相同脚本与参数，换成本模式保存的文件。

脚本返回以下证据，解释时保留其原始字段位置：

- `records`：历史检索目标在 `items`、`unconfirmed_records`、`year_review_candidates`
  中的实际定位和对应 `source_urls`。`items` 的状态优先于队列中的辅助类型证据。
  周报和即时拉取若没有逐篇最终列表，脚本明确标记最终状态不能由这些列表定位。
- `computed_counts`：结果列表数量、未确认原因、队列原因、含身份比较的记录数、比较对数、
  比较证据标签的准确计数。`confirmed_type` 是确认结果的辅助证据，队列总数不等于未决数。
- `identity_pairs`：每对比较的 PhilPapers 入口、外部原记录和指纹，均保留具体位置。
  `pending_pairs_matching_supplied_decisions` 只计当前队列与所提供裁定的匹配，不能推断
  队列之外的裁定是否已被程序使用。
- `url_field_presence`：分别统计字段不存在、JSON null 和实际值。不要将读取不存在字段后
  得到的空值引用为原始 JSON 的 null。
- `sources`：原始来源失败、万方跨源核对与发现的状态及计数、官方期刊本地线索、
  网络请求遥测与报告中的来源覆盖。请求成功次数不能替代来源状态；候选总数、尝试数和
  有效核对结果各自解释。重试遥测不证明存在持久重试队列。
- `reported_counts` 与 `display_section_counts`：原始汇总和报告显示分组分别列出。
  `confirmed_source_arrivals` 可能包含 OAI 近期变化，不将它直接改写为 Recently arrived 数量。
- `state`：原样区分字段未返回、null、false 和 true；缓存写入和周报状态分开说明。

历史检索严格核对结果与队列的 `queried_at`，不同轮次直接报错。周报和即时拉取核对
时区明确的两个窗口端点；窗口相同不单独证明同一轮运行，仍应使用同一次命令保存的文件。
脚本拒绝混有日志或尾部退出码的“JSON”，先保留控制台记录并提取、解析验证纯 JSON。

对 `year_review_pending` 按项目检索政策解释：缺少 feed 年份提示，且无适用出版方年份补证。
未定位的目标只写“本次返回的逐篇列表中未定位，具体处理关口未知”。来源字段没有给出错误
原因、排除轨迹或作品关系时，不补写这些结论。人工裁定仍需逐对阅读实际来源。


## 周窗口的执行时间

0.2.10 增加 `weekly_timing`：未来窗口直接拒绝；带有执行时间的结果还要求
`window_end <= started_at <= completed_at <= 审计时刻`。旧结果缺少执行时间时返回
`execution_time_unverified`，不能把输出摘要成功当作正式周报验收通过。
新的周报 CLI 同时返回两个执行时刻；它们不同于窗口终点和审计时刻。

正式命令不能带 `--review-queue-file`，因此可省略审计脚本的 `--queue`。
预览队列只属于预览，不与正式结果配对；报告在正式输出的 `report_path` 中时另外读取
真实文件，不修改原始 JSON。检查重复执行后的状态时比较逻辑内容，不能只比较行数。
