# 安全问题

本项目采用 GitHub Private Vulnerability Reporting 接收私密安全报告。公开仓库建立后，维护者必须
先在仓库的 `Settings → Security → Private vulnerability reporting` 中启用这一功能，再对外发布。

如果你发现可能泄露凭据、私人研究方向、论文监测记录或本地路径的漏洞，或者发现其他可被利用的
安全问题，请打开仓库的 `Security` 页面，选择 `Report a vulnerability`，通过私密漏洞报告提交。
报告内容只会对报告者和仓库维护者可见。

不要把漏洞细节、API key、Cookie、私人研究方向、本地数据库内容或可复现的利用步骤写入公开
issue。普通功能错误、文档问题和不涉及安全影响的建议可以使用公开 issue。

如果仓库的 `Security` 页面没有显示 `Report a vulnerability`，说明私密报告功能尚未正确启用。
在此情形下，不要改用公开 issue 披露敏感细节；请等待维护者完成安全渠道配置。

数据处理、凭据、网络访问、日志、缓存与提示注入的完整约束见 [安全与隐私政策](references/security-and-privacy.md)。
