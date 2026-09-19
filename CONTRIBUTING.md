# 贡献与修改约束

修改代码前请先阅读 `SKILL.md` 及 `references/` 中与改动对应的政策文件。新论文监测的核心语义不是“相关性评分”，而是：

```text
confirmed_new AND bool(paper_category_ids & interest_category_ids)
```

任何改动不得静默引入论文质量评分、作者声望、期刊等级或模型生成标签。独立历史检索按已确认
feed 分类成员关系匹配，允许按可用引用量排序，但不得将引用量作为论文质量判断或收录门槛；
其日期与缺失规则见 `references/paper-search.md`。

## 网页技术更新与变更记录

每次交付或推送项目更新，包括功能、修复、策略和文档更新，都必须同步修改中文 `README.md`
的“技术更新”、英文 `README.en.md` 的“Technical update”和 `CHANGELOG.md`，不能只在提交
说明或 CHANGELOG 中记录。GitHub 仓库网页直接展示 README，因此网页更新与代码使用同一批提交。

技术更新以实际更新日期倒序排列，保留已经发布的历史条目；同一次更新尚未完成时可合并整理
该次说明，避免为准备过程重复建条目。正文说明具体变化、用户可见的影响、实际验证和必要限制，
并链接相关用法或政策。中英文事实必须一致；未创建版本标签或 GitHub Release 时，标明为分支
更新，不把代码推送表述为正式版本发布。不得包含私人画像、报告、状态或凭据。

交付前检查网页技术更新与最终改动范围一致，再执行适合此次改动的检查和发布检查。

开发环境使用 Python 3.12 与 `uv.lock`。验证命令：

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe `
  F:\codex\CodexData\skills\.system\skill-creator\scripts\quick_validate.py .
```

涉及网络来源的修改还必须：

- 使用官方说明或来源自身公开接口作为依据；
- 保存最小、无凭据、可再分发的测试夹具；
- 测试错误页、内容类型错误和分页／令牌恢复；
- 不把一次 HTTP 200 当作字段语义已经得到证明；
- 不在 issue、日志、夹具或提交中放入 API key、Cookie、私人研究方向或非公开联系方式。维护者在
  README 与项目元数据中主动公布的联系邮箱可以随项目提交，但不得据此公开其他用户的邮箱。

普通问题也可以发送至 `junxuanxie@stu.xjtu.edu.cn`。安全漏洞不得包含在公开 Issue 中，应按照
`SECURITY.md` 使用 GitHub Private Vulnerability Reporting。

即时拉取反馈应尽量包含版本、平台、窗口天数、可复现步骤、去除私人信息后的错误摘要，以及
`pfm oai-cache status` 中的计数、令牌存在布尔值和到期时间；不得粘贴令牌本身、缓存数据库、私人
watchlist 或完整报告。维护者应先把反馈归类为：可复现代码缺陷、上游协议／字段语义变化、性能与
资源边界问题、证据政策提案。前三类修改需要相应回归测试；证据政策变更还必须更新对应 reference，
不得以单个用户样本静默放宽“新论文”、作品类型或分类交集标准。

涉及 OAI 恢复的修改至少覆盖：初始页中断、令牌续页、过期令牌、`badResumptionToken`、最后一页
后中断、滚动窗口尾部、第二写入者互斥以及 status 不泄露令牌。真实网络试跑应使用隔离的 Git 忽略
缓存和固定窗口，并在前后核对周报状态散列；不要通过删除生产状态或降低 TLS 验证来制造通过结果。
