# 贡献与修改约束

修改代码前请先阅读 `SKILL.md` 及 `references/` 中与改动对应的政策文件。项目的核心语义不是“相关性评分”，而是：

```text
confirmed_new AND bool(paper_category_ids & interest_category_ids)
```

任何改动不得静默引入论文质量评分、作者声望、期刊等级、引用量排序或模型生成标签。

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
