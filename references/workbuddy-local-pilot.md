# WorkBuddy 本地试点与结果审计

WorkBuddy 包提供项目 Skill、公开政策、预检和结果审计脚本；实际检索与报告仍由指定本地
项目中的 `pfm` 执行。包版本与 Python 应用版本分别记录。项目路径、来源提交和文件摘要
写入包内 manifest，预检会核对包与实际项目，避免指令快照与程序错配。

从项目目录构建0.2.9适配包：

```powershell
.\.venv\Scripts\python.exe scripts\build_workbuddy_skill.py --source-root <实际项目目录> --output var\workbuddy-0.2.9.zip --stage var\workbuddy-0.2.9
```

打包器只读取项目中已跟踪的 `SKILL.md`、`references/*.md` 和公开期刊目录，以及列明的
适配器文件；私人 watchlist、裁定、日志、缓存和报告不进入 ZIP。未跟踪的 reference 文件
同样不进入。ZIP内容、摘要与清单在构建时核对；已有输出路径不覆盖。
`--source-root` 可以指向用户正在使用的另一份检出，manifest将明确记录那一份的提交和摘要。
更新适配器本身不要求变更用户正在使用的核心程序或私人配置。

安装后先运行包内 `scripts/preflight.py`。每次完成周报、即时拉取或历史检索，再将对应的
纯结果 JSON 与私人复核队列交给 `scripts/audit_result.py`，保存审计 JSON 和 Markdown。
详细参数见包内 `references/audit-output.md`。需要解释指定作品时，通过可重复的 `--record`
加入其 PhilPapers URL或ID；实际使用了身份裁定文件时，可以通过 `--identity-reviews`
核对当前待审比较与该文件的记录、来源、外部ID、指纹匹配。

审计脚本从原始字段生成定位、精确计数和来源摘要：

- 历史检索从 `items`、`unconfirmed_records`、`year_review_candidates` 定位，链接取
  `source_urls`、`review_record_url` 等实际字段。确认结果优先于队列中的辅助类型证据。
- 队列项、含比较的记录数、比较对数及证据标签分别计数；字段不存在、null、false与零分开。
- 来源失败、万方跨源核对、万方发现、官方期刊本地线索和网络遥测分别输出；候选数不转为
  实际请求数，长等待延后不转为持久重试队列，来源变化时间不转为发表日。
- 历史检索核对查询时间；周报和即时拉取核对窗口端点，并明确相同窗口不能独自证明同一轮。
  没有逐篇最终列表时，不从队列推断最终确认状态。每个定位保留原字段位置。

这些输出供智能体组织报告。作品关系仍按[同一性政策](identity-review-policy.md)逐对判断；
日期、类型、分类与各模式窗口继续各自核验。审计成功证明输入可按这些规则核对，不保证
来源完整或智能体的后续说明永远正确。回归使用合成夹具；本地私人运行文件可以用于私下
比对，但不得复制进公开仓库或测试夹具。
