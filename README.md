# Philosophy Frontier Monitor

按研究方向监测每周新出哲学论文的 Codex Skill。

> 稳定版能力：兴趣分类确认、历史基线、每周增量监测、漏周补跑、去重与事实性周报。
>
> 实验性能力：用户主动发起的最近论文即时拉取。

## 最新更新：即时拉取冷启动、额度与作品类型证据（2026-09-07）

证据管线 `0.2.2` 修复了即时拉取冷启动时的外部请求放大：OpenAlex 题名批次现在先消费已经取得
的首屏结果，只补查没有候选的题名，并把每个初始批次限制在最多 3 个逻辑请求。默认安全上限下，
OpenAlex／Crossref 在重试前的计划硬上界分别为 220／50 个逻辑请求；真实七日复跑中，OpenAlex
从修复前的 130 个逻辑请求降至 31 个，短时间重复拉取在调度缓存生效后降至 3 个。

本次更新还加入一小时题名批次调度标记、完整缓存不占逐篇远程预算、31 天候选轮转，以及明确书评
的多语言本地过滤。题名明确以 `Review of`、`Book Review`、`Rezension`、`Compte rendu`、`Reseña`、
`Recensione`、`Resenha` 或“书评：”开头时，会在外部查询前按共用作品类型门槛排除；程序不会根据
主题或模型判断猜测作品类型。周报的证据标准没有改变，`pull-now` 仍是实验功能。完整技术记录见
[CHANGELOG.md](CHANGELOG.md)和[数据源目录](references/source-catalog.md#2026-09-07-冷启动与额度复核)。

实验性证据管线 `0.4.0` 进一步统一 Crossref／OpenAlex／PhilArchive OAI 的结构化作品类型，并以
完整 OAI 增量窗口缩小完全无时间信息的即时拉取候选。
它把 OpenAlex `book-review` 与表示综述论文的 `review` 分开；前者排除，后者规范为支持的
`review-article`。如果来源在“论文／书籍或书章”之间冲突，或返回未知类型，程序会暂缓推送而不是
擅自选择一方。该识别复用现有书目响应，不新增逐篇 PhilPapers 页面抓取。完整规则见
[结构化作品类型政策](references/work-type-policy.md)。

## 一、旨在解决的问题

哲学研究者往往需要反复打开 PhilPapers、期刊页面和书目数据库，检查自己的研究领域是否出现了
新论文。本项目把这项机械劳动转化为持续监测：研究者用中文或英文说明研究方向，Skill 将其映射
到经过核验的 PhilPapers 受控分类，并在新论文命中已确认分类时生成周报。

它不评价论文质量，不替研究者判断论证是否成功，也不按作者声望、期刊等级、引用量或模型评分
删减结果。它要解决的是“不要漏掉可能相关的新论文”，而不是“替我决定哪些论文值得读”。

## 二、功能

公开 `v0.1.0` 的稳定功能包括：

- 把自然语言研究方向转换为待确认的 PhilPapers 分类集合；
- 只启用用户确认过的真实分类，不把模型生成的关键词冒充官方分类；
- 首次运行先建立历史基线，避免把既有论文误报为本周新作；
- 按用户时区执行每周增量监测，并在错过计划时间后按窗口顺序补跑；
- 区分正式发表、预印本／手稿新近可得、来源重新收录和元数据更新；
- 合并多个分类来源中的同一作品，并抑制正式周报中的重复通知；
- 输出题目、作者、载体、日期证据、命中分类、来源和稳定链接；
- 没有匹配结果时如实报告零结果，来源失败时明确说明覆盖缺口。

核心规则是：

```text
(确认在窗口内新出，或确认新近进入 PhilPapers 提醒流)
AND
(论文分类集合 ∩ 用户已确认兴趣分类集合 ≠ ∅)
```

`pull-now` 可以在用户明确提出时立即检查最近 1—31 个当地日，但目前仍是实验功能。PhilPapers
分类 feed 经常缺少逐条日级时间戳和 DOI，冷启动时可能产生大量需要 Crossref／OpenAlex 逐篇核验
的候选；程序已经提供有硬上界的批量查询、短期私人缓存、跨次拉取轮转、有界重试、逐篇远程预算、
候选延期和来源熔断。缓存命中不占逐篇远程预算，下一次拉取优先核验此前未尝试的延期候选，但宽
研究范围下仍可能需要多次拉取才能消化冷启动积压。因此，即时拉取不属于 `v0.1.0` 的稳定承诺，
也不会代替或改变正式周报的通知历史与检查点。题名明确带有受控多语言书评／评论前缀的记录会在
外部书目查询之前由周报和即时拉取共用的作品类型门槛排除；没有明确标签时不根据主题或模型判断
擅自推断作品类型。

## 三、如何安装与首次使用

### 推荐：让 Codex 完成安装

需要 Codex、Git、[`uv`](https://docs.astral.sh/uv/) 和 Python 3.12 或 3.13。当前正式实机验收在
Windows 上完成；其他系统的路径已作兼容设计，但尚未获得同等的真实定时运行验证。

在 Codex 中调用内置安装器，并把下面的占位仓库地址替换为发布后的真实地址：

```text
$skill-installer 请从 https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor
仓库根目录安装这个 Skill；path 使用 .，安装名称使用 philosophy-frontier-monitor。
```

安装完成后的下一轮对话中，请 Codex 执行：

```text
请进入已安装的 philosophy-frontier-monitor 目录，运行 uv sync --python 3.12，
复制公开示例为私人 watchlist，并按照 Skill 引导我完成首次配置。
不要让我把任何 API key、密码或 Cookie 粘贴到聊天中。
```

Codex 官方文档说明，`$skill-installer` 可以从其他 GitHub 仓库安装 Skill；新安装的 Skill 通常在
下一轮对话可用，如果没有出现则重启 Codex。参见 [OpenAI 官方 Skill 文档](https://learn.chatgpt.com/zh-Hant/docs/build-skills)。

### 首次配置流程

1. 用普通语言告诉 Skill 你的研究方向；你不需要预先知道 PhilPapers 分类名称。
2. 审查 Skill 提出的真实分类、范围和推理理由，明确接受、拒绝或暂缓每个候选。
3. 按引导取得 PhilPapers taxonomy 所需的 API ID 和 API key，并只保存到本机 Git 忽略目录；
   不要把凭据发到聊天、Issue、截图或命令行参数中。
4. 建立历史基线。基线只把当前 feed 条目标为既有历史，不生成历史论文洪水。
5. 先完成一次 dry run 和正式周运行，再让 Codex建立每周计划任务。默认是用户当地星期一 08:00，
   可以改为其他时区、星期和时间。

熟悉终端的用户可以在安装目录中运行：

```powershell
uv sync --python 3.12
Copy-Item .\config\watchlist.example.yaml .\config\watchlist.yaml
.\.venv\Scripts\pfm.exe doctor
```

macOS／Linux 对应的程序路径是 `.venv/bin/pfm`。完整的安装、运行目录、凭据和首次基线说明见
[安装与首次部署](references/installation.md)。

## 四、当前版本

当前公开候选版本：`v0.1.0`。

| 能力 | 状态 |
|---|---|
| 自然语言研究方向到受控分类 | 稳定 |
| 分类范围预估与用户确认 | 稳定 |
| 历史基线 | 稳定 |
| 每周增量监测与事实性周报 | 稳定 |
| 错过窗口后的顺序补跑 | 稳定 |
| SQLite 状态、去重与事务性检查点 | 稳定 |
| 数据源有界重试、遥测与运行级熔断 | 稳定 |
| 用户主动即时拉取 `pull-now` | 实验性 |

`v0.1.0` 已完成自动测试、构建、发布隐私检查，以及一次真实的无人值守周一计划运行。证据管线
`0.4.0` 已加入即时拉取的冷启动、外部额度控制、结构化作品类型证据和 PhilArchive OAI 增量
候选适配器。适配器完整跟随分页，将 OAI 记录变化时间与论文出版时间分开，并只通过共享 `/rec/`
标识和 PhilPapers 分类结果求交。结构化类型识别与即时 OAI 缩减仍处于实验验证阶段；它们不会
阻碍周报功能使用，也不改变周报的分类交集和旧作检查标准。
即时报告把“机器核验积压”“需要人工复核”“等待程序自动重试”分别列出；只有人工复核数表示
需要研究者逐项判断的工作量，逐篇查询预算造成的延期不会再冒充用户待审候选。

### 设计来源、差异与本项目新增

本次 OAI 增量候选思路直接受到
[`sea9401/philosophy-mcp`](https://github.com/sea9401/philosophy-mcp) 的 `list_recent` 启发：当
PhilPapers RSS 没有条目时间时，改用 PhilArchive OAI 的记录变化窗口。不过，本项目没有复制其
代码，也没有沿用“只处理第一响应页”的实现，而是增加完整 `resumptionToken` 分页、重叠窗口与
精确本地过滤、删除记录处理、OAI 时间／出版时间分离、与确认分类 `/rec/` 集合求交、旧作检查和
失败回退。首次真实七日测试把 347 条宽候选缩为 47 条，真正需要用户判断的候选为 2 条。

我们也把 [`tamnd/philpapers-cli`](https://github.com/tamnd/philpapers-cli) 的无窗口、首响应页
`Recent` 实现，以及 [`Sfgangloff/co-philosopher`](https://github.com/Sfgangloff/co-philosopher)
的 RSS 年份／稿本状态提取作为对照，说明哪些办法不能单独解决“完全无时间信息”的问题。完整的
固定版本链接、许可证状态、没有复制上游代码的边界、逐项实现差异和仍然存在的覆盖限制见
[设计来源、实现差异与独立贡献](references/design-lineage.md)。

版本变化见 [CHANGELOG.md](CHANGELOG.md)，贡献和验证要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 五、安全与来源边界

- 私人研究方向、确认分类、凭据、SQLite 状态和周报默认只保存在本机，不进入公开仓库；
- `config/watchlist.yaml`、`var/`、`reports/`、`.env` 和虚拟环境均受 Git 忽略规则保护；
- API key 只允许从本机私人文件或进程环境读取，不写入代码、报告、日志、测试夹具或 URL 输出；
- 远程题目、摘要、XML、JSON 和错误页都按不可信数据处理，不能成为执行本地命令的指令；
- 默认不下载论文全文，不绕过付费墙、访问控制、TLS 验证或站点防护；
- 项目只监测用户确认的分类和运行时成功返回的来源，不承诺穷尽全球哲学新作或消除来源延迟；
- PhilPapers feed 到达时间不自动等于论文正式发表时间，报告会保留事件类型、日期精度和证据来源；
- OpenAlex 与 Crossref 用于书目身份、旧作和日期核验；它们的未收录或暂时失败不能被伪装成确定结论；
- MIT License 只适用于本项目代码，不自动适用于 PhilPapers taxonomy、第三方元数据、摘要或论文全文。

详细规则见 [安全与隐私政策](references/security-and-privacy.md)、
[监测政策](references/monitoring-policy.md)、[时间与版本语义](references/date-and-version-semantics.md)、
[数据源目录](references/source-catalog.md)与[第三方声明](THIRD_PARTY_NOTICES.md)。

## 六、问题反馈

- 维护者：**Asahina Mafuyu**
- 联系邮箱：[junxuanxie@stu.xjtu.edu.cn](mailto:junxuanxie@stu.xjtu.edu.cn)

普通安装问题、功能错误、来源兼容问题和改进建议，可以在
[GitHub Issues](https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor/issues) 中提交，也可以发送邮件。
反馈时请提供版本、操作系统、执行的命令以及已经删除私人信息的错误摘要；不要提交 API key、密码、
Cookie、私人研究方向、完整 `watchlist.yaml`、数据库、周报或带认证参数的 URL。

可能导致凭据或私人研究数据泄露的安全问题，不要发到公开 Issue。请使用 GitHub Private
Vulnerability Reporting；具体步骤见 [SECURITY.md](SECURITY.md)。
