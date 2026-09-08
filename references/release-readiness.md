# 公开发布准备与隐私闸门

状态：发布前技术基线；许可证与私密安全渠道已经维护者确认
最后修订：2026-09-06

## 1. 目的

本规则约束 `philosophy-frontier-monitor` 从本机私人项目转为公开 Git 仓库之前的检查。它不替
维护者决定项目采用哪一种许可证，也不授权创建远程仓库、提交文件或上传 GitHub。维护者已于
2026-09-06 明确选择 MIT License 和 GitHub Private Vulnerability Reporting；本次决定同时授权
初始化本地 Git 并审查首次跟踪集合，但不授权创建远程仓库、提交或上传。

发布检查的首要目标不是证明代码“没有任何风险”，而是阻止几类已有明确边界的内容被误公开：

- 用户真实研究方向和兴趣画像；
- API key、访问令牌、Cookie、密码、私钥和认证 URL；
- SQLite 状态库、通知历史和私人周报；
- 浏览器下载的原始 taxonomy 及完整规范化 taxonomy；
- 本地环境、虚拟环境、缓存和运行日志；
- 项目尚未说明的第三方数据与依赖边界。

## 2. 命令与副作用边界

运行：

```powershell
.\.venv\Scripts\pfm.exe release-check `
  --private-config .\config\watchlist.yaml
```

该命令只读。它不会：

- 修改 `.gitignore`；
- 初始化 Git；
- 执行 `git add`、`git commit` 或 `git push`；
- 打印私人研究方向或凭据值；
- 扫描 `var/secrets/` 内部凭据正文；
- 替维护者选择许可证或安全报告渠道。

审计成功执行时输出 `ok: true`。只要仍有技术阻断项或维护者决策项，命令以非零退出码结束，并
输出 `release_ready: false`。这个非零退出码表示“还不能公开发布”，不是程序崩溃。

## 3. 公开面与私人面

公开候选面包括项目根目录文档，以及 `src/`、`tests/`、`scripts/`、`agents/`、`references/` 和
经过审查的 `config/watchlist.example.yaml`。

私人面至少包括：

```text
config/watchlist.yaml
var/
reports/
.env
.env.*
```

`.env.example` 可以公开，但只能包含变量名、解释和无效占位符。完整 PhilPapers taxonomy 即使不含
凭据，也不属于项目代码的公开夹具；公开仓库只保留经过审查的最小不完整 fixture。

扫描器主动跳过私人目录正文，先检查 `.gitignore` 是否明确保护这些路径。这样可以验证发布边界，
同时避免为了“找秘密”而把秘密本身读入诊断输出。

## 4. 检查项目

### 4.1 基础公开文件

检查 README、Skill 入口、贡献规则、安全说明、变更记录、构建元数据、代理元数据和公开示例是否
存在。文件存在不等于内容已经完成；许可证、第三方声明和安全渠道另设闸门。

### 4.2 Git 忽略与实际跟踪集合

在 Git 初始化前，程序可以检查 `.gitignore` 和拟公开文件，但无法证明实际跟踪集合。因此
`git_repository` 保持阻断状态。

Git 初始化后，程序使用只读的 `git ls-files --cached` 检查是否已经跟踪：

- 私人 watchlist；
- `var/` 或 `reports/`；
- `.env`；
- SQLite、数据库、私钥或证书文件。

程序还会把拟公开候选文件与 Git 跟踪集合相互比较。存在未跟踪的公开源码、测试或文档时，首次
提交面尚未固定，`untracked_public_files` 保持阻断；这样不能通过“Git 仓库是空的”来绕过发布
检查。

`.gitignore` 不能使一个已经被 Git 跟踪的文件自动退出历史，所以必须同时检查忽略规则和实际
跟踪集合。

### 4.3 高置信度凭据扫描

扫描器只检测少数高置信度模式，例如私钥头、具有已知前缀的访问令牌、AWS access key 和 URL 中
长度足够的认证参数。输出只包含相对文件名和检测器名称，不包含命中的秘密值。

为了减少误报，它不把文档中的 `[API_KEY]`、环境变量名称或源代码字段名当成真实凭据。相应代价是
这不是完整的秘密检测器；公开仓库建立后，仍应在提交和远程发布前使用托管平台 secret scanning
或经过审查的专用历史扫描器。

### 4.4 私人画像与公开示例分离

如果本机存在私人 watchlist，程序只在内存中比较 `profile_id` 和 `original_text` 是否被公开示例
逐字复制。结果只报告重复的字段名，不输出字段值。

公开示例可以使用真实存在的 PhilPapers 分类，但必须明确标成虚构示例，不得复制本机研究者的完整
研究方向。分类重合本身不构成泄漏，因为测试需要覆盖真实分类 ID；逐字复制私人描述则构成阻断。

### 4.5 完整 taxonomy 再分发

公开 JSON 如果同时声明 `source: philpapers`，且 `complete: true` 或 `fixture: false`，发布检查会
阻断。完整 taxonomy 应保留在 Git 忽略的 `var/taxonomy/`，其缓存和再分发受 PhilPapers 条款约束。

### 4.6 许可证、第三方声明和安全渠道

- `project_license` 在没有许可证文件时是 `decision_required`；维护者已经选择 MIT License，根目录
  `LICENSE` 存在时该项通过；
- `third_party_notices` 是技术阻断项：需要说明直接依赖、学术数据来源以及代码许可证不覆盖哪些
  第三方数据；
- `private_security_contact` 在没有明确渠道时是 `decision_required`；维护者已经选择 GitHub
  Private Vulnerability Reporting，`SECURITY.md` 说明报告路径以及未启用时不得公开披露。远程
  仓库创建后、公开发布前仍必须实际启用该功能；本地检查只能验证政策文本，不能验证 GitHub 开关。

这些项目不能由检查器自动补写，因为许可证与联系方式涉及维护者的真实选择，而第三方声明需要
根据当时依赖版本和来源条款核验。

## 5. 结果语义

每个检查项使用以下状态：

- `pass`：本次检查没有发现该类阻断；
- `warning`：存在未扫描或需要复核的内容，但没有直接证明发布一定不安全；
- `not_applicable`：当前阶段无法执行，例如 Git 尚未初始化时不能检查 tracked files；
- `blocker`：必须先完成的技术修复；
- `decision_required`：必须由维护者明确选择，程序不能代替决定。

只有 `blocker_codes` 与 `decision_codes` 都为空时，`release_ready` 才为 `true`。`release_ready: true`
也只表示本规则列出的当前工作树检查通过，不等于完成法律审查、安全审计或 Git 历史审计。

## 6. 推荐发布顺序

```text
完成当前代码与测试
  → 运行 release-check，先消除隐私和文件阻断
  → 明确选择代码许可证和私密安全报告渠道
  → 核验依赖许可证与数据来源条款，写第三方声明
  → 初始化本地 Git
  → 再次运行 release-check，检查实际 tracked files
  → 审查首次提交差异
  → 创建远程仓库并发布
```

创建远程仓库和上传属于外部公开动作，必须由用户明确授权。首次提交前不得因为 `.gitignore` 看似
正确就跳过 `git ls-files` 和差异审查。采用 GitHub Private Vulnerability Reporting 时，还必须在
公开仓库建立后实际启用该功能；仅有 `SECURITY.md` 不能证明远程开关已经打开。

## 7. 当前验收要求

- 私人运行路径缺少任一必要忽略规则时阻断；
- 拟公开文件中的高置信度秘密只报告检测器，不回显值；
- 公开示例逐字复制私人研究原文时阻断且不回显原文；
- 完整 PhilPapers taxonomy 出现在公开候选目录时阻断；
- Git 未初始化、第三方声明缺失和维护者决策尚未完成时，如实给出
  `release_ready: false`；
- 所有检查保持只读并输出 `state_advanced: false`。

自 `v0.3.0` 起，公开稳定能力声明也覆盖用户主动、只读的 `pull-now`。发布检查必须验证 OAI 分页
中断恢复、失效令牌安全重启、缓存互斥和周报状态隔离，并继续明确冷启动成本、私人磁盘占用和
请求安全上限。稳定不等于默认定时执行或固定运行时长。

发布包还必须包含可从中文 README 到达的英文 GitHub 介绍页、英文安装指南，并验证周报与即时报告
都从同一结果生成完整中英文部分。`pull-now` 的用户可见磁盘建议必须在网络请求之前出现，且不得
擅自移动或删除 C: 上的现有私人数据。
